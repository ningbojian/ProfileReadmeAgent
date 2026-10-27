"""清单 issue/通知通道测试（测试计划 IW01-IW08、IW26，全离线）。

设计 §4-2、§5.5 文案草案、§5.7、§12-R8：唯一单份清单 issue（可原地改写、
全部结清自动关闭、永不发评论）、三结局文案、@你 + assignee 主动通知。
"""

import pytest
from fake_github import FakeHttp, FakeResponse

from profile_agent.checklist import (
    CHECKLIST_TITLE,
    append_final_state,
    build_checklist_body,
    parse_checked_prs,
    parse_merge_targets,
    sync_from_issue_event,
    sync_merge_all,
    update_checklist,
)
from profile_agent.github_client import API, GitHubClient, GitHubError

SELF = "alice/alice"
PR7 = {
    "full_name": "ningbojian/shiyuan",
    "html_url": "https://github.com/ningbojian/shiyuan/pull/7",
    "branch": "readme-auto/update",
}
PR43 = {
    "full_name": "ningbojian/profile-readme-agent",
    "html_url": "https://github.com/ningbojian/profile-readme-agent/pull/43",
    "branch": "readme-auto/update",
}


def _client(http: FakeHttp) -> GitHubClient:
    return GitHubClient("g-tok", session=http)


def _search_empty(http: FakeHttp):
    http.add("GET", f"{API}/repos/alice/alice/issues?state=open&per_page=100",
             FakeResponse(json_data=[]))


def _search_existing(http: FakeHttp, number=5, body="旧清单正文"):
    http.add("GET", f"{API}/repos/alice/alice/issues?state=open&per_page=100",
             FakeResponse(json_data=[{
                "number": number,
                "title": f"{CHECKLIST_TITLE}（待合并 PR）",
                "body": body,
                "html_url": f"https://x/issues/{number}",
            }]))


def test_iw01_create_checklist_issue_structure():
    """IW01：首次产出后创建清单 issue——assignee、时间戳标题、逐条链接+预览、三结局、@你。"""
    http = FakeHttp()
    _search_empty(http)
    http.add("POST", f"{API}/repos/alice/alice/issues",
             FakeResponse(status=201, json_data={"number": 5, "html_url": "https://x/issues/5"}))
    issue = update_checklist(_client(http), SELF, [PR7], username="alice", updated_at="2026-10-22 18:30")
    assert issue["number"] == 5
    assert issue["html_url"] == "https://x/issues/5"
    assert "/pull/7" in issue["body"]  # 回带刚写入的正文（收尾 append 的底稿，OR13）
    posts = [r for r in http.writes() if r["method"] == "POST"]
    assert len(posts) == 1
    assert posts[0]["json"]["assignees"] == ["alice"]  # ① 主动通知通道
    body = posts[0]["json"]["body"]
    assert "## 🔔 README 更新待合并清单（更新于 2026-10-22 18:30，北京时间）" in body  # ②
    assert "https://github.com/ningbojian/shiyuan/pull/7" in body  # ③ 逐条链接
    assert "https://github.com/ningbojian/shiyuan/blob/readme-auto/update/README.md" in body  # 预览链接
    assert "合并" in body and "关闭" in body and "先放着" in body  # 三结局
    assert "@alice" in body  # ④ @你
    assert "刷新" in body and "重读 diff" in body  # ⑤ 先放着条含刷新语义


def test_iw02_iw04_refresh_in_place_only_patch_keeps_links_and_adds_timestamp():
    """IW02/IW04：有遗留 open PR 仍刷新清单——仅 PATCH 原地改 body、不发评论、链接保留、时间戳刷新。"""
    http = FakeHttp()
    _search_existing(http, body="旧正文 https://x/pull/7\n")
    http.add("PATCH", f"{API}/repos/alice/alice/issues/5", FakeResponse(json_data={"number": 5}))
    issue = update_checklist(_client(http), SELF, [PR7], username="alice", updated_at="2026-10-22 19:00")
    assert issue["number"] == 5
    writes = http.writes()
    assert all(r["method"] == "PATCH" for r in writes)  # ④ 无 POST 无评论
    assert not any("comments" in r["url"] for r in writes)
    body = writes[0]["json"]["body"]
    assert "https://github.com/ningbojian/shiyuan/pull/7" in body  # ② 链接仍在
    assert "2026-10-22 19:00" in body  # 时间戳已刷新
    assert "合并" in body and "关闭" in body and "先放着" in body  # 三结局完整


