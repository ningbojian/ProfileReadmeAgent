"""AI 简介：提示词构建与 Claude 调用（含复用与回退）。"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from . import ai_failures
from .config import AIConfig
from .text_lang import language_mismatch

_SYSTEM = (
    "你是一位技术写作助手，为 GitHub 个人主页撰写中文简介。"
    "只输出简介正文，不要 markdown 标题、代码块或任何解释。"
)


def chat_text(
    cfg: AIConfig,
    system: str,
    user_content: str,
    *,
    client=None,
    http_post=None,
) -> str:
    """按 provider 调用模型，返回原始回复文本。

    - anthropic：官方 SDK（默认）
    - openai：OpenAI 兼容 /chat/completions（DeepSeek/千问/Kimi/MiMo/Ollama 等）
    """
    if cfg.provider == "openai":
        import os

        import requests

        post = http_post or requests.post
        key = os.environ.get(cfg.key_env_name) or ""
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        headers.update(cfg.headers)
        try:
            resp = post(
                f"{cfg.base_url}/chat/completions",
                headers=headers,
                json={
                    "model": cfg.model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user_content},
                    ],
                },
                timeout=180,
            )
            status = getattr(resp, "status_code", None)
            if status and int(status) >= 400:
                # requests 的 raise_for_status 只给「401 Client Error」，服务商的原始
                # 原因（如 'Go requires a credential'）在响应体里，必须带出来才排得动错
                err = RuntimeError(
                    f"HTTP {status}: {str(getattr(resp, 'text', '') or '')[:400]}"
                )
                err.status_code = int(status)  # 让 ai_failures 显示真实状态码
                raise err
            resp.raise_for_status()
        except Exception as exc:  # 记录后原样抛出：上层继续沿用缓存
            ai_failures.record(exc, cfg.provider, cfg.model)
            raise
        return str(resp.json()["choices"][0]["message"]["content"] or "")

    if client is None:
        import os

        import anthropic

        # base_url/key_env 可指向 Anthropic 兼容端点（如 MiMo 的 /anthropic 接口）
        kwargs = {}
        if cfg.base_url:
            kwargs["base_url"] = cfg.base_url
        key = os.environ.get(cfg.key_env_name)
        if key:
            kwargs["api_key"] = key
        if cfg.headers:
            # OpenCode Go 等端点要求额外请求头（如 x-opencode-session），交给 SDK 统一带上
            kwargs["default_headers"] = dict(cfg.headers)
        client = anthropic.Anthropic(**kwargs)
    try:
        message = client.messages.create(
            model=cfg.model,
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": user_content}],
        )
    except Exception as exc:  # 记录后原样抛出：上层继续沿用缓存
        ai_failures.record(exc, cfg.provider, cfg.model)
        raise
    return extract_text(message.content)


class BioError(Exception):
    """简介生成失败（消息为中文）。"""


def build_prompt(payload: dict, cfg: AIConfig, bilingual: bool = True) -> str:
    base = (
        f"请为下面这位开发者写一段 GitHub 个人主页简介，使用简体中文。\n"
        f"简介长度在 {cfg.bio_min_chars} 到 {cfg.bio_max_chars} 字之间，"
        f"语气自然、专业，突出技术方向与代表项目，不要夸大。\n"
        f"只写一段连续文字，不要分点、不要列表、不要附加小节"
        f"（项目信息由主页的置顶项目与仓库列表展示）。\n\n"
        f"个人资料：\n{json.dumps({k: payload.get(k) for k in ('name', 'blog', 'bio', 'totals')}, ensure_ascii=False, indent=2)}\n\n"
        f"代表项目：\n{json.dumps(payload.get('repos', []), ensure_ascii=False, indent=2)}\n"
    )
    if bilingual:
        # WP4 双语：单次调用输出 JSON {zh,en}（≈1.3× 成本而非 2×）；
        # 解析失败由 generate_bio 降级单语（zh=清洗后原文，en 置空）
        base += (
            "另外，为同一段简介提供自然地道的英文版。"
            "请只输出一个 JSON 对象，形如：\n"
            '{"zh": "中文简介全文", "en": "English bio"}\n'
            "不要输出 JSON 之外的任何文字或代码块围栏。\n"
        )
    return base


def _parse_dual(raw: str) -> tuple[str, str] | None:
    """从模型输出提取 {"zh":…,"en":…}；不是合法 JSON dict → None（调用方降级单语）。

    宽松解析：容忍 ```json 围栏与前后杂文，取首个 { 到末个 } 之间的片段。
    （short_intro 与后续 WP7 仓库级双语共用此函数。）
    """
    text = raw.strip()
    if text.startswith("```"):
        # 剥掉代码块围栏（首行 ```lang，尾行 ```）
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
    return str(data.get("zh") or "").strip(), str(data.get("en") or "").strip()


