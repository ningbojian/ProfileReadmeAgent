"""repo_readmes_pr.py 测试（测试计划 GP01-GP19，git-data 推送与 PR 流，全离线）。

设计 §5.5：blob→tree→commit→ref→PR 全 API、同仓单条 open PR（force-push 刷新）、
标题带短名、body 三结局警示 + marker 说明 + commit 范围、合并检测推进到 head SHA（§12-R10）、
推送/检测流永不调用合并/关闭接口（GP16 安全边界）；合并仅限清单勾选授权的 merge_pr
（2026-10-23 §5.5 修订：勾选 = 人工授权，运行时代为合并）。
"""

from fake_github import FakeHttp, FakeResponse

from profile_agent.github_client import GitHubClient
from profile_agent.repo_readmes import QueueItem
from profile_agent.repo_readmes_ai import ProcessResult
from profile_agent.repo_readmes_pr import (
    PR_BRANCH,
    advance_baseline_from_merged_pr,
    build_pr_body,
    build_pr_title,
    get_pr_detail,
    get_pr_state,
    merge_pr,
    open_or_refresh_pr,
    parse_commit_titles,
    push_results,
    read_branch_block_hash,
    takeover_pr_body,
)

API = "https://api.github.com"
FULL = "alice/cool-tool"
REPO_URL = f"{API}/repos/alice/cool-tool"


def _result(**kw) -> ProcessResult:
    base = dict(
        full_name=FULL,
        status="generated",
        input_hash="sha256:abc",
        base_sha="base1234567",
        head_sha="def4567890",
        content="# 标题\n托管区正文\n",
        commits=[{"sha": "def4567890", "message": "feat: 新增 add 函数"}],
        files=[{"filename": "src/core.py", "patch": "+def add(a, b)"}],
    )
    base.update(kw)
    return ProcessResult(**base)


def _client(http: FakeHttp) -> GitHubClient:
    return GitHubClient("p-pat", session=http)


def _happy_gitdata(http: FakeHttp, *, ref_exists=False):
    """预置 git-data 五连与 pulls 响应。"""
    if ref_exists:
        http.add("GET", f"{REPO_URL}/git/ref/heads/readme-auto/update",
                 FakeResponse(json_data={"object": {"sha": "oldref00"}}))
        http.add("PATCH", f"{REPO_URL}/git/refs/heads/readme-auto/update",
                 FakeResponse(json_data={"object": {"sha": "commit1"}}))
    else:
        http.add("GET", f"{REPO_URL}/git/ref/heads/readme-auto/update",
                 FakeResponse(status=404, text="not found"))
        http.add("POST", f"{REPO_URL}/git/refs",
                 FakeResponse(status=201, json_data={"ref": "refs/heads/readme-auto/update"}))
    http.add("POST", f"{REPO_URL}/git/blobs", FakeResponse(status=201, json_data={"sha": "blob1"}))
    http.add("POST", f"{REPO_URL}/git/trees", FakeResponse(status=201, json_data={"sha": "tree1"}))
    http.add("POST", f"{REPO_URL}/git/commits", FakeResponse(status=201, json_data={"sha": "commit1"}))


def _open_pr_check(http: FakeHttp, prs: list):
    http.add(
        "GET",
        f"{REPO_URL}/pulls?state=open&head=alice:readme-auto/update&base=main",
        FakeResponse(json_data=prs),
    )


# ---- GP01：git-data 调用序列与载荷 ----


