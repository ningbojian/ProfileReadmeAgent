"""命令行入口与编排：拉数据 → 聚合 → 增量判断 → AI 简介 → 渲染 → 写入。"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import ai_failures, usage
from .aggregate import (
    bio_input_hash,
    build_bio_payload,
    compute_totals,
    normalize_repos,
    render_fingerprint,
    resolve_pins,
    select_featured,
    stats_fingerprint,
)
from .bio import BioError, _is_fresh, generate_bio
from .checklist import (
    UNSET,
    append_final_state,
    find_checklist_issue,
    get_issue,
    parse_merge_targets,
    sync_from_issue_event,
    update_checklist,
)
from .config import Config, ConfigError, load_config
from .desc_translate import contains_cjk, translate_descriptions
from .github_client import API, GitHubClient, GitHubError
from .models import Snapshot, TemplateContext
from .preserve import extract_custom, merge_readme_3way
from .readme_block import block_body, markers_balanced, strip_block, upsert_block
from .render import embed_body, render_readme, write_if_changed
from .repo_descriptions import (
    apply_updates,
    generate_descriptions,
    is_dirty,
    plan_updates,
    summary_lines as description_summary_lines,
    target_language,
)
from .repo_readmes import CompareGone, compute_queue, participants
from .repo_readmes_ai import MISSING_KEY_REASON, process_queue
from .repo_readmes_pr import (
    TAKEOVER_BRANCH,
    advance_baseline_from_merged_pr,
    get_open_pr,
    get_pr_detail,
    get_pr_state,
    merge_pr,
    open_or_refresh_pr,
    parse_commit_titles,
    push_results,
    takeover_pr_body,
    takeover_pr_title,
)
from .short_intro import generate_short_intro, short_intro_input_hash
from .snapshot import load_snapshot, save_snapshot, snapshot_text
from .text_lang import language_mismatch
from .wait_merges import wait_first_line, wait_for_merges

# README 展示时间统一按北京时间（UTC+8）标注
_BEIJING = timezone(timedelta(hours=8))

# A-WP2 一次性迁移标记：盘上 README 残留旧版置顶面板/指针文案 → 强制开门摘除。
# 模板指纹变化本就会开门一次；此条件兜底客制模板 fork 等指纹未变的场景。
# 自终止：迁移后盘上无标记 → 下轮为假 → noop（0 AI）
LEGACY_MARKERS_ZH = ("<!-- pin-config:start -->", "页面底部的置顶配置面板")
LEGACY_MARKERS_EN = (
    "Pin configuration lives in the pin panel",
    "the pin panel at the bottom",
)


def _load_fixture(fixture: Path, username: str) -> tuple[dict, list[dict]]:
    user = json.loads((fixture / "user.json").read_text(encoding="utf-8"))
    repos = json.loads((fixture / "repos.json").read_text(encoding="utf-8"))
    user.setdefault("login", username)
    return user, repos


def _prev_repo_states(prev: Snapshot | None) -> dict[str, dict]:
    if prev is None:
        return {}
    return {r["full_name"]: r for r in prev.repos if isinstance(r, dict) and r.get("full_name")}


def _memo_readme(gh_client: GitHubClient | None):
    """同一次运行内每个仓库只拉一次 README；无 client 时返回空串。"""
    cache: dict[str, str] = {}

    def get(full_name: str) -> str:
        if full_name not in cache:
            cache[full_name] = gh_client.get_repo_readme(full_name) if gh_client else ""
        return cache[full_name]

    get.cache = cache  # 模式 B 预填 §5.6 内存/分支内容（A 同轮吸收）
    return get


def _fetchers_from_client(client: GitHubClient):
    """生产路径的 head/compare 取数：compare 归一化为队列层契约，404/409 → CompareGone。"""

    class _Fetchers:
        def fetch_head(self, full_name: str) -> str:
            data, _ = client._get(f"{API}/repos/{full_name}/commits/HEAD")
            assert isinstance(data, dict)
            return str(data.get("sha") or "")

        def fetch_compare(self, full_name: str, base: str, head: str):
            # REST compare 只认 base...head（三个点）：两个点一律 404（实测 7 仓全中），
            # 且与令牌、历史重写无关——别改回两个点。
            try:
                data, _ = client._get(f"{API}/repos/{full_name}/compare/{base}...{head}")
            except GitHubError as exc:
                if getattr(exc, "status", None) in (404, 409):
                    raise CompareGone(str(exc)) from exc
                raise
            assert isinstance(data, dict)
            return {
                "commits": [
                    {"sha": c.get("sha"), "message": (c.get("commit") or {}).get("message", "")}
                    for c in (data.get("commits") or [])
                    if isinstance(c, dict)
                ],
                "files": [f for f in (data.get("files") or []) if isinstance(f, dict)],
            }

    return _Fetchers()


def _fetch_tree_paths(client: GitHubClient, full_name: str, sha: str) -> list[str]:
    """二进制降级用文件树（§5.3）：只取 blob 路径。"""
    data, _ = client._get(f"{API}/repos/{full_name}/git/trees/{sha}?recursive=1")
    assert isinstance(data, dict)
    return [str(t.get("path") or "") for t in (data.get("tree") or []) if t.get("type") == "blob"]


def _save_b_snapshot(snap_path: Path, prev: Snapshot | None, cfg: Config, state: dict) -> None:
    """B 完成即落盘（§4 第 1 步）：等待期间被取消也不丢基线与 AI 缓存（SB22/SB23）。"""
    base = prev if prev is not None else Snapshot(username=cfg.github.username)
    save_snapshot(
        snap_path,
        dataclasses.replace(
            base,
            repo_readmes=state,
            generated_at=datetime.now(timezone.utc).isoformat(),
        ),
    )


def _short_intros_clean(targets, cfg: Config, prev: Snapshot | None, readme_of) -> bool:
    """全部补位目标都有最新双语文案（指纹含 README 内容）→ 无待生成内容。"""
    states = _prev_repo_states(prev)
    for r in targets:
        st = states.get(r.full_name) or {}
        if not st.get("short_intro"):
            return False
        # 双语下 en 缺失同样视为不完整——否则 hash 命中却永远单语，门永远关着（WP4 死锁防护）
        if cfg.readme.bilingual and not st.get("short_intro_en"):
            return False
        if st.get("short_intro_hash") != short_intro_input_hash(
            r, cfg.ai, readme_of(r.full_name), bilingual=cfg.readme.bilingual
        ):
            return False
    return True


def _report_custom_mode(mode: str, where: str, *, embed: bool = False) -> None:
    """WP5 custom 区状态提示（take_over/broken 才出声，正常路径零扰民）。"""
    if mode == "take_over":
        if embed:
            print(
                f"{where}：检测到手写 README，auto 模式只插入/更新 AI 简介块，其余内容不动",
                file=sys.stderr,
            )
        else:
            print(
                f"{where}：检测到手写 README（无 profile-agent 标记），原文已整体保留到 custom 区",
                file=sys.stderr,
            )
    elif mode == "broken":
        print(
            f"警告：{where} 的 custom 标记不配对（start/end 须各恰好一次），本次不改动该文件",
            file=sys.stderr,
        )


def _repo_rel_path(path: Path, root: Path) -> tuple[str, bool]:
    """本地路径 → 仓库相对路径（PR 的 tree 只接受相对路径）。

    配置把 README/快照放在仓库外时回落文件名并返回 False，调用方据此警告。
    """
    try:
        return path.resolve().relative_to(root.resolve()).as_posix(), True
    except ValueError:
        return path.name, False


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


def _emit_usage(started_at: float, b_stats: dict | None = None) -> None:
    """运行末尾的数字：AI 请求 / 缓存复用 / 仓库无需重跑率 / 耗时。

    这些是「增量更新省了多少调用」的唯一证据，日志与摘要都写一份。
    """
    for line in usage.summary_lines(time.monotonic() - started_at, b_stats):
        print(line)
        _append_summary(f"- {line}")


_B_STAT_LABELS = (
    ("queued", "入队"),
    ("first_seen", "首见记基线"),
    ("readme_only", "仅README推进基线"),
    ("no_change", "无新提交"),
    ("reset", "基线重置"),
    ("error", "读取失败"),
)


def _b_status_line(stats: dict, reason: str = "") -> str:
    """「模式 B 状态」一行（设计 §8①：静默必须能被看懂是设计如此还是出问题了）。"""
    parts = [f"{label} {stats.get(key, 0)} 仓" for key, label in _B_STAT_LABELS if stats.get(key)]
    if parts:
        body = "、".join(parts) + (f"（{reason}）" if reason else "")
    else:
        body = reason or "无事可做"
    return "模式 B 状态：" + body


def _resolve_github_token(cfg) -> str | None:
    """读仓库用的 token。

    Actions 内置的 GITHUB_TOKEN 只对**当前仓库**有效：拿它去读账号下的其他仓库
    （哪怕是公开仓库），GitHub 一律返回 404 而不是 403，用户只会看到「全部仓库
    读取失败」。workflow 已经把 WORKFLOW_PAT 注入环境，配了就用它——用户配这把
    令牌本来就是为了读别的仓库。显式把 github.token_env 写成别的名字时照常尊重。
    """
    token = os.environ.get(cfg.github.token_env) or ""
    if cfg.github.token_env in ("", "GITHUB_TOKEN"):
        token = os.environ.get("WORKFLOW_PAT") or token
    return token or None


def _token_kind(token: str) -> str:
    """按前缀判断令牌类型（不打印令牌本身，只看类型）。"""
    if not token:
        return "匿名"
    for prefix, name in (
        ("github_pat_", "fine-grained"),
        ("ghp_", "classic"),
        ("ghs_", "GitHub App 安装令牌"),
        ("gho_", "OAuth"),
    ):
        if token.startswith(prefix):
            return f"{name}（{prefix}）"
    return "未知类型"


def _token_desc(token: str | None, client=None) -> str:
    """「类型 · 账号 · scopes」一句话——404 时唯一能自证是不是拿错了令牌的信息。"""
    if not token:
        return "匿名（未配置令牌）"
    info: dict = {}
    if client is not None:
        try:
            info = client.token_info()
        except Exception:  # noqa: BLE001 —— 诊断信息，取不到就算了
            info = {}
    login = info.get("login") or "未知"
    scopes = info.get("scopes") or ""
    tail = f" · scopes: {scopes}" if scopes else " · scopes: 未返回（fine-grained / App 安装令牌）"
    return f"{_token_kind(token)} · 账号 {login}{tail}"


def _pat_404_hint(stats: dict, token_desc: str = "") -> str:
    """404 占多数时的令牌提示：带出本轮用的令牌类型与账号，再说排查顺序。

    真实原因常常是「用错了 token」或「令牌类型不对」，而不是仓库不存在——
    不提示就会被当成工具故障。
    """
    not_found = int(stats.get("not_found") or 0)
    errors = int(stats.get("error") or 0)
    if not_found < 2 or not_found * 2 < errors:
        return ""
    who = f"本轮读取用的令牌：{token_desc}。" if token_desc else ""
    return (
        f"⚠️ 读取失败多为 404：{who}内置 GITHUB_TOKEN 只对当前仓库有效，"
        "fine-grained 令牌对未勾选仓库同样一律 404；"
        "请确认 github.token_env 指向 WORKFLOW_PAT，且该 secret 里是 classic 令牌的最新值"
    )


def _report_ai_failures() -> None:
    """运行结束时统一播报 AI 服务商侧失败（额度/认证/请求头/限流）。

    这类失败会静默降级成「沿用缓存」，不显式说明就会被误当成工具本身的毛病。
    """
    if not ai_failures.failures():
        return
    block = ai_failures.summary_markdown()
    _append_summary(block)
    print(block)
    for line in ai_failures.annotations():
        print(line)


def _b_repo_progress(r) -> str:
    """单仓进度描述（OR20 逐仓进度行；与 ProcessResult.status 一一对应）。"""
    return {
        "generated": "已推分支（PR 待审核）",
        "cached": "缓存命中，跳过 AI",
        "suppressed": "与上次失败输入相同，已抑制",
        "failed": "AI 失败（已记录，同输入下轮跳过）",
        "skipped": "README marker 不平衡，已跳过",
    }.get(r.status, "缺 key 积压")


_DEFAULT_TEMPLATE = "templates/profile.md.j2"


def _resolve_template(root: Path, configured: str) -> Path:
    """默认模板跟随上游工具目录；自定义路径永远以本地配置为准。

    可复用 workflow 调用时，用户仓库没有 templates/：workflow 会把工具检出到
    PROFILE_AGENT_HOME 并注入同名环境变量。默认路径优先上游（模板副本也能收到
    模板更新）；readme.template 指向别的文件时保持配置优先。
    """
    tool_home = os.environ.get("PROFILE_AGENT_HOME") or ""
    if tool_home and configured == _DEFAULT_TEMPLATE:
        upstream = Path(tool_home) / configured
        if upstream.is_file():
            return upstream
    local = root / configured
    if local.exists():
        return local
    # 本地没有：再试环境变量，最后试 editable 安装的仓库根（本地 CLI 兜底）
    for base in (tool_home, str(Path(__file__).resolve().parents[2])):
        if base and (Path(base) / configured).is_file():
            return Path(base) / configured
    return local


def run(
    cfg_path: Path,
    *,
    dry_run: bool = False,
    force: bool = False,
    no_ai: bool = False,
    skip_readme: bool = False,
    fixture: Path | None = None,
    readme_fetchers=None,
    readme_generate=None,
    b_client=None,
    issue_client=None,
    b_force: bool = False,
    merge_wait_minutes: float = 0,
    now_fn=None,
    sleep_fn=None,
) -> int:
    ai_failures.reset()  # 每轮开始清空：进程内可能跑多轮（测试/连续本地运行）
    usage.reset()  # 用量统计同理：摘要里的「AI 请求 / 缓存复用」必须只算本轮
    started_at = time.monotonic()
    token_desc = ""  # 「令牌类型 · 账号」诊断信息，读到仓库后填（404 提示会带上）
    fallback = os.environ.get("GITHUB_REPOSITORY_OWNER")
    try:
        cfg: Config = load_config(cfg_path, fallback_username=fallback)
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2

    # 手动点名仓库机制已随项目介绍功能一并下线（A-P0 有意变更：唯一作用面是 projects 点名）
    root = cfg_path.parent
    readme_path = root / cfg.readme.path
    # 双语第二文件（2026-10-23 WP3）：README.md → 同目录 README.en.md，零配置派生
    en_readme_path = readme_path.with_name(readme_path.stem + ".en.md")
    template_path = _resolve_template(root, cfg.readme.template)
    readme_active = cfg.readme.enabled and not skip_readme

    # A-WP2：面板已下线——prev 提前加载（A-WP3：native 失败时的回退来源）
    snap_path = root / cfg.snapshot.path
    prev = load_snapshot(snap_path)

    # 1. 取数
    gh_client: GitHubClient | None = None
    native_pins: list[str] | None = None  # A-WP3：原生 Pinned（None=读取失败/不可用）
    try:
        if fixture is not None:
            user, raw_repos = _load_fixture(fixture, cfg.github.username)
            # fixture 语义：pinned.json 存在=读取成功（官方置顶序，[]=成功清空）；
            # 缺文件=读取失败（None）→ 回退阶梯。默认 fixtures 无此文件 → 存量测试零影响
            pinned_file = fixture / "pinned.json"
            if pinned_file.exists():
                native_pins = json.loads(pinned_file.read_text(encoding="utf-8"))
        else:
            token = _resolve_github_token(cfg)
            gh_client = GitHubClient(token)
            token_desc = _token_desc(token, gh_client)
            print(f"GitHub 令牌：{token_desc}")
            user = gh_client.get_user(cfg.github.username)
            raw_repos = gh_client.list_repos(cfg.github.username)
            native_pins = gh_client.get_pinned_repos(cfg.github.username)
            # 仅「token 在场但返回 None」警告（无 token 是既定本地行为，静默走回退）
            if token and native_pins is None:
                print(
                    "警告：读取 GitHub 原生 Pinned 失败（GraphQL 网络/权限异常），本轮沿用上次置顶",
                    file=sys.stderr,
                )
    except (GitHubError, OSError) as exc:
        print(f"获取数据失败：{exc}", file=sys.stderr)
        return 3

    # A-WP3 三段阶梯：原生成功（含 []：清空就是没置顶，config 不介入）一律信任；
    # 读取失败沿用快照 pins 防闪断（指纹不变→noop）；无快照首跑回退 config 种子
    if native_pins is not None:
        effective_pin = list(native_pins)
    elif prev and prev.pins:
        effective_pin = list(prev.pins)
    else:
        effective_pin = list(cfg.featured.pin)

    # 2. 聚合（精选/置顶统一用 effective_pin：原生 Pinned 解析或回退结果，A-WP3 三段阶梯）
    repos = normalize_repos(raw_repos, cfg.repos)
    totals = compute_totals(user, repos)
    featured_cfg = dataclasses.replace(cfg.featured, pin=effective_pin)
    featured = select_featured(repos, featured_cfg)
    # 置顶板块：pin 命中且仍在精选名单内（count 截断语义由 select_featured 统一负责），按 pin 顺序
    featured_names = {r.full_name for r in featured}
    pinned = [
        r for r in resolve_pins(repos, effective_pin) if r.full_name in featured_names
    ]
    fingerprint = stats_fingerprint(totals, repos, featured)
    template_bytes = template_path.read_bytes() if template_path.exists() else b""
    cfg_hash = render_fingerprint(cfg, template_bytes, user)

    # 3. 增量门：数据、配置/模板/资料、README 存在、简介新鲜 全部无变化才跳过
    # A-WP2 迁移门：盘上残留旧版置顶面板/指针 → 开门一次性摘除（0 AI；自终止）。
    # （prev 已在上方 effective_pin 处加载；zh/en 盘上内容在此读取，渲染段复用不再二读）
    disk_zh = readme_path.read_text(encoding="utf-8") if readme_path.exists() else ""
    disk_en = (
        en_readme_path.read_text(encoding="utf-8")
        if cfg.readme.bilingual and en_readme_path.exists()
        else ""
    )
    # 首次接管检测（2026-10-25）：必须早于增量门——数据/配置完全没变时，
    # 第一次接管仍然要运行（否则手写 README 永远等不到预览 PR）。
    custom_zh, mode_zh = extract_custom(disk_zh)
    custom_en, mode_en = (
        extract_custom(disk_en)
        if (readme_active and cfg.readme.bilingual)
        else ("", "machine")
    )
    # 手写保护（2026-10-25）：auto 只在手写 README 的标题后插一个 AI 简介块；
    # full 才整页接管（先开预览 PR）。两个模式共用「broken 一律不写」的安全底线。
    handwritten = "take_over" in (mode_zh, mode_en)
    use_embed = readme_active and cfg.readme.mode == "auto" and handwritten
    embed_zh_due = use_embed and mode_zh == "take_over"
    embed_en_due = (
        use_embed
        and cfg.readme.bilingual
        and mode_en == "take_over"
        and en_readme_path.exists()
    )
    broken_zh = mode_zh == "broken" or (
        use_embed and mode_zh == "take_over" and not markers_balanced(disk_zh)
    )
    broken_en = mode_en == "broken" or (
        use_embed and mode_en == "take_over" and not markers_balanced(disk_en)
    )
    broken_pending = readme_active and (broken_zh or broken_en)
    takeover_active = (
        readme_active
        and cfg.readme.mode == "full"
        and handwritten
        and not broken_pending
    )
    # 首次插入 / 用户删块 / 手改块 / 简介已缓存但块没写：都必须开门补齐。
    # 用缓存简介比较（AI 生成在门之后），避免「块缺失但数据没变」被永久跳过。
    _prev_bio_zh = prev.bio.text if prev else ""
    _prev_bio_en = prev.bio.text_en if prev else ""
    embed_pending = bool(
        (
            embed_zh_due
            and _prev_bio_zh
            and block_body(disk_zh) != embed_body("zh", _prev_bio_zh)
        )
        or (
            embed_en_due
            and _prev_bio_en
            and block_body(disk_en) != embed_body("en", _prev_bio_en)
        )
    )
    legacy_residue = readme_active and (
        any(m in disk_zh for m in LEGACY_MARKERS_ZH)
        or any(m in disk_en for m in LEGACY_MARKERS_EN)
    )
    if legacy_residue:
        print("警告：检测到旧版置顶面板/指针（一次性迁移），本轮已清除", file=sys.stderr)
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
        if (
            readme_active
            and ai_active
            and cfg.ai.fill_empty_descriptions
            # 置顶与列表都关 → 没有任何仓库展示位，补位文案无处可去（也防门反复开门）
            and (cfg.readme.sections.pinned or cfg.readme.sections.table)
        )
        else []
    )
    payload = build_bio_payload(user, featured, cfg.ai, bilingual=cfg.readme.bilingual)
    cur_hash = bio_input_hash(payload, cfg.ai)
    if ai_active and cfg.readme.sections.bio and prev is not None:
        bio_fresh = (
            bool(prev.bio.text)
            # 双语下 en 缺失 → 单语不完整；但若对**当前输入**已经试过且只拿到单语
            # （en_dropped_hash 命中），就接受单语，等数据变化/prompt_version/过期再试。
            and (
                not cfg.readme.bilingual
                or bool(prev.bio.text_en)
                or prev.bio.en_dropped_hash == cur_hash
            )
            and prev.bio.input_hash == cur_hash
            and _is_fresh(prev.bio.generated_at, cfg.ai.max_age_days)
        )
    else:
        bio_fresh = True

    # 门的开门条件新增一条（设计 §7）：任一仓库留有积压标记（pending）→ 开门，
    # 否则「提交发生在缺 key 期间、指纹随后被记录」的积压会永远饿死
    b_pending = prev is not None and any(
        bool(e.get("pending")) for e in prev.repo_readmes.values()
    )

    # ---- 模式 B 前置（2026-10-23 设计验证修订）：PAT/清单通道/开局勾选收割先于增量门 ----
    # 勾选（- [x]）是用户的人工授权动作：无条件收割，门早退不得让它永不生效；
    # 门再由 merge_requested/b_force 决定是否放行整轮 B
    owner_repo = f"{cfg.github.username}/{cfg.github.username}"
    issue: dict | None = None
    issue_eff = None
    # 开局预取的清单 issue（2026-10-23 钩子①）：UNSET=未预取/预取失败（清单步现场搜索），
    # dict=确认命中（清单步免搜索），None=确认无清单（直接创建）
    issue_cached: object = UNSET
    checklist_closed = False
    merge_requested = False  # 门条件之一：清单里存在勾选或「全部合并」总开关（无论合并成败，用户在要求动作）
    attempted: set[tuple[str, int]] = set()  # 同轮每条勾选至多一次 PUT（防重复与误报）

    # PAT 解析（Option A）：注入参数优先；生产路径从 WORKFLOW_PAT 构造（fixture 离线不读环境）
    b_client_eff = b_client
    if b_client_eff is None and fixture is None:
        _pat = os.environ.get("WORKFLOW_PAT") or None
        if _pat:
            b_client_eff = GitHubClient(_pat)
    if b_force and not cfg.repo_readmes.enabled:
        _conflict = (
            "已勾选 repo_readmes 但 config.yaml 中 repo_readmes.enabled=false，本次未运行模式 B"
        )
        print(_conflict)
        _append_summary(f"- ⚠️ {_conflict}")

    # 清单通道提前解析（2026-10-23 钩子①）：开局预取清单、读勾选需要它
    # （IW27 权限分离保持：清单读写走 GITHUB_TOKEN/issue_client，合并走 PAT）
    if cfg.repo_readmes.enabled and b_client_eff is not None and not dry_run:
        if issue_client is not None:
            issue_eff = issue_client
        else:
            _tok = os.environ.get(cfg.github.token_env) or None
            if _tok:
                issue_eff = GitHubClient(_tok)

    def _merge_from_issue(src, where: str) -> list[tuple[str, int]]:
        """按清单勾选（- [x]）逐条代为合并（2026-10-23 §5.5 修订：勾选=人工授权）。

        返回成功合并的 (full_name, number) 列表（钩子①' 用它推进基线）；单条失败只
        记录不中断（修复后重新勾选、下轮重试）；同轮已试过的条目直接跳过（attempted：
        防重复 PUT 与「成功后再报失败」）；src 非 dict（None=无清单、UNSET=预取失败
        未命中）直接跳过——UNSET 是 truthy 哨兵，不能只判 falsy。

        2026-10-23 交互治理两道防线：
        - allowed 过滤：full_name ∉ 本账号仓库集合 → 忽略（清单 body 可被网页编辑，
          伪造链接不得借 PAT 合并他人仓库）；
        - PUT 前先 GET 状态降噪：已合并 → 「已合并，无需处理」不 PUT（修复 405 吓人
          报错）；已关闭 → 「已关闭（拒绝）」不 PUT。已合并的条目仍进 merged_refs——
          同轮推基线，下轮不重跑该批次。
        """
        if not isinstance(src, dict) or b_client_eff is None:
            return []
        # 本账号仓库集合（normalize 后本轮可见仓库 + 主页仓库本身）：PR 只可能来自它们
        allowed = {r.full_name for r in repos} | {owner_repo}
        merged_refs: list[tuple[str, int]] = []
        for _cf, _cn in parse_merge_targets(str(src.get("body") or "")):
            if (_cf, _cn) in attempted:
                continue
            attempted.add((_cf, _cn))
            if _cf not in allowed:
                _line = (
                    f"「{_cf}」PR #{_cn} 不在本账号仓库范围，已忽略"
                    f"（{where}；清单正文可被编辑，防伪造链接误合并他人仓库）"
                )
                print(_line, file=sys.stderr)
                _append_summary(f"- ⚠️ {_line}")
                continue
            _state = get_pr_state(b_client_eff, _cf, _cn)
            if _state == "merged":
                merged_refs.append((_cf, _cn))  # 已合并 → 同轮推基线即可
                _line = f"「{_cf}」PR #{_cn} 已合并，无需处理（{where}）"
                print(_line)
                _append_summary(f"- ✅ {_line}")
                continue
            if _state == "closed":
                _line = f"「{_cf}」PR #{_cn} 已关闭（拒绝），不会合并（{where}）"
                print(_line, file=sys.stderr)
                _append_summary(f"- ⚠️ {_line}")
                continue
            _ok, _why = merge_pr(b_client_eff, _cf, _cn)
            if _ok:
                merged_refs.append((_cf, _cn))
                _line = f"「{_cf}」PR #{_cn} 已按清单勾选合并（{where}）"
                print(_line)
                _append_summary(f"- ✅ {_line}")
            else:
                _line = (
                    f"「{_cf}」PR #{_cn} 勾选合并失败（{where}；修复后重新勾选，下轮重试）：{_why}"
                )
                print(_line, file=sys.stderr)
                _append_summary(f"- ⚠️ {_line}")
        return merged_refs

    # 钩子①（2026-10-23 勾选合并）：预取清单 → 按勾选代为合并。无条件先于增量门——
    # 门若先早退，cron 轮里的勾选将永不生效（设计验证实测缺口）
    if b_client_eff is not None and not dry_run and issue_eff is not None:
        try:
            issue_cached = find_checklist_issue(issue_eff, owner_repo)
        except Exception as exc:  # noqa: BLE001 —— 预取失败退化为清单步现场搜索
            print(f"清单预取失败（勾选合并跳过，清单步会重查）：{exc}", file=sys.stderr)
        if isinstance(issue_cached, dict):
            # 「全部合并」总开关与逐条勾选同为授权（parse_merge_targets 并集口径）
            merge_requested = bool(parse_merge_targets(str(issue_cached.get("body") or "")))
        _merge_from_issue(issue_cached, "开局")

    # 首次接管 PR 检测（2026-10-25）：放在勾选合并之后、增量门之前。
    # 勾选是人工授权，门早退不得吞掉；但接管 PR 未合并前暂停本轮 B/描述写回，
    # 避免快照分叉与「预览还没看，外部状态先被改」。
    profile_base = "main"
    if takeover_active:
        _profile_repo = next((r for r in repos if r.full_name == owner_repo), None)
        if _profile_repo is not None and _profile_repo.default_branch:
            profile_base = _profile_repo.default_branch
        elif b_client_eff is not None and not dry_run:
            # profile 仓库常被 repos.exclude 排除（示例配置就建议排除），此时查一次
            # 仓库元数据拿真实默认分支；master 仓库不再因为硬编码 main 而开不出 PR
            try:
                _repo_data, _ = b_client_eff._get(f"{API}/repos/{owner_repo}")
                profile_base = str(
                    (_repo_data or {}).get("default_branch") or "main"
                )
            except Exception as exc:  # noqa: BLE001 —— 查不到按 main，PR 失败会回退
                print(f"读取主页仓库默认分支失败（按 main 处理）：{exc}", file=sys.stderr)
    takeover_pr: dict | None = None
    # 手写页也查一次：从 full 切到 auto 时，旧接管 PR 可能还开着。
    # 不查会直接写 main，之后有人合并旧 PR 就把嵌入块覆盖掉了。
    if readme_active and handwritten and b_client_eff is not None and not dry_run:
        try:
            takeover_pr = get_open_pr(
                b_client_eff, owner_repo, TAKEOVER_BRANCH, base=profile_base
            )
        except Exception as exc:  # noqa: BLE001 —— 查询失败不阻断本轮渲染
            print(f"检查首次接管 PR 失败（本轮继续，按无 PR 处理）：{exc}", file=sys.stderr)
    if takeover_pr is not None:
        _num = takeover_pr.get("number")
        _url = takeover_pr.get("html_url") or ""
        if use_embed:
            _line = (
                f"检测到旧的整页接管 PR #{_num} 仍待合并：auto 模式不会改写 main"
                f"（避免合并旧 PR 时覆盖你的排版）。请关闭该 PR 以保留手写排版，"
                f"或把 readme.mode 设为 full 继续整页方案：{_url}"
            )
            print(_line, file=sys.stderr)
            _append_summary(f"- ⏳ {_line}")
            return 0
        if force:
            # 用户明确要求强制重写：刷新这条待审核的 PR（仍不写 main）。
            # 分支上可能有人手动解决过冲突——必须提前说清会被覆盖。
            _line = (
                f"接管 PR #{_num} 仍待合并：将按最新 main 重新生成并刷新该 PR"
                "（分支上手动改动会被覆盖），main 仍不改动"
            )
            print(_line)
            _append_summary(f"- ♻️ {_line}")
        else:
            _line = (
                f"首次接管 PR #{_num} 仍待合并，本轮跳过；"
                f"合并后自动进入正常更新：{_url}"
            )
            print(_line)
            _append_summary(f"- ⏳ {_line}")
            return 0

    # 仓库描述写回（2026-10-25）：开关不进 render_fingerprint，用 desc_dirty 单独开门。
    # 首次记基线、有新推送或手动修改识别都属于「有待办」——否则门关着基线永远写不进去。
    desc_entries: dict[str, dict] = dict(prev.repo_descriptions) if prev else {}
    descriptions_active = bool(
        cfg.repo_descriptions.enabled
        and ai_active
        and b_client_eff is not None
        and not dry_run
        and not takeover_active
    )
    desc_dirty = descriptions_active and is_dirty(
        repos, cfg.repo_descriptions, desc_entries, cfg.github.username
    )
    if cfg.repo_descriptions.enabled and not dry_run:
        if b_client_eff is None:
            _line = "仓库描述写回已开启，但缺少 WORKFLOW_PAT，本次跳过（配置后自动启用）"
            print(_line, file=sys.stderr)
            _append_summary(f"- ⚠️ {_line}")
        elif not no_ai and not ai_active:
            _line = "仓库描述写回已开启，但缺少 AI key，本次跳过（配好后自动重试）"
            print(_line, file=sys.stderr)
            _append_summary(f"- ⚠️ {_line}")

    # 增量门（2026-10-23 设计验证修订）：b_force（对话框承诺「全量重跑开 PR」，Q21）与
    # 清单勾选（merge_requested，用户在要求动作）都必须开门；enabled=false 时勾选只警告
    # 不运行（Q22）→ b_gate 含 enabled 判定，冲突警告已在上方输出
    b_gate = b_force and cfg.repo_readmes.enabled
    if (
        not force
        and not b_gate
        and not merge_requested
        and prev is not None
        and prev.stats_fingerprint == fingerprint
        and prev.stats_config_hash == cfg_hash
        and (not readme_active or readme_path.exists())
        # 双语开时 en 文件被手删也视为「缺失」开门重建（WP3）
        and (not (readme_active and cfg.readme.bilingual) or en_readme_path.exists())
        # A-WP2：旧面板残留（一次性迁移）开门（仅渲染+写入，0 AI）
        and not legacy_residue
        and bio_fresh
        and (not short_targets or _short_intros_clean(short_targets, cfg, prev, readme_of))
        and not b_pending
        and not desc_dirty  # 描述写回有待办（首次记基线/新推送）时开门
        and not takeover_active  # 首次接管：指纹没变也必须开门
        and not embed_pending  # 手写页待插/待更新简介块（首插、删块、手改块）
        and not broken_pending  # marker 损坏：每轮开门提示修复，直到用户处理
    ):
        usage.note_cache("short_intro", len(short_targets))
        _emit_usage(started_at)
        print("无变化，跳过更新")
        return 0

    # 3.5 模式 B（M3）：合并检测 → 队列 → AI → 推送/PR → 即时落盘 → 清单 → 等待 → §5.6 新鲜度
    # --no-ai 时整个 AI 阶段跳过且不置 pending（显式关 AI 是开发/测试语义，不是缺 key 积压）
    repo_readmes_state: dict[str, dict] = dict(prev.repo_readmes) if prev else {}
    b_stats: dict = {}
    b_reason = ""
    b_crash = ""  # 整体（非逐仓）失败原因：状态行不得用「无事可做」掩盖（生产 404 事故回归）
    b_results = []
    b_merged: set[str] = set()
    open_prs: list[dict] = []
    b_readme_sources: dict[str, str] = {}  # §5.6：full_name → 内存/API 真源/分支/旧文
    fetchers = readme_fetchers
    if fetchers is None and b_client_eff is not None and cfg.repo_readmes.enabled:
        fetchers = _fetchers_from_client(b_client_eff)
    # B 活跃条件：启用 且（测试注入 fetchers 或 生产 PAT 可用）——queue-only 模式合法（IW10）
    # 首次接管轮暂停模式 B：PR 预览还没合并，不能先动其他仓库的外部状态
    b_active = (
        cfg.repo_readmes.enabled
        and (fetchers is not None or b_client_eff is not None)
        and not takeover_active
    )
    # 各仓默认分支（§5.5 PR 目标；master 仓库不再硬编码 main 导致 422）
    default_bases = {r.full_name: r.default_branch for r in repos}

    if b_active:
        b_progress: list[str] = []  # 逐仓进度行（OR20：串行 AI 静默期的可见性，写 Summary）
        b_errors: list[str] = []  # 逐仓失败行（OR06：异常不跨仓传播，写 Summary）
        b_warns: list[str] = []  # 语言提醒（只提醒，不阻断）
        try:
            # 合并检测：已合并 bot PR 的基线推进到 PR head SHA（§12-R10）；b_merged 供 §5.3 剔除
            if b_client_eff is not None and not dry_run:
                for _fn, _entry in list(repo_readmes_state.items()):
                    if not _entry.get("sha"):
                        continue
                    try:
                        if advance_baseline_from_merged_pr(
                            b_client_eff,
                            _entry,
                            _fn,
                            cfg.repo_readmes.pr_branch,
                            base=default_bases.get(_fn, "main"),
                        ):
                            b_merged.add(_fn)
                    except Exception as exc:  # noqa: BLE001 —— 逐仓隔离：一仓合并检测失败不拖死其他仓
                        print(
                            f"「{_fn}」合并检测失败（本轮跳过该仓，不影响其他仓库）：{exc}",
                            file=sys.stderr,
                        )
                        b_errors.append(f"- `{_fn}` 合并检测失败：{exc}")

            b_queue = compute_queue(
                repos,
                repo_readmes_state,
                cfg.repo_readmes,
                cfg.github.username,
                fetch_head=fetchers.fetch_head,
                fetch_compare=fetchers.fetch_compare,
                # 有意变更（A-P0 交互治理）：force 只负责主页 A 侧；模式 B 的强制
                # 只认 --repo-readmes（与 workflow「强制重跑各仓库」下拉一一对应），
                # 消除「force 已包含 repo_readmes、两勾重叠」的困惑
                force=b_force,
                stats=b_stats,
            )
            # 逐仓走完「AI → 推送」再进下一仓（OR20：第一仓终态行先于第二仓进度行）；
            # 逐仓 try 隔离（OR06：一仓推送失败不拖死其他仓）
            if b_queue and not no_ai:
                for item in b_queue:
                    try:
                        print(f"「{item.full_name}」AI 生成中（缓存/失败抑制则直接跳过）…")
                        one = process_queue(
                            [item],
                            repo_readmes_state,
                            cfg.ai,
                            cfg.repo_readmes,
                            ai_active=ai_active,
                            generate=readme_generate,
                            fetch_readme=(
                                b_client_eff.get_repo_readme if b_client_eff is not None else None
                            ),
                            fetch_readme_en=(
                                (
                                    lambda fn: b_client_eff.get_repo_readme(
                                        fn, path="README.en.md"
                                    )
                                )
                                if b_client_eff is not None and cfg.readme.bilingual
                                else None
                            ),
                            fetch_tree=(
                                (lambda fn, sha: _fetch_tree_paths(b_client_eff, fn, sha))
                                if b_client_eff is not None
                                else None
                            ),
                            strip_readme=b_merged,
                            # 有意变更（A-P0）：B 侧强制只认 --repo-readmes，不叠加主页 force
                            force=b_force,
                            bilingual=cfg.readme.bilingual,
                        )
                        b_results.extend(one)
                        if b_client_eff is not None and not dry_run:
                            # 只推 generated；成功后 entries 回写 block_hash（SB07）
                            for res in push_results(
                                b_client_eff,
                                one,
                                entries=repo_readmes_state,
                                branch=cfg.repo_readmes.pr_branch,
                                bases=default_bases,
                            ):
                                print(f"「{item.full_name}」已推分支：{res.html_url}")
                        for r in one:
                            b_progress.append(f"- `{r.full_name}`：{_b_repo_progress(r)}")
                            for _w in r.warnings:
                                b_warns.append(f"- ⚠️ `{r.full_name}`：{_w}")
                    except Exception as exc:  # noqa: BLE001 —— 逐仓隔离，不跨仓传播（OR06）
                        print(
                            f"「{item.full_name}」模式 B 失败（不影响其他仓库与模式 A）：{exc}",
                            file=sys.stderr,
                        )
                        b_errors.append(f"- `{item.full_name}`：{exc}")
            # B 完成即落盘（§4 第 1 步，SB22/23）：等待期间被取消也不丢基线与 AI 缓存
            if not dry_run:
                _save_b_snapshot(snap_path, prev, cfg, repo_readmes_state)
        except Exception as exc:  # noqa: BLE001 —— B 任何异常都不得阻断 A（§4 护栏）
            print(f"模式 B 失败（不影响模式 A）：{exc}", file=sys.stderr)
            b_errors.append(f"- 整体：{exc}")
            b_crash = str(exc)
        if b_progress or b_errors or b_warns:
            _append_summary(
                "### 模式 B 逐仓进度\n" + "\n".join(b_progress + b_errors + b_warns)
            )

    # 钩子①'（2026-10-23 设计验证修订）：扫描前再收割一次——AI/推送阶段（可达数分钟）期间
    # 新勾选若不先收割会被清单重建抹掉；合并成功即同轮推进该仓基线（attempted 挡重复 PUT）
    def _advance_after_merge(refs: list[tuple[str, int]]) -> None:
        """勾选合并（含已合并降噪分流）的条目同轮推进基线；逐仓隔离不拖死后续流程。"""
        for _cf, _cn in refs:
            _merged_entry = repo_readmes_state.get(_cf)
            if _merged_entry and _merged_entry.get("sha"):
                try:
                    if advance_baseline_from_merged_pr(
                        b_client_eff,
                        _merged_entry,
                        _cf,
                        cfg.repo_readmes.pr_branch,
                        base=default_bases.get(_cf, "main"),
                    ):
                        b_merged.add(_cf)
                except Exception as exc:  # noqa: BLE001 —— 逐仓隔离：推进失败不拖死扫描
                    print(
                        f"「{_cf}」合并后基线推进失败（不影响清单重建）：{exc}",
                        file=sys.stderr,
                    )

    def _reread_issue() -> dict | None:
        """按号复读清单最新 body（dict 预取 → 按号 GET；确认无 → None；预取失败 → 重查）。"""
        if isinstance(issue_cached, dict):
            return get_issue(issue_eff, owner_repo, int(issue_cached["number"]))
        if issue_cached is None:
            return None  # 开局确认无既有清单：本轮新建的不可能有用户勾选
        return find_checklist_issue(issue_eff, owner_repo)  # 预取失败退化重查

    if b_client_eff is not None and not dry_run and issue_eff is not None:
        try:
            _advance_after_merge(_merge_from_issue(_reread_issue(), "推送后"))
        except Exception as exc:  # noqa: BLE001 —— 收割失败不拖死扫描与清单
            print(f"扫描前清单收割失败（忽略，清单重建照旧）：{exc}", file=sys.stderr)

    # open-PR 扫描（清单/等待的输入）；§5.6 新鲜度预填延后到等待之后——
    # 等待中被结清的 PR 不得再用分支内容（OR04 回退旧文防线）
    if b_client_eff is not None and cfg.repo_readmes.enabled and not dry_run:
        for _fn, _entry in sorted(repo_readmes_state.items()):
            if not _entry.get("sha"):
                continue
            pr = get_open_pr(
                b_client_eff, _fn, cfg.repo_readmes.pr_branch,
                base=default_bases.get(_fn, "main"),
            )
            if pr:
                pr_info = {
                    "full_name": _fn,
                    "html_url": pr.get("html_url", ""),
                    "number": pr.get("number"),
                    "branch": cfg.repo_readmes.pr_branch,
                }
                # W3 清单明细（2026-10-23）：list 接口不带统计——逐 PR 单 GET；
                # 失败降级为无细节条目（清单省略该行，不拖死扫描）
                try:
                    det = get_pr_detail(b_client_eff, _fn, int(pr["number"]))
                except Exception as exc:  # noqa: BLE001 —— 明细失败不影响清单主行
                    print(
                        f"「{_fn}」PR 明细获取失败（清单该条省略细节）：{exc}",
                        file=sys.stderr,
                    )
                    det = None
                if isinstance(det, dict):
                    for _k in ("additions", "deletions", "changed_files", "commits"):
                        if isinstance(det.get(_k), int):
                            pr_info[_k] = det[_k]
                    if isinstance(det.get("mergeable_state"), str):
                        pr_info["mergeable_state"] = det["mergeable_state"]
                    pr_info["commit_titles"] = parse_commit_titles(
                        str(det.get("body") or "")
                    )
                open_prs.append(pr_info)

    # 「模式 B 状态」行（§8①）：七种静默场景各有可读原因，日志 + Summary 同源；每轮必有（OR11②）
    if b_crash:
        b_reason = f"整体失败（不影响模式 A）：{b_crash}"  # 失败不得被「无事可做」掩盖
    elif b_force and not cfg.repo_readmes.enabled:
        b_reason = "未运行（勾选与 config 开关冲突，见上方警告）"
    elif not cfg.repo_readmes.enabled:
        b_reason = "未启用（config.yaml repo_readmes.enabled=false）"
    elif b_client_eff is None and readme_fetchers is None:
        b_reason = "未运行：缺 WORKFLOW_PAT，无法 compare/推送（配好后自动启用）"
    elif any(r.status == "pending" for r in b_results):
        b_reason = MISSING_KEY_REASON
    elif any(r.status == "suppressed" for r in b_results):
        b_reason = "本批输入与上次失败相同已跳过（不会自动重试；新提交或 --force 才重试）"
    if b_stats:
        # 没进队列的仓库就是「没发生 AI 调用」的仓库：首见只记基线、无变化、仅 README 变动
        usage.note_cache(
            "repo_readme",
            sum(int(b_stats.get(k) or 0) for k in ("no_change", "readme_only", "first_seen")),
        )
    status_line = _b_status_line(b_stats, b_reason)
    print(status_line)
    _append_summary(f"- {status_line}")
    pat_hint = _pat_404_hint(b_stats, token_desc)
    if pat_hint:
        print(pat_hint)
        _append_summary(f"- {pat_hint}")

    # 3.6 清单/通知 + 等待（§4-2/§4-3）：有 open PR 才动 issue；等待仅勾选且非空时执行
    # （issue_eff 已在开局解析；issue_cached 为钩子①的预取——省一次搜索，条件同款）
    if cfg.repo_readmes.enabled and b_client_eff is not None and not dry_run:
        # 钩子③（2026-10-23 勾选丢失窗口修复）：清单重建前再收割一次——钩子①' 与重建
        # 之间用户刚勾上的条目若不收割，重建（body 由本任务重写）会把它抹掉、永不生效
        if issue_eff is not None:
            try:
                _advance_after_merge(_merge_from_issue(_reread_issue(), "重建前"))
            except Exception as exc:  # noqa: BLE001 —— 收割失败不拖死清单重建
                print(f"重建前清单收割失败（忽略，清单重建照旧）：{exc}", file=sys.stderr)
        wait_hint = ""
        if merge_wait_minutes > 0 and open_prs:
            _until = (
                datetime.now(_BEIJING) + timedelta(minutes=merge_wait_minutes)
            ).strftime("%H:%M")
            wait_hint = wait_first_line(len(open_prs), _until)
        if issue_eff is not None:
            try:
                issue = update_checklist(
                    issue_eff,
                    owner_repo,
                    open_prs,
                    username=cfg.github.username,
                    updated_at=datetime.now(_BEIJING).strftime("%Y-%m-%d %H:%M"),
                    branch=cfg.repo_readmes.pr_branch,
                    tail=wait_hint,
                    existing=issue_cached,
                )
            except Exception as exc:  # noqa: BLE001 —— 清单失败任务不失败（IW26）
                print(f"清单 issue 更新失败（不影响本轮任务）：{exc}", file=sys.stderr)
        if open_prs:
            # Summary 清单快照（IW25）：无论 issue 通道是否可用都留一份可点的清单
            _append_summary(
                "### README 待合并清单\n"
                + "\n".join(f"- {p['full_name']}：{p['html_url']}" for p in open_prs)
                + (f"\n清单 issue：{issue['html_url']}" if issue else "\n（清单 issue 未创建）")
            )

    waited = False
    current: set[tuple[str, int]] = set()
    if merge_wait_minutes > 0 and open_prs and b_client_eff is not None and not dry_run:
        pending_refs = [
            (str(p["full_name"]), int(p["number"]))
            for p in open_prs
            if p.get("number")
        ]
        if pending_refs:
            waited = True
            current = set(pending_refs)
            until_bj = (
                datetime.now(_BEIJING) + timedelta(minutes=merge_wait_minutes)
            ).strftime("%H:%M")
            first = wait_first_line(len(pending_refs), until_bj)
            print(f"等待合并：{first}")
            _append_summary(f"### 等待合并\n{first}")

            def _on_change(pending: list[tuple[str, int]]) -> None:
                nonlocal current, checklist_closed, issue
                current = set(pending)
                if not pending:
                    checklist_closed = True
                remaining = [
                    p for p in open_prs if (p["full_name"], p["number"]) in current
                ]
                if issue is not None and issue_eff is not None:
                    try:
                        # 回带最新 body：收尾 append_final_state 在其上追加，
                        # 抹掉已结清条目（终审 I1）
                        issue = update_checklist(
                            issue_eff,
                            owner_repo,
                            remaining,
                            username=cfg.github.username,
                            updated_at=datetime.now(_BEIJING).strftime("%Y-%m-%d %H:%M"),
                            branch=cfg.repo_readmes.pr_branch,
                            existing=issue,
                        )
                    except Exception as exc:  # noqa: BLE001 —— 刷新失败忽略，不拖死等待
                        print(f"清单刷新失败（忽略）：{exc}", file=sys.stderr)
                print(f"::notice::等待合并：还剩 {len(pending)} 条待处理")

            def _tick_merge() -> None:
                # 钩子②（2026-10-23）：等待期间新勾选 → 立即代为合并，本轮轮询即见结果；
                # 每 tick 按号直读清单（勾选是用户在等待期间改的；按号 GET 不消耗
                # 搜索路由的测试配额/限流预算），异常自隔离不拖死等待
                if issue is None or issue_eff is None:
                    return
                try:
                    cur = get_issue(issue_eff, owner_repo, int(issue["number"]))
                    _merge_from_issue(cur, "等待中")
                except Exception as exc:  # noqa: BLE001
                    print(f"等待中勾选合并失败（忽略，下个 tick 重试）：{exc}", file=sys.stderr)

            try:
                wait_result = wait_for_merges(
                    b_client_eff,
                    pending_refs,
                    minutes=merge_wait_minutes,
                    now_fn=now_fn or time.monotonic,
                    sleep_fn=sleep_fn or time.sleep,
                    on_change=_on_change,
                    on_tick=_tick_merge,
                )
            except KeyboardInterrupt:
                raise  # 取消语义：B 产物已落盘，不回滚（IW24 前半）
            except Exception as exc:  # noqa: BLE001 —— 轮询失败不拖死整轮，按超时继续
                print(f"等待合并轮询失败（继续执行模式 A）：{exc}", file=sys.stderr)
                wait_result = "timeout"
            if wait_result == "timeout":
                timeout_tail = "⏰ 等待窗口已耗尽，未合并的 PR 会继续留在清单里，任务继续。"
                print(timeout_tail)
                remaining = [
                    p for p in open_prs if (p["full_name"], p["number"]) in current
                ]
                if issue is not None and issue_eff is not None and remaining:
                    try:
                        # 回带 ⏰ 终态 body：收尾追加不得抹掉它（终审 I1）
                        issue = update_checklist(
                            issue_eff,
                            owner_repo,
                            remaining,
                            username=cfg.github.username,
                            updated_at=datetime.now(_BEIJING).strftime("%Y-%m-%d %H:%M"),
                            branch=cfg.repo_readmes.pr_branch,
                            tail=timeout_tail,
                            existing=issue,
                        )
                    except Exception as exc:  # noqa: BLE001 —— 终态行失败忽略
                        print(f"清单更新失败（忽略）：{exc}", file=sys.stderr)

    # 3.7 §5.6 同轮新鲜度（等待后评估，只喂主页描述补位会读的短介绍仓）：
    # 合并（含等待中合并）→ API 真源 > 等待中被关（拒绝）→ 旧文 > 本轮生成 → 内存 >
    # 静默且 PR 仍 open → 分支 > 其余 → 旧文（不预填，A 拉默认分支）
    if b_client_eff is not None and cfg.repo_readmes.enabled and not dry_run:
        generated = {
            r.full_name: r.content for r in b_results if r.status == "generated"
        }
        short_names = {r.full_name for r in short_targets}
        settled_ok: set[str] = set(b_merged)  # 合并检测已确认合并的仓
        settled_closed: set[str] = set()
        if waited:
            for p in open_prs:
                _fn = p["full_name"]
                if _fn not in short_names or (_fn, p["number"]) in current:
                    continue
                # 等待中结清 → 复查 GET /pulls/{n}：合并=新版真源，关闭=旧文（OR04 防线，
                # 绝不因「本轮生成过」把被拒内容留在主页——规则 3 覆盖规则 2）
                try:
                    data, _ = b_client_eff._get(f"{API}/repos/{_fn}/pulls/{p['number']}")
                    _merged = bool(isinstance(data, dict) and data.get("merged"))
                except Exception:  # noqa: BLE001 —— 状态复查失败按未合并（旧文）处理
                    _merged = False
                (settled_ok if _merged else settled_closed).add(_fn)
        for _fn in sorted(short_names):
            if _fn in settled_ok:
                b_readme_sources[_fn] = "API 真源"
                readme_of.cache[_fn] = b_client_eff.get_repo_readme(_fn)
            elif _fn in settled_closed:
                b_readme_sources[_fn] = "旧文"
                readme_of.cache[_fn] = b_client_eff.get_repo_readme(_fn)
            elif _fn in generated:
                b_readme_sources[_fn] = "内存"
                readme_of.cache[_fn] = generated[_fn]
            elif any(p["full_name"] == _fn for p in open_prs):
                b_readme_sources[_fn] = "分支"
                readme_of.cache[_fn] = b_client_eff.get_repo_readme(
                    _fn, ref=cfg.repo_readmes.pr_branch
                )
        # 对账行（§8④，OR10 每轮必含）：本轮 A（描述补位）读到的 README 各来自哪里
        counts = {"内存": 0, "API 真源": 0, "分支": 0, "旧文": 0}
        for r in short_targets:
            counts[b_readme_sources.get(r.full_name, "旧文")] += 1
        sources_line = (
            "本轮 A 的 README 输入来源："
            + "、".join(f"{label} {counts[label]}" for label in counts)
        )
        print(sources_line)
        _append_summary(f"- {sources_line}")

    # 3.8 仓库描述写回（2026-10-25）：模式 B 之后、补位/翻译/简介之前——
    # 同轮渲染、描述翻译与简介都直接使用新描述；不重新精选、不重算 totals。
    desc_results: list[dict] = []
    desc_notices: list[str] = []
    desc_remaining = 0
    if descriptions_active:
        _desc_now = datetime.now(timezone.utc).isoformat()
        _desc_extra: list[str] = []  # 生成警告与结果合并进同一个 Step Summary 小节
        candidates, desc_notices, desc_remaining = plan_updates(
            repos,
            cfg.repo_descriptions,
            desc_entries,
            cfg.github.username,
            now_iso=_desc_now,
        )
        if candidates:
            items = []
            for r in candidates:
                readme_text = readme_of(r.full_name)
                items.append(
                    {
                        "full_name": r.full_name,
                        "description": r.description,
                        "readme": readme_text,
                        "language": r.language,
                        "topics": list(r.topics),
                        "target": target_language(r.description, readme_text, cfg.ai),
                    }
                )
            generated, warn = generate_descriptions(items, cfg.ai)
            if warn:
                print(warn, file=sys.stderr)
                _desc_extra.append(f"- ⚠️ {warn}")
            desc_results = apply_updates(
                b_client_eff,
                candidates,
                generated,
                desc_entries,
                now_iso=_desc_now,
            )
            if any(r.get("status") == "updated" for r in desc_results):
                # 描述已变：指纹、简介输入、补位目标同轮跟进（不重新精选/重算 totals）
                fingerprint = stats_fingerprint(totals, repos, featured)
                payload = build_bio_payload(
                    user, featured, cfg.ai, bilingual=cfg.readme.bilingual
                )
                cur_hash = bio_input_hash(payload, cfg.ai)
                short_targets = [r for r in short_targets if not r.description]
        _desc_lines = (
            desc_notices + _desc_extra + description_summary_lines(
                desc_results, desc_remaining
            )
        )
        if _desc_lines:
            print("\n".join(_desc_lines))
            _append_summary("### 仓库描述写回\n" + "\n".join(_desc_lines))

    # 4. 描述补位：缺 description 的精选仓库，用其 README 生成一句主页文案
    short_intros: dict[str, str] = {}
    short_intros_en: dict[str, str] = {}
    short_states: dict[str, dict] = {}
    if short_targets:
        prev_states_all = _prev_repo_states(prev)
        for r in short_targets:
            readme_text = readme_of(r.full_name)
            h = short_intro_input_hash(
                r, cfg.ai, readme_text, bilingual=cfg.readme.bilingual
            )
            st = prev_states_all.get(r.full_name) or {}
            if (
                st.get("short_intro")
                and st.get("short_intro_hash") == h
                # 双语下 en 缺失不得复用（防 hash 命中却永远单语卡死门，WP4）
                and (not cfg.readme.bilingual or st.get("short_intro_en"))
            ):
                short_intros[r.full_name] = st["short_intro"]
                short_intros_en[r.full_name] = st.get("short_intro_en") or ""
                usage.note_cache("short_intro")
                continue
            try:
                text, text_en = generate_short_intro(
                    r, cfg.ai, readme=readme_text, bilingual=cfg.readme.bilingual
                )
            except Exception as exc:
                text = st.get("short_intro") or ""
                text_en = st.get("short_intro_en") or ""
                print(f"「{r.name}」描述补位失败，沿用旧文案：{exc}", file=sys.stderr)
            short_states[r.full_name] = {
                "short_intro": text,
                "short_intro_en": text_en,
                "short_intro_hash": h,
            }
            if text:
                short_intros[r.full_name] = text
            if text_en:
                short_intros_en[r.full_name] = text_en

    # 4.5 仓库描述本地化（A-WP-B，2026-10-24 扩展双向）：en 文件里含中文的描述换英译，
    # zh 文件里不含汉字的描述换中译——切到哪版就读到哪一版的语言。各自按描述原文缓存
    # （desc_en/desc_en_src、desc_zh/desc_zh_src）；失败/漏译回落原文且不写缓存
    # （下轮开门自动重试）；不改增量门、不进任何指纹。
    desc_states: dict[str, dict] = {}
    descs_en: dict[str, str] = {}
    descs_zh: dict[str, str] = {}
    # 置顶与列表都关 → 描述没有展示位，不调翻译（省额度）
    if ai_active and readme_active and (cfg.readme.sections.pinned or cfg.readme.sections.table):
        prev_states_desc = _prev_repo_states(prev)
        stale_en: list = []
        stale_zh: list = []
        for r in featured:
            if not r.description:
                continue  # 无描述：交给补位文案，不进翻译
            st = prev_states_desc.get(r.full_name) or {}
            if contains_cjk(r.description):
                # zh 侧原本就是中文；en 侧需要英译（仅双语模式生成 en 文件）
                if cfg.readme.bilingual:
                    if st.get("desc_en") and st.get("desc_en_src") == r.description:
                        descs_en[r.full_name] = st["desc_en"]  # 缓存新鲜：直接用
                        usage.note_cache("desc")
                    else:
                        stale_en.append(r)
            else:
                # en 侧原本就是非中文；zh 侧需要中译（中英混排之外的假名等也按中译处理）
                if st.get("desc_zh") and st.get("desc_zh_src") == r.description:
                    descs_zh[r.full_name] = st["desc_zh"]  # 缓存新鲜：直接用
                    usage.note_cache("desc")
                else:
                    stale_zh.append(r)
        if stale_en:
            translated, warn = translate_descriptions(
                [(r.full_name, r.description) for r in stale_en], cfg.ai, target="en"
            )
            if warn:
                print(warn, file=sys.stderr)
            for r in stale_en:
                if r.full_name in translated:
                    descs_en[r.full_name] = translated[r.full_name]
                    desc_states.setdefault(r.full_name, {}).update(
                        {"desc_en": translated[r.full_name], "desc_en_src": r.description}
                    )
        if stale_zh:
            translated, warn = translate_descriptions(
                [(r.full_name, r.description) for r in stale_zh], cfg.ai, target="zh"
            )
            if warn:
                print(warn, file=sys.stderr)
            for r in stale_zh:
                if r.full_name in translated:
                    descs_zh[r.full_name] = translated[r.full_name]
                    desc_states.setdefault(r.full_name, {}).update(
                        {"desc_zh": translated[r.full_name], "desc_zh_src": r.description}
                    )

    # 4. AI 简介（只在更新 overview 时生成；失败保留旧简介，任务不失败）
    bio_text = prev.bio.text if prev else ""
    bio_text_en = prev.bio.text_en if prev else ""
    bio_generated = False
    if ai_active and readme_active and cfg.readme.sections.bio:
        try:
            bio_text, bio_text_en, bio_generated = generate_bio(
                payload,
                cfg.ai,
                input_hash=cur_hash,
                prev_hash=prev.bio.input_hash if prev else "",
                prev_text=prev.bio.text if prev else "",
                prev_text_en=prev.bio.text_en if prev else "",
                prev_generated_at=prev.bio.generated_at if prev else "",
                bilingual=cfg.readme.bilingual,
            )
        except BioError as exc:
            print(f"简介生成失败，沿用旧简介：{exc}", file=sys.stderr)

    if bio_text and not bio_generated:
        usage.note_cache("bio")
    # 用量行放在最后一个 AI 阶段（简介）之后、渲染之前：此时本轮所有调用都已发生
    _emit_usage(started_at, b_stats)

    now_bj = datetime.now(_BEIJING)
    changed = False
    # WP5：三路合并 base = 本轮渲染输出（存含手改的写入内容会让下轮把手改误当已拥有而抹掉）
    readme_last_new = prev.readme_last if prev else ""
    readme_last_en_new = prev.readme_last_en if prev else ""
    if readme_active:
        # custom 区已在增量门前提取（首次接管检测复用同一份，避免二读）
        _report_custom_mode(mode_zh, "README.md", embed=use_embed)
        # 语言提醒（2026-10-25）：手写区语言不匹配只警告，绝不翻译或改写
        _area_zh = "手写内容" if use_embed else "custom 区"
        if custom_zh and language_mismatch(custom_zh, "zh"):
            _line = (
                f"⚠️ README.md 的{_area_zh}看起来是英文（目标语言：中文）；"
                "手写内容不会被翻译或改写"
            )
            print(_line, file=sys.stderr)
            _append_summary(f"- {_line}")
        if cfg.readme.bilingual:
            _report_custom_mode(mode_en, "README.en.md", embed=use_embed)
            _area_en = "手写内容" if use_embed else "custom 区"
            if custom_en and language_mismatch(custom_en, "en"):
                _line = (
                    f"⚠️ README.en.md 的{_area_en}看起来是中文（目标语言：英文）；"
                    "手写内容不会被翻译或改写"
                )
                print(_line, file=sys.stderr)
                _append_summary(f"- {_line}")
        if use_embed:
            # auto（默认）：手写 README 只插/更新一个 AI 简介块，其余字节不动。
            # 没有 pin、没有统计卡、没有仓库列表——Pinned 也不会被重新展示或修改。
            if mode_zh != "take_over":
                print(
                    "README.md 不是手写文件，auto 模式本轮不改动它", file=sys.stderr
                )
            if cfg.readme.bilingual and en_readme_path.exists() and mode_en != "take_over":
                print(
                    "README.en.md 不是手写文件，auto 模式本轮不改动它",
                    file=sys.stderr,
                )
            if dry_run:
                preview_zh = disk_zh
                if embed_zh_due and bio_text.strip():
                    preview_zh = upsert_block(disk_zh, embed_body("zh", bio_text)) or disk_zh
                print(preview_zh)
                if cfg.readme.bilingual:
                    print(f"\n{'=' * 20} README.en.md (English) {'=' * 20}")
                    preview_en = disk_en
                    if embed_en_due and bio_text_en.strip():
                        preview_en = (
                            upsert_block(disk_en, embed_body("en", bio_text_en)) or disk_en
                        )
                    print(preview_en)
                return 0
            if embed_zh_due:
                if not bio_text.strip():
                    _line = (
                        "README.md 还没有可用的 AI 简介（缺 AI key 或 bio 开关关闭），"
                        "本次不写；配好后自动补上"
                    )
                    print(_line, file=sys.stderr)
                    _append_summary(f"- ⚠️ {_line}")
                else:
                    out = upsert_block(disk_zh, embed_body("zh", bio_text))
                    if out is not None:
                        changed = write_if_changed(readme_path, out)
            if embed_en_due:
                if not bio_text_en.strip():
                    _line = "README.en.md 还没有可用的英文简介，本次不写"
                    print(_line, file=sys.stderr)
                    _append_summary(f"- ⚠️ {_line}")
                else:
                    out = upsert_block(disk_en, embed_body("en", bio_text_en))
                    if out is not None and write_if_changed(en_readme_path, out):
                        changed = True
            if changed:
                _line = "主页模式：auto（仅维护 AI 简介块，其余内容未改动）"
                _append_summary(f"- 🧩 {_line}")
        else:
            # 整页渲染：machine / preserved，或显式 mode: full
            # auto → full 迁移：先把旧托管块从 custom 区剥掉，避免简介重复
            custom_zh_for_render = (
                strip_block(custom_zh) if mode_zh == "take_over" else custom_zh
            )
            custom_en_for_render = (
                strip_block(custom_en) if mode_en == "take_over" else custom_en
            )
            ctx = TemplateContext(
                username=cfg.github.username,
                display_name=user.get("name") or cfg.github.username,
                blog=user.get("blog") or "",
                totals=totals,
                featured=featured,
                pinned=pinned,
                bio_text=bio_text,
                bio_text_en=bio_text_en,  # WP4：en 渲染的简介来源（空 → 英文占位注释）
                cards_theme=cfg.readme.cards.theme,
                cards_show=cfg.readme.cards.show,
                include_update_time=cfg.readme.include_update_time,
                # 时间戳本体不含时区字样——「（北京时间）」/「 (UTC+8)」由模板 labels 提供（WP3）
                updated_at=now_bj.strftime("%Y-%m-%d %H:%M"),
                short_intros=short_intros,
                short_intros_en=short_intros_en,  # WP4：en 表格补位文案（空 → 回落 description）
                visitors_badge=cfg.readme.visitors_badge,
                key_env=cfg.ai.key_env_name,
                custom_text=custom_zh_for_render,  # WP5：custom 区内容回注渲染（永久保留）
                # A-WP-B（zh 方向）：非中文描述的中译（缺省回落原文）
                display_descs=descs_zh,
                sections=cfg.readme.sections,  # 板块开关（默认全开，输出与旧版一致）
            )
            content = render_readme(ctx, template_path)
            # 英文版（WP3）：整页换 labels，简介/补位换 en 来源、custom 换 en 提取
            content_en = ""
            if cfg.readme.bilingual:
                content_en = render_readme(
                    dataclasses.replace(
                        ctx,
                        bio_text=ctx.bio_text_en,
                        short_intros=ctx.short_intros_en,
                        display_descs=descs_en,  # A-WP-B：en 侧 AI 英译描述（缺省回落原文）
                        custom_text=custom_en_for_render,
                    ),
                    template_path,
                    lang="en",
                )
            readme_last_new = content
            readme_last_en_new = content_en if cfg.readme.bilingual else ""

            if dry_run:
                print(content)
                if cfg.readme.bilingual:
                    print(f"\n{'=' * 20} README.en.md (English) {'=' * 20}")
                    print(content_en)
                return 0

            if broken_pending:
                # marker 损坏时 custom 区会被当成空——整页写回就是拿空区覆盖原文。
                # 一律不写（含同轮正常的另一个文件），修复注释对后自动恢复。
                _line = (
                    "主页 README 的 marker 损坏（start/end 不成对）：本轮不改动任何主页文件，"
                    "修复后自动恢复"
                )
                print(_line, file=sys.stderr)
                _append_summary(f"- ⚠️ {_line}")
                readme_last_new = prev.readme_last if prev else ""
                readme_last_en_new = prev.readme_last_en if prev else ""
            else:
                # WP5 三路合并：手改保留、冲突以数据为准、删除恢复；升级首跑 base 兜底取盘上
                # （该轮以新渲染为准，一次性——文档明示）
                base_zh = prev.readme_last if (prev and prev.readme_last) else disk_zh
                merged_zh = merge_readme_3way(base_zh, disk_zh, content)
                merged_en = ""
                if cfg.readme.bilingual:
                    base_en = (
                        prev.readme_last_en if (prev and prev.readme_last_en) else disk_en
                    )
                    merged_en = merge_readme_3way(base_en, disk_en, content_en)
                # 首次接管不写本地：内容全部进 PR，等合并后才落 main（快照同船）
                if not takeover_active:
                    changed = write_if_changed(readme_path, merged_zh)
                    if cfg.readme.bilingual and write_if_changed(en_readme_path, merged_en):
                        changed = True
    elif dry_run:
        print("readme 已禁用（readme.enabled=false 或 --skip-readme），跳过 overview 生成")
        return 0

    # 6. 快照：bio 的 hash/时间只在本次真正生成时前进，复用/回退时保留旧值
    now_iso = datetime.now(timezone.utc).isoformat()
    if bio_generated:
        bio_info = dataclasses.replace(
            prev.bio if prev else Snapshot().bio,
            text=bio_text,
            text_en=bio_text_en,
            input_hash=cur_hash,
            generated_at=now_iso,
            model=cfg.ai.model,
            prompt_version=cfg.ai.prompt_version,
            # 双语下英文被丢弃：记下当前输入，同一输入不再每轮重试
            en_dropped_hash=(
                cur_hash if cfg.readme.bilingual and not bio_text_en else ""
            ),
        )
    else:
        bio_info = dataclasses.replace(
            prev.bio if prev else Snapshot().bio,
            text=bio_text,
            text_en=bio_text_en,
        )

    merged_states = _prev_repo_states(prev)
    merged_states.update(short_states)
    merged_states.update(desc_states)  # A-WP-B：英译缓存（desc_en/desc_en_src）落盘
    new_snap = Snapshot(
        schema=5,  # 2026-10-25：新增 repo_descriptions（描述写回状态）
        username=cfg.github.username,
        stats_fingerprint=fingerprint,
        stats_config_hash=cfg_hash,
        # WP5：三路合并 base 存「本轮渲染」而非合并写入（防下轮把手改误当已拥有而抹掉）
        readme_last=readme_last_new,
        readme_last_en=readme_last_en_new,
        # 最近一次生效置顶名单（原生 Pinned 解析或回退结果；读取失败时作下轮回退来源）
        pins=list(effective_pin),
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
                    # 有意变更（A-P0）：intro_*/ai_generated 白名单删除——项目介绍已移除，
                    # 旧快照里的存量 intro 键经此自然清除（下一次 save 起不再落盘）
                    if k.startswith("short_intro")
                    or k.startswith("desc_en")
                    or k.startswith("desc_zh")
                },
            }
            for r in repos
        ],
        bio=bio_info,
        repo_readmes=repo_readmes_state,
        repo_descriptions=desc_entries,
        generated_at=now_iso,
    )
    if takeover_active:
        pr_opened = False
        if b_client_eff is None:
            _line = (
                "检测到手写 README，但未配置 WORKFLOW_PAT，无法创建首次接管 PR；"
                "main 未改动。把 readme.mode 设为 auto 可只插入 AI 简介块，"
                "或配置 WORKFLOW_PAT 后再整页接管"
            )
            print(_line, file=sys.stderr)
            _append_summary(f"- ⚠️ {_line}")
            return 0
        # 阶段一：读默认分支 HEAD。读不到就无法建分支 → 跳过本轮，绝不直写 main。
        head_sha = ""
        try:
            head_data, _ = b_client_eff._get(f"{API}/repos/{owner_repo}/commits/HEAD")
            if isinstance(head_data, dict):
                head_sha = str(head_data.get("sha") or "")
        except Exception as exc:  # noqa: BLE001 —— 读不到就按无 PR 处理
            print(f"读取主页仓库 HEAD 失败（按无 PR 处理）：{exc}", file=sys.stderr)
        if not head_sha:
            _line = (
                "首次接管：无法读取主页仓库 HEAD，本轮跳过（main 未改动，下一轮自动重试）；"
                "想避免整页接管可把 readme.mode 设为 auto"
            )
            print(_line, file=sys.stderr)
            _append_summary(f"- ⚠️ {_line}")
            return 0
        # 阶段二：推送 + 开/刷 PR。从这里开始失败也不写 main。
        try:
            rel_zh, zh_ok = _repo_rel_path(readme_path, root)
            rel_snap, snap_ok = _repo_rel_path(snap_path, root)
            files: dict[str, str] = {rel_zh: merged_zh}
            if cfg.readme.bilingual:
                rel_en, en_ok = _repo_rel_path(en_readme_path, root)
                files[rel_en] = merged_en
                if not en_ok:
                    _warn = "README.en.md 不在仓库目录内，PR 中已回落文件名，请检查配置"
                    print(_warn, file=sys.stderr)
                    _append_summary(f"- ⚠️ {_warn}")
            files[rel_snap] = snapshot_text(new_snap)
            if not zh_ok or not snap_ok:
                _warn = "README/快照不在仓库目录内，PR 中已回落文件名，请检查配置"
                print(_warn, file=sys.stderr)
                _append_summary(f"- ⚠️ {_warn}")
            result = open_or_refresh_pr(
                b_client_eff,
                full_name=owner_repo,
                content=merged_zh,
                head_sha=head_sha,
                title=takeover_pr_title(cfg.github.username),
                body=takeover_pr_body(
                    username=cfg.github.username,
                    snapshot_path=rel_snap,
                    bilingual=cfg.readme.bilingual,
                ),
                branch=TAKEOVER_BRANCH,
                base=profile_base,
                files=files,
            )
            _action = "已刷新" if result.refreshed else "已打开"
            _line = (
                f"首次接管：{_action} PR #{result.number}（{result.html_url}）；"
                "main 未改动，合并后自动进入正常更新"
            )
            print(_line)
            _append_summary(f"- ✅ {_line}")
            pr_opened = True
        except Exception as exc:  # noqa: BLE001 —— 失败不回退，下轮重试
            _line = f"接管 PR 更新失败（main 未改动，下一轮自动重试）：{exc}"
            print(_line, file=sys.stderr)
            _append_summary(f"- ⚠️ {_line}")
        if pr_opened:
            return 0
    else:
        save_snapshot(snap_path, new_snap)

    # 6.5 收尾（§4-5）：本轮确实更新了 Overview 且清单仍开着时，原地追加终态行。
    # （终审 I4：未更新 Overview 不写「已进入 main」——提交由随后的工作流步骤完成，
    # 不替它打包票）
    if issue is not None and issue_eff is not None and not checklist_closed and changed:
        try:
            append_final_state(issue_eff, owner_repo, issue)
        except Exception as exc:  # noqa: BLE001 —— 收尾行失败忽略，不改任务结果
            print(f"清单收尾更新失败（忽略）：{exc}", file=sys.stderr)

    # 7. 运行明细：逐仓库列出本次做了什么（写入 Actions 摘要页 + 日志）
    if short_targets:
        actions: dict[str, str] = {}
        for r in short_targets:
            if r.full_name not in short_states:
                actions[r.name] = "已是最新（复用）"
            elif short_states[r.full_name].get("short_intro"):
                actions[r.name] = "已生成补位文案"
            else:
                actions[r.name] = "生成失败（保留暂无描述）"
        lines = [
            "## 本次运行明细（逐仓库）",
            "",
            "| 仓库 | 主页描述补位 |",
            "| --- | --- |",
        ]
        for name, action in actions.items():
            lines.append(f"| `{name}` | {action} |")
        _append_summary("\n".join(lines))
        print("\n".join(lines))

    print("已更新" if changed else "内容无差异")
    return 0


def sync_checklist_from_env(cfg_path: Path) -> int:
    """issues.edited 事件入口：只做清单全选联动，不跑更新。

    勾选同步是排版糖：失败只打 warning、永远返回 0——运行时「总开关=全合并」
    的语义仍在，不能让一次权限抖动把工作流标红。
    """
    if os.environ.get("GITHUB_EVENT_NAME") != "issues":
        return 0
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        return 0
    try:
        with open(event_path, encoding="utf-8") as fh:
            event = json.load(fh)
        cfg: Config = load_config(
            cfg_path, fallback_username=os.environ.get("GITHUB_REPOSITORY_OWNER")
        )
        # 清单读写沿用内置 GITHUB_TOKEN（IW27 权限分离：合并才用 PAT）
        client = GitHubClient(os.environ.get(cfg.github.token_env) or None)
        changed = sync_from_issue_event(event, client)
        print("清单勾选已同步" if changed else "清单勾选无需调整")
    except Exception as exc:  # noqa: BLE001 —— 排版同步失败绝不让工作流变红
        print(f"::warning::清单勾选同步失败（不影响合并语义）：{exc}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    parser = argparse.ArgumentParser(description="自动维护 GitHub Profile README")
    parser.add_argument("--config", default="config.yaml", help="配置文件路径")
    parser.add_argument(
        "--sync-checklist",
        action="store_true",
        help="只同步清单「全部合并」勾选（issues.edited 事件用），不跑更新",
    )
    parser.add_argument("--dry-run", action="store_true", help="打印到 stdout，不写文件")
    parser.add_argument(
        "--force",
        action="store_true",
        help="忽略增量检测，强制重写主页（不连带强制各仓库 README；那用 --repo-readmes）",
    )
    parser.add_argument("--no-ai", action="store_true", help="跳过 AI 调用（仍生成数据版内容）")
    parser.add_argument("--skip-readme", action="store_true", help="本次不更新 overview README")
    parser.add_argument("--fixture", default=None, help="从本地 fixture 目录读取数据（离线测试）")
    parser.add_argument(
        "--repo-readmes", action="store_true", help="本次强制重跑模式 B（各仓库 README 托管区更新）"
    )
    parser.add_argument(
        "--wait-for-merges",
        action="store_true",
        help="等待待合并的 README PR 结清后再更新主页（默认窗口 30 分钟）",
    )
    parser.add_argument(
        "--merge-wait-minutes",
        type=float,
        default=None,
        help="等待合并窗口分钟数（0 = 不等待；给出时覆盖 --wait-for-merges 的默认 30）",
    )
    args = parser.parse_args(argv)
    if args.sync_checklist:
        return sync_checklist_from_env(Path(args.config))
    merge_wait = 30.0 if args.wait_for_merges else 0.0
    if args.merge_wait_minutes is not None:
        merge_wait = args.merge_wait_minutes
    rc = run(
        Path(args.config),
        dry_run=args.dry_run,
        force=args.force,
        no_ai=args.no_ai,
        skip_readme=args.skip_readme,
        fixture=Path(args.fixture) if args.fixture else None,
        b_force=args.repo_readmes,
        merge_wait_minutes=merge_wait,
    )
    # 所有退出路径统一播报：AI 额度/认证问题必须显式可见（不改变退出码）
    _report_ai_failures()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
