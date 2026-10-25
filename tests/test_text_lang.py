"""text_lang.py：语言判定纯函数（模式 A/B 警告与描述写回共用）。

设计口径（2026-10-25）：
- 只认汉字（基本区 + 扩展A）与拉丁字母；假名/谚文不算中文；
- 代码围栏整体剔除后再计数，避免代码仓库/命令示例误报；
- 短文本（Han+Latin < min_letters）不判「英文写成中文」；
- 有意变更（阈值 0.20 → 0.50）：汉字占比超过一半才算「这看起来是中文」——
  0.20 会把 "A CLI tool 用来生成密码"（约 0.35–0.43）这类中英混排误报；
  纯中文正文仍接近 1.0，不受影响。
"""

from profile_agent.text_lang import (
    contains_han,
    dominant_language,
    han_ratio,
    language_mismatch,
)


def test_contains_han_only_counts_han():
    assert contains_han("你好，世界") is True
    assert contains_han("hello world") is False
    assert contains_han("ひらがなカタカナ") is False  # 假名不是汉字
    assert contains_han("한국어") is False  # 谚文不是汉字
    assert contains_han("日本語") is True  # 日文汉字仍是 Han
    assert contains_han("") is False


def test_han_ratio_ignores_code_fences():
    assert han_ratio("```\n中文中文中文\n```\nhello") == 0.0
    # 2 个汉字 / (2 汉字 + 5 拉丁) ≈ 0.2857
    assert abs(han_ratio("你好hello") - 2 / 7) < 1e-9
    assert han_ratio("") == 0.0
    assert han_ratio("```\n未闭合围栏里的中文") == 0.0  # 未闭合围栏同样剔除


def test_dominant_language_returns_zh_en_or_empty():
    assert dominant_language("你好，世界") == "zh"
    assert dominant_language("hello world") == "en"
    assert dominant_language("") == ""
    assert dominant_language("```\n中文\n```") == ""
    assert dominant_language("你好hello world") == "en"  # 拉丁更多
    assert dominant_language("你好世界hello") == "zh"  # 汉字更多


def test_language_mismatch_english_target():
    assert language_mismatch("This README is written in English.", "en") is False
    assert language_mismatch("这是一段完全用中文写的内容。", "en") is True
    # 中英混排但汉字占比未过阈值 → 不报警
    assert language_mismatch("A CLI tool 用来生成密码", "en") is False
    # 代码围栏里的中文不算正文
    assert language_mismatch("```\n这是代码注释里的中文内容\n```\nEnglish only", "en") is False


def test_language_mismatch_chinese_target():
    assert language_mismatch("这是一段完全用中文写的内容。", "zh") is False
    assert language_mismatch("This README is written in English only.", "zh") is True
    # 短文本不误报（CLI / README 这类词不算「英文正文」）
    assert language_mismatch("CLI", "zh") is False
    assert language_mismatch("README", "zh") is False
    # 极短中文也不误报
    assert language_mismatch("你好", "zh") is False


def test_language_mismatch_custom_min_letters():
    text = "Short English note"  # 16 个拉丁字母
    assert language_mismatch(text, "zh") is False  # 默认 20 下限：短文本不误报
    assert language_mismatch(text, "zh", min_letters=10) is True  # 收紧下限才判


def test_language_mismatch_unknown_target_is_false():
    assert language_mismatch("这是一段中文", "fr") is False


def test_language_mismatch_ignores_urls_and_inline_code():
    """中文手写区里的链接与行内代码不算拉丁字母，不应误报「看起来是英文」。"""
    text = (
        "我做了很多工具，详情见 https://github.com/alice/cool-tool ，"
        "安装命令是 `pip install cool-tool` ，欢迎试用。"
    )
    assert language_mismatch(text, "zh") is False
    assert han_ratio(text) > 0.9  # URL 与行内代码整体剔除
