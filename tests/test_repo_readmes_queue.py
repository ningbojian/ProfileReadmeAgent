"""repo_readmes.py 队列计算测试（测试计划 Q01-Q13、Q26，模式 B 五分支，全离线）。

设计 §4 队列图：无新提交跳过 / 仅 README 只推进基线 / 首见只记基线（force 例外，
合成全量队列项——GitHub compare 拒绝空树 SHA，不发 compare）/ compare 404/409
重置基线+中文警告 / 有代码变更入队且基线不前进。
"""

import pytest

from profile_agent.config import RepoReadmesConfig
from profile_agent.models import Repo
from profile_agent.repo_readmes import CompareGone, compute_queue, participants

ALICE = "alice"


def _repo(name: str) -> Repo:
    return Repo(name=name, full_name=f"{ALICE}/{name}")


def _entry(sha="base123", **kw):
    e = {
        "sha": sha,
        "block_hash": "",
        "mode": "block",
        "failed_hash": "",
        "last_input_hash": "",
        "pending": False,
        "updated_at": "old",
    }
    e.update(kw)
    return e


class FakeFetchers:
    """按 (仓库, base, head) 注册 compare 响应；记录调用。"""

    def __init__(self, heads=None, compares=None):
        self.heads = dict(heads or {})
        self.compares = dict(compares or {})
        self.head_calls: list[str] = []
        self.compare_calls: list[tuple] = []

    def fetch_head(self, full_name: str) -> str:
        self.head_calls.append(full_name)
        return self.heads[full_name]

    def fetch_compare(self, full_name: str, base: str, head: str):
        self.compare_calls.append((full_name, base, head))
        key = (full_name, base, head)
        if key not in self.compares:
            raise AssertionError(f"未注册的 compare：{key}")
        value = self.compares[key]
        if isinstance(value, Exception):
            raise value
        return value


def _run(repos, entries, fetchers, cfg=None, force=False):
    cfg = cfg or RepoReadmesConfig(enabled=True)
    return compute_queue(
        repos,
        entries,
        cfg,
        ALICE,
        fetch_head=fetchers.fetch_head,
        fetch_compare=fetchers.fetch_compare,
        force=force,
    )


# ---- 无新提交 / 仅 README（Q01/Q02）----


def test_no_new_commits_skips_without_touching_baseline():
    """Q01：基线..HEAD 无差异 → 不入队、基线不动、零 compare 成本。"""
    entries = {"alice/cool-tool": _entry("base123")}
    fetchers = FakeFetchers(heads={"alice/cool-tool": "base123"})
    queue = _run([_repo("cool-tool")], entries, fetchers)
    assert queue == []
    assert entries["alice/cool-tool"]["sha"] == "base123"
    assert fetchers.compare_calls == []  # base==head 连 compare 都不发


def test_readme_only_change_advances_baseline_not_queue():
    """Q02：变更只有 README 自身 → 只推进基线（防自触发循环）。"""
    entries = {"alice/cool-tool": _entry("base123")}
    fetchers = FakeFetchers(
        heads={"alice/cool-tool": "def456"},
        compares={
            ("alice/cool-tool", "base123", "def456"): {
                "commits": [{"sha": "def456", "message": "docs: 机器人更新 README"}],
                "files": [{"filename": "README.md", "patch": "@@ -1 +1 @@\n-旧\n+新"}],
            }
        },
    )
    queue = _run([_repo("cool-tool")], entries, fetchers)
    assert queue == []
    assert entries["alice/cool-tool"]["sha"] == "def456"
    assert entries["alice/cool-tool"]["failed_hash"] == ""
    assert entries["alice/cool-tool"]["last_input_hash"] == ""


def test_en_only_commit_advances_baseline_not_queue():
    """WP7：变更只有 README.en.md 自身 → 与 README.md 同等防自触发：只推进基线不入队。"""
    entries = {"alice/cool-tool": _entry("base123")}
    fetchers = FakeFetchers(
        heads={"alice/cool-tool": "def456"},
        compares={
            ("alice/cool-tool", "base123", "def456"): {
                "commits": [{"sha": "def456", "message": "docs: 更新英文 README"}],
                "files": [{"filename": "README.en.md", "patch": "@@ -1 +1 @@\n-Old\n+New"}],
            }
        },
    )
    queue = _run([_repo("cool-tool")], entries, fetchers)
    assert queue == []
    assert entries["alice/cool-tool"]["sha"] == "def456"


# ---- 首见（Q03/Q07）----


