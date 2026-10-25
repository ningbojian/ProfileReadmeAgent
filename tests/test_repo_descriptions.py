"""仓库描述写回（repo_descriptions）测试，全离线。

安全阀（设计顺序）：首次只记基线 → pushed_at 未变不动 → 手动改过永久跳过 →
每轮最多 5 个 → PATCH 响应值入库（GitHub 可能归一化）→ 失败下轮重试。
"""

import json
from datetime import datetime, timezone

import pytest

from profile_agent.config import AIConfig, RepoDescriptionsConfig
from profile_agent.github_client import API, GitHubClient, GitHubError
from profile_agent.models import Repo
from profile_agent.repo_descriptions import (
    BATCH_LIMIT,
    MAX_ATTEMPTS,
    MAX_DESCRIPTION_CHARS,
    apply_updates,
    build_prompt,
    describe_participants,
    generate_descriptions,
    is_dirty,
    iso_pushed,
    parse_description_map,
    plan_updates,
    summary_lines,
    target_language,
)
from fake_github import FakeHttp, FakeResponse

ALICE = "alice"


def _repo(
    name: str = "cool-tool",
    *,
    description: str = "旧描述",
    pushed: str = "2026-10-01T00:00:00+00:00",
    language: str = "Python",
    topics: list[str] | None = None,
) -> Repo:
    return Repo(
        name=name,
        full_name=f"{ALICE}/{name}",
        description=description,
        language=language,
        stars=3,
        pushed_at=datetime.fromisoformat(pushed),
        html_url=f"https://github.com/{ALICE}/{name}",
        topics=topics or [],
    )


def _entry(**kw) -> dict:
    e = {
        "baseline": "旧描述",
        "previous": "旧描述",
        "written": "旧描述",
        "pushed_at": "2026-10-01T00:00:00+00:00",
        "generated_at": "2026-10-01T00:00:00+00:00",
        "manual": False,
    }
    e.update(kw)
    return e


def _plan(repos, entries, cfg=None):
    return plan_updates(
        repos,
        cfg or RepoDescriptionsConfig(enabled=True),
        entries,
        ALICE,
        now_iso="2026-10-25T00:00:00+00:00",
    )


def _client(http: FakeHttp) -> GitHubClient:
    return GitHubClient("pat", session=http, sleep=lambda _s: None)


# ---- 参与名单与脏判定 ----


def test_describe_participants_honors_include_and_excludes_profile():
    repos = [
        _repo("cool-tool"),
        _repo("other"),
        Repo(name="alice", full_name="alice/alice", description="主页"),
    ]
    cfg = RepoDescriptionsConfig(enabled=True, include=["cool-tool"])
    assert [r.full_name for r in describe_participants(repos, cfg, ALICE)] == ["alice/cool-tool"]
    cfg_ex = RepoDescriptionsConfig(enabled=True, exclude=["alice/other"])
    assert [r.full_name for r in describe_participants(repos, cfg_ex, ALICE)] == ["alice/cool-tool"]


def test_is_dirty_covers_baseline_and_pushed_changes():
    cfg = RepoDescriptionsConfig(enabled=True)
    repo = _repo()
    assert is_dirty([repo], cfg, {}, ALICE) is True  # 首次：要记基线
    assert is_dirty([repo], cfg, {repo.full_name: _entry()}, ALICE) is False
    moved = _repo(pushed="2026-10-20T00:00:00+00:00")
    assert is_dirty([moved], cfg, {moved.full_name: _entry()}, ALICE) is True
    manual = {repo.full_name: _entry(manual=True)}
    assert is_dirty([repo], cfg, manual, ALICE) is False  # 手动改过：永不再动


def test_iso_pushed_handles_missing_value():
    assert iso_pushed(_repo()) == "2026-10-01T00:00:00+00:00"
    assert iso_pushed(Repo(name="x", full_name="a/x", pushed_at=None)) == ""


# ---- 计划：基线 / 手动 / 批量上限 ----


def test_first_sight_records_baseline_and_never_patches():
    entries: dict = {}
    candidates, notices, remaining = _plan([_repo()], entries)
    assert candidates == [] and remaining == 0
    e = entries["alice/cool-tool"]
    assert e["baseline"] == e["previous"] == e["written"] == "旧描述"
    assert e["pushed_at"] == "2026-10-01T00:00:00+00:00"
    assert e["manual"] is False
    assert any("基线" in n for n in notices)


