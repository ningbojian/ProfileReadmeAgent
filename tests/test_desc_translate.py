"""desc_translate.py：仓库描述批量英译（A-WP-B1）。

- contains_cjk 只认 Han（基本区+扩展A）：纯假名/纯 ASCII 不译（零回归裁定）
- 单批一次 chat_text 调用（全量目标一条请求），JSON map 宽松解析（仿 _parse_dual）
- 失败/解析失败 → ({}, 中文警告含「沿用原文"])，不写缓存（下轮开门自动重试）
"""

from types import SimpleNamespace

from profile_agent.config import AIConfig
from profile_agent.desc_translate import (
    _parse_desc_map,
    build_desc_translate_prompt,
    contains_cjk,
    translate_descriptions,
)


class FakeClient:
    """仿 test_short_intro：记录 create 调用并返回预设文本块。"""

    def __init__(self, blocks):
        self.calls = []
        self._blocks = blocks
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=t) for t in self._blocks]
        )


def _cfg():
    return AIConfig()


def test_contains_cjk_han_only():
    """Han 基本区与扩展A → 真；混排含汉字 → 真；纯 ASCII/纯假名 → 假（不译）。"""
    assert contains_cjk("一个很酷的工具")
    assert contains_cjk("CLI 工具")  # 混排含汉字
    assert contains_cjk("㐀生僻字")  # 扩展A
    assert not contains_cjk("A cool CLI tool")
    assert not contains_cjk("かんたんなツール")  # 纯假名不算 Han
    assert not contains_cjk("")


def test_parse_desc_map_fences_junk_and_invalid():
    """围栏/杂文宽容；非法输入 None；空值条目剔除。"""
    raw = '```json\n前置杂文 {"a/b": "English one", "c/d": ""} 后记\n```'
    got = _parse_desc_map(raw)
    assert got == {"a/b": "English one"}  # 空值剔除
    assert _parse_desc_map("完全不是 JSON") is None
    assert _parse_desc_map('{"k": 123}') is None  # 值非字符串 → 整体判非法


def test_translate_single_batch_one_call():
    """全量目标一条请求、一次调用；返回仅含请求集内且非空的译文。"""
    fake = FakeClient(['{"a/b": "Cool tool", "c/d": "Web app"}'])
    got, warn = translate_descriptions(
        [("a/b", "很酷的工具"), ("c/d", "网页应用")], _cfg(), client=fake
    )
    assert got == {"a/b": "Cool tool", "c/d": "Web app"}
    assert warn == ""
    assert len(fake.calls) == 1  # 单批单次（非逐仓循环）
    prompt = fake.calls[0]["messages"][0]["content"]
    assert "很酷的工具" in prompt and "网页应用" in prompt  # 两条描述都在同一请求里


def test_translate_exception_returns_empty_with_warning():
    """AI 异常 → ({}, 警告含「沿用原文」)，绝不抛错。"""
    class Boom:
        messages = None

        def create(self, **kwargs):
            raise RuntimeError("api down")

    got, warn = translate_descriptions([("a/b", "很酷")], _cfg(), client=Boom())
    assert got == {}
    assert "沿用原文" in warn and "a/b" in warn


def test_translate_partial_missing_keys_warns_only_missing():
    """模型漏译的仓库只回落该仓（返回缺它），警告点名缺的仓库。"""
    fake = FakeClient(['{"a/b": "Cool tool"}'])
    got, warn = translate_descriptions(
        [("a/b", "很酷"), ("c/d", "网页")], _cfg(), client=fake
    )
    assert got == {"a/b": "Cool tool"}  # c/d 缺 → 不进返回（调用方回落原文）
    assert "c/d" in warn and "沿用原文" in warn
    assert "a/b" not in warn.split("沿用原文")[0]  # 已译的不进警告（点名缺者）


def test_translate_newlines_flattened_to_single_line():
    """译文含换行 → 压平成单行（README 描述必须单行）。"""
    fake = FakeClient(['{"a/b": "Cool\\ntool  version 2"}'])
    got, _ = translate_descriptions([("a/b", "很酷")], _cfg(), client=fake)
    assert got == {"a/b": "Cool tool  version 2"}


def test_translate_empty_items_skips_ai():
    """空目标 → ({}, "")，不发起任何调用。"""
    fake = FakeClient(["{}"])
    got, warn = translate_descriptions([], _cfg(), client=fake)
    assert got == {} and warn == ""
    assert fake.calls == []


def test_prompt_requests_json_map_only():
    """提示词要求：单行自然英文、只输出 full_name→英文 的 JSON map。"""
    prompt = build_desc_translate_prompt([("a/b", "很酷的工具")])
    assert "JSON" in prompt
    assert "很酷的工具" in prompt and "a/b" in prompt


# ---- 中文方向（2026-10-24）：中文 README 里的英文描述翻成中文 ----


def test_translate_to_zh_uses_chinese_system_and_prompt():
    """target="zh"：英文描述 → 中文译文，system/prompt 均要求简体中文（不误用英译指令）。"""
    fake = FakeClient(['{"a/b": "一个很酷的工具"}'])
    got, warn = translate_descriptions(
        [("a/b", "A cool CLI tool")], _cfg(), target="zh", client=fake
    )
    assert got == {"a/b": "一个很酷的工具"} and warn == ""
    assert "简体中文" in fake.calls[0]["system"]
    prompt = fake.calls[0]["messages"][0]["content"]
    assert "A cool CLI tool" in prompt and "中文" in prompt


def test_build_prompt_zh_direction_distinct_from_en():
    """中文方向提示词与英文方向不同：一个要求英译、一个要求中译。"""
    en_prompt = build_desc_translate_prompt([("a/b", "一个很酷的工具")], target="en")
    zh_prompt = build_desc_translate_prompt([("a/b", "A cool tool")], target="zh")
    assert "翻译成自然地道的英文" in en_prompt
    assert "翻译成自然地道的简体中文" in zh_prompt


def test_translate_target_zh_failure_warning_names_direction():
    """中文方向失败：警告仍含「沿用原文」，且标明目标语言，便于日志定位。"""
    class Boom:
        messages = None

        def create(self, **kwargs):
            raise RuntimeError("api down")

    got, warn = translate_descriptions(
        [("a/b", "A cool tool")], _cfg(), target="zh", client=Boom()
    )
    assert got == {} and "沿用原文" in warn and "中文" in warn
