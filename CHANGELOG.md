# 更新日志

## 1.1.5（2026-10-27）

**用户什么都不用做**，`@v1` 自动生效。

### 改进

- **令牌自诊断再加两项**：日志开头那行 `GitHub 令牌：…` 现在还会带上 classic 令牌的 **OAuth scopes**（`repo, workflow` 之类）；fine-grained / App 令牌不返回 scopes，会明确写「未返回」，一眼能看出令牌类型是不是自己以为的那种。
- **404 报错带请求路径**：同一条「用户不存在或仓库不可见」可能来自仓库信息、`/commits/HEAD`、README、compare 等不同端点，现在括号里写明是哪一个，排查不用猜。

## 1.1.4（2026-10-27）

**用户什么都不用做**，`@v1` 自动生效。

### 改进

- **运行日志自报令牌身份**：开头多一行 `GitHub 令牌：classic（ghp_）· 账号 xxx`（只报类型前缀和账号，不打印令牌本身）。「配了令牌却全 404」时，这一行直接说明本轮到底用了哪把令牌、属于谁。
- **404 提示带上这行信息**：摘要里会写「本轮读取用的令牌：…」，把类型/账号和排查顺序放在一起，不用再猜。

## 1.1.3（2026-10-27）

**用户什么都不用做**，`@v1` 自动生效。

### 修复

- **「所有仓库读取失败 404」的真凶**：配置里默认的 `github.token_env: "GITHUB_TOKEN"` 指的是 Actions 内置令牌，它**只对当前仓库有效**——拿它读账号下的其他仓库（哪怕公开仓库）GitHub 一律返回 404 而不是 403。现在只要配了 `WORKFLOW_PAT` secret，工具就自动优先用它；显式把 `token_env` 写成别的名字时行为不变。
- **404 提示文案纠正**：不再一口断定「你的令牌是 fine-grained」，改为先指出「内置 token 读不了别的仓库」，再依次列令牌类型、secret 是否粘了最新值、是否过期。

## 1.1.2（2026-10-27）

**用户什么都不用做**，`@v1` 自动生效。

### 修复

- **OpenAI 兼容分支的报错不再丢原因**：以前只报 `401 Client Error`，服务商写在响应体里的原始消息（如 `Go requires a credential`）看不到；现在连响应体一起上报。
- **`Go requires a credential` 不再被误判成「额度/计费」**：它表示这个地址不认这把凭据（典型是把个人 API key 打到了 CLI 的 `/zen/go` 路由），归到「凭据类型不匹配」，提示里给出正确端点。

### 文档

- OpenCode Go 的正确地址：DeepSeek 等用 `https://opencode.ai/inference/go/openai/v1`（`provider: "openai"`），Claude Haiku / MiniMax / Qwen 用 `https://opencode.ai/inference/go/anthropic`。**不要用 `https://opencode.ai/zen/go`**。

## 1.1.1（2026-10-27）

**用户什么都不用做**，`@v1` 自动生效。

### 改进

- **AI 失败显式上报**：余额不足 / key 失效 / 缺请求头 / 限流不再只留一行 stderr。运行摘要末尾会出现「AI 调用失败」表（服务商、模型、状态码、完整原因、处理建议），run 页面顶部同时出现黄色 warning。
- **明确责任边界**：这类失败不是工具故障——本轮简介、翻译、描述继续沿用缓存，修好后重跑一次即可。
- **PAT 404 提示**：多个仓库同时读取失败且多为 404 时，摘要会给出排查顺序（见 1.1.3 的纠正——最常见的原因其实是拿内置 `GITHUB_TOKEN` 读别的仓库）。

### 兼容性

- 退出码、生成内容、缓存行为全部不变，只是把本来就存在的失败说清楚。

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
