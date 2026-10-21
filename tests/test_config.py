"""config.py 加载与校验测试。"""

from pathlib import Path

import pytest

from profile_agent.config import ConfigError, load_config


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_minimal_config_uses_defaults(tmp_path):
    path = _write(tmp_path, 'github:\n  username: "alice"\n')
    cfg = load_config(path)
    assert cfg.github.username == "alice"
    assert cfg.schedule.enabled is True
    assert cfg.repos.exclude_forks is True
    assert cfg.featured.count == 0  # 默认展示全部仓库
    assert cfg.featured.recency_days == 0  # 默认不限推送时间
    assert cfg.projects.featured_only is False  # 默认全部仓库参与介绍生成
    assert cfg.readme.template == "templates/profile.md.j2"
    assert cfg.ai.enabled is True
    assert cfg.ai.model == "claude-opus-5-5"
    assert cfg.snapshot.path == "data/snapshot.json"


def test_empty_username_raises_chinese_error(tmp_path):
    path = _write(tmp_path, 'github:\n  username: ""\n')
    with pytest.raises(ConfigError, match="用户名"):
        load_config(path)


def test_missing_username_key_raises(tmp_path):
    path = _write(tmp_path, "repos:\n  exclude_forks: true\n")
    with pytest.raises(ConfigError, match="用户名"):
        load_config(path)


def test_missing_file_raises(tmp_path):
    with pytest.raises(ConfigError, match="找不到"):
        load_config(tmp_path / "nope.yaml")


def test_full_config_values_respected(tmp_path):
    path = _write(
        tmp_path,
        """
schedule:
  enabled: false
github:
  username: "bob"
repos:
  exclude_forks: false
  exclude: ["bob/bob"]
featured:
  count: 3
  sort_by: "stars"
readme:
  cards:
    theme: "github_light"
    show: ["stats"]
ai:
  model: "claude-haiku-5-5"
  prompt_version: 2
""",
    )
    cfg = load_config(path)
    assert cfg.schedule.enabled is False
    assert cfg.repos.exclude_forks is False
    assert cfg.repos.exclude == ["bob/bob"]
    assert cfg.featured.count == 3
    assert cfg.featured.sort_by == "stars"
    assert cfg.readme.cards.theme == "github_light"
    assert cfg.readme.cards.show == ["stats"]
    assert cfg.ai.model == "claude-haiku-5-5"
    assert cfg.ai.prompt_version == 2


def test_invalid_sort_by_raises(tmp_path):
    path = _write(
        tmp_path,
        'github:\n  username: "a"\nfeatured:\n  sort_by: "magic"\n',
    )
    with pytest.raises(ConfigError, match="sort_by"):
        load_config(path)


def test_invalid_card_name_raises(tmp_path):
    path = _write(
        tmp_path,
        'github:\n  username: "a"\nreadme:\n  cards:\n    show: ["stats", "pie"]\n',
    )
    with pytest.raises(ConfigError, match="pie"):
        load_config(path)


def test_fallback_username_used_when_empty(tmp_path):
    path = _write(tmp_path, 'github:\n  username: ""\n')
    cfg = load_config(path, fallback_username="repo-owner")
    assert cfg.github.username == "repo-owner"


def test_fallback_username_ignored_when_set(tmp_path):
    path = _write(tmp_path, 'github:\n  username: "alice"\n')
    cfg = load_config(path, fallback_username="other")
    assert cfg.github.username == "alice"


def test_ai_provider_openai_fields(tmp_path):
    path = _write(
        tmp_path,
        """
github:
  username: "a"
ai:
  provider: "openai"
  base_url: "https://api.deepseek.com"
  key_env: "DEEPSEEK_API_KEY"
  model: "deepseek-chat"
""",
    )
    cfg = load_config(path)
    assert cfg.ai.provider == "openai"
    assert cfg.ai.base_url == "https://api.deepseek.com"
    assert cfg.ai.key_env == "DEEPSEEK_API_KEY"
    assert cfg.ai.model == "deepseek-chat"


def test_ai_invalid_provider_raises(tmp_path):
    path = _write(
        tmp_path,
        'github:\n  username: "a"\nai:\n  provider: "bogus"\n',
    )
    with pytest.raises(ConfigError, match="provider"):
        load_config(path)


def test_ai_fill_empty_descriptions_default_on(tmp_path):
    path = _write(tmp_path, 'github:\n  username: "a"\n')
    assert load_config(path).ai.fill_empty_descriptions is True


def test_ai_fill_empty_descriptions_can_disable(tmp_path):
    path = _write(
        tmp_path,
        'github:\n  username: "a"\nai:\n  fill_empty_descriptions: false\n',
    )
    assert load_config(path).ai.fill_empty_descriptions is False


def test_readme_visitors_badge_default_off(tmp_path):
    path = _write(tmp_path, 'github:\n  username: "a"\n')
    assert load_config(path).readme.visitors_badge is False


def test_readme_visitors_badge_can_enable(tmp_path):
    path = _write(
        tmp_path,
        'github:\n  username: "a"\nreadme:\n  visitors_badge: true\n',
    )
    assert load_config(path).readme.visitors_badge is True