def test_first_seen_records_baseline_only():
    """Q03：首次见到的仓库只记基线不入队（防启用即 PR 风暴）。"""
    entries: dict = {}
    fetchers = FakeFetchers(heads={"alice/cool-tool": "head789"})
    queue = _run([_repo("cool-tool")], entries, fetchers)
    assert queue == []
    assert entries["alice/cool-tool"]["sha"] == "head789"
    assert entries["alice/cool-tool"]["block_hash"] == ""
    assert fetchers.compare_calls == []  # 无 compare、无 AI


def test_first_seen_with_force_queues_synthesized_full_item():
    """Q07：首见 + force → 合成全量队列项入队（不发 compare——空树 compare 被 GitHub 404 拒绝）。"""
    entries: dict = {}
    fetchers = FakeFetchers(heads={"alice/cool-tool": "head789"})
    queue = _run([_repo("cool-tool")], entries, fetchers, force=True)
    assert [q.full_name for q in queue] == ["alice/cool-tool"]
    item = queue[0]
    assert item.head_sha == "head789"
    assert item.base_sha == "head789"  # 无真实 diff 基线
    assert item.files == [] and item.diff_text == ""  # 原料由 AI 层文件树降级补齐
    assert item.commits and "强制重跑" in item.commits[0]["message"]
    assert fetchers.compare_calls == []  # 合成路径零 compare（生产空树 compare 必 404）
    assert "alice/cool-tool" not in entries  # 入队不记基线（开 PR 时基线不前进）


def test_baselined_no_change_with_force_queues_synthesized_full_item():
    """force 对已基线且无新提交的仓同样全量重跑：绕过 base==head 的零成本跳过。"""
    entries = {"alice/cool-tool": _entry("head789")}
    fetchers = FakeFetchers(heads={"alice/cool-tool": "head789"})
    queue = _run([_repo("cool-tool")], entries, fetchers, force=True)
    assert [q.full_name for q in queue] == ["alice/cool-tool"]
    assert queue[0].base_sha == queue[0].head_sha == "head789"
    assert queue[0].files == []
    assert fetchers.compare_calls == []
    assert entries["alice/cool-tool"]["sha"] == "head789"  # 基线不前进


# ---- compare 失效（Q04/Q05）----


def test_compare_404_resets_baseline_with_chinese_warning(capsys):
    """Q04：compare 404（历史被 rebase 重写）→ 重置基线 + 中文警告，不阻断其余仓库。"""
    entries = {
        "alice/cool-tool": _entry("base123"),
        "alice/old-thing": _entry("base123"),
    }
    fetchers = FakeFetchers(
        heads={"alice/cool-tool": "def456", "alice/old-thing": "head222"},
        compares={
            ("alice/cool-tool", "base123", "def456"): CompareGone("404"),
            ("alice/old-thing", "base123", "head222"): {
                "commits": [{"sha": "head222", "message": "feat: 新功能"}],
                "files": [{"filename": "app.py", "patch": "+x = 1"}],
            },
        },
    )
    queue = _run([_repo("cool-tool"), _repo("old-thing")], entries, fetchers)
    assert entries["alice/cool-tool"]["sha"] == "def456"  # 重置到当前 HEAD
    assert [q.full_name for q in queue] == ["alice/old-thing"]  # 其余仓库照常
    err = capsys.readouterr().err
    assert "重置" in err and "cool-tool" in err


def test_compare_409_resets_baseline_same_as_404():
    """Q05：409 同路径。"""
    entries = {"alice/cool-tool": _entry("base123")}
    fetchers = FakeFetchers(
        heads={"alice/cool-tool": "def456"},
        compares={("alice/cool-tool", "base123", "def456"): CompareGone("409")},
    )
    queue = _run([_repo("cool-tool")], entries, fetchers)
    assert entries["alice/cool-tool"]["sha"] == "def456"
    assert queue == []


# ---- 逐仓隔离（生产事故回归：一仓 404 不得拖死整个模式 B 段）----


