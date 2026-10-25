"""git-data 推送与 PR 流（设计 §5.5，测试计划 GP）。

- 全走 GitHub REST API，无本地 git；
- 每仓恒为一条 open PR：blob→tree→commit→ref（新建或 force-push）→PR 创建或刷新；
- 合并检测把基线推进到 PR head SHA 而非 merge commit（§12-R10）；
- 合并仅限清单勾选授权的 merge_pr（2026-10-23 §5.5 修订：勾选 = 人工授权，
  运行时代为合并）；推送/检测流仍永不合并（GP16），关闭接口永不调用；
- 抑制只拦 cached（AI 输入哈希命中）：推送层不查 PR 状态（GP05）。
"""

from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass
from typing import Callable

from profile_agent.github_client import API, GitHubClient, GitHubError
from profile_agent.readme_block import END, START
from profile_agent.repo_readmes_ai import ProcessResult

PR_BRANCH = "readme-auto/update"
# 首次接管专用分支（2026-10-25）：与日常更新分支分开，避免普通模式 B PR 干扰预览语义
TAKEOVER_BRANCH = "readme-auto/takeover"
BASE_BRANCH = "main"


@dataclass
class PrResult:
    number: int
    html_url: str
    refreshed: bool


def takeover_pr_title(username: str) -> str:
    """首次接管 PR 标题：一眼能看出这是预览，不是日常自动更新。"""
    return f"feat({username}): 首次接管主页 README（预览，待审核）"


def takeover_pr_body(*, username: str, snapshot_path: str, bilingual: bool) -> str:
    """首次接管 PR 正文：讲清「合并什么、关了什么后果、冲突怎么办」。"""
    files = "`README.md`" + (" 与 `README.en.md`" if bilingual else "")
    return (
        f">这是给 {username} 的一次性**首次接管预览**：合并本 PR = 采用工具生成的版面；"
        f"关闭本 PR = 保留你现在的 `README.md`（main 不会改动）。\n"
        "\n"
        f"本 PR 同时更新 {files}，并把首次接管状态写入 `{snapshot_path}`。"
        "两个文件必须一起合并——快照缺失会让下一轮再次进入首次接管。\n"
        "\n"
        "你原来的内容会**逐字完整**放进 custom 标记区，不会被翻译、改写或删除；"
        "联系方式、社交账号、HTML 都原样保留。\n"
        "\n"
        "请注意：\n"
        "- 合并后，下一轮才会恢复正常自动更新；\n"
        "- 关闭本 PR 不会改动 main，但下一轮会重新开一个接管 PR"
        "（main 上没有地方记录“已拒绝”）；\n"
        "- PR 打开期间如果 main 又前进了，合并可能冲突：关闭重开，或手动解决冲突即可；\n"
        "- 在分支上手动解决过冲突后，**合并前别用「强制重写主页」**——"
        "它会以最新 main 重新生成并覆盖你在分支上的改动。\n"
    )


def build_pr_title(full_name: str) -> str:
    """标题带仓库短名——多仓多标签页靠它分辨（GP03）。"""
    short = full_name.split("/", 1)[-1]
    return f"docs({short}): README 自动更新（待审核）"


def build_pr_body(
    *, commits: list[dict], base_sha: str, head_sha: str, bilingual: bool = False
) -> str:
    """PR body：三结局首行警示 + marker 说明 + base..head 短 SHA 列表（GP04）。

    marker 字样（profile-agent:readme）允许出现在 PR body——只禁止进 AI prompt。
    bilingual（WP7）：说明两个文件在同一 PR 里审核合并。
    """
    base8, head8 = base_sha[:8], head_sha[:8]
    sha_lines = "\n".join(
        f"- `{c.get('sha', '')[:8]}` {str(c.get('message', '')).strip().splitlines()[0] if c.get('message') else ''}".rstrip()
        for c in commits
    )
    dual_line = (
        "本次同步更新 `README.md`（中文）与 `README.en.md`（英文）两个文件，同一 PR 审核合并。\n"
        if bilingual
        else ""
    )
    return (
        ">三结局：**合并本 PR = 采用这批文案**；**关闭本 PR = 拒绝这批文案**"
        "（同输入不会重发）；**先不处理** = 保持原样，合并前请重读 diff。\n"
        "\n"
        "改动仅限 README 中由两行 HTML 注释标记包裹的托管区"
        "（标记 `profile-agent:readme` 在渲染页不可见，请勿手删）。\n"
        f"{dual_line}"
        f"覆盖提交范围：`{base8}..{head8}`\n"
        "\n"
        "涉及提交：\n"
        f"{sha_lines}\n"
        "\n"
        "如需放弃本批文案：关闭本 PR 即可——删掉区间 = 放弃，"
        "本批不会再来（除非出现新提交触发新哈希）。\n"
    )


