"""aggregate.py 过滤/统计/精选/指纹测试。"""

import json
from datetime import datetime, timezone
from pathlib import Path

from profile_agent.aggregate import (
    bio_input_hash,
    build_bio_payload,
    compute_totals,
    normalize_repos,
    resolve_pins,
    select_featured,
    stats_fingerprint,
)
from profile_agent.config import AIConfig, FeaturedConfig, ReposConfig

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 21, tzinfo=timezone.utc)


def _raw_repos() -> list[dict]:
    return json.loads((FIXTURES / "repos.json").read_text(encoding="utf-8"))


def _user() -> dict:
    return json.loads((FIXTURES / "user.json").read_text(encoding="utf-8"))


def test_normalize_filters_private_fork_archived_and_blacklist():
    cfg = ReposConfig(exclude_forks=True, exclude_archived=False, exclude=["alice/old-profile"])
    repos = normalize_repos(_raw_repos(), cfg)
    names = {r.full_name for r in repos}
    assert "alice/secret" not in names  # private 跳过
    assert "alice/forked" not in names  # fork 跳过
    assert "alice/old-profile" not in names  # 黑名单跳过
    assert "alice/cool-tool" in names
    assert "alice/archived-one" in names  # exclude_archived=False 时保留


def test_normalize_keeps_forks_when_disabled():
    cfg = ReposConfig(exclude_forks=False)
    names = {r.full_name for r in normalize_repos(_raw_repos(), cfg)}
    assert "alice/forked" in names


def test_normalize_parses_pushed_at():
    cfg = ReposConfig()
    repos = {r.full_name: r for r in normalize_repos(_raw_repos(), cfg)}
    assert repos["alice/cool-tool"].pushed_at == datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc)


def test_normalize_carries_default_branch_with_main_fallback():
    """终审 I2 回归：default_branch 随仓库数据带出（模式 B PR 目标 = 默认分支，§5.5）；
    载荷缺失时回落 main。"""
    cfg = ReposConfig()
    raw = _raw_repos()
    for item in raw:
        item["default_branch"] = "master"
    repos = {r.full_name: r for r in normalize_repos(raw, cfg)}
    assert repos["alice/cool-tool"].default_branch == "master"
    # 载荷没有该字段（如精简 fixture）→ 回落 main
    fallback = {r.full_name: r for r in normalize_repos(_raw_repos(), cfg)}
    assert fallback["alice/cool-tool"].default_branch == "main"


def test_compute_totals():
    repos = normalize_repos(_raw_repos(), ReposConfig(exclude=["alice/old-profile"]))
    totals = compute_totals(_user(), repos)
    assert totals.public_repos == 42
    assert totals.followers == 128
    # 默认剔除 fork 与 private：120 + 80(老项目) + 30(归档)
    assert totals.total_stars == 120 + 80 + 30
    assert totals.top_languages[0] == ("Python", 120)  # 按 star 数排的语言
    assert ("JavaScript", 80) in totals.top_languages


def test_featured_excludes_by_recency_and_min_stars():
    feat = FeaturedConfig(count=10, min_stars=50, recency_days=365, sort_by="score")
    repos = normalize_repos(_raw_repos(), ReposConfig())
    featured = select_featured(repos, feat, now=NOW)
    names = {r.full_name for r in featured}
    assert "alice/old-thing" not in names  # 2020 年 push，超出 365 天
    assert "alice/secret" not in names  # private 仓库在 normalize 阶段已剔除
    assert "alice/cool-tool" in names


def test_featured_sorts_by_score_and_takes_count():
    feat = FeaturedConfig(count=2, min_stars=0, recency_days=365, sort_by="score")
    repos = normalize_repos(
        _raw_repos(), ReposConfig(exclude_forks=False, exclude=["alice/old-profile"])
    )
    featured = select_featured(repos, feat, now=NOW)
    assert len(featured) == 2
    # forked: 999★ 得分 3+0.44；cool-tool: 120★ 得分 2.08+0.47 → forked 在前
    assert featured[0].full_name == "alice/forked"
    assert featured[1].full_name == "alice/cool-tool"


