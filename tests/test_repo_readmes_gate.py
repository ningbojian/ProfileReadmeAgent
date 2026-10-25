"""模式 B 与增量门的交互测试（测试计划 Q09、Q17-Q21，离线 fixture + 注入 fetcher）。

Q21 落在本文件而非测试计划原写的 test_repo_readmes_cli.py——门测试已随
演进集中到本文件（Q09 同款先例），保持同一套 fixture 夹具。
"""

import datetime as dt
import json
from pathlib import Path

from fake_github import FakeResponse

from profile_agent.cli import run

FIXTURES = Path(__file__).parent / "fixtures"
TEMPLATE = Path(__file__).parent.parent / "templates" / "profile.md.j2"

# fixture 归一化后的参与仓库（fork/私有被剔除，old-profile 被 config 排除）
PARTICIPANTS = ["alice/archived-one", "alice/cool-tool", "alice/old-thing"]


class _FrozenDT:
    """固定时间：两次运行渲染出的 README 逐字节可比。"""

    FROZEN = dt.datetime(2026, 10, 22, 10, 0, tzinfo=dt.timezone(dt.timedelta(hours=8)))

    @classmethod
    def now(cls, tz=None):
        if tz is not None:
            return cls.FROZEN.astimezone(tz)
        return cls.FROZEN


def _freeze_time(monkeypatch):
    import profile_agent.cli as cli_mod

    monkeypatch.setattr(cli_mod, "datetime", _FrozenDT)


class FakeFetchers:
    def __init__(self, heads=None, compares=None):
        self.heads = dict(heads or {})
        self.compares = dict(compares or {})
        self.head_calls: list[str] = []
        self.compare_calls: list[tuple] = []

    def fetch_head(self, full_name: str) -> str:
        self.head_calls.append(full_name)
        if full_name not in self.heads:
            raise AssertionError(f"未注册的 head：{full_name}")
        return self.heads[full_name]

    def fetch_compare(self, full_name: str, base: str, head: str):
        self.compare_calls.append((full_name, base, head))
        key = (full_name, base, head)
        if key not in self.compares:
            raise AssertionError(f"未注册的 compare：{key}")
        return self.compares[key]


def _heads_all(sha="head0001"):
    return {fn: sha for fn in PARTICIPANTS}


def _write_config(tmp_path: Path, *, rr_enabled: bool = True, extra: str = "") -> Path:
    cfg = tmp_path / "config.yaml"
    p = lambda x: x.as_posix()  # noqa: E731
    rr = "true" if rr_enabled else "false"
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
  enabled: {rr}
{extra}
snapshot:
  path: "{p(tmp_path / 'data' / 'snapshot.json')}"