# build_pr_body 的「涉及提交」列表行：- `sha8` 标题（标题取每条 message 首行）
_COMMIT_LINE_RE = re.compile(r"^- `[0-9a-f]+`\s*(.*)$")


def parse_commit_titles(body: str) -> list[str]:
    """从 PR body 的「涉及提交」段提取各提交标题（与 build_pr_body 对偶，W3）。

    清单明细行的标题零额外取数——直接复用 body 里已有的列表；marker 之前/之后的
    散文段不掺入，非列表行即段落结束（宁缺勿错）。
    """
    out: list[str] = []
    seen_marker = False
    for line in (body or "").splitlines():
        s = line.strip()
        if not seen_marker:
            if s == "涉及提交：":
                seen_marker = True
            continue
        if not s:
            continue  # 空行跳过（无提交时 marker 后直接接散文段前的空行）
        m = _COMMIT_LINE_RE.match(s)
        if not m:
            break  # 段落结束
        title = m.group(1).strip()
        if title:
            out.append(title)
    return out


def _owner(full_name: str) -> str:
    return full_name.split("/", 1)[0]


def get_open_pr(client: GitHubClient, full_name: str, branch: str, *, base: str = BASE_BRANCH) -> dict | None:
    """查同 head 同 base 的 open PR；无则 None（同仓恒单条，GP02）。

    base 跟随仓库默认分支（§5.5：master 仓库不再永远查不到/开不出 PR）。
    """
    data, _ = client._get(
        f"{API}/repos/{full_name}/pulls?state=open&head={_owner(full_name)}:{branch}&base={base}"
    )
    prs = data if isinstance(data, list) else []
    return prs[0] if prs else None


def get_pr_detail(client: GitHubClient, full_name: str, number: int) -> dict | None:
    """单条 PR 明细（W3 清单详情行）：增删行/文件数/提交数 + body（含涉及提交）。

    list 接口实测不带 additions/deletions/changed_files/commits——逐 PR 单 GET 才有，
    每仓每轮至多 1 次；异常向上抛，由 cli 隔离成「该条清单省略细节」。
    """
    data, _ = client._get(f"{API}/repos/{full_name}/pulls/{number}")
    return data if isinstance(data, dict) else None


def find_latest_merged_pr(
    client: GitHubClient, full_name: str, branch: str, *, base: str = BASE_BRANCH
) -> dict | None:
    """最近一条已合并的 bot PR；无合并记录返回 None。"""
    data, _ = client._get(
        f"{API}/repos/{full_name}/pulls?state=all&head={_owner(full_name)}:{branch}"
        f"&base={base}&sort=updated&direction=desc"
    )
    prs = data if isinstance(data, list) else []
    # 真实 LIST API（Pull Request Simple）只有 merged_at（null/ISO），无 merged 布尔；
    # 测试假件常用 merged=True——两族形状都认（2026-10-23 生产缺口回归）
    if prs and (prs[0].get("merged_at") or prs[0].get("merged")):
        return prs[0]
    return None


def advance_baseline_from_merged_pr(
    client: GitHubClient, entry: dict, full_name: str, branch: str, *, base: str = BASE_BRANCH
) -> bool:
    """合并检测：把该仓快照基线推进到 PR head SHA（不是 merge commit，§12-R10）。

    幂等：基线已等于 head SHA 时返回 False 不改写（GP13）；PR 仍 open 不动（GP14）。
    """
    pr = find_latest_merged_pr(client, full_name, branch, base=base)
    if not pr:
        return False
    head_sha = pr.get("head", {}).get("sha") or ""
    if not head_sha or entry.get("sha") == head_sha:
        return False
    entry["sha"] = head_sha
    return True


