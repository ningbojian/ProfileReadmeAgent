"""托管区 marker 读写（设计 §5.2）。

- 首次插入位置：文件 H1 之后（GitHub 首屏可见）；无 H1 则插在文件头部；
- 更新：仅替换 start/end 之间字节，区间外逐字节不动；
- marker 不平衡（缺一半 / 乱序 / 多对歧义）：跳过 + 中文警告，绝不猜测修复；
- 有手写 README 永不整篇重写；无 README 才走 mode=full 整份生成。
"""

from __future__ import annotations

import re
import sys

START = "<!-- profile-agent:readme:start -->"
END = "<!-- profile-agent:readme:end -->"


def _warn_skip(reason: str) -> None:
    print(
        f"README 托管区 marker 不平衡（{reason}），已跳过，请手动检查 start/end 注释对",
        file=sys.stderr,
    )


def _sanitize(text: str) -> str:
    """AI 正文里混入的 marker 字面清掉，保证写出后恒为恰一对平衡 marker（MK18）。"""
    return text.replace(START, "").replace(END, "")


def _insert(readme: str, text: str) -> str:
    """无 marker 时首插：H1 之后；无 H1 则文件头（设计 §5.2）。

    H1 行无尾换行时补单个换行使 marker 独占行（测试计划裁定 R-T2 的插入路径例外）；
    替换路径不受影响，仍严格逐字节。
    """
    block = f"{START}\n{text}\n{END}\n"
    lines = readme.splitlines(keepends=True)
    for idx, line in enumerate(lines):
        if line.startswith("# "):
            head = "".join(lines[: idx + 1])
            rest = "".join(lines[idx + 1 :])
            if not head.endswith("\n"):
                head += "\n"
            return head + block + rest
    return block + readme


def markers_balanced(readme: str) -> bool:
    """README 的 marker 是否恰好一对且顺序正确（§5.2）。

    0 对 = 托管区尚未建立（合法，走首插路径）；恰一对且 start 在前 = 正常；
    其余（缺一半 / 乱序 / 多对歧义）= 不平衡，调用方应在写回前跳过该仓库。
    """
    n_start, n_end = readme.count(START), readme.count(END)
    if n_start == 0 and n_end == 0:
        return True
    if n_start == 1 and n_end == 1:
        return readme.index(END) > readme.index(START)
    return False


def outside_block(readme: str) -> str:
    """取托管区之外的手写内容（语言提醒用，绝不据此改写）。

    无 marker → 全文都是手写；marker 不平衡 → 从第一个 start 剥到文末
    （半截托管区不是用户手写的，不能拿它误报语言）。
    """
    text = readme or ""
    i = text.find(START)
    if i >= 0:
        j = text.find(END, i + len(START))
        text = text[:i] + (text[j + len(END) :] if j >= 0 else "")
    # 乱序残留（end 在 start 之前）或后续重复 marker 一并清掉
    return text.replace(START, "").replace(END, "")


def apply_block(readme: str, new_text: str) -> str | None:
    """把托管区正文写进 README；marker 不平衡时返回 None（跳过信号）并打中文警告。"""
    text = _sanitize(new_text)
    n_start, n_end = readme.count(START), readme.count(END)
    if n_start == 0 and n_end == 0:
        return _insert(readme, text)
    if n_start == 1 and n_end == 1:
        i, j = readme.index(START), readme.index(END)
        if j < i:
            _warn_skip("end 在 start 之前")
            return None
        return readme[:i] + f"{START}\n{text}\n{END}" + readme[j + len(END) :]
    _warn_skip(f"出现 {n_start} 个 start / {n_end} 个 end，歧义")
    return None


def block_body(readme: str) -> str | None:
    """取托管区正文（不含 marker）；未建立/不平衡返回 None。"""
    if not markers_balanced(readme) or START not in readme:
        return None
    return readme.split(START, 1)[1].split(END, 1)[0].strip("\n")


def _first_title_index(lines: list[str]) -> int | None:
    """第一个「真实标题」的行下标（在其后插入）；没有返回 None。

    真实 = 不在代码围栏 / 跨行 HTML 注释 / <details> 里。支持 Markdown `# `、
    一行或跨行的 HTML `<h1>`、以及 setext 的 `标题\n====`。缩进标题（列表里的
    `#`）跳过——那不是页面标题。
    """
    in_fence = False
    fence = ""
    in_comment = False
    details = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if in_comment:
            if "-->" in stripped:
                in_comment = False
            continue
        if in_fence:
            if stripped.startswith(fence):
                in_fence = False
            continue
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = True
            fence = stripped[:3]
            continue
        if stripped.startswith("<!--"):
            if "-->" not in stripped:
                in_comment = True
            continue
        lower = stripped.lower()
        if lower.startswith("<details"):
            details += 1
            continue
        if lower.startswith("</details"):
            details = max(0, details - 1)
            continue
        if details:
            continue
        if line.startswith("# "):
            return i
        if "<h1" in lower:
            if "</h1>" in lower:
                return i
            for k in range(i + 1, len(lines)):
                if "</h1>" in lines[k].lower():
                    return k
            return i
        # setext H1：标题行 + 下一行全是 =
        if (
            stripped
            and i + 1 < len(lines)
            and re.fullmatch(r"=+\s*", lines[i + 1].strip())
        ):
            return i + 1
    return None


def upsert_block(readme: str, body: str) -> str | None:
    """插入或更新托管块；marker 不平衡返回 None（跳过，绝不猜修复）。

    已有平衡 marker → 只替换区间内正文；没有 → 插在第一个真实标题之后，
    没有标题则追加到文末（绝不插文件头——那会破坏 front matter）。
    """
    if not markers_balanced(readme):
        _warn_skip("start/end 不配对或顺序颠倒")
        return None
    text = _sanitize(body).strip("\n")
    if START in readme:
        return apply_block(readme, text)
    block = f"{START}\n{text}\n{END}\n"
    lines = readme.splitlines(keepends=True)
    idx = _first_title_index(lines)
    if idx is None:
        if not readme:
            return block
        prefix = readme if readme.endswith("\n") else readme + "\n"
        return prefix + "\n" + block
    head = "".join(lines[: idx + 1])
    if not head.endswith("\n"):
        head += "\n"
    rest = "".join(lines[idx + 1 :])
    if rest and not rest.startswith("\n"):
        rest = "\n" + rest
    return head + "\n" + block + rest


def strip_block(text: str) -> str:
    """剥掉一对平衡的托管块（auto → full 迁移用，防简介重复）。

    不平衡或没有 marker 时原样返回——半截块不动，交给 full 路径的 broken 跳过。
    """
    if not markers_balanced(text) or START not in text:
        return text
    i = text.index(START)
    j = text.index(END) + len(END)
    before = text[:i].rstrip("\n")
    after = text[j:].lstrip("\n")
    if not before:
        return after
    if not after:
        return before + "\n"
    return before + "\n\n" + after


def merge_readme(existing: str, ai_text: str) -> tuple[str, str]:
    """按 README 存在性选写回路径；返回 (新内容, mode)。

    - 不存在/空 README → mode=full 整份生成（不强加 marker）；
    - 已有 README → 只替换 marker 区间，mode=block；不平衡时原样返回（调用方隔离跳过）。
    """
    if not existing:
        return ai_text, "full"
    out = apply_block(existing, ai_text)
    if out is None:
        return existing, "block"
    return out, "block"
