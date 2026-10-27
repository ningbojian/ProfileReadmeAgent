"""清单 issue/通知通道（设计 §4-2、§5.5 文案草案、§5.7、§12-R8）。

- 本仓（username/username）唯一单份「🔔 待合并清单」issue：@你 + assignee 制造
  主动通知（自 PAT 开的 PR 不通知作者本人，mention/assign 不受自操作抑制）；
- 状态变化只原地改 body、永不发评论（避免通知轰炸，IW04）；
- 全部结清自动关闭（关闭通知 = 可以收工，IW03）；无 open PR 且无既有清单不开新 issue（IW05）；
- 异常向上抛，由 cli 隔离（创建失败任务不失败，IW26）。
"""

from __future__ import annotations

import re

from .github_client import API, GitHubClient
from .repo_readmes_pr import PR_BRANCH

CHECKLIST_TITLE = "🔔 README 更新待合并清单"

# 条目行（任意勾选态）：- [ ]/- [x] 开头 + 规范 [PR](https://github.com/{owner}/{repo}/pull/{n})
# 链接；仓库全名只能从 URL 取（可见文本只有短名）。半截链接/坏行不匹配 → 宁漏勿错。
# 「全部合并」总开关行不含 PR 链接，绝不落进本正则（不会被当成条目）。
_ENTRY_RE = re.compile(
    r"^- \[([ xX])\] .*?\[PR\]\(https://github\.com/([^/\s)]+)/([^/\s)]+)/pull/(\d+)\)"
)

# 「全部合并」总开关锚点：隐藏 token（用户改措辞不丢语义），见 build_checklist_body
_MERGE_ALL_RE = re.compile(r"^- \[[xX]\] .*?<!--\s*merge-all\s*-->")
# 任意勾选态的总开关行：全选联动改写用（_MERGE_ALL_RE 只认已勾选）
_MERGE_ALL_ANY_RE = re.compile(r"^- \[([ xX])\] .*?<!--\s*merge-all\s*-->")


def _parse_entries(body: str) -> list[tuple[str, int, bool]]:
    """条目行解析 → [(仓库全名, PR 号, 是否勾选)]；坏行不匹配——宁漏勿错。"""
    out: list[tuple[str, int, bool]] = []
    for line in (body or "").splitlines():
        m = _ENTRY_RE.match(line.strip())
        if m:
            out.append((f"{m.group(2)}/{m.group(3)}", int(m.group(4)), m.group(1) in "xX"))
    return out


def parse_checked_prs(body: str) -> list[tuple[str, int]]:
    """从清单 body 提取用户勾选的条目 → [(仓库全名, PR 号)]（2026-10-23 勾选=合并授权）。

    勾选动作即用户的人工合并授权（设计 §5.5 修订）；未勾选、格式损坏的行
    一律忽略——宁漏勿错，漏了让用户重勾，错了会合错东西。
    """
    return [(cf, cn) for cf, cn, ticked in _parse_entries(body) if ticked]


def parse_merge_targets(body: str) -> list[tuple[str, int]]:
    """本轮应合并的目标条目（2026-10-23 Q1「一键全选」：勾选=合并授权的并集口径）。

    「全部合并」总开关被勾 → 清单里全部条目（无论逐条勾选态）；否则仅逐条勾选的条目。
    总开关无 PR 链接、不是条目本身；个别不想合并就别勾总开关、逐条勾。
    """
    entries = _parse_entries(body)
    merge_all = any(_MERGE_ALL_RE.match(line.strip()) for line in (body or "").splitlines())
    if merge_all:
        return [(cf, cn) for cf, cn, _ in entries]
    return [(cf, cn) for cf, cn, ticked in entries if ticked]


def _master_state(body: str) -> bool | None:
    """总开关是否勾选；body 里没有总开关行时返回 None。"""
    for line in (body or "").splitlines():
        if _MERGE_ALL_ANY_RE.match(line.strip()):
            return line.strip()[3] in "xX"
    return None


def _set_checkbox(line: str, checked: bool) -> str:
    """只改行的勾选态，其余字节（缩进、链接、结尾换行）原样保留。"""
    m = re.match(r"^(\s*-\s+\[)([ xX])(\])", line)
    if not m:
        return line
    return f"{m.group(1)}{'x' if checked else ' '}{line[m.end(2):]}"


