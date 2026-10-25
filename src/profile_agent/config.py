"""配置加载与校验。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .models import Sections

VALID_SORT_BY = ("score", "stars", "updated")
VALID_CARDS = ("stats", "top_langs", "streak")


class ConfigError(Exception):
    """配置错误（消息为中文）。"""


@dataclass
class ScheduleConfig:
    enabled: bool = True


@dataclass
class GitHubConfig:
    username: str = ""
    token_env: str = "GITHUB_TOKEN"


@dataclass
class ReposConfig:
    exclude_forks: bool = True
    exclude_archived: bool = False
    exclude: list[str] = field(default_factory=list)


@dataclass
class FeaturedConfig:
    count: int = 0  # 上主页的项目数上限；0 = 全部
    min_stars: int = 0
    recency_days: int = 0  # 只考虑最近 N 天有推送的仓库；0 = 不限
    sort_by: str = "score"
    score_stars_weight: float = 1.0
    score_recency_weight: float = 0.5
    pin: list[str] = field(default_factory=list)  # 置顶展示的仓库（full_name 或裸名），按此顺序排最前


@dataclass
class CardsConfig:
    theme: str = "github_dark"
    show: list[str] = field(default_factory=lambda: ["stats", "top_langs", "streak"])


@dataclass
class ReadmeConfig:
    enabled: bool = True  # false = 本次不更新 overview README
    path: str = "README.md"
    template: str = "templates/profile.md.j2"
    cards: CardsConfig = field(default_factory=CardsConfig)
    include_update_time: bool = True
    visitors_badge: bool = False  # 主页嵌入第三方「主页访问量」计数徽章（komarev.com）
    bilingual: bool = True  # true = 同时生成 README.en.md 英文版，顶部互链切换
    # 手写 README 的保护策略（2026-10-25）：
    #   auto = 只在首个标题后插/更新一个 AI 简介托管块，其余字节不动；
    #   full = 整页接管（有 PAT 先开预览 PR，无 PAT 跳过）。
    mode: str = "auto"
    sections: Sections = field(default_factory=Sections)  # 四个板块开关，默认全开


@dataclass
class AIConfig:
    enabled: bool = True
    provider: str = "anthropic"  # anthropic | openai（OpenAI 兼容：DeepSeek/千问/Kimi/Ollama 等）
    base_url: str = ""  # provider=openai 时必填，如 https://api.deepseek.com
    key_env: str = ""  # API key 所在环境变量名；默认 anthropic→ANTHROPIC_API_KEY, openai→OPENAI_API_KEY
    model: str = "claude-opus-5-5"
    max_repos: int = 15
    max_age_days: int = 14
    bio_min_chars: int = 100
    bio_max_chars: int = 150
    language: str = "zh-CN"
    # 2：2026-10-24 移除简介末尾「亮点：」小节（与置顶项目/仓库列表重复）
    prompt_version: int = 2
    fill_empty_descriptions: bool = True  # 精选仓库缺描述时用 AI 基于其 README 生成主页补位文案

    @property
    def key_env_name(self) -> str:
        if self.key_env:
            return self.key_env
        return "OPENAI_API_KEY" if self.provider == "openai" else "ANTHROPIC_API_KEY"

    @property
    def default_model(self) -> str:
        return "deepseek-chat" if self.provider == "openai" and not self.model else self.model


@dataclass
class SnapshotConfig:
    path: str = "data/snapshot.json"


@dataclass
class RepoReadmesConfig:
    """模式 B：代码变更 → 各仓库 README 托管区自动更新（默认关闭）。

    独立于 ai.prompt_version（设计 §12-6）：两种内容可独立迭代。
    """

    enabled: bool = False
    include: list[str] = field(default_factory=list)  # 空 = 按 repos 过滤规则的全部仓库
    exclude: list[str] = field(default_factory=list)  # 在 include 之上再排除
    max_diff_chars: int = 6000
    prompt_version: int = 1
    pr_branch: str = "readme-auto/update"


@dataclass
class RepoDescriptionsConfig:
    """仓库描述自动写回（默认关闭）。

    第一次见到仓库只记基线、绝不 PATCH；用户手动改过描述即永久交还控制权；
    每轮最多写 BATCH_LIMIT 个。include/exclude 语义与 repo_readmes 完全一致。
    """

    enabled: bool = False
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)


@dataclass
class Config:
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    github: GitHubConfig = field(default_factory=GitHubConfig)
    repos: ReposConfig = field(default_factory=ReposConfig)
    featured: FeaturedConfig = field(default_factory=FeaturedConfig)
    readme: ReadmeConfig = field(default_factory=ReadmeConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    repo_readmes: RepoReadmesConfig = field(default_factory=RepoReadmesConfig)
    repo_descriptions: RepoDescriptionsConfig = field(
        default_factory=RepoDescriptionsConfig
    )
    snapshot: SnapshotConfig = field(default_factory=SnapshotConfig)


def _section(data: dict, key: str) -> dict:
    value = data.get(key) or {}
    if not isinstance(value, dict):
        raise ConfigError(f"配置项 {key} 必须是一个映射（键值对）")
    return value


def load_config(path: Path, *, fallback_username: str | None = None) -> Config:
    """读取 config.yaml 并校验，失败抛出 ConfigError（中文消息）。

    github.username 为空时可用 fallback_username（如 Actions 的
    GITHUB_REPOSITORY_OWNER）兜底，实现 fork 即用、零配置。
    """
    if not path.exists():
        raise ConfigError(f"找不到配置文件：{path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"配置文件 YAML 解析失败：{exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError("配置文件顶层必须是映射（键值对）")

    cfg = Config()
    sched = _section(data, "schedule")
    cfg.schedule = ScheduleConfig(enabled=bool(sched.get("enabled", True)))

    gh = _section(data, "github")
    username = str(gh.get("username") or "").strip()
    if not username and fallback_username:
        username = fallback_username.strip()
    if not username:
        raise ConfigError(
            "请先在 config.yaml 中填写 github.username（你的 GitHub 用户名）"
        )
    cfg.github = GitHubConfig(
        username=username,
        token_env=str(gh.get("token_env") or "GITHUB_TOKEN"),
    )

    repos = _section(data, "repos")
    exclude = repos.get("exclude") or []
    if not isinstance(exclude, list):
        raise ConfigError("repos.exclude 必须是列表")
    cfg.repos = ReposConfig(
        exclude_forks=bool(repos.get("exclude_forks", True)),
        exclude_archived=bool(repos.get("exclude_archived", False)),
        exclude=[str(x) for x in exclude],
    )

    feat = _section(data, "featured")
    sort_by = str(feat.get("sort_by") or "score")
    if sort_by not in VALID_SORT_BY:
        raise ConfigError(f"featured.sort_by 只能是 {VALID_SORT_BY} 之一，收到：{sort_by}")
    pin = feat.get("pin") or []
    if not isinstance(pin, list):
        raise ConfigError("featured.pin 必须是列表")
    cfg.featured = FeaturedConfig(
        count=int(feat.get("count", 0)),
        min_stars=int(feat.get("min_stars", 0)),
        recency_days=int(feat.get("recency_days", 0)),
        sort_by=sort_by,
        score_stars_weight=float(feat.get("score_stars_weight", 1.0)),
        score_recency_weight=float(feat.get("score_recency_weight", 0.5)),
        pin=[str(x) for x in pin],
    )

    readme = _section(data, "readme")
    cards_raw = readme.get("cards") or {}
    if not isinstance(cards_raw, dict):
        raise ConfigError("readme.cards 必须是映射（键值对）")
    show = cards_raw.get("show") or ["stats", "top_langs", "streak"]
    if not isinstance(show, list):
        raise ConfigError("readme.cards.show 必须是列表")
    for name in show:
        if name not in VALID_CARDS:
            raise ConfigError(f"readme.cards.show 含未知卡片：{name}（可选：{VALID_CARDS}）")
    sections_raw = readme.get("sections", {})
    if sections_raw is None:
        sections_raw = {}
    if not isinstance(sections_raw, dict):
        raise ConfigError("readme.sections 必须是映射（键值对）")
    sections = Sections()
    for key in ("bio", "stats", "pinned", "table"):
        if key in sections_raw:
            value = sections_raw[key]
            # 必须是真正的布尔值：YAML 里写 "false"（带引号）是字符串，静默当真会害人
            if not isinstance(value, bool):
                raise ConfigError(
                    f"readme.sections.{key} 必须是 true 或 false（收到：{value!r}）"
                )
            setattr(sections, key, value)
    mode = str(readme.get("mode") or "auto")
    if mode not in ("auto", "full"):
        raise ConfigError(
            f'readme.mode 只支持 "auto"（手写页只插简介块）/ "full"（整页接管），收到：{mode}'
        )
    cfg.readme = ReadmeConfig(
        enabled=bool(readme.get("enabled", True)),
        path=str(readme.get("path") or "README.md"),
        template=str(readme.get("template") or "templates/profile.md.j2"),
        cards=CardsConfig(
            theme=str(cards_raw.get("theme") or "github_dark"),
            show=[str(x) for x in show],
        ),
        include_update_time=bool(readme.get("include_update_time", True)),
        visitors_badge=bool(readme.get("visitors_badge", False)),
        bilingual=bool(readme.get("bilingual", True)),
        mode=mode,
        sections=sections,
    )

    ai = _section(data, "ai")
    provider = str(ai.get("provider") or "anthropic")
    if provider not in ("anthropic", "openai"):
        raise ConfigError(
            f'ai.provider 只能是 "anthropic" 或 "openai"（OpenAI 兼容接口），收到：{provider}'
        )
    base_url = str(ai.get("base_url") or "")
    if provider == "openai" and not base_url:
        raise ConfigError(
            'ai.provider="openai" 时必须填 ai.base_url（如 https://api.deepseek.com）'
        )
    language = str(ai.get("language") or "zh-CN")
    if language != "zh-CN":
        raise ConfigError(
            f'ai.language 目前只支持 "zh-CN"（输出语言由 readme.bilingual 控制），收到：{language}'
        )
    cfg.ai = AIConfig(
        enabled=bool(ai.get("enabled", True)),
        provider=provider,
        base_url=base_url.rstrip("/"),
        key_env=str(ai.get("key_env") or ""),
        model=str(ai.get("model") or ("deepseek-chat" if provider == "openai" else "claude-opus-5-5")),
        max_repos=int(ai.get("max_repos", 15)),
        max_age_days=int(ai.get("max_age_days", 14)),
        bio_min_chars=int(ai.get("bio_min_chars", 100)),
        bio_max_chars=int(ai.get("bio_max_chars", 150)),
        language=language,
        prompt_version=int(ai.get("prompt_version", 2)),
        fill_empty_descriptions=bool(ai.get("fill_empty_descriptions", True)),
    )

    # 有意变更（A-P0）：项目介绍功能已移除——用户 config 残留的 projects: 段
    # 一律静默忽略（_section 之外不再读取），不报错、不生效

    rr = _section(data, "repo_readmes")
    rr_include = rr.get("include") or []
    rr_exclude = rr.get("exclude") or []
    if not isinstance(rr_include, list):
        raise ConfigError("repo_readmes.include 必须是列表")
    if not isinstance(rr_exclude, list):
        raise ConfigError("repo_readmes.exclude 必须是列表")
    cfg.repo_readmes = RepoReadmesConfig(
        enabled=bool(rr.get("enabled", False)),
        include=[str(x) for x in rr_include],
        exclude=[str(x) for x in rr_exclude],
        max_diff_chars=int(rr.get("max_diff_chars", 6000)),
        prompt_version=int(rr.get("prompt_version", 1)),
        pr_branch=str(rr.get("pr_branch") or "readme-auto/update"),
    )

    # 仓库描述自动写回（2026-10-25）：默认关闭；开关不进 render_fingerprint——
    # 开启/关闭本身不该重写 README，用 repo_descriptions.is_dirty 单独开门
    rd = _section(data, "repo_descriptions")
    rd_include = rd.get("include") or []
    rd_exclude = rd.get("exclude") or []
    if not isinstance(rd_include, list):
        raise ConfigError("repo_descriptions.include 必须是列表")
    if not isinstance(rd_exclude, list):
        raise ConfigError("repo_descriptions.exclude 必须是列表")
    rd_enabled = rd.get("enabled", False)
    # 必须是真正的布尔值：YAML 里写 "false"（带引号）是字符串，bool() 会当成 True，
    # 从而意外开启「对外写仓库描述」——与 readme.sections 同款校验。
    if not isinstance(rd_enabled, bool):
        raise ConfigError(
            f"repo_descriptions.enabled 必须是 true 或 false（收到：{rd_enabled!r}）"
        )
    cfg.repo_descriptions = RepoDescriptionsConfig(
        enabled=rd_enabled,
        include=[str(x) for x in rd_include],
        exclude=[str(x) for x in rd_exclude],
    )

    snap = _section(data, "snapshot")
    cfg.snapshot = SnapshotConfig(path=str(snap.get("path") or "data/snapshot.json"))

    return cfg
