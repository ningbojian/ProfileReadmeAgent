"""ai_failures：把服务商侧失败（额度/认证/请求头/限流）说成人话。"""

import pytest

from profile_agent import ai_failures


class FakeStatusError(Exception):
    def __init__(self, msg: str, status_code: int | None = None):
        super().__init__(msg)
        self.status_code = status_code


@pytest.fixture(autouse=True)
def _clean():
    ai_failures.reset()
    yield
    ai_failures.reset()


def test_billing_message_wins_over_401_status():
    """OpenCode Go 用 401 + 计费文案；必须判成额度问题，而不是「认证失败」。"""
    exc = FakeStatusError(
        "Error code: 401 - {'type': 'error', 'error': {'type': 'api_error', "
        "'message': 'Upstream request failed: Go requires a credit card'}}",
        401,
    )
    f = ai_failures.classify(exc, "anthropic", "deepseek-v4.1-flash")
    assert f.kind == "额度/计费"
    assert "控制台" in f.hint
    assert f.status == "401"


def test_missing_session_header_is_explained():
    exc = FakeStatusError(
        "Error code: 400 - MissingSessionID: Request is missing x-opencode-session",
        400,
    )
    f = ai_failures.classify(exc, "anthropic", "m")
    assert f.kind == "缺少必需请求头"
    assert "ai.headers" in f.hint


def test_plain_401_is_auth_failure():
    f = ai_failures.classify(FakeStatusError("Error code: 401 - unauthorized", 401), "anthropic", "m")
    assert f.kind == "认证失败"


def test_429_is_rate_limited():
    f = ai_failures.classify(FakeStatusError("Error code: 429 - slow down", 429), "openai", "m")
    assert f.kind == "触发限流"


def test_timeout_is_network():
    f = ai_failures.classify(TimeoutError("read timed out"), "anthropic", "m")
    assert f.kind == "网络或服务端"


def test_record_dedupes_and_caps():
    exc = FakeStatusError("Insufficient account balance", 402)
    ai_failures.record(exc, "anthropic", "m")
    ai_failures.record(exc, "anthropic", "m")
    assert len(ai_failures.failures()) == 1


def test_summary_empty_without_failures():
    assert ai_failures.summary_markdown() == ""
    assert ai_failures.annotations() == []


def test_summary_lists_kind_detail_and_hint():
    ai_failures.record(FakeStatusError("Insufficient account balance", 402), "anthropic", "deepseek-v4.1-flash")
    md = ai_failures.summary_markdown()
    assert "AI 调用失败" in md
    assert "额度/计费" in md
    assert "Insufficient account balance" in md
    assert "不是本工具" in md
    ann = ai_failures.annotations()
    assert len(ann) == 1 and ann[0].startswith("::warning::")


def test_long_detail_is_truncated_but_keeps_prefix():
    exc = FakeStatusError("x" * 1000, 500)
    f = ai_failures.classify(exc, "anthropic", "m")
    assert len(f.detail) <= ai_failures.MAX_DETAIL