def sync_merge_all(old_body: str, new_body: str) -> str:
    """把「全部合并」与条目复选框同步成一致状态（2026-10-27 全选联动）。

    用户在 issue 里勾选只产生一次 body 编辑，这里靠 old/new 两份 body 判断
    「谁被点了」：

    - 总开关态变了（用户刚点全选/取消全选）→ 全部条目跟随；
    - 总开关没变（用户点了某一条）→ 只回写总开关 = 是否每条都勾上，
      其余条目保持用户刚点的样子（取消一条不会连带取消别的）。

    与勾选无关的编辑返回原 body；结果一致不产生新编辑（幂等，防 bot 自触发）。
    """
    old_master = _master_state(old_body)
    new_master = _master_state(new_body)
    if old_master is None or new_master is None:
        return new_body
    entries = [ln for ln in new_body.splitlines() if _ENTRY_RE.match(ln.strip())]
    if not entries:
        return new_body

    if new_master != old_master:
        entries_target: bool | None = new_master  # 全选/取消：条目跟随
        master_target = new_master
    else:
        entries_target = None  # 只动了某一条：条目保持原样
        master_target = all(ln.strip()[3] in "xX" for ln in entries)

    out: list[str] = []
    for line in new_body.splitlines(keepends=True):
        stripped = line.strip()
        if _MERGE_ALL_ANY_RE.match(stripped):
            if master_target != (stripped[3] in "xX"):
                line = _set_checkbox(line, master_target)
        elif entries_target is not None and _ENTRY_RE.match(stripped):
            if entries_target != (stripped[3] in "xX"):
                line = _set_checkbox(line, entries_target)
        out.append(line)
    return "".join(out)


def _same_body(a: str, b: str) -> bool:
    """逐字节比较正文（只抹平换行符差异与末尾空白，GitHub 可能归一化）。"""
    return a.replace("\r\n", "\n").rstrip() == b.replace("\r\n", "\n").rstrip()


def sync_from_issue_event(event: dict, client: GitHubClient) -> bool:
    """处理 issues.edited 事件：把清单勾选同步回 GitHub；返回是否真的改写了。

    只做排版联动，不合并、不读快照。异常向上抛，由 cli 降级成 warning
    （同步失败不能让工作流变红）。

    回写前先读服务端最新正文（2026-10-27 防抢写）：一次点击到本 job 跑完有
    十几秒，期间用户完全可能又点了一次——此时按旧正文回写会覆盖那次新编辑，
    用户侧还会看到 GitHub 的「Could not update issue comment」。不一致就
    不写，交给排在后面、拿着最新 body 的那一轮。
    """
    if not isinstance(event, dict) or event.get("action") != "edited":
        return False
    issue = event.get("issue") or {}
    new_body = issue.get("body")
    old_body = ((event.get("changes") or {}).get("body") or {}).get("from")
    number = issue.get("number")
    repo = (event.get("repository") or {}).get("full_name")
    if not old_body or new_body is None or not number or not repo:
        return False
    updated = sync_merge_all(str(old_body), str(new_body))
    if updated == new_body:
        return False
    fresh = get_issue(client, str(repo), int(number))
    current = str((fresh or {}).get("body") or "")
    if fresh is None or not _same_body(current, str(new_body)):
        print("清单勾选：检测到更新的一次编辑（或读不到最新正文），本次不写，交给最新一轮")
        return False
    client._patch(f"{API}/repos/{repo}/issues/{number}", {"body": updated})
    return True


def find_checklist_issue(client: GitHubClient, owner_repo: str) -> dict | None:
    """找本仓唯一的清单 issue（open 态、标题前缀匹配）；无则 None。

    公开给 cli：开局预取（2026-10-23 勾选合并钩子①）与清单步共用同一次读取。
    """
    data, _ = client._get(f"{API}/repos/{owner_repo}/issues?state=open&per_page=100")
    for it in data if isinstance(data, list) else []:
        if str(it.get("title") or "").startswith(CHECKLIST_TITLE):
            return it
    return None


# 未预取/预取失败哨兵：update_checklist 的 existing 缺省值
# （≠ None——None 表示「确认无既有清单」，直接走创建）
UNSET = object()


def get_issue(client: GitHubClient, owner_repo: str, number: int) -> dict | None:
    """按号直读清单 issue 最新 body；不存在/不可读 → None（钩子② 每 tick 用）。

    等待期间勾选可能刚被用户改过，按号重读拿的是实时 body；已关闭也照读
    （GitHub 对 closed issue 的 GET 同样 200，读到清空后的正文即自然无勾选）。
    """
    data, _ = client._get(f"{API}/repos/{owner_repo}/issues/{number}", allow_404=True)
    return data if isinstance(data, dict) else None