def test_iw03_all_settled_auto_closes_issue():
    """IW03：全部 PR 结清（open_prs 为空）→ PATCH state=closed、无评论、关闭在刷新逻辑内。"""
    http = FakeHttp()
    _search_existing(http)
    http.add("PATCH", f"{API}/repos/alice/alice/issues/5", FakeResponse(json_data={"number": 5}))
    assert update_checklist(_client(http), SELF, [], username="alice", updated_at="t") is None
    writes = http.writes()
    assert [r["method"] for r in writes] == ["PATCH"]
    assert writes[0]["json"]["state"] == "closed"
    assert not any("comments" in r["url"] for r in writes)


def test_iw05_no_open_prs_and_no_existing_issue_creates_nothing():
    """IW05：无任何 open PR 且无既有清单 → 不开 issue、零写请求。"""
    http = FakeHttp()
    _search_empty(http)
    assert update_checklist(_client(http), SELF, [], username="alice", updated_at="t") is None
    assert http.writes() == []


def test_iw08_reuse_single_issue_with_multiple_prs():
    """IW08：已存在清单 → 复用单份不新建；body 同时含多条 PR，assignee/@ 保持。"""
    http = FakeHttp()
    _search_existing(http)
    http.add("PATCH", f"{API}/repos/alice/alice/issues/5", FakeResponse(json_data={"number": 5}))
    update_checklist(_client(http), SELF, [PR7, PR43], username="alice", updated_at="t")
    assert not http.has("POST", "/issues")
    body = [r for r in http.writes() if r["method"] == "PATCH"][0]["json"]["body"]
    assert "/pull/7" in body and "/pull/43" in body
    assignees = [r for r in http.writes() if r["method"] == "PATCH"][0]["json"]["assignees"]
    assert assignees == ["alice"]


def test_iw06_final_state_appends_overview_line():
    """IW06：收尾追加 Overview 终态行——仍原地 PATCH、无评论。

    有意变更（A-P0）：项目介绍功能已移除，project_pr_url 参数与项目介绍 PR
    链接一并下线，终态行只剩 Overview 进 main 一条。
    """
    http = FakeHttp()
    _search_existing(http, body="清单正文\n")
    http.add("PATCH", f"{API}/repos/alice/alice/issues/5", FakeResponse(json_data={"number": 5}))
    append_final_state(_client(http), SELF,
                       {"number": 5, "body": "清单正文\n"})
    writes = http.writes()
    assert [r["method"] for r in writes] == ["PATCH"]
    body = writes[0]["json"]["body"]
    assert "本轮 Overview 已进入 main" in body


def test_iw07_final_state_without_project_pr_has_no_dangling_link():
    """IW07：收尾仅追加终态行，正文无悬空死链（项目介绍字样恒不出现）。"""
    http = FakeHttp()
    http.add("PATCH", f"{API}/repos/alice/alice/issues/5", FakeResponse(json_data={"number": 5}))
    append_final_state(_client(http), SELF, {"number": 5, "body": "清单正文\n"})
    body = [r for r in http.writes() if r["method"] == "PATCH"][0]["json"]["body"]
    assert "本轮 Overview 已进入 main" in body
    assert "项目介绍" not in body


def test_iw07_no_issue_noop():
    """IW07 附：无清单 issue（已结清/从未创建）时收尾直接跳过、零写请求。"""
    http = FakeHttp()
    append_final_state(_client(http), SELF, None)
    assert http.writes() == []


