"""精选仓库缺描述时的 AI 短介绍（主页补位文案）。

- 只针对 description 为空的精选仓库；以该仓库 README 为事实来源
- 输入指纹含 README 内容与 prompt_version：仓库没动就不重新生成、不调 API
- 生成失败时调用方回退旧文案（若有），不影响任务退出码
"""

from __future__ import annotations

import hashlib
import json

from .config import AIConfig
from .models import Repo

_SYSTEM = (
    "你是一位技术写作助手，为 GitHub 个人主页的项目列表撰写简短中文描述。"
    "只输出描述正文，不要 markdown、引号或解释。"
)

# 硬性长度上限（提示词要求 60 字以内，这里兜底防跑飞）
_MAX_CHARS = 80


def build_short_intro_prompt(repo: Repo, readme: str = "", bilingual: bool = True) -> str:
    """构建补位文案 prompt：README 是主要事实来源，缺失时只用已知事实。"""
    facts = json.dumps(
        {
            "name": repo.name,
            "language": repo.language,
            "stars": repo.stars,
            "topics": repo.topics,
        },
        ensure_ascii=False,
    )
    text = (readme or "").strip()
    if len(text) > 4000:
        text = text[:4000] + "\n……（README 过长，已截断）"
    if text:
        context = f"仓库 README（主要事实来源）：\n{text}"
    else:
        context = "仓库没有 README，只能使用上面的已知事实。"
    base = (
        f"这个 GitHub 仓库在个人主页的项目列表里没有一句话描述，请补一句。\n"
        f"要求：用 1-2 句话、不超过 60 字，说明它是做什么的、有什么特点；"
        f"只基于提供的事实，不要编造，不要夸大。\n"
        f"已知事实：{facts}\n\n{context}\n"
    )
    if bilingual:
        # WP4 双语：单次调用 JSON {zh,en}；解析失败由 generate_short_intro 降级单语
        base += (
            "另外给出自然地道的英文版。请只输出一个 JSON 对象，形如：\n"
            '{"zh": "中文描述", "en": "English description"}\n'
            "不要输出 JSON 之外的任何文字或代码块围栏。\n"
        )
    return base


def short_intro_input_hash(
    repo: Repo, cfg: AIConfig, readme: str, bilingual: bool = True
) -> str:
    """补位文案的输入指纹（含 README 内容、prompt_version 与 bilingual）。"""
    payload = {
        "full_name": repo.full_name,
        "pushed_at": repo.pushed_at.isoformat() if repo.pushed_at else None,
        "readme_sha256": hashlib.sha256((readme or "").encode("utf-8")).hexdigest(),
        "prompt_version": cfg.prompt_version,
        # 双语开关入指纹：单语↔双语切换触发一次重生成（迁移成本一次）
        "bilingual": bilingual,
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def generate_short_intro(
    repo: Repo,
    cfg: AIConfig,
    *,
    client=None,
    http_post=None,
    readme: str = "",
    bilingual: bool = True,
) -> tuple[str, str]:
    """生成 (zh, en) 两句主页展示文案；zh 为空视为失败（en 允许为空=降级单语）。"""
    from .bio import _parse_dual, chat_text

    raw = chat_text(
        cfg,
        _SYSTEM,
        build_short_intro_prompt(repo, readme, bilingual=bilingual),
        client=client,
        http_post=http_post,
    ).strip()
    if bilingual:
        dual = _parse_dual(raw)
        if dual is None:
            text, text_en = raw, ""  # 降级单语：宁可单语不可失败
        else:
            text, text_en = dual
    else:
        text, text_en = raw, ""
    if not text:
        raise RuntimeError("模型返回了空的项目描述")
    # 硬性长度上限双语各截一次（提示词要求 60 字以内，这里兜底防跑飞）
    if len(text) > _MAX_CHARS:
        text = text[:_MAX_CHARS]
    if len(text_en) > _MAX_CHARS:
        text_en = text_en[:_MAX_CHARS]
    return text, text_en
