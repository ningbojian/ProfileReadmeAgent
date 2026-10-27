"""usage.py：运行级 AI 请求 / 缓存复用统计（简历上可验证的数字来源）。"""

from profile_agent import usage


def test_reset_clears_all_counters():
    usage.reset()
    usage.note_ai_call()
    usage.note_ai_call(ok=False)
    usage.note_cache("bio")
    usage.reset()
    assert usage.counters()["ai_calls"] == 0
    assert usage.counters()["ai_failures"] == 0


def test_counters_accumulate_calls_and_failures():
    usage.reset()
    usage.note_ai_call()
    usage.note_ai_call()
    usage.note_ai_call(ok=False)
    data = usage.counters()
    assert data["ai_calls"] == 3
    assert data["ai_failures"] == 1


def test_note_cache_accumulates_by_kind_ignoring_zero():
    usage.reset()
    usage.note_cache("bio")
    usage.note_cache("repo_readme", 6)
    usage.note_cache("desc", 0)
    assert usage.counters()["cache"] == {"bio": 1, "repo_readme": 6}


def test_summary_lines_without_any_activity():
    usage.reset()
    lines = usage.summary_lines(5.0)
    assert lines[0] == "本轮 AI 用量：请求 0 次（失败 0）· 缓存复用 0 项"
    assert lines[-1] == "耗时：5 秒"


def test_summary_lines_lists_cache_kinds_in_fixed_order():
    """顺序固定 → 每次运行的数字可以直接对比，不会因字典顺序漂移。"""
    usage.reset()
    usage.note_ai_call()
    usage.note_cache("repo_readme", 6)
    usage.note_cache("bio")
    usage.note_cache("desc", 2)
    lines = usage.summary_lines(72.0)
    assert "缓存复用 9 项（简介 1、描述翻译 2、仓库 README 6）" in lines[0]
    assert lines[-1] == "耗时：1 分 12 秒"


def test_summary_lines_reports_repo_skip_rate():
    """仓库 README 无需重跑率：分母是参与本轮的全部仓库，404 等错误也计入。"""
    usage.reset()
    stats = {"queued": 1, "no_change": 4, "readme_only": 1, "first_seen": 1, "error": 1}
    lines = usage.summary_lines(10.0, repo_stats=stats)
    assert any("无需重跑：6/8（75%）" in line for line in lines)


def test_summary_lines_omits_repo_rate_when_mode_b_disabled():
    usage.reset()
    lines = usage.summary_lines(10.0, repo_stats=None)
    assert not any("无需重跑" in line for line in lines)


def test_duration_formatting_covers_minutes_and_hours():
    assert usage.format_duration(0) == "0 秒"
    assert usage.format_duration(59.9) == "59 秒"
    assert usage.format_duration(60) == "1 分 0 秒"
    assert usage.format_duration(3723) == "1 小时 2 分 3 秒"
