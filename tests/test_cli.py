"""cli.py 离线端到端编排测试（用 fixture，不联网、不调 AI）。"""

import json
from pathlib import Path

import pytest

from profile_agent.cli import run

FIXTURES = Path(__file__).parent / "fixtures"
TEMPLATE = Path(__file__).parent.parent / "templates" / "profile.md.j2"


def _write_config(
    tmp_path: Path,
    username: str = "alice",
    *,
    mode: str | None = None,
    bilingual: bool | None = None,
) -> Path:
    cfg = tmp_path / "config.yaml"
    as_posix = lambda p: p.as_posix()  # noqa: E731
    mode_line = f'  mode: "{mode}"\n' if mode else ""
    bi_line = "" if bilingual is None else f"  bilingual: {'true' if bilingual else 'false'}\n"
    cfg.write_text(
        f"""
github:
  username: "{username}"
repos:
  exclude: ["alice/old-profile"]
readme:
  path: "{as_posix(tmp_path / 'README.md')}"
  template: "{as_posix(TEMPLATE)}"
{bi_line}{mode_line}snapshot:
  path: "{as_posix(tmp_path / 'data' / 'snapshot.json')}"
""",
        encoding="utf-8",
    )
    return cfg


def _run(cfg: Path, **kwargs) -> int:
    kwargs.setdefault("fixture", FIXTURES)
    kwargs.setdefault("no_ai", True)
    return run(cfg, **kwargs)


def test_dry_run_prints_readme_and_writes_nothing(tmp_path, capsys):
    cfg = _write_config(tmp_path)
    code = _run(cfg, dry_run=True)
    assert code == 0
    out = capsys.readouterr().out
    assert "统计信息" in out and "仓库列表" in out
    assert not (tmp_path / "README.md").exists()
    assert not (tmp_path / "data" / "snapshot.json").exists()
    # --dry-run 承诺「只打印不写文件」——项目介绍目录同样不得落盘（M4 走查回归）
    assert not (tmp_path / "docs" / "projects").exists()


def test_full_run_never_creates_docs_projects(tmp_path):
    """项目介绍功能已移除（A-P0 回归锁②）：完整运行不得产生 docs/projects（0 token 消耗）。"""
    cfg = _write_config(tmp_path)
    assert _run(cfg) == 0
    assert not (tmp_path / "docs" / "projects").exists()


def test_run_prints_usage_numbers(tmp_path, capsys):
    """每轮都要给出可核对数字：AI 请求次数、缓存复用、耗时（简历/成本分析的证据）。"""
    cfg = _write_config(tmp_path)
    assert _run(cfg) == 0
    out = capsys.readouterr().out
    assert "本轮 AI 用量：请求 0 次（失败 0）" in out  # no_ai → 0 次
    assert "耗时：" in out


def test_no_change_run_still_reports_usage(tmp_path, capsys):
    """「无变化，跳过更新」这条路也要有数字，否则省下的调用无法量化。"""
    cfg = _write_config(tmp_path)
    assert _run(cfg) == 0
    capsys.readouterr()
    assert _run(cfg) == 0
    out = capsys.readouterr().out
    assert "无变化，跳过更新" in out
    assert "本轮 AI 用量：" in out and "耗时：" in out


def test_full_run_writes_readme_and_snapshot(tmp_path):
    cfg = _write_config(tmp_path)
    assert _run(cfg) == 0
    readme = tmp_path / "README.md"
    snap = tmp_path / "data" / "snapshot.json"
    assert readme.exists() and "统计信息" in readme.read_text(encoding="utf-8")
    # 有意变更（WP6→A-WP2）：旧注释锁「面板常驻」——面板已彻底移除，未配置 pin 时不再出现任何 📌
    assert "## 📌" not in readme.read_text(encoding="utf-8")  # 未配置 pin 不出置顶板块
    data = json.loads(snap.read_text(encoding="utf-8"))
    assert data["username"] == "alice"
    assert data["stats_fingerprint"].startswith("sha256:")


def test_second_run_is_noop_when_unchanged(tmp_path):
    cfg = _write_config(tmp_path)
    _run(cfg)
    readme = tmp_path / "README.md"
    snap = tmp_path / "data" / "snapshot.json"
    readme_mtime = readme.stat().st_mtime_ns
    snap_mtime = snap.stat().st_mtime_ns
    assert _run(cfg) == 0
    # 无变化：文件时间戳不变（未重写）
    assert readme.stat().st_mtime_ns == readme_mtime
    assert snap.stat().st_mtime_ns == snap_mtime


def test_featured_pin_renders_pinned_section(tmp_path):
    """featured.pin 端到端（2026-10-23 置顶需求 + README 视觉改版）：置顶仓库渲染
    成 📌 置顶卡片，📦 仓库列表不重复它，板块顺序置顶在前。"""
    cfg = _write_config(tmp_path)
    with cfg.open("a", encoding="utf-8") as fh:
        fh.write('featured:\n  pin: ["alice/old-thing"]\n')
    assert _run(cfg) == 0
    readme = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "## 📌 置顶项目" in readme
    assert "### ⭐ [old-thing](https://github.com/alice/old-thing)" in readme
    assert "> 很久没动的项目" in readme  # 置顶卡片一句话摘要
    assert readme.index("## 📌 置顶项目") < readme.index("## 📦 仓库列表")
    assert "| [old-thing](" not in readme  # 列表不重复置顶仓库
    assert "| [cool-tool](" in readme  # 其余仓库仍在表格


# ---- A-WP2 一次性迁移：旧版置顶面板摘除 ----

# 旧版面板块（WP6 渲染形态：zh README 底部 <details> 折叠区 + pin-config 锚点）
_LEGACY_PANEL = (
    "<details>\n<summary><b>📌 置顶配置面板（网页直接勾选）</b></summary>\n\n"
    "<!-- pin-config:start -->\n- [x] alice/old-thing\n<!-- pin-config:end -->\n</details>\n\n"
)


def _simulate_legacy_run(tmp_path: Path, panel: str = _LEGACY_PANEL, extra: str = "") -> None:
    """把基线产物改写成「旧版最后一轮」形态：README/快照 base 都带面板、pins=旧勾选。"""
    readme = tmp_path / "README.md"
    snap = tmp_path / "data" / "snapshot.json"
    old_text = readme.read_text(encoding="utf-8").replace(
        "<!-- profile-agent:custom:start -->", panel + extra + "<!-- profile-agent:custom:start -->"
    )
    readme.write_text(old_text, encoding="utf-8")
    data = json.loads(snap.read_text(encoding="utf-8"))
    data["pins"] = ["alice/old-thing"]
    data["readme_last"] = old_text  # base = 旧版渲染输出（含面板）——真实升级形态
    snap.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_legacy_panel_stripped_once_then_noop(tmp_path, capsys):
    """A-WP2：盘上残留旧版面板 → 迁移轮整块摘除 + 中文警告；旧勾选沿用为置顶；下轮 noop。"""
    cfg = _write_config(tmp_path)
    assert _run(cfg) == 0
    _simulate_legacy_run(tmp_path)
    assert _run(cfg) == 0
    out = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "pin-config" not in out  # 锚点随面板整块消失
    assert "置顶配置面板" not in out
    err = capsys.readouterr().err
    assert "警告：检测到旧版置顶面板/指针（一次性迁移），本轮已清除" in err
    assert "## 📌 置顶项目" in out  # prev.pins 过渡：旧面板勾选沿用为置顶
    # 迁移自终止：下一轮无残留、指纹一致 → noop（mtime 不变）
    mtime = (tmp_path / "README.md").stat().st_mtime_ns
    assert _run(cfg) == 0
    assert (tmp_path / "README.md").stat().st_mtime_ns == mtime


def test_legacy_en_pointer_stripped(tmp_path):
    """A-WP2：en 文件残留旧指针文案 → 迁移轮同样摘除（zh/en 标记独立检测）。"""
    cfg = _write_config(tmp_path)
    assert _run(cfg) == 0
    en = tmp_path / "README.en.md"
    snap = tmp_path / "data" / "snapshot.json"
    pointer = (
        "> ⚙️ Pin configuration lives in the pin panel at the bottom of the Chinese README "
        "(tick, then Commit — it takes effect within seconds).\n"
    )
    old_en = en.read_text(encoding="utf-8").replace(
        "<!-- profile-agent:custom:start -->", pointer + "<!-- profile-agent:custom:start -->"
    )
    en.write_text(old_en, encoding="utf-8")
    # base 同步含指针（真实升级形态：旧版 en 渲染输出即带指针）
    data = json.loads(snap.read_text(encoding="utf-8"))
    data["readme_last_en"] = old_en
    snap.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert _run(cfg) == 0
    out = en.read_text(encoding="utf-8")
    assert "Pin configuration lives" not in out
    assert "pin panel" not in out


def test_user_edited_panel_lines_still_removed_by_migration(tmp_path):
    """A-WP2：用户手改过面板行也整块摘除（编辑与 ours 删除区间相撞被拦）；custom 手改保留。"""
    cfg = _write_config(tmp_path)
    assert _run(cfg) == 0
    # 基线+旧面板作为 base；盘上=用户又勾了一个仓库（手改面板行）
    _simulate_legacy_run(tmp_path)
    readme = tmp_path / "README.md"
    theirs = readme.read_text(encoding="utf-8").replace(
        "- [x] alice/old-thing", "- [x] alice/old-thing\n- [x] alice/cool-tool"
    )
    readme.write_text(theirs, encoding="utf-8")
    assert _run(cfg) == 0
    out = readme.read_text(encoding="utf-8")
    assert "pin-config" not in out and "置顶配置面板" not in out
    # 沿用 base 里的旧勾选（手改的新增勾选随面板一并丢弃，不产生半摘状态）
    assert "### ⭐ [old-thing](https://github.com/alice/old-thing)" in out


def test_force_regenerates_even_when_unchanged(tmp_path):
    cfg = _write_config(tmp_path)
    _run(cfg)
    snap = tmp_path / "data" / "snapshot.json"
    before = snap.read_text(encoding="utf-8")
    snap.write_text(before.replace('"schema": 1', '"schema": 1'), encoding="utf-8")
    # 篡改指纹，force 应覆盖回来
    tampered = json.loads(before)
    tampered["stats_fingerprint"] = "sha256:tampered"
    snap.write_text(json.dumps(tampered, ensure_ascii=False), encoding="utf-8")
    assert _run(cfg, force=True) == 0
    assert json.loads(snap.read_text(encoding="utf-8"))["stats_fingerprint"] != "sha256:tampered"


