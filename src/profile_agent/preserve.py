"""Overview README 内容保留（WP5）：custom 标记区提取 + 生成区行级三路合并。

用户原话约束：「Overview 也需要结合历史版本来看，不能整文件覆盖」——
生成区随数据更新，但用户的手工微调保留、删除会被恢复（防抖动），
custom 标记区与底部面板的内容永远保留。
"""

from __future__ import annotations

import difflib

CUSTOM_START = "<!-- profile-agent:custom:start -->"
CUSTOM_END = "<!-- profile-agent:custom:end -->"

# 自动头启发式：首行是 HTML 注释且含 profile-agent（双条件，缺一不算机器文件）
_HEADER_MARK = "profile-agent"


def extract_custom(existing: str) -> tuple[str, str]:
    """从盘上 README 提取 custom 区内容。返回 (内容, mode)。

    mode ∈:
    - preserved：锚点平衡（start/end 各恰一次且有序）→ 取锚点之间内容
    - take_over：无头无锚点的手写文件 → 整份成为 custom（首次接管，不丢内容）
    - machine：空串/不存在，或有头无锚点 → custom 空
    - broken：有锚点但不平衡 → custom 空（防自我复制；调用方打中文警告）
    """
    if not existing:
        return "", "machine"  # 空/不存在特判：不落 take_over（日志语义正确）
    start_count = existing.count(CUSTOM_START)
    end_count = existing.count(CUSTOM_END)
    if start_count or end_count:
        if (
            start_count == 1
            and end_count == 1
            and existing.index(CUSTOM_START) < existing.index(CUSTOM_END)
        ):
            body = existing.split(CUSTOM_START, 1)[1].split(CUSTOM_END, 1)[0]
            return body.strip("\n"), "preserved"
        return "", "broken"
    first_line = existing.lstrip().split("\n", 1)[0]
    if first_line.lstrip().startswith("<!--") and _HEADER_MARK in first_line:
        return "", "machine"  # 有头无锚点：全机器生成，custom 空
    return existing, "take_over"  # 手写文件首次接管：整份进 custom


def merge_readme_3way(base: str, theirs: str, ours: str) -> str:
    """生成区行级三路合并（纯函数、difflib）。

    base=上次渲染输出（非合并写入——含手改的 base 会让下轮把手改误当已拥有而抹掉），
    theirs=盘上内容（可能含用户手改），ours=本轮新渲染。

    规则（设计锁定）：
    1. theirs 相对 base 修改/插入、ours 未动该处 → 保留 theirs（用户手改）
    2. ours 与 theirs 都动过同一处 → ours（数据为真）
    3. theirs 删除而 ours 保留 → 恢复 ours（生成区删除无效，防抖动；想隐藏用 config）。
       含删除成分的「缩行替换」同样按删除处理——SequenceMatcher 常把整块删除
       对齐成缩行 replace 而非 delete，不拦会被规则 1 误收（面板锚点块回归实测）
    4. 其余差异 → ours
    跨越再生成块的用户编辑整段丢弃（保守取 ours，罕见情形不值得冒险）。
    """
    if not base:
        return ours  # 无 base：以新渲染为准（升级首跑由调用方兜底后仍防御一次）
    b = base.splitlines(keepends=True)
    t = theirs.splitlines(keepends=True)
    o = ours.splitlines(keepends=True)
    ops_t = difflib.SequenceMatcher(None, b, t, autojunk=False).get_opcodes()
    ops_o = difflib.SequenceMatcher(None, b, o, autojunk=False).get_opcodes()

    # base 行 → ours 行的一一对应（仅 o-op equal 段成立；None=ours 已改/删）
    o_match: list[int | None] = [None] * len(b)
    # ours 动过的 base 区间（闭区间；插入为边界零宽点）
    o_spans: list[tuple[int, int]] = []
    for tag, i1, i2, j1, _j2 in ops_o:
        if tag == "equal":
            for k in range(i1, i2):
                o_match[k] = j1 + (k - i1)
        elif i1 == i2:
            o_spans.append((i1, i1))
        else:
            o_spans.append((i1, i2 - 1))

    def anchor(start: int) -> int:
        """base 位置 start 在 ours 中的插入锚点（其后第一个仍匹配的行；越界取行尾）。"""
        for k in range(start, len(b)):
            if o_match[k] is not None:
                return o_match[k]
        return len(o)

    def conflicts(i1: int, i2: int) -> bool:
        """theirs 编辑区间（含两端边界，保守纳邻）与 ours 改动是否相撞。"""
        lo, hi = i1, (i1 if i1 == i2 else i2)
        return any(lo <= h and l <= hi for l, h in o_spans)

    # 收集可应用的用户编辑：replace/insert 且无冲突；delete 与缩行替换按规则 3 丢弃
    edits: list[tuple[int, int, list[str]]] = []
    for tag, i1, i2, j1, j2 in ops_t:
        if tag == "equal" or tag == "delete":
            continue
        if tag == "replace" and (j2 - j1) < (i2 - i1):
            continue  # 规则 3：含删除成分的缩行替换同样无效（整块删除常被对齐成缩行 replace）
        if conflicts(i1, i2):
            continue  # 规则 2 / 跨块：ours 为准
        edits.append((i1, i2, t[j1:j2]))
    # 从 ours 出发，按 base 起点倒序套用用户编辑（倒序保证先套用的位置不受后续位移影响）
    result = list(o)
    for i1, i2, repl in sorted(edits, key=lambda e: e[0], reverse=True):
        result[anchor(i1) : anchor(i2)] = repl
    return "".join(result)
