"""精选仓库缺描述时的 AI 短介绍（主页补位文案）。

- 只针对 description 为空的精选仓库；以该仓库 README 为事实来源
- 输入指纹含 README 内容与 prompt_version：仓库没动就不重新生成、不调 API
- 生成失败时回退旧文案（若有），不影响任务退出码
"""

from datetime import datetime, timezone

from profile_agent.config import AIConfig
from profile_agent.models import Repo
from profile_agent.short_intro import (
    build_short_intro_prompt,
    generate_short_intro,
    short_intro_input_hash,
)

REPO = Repo(
    name="mystery",
    full_name="alice/mystery",
    description="",
    language="Python",
    stars=3,
    forks=0,
    pushed_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
    html_url="https://github.com/alice/mystery",
    topics=["cli"],
)

README = "# mystery\n一个命令行密码生成器，支持自定义字符集与长度。"


class FakeClient:
    def __init__(self, blocks):
        self.calls = []
        self._blocks = blocks
        self.messages = self

    def create(self, **kwargs):
        from types import SimpleNamespace

        self.calls.append(kwargs)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=t) for t in self._blocks]
        )


def test_prompt_uses_readme_as_source():
    prompt = build_short_intro_prompt(REPO, README)
    assert "README" in prompt
    assert "密码生成器" in prompt
    assert "1-2" in prompt and "60" in prompt


def test_prompt_without_readme_still_works():
    prompt = build_short_intro_prompt(REPO, "")
    assert "mystery" in prompt  # 至少有仓库名/事实
    assert "不要编造" in prompt


def test_hash_stable_and_changes_with_readme_or_version():
    cfg = AIConfig(prompt_version=1)
    h1 = short_intro_input_hash(REPO, cfg, README)
    assert h1.startswith("sha256:")
    assert short_intro_input_hash(REPO, cfg, README) == h1
    assert short_intro_input_hash(REPO, cfg, README + "\n新增一节") != h1
    assert short_intro_input_hash(REPO, AIConfig(prompt_version=2), README) != h1
    assert (
        short_intro_input_hash(
            REPO, cfg, README, **{}
        )
        == h1
    )


def test_generate_returns_clean_text():
    client = FakeClient(["命令行密码生成器，支持自定义字符集。"])
    text, _en = generate_short_intro(REPO, AIConfig(model="m"), client=client, readme=README)
    assert text == "命令行密码生成器，支持自定义字符集。"
    assert "密码生成器" in client.calls[0]["messages"][0]["content"]


def test_generate_empty_raises():
    class Empty:
        def __init__(self):
            self.messages = self

        def create(self, **kwargs):
            from types import SimpleNamespace

            return SimpleNamespace(content=[SimpleNamespace(type="text", text="  ")])

    import pytest

    with pytest.raises(RuntimeError):
        generate_short_intro(REPO, AIConfig(), client=Empty(), readme=README)


def test_generate_truncates_very_long_output():
    client = FakeClient(["很长的介绍。" + "字" * 200])
    text, _en = generate_short_intro(REPO, AIConfig(), client=client, readme=README)
    assert len(text) <= 80


# ============================ WP4 双语补位（2026-10-23） ============================


def test_generate_dual_language_json_contract():
    """双语默认开：单次调用 JSON {zh,en} → (zh, en)，prompt 含 JSON 契约。"""
    raw = '{"zh": "命令行密码生成器。", "en": "Command-line password generator."}'
    client = FakeClient([raw])
    zh, en = generate_short_intro(REPO, AIConfig(model="m"), client=client, readme=README)
    assert zh == "命令行密码生成器。"
    assert en == "Command-line password generator."
    assert "JSON" in client.calls[0]["messages"][0]["content"]


def test_generate_parse_failure_falls_back_zh_only():
    """模型不按 JSON 输出 → 降级单语 (清洗后原文, "")，任务不失败。"""
    client = FakeClient(["命令行密码生成器，支持自定义字符集。"])
    zh, en = generate_short_intro(REPO, AIConfig(model="m"), client=client, readme=README)
    assert zh == "命令行密码生成器，支持自定义字符集。"
    assert en == ""