def test_unchanged_pushed_at_is_not_a_candidate():
    repo = _repo()
    candidates, notices, _ = _plan([repo], {repo.full_name: _entry()})
    assert candidates == [] and notices == []


def test_manual_edit_after_write_is_skipped_forever():
    repo = _repo(description="我手改过的描述", pushed="2026-10-20T00:00:00+00:00")
    entries = {repo.full_name: _entry(written="工具上次写入")}
    candidates, notices, _ = _plan([repo], entries)
    assert candidates == []
    assert entries[repo.full_name]["manual"] is True
    assert any("手动" in n for n in notices)


def test_manual_edit_before_first_write_compares_baseline():
    """written 为空（还没写过）时，以 baseline 判断用户是否改过。"""
    repo = _repo(description="基线之后我改的", pushed="2026-10-20T00:00:00+00:00")
    entries = {repo.full_name: _entry(written="", baseline="原始描述")}
    candidates, notices, _ = _plan([repo], entries)
    assert candidates == []
    assert entries[repo.full_name]["manual"] is True


def test_candidate_when_pushed_at_moved_and_description_untouched():
    repo = _repo(pushed="2026-10-20T00:00:00+00:00")
    entries = {repo.full_name: _entry()}
    candidates, _, _ = _plan([repo], entries)
    assert [r.full_name for r in candidates] == ["alice/cool-tool"]


def test_batch_limit_keeps_newest_and_reports_remaining():
    repos = [
        _repo(f"r{i}", pushed=f"2026-10-{i:02d}T00:00:00+00:00")
        for i in range(1, 8)
    ]
    entries = {r.full_name: _entry(pushed_at="2026-09-01T00:00:00+00:00") for r in repos}
    candidates, _, remaining = _plan(repos, entries)
    assert len(candidates) == BATCH_LIMIT == 5
    assert [r.name for r in candidates] == ["r7", "r6", "r5", "r4", "r3"]
    assert remaining == 2


# ---- 目标语言 ----


def test_target_language_prefers_description_then_readme_then_config():
    cfg = AIConfig(language="zh-CN")
    assert target_language("一个中文工具", "", cfg) == "zh"
    assert target_language("A CLI tool", "", cfg) == "en"
    assert target_language("", "# 工具\n这是中文 README 的说明文字。", cfg) == "zh"
    assert target_language("", "# Tool\nThis README is written in English prose.", cfg) == "en"
    assert target_language("", "", cfg) == "zh"  # 兜底 ai.language
    assert target_language("123", "", AIConfig(language="zh-CN")) == "zh"


# ---- 生成与解析 ----


def test_build_prompt_contains_contract_and_facts():
    item = {
        "full_name": "alice/cool-tool",
        "description": "旧描述",
        "readme": "# 工具\n支持导出 CSV。",
        "language": "Python",
        "topics": ["cli", "csv"],
        "target": "zh",
    }
    prompt = build_prompt([item])
    assert "alice/cool-tool" in prompt and "旧描述" in prompt
    assert "支持导出 CSV" in prompt and "Python" in prompt
    assert '"alice/cool-tool"' in prompt  # JSON map 契约
    assert "350" in prompt and "中文" in prompt
    assert "不要编造" in prompt


def test_parse_description_map_strips_fences_and_clamps():
    long = "长" * (MAX_DESCRIPTION_CHARS + 50)
    raw = "```json\n" + json.dumps({"alice/x": long, "alice/y": "第一行\n第二行"}, ensure_ascii=False) + "\n```"
    parsed = parse_description_map(raw)
    assert parsed is not None
    assert len(parsed["alice/x"]) == MAX_DESCRIPTION_CHARS
    assert parsed["alice/y"] == "第一行 第二行"
    assert parse_description_map("不是 JSON") is None
    assert parse_description_map('["x"]') is None


