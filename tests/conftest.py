"""测试全局挡板：默认挡住真实仓库描述英译（A-WP-B）。

带 ANTHROPIC_API_KEY 的既有 AI 测试（bio/补位等）不该因新增的 step 4.5
真连网——默认替换成零调用 no-op；翻译专项测试用 monkeypatch.setattr
自行覆盖为记录型 fake（后设置者生效，fixture 撤销互不干扰）。
"""

import pytest


@pytest.fixture(autouse=True)
def _no_real_desc_translation(monkeypatch):
    import profile_agent.cli as cli_mod

    monkeypatch.setattr(cli_mod, "translate_descriptions", lambda *a, **k: ({}, ""))