def test_push_sequence_blob_tree_commit_ref_pr_and_payloads():
    """GP01：按 blob→tree→commit→ref→PR 顺序执行且载荷正确；全程 api.github.com。"""
    http = FakeHttp()
    _open_pr_check(http, [])
    _happy_gitdata(http)
    http.add("POST", f"{REPO_URL}/pulls",
             FakeResponse(status=201, json_data={"number": 42, "html_url": "https://x/42"}))
    res = open_or_refresh_pr(
        _client(http),
        full_name=FULL,
        content="# 标题\n托管区正文\n",
        head_sha="def4567890",
        title=build_pr_title(FULL),
        body=build_pr_body(commits=[{"sha": "def4567890"}], base_sha="base1234567", head_sha="def4567890"),
    )
    assert (res.number, res.html_url, res.refreshed) == (42, "https://x/42", False)
    posts = [r for r in http.writes() if r["method"] == "POST"]
    assert [u for _, u in [(r["method"], r["url"]) for r in posts]] == [
        f"{REPO_URL}/git/blobs",
        f"{REPO_URL}/git/trees",
        f"{REPO_URL}/git/commits",
        f"{REPO_URL}/git/refs",
        f"{REPO_URL}/pulls",
    ]
    blob, tree, commit, ref, pr = posts
    assert blob["json"] == {"content": "# 标题\n托管区正文\n", "encoding": "utf-8"}
    assert tree["json"]["base_tree"] == "def4567890"  # 默认分支 HEAD
    assert tree["json"]["tree"] == [
        {"path": "README.md", "mode": "100644", "type": "blob", "sha": "blob1"}
    ]
    assert commit["json"]["parents"] == ["def4567890"]  # 基线侧 HEAD
    assert ref["json"]["ref"] == "refs/heads/readme-auto/update"
    assert ref["json"]["sha"] == "commit1"
    assert pr["json"]["head"] == "readme-auto/update" and pr["json"]["base"] == "main"
    assert all(r["url"].startswith(API) for r in http.requests)  # 无本地 git，纯 HTTP
    assert all(r["headers"]["Authorization"] == "Bearer p-pat" for r in http.requests)


# ---- GP02/GP03/GP04：同仓刷新与 PR 文案 ----


def test_open_pr_force_pushes_and_edits_same_pr():
    """GP02：已有 open PR → force-push 刷新同一条 + 编辑 body，不新开。"""
    http = FakeHttp()
    _open_pr_check(http, [{"number": 42, "html_url": "https://x/42"}])
    _happy_gitdata(http, ref_exists=True)
    http.add("PATCH", f"{REPO_URL}/pulls/42", FakeResponse(json_data={"number": 42}))
    body = build_pr_body(
        commits=[{"sha": "base1234567"}, {"sha": "def4567890"}],
        base_sha="base1234567",
        head_sha="def4567890",
    )
    res = open_or_refresh_pr(
        _client(http),
        full_name=FULL,
        content="x\n",
        head_sha="def4567890",
        title=build_pr_title(FULL),
        body=body,
    )
    assert res.number == 42 and res.refreshed is True
    assert not http.has("POST", "/pulls")  # 不新开
    ref_patch = [r for r in http.writes() if r["url"].endswith("/git/refs/heads/readme-auto/update")][0]
    assert ref_patch["json"] == {"sha": "commit1", "force": True}
    pr_edit = [r for r in http.writes() if r["url"].endswith("/pulls/42")][0]
    assert "def4567" in pr_edit["json"]["body"]  # 新短 SHA 已进 body
    assert "关闭本 PR = 拒绝这批文案" in pr_edit["json"]["body"]  # 三结局警示仍在


def test_title_carries_repo_short_name():
    """GP03：标题 docs(短名): README 自动更新（待审核）——多标签页靠标题分辨。"""
    assert build_pr_title("ningbojian/shiyuan") == "docs(shiyuan): README 自动更新（待审核）"


def test_body_has_three_outcomes_marker_note_and_commit_range():
    """GP04：body 首行三结局警示、marker 说明、commit 范围与短 SHA 列表。"""
    body = build_pr_body(
        commits=[{"sha": "c111111a00000000000000000000000000000001"}, {"sha": "d222222b00000000000000000000000000000002"}],
        base_sha="c111111a00000000000000000000000000000001",
        head_sha="d222222b00000000000000000000000000000002",
    )
    first = next(line for line in body.splitlines() if line.strip())
    assert "关闭本 PR = 拒绝这批文案" in first
    assert "先不处理" in first and "合并前请重读 diff" in first
    assert "profile-agent:readme" in body  # marker 说明段
    assert "HTML 注释" in body and "渲染页不可见" in body
    assert "删掉区间 = 放弃" in body
    assert "c111111a..d222222b" in body  # base..head 范围（短 SHA 取 [:8]，与全库口径一致）
    assert "c111111a" in body and "d222222b" in body  # 短 SHA 列表


