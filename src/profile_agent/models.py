"""核心数据结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Repo:
    name: str
    full_name: str
    description: str = ""
    language: str = ""
    stars: int = 0
    forks: int = 0
    private: bool = False
    fork: bool = False
    archived: bool = False
    pushed_at: datetime | None = None
    html_url: str = ""
    homepage: str = ""
    topics: list[str] = field(default_factory=list)

    @property
    def age_days(self) -> float | None:
        """距上次 push 的天数；无 push 记录返回 None。"""
        if self.pushed_at is None:
            return None
        delta = datetime.now(self.pushed_at.tzinfo) - self.pushed_at
        return delta.total_seconds() / 86400


@dataclass
class Totals:
    public_repos: int = 0
    followers: int = 0
    total_stars: int = 0
    top_languages: list[tuple[str, int]] = field(default_factory=list)


@dataclass
class TemplateContext:
    username: str
    display_name: str
    blog: str
    totals: Totals
    featured: list[Repo]
    bio_text: str = ""
    cards_theme: str = "github_dark"
    cards_show: list[str] = field(default_factory=lambda: ["stats", "top_langs", "streak"])
    include_update_time: bool = True
    updated_at: str = ""
    short_intros: dict[str, str] = field(default_factory=dict)  # full_name → AI 补位文案
    visitors_badge: bool = False  # 主页嵌入第三方「主页访问量」计数徽章
    key_env: str = "ANTHROPIC_API_KEY"  # 用户实际配置的 AI key secret 名（简介缺失提示用）


@dataclass
class BioInfo:
    text: str = ""
    input_hash: str = ""
    generated_at: str = ""
    model: str = ""
    prompt_version: int = 0


@dataclass
class Snapshot:
    schema: int = 2
    username: str = ""
    stats_fingerprint: str = ""
    stats_config_hash: str = ""
    totals: dict = field(default_factory=dict)
    repos: list[dict] = field(default_factory=list)
    bio: BioInfo = field(default_factory=BioInfo)
    generated_at: str = ""
