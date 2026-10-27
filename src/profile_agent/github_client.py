"""GitHub REST API 客户端（requests，支持分页与重试）。"""

from __future__ import annotations

import base64
import re
import time
from typing import Callable
from urllib.parse import quote

import requests

API = "https://api.github.com"
_HEADERS_JSON = {"Accept": "application/vnd.github+json", "User-Agent": "profile-agent"}
_NEXT_RE = re.compile(r'<([^>]+)>;\s*rel="next"')


class GitHubError(Exception):
    """GitHub API 错误（消息为中文）。

    status 为触发错误的 HTTP 状态码（无响应/重试耗尽时 None）——调用方据此区分
    「404 基线失效」和「网络/限流」等不同恢复策略。
    """

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class GitHubClient:
    def __init__(
        self,
        token: str | None,
        session: requests.Session | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
        max_retries: int = 2,
    ) -> None:
        self._session = session or requests.Session()
        self._headers = dict(_HEADERS_JSON)
        if token:
            self._headers["Authorization"] = f"Bearer {token}"
        self._sleep = sleep
        self._max_retries = max_retries

    def _request(
        self,
        method: str,
        url: str,
        payload: dict | None = None,
        *,
        allow_404: bool = False,
        ok_status: tuple[int, ...] = (200,),
    ) -> tuple[dict | list | None, dict]:
        """通用请求（GET/POST/PATCH…），复用同一套重试与中文错误语义。"""
        for attempt in range(self._max_retries + 1):
            resp = self._session.request(
                method, url, headers=self._headers, json=payload, timeout=30
            )
            if resp.status_code in ok_status:
                return resp.json(), resp.headers
            if resp.status_code in (403, 429) and resp.headers.get("X-RateLimit-Remaining") == "0":
                reset = resp.headers.get("X-RateLimit-Reset", "未知")
                raise GitHubError(
                    f"GitHub API 限流（速率已达上限），重置时间戳：{reset}，请稍后再试",
                    resp.status_code,
                )
            if resp.status_code == 404:
                if allow_404:
                    return None, resp.headers
                raise GitHubError(
                    f"GitHub API 返回 404：用户不存在或仓库不可见（{url.replace(API, '')}）",
                    resp.status_code,
                )
            if resp.status_code >= 500 and attempt < self._max_retries:
                self._sleep(0.5 * (attempt + 1))
                continue
            raise GitHubError(
                f"GitHub API 请求失败：HTTP {resp.status_code} {resp.text[:200]}",
                resp.status_code,
            )
        raise GitHubError("GitHub API 请求失败：重试次数已用尽")

    def _get(self, url: str, *, allow_404: bool = False) -> tuple[dict | list | None, dict]:
        for attempt in range(self._max_retries + 1):
            resp = self._session.get(url, headers=self._headers, timeout=30)
            if resp.status_code == 200:
                return resp.json(), resp.headers
            if resp.status_code in (403, 429) and resp.headers.get("X-RateLimit-Remaining") == "0":
                reset = resp.headers.get("X-RateLimit-Reset", "未知")
                raise GitHubError(
                    f"GitHub API 限流（速率已达上限），重置时间戳：{reset}，请稍后再试",
                    resp.status_code,
                )
            if resp.status_code == 404:
                if allow_404:
                    return None, resp.headers
                raise GitHubError(
                    f"GitHub API 返回 404：用户不存在或仓库不可见（{url.replace(API, '')}）",
                    resp.status_code,
                )
            if resp.status_code >= 500 and attempt < self._max_retries:
                self._sleep(0.5 * (attempt + 1))
                continue
            raise GitHubError(
                f"GitHub API 请求失败：HTTP {resp.status_code} {resp.text[:200]}",
                resp.status_code,
            )
        raise GitHubError("GitHub API 请求失败：重试次数已用尽")

    def _post(
        self,
        url: str,
        payload: dict,
        *,
        allow_404: bool = False,
        ok_status: tuple[int, ...] = (201,),
    ) -> tuple[dict | list | None, dict]:
        return self._request("POST", url, payload, allow_404=allow_404, ok_status=ok_status)

    def _patch(
        self,
        url: str,
        payload: dict,
        *,
        allow_404: bool = False,
        ok_status: tuple[int, ...] = (200,),
    ) -> tuple[dict | list | None, dict]:
        return self._request("PATCH", url, payload, allow_404=allow_404, ok_status=ok_status)

    def get_user(self, username: str) -> dict:
        data, _ = self._get(f"{API}/users/{username}")
        assert isinstance(data, dict)
        return data

    def whoami(self) -> str:
        """当前令牌所属账号（未认证/失败返回空串）。

        用途只有诊断：令牌类型看前缀、账号看这里——「明明配了令牌却全 404」
        时，两样加起来才能判断是不是拿错了令牌（别人账号 / App 安装令牌）。
        """
        return self.token_info()["login"]

    def token_info(self) -> dict:
        """诊断用：令牌所属账号 + classic 令牌的 OAuth scopes（fine-grained 不返回）。"""
        try:
            data, headers = self._get(f"{API}/user")
        except GitHubError as exc:
            return {"login": "", "scopes": "", "error": str(exc)[:120]}
        login = str(data.get("login") or "") if isinstance(data, dict) else ""
        scopes = ""
        for key, value in (headers or {}).items():
            if str(key).lower() == "x-oauth-scopes":
                scopes = str(value)
                break
        return {"login": login, "scopes": scopes}

    def list_repos(self, username: str) -> list[dict]:
        """列出用户全部公开仓库（owner 归属，按更新时间倒序，自动翻页）。"""
        url: str | None = f"{API}/users/{username}/repos?type=owner&sort=updated&per_page=100"
        repos: list[dict] = []
        while url:
            data, headers = self._get(url)
            assert isinstance(data, list)
            repos.extend(data)
            match = _NEXT_RE.search(headers.get("Link", ""))
            url = match.group(1) if match else None
        return repos

    def get_pinned_repos(self, username: str) -> list[str] | None:
        """读取用户个人主页原生 Pinned 仓库（GraphQL，官方顺序，最多 6 个）。

        返回 nameWithOwner 列表；用户清空置顶时返回 []（成功语义）。
        失败一律返回 None（无 token / 网络异常 / GraphQL 语义错误），绝不抛错——
        调用方据此回退快照或配置，避免置顶闪断。REST 无此接口，仅 GraphQL 提供。
        """
        if "Authorization" not in self._headers:
            return None  # GraphQL 必须认证，匿名直接放弃（零网络开销）
        query = (
            "query($login: String!) { user(login: $login) { "
            "pinnedItems(first: 6, types: REPOSITORY) { "
            "nodes { ... on Repository { nameWithOwner } } } } }"
        )
        try:
            data, _ = self._request(
                "POST",
                f"{API}/graphql",
                {"query": query, "variables": {"login": username}},
                ok_status=(200,),
            )
        except GitHubError:
            return None
        if not isinstance(data, dict) or data.get("errors"):
            return None
        user = (data.get("data") or {}).get("user")
        if not isinstance(user, dict):
            return None
        nodes = ((user.get("pinnedItems") or {}).get("nodes")) or []
        return [n["nameWithOwner"] for n in nodes if isinstance(n, dict) and n.get("nameWithOwner")]

    def get_repo_readme(
        self, full_name: str, ref: str | None = None, *, path: str = "README.md"
    ) -> str:
        """读取仓库 README 文本；无 README 或读取失败时返回空串（不报错）。

        ref 指定分支/提交（§5.6 规则 2：open PR 仓从 readme-auto 分支读，防回退旧文）。
        path 指定文件（WP7：README.en.md 走 /contents/{path}，无 /readme 等价端点）。
        """
        if path == "README.md":
            url = f"{API}/repos/{full_name}/readme"
        else:
            url = f"{API}/repos/{full_name}/contents/{quote(path, safe='')}"
        url += f"?ref={ref}" if ref else ""
        try:
            data, _ = self._get(url, allow_404=True)
        except GitHubError:
            return ""
        if not isinstance(data, dict):
            return ""
        content = str(data.get("content") or "")
        if data.get("encoding") != "base64" or not content:
            return ""
        try:
            return base64.b64decode(content).decode("utf-8", errors="replace")
        except (ValueError, OSError):
            return ""
