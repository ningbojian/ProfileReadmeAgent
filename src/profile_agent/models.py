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
    # 仓库默认分支（模式 B 的 PR 目标，§5.5）；/users/{u}/repos 载荷缺失时回落 main
    default_branch: str = "main"
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
class Sections:
    """Overview README 板块开关（默认全开；只隐藏生成区，不动手写/custom 内容）。"""

    bio: bool = True
    stats: bool = True
    pinned: bool = True
    table: bool = True


@dataclass
class TemplateContext:
    username: str
    display_name: str
    blog: str
    totals: Totals
    featured: list[Repo]
    pinned: list[Repo] = field(default_factory=list)  # 置顶仓库（featured.pin 命中且未被 count 截断）
    bio_text: str = ""
    cards_theme: str = "github_dark"
    cards_show: list[str] = field(default_factory=lambda: ["stats", "top_langs", "streak"])
    include_update_time: bool = True
    updated_at: str = ""
    short_intros: dict[str, str] = field(default_factory=dict)  # full_name → AI 补位文案
    visitors_badge: bool = False  # 主页嵌入第三方「主页访问量」计数徽章
    key_env: str = "ANTHROPIC_API_KEY"  # 用户实际配置的 AI key secret 名（简介缺失提示用）
    # ---- 双语/保留（2026-10-23 WP3-WP6；A-WP2 起置顶面板整体下线）----
    bio_text_en: str = ""  # 英文简介（en 文件用；空 → 英文占位注释）
    short_intros_en: dict[str, str] = field(default_factory=dict)  # 英文补位文案
    switch_zh_href: str = "./README.md"  # 顶部切换行互链（双文件互切）
    switch_en_href: str = "./README.en.md"
    custom_text: str = ""  # custom 标记区内容（WP5：逐文件独立提取、永久保留）
    display_descs: dict[str, str] = field(default_factory=dict)  # A-WP-B：en 专用 AI 英译描述（zh 恒空 → 照搬原文）
    sections: Sections = field(default_factory=Sections)  # readme.sections 板块开关（默认全开）


@dataclass
class BioInfo:
    text: str = ""
    text_en: str = ""  # 英文简介（bilingual 单次 JSON 双语调用产出；schema 4）
    input_hash: str = ""
    generated_at: str = ""
    model: str = ""
    prompt_version: int = 0
    # 英文被丢弃时记下的输入指纹：同一输入只试一次，避免每轮重生成
    # （数据变化 / 改 ai.prompt_version / 简介过期后才会重新尝试）
    en_dropped_hash: str = ""


@dataclass
class Snapshot:
    schema: int = 5
    username: str = ""
    stats_fingerprint: str = ""
    stats_config_hash: str = ""
    totals: dict = field(default_factory=dict)
    repos: list[dict] = field(default_factory=list)
    bio: BioInfo = field(default_factory=BioInfo)
    # 模式 B（repo_readmes）逐仓状态：full_name → {sha, block_hash, mode,
    # failed_hash, last_input_hash, pending, updated_at}（设计 §5.1，schema 3）
    repo_readmes: dict[str, dict] = field(default_factory=dict)
    # 仓库描述写回逐仓状态：full_name → {baseline, previous, written,
    # pushed_at, generated_at, manual}（schema 5；baseline 是首次见到的描述，
    # written 是工具上次成功写入的值——当前描述 ≠ written 即视为用户手改）
    repo_descriptions: dict[str, dict] = field(default_factory=dict)
    # 最近一次生效置顶（原生 Pinned 解析结果；读取失败时为回退结果，schema 4）
    pins: list[str] = field(default_factory=list)
    # 上次渲染输出（非合并写入）——生成区三路合并的 base，zh/en 各自独立（schema 4）
    readme_last: str = ""
    readme_last_en: str = ""
    generated_at: str = ""