# ---- GP05-GP12：抑制、静默、重开（push_results 层）----


def test_cached_results_produce_zero_writes():
    """GP05/GP09/GP12：缓存命中（closed-unmerged 同哈希 / 分支一致 / squash 重放）→ 零写请求。"""
    http = FakeHttp()
    res = push_results(
        _client(http),
        [_result(status="cached")],
        now=None,
    )
    assert res == []
    assert http.writes() == []  # 连推送、连 PATCH pulls 都没有（Last Update 不动）
    assert http.requests == []  # 读请求也没有：抑制判定不依赖 PR 状态查询（§5.1）


def test_new_batch_reopens_pr_with_full_commit_range():
    """GP06：新提交改变哈希 → 作为新批次重开；body 带被拒批次+新提交短 SHA（刷新即全量）。"""
    http = FakeHttp()
    _open_pr_check(http, [])  # closed-unmerged 后无 open PR
    _happy_gitdata(http)
    http.add("POST", f"{REPO_URL}/pulls",
             FakeResponse(status=201, json_data={"number": 43, "html_url": "https://x/43"}))
    res = push_results(
        _client(http),
        [_result(commits=[{"sha": "old00001"}, {"sha": "new00002"}])],
        now=None,
    )
    assert [r.number for r in res] == [43]
    pr_post = [r for r in http.writes() if r["url"].endswith("/pulls")][0]
    assert "old00001" in pr_post["json"]["body"] and "new00002" in pr_post["json"]["body"]


def test_push_is_per_repo_independent():
    """GP08：一仓被抑制（cached）不影响另一仓完整推送。"""
    http = FakeHttp()
    other = f"{API}/repos/alice/old-thing"
    http.add("GET", f"{other}/pulls?state=open&head=alice:readme-auto/update&base=main",
             FakeResponse(json_data=[]))
    http.add("GET", f"{other}/git/ref/heads/readme-auto/update", FakeResponse(status=404))
    http.add("POST", f"{other}/git/blobs", FakeResponse(status=201, json_data={"sha": "b2"}))
    http.add("POST", f"{other}/git/trees", FakeResponse(status=201, json_data={"sha": "t2"}))
    http.add("POST", f"{other}/git/commits", FakeResponse(status=201, json_data={"sha": "c2"}))
    http.add("POST", f"{other}/git/refs", FakeResponse(status=201, json_data={}))
    http.add("POST", f"{other}/pulls", FakeResponse(status=201, json_data={"number": 44, "html_url": "u"}))
    res = push_results(
        _client(http),
        [_result(status="cached"), _result(full_name="alice/old-thing")],
        now=None,
    )
    assert [r.number for r in res] == [44]
    # 被抑制仓（cool-tool）零请求；写请求全部指向 old-thing
    assert all("/repos/alice/old-thing/" in r["url"] for r in http.writes())


def test_force_generated_result_pushes_even_after_closed_batch():
    """GP11：force 重新生成的结果照常推送（抑制只拦 cached，不拦 generated）。"""
    http = FakeHttp()
    _open_pr_check(http, [])
    _happy_gitdata(http)
    http.add("POST", f"{REPO_URL}/pulls",
             FakeResponse(status=201, json_data={"number": 45, "html_url": "https://x/45"}))
    res = push_results(_client(http), [_result(status="generated")], now=None)
    assert [r.number for r in res] == [45]


# ---- GP10：分支漂移检测 ----


