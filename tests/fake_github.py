"""共享假 HTTP 基建（测试计划 §1.4）：按 (method, url) 模式注册响应，记录完整请求。

- 每条路由一个响应队列；sticky=True 时队列耗尽后持续返回最后一条（轮询类接口用）；
- 记录 {method, url, headers, json}——权限分离（IW27）、载荷断言（GP01）都靠它。
"""

from __future__ import annotations


class FakeResponse:
    def __init__(self, status=200, json_data=None, headers=None, text=""):
        self.status_code = status
        self._json = json_data
        self.headers = headers or {}
        self.text = text

    def json(self):
        return self._json


class FakeHttp:
    """假 requests.Session：GET/POST/PATCH/DELETE 全覆盖，未注册请求直接 AssertionError。"""

    def __init__(self):
        self.routes: dict[tuple[str, str], list] = {}
        self.sticky: set[tuple[str, str]] = set()
        self.requests: list[dict] = []

    def add(self, method: str, url: str, *responses: FakeResponse, sticky: bool = False):
        key = (method.upper(), url)
        self.routes.setdefault(key, []).extend(responses)
        if sticky:
            self.sticky.add(key)
        return self

    def _handle(self, method: str, url: str, headers: dict | None, body) -> FakeResponse:
        self.requests.append(
            {"method": method.upper(), "url": url, "headers": headers or {}, "json": body}
        )
        key = (method.upper(), url)
        queue = self.routes.get(key)
        if not queue:
            raise AssertionError(f"未注册的请求：{method.upper()} {url}")
        resp = queue.pop(0)
        if not queue and key in self.sticky:
            queue.append(resp)  # 末条粘滞：轮询持续返回同一状态
        return resp

    def request(self, method, url, headers=None, json=None, timeout=None):
        """GitHubClient._request 的入口（GET/POST/PATCH… 统一走这里）。"""
        return self._handle(method, url, headers, json)

    def get(self, url, headers=None, timeout=None):
        return self._handle("GET", url, headers, None)

    def post(self, url, headers=None, json=None, timeout=None):
        return self._handle("POST", url, headers, json)

    def patch(self, url, headers=None, json=None, timeout=None):
        return self._handle("PATCH", url, headers, json)

    def delete(self, url, headers=None, timeout=None):
        return self._handle("DELETE", url, headers, None)

    # ---- 断言辅助 ----

    def writes(self) -> list[dict]:
        """全部非 GET 请求（写操作）。"""
        return [r for r in self.requests if r["method"] != "GET"]

    def write_sequence(self) -> list[tuple[str, str]]:
        return [(r["method"], r["url"]) for r in self.writes()]

    def has(self, method: str, url_sub: str) -> bool:
        return any(r["method"] == method.upper() and url_sub in r["url"] for r in self.requests)
