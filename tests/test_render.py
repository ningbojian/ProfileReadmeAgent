"""render.py 模板渲染与写入测试。

2026-10-23 README 视觉改版规范（用户原话要点）：
年轻开发者极简风；# / ## / ### 层级不跳跃；板块用 --- 分割线；
一个板块固定一个 emoji（📌置顶 📦列表 🏷️标签 📊统计 ℹ️说明）；
徽章统一 shields.io、圆角、低饱和、同行横排；
置顶卡片 = ⭐ 名称｜徽章 + > 一句话摘要 + 亮点 ≤3 条；
普通仓库用对齐表格；提示块用 GitHub 原生 alert 且一页 ≤3 个。
"""

import dataclasses
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from profile_agent.models import Repo, Sections, TemplateContext, Totals
from profile_agent.render import render_readme, write_if_changed

TEMPLATE = Path(__file__).parent.parent / "templates" / "profile.md.j2"

# 视觉规范锁定的低饱和配色（大红大亮的 3776AB/f7df1e 已废弃）
_SOFT = "8aa6c4"  # 统计徽章主色（灰蓝）


def _ctx() -> TemplateContext:
    return TemplateContext(
        username="alice",
        display_name="Alice Zhang",
        blog="https://alice.dev",
        totals=Totals(
            public_repos=42,
            followers=128,
            total_stars=230,
            top_languages=[("Python", 120), ("JavaScript", 80)],
        ),
        featured=[
            Repo(
                name="cool-tool",
                full_name="alice/cool-tool",
                description="一个很酷的工具",
                language="Python",
                stars=120,
                forks=12,
                pushed_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
                html_url="https://github.com/alice/cool-tool",
            ),
            Repo(
                name="web-app",
                full_name="alice/web-app",
                description="网页应用",
                language="JavaScript",
                stars=80,
                forks=4,
                pushed_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
                html_url="https://github.com/alice/web-app",
            ),
        ],
        bio_text="你好，我是 Alice，一名热爱开源的工程师。",
        cards_theme="github_dark",
        cards_show=["stats", "top_langs", "streak"],
        include_update_time=True,
        updated_at="2026-10-21 10:23",
    )


def test_render_contains_chinese_sections():
    """板块结构：五个固定 emoji 分区、--- 分割线、默认无置顶板块。"""
    out = render_readme(_ctx(), TEMPLATE)
    assert out.lstrip().startswith("<!--")  # 自动生成标记
    assert "# 👋 你好，我是 Alice Zhang" in out
    assert "## 📊 统计信息" in out
    assert "## 🏷️ 技术标签" in out
    assert "## 📦 仓库列表" in out
    assert "## ℹ️ 说明备注" in out
    assert "📌" not in out  # 默认无置顶 → 不出该板块
    assert "你好，我是 Alice，一名热爱开源的工程师。" in out
    # 板块之间用 --- 分割线隔开（统计/标签/列表/说明至少 3 条）
    assert out.count("\n---\n") >= 3


def test_render_section_emoji_used_once_no_pile():
    """一个板块固定一个 emoji，不乱堆：各区图标恰出现一次，旧 emoji 已清除。"""
    out = render_readme(_ctx(), TEMPLATE)
    for e in ("📊", "🏷️", "📦", "ℹ️"):
        assert out.count(e) == 1, e
    assert "🌟" not in out  # 旧「我的项目」板块
    assert "🐍" not in out  # 语言 emoji 已废弃
    assert "⏱️" not in out  # 旧页脚


def test_render_stats_badge_row_replaces_table():
    """📊 统计信息：三枚统计徽章同行横排（rounded、低饱和），不再是纯文字表格。"""
    out = render_readme(_ctx(), TEMPLATE)
    assert (
        f"https://img.shields.io/badge/{quote('公开仓库', safe='')}-42-{_SOFT}?style=flat"
        in out
    )
    assert f"badge/Followers-128-{_SOFT}?style=flat" in out
    assert f"badge/{quote('总 Star', safe='')}-230-{_SOFT}?style=flat" in out
    # 旧指标表废弃
    assert "| 公开仓库 | 42 |" not in out
    # 三枚在同一行（横向并排，不一行一个）
    stat_line = next(ln for ln in out.splitlines() if "-42-" in ln and "8aa6c4" in ln)
    assert "-128-" in stat_line and "-230-" in stat_line


