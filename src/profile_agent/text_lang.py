"""语言判定纯函数（模式 A/B 警告与描述写回共用）。

口径（2026-10-25）：
- 只认汉字（基本区 + 扩展A）与拉丁字母；假名/谚文不算中文；
- 代码围栏整体剔除后再计数，避免代码仓库/命令示例误报；
- URL 与行内代码同样剔除：中文手写区里的链接、`pip install ...` 不该被算成英文；
- ``dominant_language`` 按「汉字个数 vs 英文单词数」比较，平局归 en；
- ``han_ratio`` 按字符数计算（汉字 /（汉字 + 拉丁字母））；
- ``language_mismatch`` 默认 0.50 阈值：中英混排（如 "A CLI tool 用来生成密码"）
  的汉字占比通常低于一半，不应误报为「整段中文」；纯中文正文仍接近 1.0。
  **AI 输出校验请显式传 ``threshold=0.20``**，成段中文直接丢弃。
"""

from __future__ import annotations

import re

# Han 基本区（一-鿿）+ 扩展A（㐀-䶿）；不含假名/谚文
_HAN_RE = re.compile("[一-鿿㐀-䶿]")
_LATIN_RE = re.compile("[A-Za-z]")
_LATIN_WORD_RE = re.compile("[A-Za-z]+")
# 代码围栏（含未闭合围栏）：整段剔除，围栏里的中文不算正文
_FENCE_RE = re.compile(r"```.*?(?:```|\Z)", re.DOTALL)
# URL 与行内代码：链接/命令里的拉丁字母不算正文（中文手写区常带这两样）
_URL_RE = re.compile(r"https?://\S+")
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")


def _prose(text: str) -> str:
    """去掉代码围栏、URL 与行内代码后的正文（未闭合围栏同样剔除到文末）。"""
    prose = _FENCE_RE.sub("", text or "")
    prose = _INLINE_CODE_RE.sub("", prose)
    return _URL_RE.sub("", prose)


def contains_han(text: str) -> bool:
    """是否含汉字（基本区或扩展A）；纯 ASCII/假名/谚文返回 False。"""
    return bool(text) and bool(_HAN_RE.search(text))


def han_ratio(text: str) -> float:
    """汉字占「汉字 + 拉丁字母」的比例（忽略代码围栏）；无可计字符返回 0.0。"""
    prose = _prose(text)
    han = len(_HAN_RE.findall(prose))
    latin = len(_LATIN_RE.findall(prose))
    total = han + latin
    return han / total if total else 0.0


def dominant_language(text: str) -> str:
    """主导语言："zh" | "en" | ""（按汉字个数与英文单词数比较，平局归 en）。"""
    prose = _prose(text)
    han = len(_HAN_RE.findall(prose))
    latin_words = len(_LATIN_WORD_RE.findall(prose))
    if not han and not latin_words:
        return ""
    return "zh" if han > latin_words else "en"


def language_mismatch(
    text: str,
    target: str,
    *,
    min_letters: int = 20,
    threshold: float = 0.50,
) -> bool:
    """文本是否明显不是目标语言（只用于提醒，绝不据此翻译或改写）。

    - target="en"：汉字占比 > threshold → True；
    - target="zh"：汉字占比 ≤ threshold 且 Han+Latin ≥ min_letters → True
      （短词如 "CLI"/"README" 不误报）；
    - 未知 target → False。
    """
    if target not in ("zh", "en"):
        return False
    ratio = han_ratio(text)
    if target == "en":
        return ratio > threshold
    prose = _prose(text)
    total = len(_HAN_RE.findall(prose)) + len(_LATIN_RE.findall(prose))
    return ratio <= threshold and total >= min_letters
