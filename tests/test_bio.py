"""bio.py 提示词构建与生成逻辑测试（假 client，无网络）。"""

import json
from types import SimpleNamespace

import pytest

from profile_agent.bio import BioError, build_prompt, generate_bio
from profile_agent.config import AIConfig

PAYLOAD = {
    "name": "Alice Zhang",
    "blog": "https://alice.dev",
    "bio": "全栈工程师",
    "totals": {"public_repos": 42, "followers": 128},
    "repos": [
        {"name": "cool-tool", "description": "一个很酷的工具", "language": "Python", "stars": 120},
        {"name": "web-app", "description": "网页应用", "language": "JavaScript", "stars": 80},
    ],
}


def _block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


class FakeClient:
    """返回真实 SDK 形态的 content blocks；记录 create 调用。"""

    def __init__(self, blocks, raise_exc=None):
        self.calls = []
        self._blocks = blocks
        self._exc = raise_exc
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._exc:
            raise self._exc
        return SimpleNamespace(content=self._blocks)


def test_build_prompt_contains_zh_instructions_and_repos():
    cfg = AIConfig(prompt_version=7)
    prompt = build_prompt(PAYLOAD, cfg)
    assert "简体中文" in prompt
    assert str(cfg.bio_min_chars) in prompt
    assert str(cfg.bio_max_chars) in prompt
    # 有意变更（2026-10-24 视觉）：简介末尾的「亮点：」要点与下方置顶项目/仓库列表
    # 重复，已从 prompt 移除——简介只留一段自然文字，项目信息交给列表
    assert "亮点" not in prompt
    assert "cool-tool" in prompt
    assert "网页应用" in prompt
    assert "128" in prompt  # followers 注入


def test_generate_reuses_existing_recent_bio():
    cfg = AIConfig()
    client = FakeClient([_block("不应被调用")])
    text, _en, generated = generate_bio(
        PAYLOAD,
        cfg,
        input_hash="sha256:whatever",
        prev_hash="sha256:whatever",
        prev_text="这是我已有的简介。",
        prev_generated_at="2099-01-01T00:00:00+00:00",  # 尚未过期
        client=client,
    )
    assert text == "这是我已有的简介。"
    assert generated is False
    assert client.calls == []  # 未调用 API


def test_generate_joins_text_blocks_and_strips_quotes():
    client = FakeClient(
        [
            SimpleNamespace(type="thinking", text="思考过程……"),
            _block("  「第一段简介。」  "),
            _block("第二段亮点。"),
        ]
    )
    text, _en, generated = generate_bio(
        PAYLOAD,
        AIConfig(model="claude-opus-5-5"),
        input_hash="sha256:new",
        prev_hash="sha256:old",
        prev_text="旧简介",
        prev_generated_at="2000-01-01T00:00:00+00:00",
        client=client,
    )
    assert generated is True
    assert text == "第一段简介。\n第二段亮点。"  # 只拼 text 块（块间换行），去引号
    assert client.calls[0]["model"] == "claude-opus-5-5"
    assert client.calls[0]["max_tokens"] >= 4096
    assert client.calls[0]["messages"][0]["role"] == "user"


def test_generate_api_error_falls_back_to_previous():
    client = FakeClient([], raise_exc=RuntimeError("api down"))
    text, _en, generated = generate_bio(
        PAYLOAD,
        AIConfig(),
        input_hash="sha256:new",
        prev_hash="sha256:old",
        prev_text="旧简介必须保留",
        prev_generated_at="2000-01-01T00:00:00+00:00",
        client=client,
    )
    assert text == "旧简介必须保留"
    assert generated is False


def test_generate_empty_response_falls_back_to_previous():
    client = FakeClient([_block("")])
    text, _en, generated = generate_bio(
        PAYLOAD,
        AIConfig(),
        input_hash="sha256:new",
        prev_hash="sha256:old",
        prev_text="旧简介",
        prev_generated_at="2000-01-01T00:00:00+00:00",
        client=client,
    )
    assert text == "旧简介"
    assert generated is False


def test_generate_no_previous_and_error_raises():
    client = FakeClient([], raise_exc=RuntimeError("api down"))
    with pytest.raises(BioError):
        generate_bio(
            PAYLOAD,
            AIConfig(),
            input_hash="sha256:new",
            prev_hash="",
            prev_text="",
            prev_generated_at="",
            client=client,
        )


# ---- OpenAI 兼容接口（DeepSeek/千问/Kimi/Ollama 等）----


