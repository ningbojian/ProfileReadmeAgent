"""运行级用量统计：AI 请求次数与缓存复用项数。

用途只有一个：把「增量更新到底省了多少调用」变成可验证的数字，写进日志与
Actions 摘要——否则它只是形容词，没法用来判断工具是否真的在省额度。
"""

from __future__ import annotations

# 固定展示顺序：每次运行的数字可以直接横向对比，不随字典顺序漂移
_CACHE_ORDER = ("bio", "short_intro", "desc", "repo_readme")
_CACHE_LABELS = {
    "bio": "简介",
    "short_intro": "描述补位",
    "desc": "描述翻译",
    "repo_readme": "仓库 README",
}

_ai_calls = 0
_ai_failures = 0
_cache: dict[str, int] = {}


def reset() -> None:
    """每轮运行开始时清空（进程内可能跑多轮，测试也依赖它）。"""
    global _ai_calls, _ai_failures
    _ai_calls = 0
    _ai_failures = 0
    _cache.clear()


def note_ai_call(ok: bool = True) -> None:
    """记一次真实发出的模型请求；ok=False 时同时计入失败。"""
    global _ai_calls, _ai_failures
    _ai_calls += 1
    if not ok:
        _ai_failures += 1


def note_cache(kind: str, count: int = 1) -> None:
    """记 count 项因缓存命中而省下的调用（kind 见 _CACHE_LABELS）。"""
    if count <= 0:
        return
    _cache[kind] = _cache.get(kind, 0) + count


def counters() -> dict:
    return {"ai_calls": _ai_calls, "ai_failures": _ai_failures, "cache": dict(_cache)}


def format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours} 小时 {minutes} 分 {secs} 秒"
    if minutes:
        return f"{minutes} 分 {secs} 秒"
    return f"{secs} 秒"


def _cache_detail() -> str:
    parts = [
        f"{_CACHE_LABELS[kind]} {_cache[kind]}"
        for kind in _CACHE_ORDER
        if _cache.get(kind)
    ]
    parts += [f"{kind} {n}" for kind, n in _cache.items() if kind not in _CACHE_ORDER and n]
    return "、".join(parts)


def summary_lines(elapsed_seconds: float, repo_stats: dict | None = None) -> list[str]:
    """运行摘要里的用量行；repo_stats 为模式 B 的计数（无模式 B 时传 None）。"""
    total_cached = sum(_cache.values())
    line = f"本轮 AI 用量：请求 {_ai_calls} 次（失败 {_ai_failures}）· 缓存复用 {total_cached} 项"
    detail = _cache_detail()
    if detail:
        line += f"（{detail}）"
    lines = [line]
    if repo_stats:
        skipped = sum(
            int(repo_stats.get(k) or 0) for k in ("no_change", "readme_only", "first_seen")
        )
        total = skipped + sum(int(repo_stats.get(k) or 0) for k in ("queued", "error", "reset"))
        if total:
            rate = round(skipped * 100 / total)
            lines.append(f"仓库 README 无需重跑：{skipped}/{total}（{rate}%）")
    lines.append(f"耗时：{format_duration(elapsed_seconds)}")
    return lines
