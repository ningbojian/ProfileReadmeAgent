"""readme_block.py 托管区 marker 读写测试（测试计划 MK01-MK22）。

设计 §5.2：首插 H1 之后 / 无 H1 插文件头；替换仅动 marker 区间、区间外逐字节不动；
不平衡（缺一半 / 乱序 / 多对歧义）一律跳过 + 中文警告，绝不猜测修复。
"""

from profile_agent.readme_block import END, START, apply_block, merge_readme, outside_block

AI_TEXT = "一个命令行配置生成器。\n- 支持 A/B 两种模式"
FULL_README = "# 完整文档\n\n第一节内容。\n\n## 第二节\n更多内容。\n"


def _balanced(prefix="# T\n", inner="旧托管区内容", suffix="\n手写后缀。\n"):
    return f"{prefix}{START}\n{inner}\n{END}{suffix}"


# ---- 首次插入（MK01/MK02/MK13/MK14/MK16）----


def test_insert_after_h1_keeps_h1_and_suffix():
    """MK01：有 H1 的 README，marker 区落在 H1 之后。"""
    readme = "# Cool Tool\n手写简介第一段。\n\n## 用法\npip install cool-tool\n"
    out = apply_block(readme, AI_TEXT)
    assert out.startswith("# Cool Tool\n")
    assert out.count(START) == 1 and out.count(END) == 1
    i, j = out.index(START), out.index(END)
    assert i >= len("# Cool Tool\n") and i < j
    assert out.endswith("手写简介第一段。\n\n## 用法\npip install cool-tool\n")
    assert AI_TEXT in out[i:j]
    assert out != AI_TEXT  # 永不整篇替换


def test_insert_without_h1_goes_to_file_head():
    """MK02：无 H1 时插文件头，原文完整保留在 end 之后。"""
    readme = "## 介绍\n一个没有一级标题的文档。\n"
    out = apply_block(readme, AI_TEXT)
    assert out.startswith(START)
    assert out.endswith(readme)
    assert out.count(START) == 1 and out.count(END) == 1
    i, j = out.index(START), out.index(END)
    assert i < j and AI_TEXT in out[i:j]


def test_insert_after_h1_when_badges_before_h1():
    """MK13：徽章在 H1 前，marker 仍插在 H1 之后，徽章原样保留。"""
    readme = '<p align="center"><img src="badge.svg" /></p>\n# Cool Tool\n手写正文。\n'
    out = apply_block(readme, AI_TEXT)
    assert out.startswith('<p align="center"><img src="badge.svg" /></p>\n')
    i = out.index(START)
    h1_end = out.index("# Cool Tool\n") + len("# Cool Tool\n")
    assert i >= h1_end  # 在 H1 行末之后
    assert out.count(START) == 1 and out.count(END) == 1


def test_insert_h1_alone_without_trailing_newline():
    """MK14：仅一行 H1 无尾换行——插入路径允许补单个换行使 marker 独占行（裁定 R-T2）。"""
    readme = "# 仅此一行"
    out = apply_block(readme, AI_TEXT)
    assert out.startswith("# 仅此一行\n")
    assert out.index(START) > out.index("\n")
    assert out.count(START) == 1 and out.count(END) == 1
    assert AI_TEXT in out


def test_insert_takes_first_h1_when_multiple():
    """MK16：多个 H1 取首个（引申，固定实现语义）。"""
    readme = "# 第一节\n内容甲。\n# 第二节\n内容乙。\n"
    out = apply_block(readme, AI_TEXT)
    first_h1_end = out.index("# 第一节\n") + len("# 第一节\n")
    second_h1 = out.index("# 第二节")
    assert first_h1_end <= out.index(START) < second_h1
    assert "# 第一节" in out and "# 第二节" in out


# ---- 替换与字节保真（MK03/MK04/MK05/MK06/MK12/MK15/MK17）----


def test_replace_is_idempotent():
    """MK03：同一内容连续两次写回输出字节相同。"""
    readme = _balanced()
    out1 = apply_block(readme, AI_TEXT)
    out2 = apply_block(out1, AI_TEXT)
    assert out1 == out2
    assert "旧托管区内容" not in out1
    assert out1.count(START) == 1 and out1.count(END) == 1


