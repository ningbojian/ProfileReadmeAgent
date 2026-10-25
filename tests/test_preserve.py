"""WP5 Overview 保留原内容：custom 标记区提取 + 生成区行级三路合并。

纯函数（preserve.py）+ cli 集成（手改保留/删除恢复/custom 永久保留/升级首跑一次性兜底）。
设计锁定规则：
1. theirs 相对 base 修改、ours 未动该处 → 保留 theirs（用户手改）
2. ours 与 theirs 都动过同一处 → ours（数据为真）
3. theirs 删除而 ours 保留 → 恢复 ours（生成区删除无效，防抖动；想隐藏用 config）
4. 其余差异 → ours
"""

import json
import shutil

from profile_agent.preserve import extract_custom, merge_readme_3way
from profile_agent.cli import run

from test_cli import FIXTURES, TEMPLATE, _write_config  # noqa: F401

BASE = (
    "<!-- 此 README 由 profile-agent 自动生成 -->\n"
    "# 👋 你好，我是 Alice Zhang\n"
    "\n"
    "| 仓库 | Stars |\n"
    "| --- | --- |\n"
    "| cool-tool | 120 |\n"
)


# ============================ extract_custom 四模式 ============================


def test_extract_custom_preserved_returns_body_between_markers():
    existing = (
        BASE
        + "<!-- profile-agent:custom:start -->\n## 我的工具箱\n<!-- profile-agent:custom:end -->\n"
    )
    body, mode = extract_custom(existing)
    assert mode == "preserved"
    assert body == "## 我的工具箱"


def test_extract_custom_take_over_for_handwritten_no_header():
    """无头无锚点的手写 README → 整份成为 custom（首次接管，不丢内容）。"""
    body, mode = extract_custom("# 手写的 README\n随便写点东西")
    assert mode == "take_over"
    assert body == "# 手写的 README\n随便写点东西"


def test_extract_custom_machine_for_header_without_anchors():
    """有头无锚点 → 全机器文件，custom 空。"""
    body, mode = extract_custom(BASE)
    assert mode == "machine"
    assert body == ""


def test_extract_custom_broken_for_unbalanced_anchors():
    """锚点不平衡 → broken（防自我复制），custom 空。"""
    existing = BASE + "<!-- profile-agent:custom:start -->\n内容\n"
    body, mode = extract_custom(existing)
    assert mode == "broken"
    assert body == ""


def test_extract_custom_empty_is_machine_not_take_over():
    """空串/不存在 → machine 特判（不落 take_over，日志语义正确）。"""
    assert extract_custom("") == ("", "machine")


# ============================ merge_readme_3way 四规则 ============================


def test_merge_3way_tweak_survives_unrelated_data_change():
    """手改问候语 + star 行数据变化：手改保留、star 更新（规则 1+4）。"""
    theirs = BASE.replace("# 👋 你好，我是", "# 👋 嗨，我是")
    ours = BASE.replace("| cool-tool | 120 |", "| cool-tool | 999 |")
    merged = merge_readme_3way(BASE, theirs, ours)
    assert "# 👋 嗨，我是 Alice Zhang" in merged
    assert "| cool-tool | 999 |" in merged
    assert "| cool-tool | 120 |" not in merged


def test_merge_3way_conflict_favors_ours():
    """同一行双方都改 → 以新渲染为准（数据为真，规则 2）。"""
    theirs = BASE.replace("| 120 |", "| 5 |")
    ours = BASE.replace("| 120 |", "| 999 |")
    merged = merge_readme_3way(BASE, theirs, ours)
    assert "| cool-tool | 999 |" in merged
    assert "| 5 |" not in merged


def test_merge_3way_deleted_generated_section_restored():
    """用户删除生成区内容 → 恢复（规则 3，防抖动）。"""
    theirs = BASE.replace("| cool-tool | 120 |\n", "")
    merged = merge_readme_3way(BASE, theirs, BASE)
    assert "| cool-tool | 120 |" in merged


def test_merge_3way_shrinking_replace_restored():
    """缩行替换（整块删除常被 SequenceMatcher 对齐成缩行 replace）→ 同样恢复 ours。

    WP6 面板锚点块删除回归：不拦缩行 replace 会把面板块当「用户修改」吞掉。
    """
    lines = BASE.splitlines(keepends=True)
    theirs = "".join(lines[:2] + lines[7:])  # 删掉表格块（5 行）→ 缩行
    merged = merge_readme_3way(BASE, theirs, BASE)
    assert "| cool-tool | 120 |" in merged


def test_merge_3way_user_insert_appended_line_kept():
    """用户在文件尾追加说明 → 保留（规则 1 插入分支）。"""
    theirs = BASE + "\n用户追加的说明\n"
    merged = merge_readme_3way(BASE, theirs, BASE)
    assert merged.endswith("用户追加的说明\n")


def test_merge_3way_second_run_stable_no_flap():
    """回环稳定：合并结果作盘上内容再合并一次（数据未变）→ 恒等，零抖动。"""
    theirs = BASE.replace("# 👋 你好，我是", "# 👋 嗨，我是")
    m1 = merge_readme_3way(BASE, theirs, BASE)
    assert m1 == theirs
    m2 = merge_readme_3way(BASE, m1, BASE)
    assert m2 == m1


def test_merge_3way_empty_base_returns_ours():
    """无 base（异常兜底）→ 以新渲染为准。"""
    assert merge_readme_3way("", BASE, "# 新渲染\n") == "# 新渲染\n"