def test_iw26_create_failure_raises_for_caller_isolation():
    """IW26：issue 创建 403 → 模块向上抛 GitHubError（cli 负责隔离不失败）。"""
    http = FakeHttp()
    _search_empty(http)
    http.add("POST", f"{API}/repos/alice/alice/issues", FakeResponse(status=403, text="forbidden"))
    with pytest.raises(GitHubError):
        update_checklist(_client(http), SELF, [PR7], username="alice", updated_at="t")


def test_body_builder_respects_custom_branch_in_preview():
    """预览链接跟随自定义 pr_branch（GP15 同款语义）。"""
    body = build_checklist_body(
        [dict(PR7, branch="docs/readme-bot")], username="alice", updated_at="t"
    )
    assert "/blob/docs/readme-bot/README.md" in body


def test_body_items_single_line_markdown_links_no_bare_urls():
    """2026-10-23 修复：条目用规范 markdown 链接、每仓恰一行。

    裸 URL 会被 GitHub 自动链接吞掉紧随其后的全角括号与中文（href 变成
    「…/pull/7（[新版」→ 点开 404），markdown 语法也被连带打坏、渲染拖成两行；
    改成 [PR](url) 写法后可见文本短、链接干净、每仓一行。
    """
    body = build_checklist_body([PR7, PR43], username="alice", updated_at="t")
    entries = [ln for ln in body.splitlines() if ln.startswith("- [ ]") and "[PR](" in ln]
    assert len(entries) == 2  # 每仓恰好一行
    assert entries[0] == (
        "- [ ] shiyuan：[PR](https://github.com/ningbojian/shiyuan/pull/7)"
        "（[新版 README 预览](https://github.com/ningbojian/shiyuan/blob/readme-auto/update/README.md)）"
    )
    assert entries[1] == (
        "- [ ] profile-readme-agent：[PR](https://github.com/ningbojian/profile-readme-agent/pull/43)"
        "（[新版 README 预览](https://github.com/ningbojian/profile-readme-agent/blob/readme-auto/update/README.md)）"
    )
    # 条目里绝不出现「：https://」形式的裸链接体——它就是 404 与两行问题的根源
    assert all("：https://" not in ln for ln in entries)


def test_parse_checked_prs_extracts_ticked_entries():
    """2026-10-23 勾选=合并授权：解析 [x] 条目得（仓库全名, PR 号）。

    仓库全名从条目里的 PR 链接 URL 提取（可见文本只有短名，URL 才含 owner/repo）。
    """
    body = build_checklist_body([PR7, PR43], username="alice", updated_at="t")
    ticked = body.replace("- [ ] shiyuan：", "- [x] shiyuan：")
    assert parse_checked_prs(ticked) == [("ningbojian/shiyuan", 7)]
    # 大写 [X] 同样算勾选（GitHub 手输大小写不敏感）
    ticked_upper = body.replace("- [ ] profile-readme-agent：", "- [X] profile-readme-agent：")
    assert parse_checked_prs(ticked_upper) == [("ningbojian/profile-readme-agent", 43)]


def test_parse_checked_prs_ignores_unticked_and_malformed():
    """未勾选与被手改坏的行不产生合并授权——解析必须防御，宁漏勿错。"""
    body = build_checklist_body([PR7, PR43], username="alice", updated_at="t")
    assert parse_checked_prs(body) == []  # 全部未勾 → 零授权
    malformed = (
        "- [x] 没有链接的坏行\n"
        "- [x] 半截链接：[PR](https://github.com/ningbojian/shiyuan/pull/\n"
        "- [x] shiyuan：[PR](https://github.com/ningbojian/shiyuan/pull/7)"
        "（[新版 README 预览](https://github.com/ningbojian/shiyuan/blob/readme-auto/update/README.md)）\n"
    )
    assert parse_checked_prs(malformed) == [("ningbojian/shiyuan", 7)]  # 仅完好的勾选行生效


