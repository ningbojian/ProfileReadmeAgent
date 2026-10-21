"""方向二：按仓库生成深度项目介绍 / 简历素材（docs/projects/）。

- 每个仓库一个 markdown 文件，文件头是事实数据（链接/star/语言/更新时间），
  正文优先由 AI 生成（项目简介 + 亮点 + 简历素材段），无 key 或失败时降级为纯数据版。
- 仓库级增量：intro_hash 不变且文件存在 → 不重新生成，不调 API。
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from .config import AIConfig, ProjectsConfig
from .models import Repo

_SYSTEM = (
    "你是一位技术写作与简历辅导助手，用简体中文撰写单个开源项目的介绍。"
    "只输出正文，不要 markdown 一级标题、代码块或解释。"
)

# 喂给模型的 README 最大长度（字符），防止超长 README 撑爆 prompt
_README_MAX_CHARS = 6000


def intro_filename(repo: Repo) -> str:
    safe = re.sub(r"[^\w.\-]+", "-", repo.name).strip("-") or "repo"
    return f"{safe}.md"


def intro_input_hash(repo: Repo, cfg: AIConfig) -> str:
    payload = {
        "full_name": repo.full_name,
        "description": repo.description,
        "language": repo.language,
        "stars": repo.stars,
        "forks": repo.forks,
        "pushed_at": repo.pushed_at.isoformat() if repo.pushed_at else None,
        "prompt_version": cfg.prompt_version,
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def build_intro_prompt(repo: Repo, readme: str = "") -> str:
    """构建项目介绍 prompt。

    有仓库 README 时把它作为主要事实来源（信息密度的关键：只靠
    description+stars 一句话事实，模型写不出有内容的介绍）；没有则退回已知事实。
    """
    facts = json.dumps(
        {
            "name": repo.name,
            "description": repo.description,
            "language": repo.language,
            "stars": repo.stars,
            "forks": repo.forks,
            "topics": repo.topics,
            "pushed_at": repo.pushed_at.isoformat() if repo.pushed_at else None,
        },
        ensure_ascii=False,
        indent=2,
    )
    text = (readme or "").strip()
    if len(text) > _README_MAX_CHARS:
        text = text[:_README_MAX_CHARS] + "\n……（README 过长，已截断）"
    if text:
        context = f"仓库 README 内容（主要事实来源，从中提取功能、架构与技术选型）：\n{text}"
    else:
        context = "仓库没有可用的 README，只能使用上面的已知事实，缺失的信息不要虚构。"
    return (
        f"请为下面这个 GitHub 项目写中文介绍，读者是招聘方与潜在协作者，"
        f"要求信息密度高、只写可验证的事实：\n"
        f"1. “项目介绍”：2-4 句话，说明它解决什么问题、核心功能与实现方式；\n"
        f"2. “亮点”：3-5 条以“- ”开头的要点（每条不超过 40 字），"
        f"优先从 README 提取架构、关键功能、技术选型等具体信息；\n"
        f"3. “简历素材”：一段 60-100 字、可直接放进简历项目经历的描述，"
        f"突出技术栈与可量化成果，不要夸大。\n"
        f"已知事实（不得编造超出这些事实的内容）：\n{facts}\n\n{context}\n"
    )


def _header(repo: Repo) -> str:
    updated = repo.pushed_at.strftime("%Y-%m-%d") if repo.pushed_at else "未知"
    lines = [
        f"<!-- 由 profile-agent 生成/维护，AI 内容已经过你的审核；事实数据自动更新 -->",
        f"# {repo.name}",
        "",
        f"- 仓库：[{repo.full_name}]({repo.html_url or 'https://github.com/' + repo.full_name})",
        f"- 语言：{repo.language or '未知'}　⭐ {repo.stars}　🍴 {repo.forks}",
        f"- 最近推送：{updated}",
        "",
    ]
    return "\n".join(lines)


def _data_stub(repo: Repo, key_env: str = "ANTHROPIC_API_KEY") -> str:
    parts = [
        "## 项目介绍",
        "",
        repo.description or "（GitHub 上暂无描述，可在仓库主页补充）",
        "",
        "## 亮点",
        "",
        f"- {repo.language or '多语言'} 项目，获得 {repo.stars} 个 star、{repo.forks} 个 fork",
        "",
        "## 简历素材",
        "",
        f"参与/维护 {repo.full_name}（{repo.language or '多语言'}），项目累计 {repo.stars} star。",
        "",
        f"> （数据版占位：配置 {key_env} 后重新生成可获得 AI 撰写的完整版本）",
        "",
    ]
    return "\n".join(parts)


def generate_project_intro(
    repo: Repo, cfg: AIConfig, *, client=None, http_post=None, readme: str = ""
) -> str:
    """调用配置的模型生成项目介绍正文（无文件头）。"""
    from .bio import chat_text

    text = chat_text(
        cfg, _SYSTEM, build_intro_prompt(repo, readme), client=client, http_post=http_post
    ).strip()
    if not text:
        raise RuntimeError("模型返回了空的项目介绍")
    return text


def update_project_intros(
    repos: list[Repo],
    proj_cfg: ProjectsConfig,
    ai_cfg: AIConfig,
    prev_states: dict[str, dict],
    *,
    client=None,
    allow_ai: bool = True,
    fetch_readme=None,
    force: bool = False,
) -> dict[str, dict]:
    """按仓库增量生成/更新介绍文件。返回每仓库的新状态。

    - hash 匹配且文件存在 → 跳过（不调 API、不写文件、不拉 README），沿用旧状态；
      但若已有内容是数据版占位（ai_generated=False）而 AI 已可用 → 视为待升级，重新生成
    - force=True（点名重跑 / --force）→ 忽略 hash 复用，重新生成
    - 否则重新生成：AI 可用且成功 → AI 正文（用 fetch_readme 拉到的 README
      作为事实来源）；否则 → 数据版占位（失败原因写 stderr，不再静默吞掉）
    - fetch_readme 失败不影响生成（退回只用 API 事实）
    """
    out_dir = Path(proj_cfg.dir)
    now_iso = datetime.now(timezone.utc).isoformat()
    states: dict[str, dict] = {}

    for repo in repos:
        h = intro_input_hash(repo, ai_cfg)
        out_path = out_dir / intro_filename(repo)
        prev = prev_states.get(repo.full_name) or {}

        if (
            not force
            and prev.get("intro_hash") == h
            and out_path.exists()
            # 占位版不设永久豁免：AI 可用后自动升级重写
            and (prev.get("ai_generated") or not allow_ai)
        ):
            # regenerated 只标记「本次运行」，复用时剥掉，防止残留到下次判断
            states[repo.full_name] = {
                k: v for k, v in prev.items() if k != "regenerated"
            }
            continue

        body = None
        ai_generated = False
        if allow_ai:
            readme = ""
            if fetch_readme is not None:
                try:
                    readme = fetch_readme(repo.full_name) or ""
                except Exception:
                    readme = ""
            try:
                body = generate_project_intro(repo, ai_cfg, client=client, readme=readme)
                ai_generated = True
            except Exception as exc:
                # 静默降级排查不动日志——这里必须留下失败原因
                print(
                    f"「{repo.name}」项目介绍 AI 生成失败，本次降级为数据版占位"
                    f"（下次运行会自动重试）：{exc}",
                    file=sys.stderr,
                )
        if body is None:
            body = _data_stub(repo, ai_cfg.key_env_name)

        out_dir.mkdir(parents=True, exist_ok=True)
        out_path.write_text(_header(repo) + "\n" + body.strip() + "\n", encoding="utf-8")
        states[repo.full_name] = {
            "intro_hash": h,
            "intro_generated_at": now_iso,
            "ai_generated": ai_generated,
            "regenerated": True,
        }

    return states
