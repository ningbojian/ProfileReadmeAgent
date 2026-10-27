# 更新日志

## 1.1.0（2026-10-27）

**用户默认什么都不用做**；接 OpenCode Go 的加两行 `ai.headers`（见下）。

### 新增

- **`ai.headers`**：给 AI 请求附加自定义请求头，`anthropic` / `openai` 两种 provider 都会带上。
- **OpenCode Go 支持**：它要求 `x-opencode-session`，缺失直接返回 `MissingSessionID`。配置：

  ```yaml
  ai:
    provider: "anthropic"
    base_url: "https://opencode.ai/zen/go"
    key_env: "AI_API_KEY"
    model: "deepseek-v4.1-flash"
    headers:
      x-opencode-session: "profile-agent"
  ```

### 兼容性

- 不写 `headers` 时行为与 1.0.0 完全一致，现有配置零改动。

## 1.0.0（2026-10-25）

首个可复用 workflow 版本。**旧模板副本需要迁移一次**（见下），之后自动收更新。

### 新机制

- **代码单源**：工具代码只在本仓库维护；用户仓库只保留调用文件 `.github/workflows/update-profile.yml` + `config.yaml`。
- **可复用 workflow**：全部逻辑在 `reusable-update-profile.yml`，调用方写
  `uses: ningbojian/ProfileReadmeAgent/.github/workflows/reusable-update-profile.yml@v1`。
  代码版本跟随 `job.workflow_sha`，工具更新随标签自动生效。
- **Use this template** 仍然一键可用，同样自动收更新。
- 版本标签：`v1.0.0`（不可变）+ `v1`（移动）；破坏性变更发 `v2`，`v1` 继续可用。

### 功能

- 手写 README 默认只维护一个 AI 简介块（`readme.mode: auto`），块外逐字节保留；整页接管改为显式 `readme.mode: full`。
- 双语 README、原生 Pinned、增量更新、各仓库 README 托管区（模式 B）、仓库描述自动写回。
- 定时「每天唤醒 + 间隔门」，Run workflow 下拉可直接改周期。
- 每次运行有逐仓库明细（Step Summary）。

### 迁移（旧模板副本 → 调用文件）

1. 用本仓库 `.github/workflows/update-profile.yml` 的内容替换你仓库里的同名文件；
2. 可选：删掉 `src/`、`tests/`、`templates/`、`pyproject.toml`、`docs/`；
3. 手动 Run workflow 一次。

`config.yaml`、`README.md`、`data/` 原样保留。