def test_merge_targets_master_ticked_authorizes_all_entries():
    """Q1（一键全选）：总开关勾上 = 全部条目授权，即使逐条全是 [ ]；
    逐条口径 parse_checked_prs 不把总开关算作条目（保持原语义）。"""
    body = build_checklist_body([PR7, PR43], username="alice", updated_at="t")
    ticked = body.replace("- [ ] 全部合并", "- [x] 全部合并")
    assert parse_merge_targets(ticked) == [
        ("ningbojian/shiyuan", 7),
        ("ningbojian/profile-readme-agent", 43),
    ]
    assert parse_checked_prs(ticked) == []  # 逐条口径：一条都没勾
    # 总开关勾选时逐条另有勾选 → 并集仍是全部（全选语义覆盖逐条）
    both = ticked.replace("- [ ] shiyuan：", "- [x] shiyuan：")
    assert parse_merge_targets(both) == [
        ("ningbojian/shiyuan", 7),
        ("ningbojian/profile-readme-agent", 43),
    ]


def test_merge_targets_default_follows_individual_ticks():
    """总开关未勾：合并目标与逐条勾选完全一致（未勾=空、个别勾=个别）。"""
    body = build_checklist_body([PR7, PR43], username="alice", updated_at="t")
    assert parse_merge_targets(body) == []
    one = body.replace("- [ ] shiyuan：", "- [x] shiyuan：")
    assert parse_merge_targets(one) == [("ningbojian/shiyuan", 7)]


def test_body_detail_line_renders_stats_and_commit_titles():
    """W3（2026-10-23）：每条 PR 下附一行细节——文件数、增删行、提交数+标题。

    细节行缩进 2 格紧跟条目行（并入同一列表项、不新增可见层级）；
    提交标题取前 2 条、超出以「等」收尾——用户反馈「每个 pr 的细节太少」的直接回应。
    """
    pr = dict(
        PR7,
        changed_files=2,
        additions=34,
        deletions=11,
        commits=3,
        commit_titles=["修复拼写错误", "更新项目列表", "补充说明"],
    )
    body = build_checklist_body([pr], username="alice", updated_at="t")
    expected = "  ↳ 2 个文件 · +34/-11 行 · 3 次提交：修复拼写错误、更新项目列表 等"
    lines = body.splitlines()
    entry_idx = next(i for i, ln in enumerate(lines) if ln.startswith("- [ ] shiyuan："))
    assert lines[entry_idx + 1] == expected  # 紧跟条目、2 格缩进


def test_body_detail_line_single_title_truncated_and_count_only():
    """W3 细节行边界：恰 1 条标题不带「等」；超长标题 20 字截断防清单被撑长。"""
    pr = dict(
        PR43,
        changed_files=1,
        additions=5,
        deletions=0,
        commits=1,
        commit_titles=["fix: typo"],
    )
    body = build_checklist_body([pr], username="alice", updated_at="t")
    assert "  ↳ 1 个文件 · +5/-0 行 · 1 次提交：fix: typo\n" in body

    long_title = "这是一个特别特别长的提交标题需要被截断处理以免清单过长"
    long_pr = dict(
        PR7, changed_files=1, additions=1, deletions=0, commits=1,
        commit_titles=[long_title],
    )
    body_long = build_checklist_body([long_pr], username="alice", updated_at="t")
    assert f"1 次提交：{long_title[:20]}…" in body_long  # 20 字截断 + 省略号

    # 有统计无标题（body 缺失/旧格式）→ 只出计数，不编造标题
    count_only = dict(PR7, changed_files=1, additions=2, deletions=1, commits=2)
    body_count = build_checklist_body([count_only], username="alice", updated_at="t")
    assert "  ↳ 1 个文件 · +2/-1 行 · 2 次提交\n" in body_count


def test_body_without_stats_keeps_single_line_entries():
    """明细缺失（GET 失败/旧数据）→ 不渲染细节行，条目保持单行——宁缺勿错。"""
    body = build_checklist_body([PR7, PR43], username="alice", updated_at="t")
    assert "↳" not in body
    entries = [ln for ln in body.splitlines() if ln.startswith("- [ ]") and "[PR](" in ln]
    assert len(entries) == 2


