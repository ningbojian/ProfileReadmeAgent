"""workflow YAML 结构回归：定时间隔下拉、RESERVED 防撞名、间隔门标记（guide「控制开销」配套）。"""

from pathlib import Path

import yaml

WF = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "update-profile.yml"


def _wf() -> dict:
    # PyYAML 按 YAML 1.1 解析：裸 `on:` 的键是布尔 True 而不是字符串 "on"
    raw = yaml.safe_load(WF.read_text(encoding="utf-8"))
    return raw.get("on") or raw.get(True)


def test_wf_schedule_interval_input_is_choice_with_sparse_options():
    """「定时运行间隔」下拉：不修改 + 每 1/3/5/7 天，且默认不修改。

    有意变更（2026-10-24 交互治理）：1–7 全档改为稀疏档位——相邻档语义差别小，
    档位越多越难选；1/3/5/7 覆盖从「每天」到「一周」的实际需求（用户定档）。
    """
    inputs = _wf()["workflow_dispatch"]["inputs"]
    spec = inputs["schedule_interval"]
    assert spec["type"] == "choice"
    assert spec["options"][0].startswith("不修改")
    assert spec["default"] == spec["options"][0]
    assert spec["options"][1:] == ["每 1 天", "每 3 天", "每 5 天", "每 7 天"]


def test_wf_daily_cron_with_interval_markers():
    """cron 每天唤醒；实际是否运行由 interval_days/interval_anchor 标记门决定。"""
    assert _wf()["schedule"][0]["cron"] == "23 2 * * *"
    text = WF.read_text(encoding="utf-8")
    assert "# interval_days:" in text
    assert "# interval_anchor:" in text


def test_wf_check_step_gates_on_interval_markers():
    """定时第一步（check）读标记做间隔门：未到日子输出 schedule=false 并写明原因。"""
    text = WF.read_text(encoding="utf-8")
    assert "interval_days" in text
    assert "interval_anchor" in text
    assert "非每" in text  # 跳过时的可读原因行


def test_wf_rewrite_step_only_on_dispatch_and_skips_default():
    """改写步骤仅手动触发执行；默认选项「不修改」时原样退出不改文件。"""
    text = WF.read_text(encoding="utf-8")
    assert 'if: github.event_name == \'workflow_dispatch\'' in text
    assert "不修改" in text


def test_wf_dialog_hides_projects_inputs():
    """Run workflow 对话框不再暴露任何 projects 相关输入（功能已彻底移除）。

    对话框输入须恰好是这四个安全项：本次强制重跑（rerun 单选下拉）/ 更新主页 /
    等待合并 / 定时运行间隔，不含 update_projects / projects_repos / 逐仓勾选框。
    有意变更（A-P0）：项目介绍功能已删除——config.yaml 的 projects 段、workflow
    勾选框映射与点名链路一并下线。
    """
    inputs = _wf()["workflow_dispatch"]["inputs"]
    assert set(inputs) == {
        "rerun",
        "update_readme",
        "wait_for_merges",
        "schedule_interval",
    }
    text = WF.read_text(encoding="utf-8")
    assert "REPO_CHECKBOXES_START" not in text  # 勾选框区块标记（移除后同步步骤自我跳过）