def test_replace_keeps_prefix_and_suffix_bytes():
    """MK04：区间外前缀后缀逐字节不动（含非 ASCII）。"""
    prefix = "前缀 é中文 🎉\n\n## 章节\n"
    suffix = "\n后缀章节 é中文。\n"
    readme = _balanced(prefix=prefix, inner="旧内容 AAA", suffix=suffix)
    out = apply_block(readme, AI_TEXT)
    assert out.startswith(prefix)
    assert out.endswith(suffix)
    assert "旧内容 AAA" not in out
    assert AI_TEXT in out


def test_replace_eof_without_trailing_newline_stays_without():
    """MK05：以 end marker 结尾无换行时输出仍无换行。"""
    readme = "# T\n" + START + "\n旧\n" + END
    out = apply_block(readme, AI_TEXT)
    assert not out.endswith("\n")
    assert out.endswith(END)
    assert out.startswith("# T\n")


def test_replace_keeps_inline_text_after_end_marker():
    """MK06：end marker 行内后随手写文字时同行文字保留。"""
    readme = "# T\n" + START + "\n旧\n" + END + " 同行尾注（手写）\n"
    out = apply_block(readme, AI_TEXT)
    assert out.endswith(END + " 同行尾注（手写）\n")
    assert out.startswith("# T\n")
    assert out.count(START) == 1 and out.count(END) == 1


def test_output_has_exactly_one_ordered_pair():
    """MK12：写回结果恰含一对 start/end 且顺序正确（首插与替换两入口）。"""
    inserted = apply_block("# T\n手写。\n", AI_TEXT)
    replaced = apply_block(_balanced(), AI_TEXT)
    for out in (inserted, replaced):
        assert out.count(START) == 1 and out.count(END) == 1
        assert out.index(START) < out.index(END)
        assert AI_TEXT in out[out.index(START):out.index(END)]


def test_noop_when_region_already_current():
    """MK15：区间内容已是最新 → 输出与输入逐字节相同。"""
    readme = f"# T\n{START}\n{AI_TEXT}\n{END}\n手写。\n"
    out = apply_block(readme, AI_TEXT)
    assert out == readme


def test_crlf_outside_region_preserved():
    """MK17：CRLF 行尾的区间外回车换行原样保留。"""
    readme = "# T\r\n" + START + "\r\n旧\r\n" + END + "\r\n手写。\r\n"
    out = apply_block(readme, AI_TEXT)
    assert out.startswith("# T\r\n")
    assert out.endswith("\r\n手写。\r\n")
    assert "旧\r\n" not in out
    assert out.count(START) == 1 and out.count(END) == 1


# ---- 不平衡保护（MK07/MK08/MK09/MK22）----


def test_missing_end_marker_skips_with_chinese_warning(capsys):
    """MK07：缺 end → 跳过 + 中文警告，不抛异常。"""
    readme = "# T\n" + START + "\n旧\n手写继续。\n"
    out = apply_block(readme, AI_TEXT)
    assert out is None
    err = capsys.readouterr().err
    assert "marker" in err and "跳过" in err
    assert "不平衡" in err or "不完整" in err or "歧义" in err


def test_missing_start_marker_skips_with_warning(capsys):
    """MK08：缺 start → 同样跳过并警告。"""
    readme = "# T\n旧\n" + END + "\n"
    assert apply_block(readme, AI_TEXT) is None
    assert "跳过" in capsys.readouterr().err


def test_reversed_markers_skip_with_warning(capsys):
    """MK09：end 在 start 前（乱序）→ 按不平衡跳过，不尝试交换修复。"""
    readme = "# T\n" + END + "\n旧\n" + START + "\n"
    assert apply_block(readme, AI_TEXT) is None
    assert "跳过" in capsys.readouterr().err