def test_openai_provider_chat(monkeypatch):
    import profile_agent.bio as bio_mod

    captured = {}

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "「来自其他模型的简介正文」"}}]}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return FakeResp()

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    cfg = AIConfig(provider="openai", base_url="https://api.deepseek.com", key_env="DEEPSEEK_API_KEY", model="deepseek-chat")
    text, _en, generated = generate_bio(
        PAYLOAD, cfg,
        input_hash="h1", prev_hash="", prev_text="", prev_generated_at="",
        http_post=fake_post,
    )
    assert generated is True
    assert "其他模型" in text
    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer sk-test"
    assert captured["json"]["model"] == "deepseek-chat"
    # system 与 user 两条消息都传了
    roles = [m["role"] for m in captured["json"]["messages"]]
    assert roles == ["system", "user"]


def test_openai_provider_error_body_is_recorded(monkeypatch):
    """OpenAI 兼容分支失败时，服务商的原始消息要进失败汇总（requests 默认只给状态码）。"""
    from profile_agent import ai_failures

    ai_failures.reset()

    class FakeResp:
        status_code = 401
        text = '{"error":{"type":"server_error","message":"Upstream request failed: Go requires a credential"}}'

        def raise_for_status(self):
            raise AssertionError("状态码 >= 400 时应先带 body 报错")

    monkeypatch.setenv("OPENCODE_KEY", "oc_sk_test")
    cfg = AIConfig(
        provider="openai",
        base_url="https://opencode.ai/inference/go/openai/v1",
        key_env="OPENCODE_KEY",
        model="deepseek-v4.1-flash",
    )
    with pytest.raises(Exception, match="HTTP 401"):
        generate_bio(
            PAYLOAD, cfg,
            input_hash="h", prev_hash="", prev_text="", prev_generated_at="",
            http_post=lambda *a, **k: FakeResp(),
        )
    recorded = ai_failures.failures()
    ai_failures.reset()
    assert recorded, "失败应被记录"
    assert "Go requires a credential" in recorded[0].detail
    assert recorded[0].status == "401"
    assert recorded[0].kind == "凭据类型不匹配"


def test_openai_provider_reuses_fresh_bio_without_http(monkeypatch):
    called = []
    cfg = AIConfig(provider="openai", base_url="http://x", key_env="K")
    text, _en, generated = generate_bio(
        PAYLOAD, cfg,
        input_hash="same", prev_hash="same", prev_text="旧简介",
        prev_generated_at="2099-01-01T00:00:00+00:00",
        http_post=lambda *a, **k: called.append(1),
    )
    assert text == "旧简介" and generated is False and called == []


def test_chat_text_counts_usage_on_success(monkeypatch):
    """所有 AI 调用都走 chat_text：成功一次就计一次请求（摘要里的数字来源）。"""
    from profile_agent import usage
    from profile_agent.bio import chat_text

    usage.reset()

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "好"}}]}

    monkeypatch.setenv("K", "sk-test")
    cfg = AIConfig(provider="openai", base_url="http://x", key_env="K", model="m")
    chat_text(cfg, "s", "u", http_post=lambda *a, **k: FakeResp())

    data = usage.counters()
    assert data["ai_calls"] == 1
    assert data["ai_failures"] == 0
    usage.reset()


def test_chat_text_counts_usage_on_failure(monkeypatch):
    """失败也计一次请求 + 一次失败，摘要里「请求 N 次（失败 K）」才对得上。"""
    from profile_agent import ai_failures, usage
    from profile_agent.bio import chat_text

    usage.reset()
    ai_failures.reset()

    class FakeResp:
        status_code = 401
        text = "bad key"

    monkeypatch.setenv("K", "sk-test")
    cfg = AIConfig(provider="openai", base_url="http://x", key_env="K", model="m")
    with pytest.raises(Exception, match="HTTP 401"):
        chat_text(cfg, "s", "u", http_post=lambda *a, **k: FakeResp())

    data = usage.counters()
    assert data["ai_calls"] == 1
    assert data["ai_failures"] == 1
    usage.reset()
    ai_failures.reset()