def test_featured_sort_by_stars():
    feat = FeaturedConfig(count=3, min_stars=0, recency_days=0, sort_by="stars")
    repos = normalize_repos(_raw_repos(), ReposConfig(exclude=["alice/old-profile"]))
    featured = select_featured(repos, feat, now=NOW)
    stars = [r.stars for r in featured]
    assert stars == sorted(stars, reverse=True)


def test_featured_count_zero_returns_all():
    """count: 0 = 不限量：过滤后全部入选，不再截断。"""
    feat = FeaturedConfig(count=0, min_stars=0, recency_days=0, sort_by="stars")
    repos = normalize_repos(_raw_repos(), ReposConfig(exclude=["alice/old-profile"]))
    featured = select_featured(repos, feat, now=NOW)
    assert {r.full_name for r in featured} == {r.full_name for r in repos}


def test_featured_count_zero_still_applies_recency_filter():
    """count 只解「截断」不解「过滤」：recency_days > 0 时老仓库照样被排除。"""
    feat = FeaturedConfig(count=0, min_stars=0, recency_days=365, sort_by="score")
    repos = normalize_repos(_raw_repos(), ReposConfig(exclude=["alice/old-profile"]))
    featured = select_featured(repos, feat, now=NOW)
    names = {r.full_name for r in featured}
    assert "alice/old-thing" not in names  # 2020 年 push，超出 365 天
    assert "alice/cool-tool" in names


def test_featured_pin_first_in_config_order():
    """置顶（2026-10-23 用户需求「自定义置顶展示的 repo」）：pin 名单按配置顺序
    排最前（与 star 排序相反也如此），其余仓库保持 sort_by 原顺序紧随其后。"""
    feat = FeaturedConfig(
        count=0, sort_by="stars", pin=["alice/old-thing", "alice/cool-tool"]
    )
    repos = normalize_repos(
        _raw_repos(), ReposConfig(exclude_forks=False, exclude=["alice/old-profile"])
    )
    names = [r.full_name for r in select_featured(repos, feat, now=NOW)]
    assert names[:2] == ["alice/old-thing", "alice/cool-tool"]  # ① pin 顺序
    assert names[2:] == ["alice/forked", "alice/archived-one"]  # ② 其余保持 stars 降序


def test_featured_pin_bypasses_min_stars_and_recency():
    """置顶豁免精选层启发式（min_stars/recency_days）——显式点名的展示意图优先，
    否则置顶的老项目/低星项目会静默消失；未置顶仓库的过滤行为不变。"""
    feat = FeaturedConfig(
        count=0, min_stars=100, recency_days=365, sort_by="stars", pin=["alice/old-thing"]
    )
    repos = normalize_repos(_raw_repos(), ReposConfig(exclude=["alice/old-profile"]))
    names = [r.full_name for r in select_featured(repos, feat, now=NOW)]
    assert names[0] == "alice/old-thing"  # 80★、2020 年 push——双过滤本会剔除
    assert "alice/cool-tool" in names  # 未置顶正常入选
    assert "alice/archived-one" not in names  # 未置顶照旧被过滤（30★<100 且超龄）


def test_featured_pin_occupies_count_quota():
    """置顶占 count 名额：count 是整个精选列表的唯一硬上限；pin 超 count 只留靠前的；
    count=0 不截断（置顶全部 + 其余全部）。"""
    repos = normalize_repos(
        _raw_repos(), ReposConfig(exclude_forks=False, exclude=["alice/old-profile"])
    )
    feat3 = FeaturedConfig(
        count=3, sort_by="stars", pin=["alice/old-thing", "alice/cool-tool"]
    )
    assert [r.full_name for r in select_featured(repos, feat3, now=NOW)] == [
        "alice/old-thing",
        "alice/cool-tool",
        "alice/forked",
    ]
    feat1 = FeaturedConfig(
        count=1, sort_by="stars", pin=["alice/old-thing", "alice/cool-tool"]
    )
    assert [r.full_name for r in select_featured(repos, feat1, now=NOW)] == [
        "alice/old-thing"
    ]  # ② pin 超 count → 只留靠前的
    feat0 = FeaturedConfig(count=0, sort_by="stars", pin=["alice/old-thing"])
    names0 = [r.full_name for r in select_featured(repos, feat0, now=NOW)]
    assert names0[0] == "alice/old-thing" and len(names0) == 4  # ③ count=0 不截断


