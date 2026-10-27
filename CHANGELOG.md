# 更新日志

## 1.1.9（2026-10-27）

**用户什么都不用做**，`@v1` 自动生效。

### 改进

- **勾选同步快了一大截**：原来同步 job 会检出调用方仓库、再 `pip install -e` 装整套依赖（实测 8 秒装 24 个包：anthropic、pydantic、httpx…），可它只做「读事件、改一篇 issue 正文」。现在只检出工具仓库、只装 requests + pyyaml、直接跑源码——自有步骤从约 13 秒降到约 5 秒。
- **连点不再排队**：同步任务在同一组里改为「新编辑取消旧运行」，点几下只由最后一轮回写一次；否则每多点一次就多排一整轮，复选框迟迟不变。
- 清单提示改为实话实说：十几秒内同步，没变化就刷新页面。

### 为什么做不到「点击即变」

GitHub Actions 从事件到作业启动本身有 **0–15 秒**排队（平台行为，工具侧无法压缩），加上同步作业自身约 5 秒。能做到的是「十几秒内」，做不到「瞬间」。

## 1.1.8（2026-10-27）

**用户什么都不用做**，`@v1` 自动生效。

### 修复

- **勾完再点一次，GitHub 报 `Could not update issue comment`**：勾「全部合并」后工具要十几秒才回写清单正文；在这个窗口里再点一次复选框，页面拿的是旧正文，GitHub 会拒绝保存。现在回写前先读服务端最新正文——期间又出现了新编辑就不写，交给排在后面、拿着最新正文的那一轮去同步。连点几次只会合并成一次回写，不再覆盖你刚点的内容。
- 清单里补了一行提示：遇到这个报错，刷新页面重新点一次即可。

## 1.1.7（2026-10-27）

**工具代码随 `@v1` 自动生效**；勾选联动需要调用文件补一行触发器（见下）。

### 改进

- **「全部合并」变成真正的全选**：勾上它，下面所有条目约 1 分钟内一起变勾选；取消它，一起取消；反过来逐条勾满时它自动勾上，取消任意一条它自动取消。GitHub 的任务列表没有原生父子联动，实现方式是接住 `issues.edited` 事件、用内置令牌把正文回写成一致状态——只改排版，不合并、不跑更新、不发评论（编辑由内置令牌发起，GitHub 不会因此再触发 workflow，天然防循环）。
- 同步失败只记 `::warning::`，绝不让工作流变红；总开关的合并语义不受影响（同步没到位时仍按旧逻辑「总开关勾选 = 合并全部条目」）。

### 需要你做一步（可选）

调用文件 `.github/workflows/update-profile.yml` 补上触发器（老用户不补也能用，只是勾选不联动）：

```yaml
on:
  issues:
    types: [edited]
```

并把 `concurrency.group` 改成
`${{ github.event_name == 'issues' && 'update-profile-sync' || 'update-profile' }}`，
否则同步会排在正在等待合并（最长 30 分钟）的更新任务后面，看起来像没生效。

## 1.1.6（2026-10-27）

**用户什么都不用做**，`@v1` 自动生效。

### 修复

- **所有仓库 compare 全 404 的真因**：compare 地址误用了 git 的 `base..head`（两个点），而 GitHub REST 的 compare 端点只认 `base...head`（三个点），两个点一律 404——与令牌类型、令牌是否过期、历史是否被重写都无关。现已改为三个点；这也是此前「7 个仓库全读取失败」的根因。
- **`CompareGone` 兜底从未生效**：`GitHubError` 此前没有 HTTP 状态码，判断 `exc.status in (404, 409)` 永远为假，于是「基线失效 → 重置到当前 HEAD、下轮重试」这条路根本走不到。现在错误会带上状态码，真正的 404/409 会正常重置基线而不是反复整仓跳过。

### 新增

- **运行用量行**：日志与 Actions 摘要末尾新增
  `本轮 AI 用量：请求 N 次（失败 K）· 缓存复用 M 项（简介/描述翻译/仓库 README …）`、
  `仓库 README 无需重跑：X/Y（Z%）`、`耗时：M 分 S 秒`。
  增量更新到底省了多少调用，从此是可核对的数字，而不是形容词；「无变化，跳过更新」的轮次同样输出。

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
