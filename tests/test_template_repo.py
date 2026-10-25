"""模板仓库专属不变量：只属于模板，同步工具代码时不要覆盖本文件与 README/config。"""

from pathlib import Path

import yaml

from profile_agent.config import load_config
from profile_agent.preserve import extract_custom

ROOT = Path(__file__).resolve().parents[1]


def _workflow() -> dict:
    """可复用 workflow：模板仓库自身的 guard 与全部执行逻辑都在这里。"""
    return yaml.safe_load(
        (ROOT / ".github" / "workflows" / "reusable-update-profile.yml").read_text(
            encoding="utf-8"
        )
    )


def _caller() -> dict:
    """调用文件：用户从模板拿到的那份（触发器 + 输入 + secrets 转发）。"""
    return yaml.safe_load(
        (ROOT / ".github" / "workflows" / "update-profile.yml").read_text(encoding="utf-8")
    )


def test_workflow_skips_template_repo_itself():
    """模板仓库自身永不运行：否则说明页会被当成个人主页改写。"""
    wf = _workflow()
    assert wf["jobs"]["update"]["if"] == (
        "github.repository != 'ningbojian/ProfileReadmeAgent'"
    )


def test_template_readme_is_machine_mode_so_first_run_replaces_it():
    """首行是含 profile-agent 的注释、且无 custom 锚点 → 首次运行整页替换，不接管。"""
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert not text.startswith("﻿")  # 有 BOM 会让首行判定失效
    first = text.splitlines()[0]
    assert first.startswith("<!--")
    assert "profile-agent" in first
    assert "profile-agent:custom:start" not in text
    assert "profile-agent:custom:end" not in text
    assert extract_custom(text) == ("", "machine")


def test_config_is_template_safe():
    """模板默认值：用户名走仓库所有者；AI 用 Anthropic 官方模型；可选功能默认关闭。"""
    cfg = load_config(ROOT / "config.yaml", fallback_username="alice")
    assert cfg.github.username == "alice"
    assert cfg.ai.provider == "anthropic"
    assert cfg.ai.model == "claude-opus-5-5"
    assert cfg.ai.key_env_name == "ANTHROPIC_API_KEY"
    assert cfg.ai.prompt_version == 2
    assert cfg.readme.visitors_badge is False
    assert cfg.readme.bilingual is True
    assert cfg.repo_readmes.enabled is False
    assert cfg.repo_descriptions.enabled is False


def test_license_is_mit():
    """模板仓库可被他人复制使用：必须有明确的许可证。"""
    text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert text.startswith("MIT License")
    assert "Copyright (c) 2026 ningbojian" in text


def test_readme_tells_existing_profile_repo_users_to_rename_first():
    """已有 用户名/用户名 仓库的用户无法直接建同名新库：README 必须先教改名备份。"""
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "profile-backup" in text
    assert "Settings → Rename" in text


def test_readme_highlights_ai_as_differentiator():
    """说明页要把「AI 写内容」放在首位：这是与只出统计卡/一次性生成工具的核心差异。

    按功能条目（行首 emoji）比较，不能用裸 substring——「仓库统计」也出现在
    标题下的定位句里，那会永远排在功能列表之前，断言就失去意义。
    """
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "大多数同类工具" in text
    lines = text.splitlines()
    ai = next(i for i, line in enumerate(lines) if line.startswith("- 🤖"))
    stats = next(i for i, line in enumerate(lines) if line.startswith("- 📊"))
    assert ai < stats


def test_removed_features_are_gone():
    """项目介绍等已下线功能不得残留在模板里。"""
    assert not (ROOT / "src" / "profile_agent" / "projects.py").exists()
    assert not (ROOT / "tests" / "test_projects.py").exists()
    assert not (ROOT / "data" / "repo_inputs.json").exists()


def _workflow_call(reusable: dict) -> dict:
    """PyYAML 把 YAML 1.1 的 on 解析成 True；两种写法都兼容。"""
    key = "on" if "on" in reusable else True
    return reusable[key]["workflow_call"]


def test_caller_uses_versioned_reusable_workflow_and_forwards_trigger_event():
    """调用方按 @v1 引用；trigger_event 必须转发，否则定时门/改周期会失效。"""
    job = _caller()["jobs"]["update"]
    assert job["uses"].endswith(
        "ningbojian/ProfileReadmeAgent/.github/workflows/reusable-update-profile.yml@v1"
    )
    assert job["with"]["trigger_event"] == "${{ github.event_name }}"


def test_caller_passes_secrets_by_name_not_inherit():
    """跨账号不能用 secrets: inherit；四个 secret 必须逐个按名字传。"""
    caller = _caller()
    assert caller["permissions"] == {"contents": "write", "issues": "write"}
    secrets = caller["jobs"]["update"]["secrets"]
    assert "inherit" not in secrets
    assert set(secrets) == {
        "WORKFLOW_PAT",
        "ANTHROPIC_API_KEY",
        "AI_API_KEY",
        "DEEPSEEK_API_KEY",
    }
    assert all(v.startswith("${{ secrets.") for v in secrets.values())


def test_reusable_declares_every_passed_secret_and_keeps_bools_as_strings():
    """caller 传的 secret 必须声明；两个布尔控件用 string（空串不能被吞成 false）。"""
    call = _workflow_call(_workflow())
    declared = set(call["secrets"])
    passed = set(_caller()["jobs"]["update"]["secrets"])
    assert passed <= declared
    inputs = call["inputs"]
    assert inputs["update_readme"]["type"] == "string"
    assert inputs["wait_for_merges"]["type"] == "string"
    assert inputs["trigger_event"]["type"] == "string"