def build_checklist_body(
    open_prs: list[dict],
    *,
    username: str,
    updated_at: str,
    branch: str = PR_BRANCH,
    tail: str = "",
) -> str:
    """清单 body：时间戳标题 + @你 + 「全部合并」总开关 + 逐条 PR（预览链接 + 明细行/
    状态徽章）+ 勾选说明（含勿手动关警示）+ 三结局文案。

    open_prs 元素：{"full_name", "html_url", "branch"?}；W3 明细字段（可缺省，
    缺省即不渲染对应片段）："changed_files"/"additions"/"deletions"/"commits"/
    "commit_titles"/"mergeable_state"（dirty/blocked/draft → 明细行打头的 ⚠️ 徽章）。
    总开关行（2026-10-23 Q1）带隐藏 token <!-- merge-all --> 供 parse_merge_targets
    锚定；无条目（关闭体）不渲染总开关。
    """
    lines = [
        f"## {CHECKLIST_TITLE}（更新于 {updated_at}，北京时间）",
        f"@{username}",
        # 头部总则（2026-10-23 交互治理）：≤3 行让新人第一眼抓到重点——勾选 = 授权、
        # 下次运行生效（定时到点或保存 README 都会触发）、可批量；手动 Merge 立即生效
        "勾选方框 = 授权自动合并：任务下次运行（定时到点，或在 GitHub 网页保存 README 触发）"
        "会自动合并勾选的条目，可多选批量；手动点 Merge 则立即生效，无需等任务。",
    ]
    if open_prs:
        # 「全部合并」总开关（Q1 2026-10-23「能不能一键全选」）：GitHub 无原生全选按钮，
        # 勾这行 = 一键授权下面全部条目；隐藏 token 供 parse_merge_targets 锚定
        # （用户改措辞也不丢语义），且不含 PR 链接——绝不被当成普通条目；
        # 写明「下次运行时合并、只合并清单现存条目」（生效时机 + 范围，不夸大）
        lines.append(
            "- [ ] 全部合并：勾这里 = 下面全部条目一起勾上（取消勾选 = 一起取消），"
            "下次运行时一并合并；也可以逐条勾<!-- merge-all -->"
        )
    for pr in open_prs:
        full = str(pr.get("full_name") or "")
        short = full.split("/", 1)[-1]
        b = str(pr.get("branch") or branch)
        preview = f"https://github.com/{full}/blob/{b}/README.md"
        # 规范 markdown 链接而非裸 URL：GitHub 裸链接会吞掉紧随的全角括号与中文
        # （href 变成「…/pull/N（[新版」→ 点开 404），整条还被拖成两行（2026-10-23）
        lines.append(
            f"- [ ] {short}：[PR]({pr.get('html_url', '')})"
            f"（[新版 README 预览]({preview})）"
        )
        # W3 明细行（2026-10-23 用户反馈「每个 pr 的细节太少」）：2 格缩进紧跟条目
        # （渲染并入同一列表项）；标题取前 2 条、超 20 字截断——宁短勿长
        bits: list[str] = []
        # ⚠️ 可合并状态徽章打头（有冲突的 PR 勾选合并必 405——合并前先看到，省一轮失败）
        _state = str(pr.get("mergeable_state") or "")
        if _state == "dirty":
            bits.append("⚠️ 有冲突（需先解决）")
        elif _state == "blocked":
            bits.append("⚠️ 被分支保护规则阻挡")
        elif _state == "draft":
            bits.append("⚠️ 草稿态（无法合并）")
        if isinstance(pr.get("changed_files"), int):
            bits.append(f"{pr['changed_files']} 个文件")
        if isinstance(pr.get("additions"), int) and isinstance(pr.get("deletions"), int):
            bits.append(f"+{pr['additions']}/-{pr['deletions']} 行")
        n = pr.get("commits")
        if isinstance(n, int) and n > 0:
            titles = [
                str(t).strip()
                for t in (pr.get("commit_titles") or [])
                if isinstance(t, str) and str(t).strip()
            ]
            if titles:
                shown = [
                    (t[:20] + "…") if len(t) > 20 else t for t in titles[:2]
                ]
                bits.append(
                    f"{n} 次提交：{'、'.join(shown)}{' 等' if len(titles) > 2 else ''}"
                )
            else:
                bits.append(f"{n} 次提交")
        if bits:
            lines.append("  ↳ " + " · ".join(bits))
    lines += [
        "",
        # 尾部逐控件一行式（2026-10-23 交互治理）：每个控件说清「勾了/点了发生什么、
        # 何时生效、不会发生什么」——回应用户反复追问的「我勾这个到底会不会……」
        "💡 每个操作的效果（一行说清）：",
        "- 勾选方框 = 授权自动合并（可多选批量）：任务下次运行时合并（定时到点或保存 "
        "README 都会触发运行）；",
        "- 手动点 Merge = 立即生效，工具不会为同内容重开 PR；",
        "- 放着不动 = 完全没问题：PR 留着以后合，主页照样是新鲜的；",
        "- 勾选后无需手动关本清单——全部处理完会自动关闭，提前手动关会让勾选失效；",
        "- 勾「全部合并」后清单十几秒内自动同步（GitHub 服务器排队，偶尔更久）；"
        "没变化就刷新本页。若提示 Could not update issue comment，也是刷新后重新点一次"
        "（同步任务正在改写正文）；",
        "- 合并失败（如冲突）的勾选会在清单刷新时清除，修复后需重新勾选。",
        "",
        "三种处理方式的效果：",
        # 准确口径（2026-10-23 核验回填）：合并只落仓库；主页仅缺描述仓库的补位简介
        # 随后吸收（§5.6），统计与其余内容不受影响——不再写「主页本轮已经吸收」
        "- **合并** → 新 README 落进仓库；主页只有缺描述仓库的一句话简介会随后吸收"
        "（下一轮），统计与其余内容不受影响；",
        "- **关闭** → 拒绝这批文案：主页将不再包含它（本轮或下一轮生效），同批内容不会自动重开，"
        "下次新提交会把新批次连同旧内容一起带回来；",
        "- **先放着** → 完全没问题：PR 留着以后合，主页照样是新鲜的"
        "（期间有新批次时 PR 会被刷新，合并前请重读 diff）。",
    ]
    if tail:
        lines += ["", tail]
    return "\n".join(lines) + "\n"