def test_branch_drift_detection_via_block_hash():
    """GP10：分支内容被改动 → read_branch_block_hash 与缓存 block_hash 不一致（重推信号）。"""
    import base64

    from profile_agent.readme_block import END, START

    drifted = f"# 标题\n{START}\n人工改过的托管区\n{END}\n"
    http = FakeHttp()
    http.add(
        "GET",
        f"{REPO_URL}/contents/README.md?ref=readme-auto/update",
        FakeResponse(json_data={
            "content": base64.b64encode(drifted.encode("utf-8")).decode("ascii"),
            "encoding": "base64",
        }),
    )
    h = read_branch_block_hash(_client(http), FULL, PR_BRANCH)
    assert h.startswith("sha256:") and h != "sha256:缓存值"
    # 与缓存同值 → 未漂移：同一内容两次读哈希一致
    http.add(
        "GET",
        f"{REPO_URL}/contents/README.md?ref=readme-auto/update",
        FakeResponse(json_data={
            "content": base64.b64encode(drifted.encode("utf-8")).decode("ascii"),
            "encoding": "base64",
        }),
    )
    assert read_branch_block_hash(_client(http), FULL, PR_BRANCH) == h


# ---- GP13/GP14：合并检测与基线 ----


def _entry(sha="abc1234", **kw):
    e = {
        "sha": sha,
        "block_hash": "",
        "mode": "block",
        "failed_hash": "",
        "last_input_hash": "",
        "pending": False,
        "updated_at": "old",
    }
    e.update(kw)
    return e


def test_merged_pr_advances_baseline_to_head_sha_not_merge_commit():
    """GP13：合并检测把基线推进到 head SHA 而非 merge commit；幂等。"""
    http = FakeHttp()
    http.add(
        "GET",
        f"{REPO_URL}/pulls?state=all&head=alice:readme-auto/update&base=main&sort=updated&direction=desc",
        FakeResponse(json_data=[{
            "number": 42,
            "merged": True,
            "merge_commit_sha": "MMM0000",
            "head": {"sha": "HHH1111"},
        }]),
        sticky=True,
    )
    entry = _entry("abc1234")
    assert advance_baseline_from_merged_pr(_client(http), entry, FULL, PR_BRANCH) is True
    assert entry["sha"] == "HHH1111"
    assert entry["sha"] != "MMM0000"
    # 幂等：重复检测不改写
    assert advance_baseline_from_merged_pr(_client(http), entry, FULL, PR_BRANCH) is False
    assert entry["sha"] == "HHH1111"


def test_open_pr_does_not_advance_baseline():
    """GP14：PR 仍 open（未合并）→ 基线不动。"""
    http = FakeHttp()
    http.add(
        "GET",
        f"{REPO_URL}/pulls?state=all&head=alice:readme-auto/update&base=main&sort=updated&direction=desc",
        FakeResponse(json_data=[{
            "number": 42,
            "merged": False,
            "merge_commit_sha": None,
            "head": {"sha": "HHH1111"},
        }]),
        sticky=True,
    )
    entry = _entry("abc1234")
    assert advance_baseline_from_merged_pr(_client(http), entry, FULL, PR_BRANCH) is False
    assert entry["sha"] == "abc1234"


# ---- GP15/GP16/GP17：自定义分支、安全边界、full 模式 ----