def test_render_tech_tag_badges_inline_row():
    """🏷️ 技术标签：top_languages 用徽章横排展示，废弃纯文字「主要语言」行。"""
    out = render_readme(_ctx(), TEMPLATE)
    py = f"badge/{quote('Python', safe='')}-{quote('120★', safe='')}"
    js = f"badge/{quote('JavaScript', safe='')}-{quote('80★', safe='')}"
    assert py in out and js in out
    tag_line = next(ln for ln in out.splitlines() if quote("120★", safe="") in ln)
    assert js in tag_line  # 同一行横排
    assert "**主要语言**" not in out  # 旧纯文字标签废弃


def test_render_tech_tags_hidden_without_languages():
    ctx = _ctx()
    ctx.totals.top_languages = []
    out = render_readme(ctx, TEMPLATE)
    assert "技术标签" not in out


def test_render_pinned_section_card_structure():
    """置顶范式（规范 §3，最重要）：⭐ + 名称行用三级标题（不用 ** 加粗）、徽章紧随
    下一行横排、> 一句话摘要、亮点 ≤3 条；📌 板块在 📦 之前，且仓库列表不重复展示置顶仓库。

    有意变更（2026-10-24 视觉）：名称由「⭐ **[name](url)**｜徽章同行」改为
    「### ⭐ [name](url)」+ 下一行徽章——正文加粗星号是 AI 生成腔，且正文字号
    太细，置顶项目突出不了重点；三级标题比板块标题低一级，层级合适。
    """
    ctx = _ctx()
    ctx.featured[0].topics = ["python", "cli"]
    ctx.pinned = [ctx.featured[0]]
    out = render_readme(ctx, TEMPLATE)
    i_pin = out.index("## 📌 置顶项目")
    i_list = out.index("## 📦 仓库列表")
    assert i_pin < i_list  # 置顶在前
    card = out[i_pin:i_list]
    lines = card.splitlines()
    title = next(ln for ln in lines if "⭐" in ln)
    assert title == "### ⭐ [cool-tool](https://github.com/alice/cool-tool)"
    assert "**" not in title  # 不再用加粗星号包裹名称
    badge_line = next(ln for ln in lines[lines.index(title) + 1 :] if "img.shields.io" in ln)
    assert "img.shields.io/badge/Python-" in badge_line  # badge1
    assert "img.shields.io/github/stars/alice/cool-tool" in badge_line  # badge2
    assert "img.shields.io/github/forks/alice/cool-tool" in badge_line  # badge3
    assert "> 一个很酷的工具" in card  # 一句话摘要（引用块）
    assert "- python" in card and "- cli" in card  # 亮点列表
    # 仓库列表不重复置顶仓库，其余照常
    assert "| [cool-tool](" not in out
    assert "| [web-app](https://github.com/alice/web-app) | 网页应用 | 80 |" in out


def test_render_pinned_topics_capped_at_three():
    """置顶卡片亮点最多 3 条；普通仓库的 topics 不占版面（表格保持紧凑）。"""
    ctx = _ctx()
    ctx.featured[0].topics = ["t1", "t2", "t3", "t4"]
    ctx.featured[1].topics = ["js"]
    ctx.pinned = [ctx.featured[0]]
    out = render_readme(ctx, TEMPLATE)
    card = out[out.index("📌") : out.index("📦")]
    assert "- t1" in card and "- t2" in card and "- t3" in card
    assert "- t4" not in card  # 第 4 条截断
    assert "- js" not in out  # 普通仓库不出 topics


def test_render_pinned_without_description_omits_quote():
    """置顶仓库没文案时省略引用块（宁缺勿废话），徽章与标题照常。"""
    ctx = _ctx()
    ctx.featured[0].description = ""
    ctx.short_intros = {}
    ctx.pinned = [ctx.featured[0]]
    card = render_readme(ctx, TEMPLATE)
    section = card[card.index("📌") : card.index("📦")]
    assert not any(ln.startswith("> ") for ln in section.splitlines())
    assert "### ⭐ [cool-tool]" in section