def test_anthropic_custom_base_url_and_key_env(monkeypatch):
    """provider=anthropic 且填了 base_url（如 MiMo 的 Anthropic 兼容端点）时，创建默认 client 要带上。"""
    import sys
    import types

    import profile_agent.bio as bio_mod

    captured = {}

    class FakeMessages:
        def create(self, **kwargs):
            captured["create"] = kwargs
            return SimpleNamespace(content=[_block("自定义端点的简介")])

    class FakeAnthropic:
        def __init__(self, **kwargs):
            captured["init"] = kwargs
            self.messages = FakeMessages()

    fake_sdk = types.ModuleType("anthropic")
    fake_sdk.Anthropic = FakeAnthropic
    monkeypatch.setitem(sys.modules, "anthropic", fake_sdk)
    monkeypatch.setenv("AI_API_KEY", "mimo-key")

    cfg = AIConfig(
        provider="anthropic",
        base_url="https://api.xiaomimimo.com/anthropic",
        key_env="AI_API_KEY",
        model="mimo-v2.6-flash",
    )
    text, _en, generated = generate_bio(
        PAYLOAD, cfg,
        input_hash="h", prev_hash="", prev_text="", prev_generated_at="",
    )
    assert generated is True and "自定义端点" in text
    assert captured["init"]["base_url"] == "https://api.xiaomimimo.com/anthropic"
    assert captured["init"]["api_key"] == "mimo-key"
    assert captured["create"]["model"] == "mimo-v2.6-flash"


# ---- ai.headers 自定义请求头（2026-10-27，OpenCode Go 缺少 x-opencode-session 会被拒）----


def test_anthropic_passes_custom_headers(monkeypatch):
    import sys
    import types

    captured = {}

    class FakeMessages:
        def create(self, **kwargs):
            return SimpleNamespace(content=[_block("带自定义请求头的简介")])

    class FakeAnthropic:
        def __init__(self, **kwargs):
            captured["init"] = kwargs
            self.messages = FakeMessages()

    fake_sdk = types.ModuleType("anthropic")
    fake_sdk.Anthropic = FakeAnthropic
    monkeypatch.setitem(sys.modules, "anthropic", fake_sdk)
    monkeypatch.setenv("AI_API_KEY", "zen-key")

    cfg = AIConfig(
        provider="anthropic",
        base_url="https://opencode.ai/zen/go",
        key_env="AI_API_KEY",
        model="deepseek-v4.1-flash",
        headers={"x-opencode-session": "profile-agent"},
    )
    text, _en, generated = generate_bio(
        PAYLOAD, cfg,
        input_hash="h", prev_hash="", prev_text="", prev_generated_at="",
    )
    assert generated is True and "自定义请求头" in text
    assert captured["init"]["default_headers"] == {"x-opencode-session": "profile-agent"}


def test_openai_merges_custom_headers(monkeypatch):
    captured = {}

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "OpenAI 通道简介"}}]}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["headers"] = headers
        return FakeResp()

    monkeypatch.setenv("AI_API_KEY", "zen-key")
    cfg = AIConfig(
        provider="openai",
        base_url="https://opencode.ai/zen/go/v1",
        key_env="AI_API_KEY",
        model="deepseek-v4.1-flash",
        headers={"x-opencode-session": "profile-agent"},
    )
    text, _en, generated = generate_bio(
        PAYLOAD, cfg,
        input_hash="h2", prev_hash="", prev_text="", prev_generated_at="",
        http_post=fake_post,
    )
    assert generated is True and "OpenAI 通道" in text
    assert captured["headers"]["x-opencode-session"] == "profile-agent"
    assert captured["headers"]["Authorization"] == "Bearer zen-key"


# ============================ WP4 双语 AI（2026-10-23） ============================


def test_generate_bio_dual_language_single_call():
    """双语默认开：单次调用要求输出 JSON {zh,en}，两个语种一次拿回（≈1.3× 成本而非 2×）。"""
    cfg = AIConfig()
    payload = {**PAYLOAD, "bilingual": True}
    raw = (
        '{"zh": "中文简介。亮点：\\n- 开源工具作者",'
        ' "en": "English bio. Highlights:\\n- Open-source author"}'
    )
    client = FakeClient([_block(raw)])
    zh, en, generated = generate_bio(
        payload,
        cfg,
        input_hash="sha256:x",
        prev_hash="",
        prev_text="",
        prev_generated_at="",
        client=client,
    )
    assert generated is True
    assert zh.startswith("中文简介")
    assert en.startswith("English bio")
    assert len(client.calls) == 1  # 单次调用拿双语
    prompt = client.calls[0]["messages"][0]["content"]
    assert "简体中文" in prompt  # 既有语言要求字样保留（测试锁）
    assert "JSON" in prompt and '"zh"' in prompt