def update_checklist(
    client: GitHubClient,
    owner_repo: str,
    open_prs: list[dict],
    *,
    username: str,
    updated_at: str,
    branch: str = PR_BRANCH,
    tail: str = "",
    existing=UNSET,
) -> dict | None:
    """创建/刷新唯一清单 issue；全部结清时自动关闭；无 open PR 且无既有 issue → 不开。

    tail 附加在 body 末尾（等待首行、⏰ 终态行等，§4-3/§4-5）。
    existing：调用方开局预取的清单 issue（2026-10-23 钩子①同一次读取的缓存，
    省一次搜索）；不传（UNSET，含预取失败）则现场搜索；显式传 None 表示
    确认无既有清单（直接走创建）。
    返回 {"number", "html_url", "body"} 或 None。异常向上抛（cli 隔离，IW26）。
    """
    repo = f"{API}/repos/{owner_repo}"
    if existing is UNSET:
        existing = find_checklist_issue(client, owner_repo)
    if not open_prs:
        if existing:
            # 关闭同时清掉已结清条目（OR04：body 不再含已处理 PR 链接）
            client._patch(
                f"{repo}/issues/{existing['number']}",
                {
                    "state": "closed",
                    "body": build_checklist_body(
                        [], username=username, updated_at=updated_at, branch=branch
                    ),
                },
            )
        return None
    body = build_checklist_body(
        open_prs, username=username, updated_at=updated_at, branch=branch, tail=tail
    )
    if existing:
        client._patch(
            f"{repo}/issues/{existing['number']}",
            {"body": body, "assignees": [username]},
        )
        return {"number": existing["number"], "html_url": existing.get("html_url", ""), "body": body}
    data, _ = client._post(
        f"{repo}/issues",
        {
            "title": f"{CHECKLIST_TITLE}（待合并 PR）",
            "body": body,
            "assignees": [username],
        },
    )
    assert isinstance(data, dict)
    # body 回带刚写入的清单正文（而非 API 回显）：收尾 append_final_state 在其上追加，
    # 即使 POST 响应不含 body 也能保住条目集合（OR13）
    return {"number": data["number"], "html_url": data.get("html_url", ""), "body": body}


def append_final_state(
    client: GitHubClient,
    owner_repo: str,
    issue: dict | None,
) -> None:
    """收尾：清单仍开着时原地追加终态行（Overview 进 main，§4-5）。

    issue 为 None（已结清/从未创建）时直接跳过（IW07 附）。
    有意变更（A-P0）：项目介绍功能已移除，project_pr_url 参数与分支一并下线。
    """
    if not issue:
        return
    lines = ["", "本轮 Overview 已进入 main"]
    body = str(issue.get("body") or "") + "\n" + "\n".join(lines) + "\n"
    client._patch(f"{API}/repos/{owner_repo}/issues/{issue['number']}", {"body": body})
