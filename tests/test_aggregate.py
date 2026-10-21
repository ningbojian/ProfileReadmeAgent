"""aggregate.py 过滤/统计/精选/指纹测试。"""

import json
from datetime import datetime, timezone
from pathlib import Path

from profile_agent.aggregate import (
    bio_input_hash,
    build_bio_payload,
    compute_totals,
    normalize_repos,
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