def test_render_repo_table_aligned_columns():
    """📦 仓库列表：四列对齐表格，Stars 用纯数字保持列对齐，语言列用徽章。"""
    out = render_readme(_ctx(), TEMPLATE)
    assert "| 仓库 | 简介 | Stars | 语言 |" in out
    assert "| --- | --- | --- | --- |" in out
    assert (
        "| [cool-tool](https://github.com/alice/cool-tool) | 一个很酷的工具 | 120 |"
        in out
    )
    # 语言列徽章（低饱和圆角）
    assert "https://img.shields.io/badge/Python-5b7f9e?style=flat" in out
    # 非置顶仓库不排实时徽章（表格保持对齐是美观关键）
    assert "github/stars/alice/cool-tool" not in out


def test_render_note_alert_single_and_clean_links():
    """ℹ️ 说明备注：GitHub 原生 alert，一页最多 2-3 个（默认 1 个）。"""
    out = render_readme(_ctx(), TEMPLATE)
    assert "> [!NOTE]" in out
    assert out.count("> [!") == 1
    assert "自动生成" in out
    assert "2026-10-21 10:23" in out
    assert "⏱️" not in out  # 旧页脚时间行废弃


def test_render_contains_card_urls():
    out = render_readme(_ctx(), TEMPLATE)
    assert "github-readme-stats.vercel.app/api?username=alice" in out
    assert "top-langs" in out
    assert "streak-stats.demolab.com/?user=alice" in out
    assert "theme=github_dark" in out
    assert "&amp;theme=github_dark" in out  # HTML 实体化的 &


def test_render_without_bio_leaves_comment():
    ctx = _ctx()
    ctx.bio_text = ""
    out = render_readme(ctx, TEMPLATE)
    assert "AI 简介" in out  # 注释形式
    assert "你好，我是 Alice，一名热爱开源的工程师。" not in out


def test_render_bio_comment_names_configured_key_env():
    """简介缺失的占位注释应指向用户实际配置的 secret 名，而非写死某一家。"""
    ctx = _ctx()
    ctx.bio_text = ""
    # 默认（anthropic）：指 ANTHROPIC_API_KEY
    assert "配置 ANTHROPIC_API_KEY 后" in render_readme(ctx, TEMPLATE)
    # 换服务商的用户（key_env=AI_API_KEY，如 MiMo）：必须指 AI_API_KEY
    out = render_readme(dataclasses.replace(ctx, key_env="AI_API_KEY"), TEMPLATE)
    assert "配置 AI_API_KEY 后" in out
    assert "配置 ANTHROPIC_API_KEY 后" not in out


def test_render_without_update_time():
    """关闭更新时间 → 说明块保留但不带时间戳。"""
    ctx = _ctx()
    ctx.include_update_time = False
    out = render_readme(ctx, TEMPLATE)
    assert "2026-10-21 10:23" not in out
    assert "> [!NOTE]" in out


def test_render_deterministic():
    assert render_readme(_ctx(), TEMPLATE) == render_readme(_ctx(), TEMPLATE)


def test_write_if_changed(tmp_path):
    path = tmp_path / "README.md"
    assert write_if_changed(path, "内容 A\n") is True
    assert path.read_text(encoding="utf-8") == "内容 A\n"
    assert write_if_changed(path, "内容 A\n") is False  # 无变化不写
    assert write_if_changed(path, "内容 B\n") is True
    assert path.read_text(encoding="utf-8") == "内容 B\n"


def test_render_has_no_per_repo_intro_links():
    """主页不再逐仓库链接 docs/projects 文件（信息熵低 + 多余点击）。"""
    out = render_readme(_ctx(), TEMPLATE)
    assert "项目介绍与简历素材" not in out
    assert "docs/projects" not in out
    assert "📄" not in out