def test_missing_username_exits_nonzero(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text('github:\n  username: ""\n', encoding="utf-8")
    assert _run(cfg) != 0


# ---- 修复轮与方向二新增 ----


def _with_env(monkeypatch, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)


def test_env_username_fallback_zero_config(tmp_path, monkeypatch):
    """fork 即用：config 里 username 留空，靠 GITHUB_REPOSITORY_OWNER 兜底。"""
    cfg = tmp_path / "config.yaml"
    as_posix = lambda p: p.as_posix()
    cfg.write_text(
        f"""
github:
  username: ""
readme:
  path: "{as_posix(tmp_path / 'README.md')}"
  template: "{as_posix(TEMPLATE)}"
snapshot:
  path: "{as_posix(tmp_path / 'data' / 'snapshot.json')}"
""",
        encoding="utf-8",
    )
    _with_env(monkeypatch, GITHUB_REPOSITORY_OWNER="alice")
    assert run(cfg, fixture=FIXTURES, no_ai=True) == 0
    assert (tmp_path / "README.md").exists()


def _gen(monkeypatch, texts, calls):
    """替换 generate_bio：记录调用并按序返回 (zh, en, generated) 三元组（WP4）。"""
    import profile_agent.cli as cli_mod

    def fake(payload, cfg, **kwargs):
        calls.append(kwargs)
        return texts[len(calls) - 1]

    monkeypatch.setattr(cli_mod, "generate_bio", fake)


def test_ai_bio_generated_then_gate_skips(tmp_path, monkeypatch):
    cfg = _write_config(tmp_path)
    _with_env(monkeypatch, ANTHROPIC_API_KEY="test-key")
    calls = []
    # 有意变更（WP4）：fake 需带 en——双语下 en 缺失会触发下轮重生成，门关不严
    _gen(monkeypatch, [("初始简介", "Initial EN", True)], calls)
    assert run(cfg, fixture=FIXTURES) == 0
    assert len(calls) == 1
    assert "初始简介" in (tmp_path / "README.md").read_text(encoding="utf-8")
    # 第二次：简介新鲜 + 数据无变 → 增量门整体跳过，不再调用
    # （有意变更 A-P0：原「项目介绍 AI 也成功」前提随功能移除，门不再有 projects 子句）
    assert run(cfg, fixture=FIXTURES) == 0
    assert len(calls) == 1


def test_stale_bio_regenerates_even_when_data_unchanged(tmp_path, monkeypatch):
    cfg = _write_config(tmp_path)
    _with_env(monkeypatch, ANTHROPIC_API_KEY="test-key")
    calls = []
    _gen(monkeypatch, [("初始简介", "", True), ("刷新后的简介", "", True)], calls)
    run(cfg, fixture=FIXTURES)
    # 把简介时间改老（超过 max_age_days=14）
    snap_path = tmp_path / "data" / "snapshot.json"
    data = json.loads(snap_path.read_text(encoding="utf-8"))
    data["bio"]["generated_at"] = "2000-01-01T00:00:00+00:00"
    snap_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert run(cfg, fixture=FIXTURES) == 0
    assert len(calls) == 2  # 简介过期 → 重新生成
    assert "刷新后的简介" in (tmp_path / "README.md").read_text(encoding="utf-8")


def test_dropped_english_bio_is_not_retried_every_round(tmp_path, monkeypatch):
    """AI 双语返回里英文被丢弃：同一输入只试一次，门关上，不再每轮白跑一次 AI。"""
    cfg = _write_config(tmp_path)
    _with_env(monkeypatch, ANTHROPIC_API_KEY="test-key")
    calls = []
    _gen(monkeypatch, [("初始简介", "", True), ("刷新后的简介", "", True)], calls)
    assert run(cfg, fixture=FIXTURES) == 0
    assert len(calls) == 1
    snap_path = tmp_path / "data" / "snapshot.json"
    snap_bytes = snap_path.read_bytes()
    readme_mtime = (tmp_path / "README.md").stat().st_mtime_ns

    assert run(cfg, fixture=FIXTURES) == 0
    assert len(calls) == 1  # 同输入不再重试
    assert (tmp_path / "README.md").stat().st_mtime_ns == readme_mtime
    assert snap_path.read_bytes() == snap_bytes  # 也不再产生「只有快照变化」的提交

    # 简介过期后才重新尝试英文（数据变化 / 改 prompt_version 同理）
    data = json.loads(snap_path.read_text(encoding="utf-8"))
    data["bio"]["generated_at"] = "2000-01-01T00:00:00+00:00"
    snap_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert run(cfg, fixture=FIXTURES) == 0
    assert len(calls) == 2


def test_config_change_invalidates_gate(tmp_path, monkeypatch):
    cfg = _write_config(tmp_path)
    run(cfg, fixture=FIXTURES)
    # 改主题配置（显式写入 cards 段）→ 增量门必须失效并重渲染
    text = cfg.read_text(encoding="utf-8").replace(
        'readme:\n  path:',
        'readme:\n  cards:\n    theme: "github_light"\n  path:',
    )
    cfg.write_text(text, encoding="utf-8")
    assert run(cfg, fixture=FIXTURES) == 0
    assert "github_light" in (tmp_path / "README.md").read_text(encoding="utf-8")


def test_missing_readme_regenerated(tmp_path):
    cfg = _write_config(tmp_path)
    run(cfg, fixture=FIXTURES)
    (tmp_path / "README.md").unlink()
    assert run(cfg, fixture=FIXTURES) == 0
    assert (tmp_path / "README.md").exists()


def test_ai_failure_with_no_previous_bio_returns_zero(tmp_path, monkeypatch):
    import profile_agent.cli as cli_mod
    from profile_agent.bio import BioError

    cfg = _write_config(tmp_path)
    _with_env(monkeypatch, ANTHROPIC_API_KEY="bad-key")
    monkeypatch.setattr(
        cli_mod, "generate_bio", lambda *a, **k: (_ for _ in ()).throw(BioError("boom"))
    )
    assert run(cfg, fixture=FIXTURES) == 0  # 任务不失败
    readme = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "AI 简介暂不可用" in readme  # 占位注释


def test_force_bypasses_gate(tmp_path, capsys):
    cfg = _write_config(tmp_path)
    run(cfg, fixture=FIXTURES)
    capsys.readouterr()
    assert run(cfg, fixture=FIXTURES, force=True) == 0
    out = capsys.readouterr().out
    assert "无变化，跳过更新" not in out  # 增量门没有拦截（明细表里的逐仓库「跳过」是另一回事）


def test_skip_readme_keeps_readme_untouched(tmp_path):
    """skip_readme（独立功能）：README 文件不动，快照等其余流程照常。"""
    cfg = _write_config(tmp_path)
    run(cfg, fixture=FIXTURES)
    readme = tmp_path / "README.md"
    mtime = readme.stat().st_mtime_ns
    assert run(cfg, fixture=FIXTURES, skip_readme=True, force=True) == 0
    assert readme.stat().st_mtime_ns == mtime  # README 未被重写
    # 有意变更（A-P0）：原「介绍文件正常生成」断言随项目介绍功能移除删除
    assert not (tmp_path / "docs" / "projects").exists()


# ---- 描述补位 + 逐仓库精细化 ----


def test_short_intro_skips_profile_repo(tmp_path, monkeypatch):
    """补位文案同样跳过 profile 仓库自身（否则它的 README 每变一次就重生成一次）。"""
    import profile_agent.cli as cli_mod

    cfg_path = tmp_path / "config.yaml"
    as_posix = lambda p: p.as_posix()
    cfg_path.write_text(
        f"""
github:
  username: "alice"
readme:
  path: "{as_posix(tmp_path / 'README.md')}"
  template: "{as_posix(TEMPLATE)}"
snapshot:
  path: "{as_posix(tmp_path / 'data' / 'snapshot.json')}"
""",
        encoding="utf-8",
    )
    _with_env(monkeypatch, ANTHROPIC_API_KEY="test-key")
    monkeypatch.setattr(cli_mod, "generate_bio", lambda *a, **k: ("简介", "", True))
    calls = []

    def fake(repo, cfg_ai, **kwargs):
        calls.append(repo.full_name)
        return "一个命令行密码生成器。", ""

    monkeypatch.setattr(cli_mod, "generate_short_intro", fake)
    fx = _empty_desc_fixture(tmp_path)
    # 在 fixture 里追加 profile 仓库自身（description 同样为空）
    repos = json.loads((fx / "repos.json").read_text(encoding="utf-8"))
    repos.append(
        {
            "name": "alice",
            "full_name": "alice/alice",
            "description": None,
            "language": "Python",
            "stargazers_count": 0,
            "forks_count": 0,
            "private": False,
            "fork": False,
            "archived": False,
            "pushed_at": "2026-10-01T00:00:00Z",
            "html_url": "https://github.com/alice/alice",
            "topics": [],
        }
    )
    (fx / "repos.json").write_text(json.dumps(repos), encoding="utf-8")

    assert run(cfg_path, fixture=fx) == 0
    assert calls == ["alice/mystery"], calls  # alice/alice 不得出现


def _empty_desc_fixture(tmp_path: Path) -> Path:
    """造一个 description 为空的精选仓库 fixture。"""
    fx = tmp_path / "fx"
    fx.mkdir()
    (fx / "user.json").write_text(
        json.dumps({"login": "alice", "name": "Alice", "public_repos": 1, "followers": 0}),
        encoding="utf-8",
    )
    (fx / "repos.json").write_text(
        json.dumps(
            [
                {
                    "name": "mystery",
                    "full_name": "alice/mystery",
                    "description": None,
                    "language": "Python",
                    "stargazers_count": 3,
                    "forks_count": 0,
                    "private": False,
                    "fork": False,
                    "archived": False,
                    "pushed_at": "2026-10-01T00:00:00Z",
                    "html_url": "https://github.com/alice/mystery",
                    "topics": [],
                }
            ]
        ),
        encoding="utf-8",
    )
    return fx


def test_short_intro_generated_for_empty_description_then_reused(tmp_path, monkeypatch):
    """description 为空的精选仓库：AI 生成补位文案，第二次运行复用不重调。"""
    import profile_agent.cli as cli_mod

    cfg_path = tmp_path / "config.yaml"
    as_posix = lambda p: p.as_posix()
    cfg_path.write_text(
        f"""
github:
  username: "alice"
readme:
  path: "{as_posix(tmp_path / 'README.md')}"
  template: "{as_posix(TEMPLATE)}"
snapshot:
  path: "{as_posix(tmp_path / 'data' / 'snapshot.json')}"
""",
        encoding="utf-8",
    )
    _with_env(monkeypatch, ANTHROPIC_API_KEY="test-key")
    monkeypatch.setattr(cli_mod, "generate_bio", lambda *a, **k: ("简介", "", True))
    calls = []

    def fake(repo, cfg_ai, **kwargs):
        calls.append(repo.full_name)
        # 有意变更（WP4）：fake 需带 en——双语下 en 缺失会触发下轮重生成，复用断言碎
        return "一个命令行密码生成器。", "A CLI password generator."

    monkeypatch.setattr(cli_mod, "generate_short_intro", fake)
    fx = _empty_desc_fixture(tmp_path)

    assert run(cfg_path, fixture=fx) == 0
    readme = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "一个命令行密码生成器" in readme
    assert "（暂无描述）" not in readme
    assert calls == ["alice/mystery"]
    # 第二次：指纹命中 → 整体增量门跳过（含补位文案），不再调用
    assert run(cfg_path, fixture=fx) == 0
    assert calls == ["alice/mystery"]


def test_short_intro_disabled_by_config(tmp_path, monkeypatch):
    import profile_agent.cli as cli_mod

    cfg_path = tmp_path / "config.yaml"
    as_posix = lambda p: p.as_posix()
    cfg_path.write_text(
        f"""
github:
  username: "alice"
ai:
  fill_empty_descriptions: false
readme:
  path: "{as_posix(tmp_path / 'README.md')}"
  template: "{as_posix(TEMPLATE)}"
snapshot:
  path: "{as_posix(tmp_path / 'data' / 'snapshot.json')}"
""",
        encoding="utf-8",
    )
    _with_env(monkeypatch, ANTHROPIC_API_KEY="test-key")
    monkeypatch.setattr(cli_mod, "generate_bio", lambda *a, **k: ("简介", "", True))
    calls = []
    monkeypatch.setattr(
        cli_mod,
        "generate_short_intro",
        lambda *a, **k: (calls.append(1) or "不该被调用", ""),
    )
    fx = _empty_desc_fixture(tmp_path)
    assert run(cfg_path, fixture=fx) == 0
    assert calls == []
    # 无补位 → 表格 — 占位（「暂无描述」废话已废弃）
    assert "| — |" in (tmp_path / "README.md").read_text(encoding="utf-8")


def test_run_writes_step_summary_table(tmp_path, monkeypatch):
    """Actions 的 GITHUB_STEP_SUMMARY 存在时，写出逐仓库动作明细表。

    有意变更（A-P0）：项目介绍列随功能移除，明细表只剩「主页描述补位」单列；
    表只在有补位目标（存在空描述仓库 + AI 可用）时出现，故用空描述 fixture + AI 挡板。
    """
    import profile_agent.cli as cli_mod

    cfg_path = tmp_path / "config.yaml"
    as_posix = lambda p: p.as_posix()
    cfg_path.write_text(
        f"""
github:
  username: "alice"
repos:
  exclude: ["alice/old-profile"]
readme:
  path: "{as_posix(tmp_path / 'README.md')}"
  template: "{as_posix(TEMPLATE)}"
snapshot:
  path: "{as_posix(tmp_path / 'data' / 'snapshot.json')}"
""",
        encoding="utf-8",
    )
    summary = tmp_path / "step_summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    _with_env(monkeypatch, ANTHROPIC_API_KEY="test-key")
    monkeypatch.setattr(cli_mod, "generate_bio", lambda *a, **k: ("简介", "", True))
    monkeypatch.setattr(cli_mod, "generate_short_intro", lambda *a, **k: ("密码生成器。", ""))
    fx = _empty_desc_fixture(tmp_path)
    assert run(cfg_path, fixture=fx) == 0
    text = summary.read_text(encoding="utf-8")
    assert "主页描述补位" in text
    assert "mystery" in text
    assert "已生成补位文案" in text


def test_snapshot_repos_carry_pushed_at(tmp_path):
    cfg = _write_config(tmp_path)
    assert _run(cfg) == 0
    data = json.loads((tmp_path / "data" / "snapshot.json").read_text(encoding="utf-8"))
    by_name = {r["full_name"]: r for r in data["repos"]}
    # 供勾选框标签显示「最近推送日期」用
    assert by_name["alice/cool-tool"]["pushed_at"].startswith("2026-10-01")


# ============================ WP3 双语 README（2026-10-23） ============================


def test_full_run_writes_both_readme_files(tmp_path):
    """bilingual 默认开：一次运行同时写 README.md（中文）与 README.en.md（英文），
    两文件顶部互链切换。"""
    cfg = _write_config(tmp_path)
    assert _run(cfg) == 0
    zh = (tmp_path / "README.md").read_text(encoding="utf-8")
    en = (tmp_path / "README.en.md").read_text(encoding="utf-8")
    assert "统计信息" in zh and "Statistics" in en
    switch = "🇨🇳 [中文](./README.md) · 🇺🇸 [English](./README.en.md)"
    assert switch in zh and switch in en


def test_bilingual_false_writes_only_readme_md(tmp_path):
    """bilingual: false 一键回退单文件：只产 README.md，不产 en（迁移护栏）。"""
    as_posix = lambda p: p.as_posix()  # noqa: E731
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        f"""
github:
  username: "alice"
repos:
  exclude: ["alice/old-profile"]
readme:
  path: "{as_posix(tmp_path / 'README.md')}"
  template: "{as_posix(TEMPLATE)}"
  bilingual: false
snapshot:
  path: "{as_posix(tmp_path / 'data' / 'snapshot.json')}"
""",
        encoding="utf-8",
    )
    assert _run(cfg) == 0
    assert (tmp_path / "README.md").exists()
    assert not (tmp_path / "README.en.md").exists()


