"""仓库数据的过滤、统计、精选与指纹计算（纯函数）。"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict
from datetime import datetime, timezone

from .config import AIConfig, FeaturedConfig, ReposConfig
from .models import Repo, Totals


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def normalize_repos(raw: list[dict], cfg: ReposConfig) -> list[Repo]:
    """把 GitHub API 原始仓库数据规范成 Repo，并按配置过滤。

    始终剔除 private 仓库：/users/{u}/repos 只返回 public，这里是防御性过滤。
    """
    result: list[Repo] = []
    for item in raw:
        if item.get("private"):
            continue
        if cfg.exclude_forks and item.get("fork"):
            continue
        if cfg.exclude_archived and item.get("archived"):
            continue
        full_name = str(item.get("full_name") or "")
        if full_name in cfg.exclude:
            continue
        result.append(
            Repo(
                name=str(item.get("name") or ""),
                full_name=full_name,
                description=str(item.get("description") or ""),
                language=str(item.get("language") or ""),
                stars=int(item.get("stargazers_count") or 0),
                forks=int(item.get("forks_count") or 0),
                private=bool(item.get("private")),
                fork=bool(item.get("fork")),
                archived=bool(item.get("archived")),
                pushed_at=_parse_dt(item.get("pushed_at")),
                html_url=str(item.get("html_url") or ""),
                homepage=str(item.get("homepage") or ""),
                default_branch=str(item.get("default_branch") or "main"),
                topics=[str(t) for t in (item.get("topics") or [])],
            )
        )
    return result


def compute_totals(user: dict, repos: list[Repo]) -> Totals:
    """汇总统计：仓库数、 followers、总 star、语言 star 直方图。"""
    lang_stars: dict[str, int] = {}
    for r in repos:
        if r.language:
            lang_stars[r.language] = lang_stars.get(r.language, 0) + r.stars
    top_languages = sorted(lang_stars.items(), key=lambda kv: (-kv[1], kv[0]))
    return Totals(
        public_repos=int(user.get("public_repos") or 0),
        followers=int(user.get("followers") or 0),
        total_stars=sum(r.stars for r in repos),
        top_languages=top_languages,
    )


def resolve_pins(repos: list[Repo], pin: list[str]) -> list[Repo]:
    """pin 名单 → 置顶仓库列表：按名单顺序、full_name 或裸名精确匹配（大小写敏感，
    与 repos.exclude 一致）、重复取首现、未知条目静默忽略。

    不做任何过滤——repos 入参应已是 normalize 之后的集合（private/fork/黑名单等
    硬过滤在那一步生效，pin 看不到也拉不回）。
    """
    pinned: list[Repo] = []
    seen: set[str] = set()
    for want in pin:
        hit = next(
            (
                r
                for r in repos
                if (r.full_name == want or r.name == want) and r.full_name not in seen
            ),
            None,
        )
        if hit is not None:
            seen.add(hit.full_name)
            pinned.append(hit)
    return pinned


def select_featured(
    repos: list[Repo], cfg: FeaturedConfig, *, now: datetime | None = None
) -> list[Repo]:
    """挑选上主页展示的项目列表：置顶（pin）排最前，其余 min_stars/recency 过滤后按 sort_by 排序。

    count 是「截断上限」（整个列表的唯一硬上限，置顶也占名额）：>0 取前 count 个；
    0 = 不限量。想要「真·全部」：count: 0 且 recency_days: 0（min_stars 保持 0）。
    """
    now = now or datetime.now(timezone.utc)

    def age_days(r: Repo) -> float | None:
        if r.pushed_at is None:
            return None
        return (now - r.pushed_at).total_seconds() / 86400

    candidates = [r for r in repos if r.stars >= cfg.min_stars]
    if cfg.recency_days > 0:
        candidates = [
            r
            for r in candidates
            if (a := age_days(r)) is not None and 0 <= a <= cfg.recency_days
        ]

    def score(r: Repo) -> float:
        s = cfg.score_stars_weight * math.log10(r.stars + 1)
        a = age_days(r)
        if cfg.recency_days > 0 and a is not None:
            s += cfg.score_recency_weight * math.exp(-a / cfg.recency_days)
        return s

    if cfg.sort_by == "stars":
        candidates.sort(key=lambda r: (-r.stars, r.full_name))
    elif cfg.sort_by == "updated":
        candidates.sort(
            key=lambda r: (r.pushed_at is None, -(r.pushed_at.timestamp() if r.pushed_at else 0), r.full_name)
        )
    else:  # score
        candidates.sort(key=lambda r: (-score(r), r.full_name))

    # 置顶（featured.pin）：resolve_pins 按 pin 顺序点名，豁免 min_stars/recency 启发式
    # （显式点名 > 启发式过滤），但不越过 normalize 硬过滤；置顶占 count 名额
    # （count 是唯一硬上限）。
    pinned = resolve_pins(repos, cfg.pin)
    seen = {r.full_name for r in pinned}
    rest = [r for r in candidates if r.full_name not in seen]
    merged = pinned + rest
    return merged if cfg.count <= 0 else merged[: cfg.count]


def stats_fingerprint(totals: Totals, repos: list[Repo], featured: list[Repo] | None = None) -> str:
    """影响渲染的数据的稳定指纹，用于增量检测。

    含精选结果：仓库随时间“老化”导致精选名单变化时指纹也会变化。
    """
    payload = {
        "totals": {
            "public_repos": totals.public_repos,
            "followers": totals.followers,
            "total_stars": totals.total_stars,
            "top_languages": totals.top_languages,
        },
        "repos": sorted(
            (
                {
                    "full_name": r.full_name,
                    "description": r.description,
                    "language": r.language,
                    "stars": r.stars,
                    "forks": r.forks,
                    "pushed_at": r.pushed_at.isoformat() if r.pushed_at else None,
                    "topics": r.topics,
                }
                for r in repos
            ),
            key=lambda d: d["full_name"],
        ),
        "featured": [r.full_name for r in (featured or [])],
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def render_fingerprint(cfg, template_bytes: bytes, user: dict) -> str:
    """渲染相关配置 + 模板内容 + 用户资料的指纹。

    改配置（主题/精选数等）、改模板、改 GitHub 资料都会让它变化，
    使增量门重新渲染 README。
    """
    from dataclasses import asdict

    payload = {
        "featured": asdict(cfg.featured),
        "readme": asdict(cfg.readme),
        "ai": {
            "enabled": cfg.ai.enabled,
            "model": cfg.ai.model,
            "language": cfg.ai.language,  # 主语言变化 → 指纹变化（双语首跑开门）
            "max_repos": cfg.ai.max_repos,
            "bio_min_chars": cfg.ai.bio_min_chars,
            "bio_max_chars": cfg.ai.bio_max_chars,
            "prompt_version": cfg.ai.prompt_version,
            "fill_empty_descriptions": cfg.ai.fill_empty_descriptions,
        },
        # 有意变更（A-P0）：projects 已随项目介绍功能移除，指纹不再含该键
        #（存量指纹变化 → 升级首跑开门重渲染一次，0 AI）
        "template_sha256": hashlib.sha256(template_bytes).hexdigest(),
        "user": {
            "name": user.get("name") or "",
            "blog": user.get("blog") or "",
            "bio": user.get("bio") or "",
        },
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def build_bio_payload(
    user: dict, featured: list[Repo], cfg: AIConfig, bilingual: bool = True
) -> dict:
    """构建喂给 AI 的简介输入（截取前 max_repos 个精选仓库）。"""
    return {
        "name": user.get("name") or user.get("login") or "",
        "blog": user.get("blog") or "",
        "bio": user.get("bio") or "",
        "totals": {
            "public_repos": user.get("public_repos") or 0,
            "followers": user.get("followers") or 0,
        },
        # bilingual 入 payload → 入指纹：单语↔双语切换触发一次重生成（迁移成本一次）
        "bilingual": bilingual,
        "repos": [
            {
                "name": r.name,
                "description": r.description,
                "language": r.language,
                "stars": r.stars,
                "topics": r.topics,
            }
            for r in featured[: cfg.max_repos]
        ],
    }


def bio_input_hash(payload: dict, cfg: AIConfig) -> str:
    """简介输入的指纹（含 prompt_version），变化才重新生成简介。

    成本护栏：repos 按 name 排序后再哈希——置顶顺序调整（同一批仓库）
    不改变指纹，不触发简介重生成；集合变化仍触发。
    """
    normalized = {
        **payload,
        "repos": sorted(payload.get("repos", []), key=lambda r: r.get("name") or ""),
    }
    blob = json.dumps(
        {"payload": normalized, "prompt_version": cfg.prompt_version},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()
