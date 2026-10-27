"""AI 调用故障归类 + 运行级汇总。

服务商侧的问题（余额不足、key 失效、缺请求头、限流）在工具里都是「降级沿用缓存」，
如果只打一行 stderr，用户会以为是工具坏了。这里把所有失败收口成结构化记录，
运行结束时统一写进 Actions 摘要 + `::warning::` 注解：说清楚是谁的问题、怎么处理。
"""

from __future__ import annotations

from dataclasses import dataclass

MAX_DETAIL = 300
MAX_ITEMS = 5

# 计费/额度关键字：OpenCode Go 用 401 + 计费文案，必须排在状态码判断之前
_QUOTA_KEYS = (
    "insufficient",
    "balance",
    "quota",
    "credit",
    "billing",
    "payment",
    "requires a cr",
)
_SESSION_KEYS = ("missingsessionid", "x-opencode-session")
_NETWORK_KEYS = ("timed out", "timeout", "connection", "network", "connect error")


@dataclass(frozen=True)
class AIFailure:
    provider: str
    model: str
    status: str  # HTTP 状态码；拿不到时为 "-"
    detail: str  # 原消息前 MAX_DETAIL 字
    kind: str  # 中文分类
    hint: str  # 处理建议


_FAILURES: list[AIFailure] = []


def _status_of(exc: Exception) -> str:
    code = getattr(exc, "status_code", None)
    if code is None:
        response = getattr(exc, "response", None)
        code = getattr(response, "status_code", None)
    return str(code) if code else "-"


def _one_line(text: str) -> str:
    return " ".join(str(text).split())[:MAX_DETAIL]


def classify(exc: Exception, provider: str, model: str) -> AIFailure:
    """把异常翻译成「谁的问题 + 怎么办」。顺序有意义：计费文案优先于 401。"""
    detail = _one_line(exc)
    low = detail.lower()
    status = _status_of(exc)

    if any(k in low for k in _SESSION_KEYS):
        kind = "缺少必需请求头"
        hint = (
            "服务商要求额外请求头（如 x-opencode-session）；"
            "在 config.yaml 的 ai.headers 里补上后重跑"
        )
    elif any(k in low for k in _QUOTA_KEYS):
        kind = "额度/计费"
        hint = "服务商账户余额或订阅额度不足——去服务商控制台充值/续订，与本工具无关"
    elif status in ("401", "403"):
        kind = "认证失败"
        hint = "AI key 无效、过期或无权限——检查 Secret 里的 key 是否填对"
    elif status == "429":
        kind = "触发限流"
        hint = "请求过于频繁或超出套餐额度——稍后重试，或升级套餐"
    elif status.startswith("5") or any(k in low for k in _NETWORK_KEYS):
        kind = "网络或服务端"
        hint = "服务商暂时不可用或网络异常——稍后重试即可"
    else:
        kind = "调用失败"
        hint = "检查 ai.provider / base_url / model 是否与服务商文档一致"

    return AIFailure(
        provider=provider or "-",
        model=model or "-",
        status=status,
        detail=detail,
        kind=kind,
        hint=hint,
    )


def record(exc: Exception, provider: str, model: str) -> AIFailure:
    """记录一次失败（同一条消息只记一次，最多 MAX_ITEMS 条），返回归类结果。"""
    failure = classify(exc, provider, model)
    key = (failure.kind, failure.detail)
    if len(_FAILURES) < MAX_ITEMS and all((f.kind, f.detail) != key for f in _FAILURES):
        _FAILURES.append(failure)
    return failure


def failures() -> list[AIFailure]:
    return list(_FAILURES)


def reset() -> None:
    """每轮运行开始时清空（进程内可能跑多轮，测试也依赖它）。"""
    _FAILURES.clear()


def _cell(text: str) -> str:
    return text.replace("|", "\\|")


def summary_markdown() -> str:
    """Actions 摘要块；没有失败时返回空串（调用方据此跳过）。"""
    if not _FAILURES:
        return ""
    lines = [
        "### ⚠️ AI 调用失败（本轮沿用缓存，不是本工具故障）",
        "",
        "| 服务商 | 模型 | 状态 | 原因 | 处理建议 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for f in _FAILURES:
        lines.append(
            f"| `{_cell(f.provider)}` | `{_cell(f.model)}` | {f.status} "
            f"| {f.kind}：{_cell(f.detail)} | {_cell(f.hint)} |"
        )
    lines += [
        "",
        "> 处理好上表后重跑一次即可；在修复前，简介、翻译与描述会继续沿用缓存内容。",
    ]
    return "\n".join(lines)


def annotations() -> list[str]:
    """GitHub Actions 注解：run 页面顶部会显示成黄色告警。"""
    return [f"::warning::AI 调用失败（{f.kind}）：{f.detail} —— {f.hint}" for f in _FAILURES]