def test_wf_rerun_choice_options_and_flag_mapping():
    """强制重跑为单选下拉（四选一互斥）：选项、默认值与 CLI 旗标映射一一对应。

    有意变更（2026-10-24 交互治理）：force / repo_readmes 两个重叠勾选合并为
    rerun 下拉——force 原已包含 repo_readmes 的全量重跑，两个勾选并存让用户
    无法判断该点哪个。shell 按选项名精确 case，测试锁选项列表与三条映射分支，
    文案改字忘改映射会立即红。
    """
    inputs = _wf()["workflow_dispatch"]["inputs"]
    spec = inputs["rerun"]
    assert spec["type"] == "choice"
    assert spec["options"] == [
        "不强制（按变化更新）",
        "强制重写主页",
        "强制重跑各仓库 README（主页仍按变化更新）",
        "两者都强制（主页重写 + 仓库 README 全量重跑）",
    ]
    assert spec["default"] == spec["options"][0]
    text = WF.read_text(encoding="utf-8")
    assert '"强制重写主页") FLAGS="$FLAGS --force"' in text
    assert 'FLAGS="$FLAGS --repo-readmes"' in text
    assert 'FLAGS="$FLAGS --force --repo-readmes"' in text
    # 旧的两个重叠布尔输入不得回潮（否则重叠困惑重现）
    assert "force" not in inputs
    assert "repo_readmes" not in inputs


def test_wf_dialog_descriptions_single_line_short_and_plain():
    """对话框四条 description 均单行、短、零黑话（用户反馈：太长、对新人不自解释）。

    description 即 Run workflow 里输入旁的可见文本：换行会拖成多行，超长会淹没
    其余选项；rerun 说明「不选 = 只更新有变化的内容」且如实交代 AI 文案仍按
    缓存复用（force 不会重生成简介）；wait_for_merges 保住「不勾零风险」语义，
    update_readme 与 default:true 一致（写明默认勾选），schedule_interval 只答
    「改什么、何时生效」。
    """
    inputs = _wf()["workflow_dispatch"]["inputs"]
    for spec in inputs.values():
        desc = spec["description"]
        assert "\n" not in desc  # 单行：> 折叠或显式换行都会让对话框变两行
        assert len(desc) <= 60  # 一行读完的上限
        for jargon in ("A+B", "模式 B", "增量门"):
            assert jargon not in desc
    rerun_desc = inputs["rerun"]["description"]
    assert "不选" in rerun_desc  # 默认档的语义可见
    assert "缓存" in rerun_desc  # 如实说明：强制重跑不会重生成 AI 文案
    wait_desc = inputs["wait_for_merges"]["description"]
    assert "不勾" in wait_desc  # 不是必选项：消除「不勾会不会漏掉」的焦虑
    assert "30 分钟" in wait_desc  # 等待窗口时长可见
    update_desc = inputs["update_readme"]["description"]
    assert "默认" in update_desc  # default:true 对应「默认勾选」
    # 跨控件矛盾化解：不勾主页时，rerun 的「强制重写主页」也失效——必须写明
    assert "完全不动主页" in update_desc
    interval_desc = inputs["schedule_interval"]["description"]
    assert "本轮结束后" in interval_desc  # 何时生效说清楚


# ---- WP8 保存即触发（push → main + README 路径过滤）----


def test_wf_push_trigger_targets_main_readme_paths():
    """WP8：README/README.en.md 保存到 main 即触发（用户选定「保存即触发」）。

    paths 只圈两个 README 文件——其余 push 不触发；
    防循环三重保险之一（bot 提交 [skip ci] + paths 过滤 + branches:[main]）。
    """
    push = _wf()["push"]  # PyYAML 解析为 dict（branches/paths 两个键，非 list 形态）
    assert push["branches"] == ["main"]
    assert push["paths"] == ["README.md", "README.en.md"]


def test_wf_steps_bypass_interval_gate_for_non_schedule():
    """WP8：push 下工作步骤穿透定时门（核验结论：两门内聚于 check 步，无需改 if）。

    工作步骤条件恒为 `!= 'schedule' || schedule=='true'`（push 下左操作数真短路）；
    仅 check 步（schedule 专属）与间隔改写步（dispatch 专属）是事件专属条件。
    不存在任何把 push 挡在门外的其他 if 形态。
    """
    raw = yaml.safe_load(WF.read_text(encoding="utf-8"))  # _wf() 只返回 on 段，jobs 在根文档
    steps = raw["jobs"]["update"]["steps"]
    allowed = {
        "github.event_name == 'schedule'",
        "github.event_name == 'workflow_dispatch'",
        "github.event_name != 'schedule' || steps.check.outputs.schedule == 'true'",
    }
    conds = [s["if"] for s in steps if s.get("if")]
    assert conds, "工作步骤不得全部无条件（定时门必须存在于 check/工作步）"
    assert [c for c in conds if c not in allowed] == []


