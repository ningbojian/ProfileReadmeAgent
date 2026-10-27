"""编排测试（测试计划 OR01-OR20、SB22/SB23、IW09-16/25/27-29 的 cli 级部分）。

Harness：FakeAClient（A 侧 user/repos/readme 本地可控）+ FakeHttp（B/清单侧全 HTTP 观测）。
A 的 README 输入来源通过「把 readme 内容编码进短文案」断言——Overview 里找特征串即为 A 的输入。
"""

import base64
import datetime as dt
import json
from pathlib import Path

import pytest
from fake_github import FakeHttp, FakeResponse

import profile_agent.cli as cli_mod
from profile_agent.cli import run
from profile_agent.github_client import GitHubClient
from profile_agent.repo_readmes import QueueItem, build_diff_text
from profile_agent.repo_readmes_ai import MISSING_KEY_REASON, compute_input_hash

FIXTURES = Path(__file__).parent / "fixtures"
TEMPLATE = Path(__file__).parent.parent / "templates" / "profile.md.j2"

B = "https://api.github.com"
FN = "alice/cool-tool"
FN2 = "alice/old-thing"
SELF = "alice/alice"
HEAD = "HHH111111"
BASE = "BBB000000"
BRANCH = "readme-auto/update"
OLD_README = (
    "手写前言\n"
    "<!-- profile-agent:readme:start -->\n旧托管内容\n<!-- profile-agent:readme:end -->\n"
)
COMMITS = [{"sha": "CCC33333", "commit": {"message": "feat: 新增 add 函数"}}]
FILES = [{"filename": "src/core.py", "patch": "@@ -1 +1 @@\n+def add(a, b)"}]
PARTICIPANTS = ["alice/archived-one", "alice/cool-tool", "alice/old-thing"]


class _FrozenDT:
    FROZEN = dt.datetime(2026, 10, 22, 10, 0, tzinfo=dt.timezone(dt.timedelta(hours=8)))

    @classmethod
    def now(cls, tz=None):
        if tz is not None:
            return cls.FROZEN.astimezone(tz)
        return cls.FROZEN


class FakeClock:
    """注入时钟：now 返回秒（monotonic），sleep 前进并记录。"""

    def __init__(self):
        self.t = 0.0
        self.sleeps: list[int] = []

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: int) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


class FakeAClient:
    """A 侧假客户端：观测 A 的 README 输入来源（readme_calls）。"""

    def __init__(self, repos, readmes=None):
        self._repos = repos
        self.readmes = dict(readmes or {})
        self.readme_calls: list[tuple] = []

    def get_user(self, username):
        return {"login": username, "name": "Alice", "blog": ""}

    def list_repos(self, username):
        return [dict(r) for r in self._repos]

    def get_pinned_repos(self, username):
        """A-WP3 有意扩展：原生 Pinned 读取，返回 None = 读取失败 → cli 走回退阶梯。"""
        return None

    def get_repo_readme(self, full_name, ref=None):
        self.readme_calls.append((full_name, ref))
        return self.readmes.get((full_name, ref) if ref else full_name, "")


class FakeFetchers:
    def __init__(self, heads, compares=None):
        self.heads = dict(heads)
        self.compares = dict(compares or {})
        self.head_calls: list[str] = []
        self.compare_calls: list[tuple] = []

    def fetch_head(self, full_name: str) -> str:
        self.head_calls.append(full_name)
        return self.heads[full_name]

    def fetch_compare(self, full_name: str, base: str, head: str):
        self.compare_calls.append((full_name, base, head))
        if full_name in self.compares:
            return self.compares[full_name]
        raise AssertionError(f"零成本轮不应 compare：{(full_name, base, head)}")


def _repo(fn, *, desc="", stars=100, pushed="2026-10-01T08:00:00Z", fork=False, archived=False):
    return {
        "name": fn.split("/", 1)[1],
        "full_name": fn,
        "description": desc,
        "language": "Python",
        "stargazers_count": stars,
        "forks_count": 1,
        "private": False,
        "fork": fork,
        "archived": archived,
        "pushed_at": pushed,
        "html_url": f"https://github.com/{fn}",
        "homepage": "",
    }


def _entry(*, sha=BASE, last_input_hash="", failed_hash="", block_hash="", pending=False):
    return {
        "sha": sha,
        "block_hash": block_hash,
        "mode": "block",
        "failed_hash": failed_hash,
        "last_input_hash": last_input_hash,
        "pending": pending,
        "updated_at": "2026-10-01T00:00:00+00:00",
    }


def _write_cfg(tmp_path: Path, *, rr_enabled: bool = True) -> Path:
    p = lambda x: x.as_posix()  # noqa: E731
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        f"""
github:
  username: "alice"
repos:
  exclude: ["alice/old-profile"]
readme:
  path: "{p(tmp_path / 'README.md')}"
  template: "{p(TEMPLATE)}"
repo_readmes:
  enabled: {"true" if rr_enabled else "false"}
snapshot:
  path: "{p(tmp_path / 'data' / 'snapshot.json')}"
""",
        encoding="utf-8",
    )
    return cfg


def _seed_snapshot(tmp_path, entries):
    snap = tmp_path / "data" / "snapshot.json"
    snap.parent.mkdir(parents=True, exist_ok=True)
    snap.write_text(
        json.dumps({"schema": 3, "username": "alice", "repo_readmes": entries}, ensure_ascii=False),
        encoding="utf-8",
    )