def test_fetch_head_error_isolated_per_repo(capsys):
    """某仓 fetch_head 抛错（如 WORKFLOW_PAT 对该仓 404 不可见）→ 只跳过该仓并计入
    error，其余仓库照常处理，不拖死整个队列；坏仓不记基线（下轮重试）。"""
    from profile_agent.github_client import GitHubError

    entries = {"alice/old-thing": _entry("base123")}

    class HeadErrFetchers(FakeFetchers):
        def fetch_head(self, full_name: str) -> str:
            self.head_calls.append(full_name)
            if full_name == "alice/cool-tool":
                raise GitHubError("GitHub API 返回 404：用户不存在或仓库不可见")
            return self.heads[full_name]

    fetchers = HeadErrFetchers(
        heads={"alice/old-thing": "def456"},
        compares={
            ("alice/old-thing", "base123", "def456"): {
                "commits": [{"sha": "def456", "message": "feat: 新功能"}],
                "files": [{"filename": "app.py", "patch": "+x = 1"}],
            }
        },
    )
    stats: dict = {}
    queue = compute_queue(
        [_repo("cool-tool"), _repo("old-thing")],
        entries,
        RepoReadmesConfig(enabled=True),
        ALICE,
        fetch_head=fetchers.fetch_head,
        fetch_compare=fetchers.fetch_compare,
        stats=stats,
    )
    assert [q.full_name for q in queue] == ["alice/old-thing"]  # 坏仓跳过、好仓照常
    assert "alice/cool-tool" not in entries  # 坏仓不记基线（下轮重试）
    assert stats.get("error") == 1  # 计数进 stats → 状态行可读
    err = capsys.readouterr().err
    assert "cool-tool" in err and "跳过" in err  # 警告点名坏仓


def test_compare_non_gone_error_isolated_per_repo(capsys):
    """fetch_compare 抛非 CompareGone 错误（如生产里 GitHubError 无 .status 逃逸）→
    同样只跳过该仓，不拖死其他仓库。"""
    entries = {"alice/cool-tool": _entry("base123"), "alice/old-thing": _entry("base123")}

    class BoomFetchers(FakeFetchers):
        def fetch_compare(self, full_name, base, head):
            self.compare_calls.append((full_name, base, head))
            if full_name == "alice/cool-tool":
                raise RuntimeError("compare 炸了")
            return self.compares[(full_name, base, head)]

    fetchers = BoomFetchers(
        heads={"alice/cool-tool": "def456", "alice/old-thing": "def456"},
        compares={
            ("alice/old-thing", "base123", "def456"): {
                "commits": [{"sha": "def456", "message": "feat"}],
                "files": [{"filename": "app.py", "patch": "+x"}],
            }
        },
    )
    stats: dict = {}
    queue = compute_queue(
        [_repo("cool-tool"), _repo("old-thing")],
        entries,
        RepoReadmesConfig(enabled=True),
        ALICE,
        fetch_head=fetchers.fetch_head,
        fetch_compare=fetchers.fetch_compare,
        stats=stats,
    )
    assert [q.full_name for q in queue] == ["alice/old-thing"]
    assert stats.get("error") == 1
    assert "cool-tool" in capsys.readouterr().err


# ---- 代码变更入队（Q06/Q08）----


def test_code_change_queues_without_advancing_baseline():
    """Q06：有代码变更 → 入队（带 commits 与 files），基线不前进。"""
    entries = {"alice/cool-tool": _entry("base123")}
    commits = [{"sha": "def456", "message": "feat: 新功能"}]
    files = [{"filename": "src/app.py", "patch": "@@ -1 +1 @@\n+x"}]
    fetchers = FakeFetchers(
        heads={"alice/cool-tool": "def456"},
        compares={("alice/cool-tool", "base123", "def456"): {"commits": commits, "files": files}},
    )
    queue = _run([_repo("cool-tool")], entries, fetchers)
    assert [q.full_name for q in queue] == ["alice/cool-tool"]
    assert queue[0].commits == commits
    assert queue[0].files == files
    assert entries["alice/cool-tool"]["sha"] == "base123"  # 入队阶段绝不推进


def test_huge_diff_truncates_recent_first_with_marker():
    """Q08：diff 超限 → 正文截到 max（裁定 R-T1：标记允许超出少量），最近优先。"""
    entries = {"alice/cool-tool": _entry("base1234")}
    old_patch = "OLD_SENTINEL " + "旧" * 300
    new_patch = "NEW_SENTINEL " + "新" * 300
    fetchers = FakeFetchers(
        heads={"alice/cool-tool": "head789"},
        compares={
            ("alice/cool-tool", "base1234", "head789"): {
                "commits": [
                    {"sha": "c111111", "message": "oldest"},
                    {"sha": "c222222", "message": "middle"},
                    {"sha": "c333333", "message": "newest"},
                ],
                "files": [
                    {"filename": "old.py", "patch": old_patch},
                    {"filename": "mid.py", "patch": "x" * 300},
                    {"filename": "new.py", "patch": new_patch},
                ],
            }
        },
    )
    queue = _run([_repo("cool-tool")], entries, fetchers)
    from profile_agent.repo_readmes import build_diff_text  # 截断口径直接对函数断言

    text = build_diff_text(queue[0].commits, queue[0].files, 200)
    marker_idx = text.find("已截断")
    assert marker_idx > 0  # 有显式截断标记
    assert marker_idx <= 200 + 60  # 正文 ≤ max，标记余量有限（R-T1）
    assert "NEW_SENTINEL" in text  # 最近提交保留
    assert "OLD_SENTINEL" not in text  # 最旧主体被裁掉
    assert queue[0].files[0]["filename"] == "old.py"  # 原始 files 不动，截断只作用于喂 AI 的文本