def extract_text(content) -> str:
    """从真实 SDK 的 content blocks 中提取文本（忽略 thinking 等块）。

    每个 text 块单独去空白与包裹引号，块之间换行拼接。
    """
    if isinstance(content, str):
        return content.strip().strip('「」"\'')
    parts = [
        getattr(block, "text", "").strip().strip('「」"\'')
        for block in (content or [])
        if getattr(block, "type", None) == "text"
    ]
    return "\n".join(p for p in parts if p)


def _is_fresh(prev_generated_at: str, max_age_days: int) -> bool:
    if not prev_generated_at:
        return False
    try:
        generated = datetime.fromisoformat(prev_generated_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - generated
    return age.total_seconds() < max_age_days * 86400


def generate_bio(
    payload: dict,
    cfg: AIConfig,
    *,
    input_hash: str,
    prev_hash: str,
    prev_text: str,
    prev_generated_at: str,
    prev_text_en: str = "",
    bilingual: bool = True,
    client=None,
    http_post=None,
) -> tuple[str, str, bool]:
    """生成简介。返回 (zh, en, generated)。

    - 输入未变且未过期：复用旧简介（含 en），generated=False
    - 调用失败/返回空：回退旧简介（若有），generated=False；否则抛 BioError
    - bilingual=True：单次调用 JSON {zh,en}；解析失败降级单语（zh=清洗后原文，en=""）
    - generated=True 时调用方才能把 input_hash 记为当前值
    """
    if prev_text and input_hash == prev_hash and _is_fresh(prev_generated_at, cfg.max_age_days):
        return prev_text, prev_text_en, False

    try:
        raw = chat_text(
            cfg,
            _SYSTEM,
            build_prompt(payload, cfg, bilingual=bilingual),
            client=client,
            http_post=http_post,
        )
    except Exception as exc:
        if prev_text:
            return prev_text, prev_text_en, False
        raise BioError(f"调用模型生成简介失败（{cfg.provider}）：{exc}") from exc

    cleaned = raw.strip().strip('「」"\'')
    if bilingual:
        dual = _parse_dual(raw)
        if dual is None:
            text, text_en = cleaned, ""  # 降级单语：宁可单语不可失败
        else:
            text, text_en = dual
    else:
        text, text_en = cleaned, ""
    if not text:
        if prev_text:
            return prev_text, prev_text_en, False
        raise BioError("模型返回了空简介")
    # 输出校验（2026-10-25）：英文简介成段写成中文 → 丢弃 en，宁可单语也不写混杂内容。
    # AI 输出用更严的 0.20 阈值（手写警告仍用默认 0.50）。
    # 同一输入只试一次：cli 会把当前 input_hash 记进 en_dropped_hash，
    # 数据变化 / 改 ai.prompt_version / 简介过期后才会重新尝试。
    if text_en and language_mismatch(text_en, "en", threshold=0.20):
        print(
            "警告：AI 返回的英文版简介看起来是中文，已丢弃英文简介"
            "（数据变化、改 ai.prompt_version 或简介过期后才会重新尝试）",
            file=sys.stderr,
        )
        text_en = ""
    return text, text_en, True