def test_custom_pr_branch_used_for_ref_and_pr_head():
    """GP15：config 自定义 pr_branch 生效于 ref 与 PR head，base 仍 main。"""
    http = FakeHttp()
    http.add("GET", f"{REPO_URL}/pulls?state=open&head=alice:docs/readme-bot&base=main",
             FakeResponse(json_data=[]))
    http.add("GET", f"{REPO_URL}/git/ref/heads/docs/readme-bot", FakeResponse(status=404))
    http.add("POST", f"{REPO_URL}/git/blobs", FakeResponse(status=201, json_data={"sha": "b"}))
    http.add("POST", f"{REPO_URL}/git/trees", FakeResponse(status=201, json_data={"sha": "t"}))
    http.add("POST", f"{REPO_URL}/git/commits", FakeResponse(status=201, json_data={"sha": "c"}))
    http.add("POST", f"{REPO_URL}/git/refs", FakeResponse(status=201, json_data={}))
    http.add("POST", f"{REPO_URL}/pulls", FakeResponse(status=201, json_data={"number": 7, "html_url": "u"}))
    res = open_or_refresh_pr(
        _client(http), full_name=FULL, content="x", head_sha="h1",
        title="t", body="b", branch="docs/readme-bot",
    )
    ref_post = [r for r in http.writes() if r["url"].endswith("/git/refs")][0]
    assert ref_post["json"]["ref"] == "refs/heads/docs/readme-bot"
    pr_post = [r for r in http.writes() if r["url"].endswith("/pulls")][0]
    assert pr_post["json"]["head"] == "docs/readme-bot" and pr_post["json"]["base"] == "main"
    assert res.number == 7


def test_never_calls_merge_or_close_endpoints():
    """GP16：全流程永不调用合并或关闭接口——写请求仅 git-data 与 PR 创建/编辑。"""
    http = FakeHttp()
    _open_pr_check(http, [{"number": 42}])
    _happy_gitdata(http, ref_exists=True)
    http.add("PATCH", f"{REPO_URL}/pulls/42", FakeResponse(json_data={}))
    http.add(
        "GET",
        f"{REPO_URL}/pulls?state=all&head=alice:readme-auto/update&base=main&sort=updated&direction=desc",
        FakeResponse(json_data=[]),
        sticky=True,
    )
    open_or_refresh_pr(
        _client(http), full_name=FULL, content="x", head_sha="h1", title="t", body="b"
    )
    advance_baseline_from_merged_pr(_client(http), _entry(), FULL, PR_BRANCH)
    assert http.writes()  # 确有写请求可检查
    for r in http.writes():
        u = r["url"]
        assert "/merge" not in u and r["method"] != "DELETE"
        # 写请求只允许：git-data 四件套 + PR 创建/编辑
        assert (
            u.endswith("/git/blobs")
            or u.endswith("/git/trees")
            or u.endswith("/git/commits")
            or "/git/refs" in u
            or "/pulls" in u
        )


def test_full_mode_blob_is_whole_generated_content():
    """GP17：mode=full 时 blob 载荷就是整份 AI 生成内容。"""
    http = FakeHttp()
    full_doc = "# My Tool\n\n完整文档正文。\n"
    _open_pr_check(http, [])
    _happy_gitdata(http)
    http.add("POST", f"{REPO_URL}/pulls", FakeResponse(status=201, json_data={"number": 9, "html_url": "u"}))
    open_or_refresh_pr(
        _client(http), full_name=FULL, content=full_doc, head_sha="h1", title="t", body="b"
    )
    blob = [r for r in http.writes() if r["url"].endswith("/git/blobs")][0]
    assert blob["json"]["content"] == full_doc  # 整份，无 marker 强加


def test_queue_item_unused_import_guard():
    """保持 QueueItem 导入被使用（lint 占位）。"""
    assert QueueItem is not None


# ---- 终审 I2 回归：PR base 跟随仓库默认分支（不再硬编码 main）----


def test_pr_base_follows_default_branch_keyword():
    """终审 I2 回归：base 可指仓库默认分支（master）——开 PR 检查 URL 与创建载荷都用它。"""
    http = FakeHttp()
    _happy_gitdata(http)
    http.add(
        "GET",
        f"{REPO_URL}/pulls?state=open&head=alice:readme-auto/update&base=master",
        FakeResponse(json_data=[]),
    )
    http.add(
        "POST", f"{REPO_URL}/pulls",
        FakeResponse(status=201, json_data={"number": 9, "html_url": "u"}),
    )
    res = open_or_refresh_pr(
        _client(http), full_name=FULL, content="# x", head_sha="def4567890",
        title="t", body="b", base="master",
    )
    assert res.number == 9
    pr_post = next(
        r for r in http.writes() if r["method"] == "POST" and r["url"].endswith("/pulls")
    )
    assert pr_post["json"]["base"] == "master"  # 载荷 base = 默认分支


