"""cli.py 离线端到端编排测试（用 fixture，不联网、不调 AI）。"""

import json
from pathlib import Path

from profile_agent.cli import run

FIXTURES = Path(__file__).parent / "fixtures"
TEMPLATE = Path(__file__).parent.parent / "templates" / "profile.md.j2"


def _write_config(tmp_path: Path, username: str = "alice") -> Path:
    cfg = tmp_path / "config.yaml"
    as_posix = lambda p: p.as_posix()  # noqa: E731
    cfg.write_text(
        f"""
github:
  username: "{username}"
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
    assert "仓库统计" in out and "精选项目" in out
    assert not (tmp_path / "README.md").exists()
    assert not (tmp_path / "data" / "snapshot.json").exists()


def test_full_run_writes_readme_and_snapshot(tmp_path):
    cfg = _write_config(tmp_path)
    assert _run(cfg) == 0
    readme = tmp_path / "README.md"
    snap = tmp_path / "data" / "snapshot.json"
    assert readme.exists() and "仓库统计" in readme.read_text(encoding="utf-8")
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


def _block_project_ai(monkeypatch):
    """挡掉项目介绍的真实 AI 调用：让 update_project_intros 快速降级为数据版。

    这些测试关注的是 bio/门控逻辑；不挡的话 anthropic SDK 会真的去连网络。
    """
    import profile_agent.projects as projects_mod

    def fake(repo, cfg, **kwargs):
        raise RuntimeError("offline")

    monkeypatch.setattr(projects_mod, "generate_project_intro", fake)


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
    """替换 generate_bio：记录调用并按序返回 (text, generated)。"""
    import profile_agent.cli as cli_mod

    def fake(payload, cfg, **kwargs):
        calls.append(kwargs)
        return texts[len(calls) - 1], True

    monkeypatch.setattr(cli_mod, "generate_bio", fake)


def test_ai_bio_generated_then_gate_skips(tmp_path, monkeypatch):
    cfg = _write_config(tmp_path)
    _with_env(monkeypatch, ANTHROPIC_API_KEY="test-key")
    _block_project_ai(monkeypatch)
    calls = []
    _gen(monkeypatch, [("初始简介", True)], calls)
    assert run(cfg, fixture=FIXTURES) == 0
    assert len(calls) == 1
    assert "初始简介" in (tmp_path / "README.md").read_text(encoding="utf-8")
    # 第二次：简介新鲜 + 数据无变 → 增量门整体跳过，不再调用
    assert run(cfg, fixture=FIXTURES) == 0
    assert len(calls) == 1


def test_stale_bio_regenerates_even_when_data_unchanged(tmp_path, monkeypatch):
    cfg = _write_config(tmp_path)
    _with_env(monkeypatch, ANTHROPIC_API_KEY="test-key")
    _block_project_ai(monkeypatch)
    calls = []
    _gen(monkeypatch, [("初始简介", True), ("刷新后的简介", True)], calls)
    run(cfg, fixture=FIXTURES)
    # 把简介时间改老（超过 max_age_days=14）
    snap_path = tmp_path / "data" / "snapshot.json"
    data = json.loads(snap_path.read_text(encoding="utf-8"))
    data["bio"]["generated_at"] = "2000-01-01T00:00:00+00:00"
    snap_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert run(cfg, fixture=FIXTURES) == 0
    assert len(calls) == 2  # 简介过期 → 重新生成
    assert "刷新后的简介" in (tmp_path / "README.md").read_text(encoding="utf-8")


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
    _block_project_ai(monkeypatch)
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


def test_skip_projects_keeps_intros_untouched(tmp_path):
    """只更新 overview：数据变化时 README 更新，但介绍文件保持原样。"""
    cfg = _write_config(tmp_path)
    run(cfg, fixture=FIXTURES)  # 无 AI key：生成数据版介绍
    intro = tmp_path / "docs" / "projects" / "cool-tool.md"
    assert intro.exists()
    before = intro.read_text(encoding="utf-8")
    intro.unlink()  # 删掉一个介绍文件
    assert run(cfg, fixture=FIXTURES, skip_projects=True, force=True) == 0
    assert not intro.exists()  # --skip-projects 不重建
    assert (tmp_path / "README.md").exists()


def test_skip_readme_keeps_readme_untouched(tmp_path):
    """只更新项目介绍：README 不动，介绍文件正常生成。"""
    cfg = _write_config(tmp_path)
    run(cfg, fixture=FIXTURES)
    readme = tmp_path / "README.md"
    mtime = readme.stat().st_mtime_ns
    (tmp_path / "docs" / "projects" / "cool-tool.md").unlink()
    assert run(cfg, fixture=FIXTURES, skip_readme=True, force=True) == 0
    assert readme.stat().st_mtime_ns == mtime  # README 未被重写
    assert (tmp_path / "docs" / "projects" / "cool-tool.md").exists()


def test_projects_include_whitelist(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    as_posix = lambda p: p.as_posix()
    cfg_path.write_text(
        f"""
github:
  username: "alice"
repos:
  exclude: ["alice/old-profile"]
projects:
  include: ["cool-tool"]
readme:
  path: "{as_posix(tmp_path / 'README.md')}"
  template: "{as_posix(TEMPLATE)}"
snapshot:
  path: "{as_posix(tmp_path / 'data' / 'snapshot.json')}"
""",
        encoding="utf-8",
    )
    assert run(cfg_path, fixture=FIXTURES, no_ai=True) == 0
    files = {p.name for p in (tmp_path / "docs" / "projects").iterdir()}
    assert files == {"cool-tool.md"}  # include 白名单外的仓库不生成


# ---- 描述补位 + 逐仓库精细化 ----


def test_project_targets_exclude_profile_repo(tmp_path):
    """profile 仓库自身（用户名/用户名）永不生成项目介绍——它的 README 是工具输出，
    pushed_at 又随每次 bot 提交前进，自引用会让它每轮都过期、无限重生成开 PR。"""
    import profile_agent.cli as cli_mod
    from profile_agent.models import Repo

    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text('github:\n  username: "alice"\n', encoding="utf-8")
    cfg = cli_mod.load_config(cfg_path)
    alice = Repo(name="alice", full_name="alice/alice", description="", stars=0)
    mystery = Repo(name="mystery", full_name="alice/mystery", description="", stars=3)
    featured = [alice, mystery]
    for featured_only in (True, False):
        cfg.projects.featured_only = featured_only
        targets = cli_mod._project_targets([alice, mystery], featured, cfg)
        assert [r.name for r in targets] == ["mystery"], f"featured_only={featured_only}"


def test_short_intro_skips_profile_repo(tmp_path, monkeypatch):
    """补位文案同样跳过 profile 仓库自身（否则它的 README 每变一次就重生成一次）。"""
    import profile_agent.cli as cli_mod

    cfg_path = tmp_path / "config.yaml"
    as_posix = lambda p: p.as_posix()
    cfg_path.write_text(
        f"""
github:
  username: "alice"
projects:
  enabled: false
readme:
  path: "{as_posix(tmp_path / 'README.md')}"
  template: "{as_posix(TEMPLATE)}"
snapshot:
  path: "{as_posix(tmp_path / 'data' / 'snapshot.json')}"
""",
        encoding="utf-8",
    )
    _with_env(monkeypatch, ANTHROPIC_API_KEY="test-key")
    monkeypatch.setattr(cli_mod, "generate_bio", lambda *a, **k: ("简介", True))
    calls = []

    def fake(repo, cfg_ai, **kwargs):
        calls.append(repo.full_name)
        return "一个命令行密码生成器。"

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
projects:
  enabled: false
readme:
  path: "{as_posix(tmp_path / 'README.md')}"
  template: "{as_posix(TEMPLATE)}"
snapshot:
  path: "{as_posix(tmp_path / 'data' / 'snapshot.json')}"
""",
        encoding="utf-8",
    )
    _with_env(monkeypatch, ANTHROPIC_API_KEY="test-key")
    monkeypatch.setattr(cli_mod, "generate_bio", lambda *a, **k: ("简介", True))
    calls = []

    def fake(repo, cfg_ai, **kwargs):
        calls.append(repo.full_name)
        return "一个命令行密码生成器。"

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
projects:
  enabled: false
