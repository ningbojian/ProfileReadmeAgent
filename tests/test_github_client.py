"""github_client.py 测试：用假 session，无网络。"""

import base64

import pytest

from profile_agent.github_client import GitHubClient, GitHubError


class FakeResponse:
    def __init__(self, status=200, json_data=None, headers=None, text=""):
        self.status_code = status
        self._json = json_data
        self.headers = headers or {}
        self.text = text

    def json(self):
        return self._json


class FakeSession:
    """按调用顺序返回预设响应，并记录请求。"""

    def __init__(self, responses):
        self._responses = list(responses)
        self.requests = []

    def get(self, url, headers=None, timeout=None):
        self.requests.append({"url": url, "headers": headers or {}})
        return self._responses.pop(0)

    def request(self, method, url, headers=None, json=None, timeout=None):
        """承接 _request 的任意 method 调用（A-WP1 GraphQL 用；有意扩展）。"""
        self.requests.append(
            {"method": method, "url": url, "headers": headers or {}, "json": json}
        )
        return self._responses.pop(0)


def test_get_user_returns_json():
    session = FakeSession([FakeResponse(json_data={"login": "alice"})])
    client = GitHubClient(token="tok", session=session)
    assert client.get_user("alice") == {"login": "alice"}
    assert session.requests[0]["url"] == "https://api.github.com/users/alice"
    assert session.requests[0]["headers"]["Authorization"] == "Bearer tok"


def test_list_repos_paginates_via_link_header():
    page1 = FakeResponse(
        json_data=[{"name": "a"}],
        headers={"Link": '<https://api.github.com/users/alice/repos?page=2>; rel="next"'},
    )
    page2 = FakeResponse(json_data=[{"name": "b"}])
    session = FakeSession([page1, page2])
    client = GitHubClient(token=None, session=session)
    repos = client.list_repos("alice")
    assert [r["name"] for r in repos] == ["a", "b"]
    assert "page=2" in session.requests[1]["url"]
    # 无 token 时不带 Authorization 头
    assert "Authorization" not in session.requests[0]["headers"]


def test_rate_limit_raises_chinese_error():
    session = FakeSession(
        [FakeResponse(status=403, headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1792000000"}, text="rate limited")]
    )
    client = GitHubClient(token=None, session=session)
    with pytest.raises(GitHubError, match="限流"):
        client.get_user("alice")


def test_server_error_retries_then_succeeds():
    session = FakeSession(
        [FakeResponse(status=500), FakeResponse(status=500), FakeResponse(json_data={"login": "alice"})]
    )
    client = GitHubClient(token=None, session=session)
    assert client.get_user("alice")["login"] == "alice"
    assert len(session.requests) == 3


def test_not_found_raises_chinese_error():
    session = FakeSession([FakeResponse(status=404, text="nope")])
    client = GitHubClient(token=None, session=session)
    with pytest.raises(GitHubError, match="不存在"):
        client.get_user("ghost-user-xyz")


def test_get_repo_readme_decodes_base64_content():
    text = "# cool-tool\n一个很酷的工具，支持 A/B。"
    b64 = base64.b64encode(text.encode("utf-8")).decode()
    session = FakeSession([FakeResponse(json_data={"content": b64, "encoding": "base64"})])
    client = GitHubClient(token=None, session=session)
    assert client.get_repo_readme("alice/cool-tool") == text
    assert session.requests[0]["url"] == "https://api.github.com/repos/alice/cool-tool/readme"


def test_get_repo_readme_missing_returns_empty_string():
    session = FakeSession([FakeResponse(status=404, text="not found")])
    client = GitHubClient(token=None, session=session)
    assert client.get_repo_readme("alice/no-readme") == ""


def test_get_repo_readme_en_path_uses_contents_api():
    """WP7：path="README.en.md" 走 /contents/{path}（en 文件不存在该专用端点）。"""
    text = "# Title\nEnglish intro"
    b64 = base64.b64encode(text.encode("utf-8")).decode()
    session = FakeSession([FakeResponse(json_data={"content": b64, "encoding": "base64"})])
    client = GitHubClient(token=None, session=session)
    assert client.get_repo_readme("alice/cool-tool", path="README.en.md") == text
    assert session.requests[0]["url"] == (
        "https://api.github.com/repos/alice/cool-tool/contents/README.en.md"
    )
    # 带 ref 同样拼接
    session2 = FakeSession([FakeResponse(json_data={"content": b64, "encoding": "base64"})])
    client2 = GitHubClient(token=None, session=session2)
    client2.get_repo_readme("alice/cool-tool", ref="readme-auto/update", path="README.en.md")
    assert session2.requests[0]["url"] == (
        "https://api.github.com/repos/alice/cool-tool/contents/README.en.md?ref=readme-auto/update"
    )


# ---- A-WP1：GraphQL 读取原生 Pinned ----


def _graphql_pins(*full_names):
    """构造 GitHub GraphQL pinnedItems 成功响应。"""
    return FakeResponse(
        json_data={
            "data": {
                "user": {
                    "pinnedItems": {
                        "nodes": [{"nameWithOwner": n} for n in full_names]
                    }
                }
            }
        }
    )


def test_get_pinned_repos_success_returns_ordered_full_names():
    """成功时按官方置顶顺序返回 nameWithOwner；POST 走 /graphql 且带 Authorization。"""
    session = FakeSession([_graphql_pins("a/first", "b/second", "c/third")])
    client = GitHubClient(token="tok", session=session)
    assert client.get_pinned_repos("alice") == ["a/first", "b/second", "c/third"]
    req = session.requests[0]
    assert req["method"] == "POST"
    assert req["url"] == "https://api.github.com/graphql"
    # 请求体含 pinnedItems 查询串与 variables.login
    assert "pinnedItems" in req["json"]["query"]
    assert req["json"]["variables"] == {"login": "alice"}
    assert req["headers"]["Authorization"] == "Bearer tok"


def test_get_pinned_repos_success_empty_returns_empty_list():
    """用户没置顶任何仓库：返回 []（成功语义），不是 None。"""
    session = FakeSession([_graphql_pins()])
    client = GitHubClient(token="tok", session=session)
    assert client.get_pinned_repos("alice") == []


def test_get_pinned_repos_without_token_returns_none_and_skips_http():
    """无 token 时 GraphQL 不可用：直接返回 None，一次网络请求都不发。"""
    session = FakeSession([])
    client = GitHubClient(token=None, session=session)
    assert client.get_pinned_repos("alice") is None
    assert session.requests == []


def test_get_pinned_repos_graphql_errors_returns_none():
    """GraphQL 语义错误（HTTP 200 + errors 字段）：返回 None 不抛错。"""
    session = FakeSession([FakeResponse(json_data={"errors": [{"message": "boom"}], "data": None})])
    client = GitHubClient(token="tok", session=session)
    assert client.get_pinned_repos("ghost") is None


def test_get_pinned_repos_http_failure_returns_none():
    """HTTP 层失败（如限流 403）：吞掉 GitHubError 返回 None，绝不抛错。"""
    session = FakeSession(
        [
            FakeResponse(
                status=403,
                headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1792000000"},
                text="rate limited",
            )
        ]
    )
    client = GitHubClient(token="tok", session=session)
    assert client.get_pinned_repos("alice") is None