def test_render_badges_flat_rounded_and_soft_colors():
    """徽章规范：style=flat（自带 3px 圆角；shields 无 rounded 样式）、markdown 图片语法、
    语言徽章低饱和配色——大红大亮的 3776AB/f7df1e 必须绝迹。"""
    out = render_readme(_ctx(), TEMPLATE)
    assert "style=flat" in out
    assert "flat-square" not in out  # 方角已废弃
    assert "![公开仓库](https://img.shields.io/badge/" in out  # markdown 徽章
    assert "badge/Python-5b7f9e?style=flat" in out  # 低饱和语言色
    assert "3776AB" not in out and "f7df1e" not in out


def test_render_badge_without_language_omits_lang_badge():
    """language 为空 → 语言列留空单元格，不产生残缺 URL，表格列结构不散。"""
    ctx = _ctx()
    ctx.featured[0].language = ""
    out = render_readme(ctx, TEMPLATE)
    row = next(ln for ln in out.splitlines() if ln.startswith("| [cool-tool]"))
    assert "badge/Python" not in row
    assert "| 120 |  |" in row  # 语言列空但分隔符完好
    assert "5b7f9e" not in row


def test_render_push_badge_absent_when_no_push_date():
    ctx = _ctx()
    ctx.featured[0].pushed_at = None
    ctx.pinned = [ctx.featured[0]]
    out = render_readme(ctx, TEMPLATE)
    card = out[out.index("📌") : out.index("📦")]
    assert "last_push" not in card


def test_render_push_badge_renders_in_pinned_card():
    """置顶卡片徽章四件套：语言色、star/fork 实时、最后推送日期。"""
    ctx = _ctx()
    ctx.pinned = [ctx.featured[0]]
    card = render_readme(ctx, TEMPLATE)
    card = card[card.index("📌") : card.index("📦")]
    assert "img.shields.io/github/stars/alice/cool-tool" in card
    assert "img.shields.io/github/forks/alice/cool-tool" in card
    # shields 静态徽章里字面横线要写成 --
    assert "last_push-2026--10--01" in card


def test_render_language_badge_escapes_special_chars():
    """语言名里的 +、空格等字符要 URL 转义，颜色按低饱和映射表上色。"""
    ctx = _ctx()
    ctx.featured[0].language = "C++"
    out = render_readme(ctx, TEMPLATE)
    row = next(ln for ln in out.splitlines() if ln.startswith("| [cool-tool]"))
    assert "badge/C%2B%2B-566f8c" in row


def test_render_empty_description_falls_back_to_short_intro():
    """description 为空时展示 AI 补位文案，而不是占位废话。"""
    ctx = _ctx()
    ctx.featured[0].description = ""
    ctx.short_intros = {"alice/cool-tool": "命令行密码生成器，支持自定义字符集。"}
    out = render_readme(ctx, TEMPLATE)
    assert "命令行密码生成器" in out
    assert "（暂无描述）" not in out  # 两个仓库都有文案，任何位置都不该出占位符


def test_render_without_short_intro_shows_dash():
    """两头落空（无描述无补位）→ 表格用 — 占位，「暂无描述」废话废弃。"""
    ctx = _ctx()
    ctx.featured[0].description = ""
    out = render_readme(ctx, TEMPLATE)
    assert "（暂无描述）" not in out
    assert "| [cool-tool](https://github.com/alice/cool-tool) | — | 120 |" in out


def test_stat_badge_helper_encodes_and_rounds():
    """stat_badge 单元：中文/空格 URL 编码 + 横线转义 + flat 圆角样式。"""
    from profile_agent.render import stat_badge

    b = stat_badge("总 Star", 230)
    assert quote("总 Star", safe="") in b
    assert f"-230-{_SOFT}?style=flat)" in b  # 尾括号 = markdown 图片语法收尾
    # 字面横线转义（shields 规则）
    assert "a--b" in stat_badge("a-b", "c-d")


def test_render_visitors_badge_absent_by_default():
    out = render_readme(_ctx(), TEMPLATE)
    assert "komarev.com/ghpvc" not in out


def test_render_visitors_badge_when_enabled():
    out = render_readme(dataclasses.replace(_ctx(), visitors_badge=True), TEMPLATE)
    assert "komarev.com/ghpvc/?username=alice" in out