def _step(text: str, name: str) -> str:
    """按步骤名截出该步的 YAML 片段（到下一个 step 或文件末尾）。"""
    start = text.index(f"- name: {name}")
    rest = text[start:]
    nxt = rest.find("\n      - ", 1)
    return rest if nxt < 0 else rest[:nxt]


def test_wf_git_add_includes_readme_en():
    """WP8：提交步 git add 带上 README.en.md（路径步输出 readme_en，与 cli 派生同口径）。"""
    text = WF.read_text(encoding="utf-8")
    assert 'print(f"readme_en=' in text  # 读取配置路径步输出 en 路径
    commit = _step(text, "提交变更")
    assert "steps.paths.outputs.readme }}" in commit
    assert "steps.paths.outputs.readme_en }}" in commit


def test_wf_git_add_skips_missing_files_and_commits_interval_change():
    """提交步只 add 真实存在的文件；定时间隔改写会 stage workflow（需 PAT）。

    真实故障（2026-10-25 复核）：用户先设 bilingual:false / readme.enabled:false 时
    README.en.md / snapshot 不存在，`set -e` 下 `git add` 返回 128，整个 job 失败；
    另一头，间隔下拉改写的 workflow 文件从未 staged，改动被丢弃、guide 承诺落空。
    """
    text = WF.read_text(encoding="utf-8")
    commit = _step(text, "提交变更")
    assert 'if [ -e "$p" ]' in commit
    for output in ("readme", "readme_en", "snapshot"):
        assert f"steps.paths.outputs.{output} }}" in commit
    assert "git add .github/workflows/update-profile.yml" in commit
    assert "定时间隔改动未保存" in commit


def test_wf_interval_rewrite_requires_pat():
    """间隔改写需要 WORKFLOW_PAT；没有就明确提示并保持原周期，不留无效改动。"""
    text = WF.read_text(encoding="utf-8")
    step = _step(text, "调整定时运行间隔（schedule_interval）")
    assert "WORKFLOW_PAT" in step
    assert "本轮保持原周期" in step


def test_wf_bot_commit_keeps_skip_ci():
    """WP8：bot 提交保留 [skip ci]（防 push 触发自循环的第一道保险）。

    有意变更（A-P0）：项目介绍 PR 提交随审核流移除，工作流只剩直提 main 一个提交。
    """
    text = WF.read_text(encoding="utf-8")
    assert 'git commit -m "chore: 自动更新 Profile README [skip ci]"' in text


def test_wf_warns_when_repo_name_is_not_username():
    """仓库名不是 用户名/用户名 时给出可执行警告：否则主页生成了却不显示。

    从模板建库最常见的失误是沿用默认仓库名；GitHub 只展示 用户名/用户名 仓库的
    README。比较用小写展开，避免用户名大小写差异导致误报。
    """
    text = WF.read_text(encoding="utf-8")
    step = _step(text, "检查仓库名（须为 用户名/用户名）")
    assert "${GITHUB_REPOSITORY,,}" in step
    assert "${GITHUB_REPOSITORY_OWNER,,}" in step
    assert "::warning::" in step
    assert "Settings → General" in step
    assert "不会显示" in step


def test_wf_has_no_projects_or_named_repo_traces():
    """反向锁（A-P0）：yml 全文不得再出现项目介绍/点名仓库链路的任何痕迹。"""
    text = WF.read_text(encoding="utf-8")
    for token in ("docs/projects", "--repos", "repo_inputs", "项目介绍", "steps.resolve"):
        assert token not in text, f"workflow 残留项目介绍痕迹：{token}"