def _prep(monkeypatch, tmp_path, *, ai_key=True):
    """环境：Summary 落盘、冻结时间、token 缺省清理、AI 假件。"""
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    if ai_key:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    else:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("WORKFLOW_PAT", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(cli_mod, "datetime", _FrozenDT)
    monkeypatch.setattr(cli_mod, "generate_bio", lambda *a, **k: ("简介文本", "", False))
    # 短文案完整携带 README 输入——Overview 里直接找特征串即为 A 的输入断言
    monkeypatch.setattr(
        cli_mod,
        "generate_short_intro",
        lambda r, cfg_ai, readme=None, **k: (f"短文案[{readme or ''}]", ""),
    )
    return summary


def _patch_a(monkeypatch, fake_a):
    monkeypatch.setattr(cli_mod, "GitHubClient", lambda token=None, *a, **k: fake_a)


def _ai(counter, text="新托管内容X"):
    def gen(system, prompt):
        counter["n"] += 1
        counter.setdefault("prompts", []).append((system, prompt))
        return text

    return gen


# ---- HTTP 路由小工具 ----


def _readme_resp(text: str) -> FakeResponse:
    return FakeResponse(
        json_data={
            "encoding": "base64",
            "content": base64.b64encode(text.encode("utf-8")).decode(),
        }
    )


def _add_head(http, fn, head=HEAD):
    http.add("GET", f"{B}/repos/{fn}/commits/HEAD", FakeResponse(json_data={"sha": head}))


def _add_compare(http, fn, base=BASE, head=HEAD, *, commits=None, files=None):
    http.add(
        "GET",
        f"{B}/repos/{fn}/compare/{base}...{head}",
        FakeResponse(json_data={"commits": commits or COMMITS, "files": files or FILES}),
    )


def _merged_detect_url(fn):
    owner = fn.split("/")[0]
    return (
        f"{B}/repos/{fn}/pulls?state=all&head={owner}:{BRANCH}"
        "&base=main&sort=updated&direction=desc"
    )


def _add_merge_detect(http, fn, *, prs=None):
    http.add("GET", _merged_detect_url(fn), FakeResponse(json_data=prs or []))


def _open_pr_url(fn, base="main"):
    owner = fn.split("/")[0]
    return f"{B}/repos/{fn}/pulls?state=open&head={owner}:{BRANCH}&base={base}"


def _pr(fn, *, number=7, merged=False, state="open", head_sha=HEAD):
    return {
        "number": number,
        "html_url": f"https://github.com/{fn}/pull/{number}",
        "state": state,
        "merged": merged,
        "head": {"sha": head_sha},
    }


def _add_push_flow(http, fn, *, number=7, head_sha=HEAD):
    """开 PR 全流程：open 检查（空）→ blob/tree/commit → ref 404 新建 → POST pulls。"""
    http.add("GET", _open_pr_url(fn), FakeResponse(json_data=[]))
    http.add("POST", f"{B}/repos/{fn}/git/blobs", FakeResponse(status=201, json_data={"sha": "blob1"}))
    http.add("POST", f"{B}/repos/{fn}/git/trees", FakeResponse(status=201, json_data={"sha": "tree1"}))
    http.add(
        "POST", f"{B}/repos/{fn}/git/commits", FakeResponse(status=201, json_data={"sha": head_sha})
    )
    http.add("GET", f"{B}/repos/{fn}/git/ref/heads/{BRANCH}", FakeResponse(status=404))
    http.add("POST", f"{B}/repos/{fn}/git/refs", FakeResponse(status=201, json_data={}))
    http.add("POST", f"{B}/repos/{fn}/pulls", FakeResponse(status=201, json_data=_pr(fn, number=number)))


def _add_scan(http, fn, *, prs):
    http.add("GET", _open_pr_url(fn), FakeResponse(json_data=prs))
    for pr in prs:  # W3：list 后逐 PR 单 GET 明细（同 URL 先于轮询响应入队，FIFO 头）
        http.add(
            "GET",
            f"{B}/repos/{fn}/pulls/{pr['number']}",
            FakeResponse(
                json_data={
                    "number": pr["number"],
                    "additions": 34,
                    "deletions": 11,
                    "changed_files": 2,
                    "commits": 3,
                    "mergeable_state": "clean",
                    "body": "三结局警示\n\n涉及提交：\n"
                    "- `abcdef12` 修复拼写错误\n"
                    "- `01234567` 更新项目列表\n",
                }
            ),
        )


def _add_issues(http, *, existing=None, number=5, search_count=1, found_later=False):
    """清单 issue 搜索序列 + （无既有时）POST 创建 + PATCH（粘滞）。

    existing：全程搜索都命中该 issue；found_later：首次搜索为空（触发创建）、后续搜索
    命中刚建的 issue（真实 GitHub 行为——创建后的二次 _find 能找到）；否则全程空。
    """
    title = "🔔 README 更新待合并清单（待合并 PR）"
    if existing is not None:
        issue = {**existing, "title": existing.get("title", title)}
        bodies = [ [issue] ] * search_count
    elif found_later:
        issue = {
            "number": number,
            "title": title,
            "html_url": f"https://github.com/{SELF}/issues/{number}",
        }
        bodies = [[]] + [[issue]] * (search_count - 1)
    else:
        bodies = [[] for _ in range(search_count)]
    http.add(
        "GET",
        f"{B}/repos/{SELF}/issues?state=open&per_page=100",
        *[FakeResponse(json_data=b) for b in bodies],
    )
    if bodies[0] == []:
        http.add(
            "POST",
            f"{B}/repos/{SELF}/issues",
            FakeResponse(
                status=201,
                json_data={"number": number, "html_url": f"https://github.com/{SELF}/issues/{number}"},
            ),
        )
    http.add(
        "PATCH",
        f"{B}/repos/{SELF}/issues/{number}",
        FakeResponse(json_data={"number": number}),
        sticky=True,
    )


def _full_b_routes(http, fn=FN, *, number=7, head=HEAD, base=BASE, scan_prs="default"):
    """一轮完整 B：合并检测（无）→ head → compare → readme(mode) → 开 PR → 扫描。"""
    _add_merge_detect(http, fn)
    _add_head(http, fn, head)
    _add_compare(http, fn, base, head)
    http.add("GET", f"{B}/repos/{fn}/readme", _readme_resp(OLD_README))
    _add_push_flow(http, fn, number=number, head_sha=head)
    _add_scan(http, fn, prs=[_pr(fn, number=number)] if scan_prs == "default" else (scan_prs or []))


def _run_b(tmp_path, monkeypatch, fake_a, http, **kw):
    _patch_a(monkeypatch, fake_a)
    return run(
        _write_cfg(tmp_path, rr_enabled=kw.pop("rr_enabled", True)),
        b_client=GitHubClient("p-pat", session=http),
        issue_client=GitHubClient("g-tok", session=http),
        **kw,
    )


# ============================ OR07（M1 保留） ============================


def test_or07_b_no_change_output_bytes_identical_to_pure_a(tmp_path, monkeypatch):
    """OR07：启用 B 但队列空 → README 与纯 A 运行逐字节一致；零 AI、fetcher 只查 head。"""
    _prep(monkeypatch, tmp_path)

    dir_a = tmp_path / "a"
    dir_a.mkdir()
    assert run(_write_cfg(dir_a, rr_enabled=False), fixture=FIXTURES, no_ai=True) == 0
    pure_a = (dir_a / "README.md").read_bytes()

    dir_b = tmp_path / "b"
    dir_b.mkdir()
    fetchers = FakeFetchers({fn: HEAD for fn in PARTICIPANTS})
    assert run(_write_cfg(dir_b), fixture=FIXTURES, no_ai=True, readme_fetchers=fetchers) == 0

    assert (dir_b / "README.md").read_bytes() == pure_a  # 字节一致（冻结时间消除页脚时间差）
    assert fetchers.head_calls == PARTICIPANTS  # B 只发了 head 查询
    assert fetchers.compare_calls == []  # 首见记基线，零 compare、零 AI、零推送


# ============================ OR01-OR06 ============================


def test_or01_merged_pr_reads_api_default_branch(tmp_path, monkeypatch):
    """OR01：PR 已合并 → A 从 API 读默认分支真源；对账行标 API 真源。"""
    summary = _prep(monkeypatch, tmp_path)
    fake_a = FakeAClient([_repo(FN, desc="")], readmes={FN: "不该用的旧文Z"})
    http = FakeHttp()
    _add_merge_detect(http, FN, prs=[_pr(FN, number=7, merged=True, state="closed")])
    _add_head(http, FN, HEAD)  # 合并检测把基线推进到 HEAD → no_change
    http.add("GET", f"{B}/repos/{FN}/readme", _readme_resp("新版N内容"))  # §5.6 规则 1 真源
    _add_scan(http, FN, prs=[])
    _add_issues(http)
    _seed_snapshot(tmp_path, {FN: _entry(sha=BASE)})

    counter = {"n": 0}
    assert _run_b(tmp_path, monkeypatch, fake_a, http, readme_generate=_ai(counter)) == 0

    overview = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "新版N内容" in overview  # ① A 输入==API 默认分支真源
    assert fake_a.readme_calls == []  # ② A 未自行拉取（用的是 B 预填）
    assert "API 真源 1" in summary.read_text(encoding="utf-8")  # ③ 对账行
    assert counter["n"] == 0


def test_or02_open_pr_uses_memory_content_push_same_bytes(tmp_path, monkeypatch):
    """OR02/OR18/OR19：open PR 仓 A 用本轮内存内容；推送载荷==A 输入；主页不走 PR。"""
    summary = _prep(monkeypatch, tmp_path)
    fake_a = FakeAClient([_repo(FN, desc="")])
    http = FakeHttp()
    _full_b_routes(http)
    _add_issues(http)
    _seed_snapshot(tmp_path, {FN: _entry(sha=BASE)})

    counter = {"n": 0}
    x = "未合并的新内容X"
    assert _run_b(tmp_path, monkeypatch, fake_a, http, readme_generate=_ai(counter, x)) == 0

    overview = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert x in overview  # ① A 输入==假 AI 返回值（内存）
    assert "旧托管内容" not in overview  # ② 默认分支旧文未进 Overview
    assert "内存 1" in summary.read_text(encoding="utf-8")  # ③ 对账行标内存
    blobs = [r for r in http.writes() if r["url"].endswith("/git/blobs")]
    assert x in blobs[0]["json"]["content"]  # OR19：blob 载荷==A 吸收的输入
    assert not http.has("POST", f"{B}/repos/{SELF}/pulls")  # OR18：主页直推不开 PR
    assert counter["n"] == 1


def test_or03_silent_round_open_pr_reads_branch_content(tmp_path, monkeypatch):
    """OR03：跨轮哈希命中静默 + PR 仍 open → A 读分支内容，不回退默认分支旧文。"""
    _prep(monkeypatch, tmp_path)
    fake_a = FakeAClient([_repo(FN, desc="")])
    ai_counter = {"n": 0}

    # 第一轮：完整 B 开 PR #7
    http1 = FakeHttp()
    _full_b_routes(http1)
    _add_issues(http1)
    _seed_snapshot(tmp_path, {FN: _entry(sha=BASE)})
    assert _run_b(tmp_path, monkeypatch, fake_a, http1, readme_generate=_ai(ai_counter)) == 0
    assert ai_counter["n"] == 1

    # 第二轮：同输入 → 缓存命中；PR open → A 读分支内容 Y（Summary 跨轮累积，OR10 每轮一条）
    http2 = FakeHttp()
    _add_merge_detect(http2, FN, prs=[_pr(FN, number=7)])
    _add_head(http2, FN, HEAD)
    _add_compare(http2, FN, BASE, HEAD)
    http2.add("GET", f"{B}/repos/{FN}/readme?ref={BRANCH}", _readme_resp("分支内容Y"))
    _add_scan(http2, FN, prs=[_pr(FN, number=7)])
    _add_issues(http2, existing={"number": 5, "html_url": f"https://github.com/{SELF}/issues/5"})

    assert _run_b(tmp_path, monkeypatch, fake_a, http2, readme_generate=_ai(ai_counter)) == 0
    assert ai_counter["n"] == 1  # 第二轮零 AI

    overview = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "分支内容Y" in overview  # ① A 输入==分支内容
    assert "默认分支旧文Z" not in overview  # ② 未回退默认分支（其路由未注册=未请求）
    assert fake_a.readme_calls == []
    text = (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert "分支 1" in text  # ③ 对账行标分支
    assert text.count("本轮 A 的 README 输入来源") == 2  # OR10：每轮都有


def test_or04_pr_closed_during_wait_falls_back_to_old_readme(tmp_path, monkeypatch):
    """OR04：等待期间 PR 被关闭 → A 回退旧文；issue body 不再含该 PR 链接；二轮同哈希抑制。"""
    _prep(monkeypatch, tmp_path)
    fake_a = FakeAClient([_repo(FN, desc="")])
    http = FakeHttp()
    _full_b_routes(http)
    _add_issues(http, search_count=2, found_later=True)  # 清单步创建 + 关闭步命中
    http.add(
        "GET", f"{B}/repos/{FN}/pulls/7",
        FakeResponse(json_data=_pr(FN, number=7, state="closed")),  # 轮询
        FakeResponse(json_data=_pr(FN, number=7, state="closed")),  # 结清复查
    )
    http.add("GET", f"{B}/repos/{FN}/readme", _readme_resp(OLD_README))  # 等待后旧文复查
    _seed_snapshot(tmp_path, {FN: _entry(sha=BASE)})

    clock = FakeClock()
    counter = {"n": 0}
    assert (
        _run_b(
            tmp_path, monkeypatch, fake_a, http,
            readme_generate=_ai(counter), merge_wait_minutes=30,
            now_fn=clock.now, sleep_fn=clock.sleep,
        )
        == 0
    )

    overview = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "旧托管内容" in overview  # ① A 输入==旧文 W（等待后复查的默认分支内容）
    patches = [r for r in http.writes() if r["method"] == "PATCH" and "/issues/" in r["url"]]
    assert patches, "应有清单 PATCH"
    assert all("/pull/7" not in (r["json"].get("body") or "") for r in patches)  # ② 不再含 PR 链接
    assert any(r["json"].get("state") == "closed" for r in patches)

    # 第二轮：同输入 → 缓存命中（无新 AI/推送）
    http2 = FakeHttp()
    _add_merge_detect(http2, FN, prs=[_pr(FN, number=7, state="closed", merged=False)])
    _add_head(http2, FN, HEAD)
    _add_compare(http2, FN, BASE, HEAD)
    _add_scan(http2, FN, prs=[])
    _add_issues(http2)
    n_before = counter["n"]
    assert _run_b(tmp_path, monkeypatch, fake_a, http2, readme_generate=_ai(counter)) == 0
    assert counter["n"] == n_before  # ③ 无新 AI
    assert not http2.has("POST", f"{B}/repos/{FN}/pulls")  # ③ 无新 pulls POST


@pytest.mark.parametrize("reason", ["first_seen", "no_change", "suppressed"])
def test_or05_no_output_no_open_pr_uses_old_readme(tmp_path, monkeypatch, reason):
    """OR05：B 未产出且无 open PR（三种原因）→ A 用旧 README，对账行标旧文。"""
    summary = _prep(monkeypatch, tmp_path)
    fake_a = FakeAClient([_repo(FN, desc="")], readmes={FN: "旧文W"})
    http = FakeHttp()
    _add_merge_detect(http, FN)
    _add_head(http, FN, HEAD)
    if reason == "first_seen":
        _seed_snapshot(tmp_path, {})
    elif reason == "no_change":
        _seed_snapshot(tmp_path, {FN: _entry(sha=HEAD)})
    else:  # suppressed：failed_hash 与本批输入哈希一致
        item = QueueItem(FN, BASE, HEAD, commits=COMMITS, files=FILES)
        h = compute_input_hash(item, 1, diff_text=build_diff_text(COMMITS, FILES, 6000))
        _seed_snapshot(tmp_path, {FN: _entry(sha=BASE, failed_hash=h)})
        _add_compare(http, FN, BASE, HEAD)
    _add_scan(http, FN, prs=[])
    _add_issues(http)

    counter = {"n": 0}
    assert _run_b(tmp_path, monkeypatch, fake_a, http, readme_generate=_ai(counter)) == 0

    overview = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "旧文W" in overview  # ① A 输入==旧文（A 侧默认拉取）
    assert fake_a.readme_calls == [(FN, None)]
    assert "旧文 1" in summary.read_text(encoding="utf-8")  # ② 对账行标旧文
    assert counter["n"] == 0  # 抑制在 AI 调用之前，零 AI
    assert not http.has("POST", f"{B}/repos/{FN}/pulls")


def test_or06_b_exception_isolated_second_repo_still_pushes(tmp_path, monkeypatch):
    """OR06：一仓推送异常被隔离——run 返回 0、A 照常、Summary 有失败行、第二仓仍开 PR。"""
    summary = _prep(monkeypatch, tmp_path)
    fake_a = FakeAClient([_repo(FN, desc="有描述"), _repo(FN2, desc="有描述")])
    http = FakeHttp()
    for fn in (FN, FN2):
        _add_merge_detect(http, fn)
        _add_head(http, fn, HEAD)
        _add_compare(http, fn, BASE, HEAD)
        http.add("GET", f"{B}/repos/{fn}/readme", _readme_resp(OLD_README))
    # 仓 1：blob 422 → GitHubError（推送失败）
    http.add("GET", _open_pr_url(FN), FakeResponse(json_data=[]))
    http.add("POST", f"{B}/repos/{FN}/git/blobs", FakeResponse(status=422, text="Unprocessable"))
    # 仓 2：完整推送成功
    _add_push_flow(http, FN2, number=8)
    _add_scan(http, FN, prs=[])
    _add_scan(http, FN2, prs=[_pr(FN2, number=8)])
    _add_issues(http)
    _seed_snapshot(tmp_path, {FN: _entry(sha=BASE), FN2: _entry(sha=BASE)})

    counter = {"n": 0}
    assert _run_b(tmp_path, monkeypatch, fake_a, http, readme_generate=_ai(counter)) == 0

    assert (tmp_path / "README.md").exists()  # ② A 照常完成
    text = summary.read_text(encoding="utf-8")
    assert FN in text and "失败" in text  # ③ Summary 含失败仓库与原因
    assert http.has("POST", f"{B}/repos/{FN2}/pulls")  # ④ 第二仓仍开出 PR
    assert not http.has("POST", f"{B}/repos/{FN}/pulls")
    assert counter["n"] == 2  # 两仓都进了 AI（失败发生在推送层）


# ============================ OR08 / IW25 / IW27 ============================


def test_or08_end_to_end_b_checklist_wait_a(tmp_path, monkeypatch):
    """OR08：端到端 B→清单→等待→A；URL 序列、issue body、Summary 进度与对账行。"""
    summary = _prep(monkeypatch, tmp_path)
    fake_a = FakeAClient([_repo(FN, desc=""), _repo(FN2, desc="有描述")])
    http = FakeHttp()
    _full_b_routes(http, FN, number=7)
    _full_b_routes(http, FN2, number=8)
    _add_issues(http, search_count=2, found_later=True)  # 清单步创建 + 全结清关闭步命中
    http.add(
        "GET", f"{B}/repos/{FN}/pulls/7",
        FakeResponse(json_data=_pr(FN, number=7, merged=True, state="closed")),
        FakeResponse(json_data=_pr(FN, number=7, merged=True, state="closed")),  # 结清复查
    )
    http.add(
        "GET", f"{B}/repos/{FN2}/pulls/8",
        FakeResponse(json_data=_pr(FN2, number=8, merged=True, state="closed")),
    )
    http.add("GET", f"{B}/repos/{FN}/readme", _readme_resp("合并后真源"))  # §5.6 规则 1 预填
    _seed_snapshot(tmp_path, {FN: _entry(sha=BASE), FN2: _entry(sha=BASE)})

    clock = FakeClock()
    counter = {"n": 0}
    assert (
        _run_b(
            tmp_path, monkeypatch, fake_a, http,
            readme_generate=_ai(counter), merge_wait_minutes=30,
            now_fn=clock.now, sleep_fn=clock.sleep,
        )
        == 0
    )

    # ① URL 前缀序列：compare → git-data → POST pulls → issues → 轮询
    seq = [(r["method"], r["url"]) for r in http.requests]
    idx = lambda pred: next(i for i, (m, u) in enumerate(seq) if pred(m, u))  # noqa: E731
    i_compare = idx(lambda m, u: m == "GET" and "/compare/" in u)
    i_blob = idx(lambda m, u: m == "POST" and u.endswith("/git/blobs"))
    i_pulls = idx(lambda m, u: m == "POST" and u.endswith("/pulls"))
    i_issues = idx(lambda m, u: m == "POST" and u.endswith("/issues"))
    # 等待轮询（首个清单 POST 之后的 /pulls/7 GET；之前那次是扫描期 W3 明细单 GET）
    i_poll = next(
        i for i, (m, u) in enumerate(seq) if i > i_issues and m == "GET" and "/pulls/7" in u
    )
    assert i_compare < i_blob < i_pulls < i_issues < i_poll

    # ② issue POST body：三结局语义、逐条链接+预览、时间戳、@assign
    issue_post = next(
        r for r in http.writes() if r["method"] == "POST" and r["url"].endswith("/issues")
    )
    body = issue_post["json"]["body"]
    assert "三种处理方式" in body and "/pull/7" in body and "/pull/8" in body
    assert "新版 README 预览" in body and "更新于" in body and "北京时间" in body
    assert issue_post["json"]["assignees"] == ["alice"]
    assert "@alice" in body

    # ③ Summary 逐仓进度行 + 对账行；④ 退出码 0（前置断言）
    text = summary.read_text(encoding="utf-8")
    assert text.count("- `alice/") >= 2
    assert "本轮 A 的 README 输入来源" in text
    # FN 短介绍仓：等待中合并 → 规则 1 覆盖规则 2 → API 真源（合并后默认分支内容）
    assert "API 真源 1" in text
    assert counter["n"] == 2


def test_iw27_issue_uses_g_tok_git_data_uses_p_pat(tmp_path, monkeypatch):
    """IW27：清单 issue 请求用 GITHUB_TOKEN（g-tok），git-data/pulls 用 WORKFLOW_PAT（p-pat）。"""
    _prep(monkeypatch, tmp_path)
    fake_a = FakeAClient([_repo(FN, desc="有描述")])
    http = FakeHttp()
    _full_b_routes(http)
    _add_issues(http)
    _seed_snapshot(tmp_path, {FN: _entry(sha=BASE)})
    assert _run_b(tmp_path, monkeypatch, fake_a, http, readme_generate=_ai({"n": 0})) == 0

    issue_reqs = [r for r in http.requests if "/alice/alice/issues" in r["url"]]
    git_reqs = [r for r in http.requests if "/alice/alice/issues" not in r["url"]]
    assert issue_reqs and all(r["headers"].get("Authorization") == "Bearer g-tok" for r in issue_reqs)
    assert git_reqs and all(r["headers"].get("Authorization") == "Bearer p-pat" for r in git_reqs)


def test_iw27_ticked_merge_put_uses_p_pat(tmp_path, monkeypatch):
    """IW27 勾选态：合并 PUT 同样走 p-pat——权限分离在代为合并路径上成立
    （清单读写 g-tok、合并 PAT；IW27 的合并侧补齐）。"""
    _prep(monkeypatch, tmp_path)
    fake_a = FakeAClient([_repo(FN, desc="有描述")])
    http = FakeHttp()
    _full_b_routes(http)
    ticked = (
        "## 🔔 README 更新待合并清单（更新于 2026-10-23 10:00，北京时间）\n"
        "@alice\n"
        "- [x] cool-tool：[PR](https://github.com/alice/cool-tool/pull/7)"
        "（[新版 README 预览](https://github.com/alice/cool-tool/blob/readme-auto/update/README.md)）\n"
    )
    _add_issues(
        http,
        existing={
            "number": 5,
            "html_url": f"https://github.com/{SELF}/issues/5",
            "body": ticked,
        },
    )
    http.add(  # 钩子①' 按号复读（attempted 挡下重复 PUT）
        "GET", f"{B}/repos/{SELF}/issues/5",
        FakeResponse(json_data={"number": 5, "html_url": f"https://github.com/{SELF}/issues/5",
                                "body": ticked}),
        sticky=True,
    )
    http.add(  # PUT 前的状态查询（WP2 降噪）：仍 open → 照常 PUT
        "GET", f"{B}/repos/{FN}/pulls/7",
        FakeResponse(json_data=_pr(FN, number=7)),
        sticky=True,
    )
    http.add(
        "PUT", f"{B}/repos/{FN}/pulls/7/merge",
        FakeResponse(json_data={"merged": True, "sha": "f" * 40, "message": "merged"}),
    )
    _seed_snapshot(tmp_path, {FN: _entry(sha=BASE)})
    assert _run_b(tmp_path, monkeypatch, fake_a, http, readme_generate=_ai({"n": 0})) == 0

    merge_reqs = [r for r in http.writes() if r["method"] == "PUT" and r["url"].endswith("/merge")]
    assert merge_reqs and all(
        r["headers"].get("Authorization") == "Bearer p-pat" for r in merge_reqs
    )  # ① 合并走 PAT
    issue_reqs = [r for r in http.requests if "/alice/alice/issues" in r["url"]]
    assert issue_reqs and all(
        r["headers"].get("Authorization") == "Bearer g-tok" for r in issue_reqs
    )  # ② 清单读写走 GITHUB_TOKEN
    assert len(merge_reqs) == 1  # ③ attempted：开局合并后不再重复 PUT


def test_iw25_summary_has_checklist_snapshot_and_issue_link(tmp_path, monkeypatch):
    """IW25：Summary 写清单快照（PR 链接）+ issue 链接 + 「模式 B 状态」行。"""
    summary = _prep(monkeypatch, tmp_path)
    fake_a = FakeAClient([_repo(FN, desc="有描述")])
    http = FakeHttp()
    _full_b_routes(http, number=7)
    _add_issues(http)
    _seed_snapshot(tmp_path, {FN: _entry(sha=BASE)})
    assert _run_b(tmp_path, monkeypatch, fake_a, http, readme_generate=_ai({"n": 0})) == 0

    text = summary.read_text(encoding="utf-8")
    assert f"https://github.com/{SELF}/issues/5" in text  # ① issue 链接
    assert f"https://github.com/{FN}/pull/7" in text  # ② PR 链接快照
    assert "模式 B 状态" in text  # ③ 状态行


# ============================ SB22 / SB23 / OR09 ============================


def test_sb22_sb23_or09_snapshot_survives_cancel_retrigger_no_reprocess(tmp_path, monkeypatch):
    """SB22/SB23/OR09：B 完成即落盘——等待中取消后磁盘条目完整；重触发零 AI 零推送、PR 不漂移。"""
    _prep(monkeypatch, tmp_path)
    fake_a = FakeAClient([_repo(FN, desc="有描述")])
    http1 = FakeHttp()
    _full_b_routes(http1, number=7)
    _add_issues(http1)
    http1.add("GET", f"{B}/repos/{FN}/pulls/7", FakeResponse(json_data=_pr(FN, number=7)))
    _seed_snapshot(tmp_path, {FN: _entry(sha=BASE)})

    observed: dict = {}

    def cancel_sleep(seconds: int) -> None:
        # SB23：等待步开始时文件已可读且含全字段；取消不影响该文件
        observed["snap"] = json.loads(
            (tmp_path / "data" / "snapshot.json").read_text(encoding="utf-8")
        )
        raise KeyboardInterrupt

    counter = {"n": 0}
    with pytest.raises(KeyboardInterrupt):
        _run_b(
            tmp_path, monkeypatch, fake_a, http1,
            readme_generate=_ai(counter), merge_wait_minutes=30,
            now_fn=FakeClock().now, sleep_fn=cancel_sleep,
        )

    e1 = observed["snap"]["repo_readmes"][FN]  # ① 等待前文件已含全字段
    assert e1["sha"] == BASE  # SB22④ 已审 PR 不漂移：基线不动
    assert e1["last_input_hash"].startswith("sha256:")
    assert e1["block_hash"].startswith("sha256:")
    assert http1.has("POST", f"{B}/repos/{FN}/pulls")  # PR #7 已开

    # 重触发（第二轮）：同输入 → 零 AI、零推送；清单列出遗留 PR
    http2 = FakeHttp()
    _add_merge_detect(http2, FN, prs=[_pr(FN, number=7)])
    _add_head(http2, FN, HEAD)
    _add_compare(http2, FN, BASE, HEAD)
    http2.add("GET", f"{B}/repos/{FN}/readme?ref={BRANCH}", _readme_resp("分支内容"))
    _add_scan(http2, FN, prs=[_pr(FN, number=7)])
    _add_issues(http2, existing={"number": 5, "html_url": f"https://github.com/{SELF}/issues/5"})
    assert _run_b(tmp_path, monkeypatch, fake_a, http2, readme_generate=_ai(counter)) == 0
    assert counter["n"] == 1  # ② 重触发不重调 AI（OR09①）
    assert not http2.has("POST", f"{B}/repos/{FN}/pulls")  # ③ 无推送，PR diff 不漂移
    patch = next(
        r for r in http2.writes() if r["method"] == "PATCH" and "/issues/5" in r["url"]
    )
    assert f"https://github.com/{FN}/pull/7" in patch["json"]["body"]  # OR09③ 遗留 PR 在清单
    snap2 = json.loads((tmp_path / "data" / "snapshot.json").read_text(encoding="utf-8"))
    assert snap2["repo_readmes"][FN]["last_input_hash"].startswith("sha256:")  # OR09④ 缓存未丢
    assert snap2["repo_readmes"][FN]["sha"] == BASE  # SB22④


# ============================ OR11 / IW09-15（七场景 + Summary 护栏） ============================


def test_or11_iw11_disabled_reason_line(tmp_path, monkeypatch):
    """OR11①/IW11：未启用 → Summary 状态行说明未启用；返回 0。"""
    summary = _prep(monkeypatch, tmp_path)
    assert run(_write_cfg(tmp_path, rr_enabled=False), fixture=FIXTURES, no_ai=True) == 0
    text = summary.read_text(encoding="utf-8")
    assert "模式 B 状态" in text and "未启用" in text


def test_or11_iw10_missing_pat_reason_line(tmp_path, monkeypatch, capsys):
    """OR11②/IW10：缺 WORKFLOW_PAT → Summary 可读原因、stdout 提示、返回 0。"""
    summary = _prep(monkeypatch, tmp_path)
    assert run(_write_cfg(tmp_path), fixture=FIXTURES, no_ai=True) == 0
    text = summary.read_text(encoding="utf-8")
    assert "缺 WORKFLOW_PAT" in text
    out = capsys.readouterr().out
    assert "模式 B 状态" in out  # stdout 中文信息级提示


def test_or11_iw12_missing_ai_key_pending(tmp_path, monkeypatch):
    """OR11③/IW12：缺 AI key → Summary 积压原因行、pending True、零 PR、返回 0。"""
    summary = _prep(monkeypatch, tmp_path, ai_key=False)
    fetchers = FakeFetchers(
        {fn: HEAD for fn in PARTICIPANTS},
        compares={FN: {"commits": COMMITS, "files": FILES}},
    )
    _seed_snapshot(tmp_path, {FN: _entry(sha=BASE)})
    assert run(_write_cfg(tmp_path), fixture=FIXTURES, readme_fetchers=fetchers) == 0

    text = summary.read_text(encoding="utf-8")
    assert MISSING_KEY_REASON in text
    snap = json.loads((tmp_path / "data" / "snapshot.json").read_text(encoding="utf-8"))
    assert snap["repo_readmes"][FN]["pending"] is True  # IW12①
    assert fetchers.compare_calls == [(FN, BASE, HEAD)]  # 有 compare 但零 PR


def test_or11_iw13_first_seen_reason_line(tmp_path, monkeypatch):
    """OR11④/IW13：首见只记基线 → Summary 含「首见」、无 PR、基线落盘。"""
    summary = _prep(monkeypatch, tmp_path)
    fetchers = FakeFetchers({fn: HEAD for fn in PARTICIPANTS})
    assert run(_write_cfg(tmp_path), fixture=FIXTURES, no_ai=True, readme_fetchers=fetchers) == 0

    assert "首见" in summary.read_text(encoding="utf-8")
    snap = json.loads((tmp_path / "data" / "snapshot.json").read_text(encoding="utf-8"))
    assert snap["repo_readmes"][FN]["sha"] == HEAD  # 基线落盘
    assert fetchers.compare_calls == []  # 无 PR、零 compare


def test_or11_iw14_no_new_commits_reason_line(tmp_path, monkeypatch):
    """OR11⑤/IW14：无新提交 → Summary 含「无新提交」、零 AI 零 PR、A 正常。"""
    summary = _prep(monkeypatch, tmp_path)
    fetchers = FakeFetchers({fn: HEAD for fn in PARTICIPANTS})
    _seed_snapshot(tmp_path, {fn: _entry(sha=HEAD) for fn in PARTICIPANTS})
    counter = {"n": 0}
    assert (
        run(
            _write_cfg(tmp_path), fixture=FIXTURES, readme_fetchers=fetchers,
            readme_generate=_ai(counter),
        )
        == 0
    )
    assert "无新提交" in summary.read_text(encoding="utf-8")
    assert counter["n"] == 0
    assert (tmp_path / "README.md").exists()  # A 正常


def test_or11_iw15_suppressed_self_help_line_and_no_retry(tmp_path, monkeypatch):
    """OR11⑥/IW15：AI 失败后自救说明（不会自动重试/--force）；二轮零 AI；返回 0。"""
    _prep(monkeypatch, tmp_path)
    fetchers = FakeFetchers(
        {fn: HEAD for fn in PARTICIPANTS},
        compares={FN: {"commits": COMMITS, "files": FILES}},
    )
    _seed_snapshot(tmp_path, {FN: _entry(sha=BASE)})

    counter = {"n": 0}

    def boom(system, prompt):
        counter["n"] += 1
        raise RuntimeError("AI 挂了")

    assert (
        run(_write_cfg(tmp_path), fixture=FIXTURES, readme_fetchers=fetchers, readme_generate=boom)
        == 0
    )

    # 第二轮：同输入 → 抑制，AI 计数不再增长；自救说明进 Summary（状态行原因）
    fetchers2 = FakeFetchers(
        {fn: HEAD for fn in PARTICIPANTS},
        compares={FN: {"commits": COMMITS, "files": FILES}},
    )
    assert (
        run(_write_cfg(tmp_path), fixture=FIXTURES, readme_fetchers=fetchers2, readme_generate=boom)
        == 0
    )
    assert counter["n"] == 1  # ② 第二轮 AI 计数 0（总 1）
    text = (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert "不会自动重试" in text and "--force" in text  # ① 自救说明


def test_or11_iw09_conflict_warning(tmp_path, monkeypatch):
    """OR11⑦/IW09：勾选 repo_readmes 但 enabled=false → Summary 冲突警告、无 compare、返回 0。"""
    summary = _prep(monkeypatch, tmp_path)
    fetchers = FakeFetchers({fn: HEAD for fn in PARTICIPANTS})
    assert (
        run(
            _write_cfg(tmp_path, rr_enabled=False), fixture=FIXTURES,
            readme_fetchers=fetchers, b_force=True,
        )
        == 0
    )
    text = summary.read_text(encoding="utf-8")
    assert "已勾选" in text and "enabled=false" in text and "未运行模式 B" in text
    assert fetchers.compare_calls == []  # 未运行 B


# ============================ OR12 / IW28 / IW29 / OR13 ============================


def _no_change_with_open_pr(tmp_path, monkeypatch, *, number=7, merge_wait_minutes=0.0, **extra):
    """共用前置：no_change 仓 + 既有 open PR #7（不发 AI 不推送）。"""
    _prep(monkeypatch, tmp_path)
    fake_a = FakeAClient([_repo(FN, desc="有描述")])
    http = FakeHttp()
    _add_merge_detect(http, FN, prs=[_pr(FN, number=number)])
    _add_head(http, FN, HEAD)
    _add_scan(http, FN, prs=[_pr(FN, number=number)])
    http.add("GET", f"{B}/repos/{FN}/readme?ref={BRANCH}", _readme_resp("分支内容"))
    _add_issues(http)
    _seed_snapshot(tmp_path, {FN: _entry(sha=HEAD)})
    clock = FakeClock()
    counter = {"n": 0}
    rc = _run_b(
        tmp_path, monkeypatch, fake_a, http,
        readme_generate=_ai(counter), merge_wait_minutes=merge_wait_minutes,
        now_fn=clock.now, sleep_fn=clock.sleep, **extra,
    )
    return rc, http, tmp_path / "summary.md", counter, clock


def test_or12_unchecked_wait_zero_polling_zero_hints(tmp_path, monkeypatch, capsys):
    """OR12/IW28 附：未勾选等待 → 无轮询、无窗口文案；清单照常、A 正常。"""
    rc, http, summary, counter, _ = _no_change_with_open_pr(tmp_path, monkeypatch)
    assert rc == 0
    pulls7 = [r for r in http.requests if r["method"] == "GET" and r["url"].endswith("/pulls/7")]
    assert len(pulls7) == 1  # ① 唯一一次 /pulls/7 是 W3 清单明细单 GET；未勾选等待 → 无轮询
    out = capsys.readouterr().out
    assert "窗口至" not in out  # ② 无等待提示
    assert "窗口至" not in summary.read_text(encoding="utf-8")
    assert http.has("POST", f"{B}/repos/{SELF}/issues")  # ③ 清单照常
    assert (tmp_path / "README.md").exists()  # ④ A 正常
    assert counter["n"] == 0


def test_iw28_checked_wait_with_empty_list_goes_straight_to_a(tmp_path, monkeypatch, capsys):
    """IW28：清单为空时勾选等待也直接进 A（不轮询、无等待首行）。"""
    summary = _prep(monkeypatch, tmp_path)
    fake_a = FakeAClient([_repo(FN, desc="有描述")])
    http = FakeHttp()
    _add_merge_detect(http, FN)
    _add_head(http, FN, HEAD)  # first_seen：无 open PR
    _add_scan(http, FN, prs=[])
    _add_issues(http)
    clock = FakeClock()
    assert (
        _run_b(
            tmp_path, monkeypatch, fake_a, http, no_ai=True,
            merge_wait_minutes=30, now_fn=clock.now, sleep_fn=clock.sleep,
        )
        == 0
    )
    assert clock.sleeps == []  # ① 不轮询
    out = capsys.readouterr().out
    assert "窗口至" not in out and "窗口至" not in summary.read_text(encoding="utf-8")
    assert (tmp_path / "README.md").exists()  # ② A 正常


def test_iw29_cron_path_also_runs_checklist_step(tmp_path, monkeypatch):
    """IW29：cron（merge_wait_minutes=0）同样执行清单步——issue 照常创建。"""
    rc, http, _, _, _ = _no_change_with_open_pr(tmp_path, monkeypatch, merge_wait_minutes=0.0)
    assert rc == 0
    assert http.has("POST", f"{B}/repos/{SELF}/issues")  # 清单步未被跳过


def test_or13_final_state_appended_to_same_issue(tmp_path, monkeypatch):
    """OR13：收尾时终态行追加到同一 issue；待合并条目集合==实际 open PR 集合。"""
    rc, http, _, _, _ = _no_change_with_open_pr(tmp_path, monkeypatch)
    assert rc == 0
    issue_writes = [r for r in http.writes() if "/alice/alice/issues" in r["url"]]
    posts = [r for r in issue_writes if r["method"] == "POST"]
    assert len(posts) == 1  # ④ 未新开（单份清单）
    final = issue_writes[-1]
    assert final["method"] == "PATCH" and "/issues/5" in final["url"]
    assert "本轮 Overview 已进入 main" in final["json"]["body"]  # ① 终态行
    assert f"https://github.com/{FN}/pull/7" in final["json"]["body"]  # ③ 条目==open PR
    assert "/pull/8" not in final["json"]["body"]


# ============================ OR14-OR17（等待编排） ============================


def test_or14_wait_first_line_count_window_consequence(tmp_path, monkeypatch, capsys):
    """OR14：等待首行含条数、窗口至 HH:MM、超时后果（日志 + issue tail 同源）。"""
    summary = _prep(monkeypatch, tmp_path)
    fake_a = FakeAClient([_repo(FN, desc="有描述"), _repo(FN2, desc="有描述")])
    http = FakeHttp()
    for fn, num in ((FN, 42), (FN2, 43)):
        _add_merge_detect(http, fn, prs=[_pr(fn, number=num)])
        _add_head(http, fn, HEAD)
        _add_scan(http, fn, prs=[_pr(fn, number=num)])
        merged = FakeResponse(json_data=_pr(fn, number=num, merged=True, state="closed"))
        http.add("GET", f"{B}/repos/{fn}/pulls/{num}", merged, merged)  # 轮询 + 结清复查
        http.add("GET", f"{B}/repos/{fn}/readme", _readme_resp("合并后真源"))  # API 真源预填
    _add_issues(http, search_count=2, found_later=True)
    _seed_snapshot(tmp_path, {FN: _entry(sha=HEAD), FN2: _entry(sha=HEAD)})
    clock = FakeClock()
    assert (
        _run_b(
            tmp_path, monkeypatch, fake_a, http, no_ai=True,
            merge_wait_minutes=30, now_fn=clock.now, sleep_fn=clock.sleep,
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "共 2 条待合并" in out and "窗口至 10:30" in out and "不会卡死" in out
    issue_post = next(
        r for r in http.writes() if r["method"] == "POST" and r["url"].endswith("/issues")
    )
    assert "共 2 条待合并" in issue_post["json"]["body"] and "10:30" in issue_post["json"]["body"]
    assert "共 2 条待合并" in summary.read_text(encoding="utf-8")


def test_or15_wait_merge_and_close_converge_checklist(tmp_path, monkeypatch, capsys):
    """OR15：等待中合并与关闭都让清单原地收敛（同 issue、无评论、::notice::）。"""
    _prep(monkeypatch, tmp_path)
    fn3 = "alice/new-lib"
    fake_a = FakeAClient(
        [_repo(FN, desc="有描述"), _repo(FN2, desc="有描述"), _repo(fn3, desc="有描述")]
    )
    http = FakeHttp()
    # 轮询序列：#42/#43/#44 均 open → #42 merged、#43 closed → 仅 #44 → #44 merged
    polls = {
        FN: [FakeResponse(json_data=_pr(FN, number=42)),
             FakeResponse(json_data=_pr(FN, number=42, merged=True, state="closed")),
             FakeResponse(json_data=_pr(FN, number=42, merged=True, state="closed"))],
        FN2: [FakeResponse(json_data=_pr(FN2, number=43)),
              FakeResponse(json_data=_pr(FN2, number=43, state="closed", merged=False)),
              FakeResponse(json_data=_pr(FN2, number=43, state="closed", merged=False))],
        fn3: [FakeResponse(json_data=_pr(fn3, number=44)),
              FakeResponse(json_data=_pr(fn3, number=44)),
              FakeResponse(json_data=_pr(fn3, number=44, merged=True, state="closed")),
              FakeResponse(json_data=_pr(fn3, number=44, merged=True, state="closed"))],
    }
    for fn, num in ((FN, 42), (FN2, 43), (fn3, 44)):
        _add_merge_detect(http, fn, prs=[_pr(fn, number=num)])
        _add_head(http, fn, HEAD)
        _add_scan(http, fn, prs=[_pr(fn, number=num)])
        http.add("GET", f"{B}/repos/{fn}/pulls/{num}", *polls[fn])
    http.add("GET", f"{B}/repos/{FN}/readme", _readme_resp("42 合并后真源"))
    http.add("GET", f"{B}/repos/{FN2}/readme", _readme_resp("43 关闭后旧文"))
    http.add("GET", f"{B}/repos/{fn3}/readme", _readme_resp("44 合并后真源"))
    _add_issues(http, existing={"number": 5, "html_url": f"https://github.com/{SELF}/issues/5"},
                search_count=3)  # 清单步 + 两次收敛步
    _seed_snapshot(tmp_path, {fn: _entry(sha=HEAD) for fn in (FN, FN2, fn3)})
    clock = FakeClock()
    assert (
        _run_b(
            tmp_path, monkeypatch, fake_a, http, no_ai=True,
            merge_wait_minutes=30, now_fn=clock.now, sleep_fn=clock.sleep,
        )
        == 0
    )
    body_edits = [
        r["json"]["body"] for r in http.writes()
        if r["method"] == "PATCH" and "/issues/5" in r["url"] and r["json"].get("body")
    ]
    # ① 收敛后的 body 不含已结清条目、仍含未处理条目
    assert any("/pull/42" not in b and "/pull/43" not in b and "/pull/44" in b for b in body_edits)
    assert all("/issues/5" in r["url"] for r in http.writes() if r["method"] == "PATCH")  # ② 同一 issue
    assert "::notice::" in capsys.readouterr().out  # ③ 日志通知
    assert not any("comments" in r["url"] for r in http.writes())  # ④ 无评论轰炸
    assert any(
        r["method"] == "PATCH" and r["json"].get("state") == "closed" for r in http.writes()
    )


def test_or16_timeout_continues_a_with_open_pr(tmp_path, monkeypatch, capsys):
    """OR16：等待超时带未合并 PR 继续 A——⏰ 终态行、规则 2 分支内容、退出 0、轮询有界。"""
    _prep(monkeypatch, tmp_path)
    fake_a = FakeAClient([_repo(FN, desc="")])
    http = FakeHttp()
    _add_merge_detect(http, FN, prs=[_pr(FN, number=7)])
    _add_head(http, FN, HEAD)
    _add_scan(http, FN, prs=[_pr(FN, number=7)])
    http.add(
        "GET", f"{B}/repos/{FN}/pulls/7",
        FakeResponse(json_data=_pr(FN, number=7)), sticky=True,
    )
    _add_issues(http, search_count=2, found_later=True)  # 清单步创建 + ⏰ 终态步命中
    http.add("GET", f"{B}/repos/{FN}/readme?ref={BRANCH}", _readme_resp("分支未合并内容"))
    _seed_snapshot(tmp_path, {FN: _entry(sha=HEAD)})
    clock = FakeClock()
    counter = {"n": 0}
    assert (
        _run_b(
            tmp_path, monkeypatch, fake_a, http, readme_generate=_ai(counter),
            merge_wait_minutes=1, now_fn=clock.now, sleep_fn=clock.sleep,
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "⏰" in out  # ① 终态行
    assert len(clock.sleeps) == 2  # ④ 轮询有上限（60s/30s）
    overview = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "分支未合并内容" in overview  # ② 规则 2：分支内容进 A
    patches = [r for r in http.writes() if r["method"] == "PATCH" and "/issues/5" in r["url"]]
    assert any("⏰" in (r["json"].get("body") or "") for r in patches)  # ③ ⏰ 进 issue


def test_or_final_state_body_keeps_timeout_line_and_settled_prs(tmp_path, monkeypatch):
    """终审 I1 回归：等待超时后收尾追加的终态行必须建立在最新清单 body 上——
    ⏰ 行不被抹掉、等待中已结清的 PR 不复活（§4-3c/§4-5）。"""
    _prep(monkeypatch, tmp_path)
    fake_a = FakeAClient([_repo(FN, desc="有描述"), _repo(FN2, desc="有描述")])
    http = FakeHttp()
    # FN 的 #42 在等待中合并；FN2 的 #43 直到超时仍 open
    _add_merge_detect(http, FN, prs=[_pr(FN, number=42)])
    _add_merge_detect(http, FN2, prs=[_pr(FN2, number=43)])
    _add_head(http, FN, HEAD)
    _add_head(http, FN2, HEAD)
    _add_scan(http, FN, prs=[_pr(FN, number=42)])
    _add_scan(http, FN2, prs=[_pr(FN2, number=43)])
    http.add(
        "GET", f"{B}/repos/{FN}/pulls/42",
        FakeResponse(json_data=_pr(FN, number=42)),
        FakeResponse(json_data=_pr(FN, number=42, merged=True, state="closed")),
    )
    http.add(
        "GET", f"{B}/repos/{FN2}/pulls/43",
        FakeResponse(json_data=_pr(FN2, number=43)), sticky=True,
    )
    _add_issues(http, found_later=True, search_count=3)  # 清单步 + 收敛步 + ⏰ 步
    _seed_snapshot(tmp_path, {FN: _entry(sha=HEAD), FN2: _entry(sha=HEAD)})
    clock = FakeClock()
    assert (
        _run_b(
            tmp_path, monkeypatch, fake_a, http, no_ai=True,
            merge_wait_minutes=1, now_fn=clock.now, sleep_fn=clock.sleep,
        )
        == 0
    )
    body_edits = [
        r["json"]["body"] for r in http.writes()
        if r["method"] == "PATCH" and "/issues/5" in r["url"] and r["json"].get("body")
    ]
    final = body_edits[-1]
    assert "⏰" in final  # 收尾不得抹掉超时终态行
    assert "/pull/43" in final  # 未结清的条目还在
    assert "/pull/42" not in final  # 等待中已合并的条目不复活
    assert "本轮 Overview 已进入 main" in final


def test_or_default_branch_master_threaded_through_b(tmp_path, monkeypatch):
    """终审 I2 回归：仓库默认分支是 master 时，合并检测/开 PR 检查/PR 创建全程
    base=master（§5.5——不再硬编码 main，master 仓库不再 422）。"""
    _prep(monkeypatch, tmp_path)
    fake_a = FakeAClient([dict(_repo(FN, desc=""), default_branch="master")])
    http = FakeHttp()
    http.add(
        "GET",
        f"{B}/repos/{FN}/pulls?state=all&head=alice:{BRANCH}"
        "&base=master&sort=updated&direction=desc",
        FakeResponse(json_data=[]),
    )
    _add_head(http, FN, HEAD)
    _add_compare(http, FN, BASE, HEAD)
    http.add("GET", f"{B}/repos/{FN}/readme", _readme_resp(OLD_README))
    http.add("GET", _open_pr_url(FN, base="master"), FakeResponse(json_data=[]))
    http.add("POST", f"{B}/repos/{FN}/git/blobs", FakeResponse(status=201, json_data={"sha": "blob1"}))
    http.add("POST", f"{B}/repos/{FN}/git/trees", FakeResponse(status=201, json_data={"sha": "tree1"}))
    http.add(
        "POST", f"{B}/repos/{FN}/git/commits", FakeResponse(status=201, json_data={"sha": HEAD})
    )
    http.add("GET", f"{B}/repos/{FN}/git/ref/heads/{BRANCH}", FakeResponse(status=404))
    http.add("POST", f"{B}/repos/{FN}/git/refs", FakeResponse(status=201, json_data={}))
    http.add("POST", f"{B}/repos/{FN}/pulls", FakeResponse(status=201, json_data=_pr(FN, number=7)))
    http.add("GET", _open_pr_url(FN, base="master"), FakeResponse(json_data=[]))  # 扫描
    _seed_snapshot(tmp_path, {FN: _entry(sha=BASE)})
    counter = {"n": 0}
    assert (
        _run_b(tmp_path, monkeypatch, fake_a, http, readme_generate=_ai(counter)) == 0
    )
    pr_posts = [
        r for r in http.writes()
        if r["method"] == "POST" and r["url"].endswith(f"/repos/{FN}/pulls")
    ]
    assert pr_posts and pr_posts[0]["json"]["base"] == "master"  # ① PR 目标 = 默认分支
    assert http.has("GET", f"base=master&sort=updated")  # ② 合并检测同样用 master
    assert http.has("GET", _open_pr_url(FN, base="master"))  # ③ 开 PR 检查与扫描


def test_or_final_state_skipped_when_overview_not_updated(tmp_path, monkeypatch):
    """终审 I4 回归：本轮没更新 Overview（--skip-readme）→ 收尾不写「已进入 main」
    终态行（避免在提交发生前替提交步骤打包票）；清单本身照常刷新。

    预置既有清单 issue：无等待路径下刷新走 PATCH，终态行若误写也落在同一批
    PATCH 上——两个断言由此独立于「issue 是否新建」。
    """
    _prep(monkeypatch, tmp_path)
    fake_a = FakeAClient([_repo(FN, desc="有描述")])
    http = FakeHttp()
    _add_merge_detect(http, FN, prs=[_pr(FN, number=7)])
    _add_head(http, FN, HEAD)
    _add_scan(http, FN, prs=[_pr(FN, number=7)])
    http.add("GET", f"{B}/repos/{FN}/readme?ref={BRANCH}", _readme_resp("分支内容"))
    _add_issues(http, existing={"number": 5, "html_url": f"https://github.com/{SELF}/issues/5"})
    _seed_snapshot(tmp_path, {FN: _entry(sha=HEAD)})
    rc = _run_b(tmp_path, monkeypatch, fake_a, http, readme_generate=_ai({}), skip_readme=True)
    assert rc == 0
    body_edits = [
        r["json"]["body"] for r in http.writes()
        if r["method"] == "PATCH" and "/issues/5" in r["url"] and r["json"].get("body")
    ]
    assert body_edits  # 清单仍刷新
    assert all("本轮 Overview" not in b for b in body_edits)


def test_or17_all_settled_early_continues_and_closes_issue(tmp_path, monkeypatch):
    """OR17：清单全部结清提前继续（轮询远小于超时）并自动关 issue；A 照常。"""
    _prep(monkeypatch, tmp_path)
    fake_a = FakeAClient([_repo(FN, desc="有描述")])
    http = FakeHttp()
    _add_merge_detect(http, FN, prs=[_pr(FN, number=7)])
    _add_head(http, FN, HEAD)
    _add_scan(http, FN, prs=[_pr(FN, number=7)])
    merged = FakeResponse(json_data=_pr(FN, number=7, merged=True, state="closed"))
    http.add("GET", f"{B}/repos/{FN}/pulls/7", merged, merged)  # 轮询 + 结清复查
    http.add("GET", f"{B}/repos/{FN}/readme", _readme_resp("合并后真源"))
    _add_issues(http, search_count=2, found_later=True)  # 清单步创建 + 结清关闭步命中
    _seed_snapshot(tmp_path, {FN: _entry(sha=HEAD)})
    clock = FakeClock()
    assert (
        _run_b(
            tmp_path, monkeypatch, fake_a, http, no_ai=True,
            merge_wait_minutes=30, now_fn=clock.now, sleep_fn=clock.sleep,
        )
        == 0
    )
    assert clock.t == 30  # ① 一次轮询即清空，远小于 30 分钟窗口
    assert any(
        r["method"] == "PATCH" and r["json"].get("state") == "closed" for r in http.writes()
    )  # ② 自动关 issue
    assert (tmp_path / "README.md").exists()  # ③ A 照常


# ============================ OR20（逐仓进度） ============================


def test_or20_per_repo_progress_visible_and_ordered(tmp_path, monkeypatch, capsys):
    """OR20：串行 AI 静默期逐仓进度可见——仓名+阶段词、首仓终态先于次仓进度、Summary 行数==队列规模。"""
    summary = _prep(monkeypatch, tmp_path)
    fake_a = FakeAClient([_repo(FN, desc="有描述"), _repo(FN2, desc="有描述")])
    http = FakeHttp()
    _full_b_routes(http, FN, number=7)
    _full_b_routes(http, FN2, number=8)
    _add_issues(http)
    _seed_snapshot(tmp_path, {FN: _entry(sha=BASE), FN2: _entry(sha=BASE)})
    counter = {"n": 0}
    assert _run_b(tmp_path, monkeypatch, fake_a, http, readme_generate=_ai(counter)) == 0

    out = capsys.readouterr().out
    assert FN in out and FN2 in out  # ① 两个仓名
    assert "AI 生成中" in out and "已推分支" in out  # ① 阶段词
    assert out.index("已推分支") < out.rindex("AI 生成中")  # ② 首仓终态先于次仓进度
    text = summary.read_text(encoding="utf-8")
    assert sum(1 for line in text.splitlines() if line.startswith("- `alice/")) == 2  # ③
    assert counter["n"] == 2


# ============================ 2026-10-23 勾选批量合并（钩子①） ============================


def test_or30_checked_merge_at_run_start_advances_baseline_same_round(tmp_path, monkeypatch):
    """2026-10-23 钩子①：运行开始即按清单勾选合并 PR——同轮基线推进、清单重建不再含它。

    勾选（- [x]）= 用户的人工合并授权：上轮留下的勾选在本轮开局就被代为合并，
    合并检测同轮把基线推到 PR head（不重跑该批次），清单重建（open_prs 空）关闭
    issue 且 body 不再含该 PR。
    """
    summary = _prep(monkeypatch, tmp_path)
    fake_a = FakeAClient([_repo(FN, desc="")], readmes={FN: "合并后真源"})
    http = FakeHttp()
    ticked = (
        "## 🔔 README 更新待合并清单（更新于 2026-10-22 18:30，北京时间）\n"
        "@alice\n"
        "- [x] cool-tool：[PR](https://github.com/alice/cool-tool/pull/7)"
        "（[新版 README 预览](https://github.com/alice/cool-tool/blob/readme-auto/update/README.md)）\n"
    )
    _add_issues(
        http,
        existing={
            "number": 5,
            "html_url": f"https://github.com/{SELF}/issues/5",
            "body": ticked,
        },
    )
    http.add(  # PUT 前的状态查询（2026-10-23 WP2 降噪）：PR 仍 open → 照常 PUT
        "GET", f"{B}/repos/{FN}/pulls/7",
        FakeResponse(json_data=_pr(FN, number=7)),
        sticky=True,
    )
    http.add(
        "PUT", f"{B}/repos/{FN}/pulls/7/merge",
        FakeResponse(json_data={"merged": True, "sha": "f" * 40,
                                "message": "Pull Request successfully merged"}),
    )
    _add_merge_detect(http, FN, prs=[_pr(FN, number=7, merged=True, state="closed")])
    _add_head(http, FN, HEAD)  # 基线推进到 HEAD → 零成本，不 compare
    http.add("GET", f"{B}/repos/{FN}/readme", _readme_resp("合并后真源"))
    _add_scan(http, FN, prs=[])  # 已合并 → 扫描不到 open PR
    _seed_snapshot(tmp_path, {FN: _entry(sha=BASE)})

    counter = {"n": 0}
    assert _run_b(tmp_path, monkeypatch, fake_a, http, readme_generate=_ai(counter)) == 0

    # ① 勾选授权触发了真实合并调用（走 WORKFLOW_PAT 客户端的 PUT）
    merges = [r for r in http.writes() if r["method"] == "PUT"]
    assert merges and merges[0]["url"] == f"{B}/repos/{FN}/pulls/7/merge"
    # ② 同轮基线推进：快照 sha 已到合并 PR 的 head（HEAD），下轮不重跑该批次
    snap = json.loads((tmp_path / "data" / "snapshot.json").read_text(encoding="utf-8"))
    assert snap["repo_readmes"][FN]["sha"] == HEAD
    # ③ 清单重建（open_prs 空 → 关闭）不再含该 PR 条目
    closes = [r for r in http.writes()
              if r["method"] == "PATCH" and r["json"].get("state") == "closed"]
    assert closes and "/pull/7" not in closes[0]["json"]["body"]
    assert "勾选" in summary.read_text(encoding="utf-8")  # ④ Summary 留痕
    assert counter["n"] == 0  # ⑤ 队列空（基线已推进）→ 零 AI


def test_or31_checkbox_merge_during_wait_converges_same_tick(tmp_path, monkeypatch):
    """2026-10-23 钩子②：等待期间新勾选 → 每 tick 重读清单直读（按 issue 号）代为合并 →
    本轮轮询当轮看到合并、清单随之结清关闭。"""
    summary = _prep(monkeypatch, tmp_path)
    fake_a = FakeAClient([_repo(FN, desc="")], readmes={FN: "合并后真源"})
    http = FakeHttp()
    unticked = (
        "## 🔔 README 更新待合并清单（更新于 2026-10-23 10:00，北京时间）\n"
        "@alice\n"
        "- [ ] cool-tool：[PR](https://github.com/alice/cool-tool/pull/7)"
        "（[新版 README 预览](https://github.com/alice/cool-tool/blob/readme-auto/update/README.md)）\n"
    )
    ticked = unticked.replace("- [ ] cool-tool：", "- [x] cool-tool：")
    _add_issues(
        http,
        existing={
            "number": 5,
            "html_url": f"https://github.com/{SELF}/issues/5",
            "body": unticked,
        },
    )
    # tick 直读（按号 GET，非整表搜索）：勾选出现在等待期间 → 读到的是已勾选 body
    http.add(
        "GET", f"{B}/repos/{SELF}/issues/5",
        FakeResponse(json_data={"number": 5, "title": "🔔 README 更新待合并清单（待合并 PR）",
                                "html_url": f"https://github.com/{SELF}/issues/5",
                                "body": ticked}),
        sticky=True,
    )
    http.add(
        "PUT", f"{B}/repos/{FN}/pulls/7/merge",
        FakeResponse(json_data={"merged": True, "sha": "f" * 40, "message": "merged"}),
    )
    merged = FakeResponse(json_data=_pr(FN, number=7, merged=True, state="closed"))
    # FIFO：状态查询（open，WP2 降噪前置 GET）→ 轮询（merged）→ §5.6 结清复查（merged）
    http.add(
        "GET", f"{B}/repos/{FN}/pulls/7",
        FakeResponse(json_data=_pr(FN, number=7)),
        merged,
        merged,
    )
    _add_merge_detect(http, FN, prs=[])
    _add_head(http, FN, HEAD)
    _add_scan(http, FN, prs=[_pr(FN, number=7)])
    http.add("GET", f"{B}/repos/{FN}/readme", _readme_resp("合并后真源"))
    _seed_snapshot(tmp_path, {FN: _entry(sha=HEAD)})
    clock = FakeClock()
    assert (
        _run_b(
            tmp_path, monkeypatch, fake_a, http, no_ai=True,
            merge_wait_minutes=30, now_fn=clock.now, sleep_fn=clock.sleep,
        )
        == 0
    )
    # ① 勾选合并真实发生，且先于本轮轮询（状态查询 → PUT → poll 顺序；
    #    WP2 降噪后 PUT 前多一次 GET pulls/7 状态查询，断言按 FIFO 顺序锚定）
    methods = [(r["method"], r["url"]) for r in http.requests]
    i_state = methods.index(("GET", f"{B}/repos/{FN}/pulls/7"))  # PUT 前的状态查询
    i_put = methods.index(("PUT", f"{B}/repos/{FN}/pulls/7/merge"))
    polls = [i for i, (m, u) in enumerate(methods)
             if m == "GET" and u == f"{B}/repos/{FN}/pulls/7"]
    assert i_state < i_put < polls[1]  # 状态查询先于合并，合并先于轮询
    # ② 当轮收敛：一次轮询即清空（未耗尽 30 分钟窗口），清单关闭
    assert clock.t == 30  # 恰一个 POLL_SECONDS
    assert any(
        r["method"] == "PATCH" and r["json"].get("state") == "closed" for r in http.writes()
    )
    assert "已按清单勾选合并" in summary.read_text(encoding="utf-8")  # ③ Summary 留痕


def test_or33_open_pr_detail_line_lands_in_issue_body(tmp_path, monkeypatch):
    """W3（2026-10-23 用户反馈「issue 里每个 pr 的细节太少」）：扫描期逐 PR 单 GET 明细
    → 渲染进清单 body 的 ↳ 行（文件数/增删行/提交标题），每仓每轮恰一次。"""
    _prep(monkeypatch, tmp_path)
    fake_a = FakeAClient([_repo(FN, desc="有描述")])
    http = FakeHttp()
    _add_merge_detect(http, FN, prs=[_pr(FN, number=7)])
    _add_head(http, FN, HEAD)
    _add_scan(http, FN, prs=[_pr(FN, number=7)])  # open 列表 + 明细单 GET 假件
    _add_issues(http)  # 无既有清单 → 创建路径（body 即 build_checklist_body 输出）
    _seed_snapshot(tmp_path, {FN: _entry(sha=HEAD)})

    assert _run_b(tmp_path, monkeypatch, fake_a, http, no_ai=True) == 0

    creates = [r for r in http.writes() if r["method"] == "POST" and r["url"].endswith("/issues")]
    assert creates  # 清单创建
    body = creates[0]["json"]["body"]
    assert "  ↳ 2 个文件 · +34/-11 行 · 3 次提交：修复拼写错误、更新项目列表" in body
    pulls7 = [r for r in http.requests if r["method"] == "GET" and r["url"].endswith("/pulls/7")]
    assert len(pulls7) == 1  # 明细恰一次（list 接口不带统计，单 GET 补齐）


def test_or32_ticked_during_ai_phase_merged_by_prescan_hook(tmp_path, monkeypatch):
    """2026-10-23 钩子①'：AI/推送阶段期间新勾选 → 扫描前收割合并，不被清单重建抹掉。

    开局清单未勾选（钩子① 无事）→ AI 阶段用户勾上 → 钩子①'（扫描前按号复读）
    代为合并并同轮推进基线；合并先于 open-PR 扫描（扫描不到已合并 PR）。
    merge-detect 两次 = 常规推进循环一次 + 钩子①' 自带推进一次。
    """
    summary = _prep(monkeypatch, tmp_path)
    fake_a = FakeAClient([_repo(FN, desc="")], readmes={FN: "合并后真源"})
    http = FakeHttp()
    unticked = (
        "## 🔔 README 更新待合并清单（更新于 2026-10-23 10:00，北京时间）\n"
        "@alice\n"
        "- [ ] cool-tool：[PR](https://github.com/alice/cool-tool/pull/7)"
        "（[新版 README 预览](https://github.com/alice/cool-tool/blob/readme-auto/update/README.md)）\n"
    )
    ticked = unticked.replace("- [ ] cool-tool：", "- [x] cool-tool：")
    _add_issues(
        http,
        existing={
            "number": 5,
            "html_url": f"https://github.com/{SELF}/issues/5",
            "body": unticked,
        },
    )
    http.add(  # 钩子①' 按号直读：AI 阶段期间用户已勾选
        "GET", f"{B}/repos/{SELF}/issues/5",
        FakeResponse(json_data={"number": 5, "html_url": f"https://github.com/{SELF}/issues/5",
                                "body": ticked}),
        sticky=True,
    )
    http.add(  # PUT 前的状态查询（WP2 降噪）：仍 open → 照常 PUT
        "GET", f"{B}/repos/{FN}/pulls/7",
        FakeResponse(json_data=_pr(FN, number=7)),
        sticky=True,
    )
    http.add(
        "PUT", f"{B}/repos/{FN}/pulls/7/merge",
        FakeResponse(json_data={"merged": True, "sha": "f" * 40, "message": "merged"}),
    )
    _add_merge_detect(http, FN, prs=[])  # ① 常规推进循环：尚未合并
    http.add(  # ② 钩子①' 合并后的自带推进：已合并 → 基线推进到 HEAD
        "GET", _merged_detect_url(FN),
        FakeResponse(json_data=[_pr(FN, number=7, merged=True, state="closed")]),
    )
    _add_head(http, FN, HEAD)
    _add_compare(http, FN, BASE, HEAD)
    _add_scan(http, FN, prs=[])  # 扫描在合并之后 → 已合并 PR 不再入清单
    _seed_snapshot(tmp_path, {FN: _entry(sha=BASE)})

    assert _run_b(tmp_path, monkeypatch, fake_a, http, no_ai=True) == 0

    # ① 勾选合并真实发生，且先于 open-PR 扫描（否则 PR 会进清单、重建带幻影勾选）
    methods = [(r["method"], r["url"]) for r in http.requests]
    i_put = methods.index(("PUT", f"{B}/repos/{FN}/pulls/7/merge"))
    i_scan = methods.index(("GET", _open_pr_url(FN)))
    assert i_put < i_scan
    # ② merge-detect 恰两次：常规推进循环 + 钩子①' 自带推进（基线同轮到 HEAD）
    detect = [u for m, u in methods if m == "GET" and u == _merged_detect_url(FN)]
    assert len(detect) == 2
    snap = json.loads((tmp_path / "data" / "snapshot.json").read_text(encoding="utf-8"))
    assert snap["repo_readmes"][FN]["sha"] == HEAD
    # ③ 清单关闭且不再含该 PR（无幻影勾选）；Summary 留痕（推送后）
    closes = [r for r in http.writes()
              if r["method"] == "PATCH" and r["json"].get("state") == "closed"]
    assert closes and "/pull/7" not in closes[0]["json"]["body"]
    assert "（推送后）" in summary.read_text(encoding="utf-8")


# ============================ 2026-10-23 勾选降噪 + 钩子③（WP2） ============================


def test_merge_ticked_already_merged_prints_notice_no_put(tmp_path, monkeypatch, capsys):
    """已手动合并的 PR 再被勾选 → 先 GET 状态分流成「已合并，无需处理」，不发吓人的
    405「勾选合并失败」，也不再 PUT；同轮仍推基线（下轮不重跑该批次）。"""
    summary = _prep(monkeypatch, tmp_path)
    fake_a = FakeAClient([_repo(FN, desc="")], readmes={FN: "合并后真源"})
    http = FakeHttp()
    ticked = (
        "## 🔔 README 更新待合并清单（更新于 2026-10-22 18:30，北京时间）\n"
        "@alice\n"
        "- [x] cool-tool：[PR](https://github.com/alice/cool-tool/pull/7)"
        "（[新版 README 预览](https://github.com/alice/cool-tool/blob/readme-auto/update/README.md)）\n"
    )
    _add_issues(http, existing={"number": 5, "html_url": f"https://github.com/{SELF}/issues/5",
                                "body": ticked})
    http.add(  # PUT 前的状态查询：已合并（真实 API 用 merged_at）
        "GET", f"{B}/repos/{FN}/pulls/7",
        FakeResponse(json_data=_pr(FN, number=7, merged=True, state="closed")),
        sticky=True,
    )
    # 无 PUT merge 路由——真发了 PUT 会被 FakeHttp 直接 AssertionError
    _add_merge_detect(http, FN, prs=[_pr(FN, number=7, merged=True, state="closed")])
    _add_head(http, FN, HEAD)
    http.add("GET", f"{B}/repos/{FN}/readme", _readme_resp("合并后真源"))
    _add_scan(http, FN, prs=[])
    _seed_snapshot(tmp_path, {FN: _entry(sha=BASE)})

    counter = {"n": 0}
    capsys.readouterr()
    assert _run_b(tmp_path, monkeypatch, fake_a, http, readme_generate=_ai(counter)) == 0

    out = capsys.readouterr().out
    assert "已合并，无需处理" in out  # ① 降噪文案（不再是吓人的「勾选合并失败」）
    assert "勾选合并失败" not in out
    puts = [r for r in http.writes() if r["method"] == "PUT"]
    assert puts == []  # ② 不发任何 PUT
    snap = json.loads((tmp_path / "data" / "snapshot.json").read_text(encoding="utf-8"))
    assert snap["repo_readmes"][FN]["sha"] == HEAD  # ③ 同轮推基线
    text = summary.read_text(encoding="utf-8")
    assert "已合并，无需处理" in text  # ④ Summary 留痕


def test_merge_ticked_closed_pr_warns_no_put(tmp_path, monkeypatch, capsys):
    """已关闭（拒绝）的 PR 再被勾选 → 警告「已关闭（拒绝），不会合并」，不发 PUT。"""
    _prep(monkeypatch, tmp_path)
    fake_a = FakeAClient([_repo(FN, desc="")], readmes={FN: "真源"})
    http = FakeHttp()
    ticked = (
        "## 🔔 README 更新待合并清单（更新于 2026-10-22 18:30，北京时间）\n"
        "@alice\n"
        "- [x] cool-tool：[PR](https://github.com/alice/cool-tool/pull/7)"
        "（[新版 README 预览](https://github.com/alice/cool-tool/blob/readme-auto/update/README.md)）\n"
    )
    _add_issues(http, existing={"number": 5, "html_url": f"https://github.com/{SELF}/issues/5",
                                "body": ticked})
    http.add(
        "GET", f"{B}/repos/{FN}/pulls/7",
        FakeResponse(json_data={"number": 7, "state": "closed", "merged": False,
                                "merged_at": None}),
        sticky=True,
    )
    # 无 PUT merge 路由；合并检测无记录（closed 未合并 → 基线不动）
    _add_merge_detect(http, FN, prs=[])
    _add_head(http, FN, BASE)
    http.add("GET", f"{B}/repos/{FN}/readme", _readme_resp("真源"))
    _add_scan(http, FN, prs=[])
    _seed_snapshot(tmp_path, {FN: _entry(sha=BASE)})

    capsys.readouterr()
    assert _run_b(tmp_path, monkeypatch, fake_a, http, no_ai=True) == 0

    out = capsys.readouterr()
    assert "已关闭（拒绝）" in out.err  # ① 警告（stderr，不伪装成功）
    assert [r for r in http.writes() if r["method"] == "PUT"] == []  # ② 不 PUT
    snap = json.loads((tmp_path / "data" / "snapshot.json").read_text(encoding="utf-8"))
    assert snap["repo_readmes"][FN]["sha"] == BASE  # ③ 未合并 → 基线不动


def test_merge_ticked_forged_repo_filtered_out(tmp_path, monkeypatch, capsys):
    """安全过滤：清单 body 被改成指向他人仓库的 PR（伪造链接）→ allowed 过滤直接忽略，
    绝不拿 PAT 去合并本账号之外的仓库；整轮其余流程照常。"""
    _prep(monkeypatch, tmp_path)
    fake_a = FakeAClient([_repo(FN, desc="有描述")])
    http = FakeHttp()
    ticked = (
        "## 🔔 README 更新待合并清单（更新于 2026-10-23 10:00，北京时间）\n"
        "@alice\n"
        "- [x] other：[PR](https://github.com/evil/other/pull/3)"
        "（[新版 README 预览](https://github.com/evil/other/blob/readme-auto/update/README.md)）\n"
    )
    _add_issues(http, existing={"number": 5, "html_url": f"https://github.com/{SELF}/issues/5",
                                "body": ticked})
    http.add(  # 钩子①'/钩子③ 复读同 body
        "GET", f"{B}/repos/{SELF}/issues/5",
        FakeResponse(json_data={"number": 5, "html_url": f"https://github.com/{SELF}/issues/5",
                                "body": ticked}),
        sticky=True,
    )
    _full_b_routes(http)  # 本账号仓库的常规 B 流程照常（evil 仓库零路由）
    _seed_snapshot(tmp_path, {FN: _entry(sha=BASE)})

    capsys.readouterr()
    assert _run_b(tmp_path, monkeypatch, fake_a, http, no_ai=True) == 0

    out = capsys.readouterr()
    assert "不在本账号仓库范围" in out.err  # ① 明确警告
    evil_reqs = [r for r in http.requests if "evil/other" in r["url"]]
    assert evil_reqs == []  # ② 对伪造仓库零请求（GET/PUT 都没有）


def test_or34_ticked_before_rebuild_merged_by_hook3(tmp_path, monkeypatch, capsys):
    """钩子③（2026-10-23 勾选丢失窗口修复）：勾选出现在钩子①' 复读之后、清单重建之前
    ——重建前再收割一次，勾选仍被合并（否则会被重建抹掉、永不生效）。"""
    summary = _prep(monkeypatch, tmp_path)
    fake_a = FakeAClient([_repo(FN, desc="")], readmes={FN: "真源"})
    http = FakeHttp()
    unticked = (
        "## 🔔 README 更新待合并清单（更新于 2026-10-23 10:00，北京时间）\n"
        "@alice\n"
        "- [ ] cool-tool：[PR](https://github.com/alice/cool-tool/pull/7)"
        "（[新版 README 预览](https://github.com/alice/cool-tool/blob/readme-auto/update/README.md)）\n"
    )
    ticked = unticked.replace("- [ ] cool-tool：", "- [x] cool-tool：")
    _add_issues(http, existing={"number": 5, "html_url": f"https://github.com/{SELF}/issues/5",
                                "body": unticked})  # 开局搜索：未勾
    http.add(  # 按号复读 FIFO：钩子①' 读到未勾（1st）、钩子③ 读到刚勾上的（2nd）
        "GET", f"{B}/repos/{SELF}/issues/5",
        FakeResponse(json_data={"number": 5, "html_url": f"https://github.com/{SELF}/issues/5",
                                "body": unticked}),
        FakeResponse(json_data={"number": 5, "html_url": f"https://github.com/{SELF}/issues/5",
                                "body": ticked}),
    )
    http.add(  # PUT 前的状态查询（WP2 降噪）：仍 open → 照常 PUT
        "GET", f"{B}/repos/{FN}/pulls/7",
        FakeResponse(json_data=_pr(FN, number=7)),
        sticky=True,
    )
    http.add(  # 钩子③ 的 PUT：重建前收割发生在此
        "PUT", f"{B}/repos/{FN}/pulls/7/merge",
        FakeResponse(json_data={"merged": True, "sha": "f" * 40, "message": "merged"}),
    )
    _add_merge_detect(http, FN, prs=[_pr(FN, number=7, merged=True, state="closed")])
    _add_head(http, FN, HEAD)
    _add_compare(http, FN)  # 队列期基线未推进（BASE≠HEAD）→ compare 照常
    http.add("GET", f"{B}/repos/{FN}/readme", _readme_resp("真源"))
    _add_scan(http, FN, prs=[])  # 钩子③ 时尚无 open PR；合并后基线推进
    _seed_snapshot(tmp_path, {FN: _entry(sha=BASE)})

    capsys.readouterr()
    assert _run_b(tmp_path, monkeypatch, fake_a, http, no_ai=True) == 0

    puts = [r for r in http.writes() if r["method"] == "PUT"]
    assert puts and puts[0]["url"] == f"{B}/repos/{FN}/pulls/7/merge"  # ① 钩子③ 完成合并
    snap = json.loads((tmp_path / "data" / "snapshot.json").read_text(encoding="utf-8"))
    assert snap["repo_readmes"][FN]["sha"] == HEAD  # ② 同轮推基线
    assert "已按清单勾选合并（重建前）" in summary.read_text(encoding="utf-8")  # ③ 留痕
    closes = [r for r in http.writes()
              if r["method"] == "PATCH" and r["json"].get("state") == "closed"]
    assert closes and "/pull/7" not in closes[0]["json"]["body"]  # ④ 重建无幻影条目