def test_deleted_en_readme_regenerated_by_gate(tmp_path):
    """en 文件被手删 → 增量门视为「readme 缺失」开门，重渲染时重建 README.en.md。"""
    cfg = _write_config(tmp_path)
    assert _run(cfg) == 0
    (tmp_path / "README.en.md").unlink()
    assert _run(cfg) == 0  # 门不得早退
    assert (tmp_path / "README.en.md").exists()


def test_dry_run_prints_both_languages(tmp_path, capsys):
    """--dry-run 双语分段打印（zh 段 + en 段），不落盘。"""
    cfg = _write_config(tmp_path)
    assert _run(cfg, dry_run=True) == 0
    out = capsys.readouterr().out
    assert "统计信息" in out and "Statistics" in out
    assert not (tmp_path / "README.md").exists()
    assert not (tmp_path / "README.en.md").exists()


def test_second_run_is_noop_with_bilingual(tmp_path):
    """双语幂等：连跑两遍，第二遍 zh/en 文件时间戳均不变（零写入）。"""
    cfg = _write_config(tmp_path)
    assert _run(cfg) == 0
    zh, en = tmp_path / "README.md", tmp_path / "README.en.md"
    zh_mt, en_mt = zh.stat().st_mtime_ns, en.stat().st_mtime_ns
    assert _run(cfg) == 0
    assert zh.stat().st_mtime_ns == zh_mt
    assert en.stat().st_mtime_ns == en_mt


def test_bio_fresh_requires_en_when_bilingual(tmp_path, monkeypatch):
    """双语下新鲜判据补查 text_en：zh 新鲜但 en 缺失 → 必须重新生成（防单语卡死）。"""
    cfg = _write_config(tmp_path)
    _with_env(monkeypatch, ANTHROPIC_API_KEY="test-key")
    calls = []
    _gen(monkeypatch, [("第一版", "First EN", True), ("第二版", "Second EN", True)], calls)
    assert run(cfg, fixture=FIXTURES) == 0
    snap_path = tmp_path / "data" / "snapshot.json"
    data = json.loads(snap_path.read_text(encoding="utf-8"))
    assert data["bio"]["text"] == "第一版"
    assert data["bio"]["text_en"] == "First EN"
    # 清掉 en：指纹/时间仍新鲜，但双语下不完整 → 门必须开、重新生成
    data["bio"]["text_en"] = ""
    snap_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert run(cfg, fixture=FIXTURES) == 0
    assert len(calls) == 2
    data = json.loads(snap_path.read_text(encoding="utf-8"))
    assert data["bio"]["text"] == "第二版"
    assert data["bio"]["text_en"] == "Second EN"


def test_short_intro_clean_requires_en_when_bilingual(tmp_path, monkeypatch):
    """双语下补位复用判据补查 short_intro_en：en 缺失时不得复用 zh-only 旧文案。"""
    import profile_agent.cli as cli_mod

    cfg_path = tmp_path / "config.yaml"
    as_posix = lambda p: p.as_posix()
    cfg_path.write_text(
        f"""
github:
  username: "alice"
readme:
  path: "{as_posix(tmp_path / 'README.md')}"
  template: "{as_posix(TEMPLATE)}"
snapshot:
  path: "{as_posix(tmp_path / 'data' / 'snapshot.json')}"
""",
        encoding="utf-8",
    )
    _with_env(monkeypatch, ANTHROPIC_API_KEY="test-key")
    monkeypatch.setattr(cli_mod, "generate_bio", lambda *a, **k: ("简介", "Bio EN", True))
    calls = []

    def fake(repo, cfg_ai, **kwargs):
        calls.append(repo.full_name)
        return "一个命令行密码生成器。", "A CLI password generator."

    monkeypatch.setattr(cli_mod, "generate_short_intro", fake)
    fx = _empty_desc_fixture(tmp_path)

    assert run(cfg_path, fixture=fx) == 0
    assert "A CLI password generator." in (tmp_path / "README.en.md").read_text(encoding="utf-8")
    assert calls == ["alice/mystery"]
    # 清掉 short_intro_en：hash 仍匹配 → 复用判据必须因缺 en 而拒绝，重新生成
    snap_path = tmp_path / "data" / "snapshot.json"
    data = json.loads(snap_path.read_text(encoding="utf-8"))
    for repo in data["repos"]:
        repo.pop("short_intro_en", None)
    snap_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert run(cfg_path, fixture=fx) == 0
    assert calls == ["alice/mystery", "alice/mystery"]


# ============================ A-WP-B：英文版仓库描述自动英译 ============================


def _desc_translation_env(monkeypatch, translate_fake=None):
    """AI 环境 + bio 挡板 + 可注入的 translate_descriptions fake；返回调用批次记录。"""
    import profile_agent.cli as cli_mod

    _with_env(monkeypatch, ANTHROPIC_API_KEY="test-key")
    monkeypatch.setattr(cli_mod, "generate_bio", lambda *a, **k: ("简介", "", True))
    calls = []

    def default_fake(items, cfg_ai, **kwargs):
        calls.append(list(items))
        return {fn: "A cool tool." for fn, _ in items}, ""

    monkeypatch.setattr(cli_mod, "translate_descriptions", translate_fake or default_fake)
    return calls


