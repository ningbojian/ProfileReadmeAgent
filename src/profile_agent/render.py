"""Jinja2 模板渲染与按需写入。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .models import TemplateContext

# 语言 → shields 徽章主色（未知语言用中性灰）
_LANG_COLORS: dict[str, str] = {
    "Python": "3776AB",
    "JavaScript": "f7df1e",
    "TypeScript": "3178c6",
    "Go": "00ADD8",
    "Rust": "dea581",
    "Java": "b07219",
    "C": "A8B9CC",
    "C++": "00599C",
    "C#": "512BD4",
    "PHP": "4F5D95",
    "Ruby": "701516",
    "Swift": "F05138",
    "Kotlin": "7F52FF",
    "Dart": "0175C2",
    "Vue": "4FC08D",
    "HTML": "E34C26",
    "CSS": "563D7C",
    "Shell": "89E051",
    "Lua": "2C2D72",
    "Jupyter Notebook": "DA5B0B",
    "Makefile": "427819",
}

# 语言 → 卡片标题前缀 emoji（未知语言用包裹箱）
_LANG_EMOJI: dict[str, str] = {
    "Python": "🐍",
    "JavaScript": "📜",
    "TypeScript": "📘",
    "Go": "🐹",
    "Rust": "🦀",
    "Java": "☕",
    "C": "⚙️",
    "C++": "🧩",
    "C#": "💜",
    "PHP": "🐘",
    "Ruby": "💎",
    "Swift": "🐦",
    "Kotlin": "🎯",
    "Dart": "🚀",
    "Vue": "🌿",
    "HTML": "🌐",
    "CSS": "🎨",
    "Shell": "🖥️",
    "Lua": "🌙",
    "Jupyter Notebook": "📓",
    "Makefile": "🔧",
}


def lang_badge(language: str) -> str:
    """语言静态彩色徽章；空语言返回空串（避免出空段的残缺 URL）。"""
    if not language:
        return ""
    color = _LANG_COLORS.get(language, "555555")
    # shields 规则：字面横线写成 --；其余按 URL 编码转义
    text = quote(language.replace("-", "--"), safe="")
    return (
        f'<img src="https://img.shields.io/badge/{text}-{color}'
        f'?style=flat-square" alt="{language}" />'
    )


def stars_badge(full_name: str) -> str:
    """star 实时徽章（img.shields.io 按需拉取，README 指纹不受数量变化影响）。"""
    return (
        f'<img src="https://img.shields.io/github/stars/{full_name}'
        '?style=flat-square&amp;label=stars" alt="Stars" />'
    )


def forks_badge(full_name: str) -> str:
    """fork 实时徽章。"""
    return (
        f'<img src="https://img.shields.io/github/forks/{full_name}'
        '?style=flat-square&amp;label=forks" alt="Forks" />'
    )


def push_badge(pushed_at: datetime | None) -> str:
    """最后推送日期静态徽章；无记录返回空串。"""
    if pushed_at is None:
        return ""
    date = pushed_at.strftime("%Y-%m-%d").replace("-", "--")
    return (
        f'<img src="https://img.shields.io/badge/last_push-{date}'
        '-lightgrey?style=flat-square" alt="最后推送" />'
    )


def lang_emoji(language: str) -> str:
    """卡片标题前缀 emoji，让不同语言的卡片有辨识度。"""
    return _LANG_EMOJI.get(language, "📦")


def render_readme(ctx: TemplateContext, template_path: Path) -> str:
    env = Environment(
        loader=FileSystemLoader(str(template_path.parent)),
        autoescape=select_autoescape(()),
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template(template_path.name)
    return template.render(
        username=ctx.username,
        display_name=ctx.display_name,
        blog=ctx.blog,
        totals=ctx.totals,
        featured=ctx.featured,
        bio_text=ctx.bio_text,
        cards_theme=ctx.cards_theme,
        cards_show=ctx.cards_show,
        include_update_time=ctx.include_update_time,
        updated_at=ctx.updated_at,
        short_intros=ctx.short_intros,
        visitors_badge=ctx.visitors_badge,
        key_env=ctx.key_env,
        lang_badge=lang_badge,
        stars_badge=stars_badge,
        forks_badge=forks_badge,
        push_badge=push_badge,
        lang_emoji=lang_emoji,
    )


def write_if_changed(path: Path, content: str) -> bool:
    """内容有变化才写入；返回是否写入。"""
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True