def test_body_explains_checkbox_batch_merge_semantics():
    """W2（2026-10-23 用户问「checkbox 是有什么作用，能批量 merge 吗」）：清单里
    必须写明——勾选 = 授权任务自动合并，可多选批量；否则新人看不懂该不该勾。"""
    body = build_checklist_body([PR7], username="alice", updated_at="t")
    assert "勾选方框" in body
    assert "自动合并" in body
    assert "多选" in body  # 批量语义可见（回答「能否批量 merge」：能）


def test_body_merge_all_master_line_above_entries():
    """Q1（2026-10-23 用户问「能不能一键全选」）：GitHub 无原生全选按钮——清单头部
    提供一条「全部合并」总开关：勾上 = 一键授权下方全部条目；隐藏 token <!-- merge-all -->
    供解析锚定（用户改措辞也不丢语义），且总开关绝不含 PR 链接（不被当成普通条目）。"""
    body = build_checklist_body([PR7, PR43], username="alice", updated_at="t")
    lines = body.splitlines()
    master = [ln for ln in lines if ln.startswith("- [ ] 全部合并")]
    assert len(master) == 1  # ① 恰一条总开关
    assert "<!-- merge-all -->" in master[0]  # ② 解析锚点
    assert "[PR](" not in master[0]  # ③ 不是条目
    first_entry = next(i for i, ln in enumerate(lines) if ln.startswith("- [ ] shiyuan："))
    assert lines.index(master[0]) < first_entry  # ④ 总开关在条目之前（一眼可见）
    # 无条目（全部结清的关闭体）不渲染总开关——死控件只会误导
    # （只断言控件本身：正文其余说明里可以提到「全部合并」这个词）
    closed = build_checklist_body([], username="alice", updated_at="t")
    assert "merge-all" not in closed
    assert "- [ ] 全部合并" not in closed


def test_body_copy_says_manual_close_cancels_auth():
    """Q2（2026-10-23 追问「是不是必须等下一轮才能 Close」）：文案讲清顺序——
    勾选后无需手动关、全部处理完自动关、提前手动关会让勾选失效（任务只读 open 清单）。"""
    body = build_checklist_body([PR7], username="alice", updated_at="t")
    assert "提前手动关会让勾选失效" in body
    assert "自动关闭" in body


def test_body_mergeable_state_warning_renders_even_without_stats():
    """⚠️ 可合并状态徽章：dirty/blocked/draft 在明细行打头；无统计时也照渲染。

    有冲突的 PR 勾选合并必然 405 失败——徽章让用户合并前先看到，省一轮失败。
    """
    dirty = dict(PR7, mergeable_state="dirty")
    body = build_checklist_body([dirty], username="alice", updated_at="t")
    assert "  ↳ ⚠️ 有冲突" in body  # 无统计字段也有明细行（警告即细节）

    blocked = dict(PR43, mergeable_state="blocked", changed_files=1,
                   additions=2, deletions=0, commits=1)
    body_b = build_checklist_body([blocked], username="alice", updated_at="t")
    assert "  ↳ ⚠️ 被分支保护规则阻挡 · 1 个文件" in body_b  # 警告打头、统计随后

    # clean/draft-无/缺失 → 不打旗（不噪音）
    clean = dict(PR7, mergeable_state="clean")
    assert "⚠️" not in build_checklist_body([clean], username="alice", updated_at="t")


# ============================ 2026-10-23 交互文案治理（WP2） ============================


def test_body_header_explainer_within_three_lines():
    """头部总则 ≤3 行（标题 + @你 + 一行总则）——新人打开 issue 第一眼就看懂勾选语义，
    不用读完长文抓重点。"""
    body = build_checklist_body([PR7], username="alice", updated_at="t")
    header = []
    for ln in body.splitlines():
        if ln.startswith("- ["):
            break
        if ln.strip():
            header.append(ln)
    assert len(header) == 3  # 标题行、@行、总则行
    assert "勾选" in header[2] and "自动合并" in header[2]


