"""render.py 模板渲染与写入测试。"""

import dataclasses
from datetime import datetime, timezone
from pathlib import Path

from profile_agent.models import Repo, TemplateContext, Totals
from profile_agent.render import render_readme, write_if_changed

TEMPLATE = Path(__file__).parent.parent / "templates" / "profile.md.j2"


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
    out = render_readme(_ctx(), TEMPLATE)
    assert "你好，我是" in out
    assert "仓库统计" in out
    assert "我的项目" in out
    assert "你好，我是 Alice" in out  # AI 简介
    assert out.lstrip().startswith("<!--")  # 自动生成标记


def test_render_contains_stats_table_and_featured():
    out = render_readme(_ctx(), TEMPLATE)
    assert "| 公开仓库 | 42 |" in out
    assert "| 总 Star | 230 |" in out
    # 主打卡（英雄卡）：语言 emoji + 可点击标题
    assert '<h3><a href="https://github.com/alice/cool-tool">🐍 cool-tool</a></h3>' in out
    assert '<h3><a href="https://github.com/alice/web-app">📜 web-app</a></h3>' in out
    assert "一个很酷的工具" in out
    assert "网页应用" in out
    # star 走实时徽章
    assert "img.shields.io/github/stars/alice/cool-tool" in out


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
    ctx = _ctx()
    ctx.include_update_time = False
    out = render_readme(ctx, TEMPLATE)
    assert "最后更新" not in out


def test_render_deterministic():
    assert render_readme(_ctx(), TEMPLATE) == render_readme(_ctx(), TEMPLATE)


def test_write_if_changed(tmp_path):
    path = tmp_path / "README.md"
    assert write_if_changed(path, "内容 A\n") is True
    assert path.read_text(encoding="utf-8") == "内容 A\n"
    assert write_if_changed(path, "内容 A\n") is False  # 无变化不写
    assert write_if_changed(path, "内容 B\n") is True
    assert path.read_text(encoding="utf-8") == "内容 B\n"


def test_render_shows_topic_chips():
    ctx = _ctx()
    ctx.featured[0].topics = ["python", "cli"]
    out = render_readme(ctx, TEMPLATE)
    assert "<code>#python</code>" in out and "<code>#cli</code>" in out
    # 无 topics 的仓库不出现标签行残留
    assert "#javascript" not in out


def test_render_has_no_per_repo_intro_links():
    """主页不再逐仓库链接 docs/projects 文件（信息熵低 + 多余点击）。"""
    out = render_readme(_ctx(), TEMPLATE)
    assert "项目介绍与简历素材" not in out
    assert "docs/projects" not in out
    assert "📄" not in out


def test_render_card_badges_language_stars_forks_push():
    """卡片徽章四件套：语言色徽章、star/fork 实时徽章、最后推送日期。"""
    out = render_readme(_ctx(), TEMPLATE)
    assert "https://img.shields.io/badge/Python-3776AB?style=flat-square" in out
    assert "https://img.shields.io/github/stars/alice/cool-tool?" in out
    assert "https://img.shields.io/github/forks/alice/cool-tool?" in out
    # shields 静态徽章里字面横线要写成 --
    assert "last_push-2026--10--01-lightgrey" in out


def test_render_badge_without_language_omits_lang_badge():
    """language 为空 → 不出语言徽章，其余徽章照常（无空段、无悬挂分隔符）。"""
    ctx = _ctx()
    ctx.featured[0].language = ""
    out = render_readme(ctx, TEMPLATE)
    section = out.split("web-app")[0]  # 只看第一个仓库（cool-tool）的卡片
    assert "badge/Python" not in section
    assert "github/stars/alice/cool-tool" in section
    assert "badge/-" not in section  # 不产生空语言的残缺 URL


def test_render_push_badge_absent_when_no_push_date():
    ctx = _ctx()
    ctx.featured[0].pushed_at = None
    out = render_readme(ctx, TEMPLATE)
    assert "last_push" not in out.split("web-app")[0]


def test_render_language_badge_escapes_special_chars():
    """语言名里的 +、空格等字符要 URL 转义，颜色按内置映射表上色。"""
    ctx = _ctx()
    ctx.featured[0].language = "C++"
    out = render_readme(ctx, TEMPLATE)
    assert "badge/C%2B%2B-00599C" in out


def test_render_hero_cards_take_top3_with_description_and_rest_grid():
    """布局分层：前 3 个「有文案」的仓库 → 通栏主打卡；其余 → 双列网格卡（奇数补位空格）。"""
    ctx = _ctx()

    def _repo(n: str, desc: str) -> Repo:
        return Repo(
            name=n,
            full_name=f"alice/{n}",
            description=desc,
            language="Python",
            stars=1,
            html_url=f"https://github.com/alice/{n}",
        )

    ctx.featured = [
        _repo("d1", "有描述一"),
        _repo("x", ""),
        _repo("d2", "有描述二"),
        _repo("d3", "有描述三"),
    ]
    ctx.short_intros = {}
    out = render_readme(ctx, TEMPLATE)
    # 主打卡恰好 3 个（有描述的前三个，保持原排序）
    assert out.count("<h3>") == 3
    for n in ("d1", "d2", "d3"):
        assert f'<h3><a href="https://github.com/alice/{n}">' in out
    # 无描述的 x 落到网格：加粗小标题，且奇数行出补位空格（共 x 一格 + 空格一格）
    assert '<a href="https://github.com/alice/x"><b>🐍 x</b></a>' in out
    assert "<h3><a href=\"https://github.com/alice/x\">" not in out
    assert out.count('width="50%"') == 2


def test_render_empty_description_falls_back_to_short_intro():
    """description 为空时展示 AI 补位文案，而不是「暂无描述」。"""
    ctx = _ctx()
    ctx.featured[0].description = ""
    ctx.short_intros = {"alice/cool-tool": "命令行密码生成器，支持自定义字符集。"}
    out = render_readme(ctx, TEMPLATE)
    assert "命令行密码生成器" in out
    assert "（暂无描述）" not in out  # 两个仓库都有文案，任何位置都不该出占位符


def test_render_without_short_intro_shows_placeholder():
    ctx = _ctx()
    ctx.featured[0].description = ""
    out = render_readme(ctx, TEMPLATE)
    assert "（暂无描述）" in out


def test_render_visitors_badge_absent_by_default():
    out = render_readme(_ctx(), TEMPLATE)
    assert "komarev.com/ghpvc" not in out


def test_render_visitors_badge_when_enabled():
    out = render_readme(dataclasses.replace(_ctx(), visitors_badge=True), TEMPLATE)
    assert "komarev.com/ghpvc/?username=alice" in out