readme:
  path: "{as_posix(tmp_path / 'README.md')}"
  template: "{as_posix(TEMPLATE)}"
snapshot:
  path: "{as_posix(tmp_path / 'data' / 'snapshot.json')}"
""",
        encoding="utf-8",
    )
    _with_env(monkeypatch, ANTHROPIC_API_KEY="test-key")
    monkeypatch.setattr(cli_mod, "generate_bio", lambda *a, **k: ("简介", True))
    calls = []
    monkeypatch.setattr(
        cli_mod,
        "generate_short_intro",
        lambda *a, **k: calls.append(1) or "不该被调用",
    )
    fx = _empty_desc_fixture(tmp_path)
    assert run(cfg_path, fixture=fx) == 0
    assert calls == []
    assert "（暂无描述）" in (tmp_path / "README.md").read_text(encoding="utf-8")


def test_only_repos_targets_named_repo_even_outside_featured(tmp_path):
    """--repos 精确指定：即使仓库不在精选名单也生成介绍。"""
    cfg_path = tmp_path / "config.yaml"
    as_posix = lambda p: p.as_posix()
    # old-thing 很久没动，默认会被 recency 过滤出精选；用 --repos 强制点名
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
    assert run(cfg_path, fixture=FIXTURES, no_ai=True, only_repos=["old-thing"]) == 0
    files = {p.name for p in (tmp_path / "docs" / "projects").iterdir()}
    assert files == {"old-thing.md"}


def test_only_repos_forces_regenerate_when_unchanged(tmp_path, monkeypatch, capsys):
    """点名 --repos：即使数据/哈希没变也强制重新生成（勾选板 /run 的核心语义）。"""
    import profile_agent.cli as cli_mod
    import profile_agent.projects as projects_mod

    cfg = _write_config(tmp_path)
    _with_env(monkeypatch, ANTHROPIC_API_KEY="test-key")
    versions = []

    def fake_gen(repo, cfg_ai, **kwargs):
        versions.append(repo.name)
        return f"介绍第{len(versions)}版"

    monkeypatch.setattr(projects_mod, "generate_project_intro", fake_gen)
    monkeypatch.setattr(cli_mod, "generate_bio", lambda *a, **k: ("简介", True))
    monkeypatch.setattr(cli_mod, "generate_short_intro", lambda *a, **k: "不该被调用")

    assert run(cfg, fixture=FIXTURES) == 0
    n_first = len(versions)
    assert n_first >= 1
    # 数据没变的普通运行 → 增量门跳过，零调用
    assert run(cfg, fixture=FIXTURES) == 0
    assert len(versions) == n_first
    capsys.readouterr()
    # 点名重跑 cool-tool：哈希没变也重新生成
    assert run(cfg, fixture=FIXTURES, only_repos=["cool-tool"]) == 0
    assert len(versions) == n_first + 1
    assert versions[-1] == "cool-tool"
    body = (tmp_path / "docs" / "projects" / "cool-tool.md").read_text(encoding="utf-8")
    assert "介绍第" in body and "版" in body
    out = capsys.readouterr().out
    assert "已重新生成" in out  # 明细表如实反映强制重跑


def test_run_writes_step_summary_table(tmp_path, monkeypatch):
    """Actions 的 GITHUB_STEP_SUMMARY 存在时，写出逐仓库动作明细表。"""
    cfg_path = tmp_path / "config.yaml"
    as_posix = lambda p: p.as_posix()
    cfg_path.write_text(
        f"""
github:
  username: "alice"
repos:
  exclude: ["alice/old-profile"]
projects:
  include: ["cool-tool"]
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
    assert run(cfg_path, fixture=FIXTURES, no_ai=True) == 0
    text = summary.read_text(encoding="utf-8")
    assert "项目介绍" in text
    assert "cool-tool" in text
    assert "已生成数据版" in text  # 无 AI key → 数据版占位


def test_snapshot_repos_carry_pushed_at(tmp_path):
    cfg = _write_config(tmp_path)
    assert _run(cfg) == 0
    data = json.loads((tmp_path / "data" / "snapshot.json").read_text(encoding="utf-8"))
    by_name = {r["full_name"]: r for r in data["repos"]}
    # 供勾选框标签显示「最近推送日期」用
    assert by_name["alice/cool-tool"]["pushed_at"].startswith("2026-10-01")