def merge_pr(client: GitHubClient, full_name: str, number: int) -> tuple[bool, str]:
    """合并一条被勾选授权的 PR；返回 (是否成功, 失败原因)（2026-10-23 §5.5 修订）。

    勾选动作即用户的人工合并授权，这里只是代为执行；不传 merge_method——
    走仓库配置的默认合并方式（squash-only 仓库也不会被改成 merge）。
    失败（405 不可合并/404 已删/限流…）只转成可读原因返回、绝不向外抛，
    调用方逐仓隔离——单仓失败不阻断批量合并与整轮任务。
    """
    try:
        client._request("PUT", f"{API}/repos/{full_name}/pulls/{number}/merge")
        return True, ""
    except GitHubError as exc:
        return False, str(exc)


def get_pr_state(client: GitHubClient, full_name: str, number: int) -> str:
    """读 PR 当前状态：merged / closed / open / unknown（2026-10-23 勾选合并降噪）。

    已手动合并的 PR 再被勾选会 PUT 405 并打出吓人的「勾选合并失败」——先 GET
    分流成「已合并，无需处理」；已关闭同理。查询失败（404/限流/网络）→ unknown，
    调用方仍可走 PUT 尝试（状态查不到不该阻断合并）。真实 API 只有 merged_at
    （null/ISO）无 merged 布尔——两族形状都认（与 find_latest_merged_pr 同款防线）。
    """
    try:
        data, _ = client._get(f"{API}/repos/{full_name}/pulls/{number}", allow_404=True)
    except GitHubError:
        return "unknown"
    if not isinstance(data, dict):
        return "unknown"
    if data.get("merged_at") or data.get("merged"):
        return "merged"
    state = str(data.get("state") or "")
    if state == "closed":
        return "closed"
    if state == "open":
        return "open"
    return "unknown"


def push_files(
    client: GitHubClient,
    full_name: str,
    files: dict[str, str],
    head_sha: str,
    *,
    branch: str = PR_BRANCH,
) -> str:
    """多文件版 blob→tree→commit→ref 四连（WP7）；返回新 commit SHA。

    files：路径 → 内容；全部 blob 先建、单 tree 单 commit 落地（两文件同船）。
    ref 不存在则创建，存在则 force-push（每仓恒一条分支，GP02）。
    """
    repo = f"{API}/repos/{full_name}"
    tree_entries: list[dict] = []
    for path, content in files.items():
        blob, _ = client._post(f"{repo}/git/blobs", {"content": content, "encoding": "utf-8"})
        tree_entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
    tree, _ = client._post(
        f"{repo}/git/trees",
        {"base_tree": head_sha, "tree": tree_entries},
    )
    commit, _ = client._post(
        f"{repo}/git/commits",
        {
            "message": "docs: 更新 README 托管区（profile-agent 自动）",
            "tree": tree["sha"],
            "parents": [head_sha],
        },
    )
    ref, _ = client._get(f"{repo}/git/ref/heads/{branch}", allow_404=True)
    if ref is None:
        client._post(f"{repo}/git/refs", {"ref": f"refs/heads/{branch}", "sha": commit["sha"]})
    else:
        client._patch(
            f"{repo}/git/refs/heads/{branch}", {"sha": commit["sha"], "force": True}
        )
    return commit["sha"]


def push_readme(
    client: GitHubClient, full_name: str, content: str, head_sha: str, *, branch: str = PR_BRANCH
) -> str:
    """单文件推送（委托 push_files，向后兼容）；返回新 commit SHA。"""
    return push_files(client, full_name, {"README.md": content}, head_sha, branch=branch)