def _copy_fixture(tmp_path):
    """拷贝基线 fixture 到临时目录（可改 repos.json 后当 fixture 用）。"""
    fx = tmp_path / "fixture"
    fx.mkdir(exist_ok=True)
    for name in ("user.json", "repos.json"):
        (fx / name).write_text(
            (FIXTURES / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    return fx


def test_en_repo_descriptions_translated_and_cached(tmp_path, monkeypatch):
    """A-WP-B：含中文描述的仓库 en 出英译、zh 逐字原文；缓存落盘 desc_en + desc_en_src。"""
    cfg = _write_config(tmp_path)
    calls = _desc_translation_env(monkeypatch)
    assert run(cfg, fixture=FIXTURES) == 0
    zh = (tmp_path / "README.md").read_text(encoding="utf-8")
    en = (tmp_path / "README.en.md").read_text(encoding="utf-8")
    assert "| [cool-tool](https://github.com/alice/cool-tool) | A cool tool. |" in en
    assert "一个很酷的工具" in zh  # zh 逐字原文（display_descs 恒空）
    assert "一个很酷的工具" not in en  # 英译命中后中文不再进 en
    assert calls and ("alice/cool-tool", "一个很酷的工具") in calls[0]
    data = json.loads((tmp_path / "data" / "snapshot.json").read_text(encoding="utf-8"))
    entry = next(r for r in data["repos"] if r["full_name"] == "alice/cool-tool")
    assert entry["desc_en"] == "A cool tool."
    assert entry["desc_en_src"] == "一个很酷的工具"


def test_translation_cached_on_force_rerun_zero_ai(tmp_path, monkeypatch):
    """A-WP-B：描述没变时 force 重渲染 0 次翻译调用（缓存仍喂 en 渲染）。"""
    cfg = _write_config(tmp_path)
    calls = _desc_translation_env(monkeypatch)
    assert run(cfg, fixture=FIXTURES) == 0
    assert len(calls) == 1
    assert run(cfg, fixture=FIXTURES, force=True) == 0
    assert len(calls) == 1  # 缓存新鲜（desc_en_src == description）→ 不重调
    en = (tmp_path / "README.en.md").read_text(encoding="utf-8")
    assert "A cool tool." in en


def test_translation_ai_failure_falls_back_to_original(tmp_path, monkeypatch):
    """A-WP-B：翻译失败 → en 回落中文原文、退出码 0、不写缓存（下轮开门重试）。"""
    def fail(items, cfg_ai, **kwargs):
        return {}, "仓库描述英译失败（沿用原文）：api down"

    cfg = _write_config(tmp_path)
    _desc_translation_env(monkeypatch, fail)
    assert run(cfg, fixture=FIXTURES) == 0
    en = (tmp_path / "README.en.md").read_text(encoding="utf-8")
    assert "一个很酷的工具" in en  # 回落原文
    data = json.loads((tmp_path / "data" / "snapshot.json").read_text(encoding="utf-8"))
    entry = next(r for r in data["repos"] if r["full_name"] == "alice/cool-tool")
    assert "desc_en" not in entry  # 失败不写缓存


def test_no_translation_when_bilingual_false(tmp_path, monkeypatch):
    """A-WP-B：单语模式（bilingual: false）不发起翻译、不产 en 文件。"""
    as_posix = lambda p: p.as_posix()  # noqa: E731
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        f"""
github:
  username: "alice"
repos:
  exclude: ["alice/old-profile"]
readme:
  path: "{as_posix(tmp_path / 'README.md')}"
  template: "{as_posix(TEMPLATE)}"
  bilingual: false
snapshot:
  path: "{as_posix(tmp_path / 'data' / 'snapshot.json')}"
""",
        encoding="utf-8",
    )
    calls = _desc_translation_env(monkeypatch)
    assert run(cfg, fixture=FIXTURES) == 0
    assert calls == []  # 单语模式零翻译
    assert not (tmp_path / "README.en.md").exists()


def test_ascii_description_becomes_chinese_in_zh_readme(tmp_path, monkeypatch):
    """2026-10-24 双语描述：纯 ASCII 描述不进英文翻译批（原本就是英文），但要进
    中文翻译批——中文 README 呈现中文描述，英文 README 逐字原文。

    有意变更：旧断言「纯 ASCII 不译」只针对 en 文件成立；现在 zh/en 各自看语言，
    zh 侧必须把非中文描述中译（用户要求：切到哪版就读哪版的语言）。
    缓存落盘 desc_zh + desc_zh_src，描述不变不重译。
    """
    import profile_agent.cli as cli_mod

    cfg = _write_config(tmp_path)
    fx = _copy_fixture(tmp_path)
    repos = json.loads((fx / "repos.json").read_text(encoding="utf-8"))
    for r in repos:
        if r["full_name"] == "alice/cool-tool":
            r["description"] = "A cool CLI tool"
    (fx / "repos.json").write_text(json.dumps(repos, ensure_ascii=False), encoding="utf-8")
    calls = []

    def fake(items, cfg_ai, *, target="en", **kwargs):
        calls.append((target, list(items)))
        return {fn: (f"中译：{d}" if target == "zh" else f"T:{d}") for fn, d in items}, ""

    _with_env(monkeypatch, ANTHROPIC_API_KEY="test-key")
    monkeypatch.setattr(cli_mod, "generate_bio", lambda *a, **k: ("简介", "", True))
    monkeypatch.setattr(cli_mod, "translate_descriptions", fake)

    assert run(cfg, fixture=fx) == 0
    en_batch = [fn for t, batch in calls if t == "en" for fn, _ in batch]
    zh_batch = [fn for t, batch in calls if t == "zh" for fn, _ in batch]
    assert "alice/cool-tool" not in en_batch  # 原本就是英文，不英译
    assert "alice/cool-tool" in zh_batch  # 中译目标
    zh = (tmp_path / "README.md").read_text(encoding="utf-8")
    en = (tmp_path / "README.en.md").read_text(encoding="utf-8")
    assert "中译：A cool CLI tool" in zh
    assert "A cool CLI tool" in en  # en 照搬原文
    data = json.loads((tmp_path / "data" / "snapshot.json").read_text(encoding="utf-8"))
    entry = next(r for r in data["repos"] if r["full_name"] == "alice/cool-tool")
    assert entry["desc_zh"] == "中译：A cool CLI tool"
    assert entry["desc_zh_src"] == "A cool CLI tool"
    # 第二轮 force：描述没变 → 缓存命中，0 次新翻译调用
    calls.clear()
    assert run(cfg, fixture=fx, force=True) == 0
    assert calls == []


def test_description_change_triggers_retranslate(tmp_path, monkeypatch):
    """A-WP-B：描述原文变化（desc_en_src 失效）→ 本轮开门重译并覆盖缓存。"""
    import profile_agent.cli as cli_mod

    cfg = _write_config(tmp_path)
    fx = _copy_fixture(tmp_path)
    calls = []

    def fake(items, cfg_ai, **kwargs):
        calls.append(list(items))
        return {fn: f"T:{desc}" for fn, desc in items}, ""

    monkeypatch.setattr(cli_mod, "translate_descriptions", fake)
    _with_env(monkeypatch, ANTHROPIC_API_KEY="test-key")
    monkeypatch.setattr(cli_mod, "generate_bio", lambda *a, **k: ("简介", "", True))
    assert run(cfg, fixture=fx) == 0
    assert len(calls) == 1
    assert "T:一个很酷的工具" in (tmp_path / "README.en.md").read_text(encoding="utf-8")
    # 改 fixture 里 cool-tool 的描述 → 指纹开门 + 缓存失效 → 重译
    repos = json.loads((fx / "repos.json").read_text(encoding="utf-8"))
    for r in repos:
        if r["full_name"] == "alice/cool-tool":
            r["description"] = "一个很酷的新工具"
    (fx / "repos.json").write_text(json.dumps(repos, ensure_ascii=False), encoding="utf-8")
    assert run(cfg, fixture=fx) == 0
    assert len(calls) == 2
    en = (tmp_path / "README.en.md").read_text(encoding="utf-8")
    assert "T:一个很酷的新工具" in en
    assert "T:一个很酷的工具" not in en  # 旧译文被覆盖
    data = json.loads((tmp_path / "data" / "snapshot.json").read_text(encoding="utf-8"))
    entry = next(r for r in data["repos"] if r["full_name"] == "alice/cool-tool")
    assert entry["desc_en_src"] == "一个很酷的新工具"


def test_no_translation_without_ai_key(tmp_path, monkeypatch):
    """A-WP-B：缺 AI key（降级运行）→ 不翻译，en 回落中文原文（预期降级）。"""
    cfg = _write_config(tmp_path)
    calls = _desc_translation_env(monkeypatch)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert run(cfg, fixture=FIXTURES, no_ai=True) == 0
    assert calls == []
    en = (tmp_path / "README.en.md").read_text(encoding="utf-8")
    assert "一个很酷的工具" in en


def test_custom_region_language_mismatch_warns_without_rewriting(tmp_path, monkeypatch, capsys):
    """手写区语言不匹配：只写警告（日志 + Step Summary），绝不翻译或改写原文。"""
    cfg = _write_config(tmp_path)
    (tmp_path / "README.md").write_text(
        "# My Profile\nI build developer tools and write about them in English.\n",
        encoding="utf-8",
    )
    (tmp_path / "README.en.md").write_text(
        "# 我的主页\n这是我手写的中文内容，本应出现在英文文件里却写成了中文。\n",
        encoding="utf-8",
    )
    summary = tmp_path / "step_summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

    assert _run(cfg) == 0

    err = capsys.readouterr().err
    assert "README.md 的手写内容看起来是英文" in err
    assert "README.en.md 的手写内容看起来是中文" in err
    text = summary.read_text(encoding="utf-8")
    assert "看起来是英文" in text and "看起来是中文" in text
    # 只提醒：手写内容逐字保留（不翻译、不改写）
    assert "I build developer tools and write about them in English." in (
        tmp_path / "README.md"
    ).read_text(encoding="utf-8")
    assert "这是我手写的中文内容" in (tmp_path / "README.en.md").read_text(encoding="utf-8")


# ============================ 仓库描述写回（2026-10-25） ============================


def _desc_cfg(
    tmp_path: Path, *, enabled: bool = True, mode: str | None = None
) -> Path:
    """repo_descriptions 场景配置：单语、无翻译干扰，只考察描述写回链路。"""
    cfg = tmp_path / "config.yaml"
    as_posix = lambda p: p.as_posix()  # noqa: E731
    mode_line = f'  mode: "{mode}"\n' if mode else ""
    cfg.write_text(
        f"""
github:
  username: "alice"
repos:
  exclude: ["alice/old-profile"]
readme:
  path: "{as_posix(tmp_path / 'README.md')}"
  template: "{as_posix(TEMPLATE)}"
  bilingual: false
{mode_line}repo_descriptions:
  enabled: {"true" if enabled else "false"}
snapshot:
  path: "{as_posix(tmp_path / 'data' / 'snapshot.json')}"
""",
        encoding="utf-8",
    )
    return cfg


def _desc_client():
    from fake_github import FakeHttp
    from profile_agent.github_client import GitHubClient

    http = FakeHttp()
    return http, GitHubClient("pat", session=http, sleep=lambda _s: None)


def _bump_fixture(fx: Path, full_name: str, *, pushed: str, description: str | None = None):
    path = fx / "repos.json"
    repos = json.loads(path.read_text(encoding="utf-8"))
    for r in repos:
        if r["full_name"] == full_name:
            r["pushed_at"] = pushed
            if description is not None:
                r["description"] = description
    path.write_text(json.dumps(repos, ensure_ascii=False), encoding="utf-8")


def test_repo_descriptions_disabled_by_default_never_patches(tmp_path, monkeypatch):
    import profile_agent.cli as cli_mod

    cfg = _desc_cfg(tmp_path, enabled=False)
    http, client = _desc_client()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(cli_mod, "generate_bio", lambda *a, **k: ("简介", "", True))
    monkeypatch.setattr(
        cli_mod,
        "generate_descriptions",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("关闭时不得调 AI")),
    )
    assert run(cfg, fixture=_copy_fixture(tmp_path), b_client=client) == 0
    assert http.writes() == []