# ============================ cli 集成 ============================


def _prepare(tmp_path):
    """复制 fixture 到临时目录并完成首次运行（返回 cfg 与 fixture 目录）。"""
    cfg = _write_config(tmp_path)
    fx = tmp_path / "fx"
    shutil.copytree(FIXTURES, fx)
    assert run(cfg, fixture=fx, no_ai=True) == 0
    return cfg, fx


def _bump_cool_tool_stars(fx, stars: int):
    repos = json.loads((fx / "repos.json").read_text(encoding="utf-8"))
    for r in repos:
        if r["full_name"] == "alice/cool-tool":
            r["stargazers_count"] = stars
    (fx / "repos.json").write_text(json.dumps(repos, ensure_ascii=False), encoding="utf-8")


def test_generated_region_tweak_survives_star_change(tmp_path):
    """端到端：手改生成区问候语 + star 数据变化 → 手改保留、star 行更新。"""
    cfg, fx = _prepare(tmp_path)
    readme = tmp_path / "README.md"
    text = readme.read_text(encoding="utf-8")
    tweaked = text.replace("# 👋 你好，我是 Alice Zhang", "# 👋 嗨，我是 Alice Zhang")
    assert tweaked != text  # 手改确实生效
    readme.write_text(tweaked, encoding="utf-8")
    _bump_cool_tool_stars(fx, 999)
    assert run(cfg, fixture=fx, no_ai=True) == 0
    out = readme.read_text(encoding="utf-8")
    assert "# 👋 嗨，我是 Alice Zhang" in out  # 规则 1：手改保留
    assert "| 999 |" in out  # 数据为真：star 更新
    # 快照记录的是「本轮渲染」而非合并写入（base 语义，防下轮手改被抹）
    data = json.loads((tmp_path / "data" / "snapshot.json").read_text(encoding="utf-8"))
    assert data["readme_last"].startswith("<!--")


def test_custom_content_survives_data_change_rerender(tmp_path):
    """custom 区内容在数据变化重渲染后永久保留（marker 提取 + 渲染回注）。"""
    cfg, fx = _prepare(tmp_path)
    readme = tmp_path / "README.md"
    text = readme.read_text(encoding="utf-8")
    customized = text.replace(
        "<!-- profile-agent:custom:start -->\n<!-- profile-agent:custom:end -->",
        "<!-- profile-agent:custom:start -->\n## 🧰 我的工具箱\n- 自定义内容\n<!-- profile-agent:custom:end -->",
    )
    assert customized != text  # custom 写入确实生效
    readme.write_text(customized, encoding="utf-8")
    _bump_cool_tool_stars(fx, 999)
    assert run(cfg, fixture=fx, no_ai=True) == 0
    out = readme.read_text(encoding="utf-8")
    assert "## 🧰 我的工具箱" in out
    assert "- 自定义内容" in out
    assert "| 999 |" in out


def test_en_custom_independent_from_zh(tmp_path):
    """en 文件的 custom 只从 en 文件提取：zh 不沾染、en 内容保留。"""
    cfg, fx = _prepare(tmp_path)
    en_readme = tmp_path / "README.en.md"
    text = en_readme.read_text(encoding="utf-8")
    customized = text.replace(
        "<!-- profile-agent:custom:start -->\n<!-- profile-agent:custom:end -->",
        "<!-- profile-agent:custom:start -->\n## My Toolbox\n- en custom\n<!-- profile-agent:custom:end -->",
    )
    assert customized != text
    en_readme.write_text(customized, encoding="utf-8")
    _bump_cool_tool_stars(fx, 999)
    assert run(cfg, fixture=fx, no_ai=True) == 0
    en_out = en_readme.read_text(encoding="utf-8")
    zh_out = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "## My Toolbox" in en_out and "- en custom" in en_out
    assert "My Toolbox" not in zh_out


def test_upgrade_first_run_base_fallback_one_time_stomp(tmp_path):
    """升级首跑：无 readme_last → base 兜底取盘上，该轮生成区以新渲染为准（一次性）。"""
    cfg = _write_config(tmp_path)
    readme = tmp_path / "README.md"
    # 盘上是机器文件但问候语被手改过，且尚无快照（升级场景）
    readme.write_text(
        "<!-- 此 README 由 profile-agent 自动生成 -->\n# 👋 嗨，我是手改的\n",
        encoding="utf-8",
    )
    assert run(cfg, fixture=FIXTURES, no_ai=True) == 0
    out = readme.read_text(encoding="utf-8")
    assert "你好，我是 Alice Zhang" in out  # 该轮以新渲染为准
    assert "手改的" not in out


def test_merge_3way_desc_translation_lands_and_conflict_favors_ours():
    """A-WP-B：译文更新 theirs==base → 取 ours（英译落盘）；手改同撞 ours → ours（数据为真）。"""
    zh_line = "| cool-tool | 一个很酷的工具 |\n"
    en_line = "| cool-tool | A cool tool |\n"
    base = BASE + zh_line
    # theirs 未动、ours 换成英译 → 英译落地
    assert merge_readme_3way(base, base, BASE + en_line) == BASE + en_line
    # 用户手改了同一行、ours 也改了（英译更新）→ ours 覆盖手改
    theirs = BASE + "| cool-tool | 用户手改 |\n"
    assert merge_readme_3way(base, theirs, BASE + en_line) == BASE + en_line
