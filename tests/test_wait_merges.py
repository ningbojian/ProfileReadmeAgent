"""等待合并步测试（测试计划 IW17-IW24 模块级，注入时钟、零真实 sleep）。

设计 §4-3：仅勾选 wait_for_merges 时执行；合并/关闭都算已处理；清单清空提前继续；
超时带未合并 PR 继续（issue ⏰ 终态行由 cli 负责，OR16 端到端钉）。
"""

from fake_github import FakeHttp, FakeResponse

from profile_agent.github_client import GitHubClient
from profile_agent.wait_merges import POLL_SECONDS, wait_first_line, wait_for_merges


class FakeClock:
    """注入时钟：now 返回秒（monotonic 语义），sleep 前进并记录。"""

    def __init__(self, start_seconds: float = 0.0):
        self.t = start_seconds
        self.sleeps: list[int] = []

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: int) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


def _pulls(http: FakeHttp, number: int, *responses: FakeResponse, sticky: bool = False):
    http.add("GET", f"https://api.github.com/repos/alice/alice/pulls/{number}", *responses,
             sticky=sticky)


def test_iw17_first_line_has_count_window_and_timeout_semantics():
    """IW17（首行文本权威）：条数、窗口至 HH:MM、超时后果、不会卡死。"""
    line = wait_first_line(2, "10:30")
    assert "共 2 条待合并" in line
    assert "窗口至 10:30" in line
    assert "超时" in line and "不会卡死" in line


def test_iw20_merge_converges_pending_and_calls_on_change():
    """IW20（模块机制权威）：等待中合并 → 清单收敛、on_change 通知、按清空提前结束。"""
    http = FakeHttp()
    _pulls(http, 42, FakeResponse(json_data={"state": "open", "merged": False}),
                   FakeResponse(json_data={"state": "open", "merged": True}))
    clock = FakeClock()
    notified: list = []
    result = wait_for_merges(
        GitHubClient("p-pat", session=http),
        [("alice/alice", 42)], minutes=30, now_fn=clock.now, sleep_fn=clock.sleep,
        on_change=lambda pending: notified.append(list(pending)),
    )
    assert result == "cleared"
    assert notified == [[]]  # 收敛后清单为空
    assert clock.sleeps == [POLL_SECONDS, POLL_SECONDS]  # 恰两次轮询间隔
    assert clock.t < 30 * 60  # 提前继续，未耗尽窗口
    assert all(r["method"] == "GET" for r in http.writes())  # 无重开无写请求


def test_iw21_closed_pr_also_settles():
    """IW21：被关闭同样算已处理——按收敛退出，无重开 POST。"""
    http = FakeHttp()
    _pulls(http, 42, FakeResponse(json_data={"state": "closed", "merged": False}))
    clock = FakeClock()
    result = wait_for_merges(
        GitHubClient("p-pat", session=http),
        [("alice/alice", 42)], minutes=30, now_fn=clock.now, sleep_fn=clock.sleep,
    )
    assert result == "cleared"
    assert http.writes() == []


def test_iw22_cleared_well_before_deadline():
    """IW22（时间语义）：清单清空在截止前结束——轮询次数远小于超时情形。"""
    http = FakeHttp()
    _pulls(http, 42, FakeResponse(json_data={"merged": True, "state": "closed"}))
    clock = FakeClock()
    assert wait_for_merges(
        GitHubClient("p-pat", session=http),
        [("alice/alice", 42)], minutes=30, now_fn=clock.now, sleep_fn=clock.sleep,
    ) == "cleared"
    assert clock.t == POLL_SECONDS  # 只睡了一个轮询间隔


def test_iw23_timeout_returns_timeout_with_bounded_polls():
    """IW23（模块侧）：超时返回 timeout 且轮询有上限（issue ⏰ 行由 cli 追加）。"""
    http = FakeHttp()
    _pulls(http, 42, FakeResponse(json_data={"state": "open", "merged": False}), sticky=True)
    clock = FakeClock()
    result = wait_for_merges(
        GitHubClient("p-pat", session=http),
        [("alice/alice", 42)], minutes=1, now_fn=clock.now, sleep_fn=clock.sleep,
    )
    assert result == "timeout"
    assert len(clock.sleeps) == 2  # 60s 窗口 / 30s 间隔 = 2 次
    assert clock.t == 60


def test_iw18_zero_minutes_or_empty_list_never_polls():
    """IW18/IW28（模块侧）：未勾选（minutes=0）或清单为空 → 直接 cleared、零请求。"""
    http = FakeHttp()
    clock = FakeClock()
    client = GitHubClient("p-pat", session=http)
    assert wait_for_merges(client, [("alice/alice", 42)], minutes=0,
                           now_fn=clock.now, sleep_fn=clock.sleep) == "cleared"
    assert wait_for_merges(client, [], minutes=30,
                           now_fn=clock.now, sleep_fn=clock.sleep) == "cleared"
    assert http.requests == []  # 连 GET 都没有；时钟未被调用
    assert clock.sleeps == []


def test_cancel_during_wait_propagates():
    """取消语义（IW24 前半）：等待中进程被取消 → 异常向外传播（B 产物已落盘，不回滚）。"""
    http = FakeHttp()
    _pulls(http, 42, FakeResponse(json_data={"state": "open", "merged": False}), sticky=True)
    clock = FakeClock()
    calls = {"n": 0}

    def cancel_sleep(seconds: int) -> None:
        calls["n"] += 1
        if calls["n"] >= 2:
            raise KeyboardInterrupt
        clock.sleep(seconds)

    try:
        wait_for_merges(
            GitHubClient("p-pat", session=http),
            [("alice/alice", 42)], minutes=30, now_fn=clock.now, sleep_fn=cancel_sleep,
        )
    except KeyboardInterrupt:
        pass
    else:  # pragma: no cover
        raise AssertionError("取消未传播")
    assert calls["n"] == 2


def test_on_tick_runs_before_each_poll_for_checkbox_merges():
    """2026-10-23 勾选合并钩子②：每轮轮询前执行 on_tick——等待期间新勾选的 PR
    在同一轮就被轮询看到合并结果（否则要多等一个 POLL_SECONDS）。"""
    http = FakeHttp()
    _pulls(http, 42, FakeResponse(json_data={"state": "open", "merged": False}),
                   FakeResponse(json_data={"merged": True, "state": "closed"}))
    clock = FakeClock()
    ticks: list[int] = []
    result = wait_for_merges(
        GitHubClient("p-pat", session=http),
        [("alice/alice", 42)], minutes=30, now_fn=clock.now, sleep_fn=clock.sleep,
        on_tick=lambda: ticks.append(len(clock.sleeps)),
    )
    assert result == "cleared"
    assert ticks == [1, 2]  # 两次轮询前各触发一次（先 tick 后 poll）