def test_repo_descriptions_enabling_records_baseline_once_then_noop(
    tmp_path, monkeypatch, capsys
):
    """升级路径：先按旧配置跑一轮，再开启开关——只记基线、零 PATCH、零 AI；
    基线记完后下一轮门关闭，不会因为开关本身反复触发。"""
    import profile_agent.cli as cli_mod

    fx = _copy_fixture(tmp_path)
    http, client = _desc_client()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(cli_mod, "generate_bio", lambda *a, **k: ("简介", "", True))
    monkeypatch.setattr(
        cli_mod,
        "generate_descriptions",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("首次只记基线")),
    )
    disabled = _desc_cfg(tmp_path, enabled=False)
    assert run(disabled, fixture=fx, b_client=client) == 0
    assert http.writes() == []

    enabled = _desc_cfg(tmp_path, enabled=True)
    assert run(enabled, fixture=fx, b_client=client) == 0
    assert http.writes() == []
    out = capsys.readouterr().out
    assert "基线" in out
    data = json.loads((tmp_path / "data" / "snapshot.json").read_text(encoding="utf-8"))
    entry = data["repo_descriptions"]["alice/cool-tool"]
    assert entry["baseline"] == "一个很酷的工具"
    assert entry["written"] == "一个很酷的工具"
    assert entry["manual"] is False
    assert "alice/alice" not in data["repo_descriptions"]  # profile 自身排除

    assert run(enabled, fixture=fx, b_client=client) == 0
    assert "无变化，跳过更新" in capsys.readouterr().out
    assert http.writes() == []


def test_repo_descriptions_push_updates_github_readme_and_snapshot(
    tmp_path, monkeypatch, capsys
):
    """pushed_at 变了 → AI 生成新描述 → PATCH → 同轮渲染用新描述 + 快照存响应值。"""
    import profile_agent.cli as cli_mod

    from fake_github import FakeResponse
    from profile_agent.github_client import API

    fx = _copy_fixture(tmp_path)
    http, client = _desc_client()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(cli_mod, "generate_bio", lambda *a, **k: ("简介", "", True))
    cfg = _desc_cfg(tmp_path)
    summary = tmp_path / "step_summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert run(cfg, fixture=fx, b_client=client) == 0  # 记基线

    _bump_fixture(
        fx, "alice/cool-tool", pushed="2026-10-24T00:00:00Z", description="一个很酷的工具"
    )
    seen = []

    def fake_generate(items, cfg_ai, **kwargs):
        seen.extend(items)
        return {"alice/cool-tool": "会把 CSV 一键转成表格的命令行工具"}, ""

    monkeypatch.setattr(cli_mod, "generate_descriptions", fake_generate)
    http.add(
        "PATCH",
        f"{API}/repos/alice/cool-tool",
        FakeResponse(200, {"description": "会把 CSV 一键转成表格的命令行工具"}),
    )
    assert run(cfg, fixture=fx, b_client=client) == 0

    assert [i["full_name"] for i in seen] == ["alice/cool-tool"]
    assert seen[0]["target"] == "zh"  # 描述原本是中文
    assert [r["method"] for r in http.writes()] == ["PATCH"]
    assert http.requests[-1]["json"] == {"description": "会把 CSV 一键转成表格的命令行工具"}
    readme = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "会把 CSV 一键转成表格的命令行工具" in readme  # 同轮渲染就用新描述
    data = json.loads((tmp_path / "data" / "snapshot.json").read_text(encoding="utf-8"))
    entry = data["repo_descriptions"]["alice/cool-tool"]
    assert entry["written"] == "会把 CSV 一键转成表格的命令行工具"
    assert entry["previous"] == "一个很酷的工具"
    assert entry["pushed_at"] == "2026-10-24T00:00:00+00:00"
    text = summary.read_text(encoding="utf-8")
    assert "仓库描述" in text and "已更新" in text
    assert "描述已更新" in capsys.readouterr().out


def test_repo_descriptions_manual_edit_is_never_overwritten(tmp_path, monkeypatch, capsys):
    """用户手改描述后永久交还控制权：不 PATCH、不调 AI，并明确提示。"""
    import profile_agent.cli as cli_mod

    fx = _copy_fixture(tmp_path)
    http, client = _desc_client()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(cli_mod, "generate_bio", lambda *a, **k: ("简介", "", True))
    cfg = _desc_cfg(tmp_path)
    assert run(cfg, fixture=fx, b_client=client) == 0  # 记基线

    _bump_fixture(
        fx,
        "alice/cool-tool",
        pushed="2026-10-24T00:00:00Z",
        description="我亲手写的描述，别动",
    )
    monkeypatch.setattr(
        cli_mod,
        "generate_descriptions",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("手动改过不得调 AI")),
    )
    assert run(cfg, fixture=fx, b_client=client) == 0
    assert http.writes() == []
    data = json.loads((tmp_path / "data" / "snapshot.json").read_text(encoding="utf-8"))
    assert data["repo_descriptions"]["alice/cool-tool"]["manual"] is True
    assert "手动" in capsys.readouterr().out
    assert "我亲手写的描述，别动" in (tmp_path / "README.md").read_text(encoding="utf-8")


# ============================ 首次接管 PR 预览（2026-10-25） ============================

_TAKEOVER_BRANCH = "readme-auto/takeover"
_OWNER = "alice/alice"


def _pulls_url(base: str = "main") -> str:
    return (
        f"https://api.github.com/repos/{_OWNER}/pulls"
        f"?state=open&head=alice:{_TAKEOVER_BRANCH}&base={base}"
    )


def _handwritten(tmp_path: Path, *, bilingual: bool = True) -> None:
    (tmp_path / "README.md").write_text(
        "# 我的主页\n\n我手写的介绍，请完整保留。\n\n## 联系我\n- email: me@example.com\n",
        encoding="utf-8",
    )
    if bilingual:
        (tmp_path / "README.en.md").write_text(
            "# My Profile\n\nHandwritten English intro to preserve.\n", encoding="utf-8"
        )


def _takeover_client(
    *, prs_first=None, with_commit_head=True, blob_ok=True, repo_default_branch="main"
):
    from fake_github import FakeHttp, FakeResponse
    from profile_agent.github_client import API, GitHubClient

    http = FakeHttp()
    # 主页仓库不在 repos 列表时（常见：config 排除了 profile 自身），默认分支走 API
    http.add(
        "GET",
        f"{API}/repos/{_OWNER}",
        FakeResponse(200, {"default_branch": repo_default_branch}),
    )
    # 开局检测 + open_or_refresh_pr 内部再查一次
    first = prs_first if prs_first is not None else [FakeResponse(200, [])] * 2
    http.add("GET", _pulls_url(repo_default_branch), *first)
    if with_commit_head:
        http.add(
            "GET",
            f"{API}/repos/{_OWNER}/commits/HEAD",
            FakeResponse(200, {"sha": "headsha123"}),
        )
        http.add(
            "GET",
            f"{API}/repos/{_OWNER}/git/ref/heads/{_TAKEOVER_BRANCH}",
            FakeResponse(404),
        )
    if blob_ok:
        http.add(
            "POST",
            f"{API}/repos/{_OWNER}/git/blobs",
            *[FakeResponse(201, {"sha": f"blob{i}"}) for i in range(3)],
        )
    else:
        # 每个 blob 重试 2 次（共 3 次）后抛错——足够让首个 blob 失败并触发回退
        http.add(
            "POST",
            f"{API}/repos/{_OWNER}/git/blobs",
            *[FakeResponse(500, text="boom") for _ in range(6)],
        )
    http.add("POST", f"{API}/repos/{_OWNER}/git/trees", FakeResponse(201, {"sha": "tree1"}))
    http.add("POST", f"{API}/repos/{_OWNER}/git/commits", FakeResponse(201, {"sha": "commit1"}))
    http.add("POST", f"{API}/repos/{_OWNER}/git/refs", FakeResponse(201, {"ref": "ref1"}))
    http.add(
        "POST",
        f"{API}/repos/{_OWNER}/pulls",
        FakeResponse(201, {"number": 12, "html_url": "https://github.com/alice/alice/pull/12"}),
    )
    return http, GitHubClient("pat", session=http, sleep=lambda _s: None)


def _takeover_ai(monkeypatch):
    import profile_agent.cli as cli_mod

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(cli_mod, "generate_bio", lambda *a, **k: ("简介", "Bio", True))
    monkeypatch.setattr(cli_mod, "translate_descriptions", lambda items, cfg, **kw: ({}, ""))
    return cli_mod


def test_first_takeover_opens_pr_and_leaves_main_untouched(tmp_path, monkeypatch, capsys):
    """首次接管：一个 PR 装齐中英 README + 快照；本地 main 一个字节都不动。"""
    cfg = _write_config(tmp_path, mode="full")
    _handwritten(tmp_path)
    before_zh = (tmp_path / "README.md").read_text(encoding="utf-8")
    before_en = (tmp_path / "README.en.md").read_text(encoding="utf-8")
    http, client = _takeover_client()
    _takeover_ai(monkeypatch)

    assert run(cfg, fixture=FIXTURES, b_client=client) == 0

    out = capsys.readouterr().out
    assert "接管" in out and "pull/12" in out
    # 本地零写入（快照也不落盘——快照随 PR 一起合并）
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == before_zh
    assert (tmp_path / "README.en.md").read_text(encoding="utf-8") == before_en
    assert not (tmp_path / "data" / "snapshot.json").exists()

    tree_req = next(r for r in http.requests if r["url"].endswith("/git/trees"))
    paths = {e["path"] for e in tree_req["json"]["tree"]}
    assert paths == {"README.md", "README.en.md", "data/snapshot.json"}
    blobs = [r for r in http.requests if r["url"].endswith("/git/blobs")]
    assert len(blobs) == 3
    assert "我手写的介绍，请完整保留" in blobs[0]["json"]["content"]
    assert "Handwritten English intro to preserve" in blobs[1]["json"]["content"]
    snap = json.loads(blobs[2]["json"]["content"])
    assert snap["schema"] == 5 and snap["username"] == "alice"
    pr_req = next(r for r in http.requests if r["url"].endswith("/pulls") and r["method"] == "POST")
    assert pr_req["json"]["head"] == _TAKEOVER_BRANCH
    assert pr_req["json"]["base"] == "main"
    assert "接管" in pr_req["json"]["title"]
    assert "data/snapshot.json" in pr_req["json"]["body"]


