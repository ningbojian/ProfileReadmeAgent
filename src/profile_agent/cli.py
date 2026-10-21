"""命令行入口与编排：拉数据 → 聚合 → 增量判断 → AI 简介/项目介绍 → 渲染 → 写入。"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .aggregate import (
    bio_input_hash,
    build_bio_payload,
    compute_totals,
    normalize_repos,
    render_fingerprint,
    select_featured,
    stats_fingerprint,
)
from .bio import BioError, _is_fresh, generate_bio
from .config import Config, ConfigError, load_config
from .github_client import GitHubClient, GitHubError
from .models import Snapshot, TemplateContext
from .projects import intro_filename, intro_input_hash, update_project_intros
from .render import render_readme, write_if_changed
from .short_intro import generate_short_intro, short_intro_input_hash
from .snapshot import load_snapshot, save_snapshot

# README 展示时间统一按北京时间（UTC+8）标注
_BEIJING = timezone(timedelta(hours=8))


def _load_fixture(fixture: Path, username: str) -> tuple[dict, list[dict]]:
    user = json.loads((fixture / "user.json").read_text(encoding="utf-8"))
    repos = json.loads((fixture / "repos.json").read_text(encoding="utf-8"))
    user.setdefault("login", username)
    return user, repos


def _prev_repo_states(prev: Snapshot | None) -> dict[str, dict]:
    if prev is None:
        return {}
    return {r["full_name"]: r for r in prev.repos if isinstance(r, dict) and r.get("full_name")}


def _project_targets(repos, featured, cfg: Config) -> list:
    """按 featured_only + include/exclude 白黑名单选出要生成介绍的仓库。

    profile 仓库自身（用户名/用户名）硬性排除：它的 README 是工具自己的输出、
    pushed_at 又随每次 bot 提交前进——自引用会让它每轮都「过期」，无限重生成
    项目介绍并开 PR（实测踩过），AI 额度被空烧。
    """
    base = featured if cfg.projects.featured_only else repos
    inc = set(cfg.projects.include)
    exc = set(cfg.projects.exclude)
    self_repo = f"{cfg.github.username}/{cfg.github.username}"
    return [
        r for r in base
        if r.name not in exc
        and (not inc or r.name in inc)
        and r.full_name != self_repo
    ]


def _projects_clean(
    targets, cfg: Config, prev: Snapshot | None, root: Path, *, ai_active: bool = False
) -> bool:
    """全部目标仓库的介绍文件都已是最新且存在 → 无待生成内容。

    AI 已可用（ai_active）但某仓库还是数据版占位（ai_generated=False）时
    视为不干净：下一次普通运行就升级重写，不让占位内容永久豁免。
    """
    states = _prev_repo_states(prev)
    out_dir = root / cfg.projects.dir
    for r in targets:
        st = states.get(r.full_name) or {}
        if st.get("intro_hash") != intro_input_hash(r, cfg.ai):
            return False
        if not (out_dir / intro_filename(r)).exists():
            return False
        if ai_active and not st.get("ai_generated"):
            return False
    return True


def _memo_readme(gh_client: GitHubClient | None):
    """同一次运行内每个仓库只拉一次 README；无 client 时返回空串。"""
    cache: dict[str, str] = {}

    def get(full_name: str) -> str:
        if full_name not in cache:
            cache[full_name] = gh_client.get_repo_readme(full_name) if gh_client else ""
        return cache[full_name]

    return get


def _short_intros_clean(targets, cfg: Config, prev: Snapshot | None, readme_of) -> bool:
    """全部补位目标都有最新文案（指纹含 README 内容）→ 无待生成内容。"""
    states = _prev_repo_states(prev)
    for r in targets:
        st = states.get(r.full_name) or {}
        if not st.get("short_intro"):
            return False
        if st.get("short_intro_hash") != short_intro_input_hash(r, cfg.ai, readme_of(r.full_name)):
            return False
    return True


def _append_summary(markdown: str) -> None:
    """写入 GitHub Actions 的运行摘要页（GITHUB_STEP_SUMMARY）；本地运行静默跳过。"""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(markdown + "\n")
    except OSError:
        pass


def run(
    cfg_path: Path,
    *,
    dry_run: bool = False,
    force: bool = False,
    no_ai: bool = False,
    skip_readme: bool = False,
    skip_projects: bool = False,
    fixture: Path | None = None,
    only_repos: list[str] | None = None,
) -> int:
    fallback = os.environ.get("GITHUB_REPOSITORY_OWNER")
    try:
        cfg: Config = load_config(cfg_path, fallback_username=fallback)
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2

    # 手动点名仓库（--repos / 勾选板 /run）：精确到仓库一级，即使不在精选名单也生成；
    # 点名即「重跑」语义——忽略仓库级 hash 复用，强制重新生成
    named_repos = bool(only_repos and only_repos != ["all"])
    if named_repos:
        cfg.projects.include = list(only_repos)
        cfg.projects.featured_only = False

    root = cfg_path.parent
    readme_path = root / cfg.readme.path
    template_path = root / cfg.readme.template
    readme_active = cfg.readme.enabled and not skip_readme
    projects_active = cfg.projects.enabled and not skip_projects

    # 1. 取数
    gh_client: GitHubClient | None = None
    try:
        if fixture is not None:
            user, raw_repos = _load_fixture(fixture, cfg.github.username)
        else:
            token = os.environ.get(cfg.github.token_env) or None
            gh_client = GitHubClient(token)
            user = gh_client.get_user(cfg.github.username)
            raw_repos = gh_client.list_repos(cfg.github.username)
    except (GitHubError, OSError) as exc:
        print(f"获取数据失败：{exc}", file=sys.stderr)
        return 3

    # 2. 聚合
    repos = normalize_repos(raw_repos, cfg.repos)
    totals = compute_totals(user, repos)
    featured = select_featured(repos, cfg.featured)
    fingerprint = stats_fingerprint(totals, repos, featured)
    template_bytes = template_path.read_bytes() if template_path.exists() else b""
    cfg_hash = render_fingerprint(cfg, template_bytes, user)

    # 3. 增量门：数据、配置/模板/资料、README 存在、简介新鲜、项目介绍齐备 全部无变化才跳过
    snap_path = root / cfg.snapshot.path
    prev = load_snapshot(snap_path)
    proj_targets = _project_targets(repos, featured, cfg) if projects_active else []
    ai_active = bool(
        not no_ai and cfg.ai.enabled and os.environ.get(cfg.ai.key_env_name)
    )
    readme_of = _memo_readme(gh_client)
    # 描述补位目标：精选里 description 为空的仓库（开关关掉或无 AI key 则为空）；
    # profile 仓库自身同样排除——它的「README 内容」就是工具输出，自引用会每轮都变
    short_targets = (
        [
            r
            for r in featured
            if not r.description
            and r.full_name != f"{cfg.github.username}/{cfg.github.username}"
        ]
        if (readme_active and ai_active and cfg.ai.fill_empty_descriptions)
        else []
    )
    payload = build_bio_payload(user, featured, cfg.ai)
    cur_hash = bio_input_hash(payload, cfg.ai)
    if ai_active and prev is not None:
        bio_fresh = (
            bool(prev.bio.text)
            and prev.bio.input_hash == cur_hash
            and _is_fresh(prev.bio.generated_at, cfg.ai.max_age_days)
        )
    else:
        bio_fresh = True

    if (
        not force
        and not named_repos  # 点名即重跑：不走整体增量门
        and prev is not None
        and prev.stats_fingerprint == fingerprint
        and prev.stats_config_hash == cfg_hash
        and (not readme_active or readme_path.exists())
        and bio_fresh
        and (not projects_active or _projects_clean(proj_targets, cfg, prev, root, ai_active=ai_active))
        and (not short_targets or _short_intros_clean(short_targets, cfg, prev, readme_of))
    ):
        print("无变化，跳过更新")
        return 0

    # 4. 描述补位：缺 description 的精选仓库，用其 README 生成一句主页文案
    short_intros: dict[str, str] = {}
    short_states: dict[str, dict] = {}
    if short_targets:
        prev_states_all = _prev_repo_states(prev)
        for r in short_targets:
            readme_text = readme_of(r.full_name)
            h = short_intro_input_hash(r, cfg.ai, readme_text)
            st = prev_states_all.get(r.full_name) or {}
            if st.get("short_intro") and st.get("short_intro_hash") == h:
                short_intros[r.full_name] = st["short_intro"]
                continue
            try:
                text = generate_short_intro(r, cfg.ai, readme=readme_text)
            except Exception as exc:
                text = st.get("short_intro") or ""
                print(f"「{r.name}」描述补位失败，沿用旧文案：{exc}", file=sys.stderr)
            short_states[r.full_name] = {"short_intro": text, "short_intro_hash": h}
            if text:
                short_intros[r.full_name] = text

    # 4. AI 简介（只在更新 overview 时生成；失败保留旧简介，任务不失败）
    bio_text = prev.bio.text if prev else ""
    bio_generated = False
    if ai_active and readme_active:
        try:
            bio_text, bio_generated = generate_bio(
                payload,
                cfg.ai,
                input_hash=cur_hash,
                prev_hash=prev.bio.input_hash if prev else "",
                prev_text=prev.bio.text if prev else "",
                prev_generated_at=prev.bio.generated_at if prev else "",
            )
        except BioError as exc:
            print(f"简介生成失败，沿用旧简介：{exc}", file=sys.stderr)

    # 5. 项目介绍（方向二：仓库级增量；include/exclude 多选控制）
    project_states: dict[str, dict] = {}
    if projects_active and proj_targets:
        project_states = update_project_intros(
            proj_targets,
            dataclasses.replace(cfg.projects, dir=str(root / cfg.projects.dir)),
            cfg.ai,
            _prev_repo_states(prev),
            allow_ai=ai_active,
            # 真实抓取时把各仓库 README 拉下来喂给模型（介绍内容的信息密度来源）
            fetch_readme=gh_client.get_repo_readme if gh_client else None,
            # --force 或点名重跑：忽略仓库级 hash 复用
            force=force or named_repos,
        )

    now_bj = datetime.now(_BEIJING)
    changed = False
    if readme_active:
        ctx = TemplateContext(
            username=cfg.github.username,
            display_name=user.get("name") or cfg.github.username,
            blog=user.get("blog") or "",
            totals=totals,
            featured=featured,
            bio_text=bio_text,
            cards_theme=cfg.readme.cards.theme,
            cards_show=cfg.readme.cards.show,
            include_update_time=cfg.readme.include_update_time,
            updated_at=now_bj.strftime("%Y-%m-%d %H:%M") + "（北京时间）",
            short_intros=short_intros,
            visitors_badge=cfg.readme.visitors_badge,
            key_env=cfg.ai.key_env_name,
        )
        content = render_readme(ctx, template_path)

        if dry_run:
            print(content)
            return 0

        changed = write_if_changed(readme_path, content)
    elif dry_run:
        print("readme 已禁用（readme.enabled=false 或 --skip-readme），跳过 overview 生成")
        return 0

    # 6. 快照：bio 的 hash/时间只在本次真正生成时前进，复用/回退时保留旧值
    now_iso = datetime.now(timezone.utc).isoformat()
    if bio_generated:
        bio_info = dataclasses.replace(
            prev.bio if prev else Snapshot().bio,
            text=bio_text,
            input_hash=cur_hash,
            generated_at=now_iso,
            model=cfg.ai.model,
            prompt_version=cfg.ai.prompt_version,
        )
    else:
        bio_info = dataclasses.replace(
            prev.bio if prev else Snapshot().bio,
            text=bio_text,
        )

    merged_states = _prev_repo_states(prev)
    merged_states.update(project_states)
    merged_states.update(short_states)
    new_snap = Snapshot(
        schema=2,
        username=cfg.github.username,
        stats_fingerprint=fingerprint,
        stats_config_hash=cfg_hash,
        totals={
            "public_repos": totals.public_repos,
            "followers": totals.followers,
            "total_stars": totals.total_stars,
        },
        repos=[
            {
                "full_name": r.full_name,
                "stars": r.stars,
                "language": r.language,
                # 供 workflow 勾选框标签显示「最近推送日期」
                "pushed_at": r.pushed_at.isoformat() if r.pushed_at else "",
                **{
                    k: v
                    for k, v in (merged_states.get(r.full_name) or {}).items()
                    if k.startswith("intro_") or k.startswith("short_intro") or k == "ai_generated"
                },
            }
            for r in repos
        ],
        bio=bio_info,
        generated_at=now_iso,
    )
    save_snapshot(snap_path, new_snap)

    # 7. 运行明细：逐仓库列出本次做了什么（写入 Actions 摘要页 + 日志）
    if proj_targets or short_targets:
        prev_states_all = _prev_repo_states(prev)
        proj_dir = root / cfg.projects.dir
        actions: dict[str, list[str]] = {}

        def _cell(name: str, col: int, value: str) -> None:
            actions.setdefault(name, ["—", "—"])[col] = value

        for r in proj_targets:
            st = prev_states_all.get(r.full_name) or {}
            new_st = project_states.get(r.full_name) or {}
            if not new_st.get("regenerated") and st.get("intro_hash") == intro_input_hash(
                r, cfg.ai
            ) and (proj_dir / intro_filename(r)).exists():
                action = "跳过（无变化）"
            elif new_st.get("ai_generated"):
                action = "已重新生成（AI，走 PR 审核）"
            else:
                action = "已生成数据版（AI 未启用或失败）"
            _cell(r.name, 0, action)
        for r in short_targets:
            if r.full_name not in short_states:
                _cell(r.name, 1, "已是最新（复用）")
            elif short_states[r.full_name].get("short_intro"):
                _cell(r.name, 1, "已生成补位文案")
            else:
                _cell(r.name, 1, "生成失败（保留暂无描述）")
        lines = [
            "## 本次运行明细（逐仓库）",
            "",
            "| 仓库 | 项目介绍 | 主页描述补位 |",
            "| --- | --- | --- |",
        ]
        for name, (a, b) in actions.items():
            lines.append(f"| `{name}` | {a} | {b} |")
        lines += [
            "",
            f"全部公开仓库（可在 projects_repos 里点名）：{'、'.join(sorted(r.name for r in repos))}",
        ]
        _append_summary("\n".join(lines))
        print("\n".join(lines))

    print("已更新" if changed else "内容无差异")
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    parser = argparse.ArgumentParser(description="自动维护 GitHub Profile README")
    parser.add_argument("--config", default="config.yaml", help="配置文件路径")
    parser.add_argument("--dry-run", action="store_true", help="打印到 stdout，不写文件")
    parser.add_argument("--force", action="store_true", help="忽略增量检测，强制重新生成")
    parser.add_argument("--no-ai", action="store_true", help="跳过 AI 调用（仍生成数据版内容）")
    parser.add_argument("--skip-readme", action="store_true", help="本次不更新 overview README")
    parser.add_argument("--skip-projects", action="store_true", help="本次不生成项目介绍")
    parser.add_argument(
        "--repos",
        default=None,
        help="只更新这些仓库的介绍（逗号分隔仓库名，可点名非精选仓库）；all = 全部精选",
    )
    parser.add_argument("--fixture", default=None, help="从本地 fixture 目录读取数据（离线测试）")
    args = parser.parse_args(argv)
    only_repos = None
    if args.repos:
        only_repos = [s.strip() for s in args.repos.split(",") if s.strip()] or None
    return run(
        Path(args.config),
        dry_run=args.dry_run,
        force=args.force,
        no_ai=args.no_ai,
        skip_readme=args.skip_readme,
        skip_projects=args.skip_projects,
        fixture=Path(args.fixture) if args.fixture else None,
        only_repos=only_repos,
    )


if __name__ == "__main__":
    raise SystemExit(main())