# ---- 范围过滤（Q10/Q11/Q12/Q13）----


def test_hard_exclude_self_repo_even_when_named_and_forced():
    """Q10：username/username 永不入队——include 点名与 force 均不放行（§12-R1）。"""
    repos = [_repo("alice"), _repo("cool-tool")]
    cfg = RepoReadmesConfig(enabled=True, include=["alice", "cool-tool"])
    assert participants(repos, cfg, ALICE) == ["alice/cool-tool"]
    # force 下同样排除：self 仓不进队列
    entries: dict = {}
    fetchers = FakeFetchers(heads={"alice/alice": "head789", "alice/cool-tool": "head789"})
    queue = _run(repos, entries, fetchers, cfg=cfg, force=True)
    assert [q.full_name for q in queue] == ["alice/cool-tool"]
    assert fetchers.compare_calls == []  # force 走合成入队，零 compare


def test_include_limits_b_scope_and_skips_compare_for_others():
    """Q11：include 白名单限定参与范围；名单外连 head/compare 都不查。"""
    cfg = RepoReadmesConfig(enabled=True, include=["cool-tool"])
    repos = [_repo("cool-tool"), _repo("old-thing")]
    assert participants(repos, cfg, ALICE) == ["alice/cool-tool"]
    # 首见 + force 合成入队（不发 compare）：old-thing 的 head/compare 都不会被查询
    # （FakeFetchers 对未注册键直接 AssertionError，能跑通即证明未查询）
    fetchers = FakeFetchers(heads={"alice/cool-tool": "h1"})
    queue = _run(repos, {}, fetchers, cfg=cfg, force=True)
    assert [q.full_name for q in queue] == ["alice/cool-tool"]
    assert fetchers.head_calls == ["alice/cool-tool"]
    assert fetchers.compare_calls == []


def test_exclude_removes_from_include_set():
    """Q12：exclude 在 include 之上再排除。"""
    repos = [_repo("cool-tool"), _repo("old-thing")]
    cfg = RepoReadmesConfig(enabled=True, exclude=["old-thing"])
    assert participants(repos, cfg, ALICE) == ["alice/cool-tool"]


def test_empty_include_exclude_uses_repos_rules():
    """Q13：全空时按 repos 过滤规则的全部仓库参与（fork/私有/黑名单被 normalize 剔除）。"""
    from profile_agent.aggregate import normalize_repos
    from profile_agent.config import ReposConfig

    raw = [
        {"name": "cool-tool", "full_name": "alice/cool-tool", "private": False, "fork": False},
        {"name": "forked", "full_name": "alice/forked", "private": False, "fork": True},
        {"name": "secret", "full_name": "alice/secret", "private": True, "fork": False},
        {"name": "old-profile", "full_name": "alice/old-profile", "private": False, "fork": False},
        {"name": "archived-one", "full_name": "alice/archived-one", "private": False, "fork": False, "archived": True},
    ]
    repos = normalize_repos(raw, ReposConfig(exclude=["alice/old-profile"]))
    cfg = RepoReadmesConfig(enabled=True)
    assert participants(repos, cfg, ALICE) == [
        "alice/archived-one",
        "alice/cool-tool",
    ]


# ---- 非默认分支（Q26）----


def test_feature_branch_commits_count_only_after_merge():
    """Q26：功能分支提交在合入默认分支前不计入，合入后下轮正常入队（§5.5 分支模型）。"""
    entries = {"alice/cool-tool": _entry("abc1234")}
    # 功能分支有提交，但默认分支 HEAD 未动 → base==head，连 compare 都不发
    fetchers = FakeFetchers(heads={"alice/cool-tool": "abc1234"})
    queue = _run([_repo("cool-tool")], entries, fetchers)
    assert queue == []
    assert entries["alice/cool-tool"]["sha"] == "abc1234"
    # 合入默认分支后：head 前进，compare(基线..HEAD) 覆盖到
    fetchers2 = FakeFetchers(
        heads={"alice/cool-tool": "def5678"},
        compares={
            ("alice/cool-tool", "abc1234", "def5678"): {
                "commits": [{"sha": "def5678", "message": "merge: 功能分支"}],
                "files": [{"filename": "core.py", "patch": "+y = 2"}],
            }
        },
    )
    queue2 = _run([_repo("cool-tool")], entries, fetchers2)
    assert [q.full_name for q in queue2] == ["alice/cool-tool"]