def test_takeover_skips_whole_round_while_pr_open(tmp_path, monkeypatch, capsys):
    """接管 PR 还开着：直接跳过（不重渲染、不调 AI、不开第二条 PR）。"""
    from fake_github import FakeResponse

    cfg = _write_config(tmp_path, mode="full")
    _handwritten(tmp_path)
    http, client = _takeover_client(
        prs_first=[
            FakeResponse(
                200,
                [{"number": 12, "html_url": "https://github.com/alice/alice/pull/12"}],
            )
        ],
        with_commit_head=False,
    )
    cli_mod = _takeover_ai(monkeypatch)
    monkeypatch.setattr(
        cli_mod,
        "generate_bio",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("PR 待合并时不得调 AI")),
    )

    assert run(cfg, fixture=FIXTURES, b_client=client) == 0
    out = capsys.readouterr().out
    assert "仍待合并" in out and "pull/12" in out
    assert not any(r["method"] != "GET" for r in http.requests)
    assert not (tmp_path / "data" / "snapshot.json").exists()
    assert (tmp_path / "README.md").read_text(encoding="utf-8").startswith("# 我的主页")


def test_takeover_without_pat_skips_and_leaves_main_untouched(
    tmp_path, monkeypatch, capsys
):
    """没有 WORKFLOW_PAT：跳过本轮（main 零改动），并提示改用 auto 或配置 PAT。"""
    cfg = _write_config(tmp_path, mode="full")
    _handwritten(tmp_path, bilingual=False)
    _takeover_ai(monkeypatch)

    assert run(cfg, fixture=FIXTURES) == 0
    err = capsys.readouterr().err
    assert "WORKFLOW_PAT" in err and "main 未改动" in err
    # 提示必须给出两条出路（默认 auto 或补 PAT），否则用户只知道失败
    assert "readme.mode" in err and "auto" in err
    readme = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert readme == "# 我的主页\n\n我手写的介绍，请完整保留。\n\n## 联系我\n- email: me@example.com\n"
    assert not (tmp_path / "data" / "snapshot.json").exists()


def test_takeover_dry_run_prints_and_never_calls_github(tmp_path, monkeypatch, capsys):
    """dry-run 只打印渲染结果：不检测 PR、不推送、不写文件。"""
    cfg = _write_config(tmp_path, mode="full")
    _handwritten(tmp_path)
    http, client = _takeover_client()
    _takeover_ai(monkeypatch)

    assert run(cfg, fixture=FIXTURES, b_client=client, dry_run=True) == 0
    assert http.requests == []
    assert (tmp_path / "README.md").read_text(encoding="utf-8").startswith("# 我的主页")


def test_takeover_bilingual_false_pushes_two_files(tmp_path, monkeypatch):
    """单语模式：PR 里只有 README.md + 快照，不凭空造 en 文件。"""
    as_posix = lambda p: p.as_posix()  # noqa: E731
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        f"""
github:
  username: "alice"
repos:
  exclude: ["alice/old-profile"]
readme:
  path: "{as_posix(tmp_path / 'README.md')}"
  template: "{as_posix(TEMPLATE)}"
  bilingual: false
  mode: "full"
snapshot:
  path: "{as_posix(tmp_path / 'data' / 'snapshot.json')}"
""",
        encoding="utf-8",
    )
    _handwritten(tmp_path, bilingual=False)
    http, client = _takeover_client()
    _takeover_ai(monkeypatch)

    assert run(cfg, fixture=FIXTURES, b_client=client) == 0
    tree_req = next(r for r in http.requests if r["url"].endswith("/git/trees"))
    assert {e["path"] for e in tree_req["json"]["tree"]} == {
        "README.md",
        "data/snapshot.json",
    }


def test_takeover_uses_profile_repo_default_branch(tmp_path, monkeypatch, capsys):
    """profile 仓库被 config 排除时，PR base 仍取真实默认分支（不是硬编码 main）。"""
    cfg = _write_config(tmp_path, mode="full")
    _handwritten(tmp_path, bilingual=False)
    http, client = _takeover_client(repo_default_branch="trunk")
    _takeover_ai(monkeypatch)

    assert run(cfg, fixture=FIXTURES, b_client=client) == 0
    assert http.has("GET", f"/repos/{_OWNER}")
    pr_req = next(r for r in http.requests if r["url"].endswith("/pulls") and r["method"] == "POST")
    assert pr_req["json"]["base"] == "trunk"
    assert "接管" in capsys.readouterr().out


def test_after_takeover_pr_merged_next_round_is_noop(tmp_path, monkeypatch, capsys):
    """PR 合并后的下一轮：盘上已是机器 README + 新快照 → 正常增量门直接 no-op。"""
    cfg = _write_config(tmp_path, mode="full")
    _handwritten(tmp_path)
    http, client = _takeover_client()
    _takeover_ai(monkeypatch)
    assert run(cfg, fixture=FIXTURES, b_client=client) == 0

    blobs = [r for r in http.requests if r["url"].endswith("/git/blobs")]
    (tmp_path / "README.md").write_text(blobs[0]["json"]["content"], encoding="utf-8")
    (tmp_path / "README.en.md").write_text(blobs[1]["json"]["content"], encoding="utf-8")
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data" / "snapshot.json").write_text(
        blobs[2]["json"]["content"], encoding="utf-8"
    )

    assert run(cfg, fixture=FIXTURES) == 0
    assert "无变化，跳过更新" in capsys.readouterr().out


def _open_pr_response() -> "object":
    from fake_github import FakeResponse

    return FakeResponse(
        200, [{"number": 12, "html_url": "https://github.com/alice/alice/pull/12"}]
    )


def test_takeover_force_refreshes_open_pr_and_stays_off_main(
    tmp_path, monkeypatch, capsys
):
    """--force + 已打开的接管 PR：刷新该 PR（不新建），main 一个字节都不动。"""
    from fake_github import FakeResponse
    from profile_agent.github_client import API

    cfg = _write_config(tmp_path, mode="full")
    _handwritten(tmp_path)
    before_zh = (tmp_path / "README.md").read_text(encoding="utf-8")
    http, client = _takeover_client(prs_first=[_open_pr_response()] * 2)
    http.add(
        "PATCH",
        f"{API}/repos/{_OWNER}/pulls/12",
        FakeResponse(200, {"number": 12}),
    )
    _takeover_ai(monkeypatch)

    assert run(cfg, fixture=FIXTURES, b_client=client, force=True) == 0

    out = capsys.readouterr().out
    assert "仍待合并" in out and "刷新" in out and "main 仍不改动" in out
    assert "分支上手动改动会被覆盖" in out  # 覆盖风险必须说清
    assert not any(
        r["method"] == "POST" and r["url"].endswith("/pulls") for r in http.requests
    )  # 刷新不新建 PR
    assert http.has("PATCH", "/pulls/12")
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == before_zh
    assert not (tmp_path / "data" / "snapshot.json").exists()


def test_takeover_force_refresh_failure_never_writes_main(
    tmp_path, monkeypatch, capsys
):
    """刷新失败也不能绕过待审核的 PR 去写 main：只警告，下一轮重试。"""
    cfg = _write_config(tmp_path, mode="full")
    _handwritten(tmp_path)
    before_zh = (tmp_path / "README.md").read_text(encoding="utf-8")
    http, client = _takeover_client(prs_first=[_open_pr_response()] * 2, blob_ok=False)
    _takeover_ai(monkeypatch)

    assert run(cfg, fixture=FIXTURES, b_client=client, force=True) == 0

    err = capsys.readouterr().err
    assert "main 未改动" in err and "自动重试" in err
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == before_zh
    assert not (tmp_path / "data" / "snapshot.json").exists()


def test_takeover_push_failure_never_writes_main(tmp_path, monkeypatch, capsys):
    """推送/开 PR 阶段失败（分支可能已推出去）：绝不回退写 main。"""
    cfg = _write_config(tmp_path, mode="full")
    _handwritten(tmp_path, bilingual=False)
    http, client = _takeover_client(blob_ok=False)
    _takeover_ai(monkeypatch)

    assert run(cfg, fixture=FIXTURES, b_client=client) == 0
    err = capsys.readouterr().err
    assert "main 未改动" in err and "自动重试" in err
    assert not (tmp_path / "data" / "snapshot.json").exists()
    assert (tmp_path / "README.md").read_text(encoding="utf-8").startswith("# 我的主页")


def test_takeover_head_read_failure_skips_round(tmp_path, monkeypatch, capsys):
    """读不到 HEAD 就无法建分支：跳过本轮、main 零改动，下一轮自动重试。"""
    cfg = _write_config(tmp_path, mode="full")
    _handwritten(tmp_path, bilingual=False)
    http, client = _takeover_client(with_commit_head=False)
    _takeover_ai(monkeypatch)

    assert run(cfg, fixture=FIXTURES, b_client=client) == 0
    err = capsys.readouterr().err
    assert "无法读取主页仓库 HEAD" in err and "本轮跳过" in err
    assert "main 未改动" in err
    assert not (tmp_path / "data" / "snapshot.json").exists()
    assert (tmp_path / "README.md").read_text(encoding="utf-8").startswith("# 我的主页")
    # 什么都没推出去：不得有 blob/tree/commit/PR 写入请求
    assert not any(r["method"] != "GET" for r in http.requests)


def test_takeover_opens_gate_even_when_fingerprint_unchanged(tmp_path, monkeypatch):
    """指纹完全没变也必须开门：否则第二次运行会 no-op，接管 PR 永远开不出来。"""
    cfg = _write_config(tmp_path, mode="full")
    _takeover_ai(monkeypatch)
    assert run(cfg, fixture=FIXTURES) == 0  # 正常首跑：机器 README + 快照
    (tmp_path / "README.md").write_text(
        "# 手写主页\n这是我后来换上的手写内容。\n", encoding="utf-8"
    )
    (tmp_path / "README.en.md").write_text(
        "# Handwritten\nReplaced after the first run.\n", encoding="utf-8"
    )

    http, client = _takeover_client()
    assert run(cfg, fixture=FIXTURES, b_client=client) == 0
    assert http.has("POST", "/pulls")


def test_after_takeover_merged_data_change_updates_normally(tmp_path, monkeypatch):
    """合并后数据变化：不再走接管分支，按普通增量更新本地 README。"""
    fx = _copy_fixture(tmp_path)
    cfg = _desc_cfg(tmp_path, enabled=False, mode="full")
    _handwritten(tmp_path, bilingual=False)
    http, client = _takeover_client()
    _takeover_ai(monkeypatch)
    assert run(cfg, fixture=fx, b_client=client) == 0

    blobs = [r for r in http.requests if r["url"].endswith("/git/blobs")]
    (tmp_path / "README.md").write_text(blobs[0]["json"]["content"], encoding="utf-8")
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data" / "snapshot.json").write_text(
        blobs[1]["json"]["content"], encoding="utf-8"
    )

    _bump_fixture(fx, "alice/cool-tool", pushed="2026-10-26T00:00:00Z", description="新的描述")
    assert run(cfg, fixture=fx) == 0
    readme = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "新的描述" in readme
    assert "我手写的介绍，请完整保留" in readme  # custom 区仍然保留