def test_two_complete_pairs_skip_with_warning(capsys):
    """MK22：两对完整 marker（歧义）→ 保守跳过（引申，「绝不猜测修复」精神）。"""
    readme = _balanced() + _balanced(prefix="## 又一段\n", inner="另一旧区")
    assert apply_block(readme, AI_TEXT) is None
    assert "跳过" in capsys.readouterr().err


# ---- 字面匹配限制与清洗（MK18/MK21）----


def test_marker_literals_in_ai_text_sanitized():
    """MK18：AI 正文混入 marker 字面 → 写出后仍恰一对平衡 marker（引申）。"""
    dirty = "说明\n" + END + "\n更多内容\n" + START
    out = apply_block("# T\n手写。\n", dirty)
    assert out.count(START) == 1 and out.count(END) == 1
    assert out.index(START) < out.index(END)
    assert "说明" in out and "更多内容" in out
    # 读回判为平衡：再次替换不触发跳过
    assert apply_block(out, AI_TEXT) is not None


def test_marker_literals_in_code_fence_matched_literally():
    """MK21：代码块内出现 marker 字面按字面匹配语义（引申，固定限制）。"""
    readme = (
        "# T\n手写前。\n\n```html\n" + START + "\n旧示例\n" + END + "\n```\n\n手写后。\n"
    )
    out = apply_block(readme, AI_TEXT)
    assert out.count(START) == 1 and out.count(END) == 1
    assert AI_TEXT in out
    assert "手写前。" in out and "手写后。" in out and "```html" in out


# ---- mode=full 分支（MK10/MK19/MK20）----


def test_missing_readme_generates_full_without_markers():
    """MK10：无 README → mode=full 整份生成，不强加 marker。"""
    content, mode = merge_readme("", FULL_README)
    assert content == FULL_README
    assert mode == "full"
    assert START not in content and END not in content


def test_empty_readme_same_as_missing():
    """MK20：空 README（0 字节）与缺 README 同语义走 full（引申）。"""
    content, mode = merge_readme("", FULL_README)
    assert (content, mode) == (FULL_README, "full")


def test_full_mode_next_round_replaces_block_only():
    """MK19：mode=full 记录后 README 已存在 → 下轮只替换区间（分支依据是存在性）。"""
    full_text, _ = merge_readme("", FULL_README)
    out, mode = merge_readme(full_text, AI_TEXT)
    assert mode == "block"
    assert out != AI_TEXT
    assert "第一节内容。" in out and "## 第二节" in out  # 区外整份旧内容保留
    assert START in out and END in out


def test_never_rewrite_full_readme():
    """MK11：有手写 README 永不整篇重写。"""
    readme = "# 工具\n\n手写贡献指南在这里。\n\n## 用法\npip install x\n"
    out, mode = merge_readme(readme, AI_TEXT)
    assert mode == "block"
    assert out != AI_TEXT
    assert "手写贡献指南在这里。" in out and "## 用法" in out
    assert out.count(START) == 1 and out.count(END) == 1


# ---- 托管区外的手写内容提取（语言提醒用，2026-10-25）----


def test_outside_block_returns_only_handwritten_text():
    """只取 marker 之外的手写内容，托管区正文不参与语言判定。"""
    readme = (
        "# T\nHandwritten preface in prose.\n"
        + START
        + "\n托管区正文\n"
        + END
        + "\nHandwritten tail in prose.\n"
    )
    outside = outside_block(readme)
    assert "托管区正文" not in outside
    assert "Handwritten preface" in outside and "Handwritten tail" in outside


def test_outside_block_without_markers_returns_whole_text():
    """没有 marker = 全部都是手写（首次接管前，整份 README 都要检查）。"""
    readme = "# T\n全部手写内容。\n"
    assert outside_block(readme) == readme


def test_outside_block_tolerates_unbalanced_markers():
    """marker 缺一半时也不能把托管区残留当手写内容误报。"""
    readme = "# T\n手写前言。\n" + START + "\n缺少 end 的半截内容\n"
    outside = outside_block(readme)
    assert START not in outside and "缺少 end 的半截内容" not in outside
    assert "手写前言" in outside