def test_featured_pin_ignores_unknown_duplicate_and_hard_filtered():
    """pin 越不过 normalize 硬过滤（private/fork/黑名单——repos 入参已剔除，pin 看不到）；
    未知名静默忽略（与 repos.exclude 容错一致）、重复取首现、大小写敏感精确匹配。"""
    feat = FeaturedConfig(
        count=0,
        sort_by="stars",
        pin=[
            "alice/nope",  # 不存在（写错/改名）→ 静默忽略
            "alice/old-thing",
            "old-thing",  # 裸名重复指同一仓 → 去重取首现
            "Alice/Old-Thing",  # 大小写不同 ≠ 命中（与 exclude 一致）
            "alice/secret",  # private：normalize 已剔除，pin 拉不回
            "alice/forked",  # fork：默认剔除，pin 拉不回
            "alice/old-profile",  # 黑名单：pin 拉不回
        ],
    )
    repos = normalize_repos(_raw_repos(), ReposConfig(exclude=["alice/old-profile"]))
    names = [r.full_name for r in select_featured(repos, feat, now=NOW)]
    assert names[0] == "alice/old-thing"
    assert names.count("alice/old-thing") == 1  # 去重
    assert "alice/secret" not in names
    assert "alice/forked" not in names
    assert "alice/nope" not in names


def test_featured_pin_matches_bare_name():
    """裸名（短名）也能命中——与 repos.exclude 的肌肉记忆一致；文档推荐 full_name。"""
    feat = FeaturedConfig(count=0, sort_by="stars", pin=["old-thing"])
    repos = normalize_repos(_raw_repos(), ReposConfig(exclude=["alice/old-profile"]))
    assert select_featured(repos, feat, now=NOW)[0].full_name == "alice/old-thing"


def test_resolve_pins_order_dedupe_unknown_and_bare_name():
    """resolve_pins（2026-10-23）：pin 名单 → 置顶仓库列表——按名单顺序、
    full_name/裸名精确匹配、重复取首现、未知静默忽略；cli 渲染置顶板块用它
    再与精选名单求交（count 截断语义保持）。"""
    repos = normalize_repos(_raw_repos(), ReposConfig(exclude=["alice/old-profile"]))
    got = [
        r.full_name
        for r in resolve_pins(
            repos, ["alice/nope", "alice/old-thing", "old-thing", "cool-tool"]
        )
    ]
    assert got == ["alice/old-thing", "alice/cool-tool"]  # 保序、去重、忽略未知
    assert resolve_pins(repos, []) == []  # 无置顶 → 空列表


def test_stats_fingerprint_changes_with_pin_order():
    """置顶顺序变化 → featured 顺序变化 → stats_fingerprint 变化（增量门自动覆盖的
    性质锁住，防未来重构丢掉「改 pin 即重渲染」）。"""
    repos = normalize_repos(_raw_repos(), ReposConfig(exclude=["alice/old-profile"]))
    totals = compute_totals(_user(), repos)
    a = select_featured(
        repos,
        FeaturedConfig(count=0, sort_by="stars", pin=["alice/old-thing", "alice/cool-tool"]),
        now=NOW,
    )
    b = select_featured(
        repos,
        FeaturedConfig(count=0, sort_by="stars", pin=["alice/cool-tool", "alice/old-thing"]),
        now=NOW,
    )
    assert stats_fingerprint(totals, repos, a) != stats_fingerprint(totals, repos, b)


def test_fingerprint_stable_and_sensitive():
    repos = normalize_repos(_raw_repos(), ReposConfig(exclude=["alice/old-profile"]))
    totals = compute_totals(_user(), repos)
    fp1 = stats_fingerprint(totals, repos)
    fp2 = stats_fingerprint(totals, repos)
    assert fp1 == fp2
    assert fp1.startswith("sha256:")
    # star 数变化 → 指纹变化
    repos2 = normalize_repos(_raw_repos(), ReposConfig(exclude=["alice/old-profile"]))
    for r in repos2:
        if r.name == "cool-tool":
            r.stars += 1
    assert stats_fingerprint(totals, repos2) != fp1