def test_description_summary_has_single_heading(tmp_path, monkeypatch, capsys):
    """仓库描述相关提示只占一个小节：### 仓库描述写回（警告与结果合并）。"""
    import profile_agent.cli as cli_mod

    from fake_github import FakeHttp
    from profile_agent.github_client import GitHubClient

    fx = _copy_fixture(tmp_path)
    http = FakeHttp()
    client = GitHubClient("pat", session=http, sleep=lambda _s: None)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(cli_mod, "generate_bio", lambda *a, **k: ("简介", "", True))
    cfg = _desc_cfg(tmp_path)
    assert run(cfg, fixture=fx, b_client=client) == 0  # 首轮：只记基线
    _bump_fixture(fx, "alice/cool-tool", pushed="2026-11-01T00:00:00+00:00")
    monkeypatch.setattr(
        cli_mod, "generate_descriptions", lambda *a, **k: ({}, "生成失败（测试）")
    )
    summary = tmp_path / "step_summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert run(cfg, fixture=fx, b_client=client) == 0
    text = summary.read_text(encoding="utf-8")
    assert "### 仓库描述写回" in text
    assert text.count("### 仓库描述") == 1  # 不再出现重复小节
    assert "生成失败（测试）" in text


def test_description_unchanged_then_next_round_is_noop(tmp_path, monkeypatch):
    """生成结果与当前描述一致也算处理完：下一轮门关上，不再白烧一次批量 AI。"""
    import profile_agent.cli as cli_mod

    from fake_github import FakeHttp
    from profile_agent.github_client import GitHubClient

    fx = _copy_fixture(tmp_path)
    http = FakeHttp()
    client = GitHubClient("pat", session=http, sleep=lambda _s: None)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(cli_mod, "generate_bio", lambda *a, **k: ("简介", "", True))
    cfg = _desc_cfg(tmp_path)
    assert run(cfg, fixture=fx, b_client=client) == 0  # 首轮：只记基线
    _bump_fixture(fx, "alice/cool-tool", pushed="2026-11-01T00:00:00+00:00")
    calls = []

    def gen(items, cfg_ai, **kw):
        calls.append(items)
        return {"alice/cool-tool": "一个很酷的工具"}, ""

    monkeypatch.setattr(cli_mod, "generate_descriptions", gen)
    assert run(cfg, fixture=fx, b_client=client) == 0
    assert len(calls) == 1
    assert http.writes() == []  # 无需改动：不 PATCH
    monkeypatch.setattr(
        cli_mod,
        "generate_descriptions",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("无需改动后不应再调 AI")),
    )
    assert run(cfg, fixture=fx, b_client=client) == 0


# ======================= 手写 README 默认只加一个托管块（auto） =======================

_EMBED_START = "<!-- profile-agent:readme:start -->"
_EMBED_END = "<!-- profile-agent:readme:end -->"

# 覆盖常见「精美 README」元素：居中 HTML、图片、引用、<details>、代码围栏假标题
_RICH_README = (
    '<p align="center"><img src="banner.png" width="100%"></p>\n'
    "\n"
    "# Alice Chen\n"
    "\n"
    "> 手写一句话介绍\n"
    "\n"
    "<details><summary>关于我</summary>\n"
    "\n"
    "```bash\n"
    "# 这不是标题\n"
    "```\n"
    "\n"
    "</details>\n"
    "\n"
    "## 联系我\n"
    "- email: me@example.com\n"
)


def _embed_ai(monkeypatch, *, zh: str = "简介", en: str = "Bio"):
    import profile_agent.cli as cli_mod

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(cli_mod, "generate_bio", lambda *a, **k: (zh, en, True))
    monkeypatch.setattr(
        cli_mod, "translate_descriptions", lambda items, cfg, **kw: ({}, "")
    )
    return cli_mod


def test_auto_embed_inserts_block_after_h1_and_preserves_everything(
    tmp_path, monkeypatch
):
    """手写页默认 auto：标题后多一个 AI 简介块，块外逐字节不变、不出现统计/置顶/pin。"""
    cfg = _write_config(tmp_path, mode="auto", bilingual=False)
    (tmp_path / "README.md").write_text(_RICH_README, encoding="utf-8")
    _embed_ai(monkeypatch)

    assert run(cfg, fixture=FIXTURES) == 0

    out = (tmp_path / "README.md").read_text(encoding="utf-8")
    block = f"{_EMBED_START}\n## 🤖 AI 简介\n\n简介\n{_EMBED_END}\n"
    assert out == _RICH_README.replace("# Alice Chen\n", "# Alice Chen\n\n" + block, 1)
    # 只含简介：整页接管才有的东西一个都不能出现，Pinned 更不会被重新展示
    for word in ("统计信息", "置顶项目", "仓库列表", "📌"):
        assert word not in out


def test_auto_embed_second_run_with_same_bio_is_noop(tmp_path, monkeypatch, capsys):
    cfg = _write_config(tmp_path, mode="auto", bilingual=False)
    (tmp_path / "README.md").write_text(_RICH_README, encoding="utf-8")
    _embed_ai(monkeypatch)
    assert run(cfg, fixture=FIXTURES) == 0
    first = (tmp_path / "README.md").read_text(encoding="utf-8")
    capsys.readouterr()

    assert run(cfg, fixture=FIXTURES) == 0
    assert "无变化，跳过更新" in capsys.readouterr().out
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == first


def test_auto_embed_bio_change_updates_only_inside_block(tmp_path, monkeypatch):
    fx = _copy_fixture(tmp_path)
    cfg = _write_config(tmp_path, mode="auto", bilingual=False)
    (tmp_path / "README.md").write_text(_RICH_README, encoding="utf-8")
    _embed_ai(monkeypatch)
    assert run(cfg, fixture=fx) == 0
    first = (tmp_path / "README.md").read_text(encoding="utf-8")
    i, j = first.index(_EMBED_START), first.index(_EMBED_END) + len(_EMBED_END)
    prefix, suffix = first[:i], first[j:]

    _bump_fixture(fx, "alice/cool-tool", pushed="2026-11-01T00:00:00+00:00")
    _embed_ai(monkeypatch, zh="更新后的简介")
    assert run(cfg, fixture=fx) == 0

    second = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "## 🤖 AI 简介\n\n更新后的简介" in second
    assert second[: second.index(_EMBED_START)] == prefix
    assert second[second.index(_EMBED_END) + len(_EMBED_END) :] == suffix


def test_auto_embed_without_ai_key_leaves_file_untouched(tmp_path, capsys):
    """没有 AI key：不插空块、文件一字节不改；配好 key 后自动补上。"""
    cfg = _write_config(tmp_path, mode="auto", bilingual=False)
    (tmp_path / "README.md").write_text(_RICH_README, encoding="utf-8")

    assert _run(cfg) == 0

    assert "还没有可用的 AI 简介" in capsys.readouterr().err
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == _RICH_README


def test_auto_embed_reinserts_after_user_deletes_block(tmp_path, monkeypatch, capsys):
    """块被删（含整段删除）→ 下一轮重新插入：与「删除会恢复」的承诺一致。"""
    cfg = _write_config(tmp_path, mode="auto", bilingual=False)
    (tmp_path / "README.md").write_text(_RICH_README, encoding="utf-8")
    _embed_ai(monkeypatch)
    assert run(cfg, fixture=FIXTURES) == 0
    out = (tmp_path / "README.md").read_text(encoding="utf-8")
    removed = out.replace(
        f"{_EMBED_START}\n## 🤖 AI 简介\n\n简介\n{_EMBED_END}\n", "", 1
    )
    assert removed != out
    (tmp_path / "README.md").write_text(removed, encoding="utf-8")
    capsys.readouterr()

    assert run(cfg, fixture=FIXTURES) == 0
    again = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "## 🤖 AI 简介\n\n简介" in again
    assert again.startswith('<p align="center">')
    assert again.rstrip().endswith("- email: me@example.com")


def test_auto_embed_broken_block_markers_are_skipped(tmp_path, monkeypatch, capsys):
    """块 marker 缺一半：跳过该文件 + 警告，绝不猜修复、绝不覆盖原文。"""
    cfg = _write_config(tmp_path, mode="auto", bilingual=False)
    original = f"# Alice\n\n{_EMBED_START}\n旧块没有结束标记\n"
    (tmp_path / "README.md").write_text(original, encoding="utf-8")
    _embed_ai(monkeypatch)

    assert run(cfg, fixture=FIXTURES) == 0

    assert "marker 不平衡" in capsys.readouterr().err
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == original


def test_auto_embed_bilingual_inserts_language_specific_blocks(
    tmp_path, monkeypatch
):
    cfg = _write_config(tmp_path, mode="auto")  # bilingual 默认 true
    _handwritten(tmp_path)
    _embed_ai(monkeypatch, zh="中文简介", en="English bio")

    assert run(cfg, fixture=FIXTURES) == 0

    zh = (tmp_path / "README.md").read_text(encoding="utf-8")
    en = (tmp_path / "README.en.md").read_text(encoding="utf-8")
    assert "## 🤖 AI 简介\n\n中文简介" in zh
    assert "## 🤖 AI Bio\n\nEnglish bio" in en
    assert zh.startswith("# 我的主页") and en.startswith("# My Profile")


def test_auto_embed_bilingual_missing_en_file_is_not_created(tmp_path, monkeypatch):
    cfg = _write_config(tmp_path, mode="auto")
    _handwritten(tmp_path, bilingual=False)
    _embed_ai(monkeypatch)

    assert run(cfg, fixture=FIXTURES) == 0

    assert "## 🤖 AI 简介" in (tmp_path / "README.md").read_text(encoding="utf-8")
    assert not (tmp_path / "README.en.md").exists()


def test_auto_embed_mixed_pair_leaves_machine_file_alone(
    tmp_path, monkeypatch, capsys
):
    """zh 手写 + en 是旧机器页：只动 zh，en 原样保留并说明原因。"""
    cfg = _write_config(tmp_path, mode="auto")
    _handwritten(tmp_path, bilingual=False)
    machine_en = (
        "<!-- profile-agent 模板说明：机器生成页 -->\n# Machine Page\n\n旧生成内容\n"
    )
    (tmp_path / "README.en.md").write_text(machine_en, encoding="utf-8")
    _embed_ai(monkeypatch)

    assert run(cfg, fixture=FIXTURES) == 0

    assert "## 🤖 AI 简介" in (tmp_path / "README.md").read_text(encoding="utf-8")
    assert (tmp_path / "README.en.md").read_text(encoding="utf-8") == machine_en
    assert "README.en.md 不是手写文件" in capsys.readouterr().err