""",
        encoding="utf-8",
    )
    return cfg


def _run(cfg: Path, fetchers=None, **kwargs) -> int:
    kwargs.setdefault("fixture", FIXTURES)
    kwargs.setdefault("no_ai", True)
    if fetchers is not None:
        kwargs["readme_fetchers"] = fetchers
    return run(cfg, **kwargs)


def _snapshot(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "data" / "snapshot.json").read_text(encoding="utf-8"))


def test_global_force_forces_b_first_seen_without_recording_baseline(tmp_path, capsys):
    """Q09：全局 force + b_force 同时强制 A 与 B——首见仓合成全量队列项入队
    （不发 compare、不记基线）。

    有意变更（A-P0 交互治理）：force 已与模式 B 解耦（只强制主页），A+B 全量
    语义改由 force=True + b_force=True 表达，与 workflow「两者都强制」下拉一一对应。
    """
    cfg = _write_config(tmp_path)
    fetchers = FakeFetchers(heads=_heads_all("head789"))
    assert _run(cfg, fetchers, force=True, b_force=True) == 0
    out = capsys.readouterr().out
    assert "无变化，跳过更新" not in out  # force 开门
    assert "入队 3 仓" in out  # 三仓合成入队（旧空树 compare 路径在生产必 404）
    assert fetchers.compare_calls == []
    assert (tmp_path / "README.md").exists()  # A 照常
    assert _snapshot(tmp_path)["repo_readmes"] == {}  # no_ai 不落条目（与 Q21 成对：全局 force=A+B）


def test_force_alone_does_not_force_b(tmp_path, capsys):
    """回归锁（A-P0 交互治理）：全局 force 只强制主页，不连带强制模式 B。

    模式 B 的强制只认 --repo-readmes（workflow「强制重跑各仓库 README」下拉项），
    避免「勾了 force，repo_readmes 变空操作」的重叠困惑。
    """
    cfg = _write_config(tmp_path)
    # 第一轮：首见 → 建立 B 基线（head0001）
    assert _run(cfg, FakeFetchers(heads=_heads_all("head0001"))) == 0
    capsys.readouterr()
    # 第二轮：heads 完全没变，只勾 force → 主页门打开，但 B 零队列（不入队）
    assert _run(cfg, FakeFetchers(heads=_heads_all("head0001")), force=True) == 0
    out = capsys.readouterr().out
    assert "无变化，跳过更新" not in out  # force 绕过了主页增量门
    assert "入队" not in out  # 但 B 没有被强制全量重跑


def test_pending_opens_gate_and_b_runs(tmp_path, capsys):
    """Q17：任一仓库 pending 积压 → 增量门开启，B 跑过且基线推进可观察。"""
    cfg = _write_config(tmp_path)
    # 第一轮：首见，三仓各记基线 head0001
    assert _run(cfg, FakeFetchers(heads=_heads_all())) == 0
    snap = _snapshot(tmp_path)
    assert set(snap["repo_readmes"]) == set(PARTICIPANTS)
    # 人工构造：cool-tool 置 pending（不依赖 M2 缺 key 路径）；old-thing 基线改 stale 便于观察推进
    path = tmp_path / "data" / "snapshot.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["repo_readmes"]["alice/cool-tool"]["pending"] = True
    data["repo_readmes"]["alice/old-thing"]["sha"] = "stale00"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    # 第二轮：old-thing 基线..HEAD 出现仅 README 变更 → 基线推进（B 状态可观察）
    fetchers = FakeFetchers(
        heads=_heads_all("head0001"),
        compares={
            ("alice/old-thing", "stale00", "head0001"): {
                "commits": [{"sha": "head0001", "message": "docs: bot"}],
                "files": [{"filename": "README.md", "patch": "@@ -1 +1 @@\n-a\n+b"}],
            }
        },
    )
    capsys.readouterr()
    assert _run(cfg, fetchers) == 0
    out = capsys.readouterr().out
    assert "无变化，跳过更新" not in out  # pending 开门
    snap = _snapshot(tmp_path)
    assert snap["repo_readmes"]["alice/old-thing"]["sha"] == "head0001"  # 本轮新计算的 B 状态
    assert snap["repo_readmes"]["alice/cool-tool"]["sha"] == "head0001"  # 未动仓库不被强制入队
    assert fetchers.compare_calls == [("alice/old-thing", "stale00", "head0001")]


def test_gate_stays_closed_without_pending(tmp_path, capsys):
    """Q18：无 pending、指纹不变 → 门维持原逻辑关闭，B 连 fetcher 都不碰。"""
    cfg = _write_config(tmp_path)
    assert _run(cfg, FakeFetchers(heads=_heads_all())) == 0
    snap_path = tmp_path / "data" / "snapshot.json"
    snap_mtime = snap_path.stat().st_mtime_ns
    fetchers = FakeFetchers()  # 任何调用都会 AssertionError
    capsys.readouterr()
    assert _run(cfg, fetchers) == 0
    out = capsys.readouterr().out
    assert "无变化，跳过更新" in out
    assert fetchers.head_calls == [] and fetchers.compare_calls == []
    assert snap_path.stat().st_mtime_ns == snap_mtime  # snapshot 未被改写


def test_gate_open_but_empty_queue_runs_at_zero_cost(tmp_path, monkeypatch, capsys):
    """Q19：pending 开门但队列空（无新提交）→ 零成本跑完，Summary/日志有可读原因。"""
    _freeze_time(monkeypatch)
    cfg = _write_config(tmp_path)
    assert _run(cfg, FakeFetchers(heads=_heads_all())) == 0
    path = tmp_path / "data" / "snapshot.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["repo_readmes"]["alice/cool-tool"]["pending"] = True
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    readme_path = tmp_path / "README.md"
    readme_mtime = readme_path.stat().st_mtime_ns
    fetchers = FakeFetchers(heads=_heads_all())  # base==head → 零 compare
    capsys.readouterr()
    assert _run(cfg, fetchers) == 0
    out = capsys.readouterr().out
    assert "模式 B 状态" in out and "无新提交" in out  # 静默有可读原因（§4 护栏）
    assert fetchers.compare_calls == []  # 零成本
    assert readme_path.stat().st_mtime_ns == readme_mtime  # README 无重写


def test_unrelated_change_opens_gate_but_untouched_repo_zero_cost(tmp_path, capsys):
    """Q20：无关仓库开门（指纹变化）时，未动仓库零成本跳过。"""
    cfg = _write_config(tmp_path)
    assert _run(cfg, FakeFetchers(heads=_heads_all())) == 0
    # 篡改指纹模拟无关仓库数据变化开门
    path = tmp_path / "data" / "snapshot.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["stats_fingerprint"] = "sha256:tampered"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    fetchers = FakeFetchers(heads=_heads_all())  # 各仓 base==head → 零 compare
    capsys.readouterr()
    assert _run(cfg, fetchers) == 0
    out = capsys.readouterr().out
    assert "无变化，跳过更新" not in out  # 门已打开
    assert fetchers.head_calls == PARTICIPANTS  # B 跑过
    assert fetchers.compare_calls == []  # 但未动仓库零成本


def test_q21_checkbox_forces_b_only_and_leaves_a_gate_untouched(tmp_path, capsys, monkeypatch):
    """Q21（与 Q09 成对）：数据无变化 + 勾选 repo_readmes → 门不早退、B 被强制执行。

    A 侧不被牵连：README mtime 不变（不碰 A 的门）、bio 不再生成。
    对话框文案承诺「全量重跑开 PR」——勾选必须参与开门，否则最该干活的场景
    （数据无变化时手动强制）直接被「无变化，跳过更新」早退吞掉（设计验证实测）。
    """
    import profile_agent.cli as cli_mod

    # 有意变更（A-P0）：项目介绍功能已移除，intro 计数与 update_project_intros 挡板一并下线
    counters = {"bio_gen": 0}

    def fake_bio(
        payload,
        cfg,
        *,
        input_hash,
        prev_hash,
        prev_text,
        prev_text_en="",
        prev_generated_at,
        bilingual=True,
    ):
        if prev_text and input_hash == prev_hash:
            return prev_text, prev_text_en, False  # 新鲜复用（与真 generate_bio 的内部跳过同语义）
        counters["bio_gen"] += 1
        return "测试简介，用于主页展示的中文简介文本。", "", True

    monkeypatch.setattr(cli_mod, "generate_bio", fake_bio)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")  # ai_active 开（key 只进夹具不调 API）

    cfg = _write_config(
        tmp_path,
        extra="ai:\n  fill_empty_descriptions: false\n",
    )
    fake_readme_ai = lambda system, prompt: "新托管内容X"  # noqa: E731

    # 第一轮：建立指纹、基线与新鲜简介
    assert (
        _run(cfg, FakeFetchers(heads=_heads_all()), no_ai=False,
             readme_generate=fake_readme_ai) == 0
    )
    assert counters["bio_gen"] == 1  # 首轮真生成（run2 的 0 才有对比意义）
    readme_mtime = (tmp_path / "README.md").stat().st_mtime_ns
    after_first = dict(counters)
    capsys.readouterr()

    # 第二轮：数据完全无变化（heads 与基线相同），仅勾选 repo_readmes
    fetchers = FakeFetchers(heads=_heads_all())
    assert (
        _run(cfg, fetchers, no_ai=False, b_force=True,
             readme_generate=fake_readme_ai) == 0
    )
    out = capsys.readouterr().out
    assert "无变化，跳过更新" not in out  # ① 勾选开门（早退=勾选成空头支票）
    assert "入队 3 仓" in out  # B 被强制执行（与 Q09 同款可观测：全量合成入队）
    assert fetchers.head_calls == PARTICIPANTS  # B 确实取了各仓 HEAD
    assert fetchers.compare_calls == []  # 强制走合成路径，零 compare
    assert (tmp_path / "README.md").stat().st_mtime_ns == readme_mtime  # ③ 不碰 A 的门
    assert counters == after_first  # ② bio 不再生成（数据未变，B 强制开门不影响 A）


def _checkbox_round_routes(http, issue_body):
    """两轮「勾选合并」场景共用路由：清单搜索 + 按号复读 + merge PUT +
    逐仓合并检测/open 扫描（sticky）+ 清单关闭。"""
    http.add(
        "GET",
        "https://api.github.com/repos/alice/alice/issues?state=open&per_page=100",
        FakeResponse(
            json_data=[{
                "number": 5,
                "title": "🔔 README 更新待合并清单（待合并 PR）",
                "html_url": "https://github.com/alice/alice/issues/5",
                "body": issue_body,
            }]
        ),
    )
    http.add(
        "GET",
        "https://api.github.com/repos/alice/alice/issues/5",
        FakeResponse(
            json_data={"number": 5,
                       "html_url": "https://github.com/alice/alice/issues/5",
                       "body": issue_body}
        ),
        sticky=True,
    )
    http.add(  # PUT 前的状态查询（2026-10-23 WP2 降噪）：仍 open → 照常 PUT
        "GET",
        "https://api.github.com/repos/alice/cool-tool/pulls/7",
        FakeResponse(json_data={"number": 7, "state": "open", "merged": False}),
        sticky=True,
    )
    http.add(
        "PUT",
        "https://api.github.com/repos/alice/cool-tool/pulls/7/merge",
        FakeResponse(json_data={"merged": True, "sha": "f" * 40, "message": "merged"}),
    )
    for fn in PARTICIPANTS:  # 合并检测：cool-tool 已合并 → 基线推进；其余无记录
        body = (
            [{"number": 7, "merged": True, "head": {"sha": "merged11"}}]
            if fn == "alice/cool-tool"
            else []
        )
        owner = fn.split("/")[0]
        http.add(
            "GET",
            f"https://api.github.com/repos/{fn}/pulls?state=all&head={owner}:readme-auto/update"
            "&base=main&sort=updated&direction=desc",
            FakeResponse(json_data=body),
            sticky=True,
        )
        http.add(  # 扫描：PR 已合并 → open 列表为空
            "GET",
            f"https://api.github.com/repos/{fn}/pulls?state=open&head={owner}:readme-auto/update&base=main",
            FakeResponse(json_data=[]),
            sticky=True,
        )
    http.add(  # 清单重建：无 open PR → 关闭 issue
        "PATCH",
        "https://api.github.com/repos/alice/alice/issues/5",
        FakeResponse(json_data={"number": 5}),
    )


def test_ticked_checkbox_opens_gate_and_merges_even_without_data_change(tmp_path, capsys, monkeypatch):
    """2026-10-23 勾选=人工授权：门全关（数据无变化、无 b_force）但清单有 - [x] →
    门必须开（merge_requested），开局即代为合并、同轮推进基线。

    钩子①原本在门早退之后——cron 轮里用户勾了也永不生效（设计验证实测缺口）；
    attempted 集合保证同一条勾选每轮至多一次 PUT（钩子①' 复读同 body 不再重复合并）。
    """
    from fake_github import FakeHttp, FakeResponse

    from profile_agent.github_client import GitHubClient

    _freeze_time(monkeypatch)
    cfg = _write_config(tmp_path)
    # 第一轮：建立真实指纹与三仓基线（fixture 离线，无 b_client → 无清单通道）
    assert _run(cfg, FakeFetchers(heads=_heads_all())) == 0
    assert _snapshot(tmp_path)["repo_readmes"]["alice/cool-tool"]["sha"] == "head0001"

    # 第二轮：数据完全无变化、不勾 b_force——唯一变量是清单里的一条 - [x]
    http = FakeHttp()
    ticked = (
        "## 🔔 README 更新待合并清单（更新于 2026-10-22 18:30，北京时间）\n"
        "@alice\n"
        "- [x] alice/cool-tool：[PR](https://github.com/alice/cool-tool/pull/7)"
        "（[新版 README 预览](https://github.com/alice/cool-tool/blob/readme-auto/update/README.md)）\n"
    )
    _checkbox_round_routes(http, ticked)
    fetchers = FakeFetchers(
        # 快进合并建模：cool-tool 的 PR head == 合并后默认分支 HEAD（merged11）
        heads={**_heads_all(), "alice/cool-tool": "merged11"},
    )  # advance 后 base==head → 零 compare，基线停在 merged11
    capsys.readouterr()
    assert (
        _run(
            cfg,
            fetchers,
            b_client=GitHubClient("p-pat", session=http),
            issue_client=GitHubClient("g-tok", session=http),
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "无变化，跳过更新" not in out  # ① 勾选开门（早退 = 勾选永不生效）
    assert "已按清单勾选合并（开局）" in out  # ② 开局即代为合并
    puts = [r for r in http.writes() if r["method"] == "PUT"]
    assert len(puts) == 1  # ③ attempted：同一勾选每轮至多一次 PUT
    assert _snapshot(tmp_path)["repo_readmes"]["alice/cool-tool"]["sha"] == "merged11"  # ④ 同轮基线推进
    closes = [
        r for r in http.writes() if r["method"] == "PATCH" and r["json"].get("state") == "closed"
    ]
    assert closes and "/pull/7" not in closes[0]["json"]["body"]  # ⑤ 清单重建无幻影勾选


def test_merge_all_master_ticked_opens_gate_and_merges(tmp_path, capsys, monkeypatch):
    """Q1（2026-10-23「一键全选」）：清单只勾「全部合并」总开关、逐条全是 [ ]——
    门必须开（merge_requested 认总开关），开局即合并总开关授权的条目。

    总开关无 PR 链接，逐条口径 parse_checked_prs 恒为空——若门或收割仍走逐条口径，
    本用例会因「无变化，跳过更新」早退而失败（一键全选成空头支票）。
    """
    from fake_github import FakeHttp

    from profile_agent.checklist import build_checklist_body
    from profile_agent.github_client import GitHubClient

    _freeze_time(monkeypatch)
    cfg = _write_config(tmp_path)
    assert _run(cfg, FakeFetchers(heads=_heads_all())) == 0

    body = build_checklist_body(
        [{
            "full_name": "alice/cool-tool",
            "html_url": "https://github.com/alice/cool-tool/pull/7",
            "branch": "readme-auto/update",
        }],
        username="alice",
        updated_at="2026-10-22 18:30",
    )
    master_ticked = body.replace("- [ ] 全部合并", "- [x] 全部合并")
    assert master_ticked != body  # 总开关确实勾上了
    assert master_ticked.count("- [x]") == 1  # 逐条一条都没勾（全靠总开关授权）

    http = FakeHttp()
    _checkbox_round_routes(http, master_ticked)
    fetchers = FakeFetchers(heads={**_heads_all(), "alice/cool-tool": "merged11"})
    capsys.readouterr()
    assert (
        _run(
            cfg,
            fetchers,
            b_client=GitHubClient("p-pat", session=http),
            issue_client=GitHubClient("g-tok", session=http),
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "无变化，跳过更新" not in out  # ① 总开关开门
    assert "已按清单勾选合并（开局）" in out  # ② 开局即合并
    puts = [r for r in http.writes() if r["method"] == "PUT"]
    assert len(puts) == 1  # attempted：总开关也至多一次 PUT


def test_q22_b_force_with_enabled_false_keeps_gate_closed(tmp_path, capsys):
    """Q22：enabled=false 时勾选 repo_readmes 只警告不运行——门不开（b_gate 含 enabled 判定），
    A 侧与纯 A 轮一致（早退不动 README）。"""
    cfg = _write_config(tmp_path, rr_enabled=False)
    assert _run(cfg, FakeFetchers(heads=_heads_all())) == 0  # 纯 A 轮建立指纹
    readme_mtime = (tmp_path / "README.md").stat().st_mtime_ns
    fetchers = FakeFetchers()  # 门若开、B 若跑，任何 fetcher 调用都会 AssertionError
    capsys.readouterr()
    assert _run(cfg, fetchers, b_force=True) == 0
    out = capsys.readouterr().out
    assert "已勾选 repo_readmes" in out and "enabled=false" in out  # 冲突警告照发（IW09 同文案）
    assert "无变化，跳过更新" in out  # 门不开：与纯 A 轮一致（开 A 门会重写 README）
    assert fetchers.head_calls == [] and fetchers.compare_calls == []
    assert (tmp_path / "README.md").stat().st_mtime_ns == readme_mtime


# ---- 生产事故回归（逐仓隔离 + 状态行诚实，不得用「无事可做」掩盖失败）----


def test_fetch_head_404_isolated_and_status_honest(tmp_path, capsys):
    """生产 404 事故回归：门开 + 某仓 fetch_head 404（PAT 不可见）→ 只跳过该仓、
    其余照常、整轮退出码 0；状态行写「读取失败」而非「无事可做」。"""
    from profile_agent.github_client import GitHubError

    cfg = _write_config(tmp_path)
    assert _run(cfg, FakeFetchers(heads=_heads_all())) == 0  # 首轮：三仓各记基线

    # pending 开门（模拟门开 + 一仓恰好不可见）
    path = tmp_path / "data" / "snapshot.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["repo_readmes"]["alice/old-thing"]["pending"] = True
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    class HeadErrFetchers(FakeFetchers):
        def fetch_head(self, full_name):
            self.head_calls.append(full_name)
            if full_name == "alice/cool-tool":
                raise GitHubError("GitHub API 返回 404：用户不存在或仓库不可见")
            return self.heads[full_name]

    fetchers = HeadErrFetchers(heads=_heads_all())  # 其余 base==head → 零 compare
    capsys.readouterr()
    assert _run(cfg, fetchers) == 0  # 整轮不受影响，退出码 0
    out = capsys.readouterr().out
    assert "模式 B 状态" in out and "读取失败" in out  # 失败可见
    assert "无事可做" not in out  # 不再被「无事可做」掩盖
    # 坏仓被跳过、基线不被强推；其余仓照常处理
    assert _snapshot(tmp_path)["repo_readmes"]["alice/cool-tool"]["sha"] == "head0001"
    assert fetchers.head_calls == PARTICIPANTS  # 坏仓抛错后仍继续查其余仓


def test_whole_b_crash_shows_failure_not_noop(tmp_path, monkeypatch, capsys):
    """整体（非逐仓）失败：_save_b_snapshot 落盘抛错 → 状态行写「整体失败」而非
    「无事可做」，且模式 A 不受影响、退出码 0。"""
    import profile_agent.cli as cli_mod

    cfg = _write_config(tmp_path)
    assert _run(cfg, FakeFetchers(heads=_heads_all())) == 0  # 首轮记基线
    path = tmp_path / "data" / "snapshot.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["repo_readmes"]["alice/cool-tool"]["pending"] = True
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def _boom(*a, **k):
        raise RuntimeError("snapshot 落盘炸了")

    monkeypatch.setattr(cli_mod, "_save_b_snapshot", _boom)
    capsys.readouterr()
    assert _run(cfg, FakeFetchers(heads=_heads_all())) == 0  # A 不受影响，退出码 0
    out = capsys.readouterr().out
    assert "模式 B 状态" in out and "整体失败" in out  # 失败被点名
    assert "无事可做" not in out