def test_merged_pr_detection_respects_default_branch_base():
    """终审 I2 回归：合并检测的 base 同样跟随默认分支（master 仓库不再永远检不到合并、
    基线永不推进）。"""
    http = FakeHttp()
    http.add(
        "GET",
        f"{REPO_URL}/pulls?state=all&head=alice:readme-auto/update"
        "&base=master&sort=updated&direction=desc",
        FakeResponse(json_data=[{"number": 3, "merged": True, "head": {"sha": "newsha1"}}]),
    )
    entry = {"sha": "oldsha"}
    assert advance_baseline_from_merged_pr(
        _client(http), entry, FULL, "readme-auto/update", base="master"
    )
    assert entry["sha"] == "newsha1"


def test_merged_pr_detection_reads_merged_at_field():
    """真实 LIST API（Pull Request Simple 形状）不带 merged 布尔字段——检测必须认 merged_at。

    生产缺口回归：`merged` 只有测试假件才有 → 真实环境合并检测永不触发、基线永不推进。
    """
    http = FakeHttp()
    http.add(
        "GET",
        f"{REPO_URL}/pulls?state=all&head=alice:readme-auto/update&base=main&sort=updated&direction=desc",
        FakeResponse(json_data=[{
            "number": 42,
            "merged_at": "2026-10-23T08:00:00Z",  # Simple 形状：无 merged 键
            "merge_commit_sha": "MMM0000",
            "head": {"sha": "HHH1111"},
        }]),
        sticky=True,
    )
    entry = _entry("abc1234")
    assert advance_baseline_from_merged_pr(_client(http), entry, FULL, PR_BRANCH) is True
    assert entry["sha"] == "HHH1111"


# ---- 勾选授权的批量合并（2026-10-23 §5.5 修订：勾选 = 人工授权，运行时代为合并）----


def test_merge_pr_puts_merge_endpoint_without_method_override():
    """勾选授权合并：PUT /pulls/{n}/merge，不传 merge_method（走仓库默认合并方式）。"""
    http = FakeHttp()
    http.add(
        "PUT", f"{REPO_URL}/pulls/42/merge",
        FakeResponse(json_data={"merged": True, "sha": "a" * 40,
                                "message": "Pull Request successfully merged"}),
    )
    ok, why = merge_pr(_client(http), FULL, 42)
    assert ok is True and why == ""  # 成功：无失败原因
    w = http.writes()[0]
    assert w["method"] == "PUT"
    assert w["url"] == f"{REPO_URL}/pulls/42/merge"
    # 不显式指定合并方式 → GitHub 用仓库配置的默认方式（squash-only 仓库也不会被我们改成 merge）
    assert not (w["json"] or {}).get("merge_method")


def test_merge_pr_failure_returns_reason_instead_of_raising():
    """405 不可合并（冲突/已关）→ (False, 可读原因)，绝不向外抛——调用方逐仓隔离。"""
    http = FakeHttp()
    http.add("PUT", f"{REPO_URL}/pulls/42/merge",
             FakeResponse(status=405, text="Pull Request is not mergeable"))
    ok, why = merge_pr(_client(http), FULL, 42)
    assert ok is False
    assert "405" in why  # 原因带状态码，Summary/日志可诊断


def test_merge_pr_404_also_isolated_with_reason():
    """PR 已不存在（404）同样只返回失败原因不抛——批量合并整体不因单点中断。"""
    http = FakeHttp()
    http.add("PUT", f"{REPO_URL}/pulls/42/merge",
             FakeResponse(status=404, text="not found"))
    ok, why = merge_pr(_client(http), FULL, 42)
    assert ok is False and why


# ---- W3 清单明细（2026-10-23 用户反馈「issue 里每个 pr 的细节太少」）----