def test_body_each_control_states_when_it_takes_effect():
    """每个控件一行说清「勾了/点了发生什么、何时生效」——回应用户反复追问的
    「我勾这个到底会不会造成那种后果」。"""
    body = build_checklist_body([PR7], username="alice", updated_at="t")
    assert "下次运行" in body  # 勾选的生效时机
    assert "立即生效" in body  # 手动 Merge 的生效时机
    assert "刷新时清除" in body  # 失败勾选的命运（修复后需重勾）
    assert "重新勾选" in body


def test_body_says_checkboxes_only_fill_missing_description_on_overview():
    """准确口径（核验 major 回填）：合并只落仓库；主页仅缺描述仓库的补位简介随后吸收，
    统计与其余内容不受影响——不得再写「主页本轮已经吸收」的过强承诺。"""
    body = build_checklist_body([PR7], username="alice", updated_at="t")
    assert "缺描述" in body and "随后吸收" in body
    assert "统计与其余内容不受影响" in body
    assert "主页本轮已经吸收" not in body


# ---- 全选联动（2026-10-27）：勾总开关带动条目，勾条目回写总开关 ----


def _sync_body(master: str, *ticks: str) -> str:
    """构造最小清单 body：总开关 + N 个条目 + 尾部说明。"""
    lines = [
        "## 🔔 README 更新待合并清单",
        f"- [{master}] 全部合并：说明<!-- merge-all -->",
    ]
    for i, tick in enumerate(ticks, 1):
        lines.append(
            f"- [{tick}] repo{i}：[PR](https://github.com/alice/repo{i}/pull/{i})"
            f"（[新版 README 预览](https://github.com/alice/repo{i}/blob/b/README.md)）"
        )
        lines.append("  ↳ 2 个文件 · +1/-1 行")
    lines += ["", "尾部说明"]
    return "\n".join(lines) + "\n"


def _states(body: str) -> tuple[str, list[str]]:
    """解析 body → (总开关状态, 各条目状态列表)。"""
    master = ""
    items: list[str] = []
    for line in body.splitlines():
        if "<!-- merge-all -->" in line:
            master = line[3]
        elif line.startswith("- [") and "/pull/" in line:
            items.append(line[3])
    return master, items


def test_sync_master_checked_ticks_every_entry():
    out = sync_merge_all(_sync_body(" ", " ", " ", " "), _sync_body("x", " ", " ", " "))
    assert _states(out) == ("x", ["x", "x", "x"])


def test_sync_master_unchecked_clears_every_entry():
    out = sync_merge_all(_sync_body("x", "x", "x", "x"), _sync_body(" ", "x", "x", "x"))
    assert _states(out) == (" ", [" ", " ", " "])


def test_sync_child_uncheck_clears_master_only():
    """手动取消一条 → 总开关跟着取消；其余条目保持用户刚点的样子。"""
    out = sync_merge_all(_sync_body("x", "x", "x", "x"), _sync_body("x", "x", " ", "x"))
    assert _states(out) == (" ", ["x", " ", "x"])


def test_sync_all_children_checked_checks_master():
    out = sync_merge_all(_sync_body(" ", "x", "x", " "), _sync_body(" ", "x", "x", "x"))
    assert _states(out) == ("x", ["x", "x", "x"])


def test_sync_ignores_edits_not_touching_checkboxes():
    """改说明文字不应产生任何改写（否则 bot 会无意义地回写、可能引发事件循环）。"""
    old = _sync_body(" ", "x", " ", "x")
    new = old.replace("尾部说明", "新的尾部说明")
    assert sync_merge_all(old, new) == new


def test_sync_is_idempotent():
    body = sync_merge_all(_sync_body(" ", " ", " "), _sync_body("x", " ", " "))
    assert sync_merge_all(body, body) == body


def test_sync_preserves_detail_lines_and_tail():
    out = sync_merge_all(_sync_body(" ", " ", " "), _sync_body("x", " ", " "))
    assert out.count("  ↳ 2 个文件 · +1/-1 行") == 2
    assert out.rstrip().endswith("尾部说明")
    assert "/blob/b/README.md" in out


