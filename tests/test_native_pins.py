"""A-WP3：置顶跟随 GitHub 原生 Pinned（cli 级 fixture：可选 pinned.json）。

fixture 模式语义：存在 pinned.json = 读取成功（内容即官方置顶序，[] = 成功清空）；
缺文件 = 读取失败（None）→ 走三段阶梯回退。默认 fixtures 无此文件，
所有存量 cli 测试天然落 config 回退路径（零改动保持绿）。
"""

import json

from test_cli import FIXTURES, _run, _write_config


def _fixture_with_pins(tmp_path, pins=None):
    """拷贝基线 fixture 到临时目录；pins 传列表写 pinned.json，传 None 不写（模拟读取失败）。"""
    fx = tmp_path / "fixture"
    fx.mkdir()
    for name in ("user.json", "repos.json"):
        (fx / name).write_text(
            (FIXTURES / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    if pins is not None:
        (fx / "pinned.json").write_text(json.dumps(pins), encoding="utf-8")
    return fx


def test_native_pinned_json_drives_section_order(tmp_path):
    """pinned.json 的官方顺序进 📌 置顶板块，📦 表格不重复置顶仓库。"""
    cfg = _write_config(tmp_path)
    fx = _fixture_with_pins(tmp_path, ["alice/old-thing", "alice/cool-tool"])
    assert _run(cfg, fixture=fx) == 0
    readme = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "## 📌 置顶项目" in readme
    # 官方顺序：old-thing 在 cool-tool 之前
    assert readme.index("### ⭐ [old-thing]") < readme.index("### ⭐ [cool-tool]")
    assert "| [old-thing](" not in readme  # 表格不重复
    assert "| [cool-tool](" not in readme


def test_native_empty_pins_beats_config_pin(tmp_path):
    """success-empty（用户清空置顶）是成功语义：不回退 config 种子，不出 📌 板块。"""
    cfg = _write_config(tmp_path)
    with cfg.open("a", encoding="utf-8") as fh:
        fh.write('featured:\n  pin: ["alice/old-thing"]\n')
    fx = _fixture_with_pins(tmp_path, [])
    assert _run(cfg, fixture=fx) == 0
    readme = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "## 📌" not in readme
    assert "| [old-thing](" in readme  # 未置顶 → 正常进表格


def test_missing_pinned_json_falls_back_to_config_pin(tmp_path):
    """读取失败（fixture 缺 pinned.json）且无快照 → config 种子生效（离线种子语义）。"""
    cfg = _write_config(tmp_path)
    with cfg.open("a", encoding="utf-8") as fh:
        fh.write('featured:\n  pin: ["alice/old-thing"]\n')
    fx = _fixture_with_pins(tmp_path, pins=None)
    assert _run(cfg, fixture=fx) == 0
    readme = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "### ⭐ [old-thing](https://github.com/alice/old-thing)" in readme


def test_pinned_change_opens_gate_then_noop(tmp_path):
    """改 pinned.json → 指纹开门重排（0 AI）；其后数据无变化连续 noop。"""
    cfg = _write_config(tmp_path)
    fx = _fixture_with_pins(tmp_path, ["alice/old-thing"])
    assert _run(cfg, fixture=fx) == 0
    readme = tmp_path / "README.md"
    assert "### ⭐ [old-thing]" in readme.read_text(encoding="utf-8")
    # 改置顶：开门 → 重排
    (fx / "pinned.json").write_text(json.dumps(["alice/cool-tool"]), encoding="utf-8")
    assert _run(cfg, fixture=fx) == 0
    out = readme.read_text(encoding="utf-8")
    assert "### ⭐ [cool-tool]" in out
    assert "### ⭐ [old-thing]" not in out
    # 其后连续两轮 noop（mtime 不变）
    mtime = readme.stat().st_mtime_ns
    assert _run(cfg, fixture=fx) == 0
    assert _run(cfg, fixture=fx) == 0
    assert readme.stat().st_mtime_ns == mtime


def test_pin_to_excluded_repo_silently_dropped(tmp_path):
    """置顶指向被 exclude 的仓库：静默丢弃（不报错、不出板块）。"""
    cfg = _write_config(tmp_path)  # 配置里 exclude: ["alice/old-profile"]
    fx = _fixture_with_pins(tmp_path, ["alice/old-profile"])
    assert _run(cfg, fixture=fx) == 0
    readme = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "## 📌" not in readme
    assert "old-profile" not in readme


def test_native_pins_failure_falls_back_to_prev_pins(tmp_path):
    """读取失败 → 沿用快照 pins（防闪断）：README 不变、noop、快照名单原样。"""
    cfg = _write_config(tmp_path)
    fx = _fixture_with_pins(tmp_path, ["alice/old-thing"])
    assert _run(cfg, fixture=fx) == 0
    readme = tmp_path / "README.md"
    snap = tmp_path / "data" / "snapshot.json"
    mtime = readme.stat().st_mtime_ns
    # 删 pinned.json = 本轮读取失败 → prev.pins 沿用 → 输出完全一致 → noop
    (fx / "pinned.json").unlink()
    assert _run(cfg, fixture=fx) == 0
    assert readme.stat().st_mtime_ns == mtime  # 未重写（指纹不变、内容不变）
    data = json.loads(snap.read_text(encoding="utf-8"))
    assert data["pins"] == ["alice/old-thing"]  # 快照名单原样，下轮继续回退
    assert "### ⭐ [old-thing]" in readme.read_text(encoding="utf-8")
