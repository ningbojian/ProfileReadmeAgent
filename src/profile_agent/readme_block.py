"""托管区 marker 读写（设计 §5.2）。

- 首次插入位置：文件 H1 之后（GitHub 首屏可见）；无 H1 则插在文件头部；
- 更新：仅替换 start/end 之间字节，区间外逐字节不动；
- marker 不平衡（缺一半 / 乱序 / 多对歧义）：跳过 + 中文警告，绝不猜测修复；
- 有手写 README 永不整篇重写；无 README 才走 mode=full 整份生成。
"""

from __future__ import annotations

import sys

START = "<!-- profile-agent:readme:start -->"
END = "<!-- profile-agent:readme:end -->"


def _warn_skip(reason: str) -> None:
    print(
        f"README 托管区 marker 不平衡（{reason}），已跳过该仓库，请手动检查 start/end 注释对",
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
