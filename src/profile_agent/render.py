"""Jinja2 模板渲染与按需写入。"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .models import TemplateContext


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
    )


def write_if_changed(path: Path, content: str) -> bool:
    """内容有变化才写入；返回是否写入。"""
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True