def test_get_pr_detail_single_get_returns_stats_and_body():
    """W3：list 接口不带增删统计——逐 PR 单 GET /pulls/{n} 取明细（每仓每轮恰一次）。"""
    http = FakeHttp()
    http.add(
        "GET",
        f"{REPO_URL}/pulls/7",
        FakeResponse(
            json_data={
                "number": 7,
                "additions": 34,
                "deletions": 11,
                "changed_files": 2,
                "commits": 3,
                "body": "三结局警示\n\n涉及提交：\n- `abcdef12` 修复拼写错误\n",
            }
        ),
    )
    det = get_pr_detail(_client(http), FULL, 7)
    assert det["additions"] == 34 and det["deletions"] == 11
    assert det["changed_files"] == 2 and det["commits"] == 3
    assert [r["method"] for r in http.requests] == ["GET"]  # 恰一次，无副作用
    assert http.requests[0]["url"] == f"{REPO_URL}/pulls/7"


def test_parse_commit_titles_round_trips_build_pr_body():
    """W3：与 build_pr_body 对偶——「涉及提交」列表零额外取数还原标题（取每条首行）。"""
    body = build_pr_body(
        commits=[
            {"sha": "abcdef1234", "message": "fix: 修复拼写错误\n\n详细说明正文"},
            {"sha": "0123456789", "message": "feat: 更新项目列表"},
        ],
        base_sha="base1234567",
        head_sha="def4567890",
    )
    assert parse_commit_titles(body) == ["fix: 修复拼写错误", "feat: 更新项目列表"]
    # 无提交（sha_lines 为空）→ 空列表，不误读 marker 之后的散文段
    empty = build_pr_body(commits=[], base_sha="base1234567", head_sha="def4567890")
    assert parse_commit_titles(empty) == []
    # marker 之前的内容不掺入
    assert parse_commit_titles("三结局警示\n覆盖提交范围：`a..b`\n涉及提交：\n") == []


# ---- 勾选合并降噪（2026-10-23 WP2：PUT 前先 GET 状态，已合并/已关闭不再吓人） ----


def test_get_pr_state_merged_via_merged_at():
    """真实 LIST/GET API 只有 merged_at（null/ISO）无 merged 布尔——merged_at 命中即 merged。"""
    http = FakeHttp()
    http.add("GET", f"{REPO_URL}/pulls/7",
             FakeResponse(json_data={"number": 7, "state": "closed",
                                     "merged_at": "2026-10-23T02:00:00Z"}))
    assert get_pr_state(_client(http), FULL, 7) == "merged"


def test_get_pr_state_closed_not_merged_and_open():
    http = FakeHttp()
    http.add("GET", f"{REPO_URL}/pulls/7",
             FakeResponse(json_data={"number": 7, "state": "closed", "merged_at": None}))
    assert get_pr_state(_client(http), FULL, 7) == "closed"
    http.add("GET", f"{REPO_URL}/pulls/8",
             FakeResponse(json_data={"number": 8, "state": "open", "merged_at": None}))
    assert get_pr_state(_client(http), FULL, 8) == "open"


def test_get_pr_state_404_and_error_degrade_to_unknown():
    """404/网络错误 → unknown（不抛）：状态查询失败时仍允许走 PUT 尝试，不阻断合并。"""
    http = FakeHttp()
    http.add("GET", f"{REPO_URL}/pulls/9", FakeResponse(status=404, text="not found"))
    assert get_pr_state(_client(http), FULL, 9) == "unknown"
    broken = FakeHttp()
    # 403 非限流 → 立即 GitHubError（无重试），get_pr_state 降级 unknown
    broken.add("GET", f"{REPO_URL}/pulls/10",
               FakeResponse(status=403, text="forbidden"))
    assert get_pr_state(_client(broken), FULL, 10) == "unknown"


# ---- WP7 双文件推送（单 tree 多 blob）----