def open_or_refresh_pr(
    client: GitHubClient,
    *,
    full_name: str,
    content: str,
    head_sha: str,
    title: str,
    body: str,
    branch: str = PR_BRANCH,
    base: str = BASE_BRANCH,
    files: dict[str, str] | None = None,
) -> PrResult:
    """推送内容并开/刷同一条 PR；永不调用合并或关闭接口（GP16）。

    files（WP7）：显式指定多文件（路径→内容）；缺省回落 content 单文件。
    """
    repo = f"{API}/repos/{full_name}"
    existing = get_open_pr(client, full_name, branch, base=base)
    push_files(
        client,
        full_name,
        files if files is not None else {"README.md": content},
        head_sha,
        branch=branch,
    )
    if existing:
        number = existing["number"]
        client._patch(f"{repo}/pulls/{number}", {"body": body})
        return PrResult(number=number, html_url=existing.get("html_url", ""), refreshed=True)
    data, _ = client._post(
        f"{repo}/pulls", {"title": title, "body": body, "head": branch, "base": base}
    )
    assert isinstance(data, dict)
    return PrResult(number=data["number"], html_url=data.get("html_url", ""), refreshed=False)


def read_branch_block_hash(client: GitHubClient, full_name: str, branch: str) -> str:
    """读 bot 分支上 README 托管区内容哈希（GP10 漂移检测信号）。

    分支无 README 或 marker 不平衡时返回空串（调用方视为「无法比对」）。
    """
    data, _ = client._get(f"{API}/repos/{full_name}/contents/README.md?ref={branch}")
    if not isinstance(data, dict) or data.get("encoding") != "base64":
        return ""
    try:
        content = base64.b64decode(str(data.get("content") or "")).decode("utf-8", errors="replace")
    except (ValueError, OSError):
        return ""
    i, j = content.find(START), content.find(END)
    if i < 0 or j < 0 or j < i:
        return ""
    return content_block_hash(content)


def content_block_hash(content: str) -> str:
    """托管区内容哈希（与 read_branch_block_hash 同口径：marker 区块整体；无 marker 取全文）。"""
    i, j = content.find(START), content.find(END)
    block = content[i : j + len(END)] if i >= 0 and j >= i else content
    return "sha256:" + hashlib.sha256(block.encode("utf-8")).hexdigest()


def push_results(
    client: GitHubClient,
    results: list[ProcessResult],
    *,
    entries: dict[str, dict] | None = None,
    now: Callable[[], str] | None = None,
    branch: str = PR_BRANCH,
    bases: dict[str, str] | None = None,
) -> list[PrResult]:
    """把 AI 结果推送成 PR；只推 generated，cached/failed/pending 全部跳过。

    抑制在 AI 输入哈希层完成（GP05/GP09/GP12）：cached 结果连一个 HTTP 请求都不发。
    force 重新生成的结果是 generated，照常推送（GP11）。
    传入 entries 时，推送成功后就地写回该仓 block_hash（托管区内容哈希，SB07）。
    bases：full_name → 默认分支（PR 目标，§5.5）；缺省回落 main。
    """
    pushed: list[PrResult] = []
    for r in results:
        if r.status != "generated":
            continue
        bilingual = bool(r.content_en)  # WP7：en 内容非空才推双文件（zh-only 回落单文件）
        files = {"README.md": r.content or ""}
        if bilingual:
            files["README.en.md"] = r.content_en
        res = open_or_refresh_pr(
            client,
            full_name=r.full_name,
            content=r.content or "",
            head_sha=r.head_sha,
            title=build_pr_title(r.full_name),
            body=build_pr_body(
                commits=r.commits,
                base_sha=r.base_sha,
                head_sha=r.head_sha,
                bilingual=bilingual,
            ),
            branch=branch,
            base=(bases or {}).get(r.full_name, BASE_BRANCH),
            files=files,
        )
        if entries is not None:
            entry = entries.setdefault(
                r.full_name,
                {
                    "sha": r.head_sha,
                    "block_hash": "",
                    "mode": "block",
                    "failed_hash": "",
                    "last_input_hash": "",
                    "pending": False,
                },
            )
            entry["block_hash"] = content_block_hash(r.content or "")
            if bilingual:
                entry["block_hash_en"] = content_block_hash(r.content_en)
        pushed.append(res)
    return pushed