def test_bio_json_parse_failure_falls_back_zh_only():
    """模型没按 JSON 输出 → 整段降级单语：清洗后原文当 zh、en 置空（宁可单语不可失败）。"""
    cfg = AIConfig()
    client = FakeClient([_block("一段普通中文简介，没有 JSON。")])
    zh, en, generated = generate_bio(
        PAYLOAD,
        cfg,
        input_hash="sha256:x",
        prev_hash="",
        prev_text="",
        prev_generated_at="",
        client=client,
    )
    assert generated is True
    assert zh == "一段普通中文简介，没有 JSON。"
    assert en == ""


def test_generate_bio_reuse_returns_prev_en():
    """复用路径同时带回英文：输入未变且新鲜 → (prev_zh, prev_en, False)，零调用。"""
    cfg = AIConfig()
    client = FakeClient([_block("不应被调用")])
    zh, en, generated = generate_bio(
        PAYLOAD,
        cfg,
        input_hash="sha256:same",
        prev_hash="sha256:same",
        prev_text="旧中文简介",
        prev_text_en="Old English bio",
        prev_generated_at="2099-01-01T00:00:00+00:00",  # 尚未过期
    )
    assert (zh, en, generated) == ("旧中文简介", "Old English bio", False)
    assert client.calls == []


def test_bio_hash_stable_under_pin_reorder():
    """成本护栏：置顶顺序调整（同一批仓库）不改变 bio 输入指纹 → 不重新生成简介。"""
    from profile_agent.aggregate import bio_input_hash

    cfg = AIConfig()
    h1 = bio_input_hash(PAYLOAD, cfg)
    reordered = {**PAYLOAD, "repos": list(reversed(PAYLOAD["repos"]))}
    assert bio_input_hash(reordered, cfg) == h1


def test_bio_hash_changes_when_bilingual_flips():
    """bilingual 入指纹：单语↔双语切换触发一次重新生成（迁移时每用户一次，计划已明示）。"""
    from profile_agent.aggregate import bio_input_hash

    cfg = AIConfig()
    h_on = bio_input_hash({**PAYLOAD, "bilingual": True}, cfg)
    h_off = bio_input_hash({**PAYLOAD, "bilingual": False}, cfg)
    assert h_on != h_off


def test_bio_english_with_chinese_is_discarded(capsys):
    """AI 的英文简介整段写成中文 → 丢弃 en（zh 照常），下一轮 bio_fresh 会重试。"""
    raw = '{"zh": "中文简介正文。", "en": "这是用中文写成的英文简介，整段都该被丢弃。"}'
    client = FakeClient([_block(raw)])
    zh, en, generated = generate_bio(
        PAYLOAD,
        AIConfig(),
        input_hash="sha256:x",
        prev_hash="",
        prev_text="",
        prev_generated_at="",
        client=client,
    )
    assert generated is True
    assert zh.startswith("中文简介")
    assert en == ""
    assert "英文版" in capsys.readouterr().err


def test_bio_english_with_a_chinese_section_is_discarded(capsys):
    """英文简介里夹着成段中文（约三成汉字）同样丢弃：AI 输出阈值 0.20 更严。"""
    en = (
        "A small tool that turns notes into slides "
        "用来整理笔记并导出为演示文稿，支持批量处理。"
    )
    raw = json.dumps({"zh": "中文简介正文。", "en": en}, ensure_ascii=False)
    client = FakeClient([_block(raw)])
    zh, got_en, generated = generate_bio(
        PAYLOAD,
        AIConfig(),
        input_hash="sha256:x",
        prev_hash="",
        prev_text="",
        prev_generated_at="",
        client=client,
    )
    assert generated is True
    assert got_en == ""
    assert "英文版" in capsys.readouterr().err


def test_ai_failure_is_recorded_for_run_summary():
    """服务商失败（余额/认证）必须被记录，供运行摘要显式提示——不能只留一行 stderr。"""
    from profile_agent import ai_failures

    ai_failures.reset()

    class BoomClient:
        class messages:
            @staticmethod
            def create(**kwargs):
                raise RuntimeError("Insufficient account balance")

    with pytest.raises(BioError):
        generate_bio(
            PAYLOAD,
            AIConfig(),
            input_hash="h",
            prev_hash="",
            prev_text="",
            prev_generated_at="",
            client=BoomClient(),
        )
    recorded = ai_failures.failures()
    assert len(recorded) == 1
    assert recorded[0].kind == "额度/计费"