def test_header_b_has_no_panel_mention():
    """A-WP2 有意变更：置顶面板彻底移除，zh 头注释换新文案且全文不再有「面板」。"""
    out = render_readme(_ctx(), TEMPLATE)
    assert "可编辑区：custom 标记之间的内容，永远保留。" in out
    assert "面板" not in out  # 渲染输出任何位置都不再出现「面板」字样


# ---- readme.sections 板块开关（2026-10-25）----


def _with_sections(**flags) -> TemplateContext:
    return dataclasses.replace(_ctx(), sections=Sections(**flags))


def test_sections_default_all_on_matches_explicit_defaults():
    default_out = render_readme(_ctx(), TEMPLATE)
    explicit_out = render_readme(_with_sections(), TEMPLATE)
    assert default_out == explicit_out
    for marker in ("## 📊 统计信息", "## 🏷️ 技术标签", "## 📦 仓库列表", "## ℹ️ 说明备注"):
        assert marker in default_out


def test_sections_bio_off_hides_bio_but_keeps_greeting():
    out = render_readme(_with_sections(bio=False), TEMPLATE)
    assert "你好，我是 Alice，一名热爱开源的工程师。" not in out
    assert "AI 简介暂不可用" not in out  # 占位注释也一并隐藏，不留空洞
    assert "# 👋 你好，我是 Alice Zhang" in out  # 问候语永远在
    assert "## 📊 统计信息" in out and "## 📦 仓库列表" in out


def test_sections_stats_off_hides_stats_and_tech_tags():
    out = render_readme(_with_sections(stats=False), TEMPLATE)
    assert "## 📊 统计信息" not in out
    assert "## 🏷️ 技术标签" not in out
    assert "github-readme-stats" not in out  # 卡片一并隐藏
    assert "## 📦 仓库列表" in out and "## ℹ️ 说明备注" in out


def test_sections_pinned_off_puts_pinned_repos_back_into_table():
    ctx = _with_sections(pinned=False)
    ctx.pinned = [ctx.featured[0]]
    out = render_readme(ctx, TEMPLATE)
    assert "## 📌 置顶项目" not in out
    assert "### ⭐" not in out
    # 置顶仓库不能凭空消失：回到表格
    assert "## 📦 仓库列表" in out
    assert "| [cool-tool](https://github.com/alice/cool-tool) | 一个很酷的工具 | 120 |" in out
    assert "| [web-app](https://github.com/alice/web-app) | 网页应用 | 80 |" in out


def test_sections_table_off_hides_non_pinned_repos():
    ctx = _with_sections(table=False)
    ctx.pinned = [ctx.featured[0]]
    out = render_readme(ctx, TEMPLATE)
    assert "## 📦 仓库列表" not in out
    assert "## 📌 置顶项目" in out
    assert "web-app" not in out  # 非置顶仓库不再有任何展示位
    assert "## ℹ️ 说明备注" in out


def test_sections_table_off_without_pinned_hides_empty_table_hint():
    ctx = _with_sections(table=False)
    out = render_readme(ctx, TEMPLATE)
    assert "## 📦 仓库列表" not in out
    assert "暂无符合条件的项目" not in out  # 不留空板块占位


def test_sections_pinned_and_table_off_hides_all_repo_blocks():
    ctx = _with_sections(pinned=False, table=False)
    ctx.pinned = [ctx.featured[0]]
    out = render_readme(ctx, TEMPLATE)
    assert "## 📌 置顶项目" not in out
    assert "## 📦 仓库列表" not in out
    assert "cool-tool" not in out and "web-app" not in out
    assert "## ℹ️ 说明备注" in out
    assert "---\n\n---" not in out  # 不留下孤立的分隔线


def test_sections_off_keeps_custom_and_notes():
    ctx = _with_sections(bio=False, stats=False, pinned=False, table=False)
    ctx.custom_text = "联系方式：alice@example.com"
    out = render_readme(ctx, TEMPLATE)
    assert "联系方式：alice@example.com" in out
    assert "<!-- profile-agent:custom:start -->" in out
    assert "## ℹ️ 说明备注" in out
    assert "> [!NOTE]" in out
