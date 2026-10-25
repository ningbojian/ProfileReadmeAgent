"""模板仓库专属不变量：只属于模板，同步工具代码时不要覆盖本文件与 README/config。"""

from pathlib import Path

import yaml

from profile_agent.config import load_config
from profile_agent.preserve import extract_custom

ROOT = Path(__file__).resolve().parents[1]


def _workflow() -> dict:
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
