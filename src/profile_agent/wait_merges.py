"""等待合并步（设计 §4-3、§5.5，仅手动触发勾选 wait_for_merges 时执行）。

- 每 POLL_SECONDS 轮询清单内 PR；合并或关闭都算「已处理」；
- 清单清空 → 提前返回 cleared；窗口耗尽 → 返回 timeout（cli 追加 ⏰ 终态行后继续 A）；
- minutes<=0（未勾选 / cron）或清单为空 → 直接 cleared、零轮询零请求（IW18/IW28）；
- 时间经注入 now_fn/sleep_fn（秒，monotonic 语义），离线测试零真实 sleep。
"""

from __future__ import annotations

from typing import Callable

from .github_client import API, GitHubClient

POLL_SECONDS = 30


def wait_first_line(count: int, until_bj: str) -> str:
    """等待开始首行（§4-3a）：条数、窗口至 HH:MM、超时后果、不会卡死——写进 issue/日志/Summary。"""
    return (
        f"共 {count} 条待合并，窗口至 {until_bj}，合并完立即继续；"
        "超时或不处理也会带着未合并 PR 自动继续，不会卡死"
    )


def wait_for_merges(
    client: GitHubClient,
    pr_refs: list[tuple[str, int]],
    *,
    minutes: float,
    now_fn: Callable[[], float],
    sleep_fn: Callable[[int], None],
    on_change: Callable[[list[tuple[str, int]]], None] | None = None,
    on_tick: Callable[[], None] | None = None,
) -> str:
    """等待清单内 PR 全部结清或超时；返回 "cleared" | "timeout"。

    pr_refs 为 (仓库全名, PR 号)——清单里的 PR 可能来自不同仓库，轮询打各自仓库
    （OR04/OR15 端到端按仓路由）。on_change(pending) 在每次状态变化后调用（cli 用它
    刷新 issue body + 打 ::notice::）。on_tick 每轮轮询前调用（2026-10-23 勾选合并
    钩子②：cli 在此读清单勾选并代为合并，本轮轮询即能看到结果；tick 回调自身
    异常由 cli 内部隔离，不从这里传播）。取消（如进程被杀）以异常形式向外传播——
    B 产物已落盘，不回滚（IW24 前半）。
    """
    if minutes <= 0 or not pr_refs:
        return "cleared"
    deadline = now_fn() + minutes * 60
    pending = {(str(fn), int(n)) for fn, n in pr_refs}
    while pending:
        if now_fn() >= deadline:
            return "timeout"
        sleep_fn(POLL_SECONDS)
        if on_tick is not None:
            on_tick()
        changed = False
        for ref in sorted(pending):
            data, _ = client._get(f"{API}/repos/{ref[0]}/pulls/{ref[1]}")
            info = data if isinstance(data, dict) else {}
            if info.get("merged") or info.get("state") == "closed":
                pending.discard(ref)
                changed = True
        if changed and on_change is not None:
            on_change(sorted(pending))
    return "cleared"