def test_auto_embed_dry_run_prints_and_writes_nothing(tmp_path, monkeypatch, capsys):
    cfg = _write_config(tmp_path, mode="auto")
    _handwritten(tmp_path)
    http, client = _takeover_client()
    _embed_ai(monkeypatch)

    assert run(cfg, fixture=FIXTURES, b_client=client, dry_run=True) == 0

    out = capsys.readouterr().out
    assert _EMBED_START in out and "# 我的主页" in out
    assert http.requests == []
    assert (tmp_path / "README.md").read_text(encoding="utf-8").startswith("# 我的主页")
    assert _EMBED_START not in (tmp_path / "README.en.md").read_text(encoding="utf-8")


def test_auto_skips_when_old_takeover_pr_is_still_open(
    tmp_path, monkeypatch, capsys
):
    """从 full 切到 auto 但旧接管 PR 还开着：跳过本轮，避免日后合并旧 PR 覆盖排版。"""
    cfg = _write_config(tmp_path, bilingual=False)  # 默认 auto
    _handwritten(tmp_path, bilingual=False)
    http, client = _takeover_client(prs_first=[_open_pr_response()])
    _embed_ai(monkeypatch)
    before = (tmp_path / "README.md").read_text(encoding="utf-8")

    assert run(cfg, fixture=FIXTURES, b_client=client) == 0

    err = capsys.readouterr().err
    assert "旧的整页接管 PR" in err and "readme.mode" in err
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == before
    assert not (tmp_path / "data" / "snapshot.json").exists()
    assert not any(r["url"].endswith("/git/blobs") for r in http.requests)


def test_auto_to_full_strips_old_embed_block_from_custom(tmp_path, monkeypatch):
    """auto → full 迁移：旧简介块从 custom 区剥掉，不在成品页里重复出现。"""
    cfg = _write_config(tmp_path, mode="full", bilingual=False)
    (tmp_path / "README.md").write_text(
        "# Alice\n\n手写内容\n\n"
        f"{_EMBED_START}\n## 🤖 AI 简介\n\n旧简介\n{_EMBED_END}\n",
        encoding="utf-8",
    )
    http, client = _takeover_client()
    _embed_ai(monkeypatch)

    assert run(cfg, fixture=FIXTURES, b_client=client) == 0

    blobs = [r for r in http.requests if r["url"].endswith("/git/blobs")]
    content = blobs[0]["json"]["content"]
    assert "手写内容" in content
    assert "旧简介" not in content
    assert _EMBED_START not in content
    assert "统计信息" in content  # full 照常整页接管


def test_full_mode_broken_custom_markers_never_overwrite_original(
    tmp_path, capsys
):
    """custom 锚点损坏（半截）：跳过写入，原文逐字节保留。"""
    cfg = _write_config(tmp_path, mode="full", bilingual=False)
    original = "# 手写\n\n<!-- profile-agent:custom:start -->\n没有结束标记\n"
    (tmp_path / "README.md").write_text(original, encoding="utf-8")

    assert _run(cfg) == 0

    assert "marker 损坏" in capsys.readouterr().err
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == original


# ---- 可复用 workflow：默认模板回退（PROFILE_AGENT_HOME） ----

TOOL_ROOT = Path(__file__).resolve().parents[1]


def _write_min_config(tmp_path: Path, template: str = "templates/profile.md.j2") -> Path:
    """只写渲染必需的配置：模板路径保持相对（模拟调用方只有 config + README）。"""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        f"""
github:
  username: "alice"
readme:
  path: "{(tmp_path / 'README.md').as_posix()}"
  template: "{template}"
snapshot:
  path: "{(tmp_path / 'data' / 'snapshot.json').as_posix()}"
""",
        encoding="utf-8",
    )
    return cfg


def test_default_template_falls_back_to_tool_home(tmp_path, monkeypatch, capsys):
    """调用方没有 templates/：用 PROFILE_AGENT_HOME 指向的工具模板，渲染成功。"""
    monkeypatch.setenv("PROFILE_AGENT_HOME", str(TOOL_ROOT))
    cfg = _write_min_config(tmp_path)

    assert _run(cfg, dry_run=True) == 0

    out = capsys.readouterr().out
    assert "统计信息" in out
    assert not (tmp_path / "templates").exists()


def test_default_template_prefers_tool_home_over_stale_local_copy(
    tmp_path, monkeypatch, capsys
):
    """默认模板路径跟随上游：副本里的旧模板不会悄悄生效。"""
    monkeypatch.setenv("PROFILE_AGENT_HOME", str(TOOL_ROOT))
    local = tmp_path / "templates" / "profile.md.j2"
    local.parent.mkdir()
    local.write_text("LOCAL-OLD-TEMPLATE", encoding="utf-8")
    cfg = _write_min_config(tmp_path)

    assert _run(cfg, dry_run=True) == 0

    out = capsys.readouterr().out
    assert "统计信息" in out
    assert "LOCAL-OLD-TEMPLATE" not in out


def test_custom_template_path_always_uses_local_file(tmp_path, monkeypatch, capsys):
    """自定义模板路径（非默认）永远以本地配置为准。"""
    monkeypatch.setenv("PROFILE_AGENT_HOME", str(TOOL_ROOT))
    (tmp_path / "my.md.j2").write_text("CUSTOM-TEMPLATE-MARKER", encoding="utf-8")
    cfg = _write_min_config(tmp_path, template="my.md.j2")

    assert _run(cfg, dry_run=True) == 0

    out = capsys.readouterr().out
    assert "CUSTOM-TEMPLATE-MARKER" in out
    assert "统计信息" not in out


# ---- AI 失败显式上报（2026-10-27：额度/认证问题不能装成工具故障）----


def test_report_ai_failures_writes_summary_and_warning(tmp_path, monkeypatch, capsys):
    from profile_agent import ai_failures, cli

    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    ai_failures.reset()

    class QuotaError(Exception):
        status_code = 402

    ai_failures.record(QuotaError("Insufficient account balance"), "anthropic", "deepseek-v4.1-flash")
    cli._report_ai_failures()

    text = summary.read_text(encoding="utf-8")
    assert "AI 调用失败" in text
    assert "额度/计费" in text
    assert "不是本工具" in text
    out = capsys.readouterr().out
    assert "::warning::" in out and "额度" in out


def test_report_ai_failures_silent_when_clean(tmp_path, monkeypatch, capsys):
    from profile_agent import ai_failures, cli

    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    ai_failures.reset()
    cli._report_ai_failures()

    assert not summary.exists() or summary.read_text(encoding="utf-8") == ""
    assert "::warning::" not in capsys.readouterr().out


# ---- GitHub 令牌解析（2026-10-27）：内置 GITHUB_TOKEN 只对本仓库有效 ----


def _cfg_with_token_env(name: str):
    from types import SimpleNamespace

    return SimpleNamespace(github=SimpleNamespace(token_env=name))


def test_token_defaults_to_workflow_pat_when_present(monkeypatch):
    """配置写的是默认 GITHUB_TOKEN，但 workflow 注入了 WORKFLOW_PAT → 用 PAT。

    内置 token 只对本仓库有效：拿它读账号下其他仓库（哪怕公开）GitHub 一律
    返回 404，用户会看到「7 个仓库全部读取失败」却找不到原因。
    """
    from profile_agent.cli import _resolve_github_token

    monkeypatch.setenv("GITHUB_TOKEN", "ghs_builtin")
    monkeypatch.setenv("WORKFLOW_PAT", "ghp_classic")
    assert _resolve_github_token(_cfg_with_token_env("GITHUB_TOKEN")) == "ghp_classic"


def test_token_explicit_env_wins_over_workflow_pat(monkeypatch):
    from profile_agent.cli import _resolve_github_token

    monkeypatch.setenv("MY_PAT", "ghp_mine")
    monkeypatch.setenv("WORKFLOW_PAT", "ghp_other")
    assert _resolve_github_token(_cfg_with_token_env("MY_PAT")) == "ghp_mine"


def test_token_falls_back_to_builtin_when_no_pat(monkeypatch):
    from profile_agent.cli import _resolve_github_token

    monkeypatch.delenv("WORKFLOW_PAT", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "ghs_builtin")
    assert _resolve_github_token(_cfg_with_token_env("GITHUB_TOKEN")) == "ghs_builtin"


def test_token_anonymous_when_nothing_set(monkeypatch):
    from profile_agent.cli import _resolve_github_token

    for name in ("GITHUB_TOKEN", "WORKFLOW_PAT"):
        monkeypatch.delenv(name, raising=False)
    assert _resolve_github_token(_cfg_with_token_env("GITHUB_TOKEN")) is None


def test_token_kind_names_the_type_from_prefix():
    """令牌类型看前缀就能说清：fine-grained 令牌读未勾选仓库一律 404。"""
    from profile_agent.cli import _token_kind

    assert "classic" in _token_kind("ghp_abc")
    assert "fine-grained" in _token_kind("github_pat_abc")
    assert "GitHub App" in _token_kind("ghs_abc")
    assert _token_kind("") == "匿名"


def test_pat_404_hint_includes_actual_token_identity():
    """404 提示要带上「本轮到底用了哪把令牌、属于谁」，否则用户只能靠猜。"""
    from profile_agent.cli import _pat_404_hint

    hint = _pat_404_hint(
        {"error": 2, "not_found": 2}, token_desc="fine-grained（github_pat_）· 账号 ningbojian"
    )
    assert "fine-grained" in hint and "ningbojian" in hint


def test_fetch_compare_uses_three_dot_range():
    """GitHub REST compare 只认 base...head（三个点）：两个点一律 404。

    两个点让每个有基线的仓库都「用户不存在或仓库不可见」，且与令牌无关。
    """
    from profile_agent.cli import _fetchers_from_client

    calls = []

    class FakeClient:
        def _get(self, url):
            calls.append(url)
            return {"commits": [], "files": []}, {}

    _fetchers_from_client(FakeClient()).fetch_compare("alice/x", "aaa", "bbb")
    assert calls == ["https://api.github.com/repos/alice/x/compare/aaa...bbb"]


def test_fetch_compare_404_becomes_compare_gone():
    """compare 404 → CompareGone（重置基线），不能被当成普通错误跳过整仓。"""
    from profile_agent.cli import _fetchers_from_client
    from profile_agent.github_client import GitHubError
    from profile_agent.repo_readmes import CompareGone

    class FakeClient:
        def _get(self, url):
            raise GitHubError("GitHub API 返回 404：用户不存在或仓库不可见", 404)

    with pytest.raises(CompareGone):
        _fetchers_from_client(FakeClient()).fetch_compare("alice/x", "aaa", "bbb")