def test_generate_descriptions_returns_map_and_warning_on_failure():
    calls = []

    def gen(system, prompt):
        calls.append((system, prompt))
        return json.dumps({"alice/cool-tool": "新描述"}, ensure_ascii=False)

    items = [{"full_name": "alice/cool-tool", "description": "旧描述", "readme": "",
              "language": "Python", "topics": [], "target": "zh"}]
    mapping, warning = generate_descriptions(items, AIConfig(), generate=gen)
    assert mapping == {"alice/cool-tool": "新描述"} and warning == ""
    assert len(calls) == 1

    mapping, warning = generate_descriptions(items, AIConfig(), generate=lambda *_: "坏 JSON")
    assert mapping == {} and "解析" in warning


# ---- 写回 ----


def _candidate_setup():
    repo = _repo(pushed="2026-10-20T00:00:00+00:00")
    entries = {repo.full_name: _entry()}
    candidates, _, _ = _plan([repo], entries)
    return repo, entries, candidates


def test_apply_updates_stores_github_normalized_value():
    repo, entries, candidates = _candidate_setup()
    http = FakeHttp().add(
        "PATCH",
        f"{API}/repos/alice/cool-tool",
        FakeResponse(200, {"description": "新描述（GitHub 归一化）"}),
    )
    results = apply_updates(
        _client(http), candidates, {repo.full_name: "新描述"},
        entries, now_iso="2026-10-25T00:00:00+00:00",
    )
    assert results[0]["status"] == "updated"
    e = entries[repo.full_name]
    assert e["written"] == "新描述（GitHub 归一化）"  # 存响应值，不存提交值
    assert e["previous"] == "旧描述"
    assert e["baseline"] == "旧描述"
    assert e["pushed_at"] == "2026-10-20T00:00:00+00:00"
    assert repo.description == "新描述（GitHub 归一化）"  # 同轮渲染立刻用新描述
    body = http.requests[-1]["json"]
    assert body == {"description": "新描述"}


def test_apply_updates_does_not_patch_when_generated_equals_current():
    """生成结果与当前一致也算本轮处理完：推进进度戳，下一轮不再重复调 AI。"""
    repo, entries, candidates = _candidate_setup()
    http = FakeHttp()
    results = apply_updates(
        _client(http), candidates, {repo.full_name: "旧描述"},
        entries, now_iso="2026-10-25T00:00:00+00:00",
    )
    assert results[0]["status"] == "unchanged"
    assert http.writes() == []
    e = entries[repo.full_name]
    assert e["pushed_at"] == "2026-10-20T00:00:00+00:00"  # 进度已推进
    assert e["generated_at"] == "2026-10-25T00:00:00+00:00"
    assert e["attempts"] == 0
    # 增量门随之关上：下一轮既不记基线也不排队，不再白烧一次批量 AI
    assert is_dirty([repo], RepoDescriptionsConfig(enabled=True), entries, ALICE) is False


def test_failed_and_skipped_accumulate_attempts():
    """失败与跳过都计入尝试次数；pushed_at 不前进，下轮继续重试。"""
    repo, entries, candidates = _candidate_setup()
    http = FakeHttp().add(
        "PATCH", f"{API}/repos/alice/cool-tool", FakeResponse(422, text="validation failed")
    )
    apply_updates(
        _client(http), candidates, {repo.full_name: "新描述"},
        entries, now_iso="2026-10-25T00:00:00+00:00",
    )
    assert entries[repo.full_name]["attempts"] == 1
    assert entries[repo.full_name]["pushed_at"].startswith("2026-10-01")
    apply_updates(_client(FakeHttp()), candidates, {}, entries, now_iso="2026-10-25T00:00:00+00:00")
    assert entries[repo.full_name]["attempts"] == 2
    assert entries[repo.full_name]["pushed_at"].startswith("2026-10-01")


def test_three_failures_park_until_next_push():
    """连续 3 轮失败：暂停该仓库，不再每轮白烧 AI；下次真实推送后自动恢复。"""
    repo, entries, candidates = _candidate_setup()
    http = FakeHttp().add(
        "PATCH", f"{API}/repos/alice/cool-tool", FakeResponse(422, text="validation failed")
    )
    for _ in range(MAX_ATTEMPTS):
        apply_updates(
            _client(http), candidates, {repo.full_name: "新描述"},
            entries, now_iso="2026-10-25T00:00:00+00:00",
        )
    e = entries[repo.full_name]
    assert e["attempts"] == MAX_ATTEMPTS == 3

    candidates2, notices, _ = _plan([repo], entries)
    assert candidates2 == []  # 已暂停，本轮不再排队
    assert e["pushed_at"] == "2026-10-20T00:00:00+00:00"  # 进度推进到当前推送
    assert e["attempts"] == 0
    assert any("暂停" in n for n in notices)
    assert is_dirty([repo], RepoDescriptionsConfig(enabled=True), entries, ALICE) is False

    moved = _repo(pushed="2026-10-24T00:00:00+00:00")
    candidates3, _, _ = _plan([moved], entries)
    assert [r.full_name for r in candidates3] == ["alice/cool-tool"]  # 新推送后恢复


