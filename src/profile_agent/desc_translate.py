"""仓库描述批量本地化（A-WP-B1，2026-10-24 扩展双向）。

- 英文方向：中文 README 之外，en 文件里含中文的 description 换成 AI 英译；
- 中文方向（2026-10-24）：zh 文件里不含汉字的 description 换成 AI 中译——
  中英两版各自只看自己语言，用户切到哪版看到的描述就是哪一版的语言。
- contains_cjk 只认 Han（基本区 + 扩展A）：纯假名/纯 ASCII 在英文方向不译，
  在中文方向则视为「非中文」需要中译（零回归裁定见测试）
- 全量目标一次 chat_text 调用（单批单请求），JSON map 宽松解析（仿 bio._parse_dual）
- 失败/解析失败 → ({}, 中文警告含「沿用原文"])：调用方回落原文且不写缓存，下轮开门自动重试
"""

from __future__ import annotations

import json
import re

from .bio import chat_text
from .config import AIConfig
from .text_lang import contains_han

# 两个翻译方向各自的 system / 动词 / 目标语言名（提示词与警告文案同源，避免漂移）
_DIRECTIONS = {
    "en": {
        "system": (
            "你是一位技术翻译助手，负责把 GitHub 仓库的中文简介翻译成自然地道的英文。"
            "只输出 JSON，不要代码块围栏或任何解释。"
        ),
        "verb": "翻译成自然地道的英文",
        "target_name": "英文",
    },
    "zh": {
        "system": (
            "你是一位技术翻译助手，负责把 GitHub 仓库的英文简介翻译成自然地道的简体中文。"
            "只输出 JSON，不要代码块围栏或任何解释。"
        ),
        "verb": "翻译成自然地道的简体中文",
        "target_name": "中文",
    },
}


def contains_cjk(text: str) -> bool:
    """描述是否含汉字（基本区或扩展A）；纯 ASCII/假名等返回 False。

    薄包装 ``text_lang.contains_han``：语言判定口径集中一处，避免两套正则漂移。
    """
    return contains_han(text)


def _parse_desc_map(raw: str) -> dict[str, str] | None:
    """从模型输出提取 {full_name: 英文译文}；非法 → None（调用方降级原文）。

    宽松解析：容忍 ```json 围栏与前后杂文，取首个 { 到末个 } 之间的片段
    （同 bio._parse_dual 的宽容口径）；值须为非空字符串，否则剔除。
    """
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
        text = text.strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    out: dict[str, str] = {}
    for k, v in data.items():
        if not isinstance(v, str):
            return None  # 值类型不对 = 整体不合法（防误把非 map 当结果）
        v = v.strip()
        if v:
            out[str(k).strip()] = v  # 空值剔除（模型偶发给空串 → 视为漏译回落原文）
    return out


def build_desc_translate_prompt(items: list[tuple[str, str]], target: str = "en") -> str:
    """批量翻译提示词：items=[(full_name, 描述)]；target 为目标语言（en/zh），只输出 JSON map。"""
    direction = _DIRECTIONS.get(target) or _DIRECTIONS["en"]
    payload = [{"full_name": fn, "description": desc} for fn, desc in items]
    return (
        f"把下面每个仓库的简介{direction['verb']}，用作 GitHub 个人主页的仓库描述。\n"
        "要求：忠实原意、不扩写、每条压成单行（不许换行）、不要引号包裹整句；"
        "保留必要的专有名词与命令原文。\n"
        "只输出一个 JSON 对象，形如：\n"
        '{"full_name": "Translated description"}\n'
        "每个仓库都要有条目，键必须与输入的 full_name 完全一致；"
        "不要输出 JSON 之外的任何文字或代码块围栏。\n\n"
        f"仓库列表：\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n"
    )


def translate_descriptions(
    items: list[tuple[str, str]],
    cfg: AIConfig,
    *,
    target: str = "en",
    client=None,
    http_post=None,
) -> tuple[dict[str, str], str]:
    """批量翻译（单批一次调用）。target=en 中→英，target=zh 英→中。返回 (译文 map, 警告)。

    - 空 items → ({}, "")，不发起调用
    - AI 异常/解析失败 → ({}, 中文警告含「沿用原文"])：不写缓存，下轮自动重试
    - 模型漏译的仓库不进返回（调用方回落原文），警告点名缺失者
    - 译文压成单行（\\n → 空格）——README 描述必须单行
    """
    if not items:
        return {}, ""
    direction = _DIRECTIONS.get(target) or _DIRECTIONS["en"]
    target_name = direction["target_name"]
    names = [fn for fn, _ in items]
    try:
        raw = chat_text(
            cfg,
            direction["system"],
            build_desc_translate_prompt(items, target),
            client=client,
            http_post=http_post,
        )
        parsed = _parse_desc_map(raw)
    except Exception as exc:  # AI 层任何失败都不影响任务（回落原文）
        msg = str(exc)[:120]
        return {}, (
            f"仓库描述翻译（目标语言：{target_name}）失败（沿用原文）：{msg}；"
            f"涉及 {len(items)} 个仓库"
            f"（{', '.join(names[:5])}{' 等' if len(names) > 5 else ''}）"
        )
    if parsed is None:
        return {}, (
            f"仓库描述翻译（目标语言：{target_name}）输出无法解析（沿用原文）；"
            f"涉及 {len(items)} 个仓库"
        )
    out: dict[str, str] = {}
    for fn in names:
        translated = parsed.get(fn)
        if translated:
            out[fn] = re.sub(r"\s*\n\s*", " ", translated).strip()
    missing = [fn for fn in names if fn not in out]
    warn = ""
    if missing:
        warn = (
            f"警告：{len(missing)} 个仓库的描述翻译缺失"
            f"（目标语言：{target_name}；沿用原文）："
            f"{', '.join(missing[:5])}{' 等' if len(missing) > 5 else ''}"
        )
    return out, warn
