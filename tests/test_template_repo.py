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


def test_removed_features_are_gone():
    """项目介绍等已下线功能不得残留在模板里。"""
    assert not (ROOT / "src" / "profile_agent" / "projects.py").exists()
    assert not (ROOT / "tests" / "test_projects.py").exists()
    assert not (ROOT / "data" / "repo_inputs.json").exists()