def test_bio_payload_and_hash():
    feat_cfg = FeaturedConfig(count=2, recency_days=0)
    ai_cfg = AIConfig(max_repos=2, prompt_version=1)
    repos = normalize_repos(_raw_repos(), ReposConfig(exclude=["alice/old-profile"]))
    featured = select_featured(repos, feat_cfg, now=NOW)
    payload = build_bio_payload(_user(), featured, ai_cfg)
    assert payload["name"] == "Alice Zhang"
    assert len(payload["repos"]) <= 2
    h1 = bio_input_hash(payload, ai_cfg)
    assert h1.startswith("sha256:")
    assert bio_input_hash(payload, ai_cfg) == h1
    # prompt_version 变化 → hash 变化（强制重新生成）
    ai_cfg2 = AIConfig(max_repos=2, prompt_version=2)
    assert bio_input_hash(payload, ai_cfg2) != h1


def test_fingerprint_changes_when_featured_selection_changes():
    """同一份仓库数据，精选结果不同（如时间推移导致 aged out）→ 指纹必须变化。"""
    from profile_agent.config import Config
    from profile_agent.aggregate import render_fingerprint

    repos = normalize_repos(_raw_repos(), ReposConfig(exclude=["alice/old-profile"]))
    totals = compute_totals(_user(), repos)
    feat_a = select_featured(repos, FeaturedConfig(count=2, recency_days=0), now=NOW)
    feat_b = select_featured(repos, FeaturedConfig(count=1, recency_days=0), now=NOW)
    assert stats_fingerprint(totals, repos, feat_a) != stats_fingerprint(totals, repos, feat_b)


def test_render_fingerprint_sensitive_to_config_and_template_and_profile():
    import dataclasses
    from profile_agent.aggregate import render_fingerprint
    from profile_agent.config import Config

    cfg = Config()
    user = _user()
    fp1 = render_fingerprint(cfg, b"template-bytes", user)
    assert fp1.startswith("sha256:")
    assert render_fingerprint(cfg, b"template-bytes", user) == fp1
    # 主题变化 → 指纹变化
    cfg2 = dataclasses.replace(cfg)
    cfg2.readme = dataclasses.replace(cfg.readme)
    cfg2.readme.cards = dataclasses.replace(cfg.readme.cards, theme="github_light")
    assert render_fingerprint(cfg2, b"template-bytes", user) != fp1
    # 模板内容变化 → 指纹变化
    assert render_fingerprint(cfg, b"other-template", user) != fp1
    # 用户资料变化 → 指纹变化
    user2 = dict(user, name="Bob")
    assert render_fingerprint(cfg, b"template-bytes", user2) != fp1


def test_normalize_parses_topics():
    raw = _raw_repos()
    for r in raw:
        r.setdefault("topics", ["django", "web"] if r["name"] == "cool-tool" else [])
    repos = {r.full_name: r for r in normalize_repos(raw, ReposConfig(exclude=["alice/old-profile"]))}
    assert repos["alice/cool-tool"].topics == ["django", "web"]
    assert repos["alice/old-thing"].topics == []


def test_topics_affect_fingerprint_and_bio_payload():
    raw = _raw_repos()
    for r in raw:
        r.setdefault("topics", [])
    cfg = ReposConfig(exclude=["alice/old-profile"])
    repos_a = normalize_repos(raw, cfg)
    totals = compute_totals(_user(), repos_a)
    fp_a = stats_fingerprint(totals, repos_a, [])
    raw2 = json.loads(json.dumps(raw))
    for r in raw2:
        if r["name"] == "cool-tool":
            r["topics"] = ["cli", "tools"]
    repos_b = normalize_repos(raw2, cfg)
    assert stats_fingerprint(totals, repos_b, []) != fp_a
    payload = build_bio_payload(_user(), repos_b, AIConfig())
    assert payload["repos"][0]["topics"] == ["cli", "tools"]


def test_render_fingerprint_changes_with_ai_language():
    """ai.language 进指纹：改主语言 → 指纹变化（强制重渲染，双语首跑开门）。"""
    import dataclasses
    from profile_agent.aggregate import render_fingerprint
    from profile_agent.config import Config

    cfg = Config()
    user = _user()
    fp = render_fingerprint(cfg, b"t", user)
    cfg2 = dataclasses.replace(cfg)
    cfg2.ai = dataclasses.replace(cfg.ai, language="zh-TW")
    assert render_fingerprint(cfg2, b"t", user) != fp