def test_sync_without_master_or_entries_returns_unchanged():
    no_master = "## 清单\n- [ ] a：[PR](https://github.com/a/b/pull/1)\n"
    assert sync_merge_all(no_master, no_master) == no_master
    no_entries = "## 清单\n- [ ] 全部合并<!-- merge-all -->\n"
    assert sync_merge_all(no_entries, no_entries) == no_entries


class _RecordingClient:
    def __init__(self, fail: bool = False, fresh_body: str | None = None):
        self.calls: list[tuple[str, dict]] = []
        self.gets: list[str] = []
        self.fail = fail
        self.fresh_body = fresh_body  # None = GET 失败（读不到最新正文）

    def _get(self, url, **kwargs):
        self.gets.append(url)
        if self.fresh_body is None:
            return None, 404
        return {"number": 15, "body": self.fresh_body}, 200

    def _patch(self, url, payload, **kwargs):
        self.calls.append((url, payload))
        if self.fail:
            raise GitHubError("boom")
        return {}, {}


def _event(old: str, new: str, *, action: str = "edited") -> dict:
    return {
        "action": action,
        "issue": {"number": 15, "body": new},
        "changes": {"body": {"from": old}},
        "repository": {"full_name": "alice/alice"},
    }


def test_sync_from_event_patches_when_master_toggled():
    new = _sync_body("x", " ")
    client = _RecordingClient(fresh_body=new)
    assert sync_from_issue_event(
        _event(_sync_body(" ", " "), new), client
    ) is True
    assert len(client.calls) == 1
    url, payload = client.calls[0]
    assert url == f"{API}/repos/alice/alice/issues/15"
    assert _states(payload["body"]) == ("x", ["x"])
    # 回写前先读服务端最新正文（防覆盖用户在排队期间的新编辑）
    assert client.gets == [f"{API}/repos/alice/alice/issues/15"]


def test_sync_from_event_noop_when_nothing_relevant_changed():
    old = _sync_body(" ", " ")
    new = old + "\n补充一句\n"
    client = _RecordingClient(fresh_body=new)
    assert sync_from_issue_event(_event(old, new), client) is False
    assert client.calls == []


def test_sync_from_event_skips_when_user_edited_again():
    """事件排队期间用户又点了一次：按旧正文回写会把新编辑覆盖掉。

    （用户侧的可见症状是随后再点一下弹「Could not update issue comment」。）
    以服务端最新正文为准：不一致就不写，交给排在后面的那一轮——它拿到的
    正是最新 body。
    """
    stale_new = _sync_body("x", " ", " ")  # 本次事件：刚勾上总开关
    newer = _sync_body("x", "x", " ")  # 紧接着用户又取消了一个条目
    client = _RecordingClient(fresh_body=newer)
    assert sync_from_issue_event(
        _event(_sync_body(" ", " ", " "), stale_new), client
    ) is False
    assert client.calls == []


def test_sync_from_event_skips_when_current_body_unreadable():
    """读不到最新正文时不猜着写：下一轮（下一次编辑触发）再同步。"""
    client = _RecordingClient(fresh_body=None)
    assert sync_from_issue_event(
        _event(_sync_body(" ", " "), _sync_body("x", " ")), client
    ) is False
    assert client.calls == []


def test_sync_from_event_ignores_non_edit_and_missing_old_body():
    client = _RecordingClient()
    assert sync_from_issue_event(
        _event(_sync_body(" ", " "), _sync_body("x", " "), action="opened"), client
    ) is False
    event = _event(_sync_body(" ", " "), _sync_body("x", " "))
    del event["changes"]
    assert sync_from_issue_event(event, client) is False
    assert client.calls == []
    assert client.gets == []  # 不是编辑事件就不该发任何请求


def test_sync_from_event_propagates_patch_failure():
    """HTTP 细节留给纯函数之外的入口；失败由 cli 层降级为 warning。"""
    new = _sync_body("x", " ")
    client = _RecordingClient(fail=True, fresh_body=new)
    with pytest.raises(GitHubError):
        sync_from_issue_event(_event(_sync_body(" ", " "), new), client)
