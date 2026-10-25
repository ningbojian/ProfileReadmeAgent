"""主页嵌入块（auto 模式）：在第一个真实标题后插/更新一个 AI 简介块。"""

from profile_agent.readme_block import START, END, block_body, strip_block, upsert_block

BODY = "## 🤖 AI 简介\n\n我是一名开发者。"


def test_inserts_block_after_first_h1_and_keeps_everything_else():
    """图片/HTML/表格原样保留——块之外逐字节相同。"""
    original = (
        '<p align="center"><img src="avatar.png" width="120"></p>\n'
        "\n"
        "# Alice\n"
        "\n"
        "手写介绍，**请保留**。\n"
        "\n"
        "<details><summary>更多</summary>\n\n隐藏内容\n\n</details>\n"
    )
    out = upsert_block(original, BODY)
    assert out == (
        '<p align="center"><img src="avatar.png" width="120"></p>\n'
        "\n"
        "# Alice\n"
        "\n"
        f"{START}\n{BODY}\n{END}\n"
        "\n"
        "手写介绍，**请保留**。\n"
        "\n"
        "<details><summary>更多</summary>\n\n隐藏内容\n\n</details>\n"
    )


def test_fenced_fake_heading_is_ignored():
    """代码块里的 `# 注释` 不是标题：插到真正的 H1 之后。"""
    original = "```bash\n# 这不是标题\n```\n\n# Alice\n\n正文\n"
    assert upsert_block(original, BODY) == (
        "```bash\n# 这不是标题\n```\n\n# Alice\n\n"
        f"{START}\n{BODY}\n{END}\n\n正文\n"
    )


def test_heading_inside_multiline_comment_is_ignored():
    original = "<!--\n# 注释里的假标题\n-->\n\n# Alice\n\n正文\n"
    out = upsert_block(original, BODY)
    assert out.index(START) > out.index("# Alice")
    assert out.index(START) > out.index("-->")


def test_heading_inside_details_is_ignored():
    """<details> 里的标题默认折叠：插进去等于看不见。"""
    original = (
        "<details>\n<summary>展开</summary>\n\n# 折叠标题\n\n</details>\n\n"
        "# Alice\n\n正文\n"
    )
    out = upsert_block(original, BODY)
    assert out.index(START) > out.index("# Alice")


def test_html_h1_single_line():
    original = '<h1 align="center">Alice</h1>\n\n正文\n'
    assert upsert_block(original, BODY) == (
        '<h1 align="center">Alice</h1>\n\n'
        f"{START}\n{BODY}\n{END}\n\n正文\n"
    )


def test_html_h1_spanning_lines_inserts_after_closing_tag():
    original = '<h1 align="center">\n  Alice\n</h1>\n\n正文\n'
    out = upsert_block(original, BODY)
    assert out.index(START) > out.index("</h1>")
    assert out.startswith('<h1 align="center">\n  Alice\n</h1>\n')


def test_setext_heading():
    original = "Alice\n=====\n\n正文\n"
    out = upsert_block(original, BODY)
    assert out.index(START) > out.index("=====")


def test_no_heading_appends_at_end_instead_of_file_head():
    """无标题（如只有 front matter）时追加到文末：插文件头会破坏 front matter。"""
    original = "---\ntitle: Alice\n---\n\n正文\n"
    out = upsert_block(original, BODY)
    assert out.startswith("---\ntitle: Alice\n---\n\n正文\n")
    assert out.rstrip().endswith(END)


def test_existing_block_is_updated_in_place_and_outside_bytes_kept():
    original = (
        "# Alice\n\n"
        f"{START}\n## 旧标题\n\n旧正文\n{END}\n\n"
        "手写内容\n"
    )
    out = upsert_block(original, BODY)
    assert out == (
        "# Alice\n\n" f"{START}\n{BODY}\n{END}\n\n" "手写内容\n"
    )


def test_broken_markers_are_skipped_with_warning(capsys):
    original = f"# Alice\n\n{START}\n没有结束标记\n"
    assert upsert_block(original, BODY) is None
    assert "marker 不平衡" in capsys.readouterr().err


def test_block_body_reads_balanced_region():
    text = f"# Alice\n\n{START}\n{BODY}\n{END}\n"
    assert block_body(text) == BODY


def test_block_body_returns_none_without_markers():
    assert block_body("# Alice\n") is None


def test_strip_block_removes_balanced_block_only():
    text = f"# Alice\n\n{START}\n{BODY}\n{END}\n\n正文\n"
    assert strip_block(text) == "# Alice\n\n正文\n"


def test_strip_block_leaves_broken_markers_alone():
    text = f"# Alice\n\n{END}\n"
    assert strip_block(text) == text
