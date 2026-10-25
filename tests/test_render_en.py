"""render.py 双语渲染测试（WP3：单模板 + labels 字典，lang 参数切换语言）。"""

from test_render import _ctx

from profile_agent.render import render_readme

TEMPLATE = _ctx.__globals__["TEMPLATE"]


def test_render_en_sections_headers_and_badges():
    """英文版：标题/分区/表头/统计徽章 label 全英文；中文分区词绝迹（宁可英文占位
    也不把中文塞进 en 文件）。"""
    out = render_readme(_ctx(), TEMPLATE, lang="en")
    assert "# 👋 Hi, I'm Alice Zhang" in out
    assert "## 📊 Statistics" in out
    assert "## 🏷️ Tech Stack" in out
    assert "## 📦 Repositories" in out
    assert "## ℹ️ Notes" in out
    assert "| Repo | Description | Stars | Lang |" in out
    assert "Public%20repos-42-" in out  # 统计徽章 label 英文化（URL 编码空格）
    assert "Total%20stars-230-" in out  # en label = "Total stars"（正常英文大小写）
    assert "统计信息" not in out and "仓库列表" not in out
    # 仓库内容数据照常展示；display_descs 为空时照搬原文（A-WP-B：英译由 cli 侧注入）
    assert "| [cool-tool](https://github.com/alice/cool-tool) | 一个很酷的工具 | 120 |" in out


def test_render_switch_line_mutual_links_in_both_languages():
    """顶部切换行：两文件同一行互指（🇨🇳 中文 · 🇺🇸 English），且位于自动头注释之后
    （test_render.py:69 startswith("<!--") 锁定）。"""
    zh = render_readme(_ctx(), TEMPLATE)
    en = render_readme(_ctx(), TEMPLATE, lang="en")
    switch = "🇨🇳 [中文](./README.md) · 🇺🇸 [English](./README.en.md)"
    assert switch in zh and switch in en
    assert zh.lstrip().startswith("<!--")  # 注释仍打头
    assert zh.index("<!--") < zh.index(switch)  # 切换行在注释之后


def test_render_en_bio_placeholder_and_no_chinese_placeholder():
    """en 生物简介为空 → 英文占位注释（指 key_env），绝不出现中文占位文案。"""
    ctx = _ctx()
    ctx.bio_text = ""  # WP4 前 en 简介来源 bio_text_en 恒空
    out = render_readme(ctx, TEMPLATE, lang="en")
    assert "AI bio unavailable" in out
    assert "ANTHROPIC_API_KEY" in out
    assert "AI 简介暂不可用" not in out


def test_render_zh_labels_keep_exact_existing_wording():
    """zh 回归网：labels 化后中文输出与旧版逐字一致（分区词/徽章 label/表头）。"""
    out = render_readme(_ctx(), TEMPLATE)
    assert "## 📊 统计信息" in out
    assert "## 🏷️ 技术标签" in out
    assert "## 📦 仓库列表" in out
    assert "## ℹ️ 说明备注" in out
    assert "| 仓库 | 简介 | Stars | 语言 |" in out
    assert "公开仓库" in out and "总 Star" in out
    assert "本页面由 profile-agent 自动生成" in out


def test_header_b_has_no_panel_mention():
    """A-WP2 有意变更：en 头注释不再提 pin panel（新文案锁字）。"""
    out = render_readme(_ctx(), TEMPLATE, lang="en")
    assert (
        "Editable areas — content between the custom markers — are always preserved."
        in out
    )
    assert "pin panel" not in out


def test_en_output_has_no_pin_pointer():
    """A-WP2：即使 ctx 侧带旧面板文本，en 文件也不再渲染指回中文面板的指针行。"""
    ctx = _ctx()
    ctx.pin_panel = "<details>旧面板</details> 模拟升级前残留的面板上下文"
    out = render_readme(ctx, TEMPLATE, lang="en")
    assert "Pin configuration lives" not in out


def test_en_display_descs_translation_overrides_table():
    """A-WP-B：display_descs 命中的仓库，en 表格用英译替换中文原文。"""
    ctx = _ctx()
    ctx.display_descs = {"alice/cool-tool": "A cool tool"}
    out = render_readme(ctx, TEMPLATE, lang="en")
    assert "| [cool-tool](https://github.com/alice/cool-tool) | A cool tool | 120 |" in out
    assert "一个很酷的工具" not in out  # 英译命中后原文不得再出现
    # 未命中的仓库照搬原文（回落链：display_descs → description → short_intros）
    assert "网页应用" in out


def test_en_display_descs_translation_overrides_pinned_quote():
    """A-WP-B：置顶卡片引用块同样优先英译。"""
    ctx = _ctx()
    ctx.pinned = [ctx.featured[0]]
    ctx.display_descs = {"alice/cool-tool": "A cool tool"}
    out = render_readme(ctx, TEMPLATE, lang="en")
    assert "> A cool tool" in out
    assert "一个很酷的工具" not in out


def test_en_display_descs_pipe_escaped():
    """A-WP-B：英译里的 | 同样走表格转义（沿用既有 replace 链）。"""
    ctx = _ctx()
    ctx.display_descs = {"alice/cool-tool": "Cool|Tool"}
    out = render_readme(ctx, TEMPLATE, lang="en")
    assert "Cool \\| Tool" in out
