"""清单勾选同步的极简入口（issues.edited → 回写 issue 正文）。

为什么不复用 `python -m profile_agent`：`cli` 会导入 jinja2 / anthropic 整套，
workflow 里就得先 `pip install -e`（实测 8 秒）——而用户此刻正站在清单页面上等
复选框变过来。这个入口只依赖 checklist + github_client（requests、pyyaml），
workflow 用 `PYTHONPATH=src python3 -m profile_agent.sync_cli` 直接跑源码。

令牌用内置 `GITHUB_TOKEN`：清单就在本仓库，写 issue 用不着 PAT；内置令牌发起的
编辑也不会再触发 workflow（GitHub 防循环），回写不会自激。
"""

from __future__ import annotations

import json
import os
import sys

from .checklist import sync_from_issue_event
from .github_client import GitHubClient


def main() -> int:
    """同步一次清单勾选；永远返回 0（排版同步失败不该把工作流弄红）。"""
    if os.environ.get("GITHUB_EVENT_NAME") != "issues":
        return 0
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        return 0
    try:
        with open(event_path, encoding="utf-8") as fh:
            event = json.load(fh)
        client = GitHubClient(os.environ.get("GITHUB_TOKEN") or None)
        changed = sync_from_issue_event(event, client)
        print("清单勾选已同步" if changed else "清单勾选无需调整")
    except Exception as exc:  # noqa: BLE001 —— 失败只告警，合并语义仍看清单原文
        print(f"::warning::清单勾选同步失败（不影响合并语义）：{exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