def test_push_results_dual_files_single_tree():
    """WP7：content_en 存在 → 一个 commit 里同时落 README.md 与 README.en.md 两个 blob。"""
    http = FakeHttp()
    _open_pr_check(http, [])
    http.add("GET", f"{REPO_URL}/git/ref/heads/readme-auto/update",
             FakeResponse(status=404, text="not found"))
    http.add("POST", f"{REPO_URL}/git/refs",
             FakeResponse(status=201, json_data={"ref": "refs/heads/readme-auto/update"}))
    http.add("POST", f"{REPO_URL}/git/blobs",
             FakeResponse(status=201, json_data={"sha": "blob-zh"}),
             FakeResponse(status=201, json_data={"sha": "blob-en"}))
    http.add("POST", f"{REPO_URL}/git/trees", FakeResponse(status=201, json_data={"sha": "tree1"}))
    http.add("POST", f"{REPO_URL}/git/commits", FakeResponse(status=201, json_data={"sha": "commit1"}))
    http.add("POST", f"{REPO_URL}/pulls",
             FakeResponse(status=201, json_data={"number": 50, "html_url": "https://x/50"}))
    entries: dict[str, dict] = {}
    res = push_results(
        _client(http),
        [_result(content="# 中文托管区\n", content_en="# EN managed block\n")],
        entries=entries,
    )
    assert [r.number for r in res] == [50]
    blobs = [r for r in http.writes() if r["url"].endswith("/git/blobs")]
    assert [b["json"]["content"] for b in blobs] == ["# 中文托管区\n", "# EN managed block\n"]
    tree = next(r for r in http.writes() if r["url"].endswith("/git/trees"))
    assert tree["json"]["tree"] == [
        {"path": "README.md", "mode": "100644", "type": "blob", "sha": "blob-zh"},
        {"path": "README.en.md", "mode": "100644", "type": "blob", "sha": "blob-en"},
    ]
    # 单 commit 单 ref：两文件同船，不各开一条 PR
    commits = [r for r in http.writes() if r["url"].endswith("/git/commits")]
    assert len(commits) == 1
    # 快照回写双语哈希（en 漂移检测信号）
    assert entries[FULL]["block_hash"].startswith("sha256:")
    assert entries[FULL]["block_hash_en"].startswith("sha256:")


def test_push_results_no_en_single_blob_unchanged():
    """护栏：content_en 为空 → 单 blob，与旧版载荷字节一致。"""
    http = FakeHttp()
    _open_pr_check(http, [])
    _happy_gitdata(http)
    http.add("POST", f"{REPO_URL}/pulls",
             FakeResponse(status=201, json_data={"number": 51, "html_url": "https://x/51"}))
    entries: dict[str, dict] = {}
    push_results(_client(http), [_result()], entries=entries)
    blobs = [r for r in http.writes() if r["url"].endswith("/git/blobs")]
    assert len(blobs) == 1
    assert "block_hash_en" not in entries[FULL]


def test_build_pr_body_bilingual_mentions_two_files():
    """PR body 在双语推送时说明两个文件同 PR 审核（交互文案可懂）。"""
    body = build_pr_body(
        commits=[{"sha": "def4567890"}], base_sha="base1234567", head_sha="def4567890",
        bilingual=True,
    )
    assert "README.md" in body and "README.en.md" in body
    body_zh = build_pr_body(
        commits=[{"sha": "def4567890"}], base_sha="base1234567", head_sha="def4567890",
    )
    assert "README.en.md" not in body_zh  # 单语 body 不提 en


def test_takeover_pr_body_warns_force_refresh_overwrites_branch():
    """首次接管 PR 正文提醒：分支上手动解过冲突后，别在合并前用「强制重写主页」。"""
    body = takeover_pr_body(
        username="alice", snapshot_path="data/snapshot.json", bilingual=True
    )
    assert "强制重写主页" in body and "覆盖" in body and "手动" in body
    assert "main 不会改动" in body  # 关闭 PR 的后果仍在