def test_summary_lines_remaining_mentions_pushed_repos():
    lines = summary_lines([], 2)
    text = "\n".join(lines)
    assert "2" in text and "有推送的仓库" in text


def test_apply_updates_failure_leaves_entry_for_retry():
    repo, entries, candidates = _candidate_setup()
    http = FakeHttp().add(
        "PATCH", f"{API}/repos/alice/cool-tool", FakeResponse(422, text="validation failed")
    )
    results = apply_updates(
        _client(http), candidates, {repo.full_name: "新描述"},
        entries, now_iso="2026-10-25T00:00:00+00:00",
    )
    assert results[0]["status"] == "failed"
    e = entries[repo.full_name]
    assert e["pushed_at"].startswith("2026-10-01")
    assert e["written"] == "旧描述"
    assert repo.description == "旧描述"
    assert "422" in results[0]["error"]


def test_apply_updates_isolates_non_github_network_errors():
    """连接中断等非 GitHubError 异常同样逐仓隔离，不得让整轮任务崩溃。"""
    repo, entries, candidates = _candidate_setup()
    client = _client(FakeHttp())

    def boom(*args, **kwargs):
        raise RuntimeError("connection reset by peer")

    client._patch = boom
    results = apply_updates(
        client, candidates, {repo.full_name: "新描述"},
        entries, now_iso="2026-10-25T00:00:00+00:00",
    )
    assert results[0]["status"] == "failed"
    assert "connection reset" in results[0]["error"]
    assert entries[repo.full_name]["written"] == "旧描述"  # 条目未前进，下轮重试


def test_apply_updates_skips_missing_generated_description():
    repo, entries, candidates = _candidate_setup()
    http = FakeHttp()
    results = apply_updates(_client(http), candidates, {}, entries, now_iso="now")
    assert results[0]["status"] == "skipped"
    assert http.writes() == []


def test_summary_lines_cover_all_outcomes():
    lines = summary_lines(
        [
            {"full_name": "a/x", "status": "updated", "old": "旧", "new": "新"},
            {"full_name": "a/y", "status": "failed", "error": "boom"},
        ],
        3,
    )
    text = "\n".join(lines)
    assert "a/x" in text and "旧" in text and "新" in text
    assert "a/y" in text and "失败" in text
    assert "3" in text and "下轮" in text


def test_generate_descriptions_drops_wrong_language_values():
    """目标语言不符的结果丢弃（计入下轮重试），符合的保留。"""
    items = [
        {"full_name": "alice/zh-repo", "description": "", "readme": "",
         "language": "Python", "topics": [], "target": "zh"},
        {"full_name": "alice/en-repo", "description": "", "readme": "",
         "language": "Python", "topics": [], "target": "en"},
    ]
    raw = json.dumps(
        {
            "alice/zh-repo": "A CLI tool for exporting CSVs quickly.",
            "alice/en-repo": "一个把数据导出为 CSV 的命令行工具。",
        },
        ensure_ascii=False,
    )
    mapping, warning = generate_descriptions(items, AIConfig(), generate=lambda *_: raw)
    assert mapping == {}
    assert "不是中文/英文" in warning and "下轮重试" in warning

    raw_ok = json.dumps(
        {
            "alice/zh-repo": "一个把数据导出为 CSV 的命令行工具。",
            "alice/en-repo": "A CLI tool that exports data to CSV.",
        },
        ensure_ascii=False,
    )
    mapping_ok, warning_ok = generate_descriptions(items, AIConfig(), generate=lambda *_: raw_ok)
    assert set(mapping_ok) == {"alice/zh-repo", "alice/en-repo"}
    assert warning_ok == ""
