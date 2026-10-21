# GitHub Overview Auto Agent 使用指南

自动维护你的 GitHub 个人主页 README（`你的用户名/你的用户名` 仓库）：拉取公开仓库数据、整理统计、精选项目、用 AI 生成中文简介，并做增量检测省额度。

## 它做什么

1. **拉数据**：通过 GitHub API 获取你的公开仓库（star、语言、最近 push 等）
2. **精选项目**：按 star 与活跃度打分，自动挑出最值得展示的项目
3. **生成统计**：中文统计表格 + github-readme-stats 可视化卡片
4. **AI 简介**：把仓库数据交给 Claude，生成 100–150 字中文简介
5. **增量更新**：数据没变化就不调 AI、不改文件、不产生提交

> **只扫公开仓库**：工具调用的 `/users/{用户名}/repos` 接口只会返回 public 仓库；即使返回里混入 private 条目也会被强制跳过，私有仓库永远不会出现在 README 或统计里。

## 快速开始（零克隆，全程网页操作，约 3 分钟）

**不需要 clone、不需要本地环境**，全程在浏览器里完成：

1. **一键获取仓库**：打开模板仓库 [ningbojian/ProfileReadmeAgent](https://github.com/ningbojian/ProfileReadmeAgent)，点绿色 **"Use this template" → Create a new repository**，把新仓库改名为 `你的用户名/你的用户名`。
   > 用户名不用填：工具会自动读取仓库所有者作为用户名。
2. **配 AI key（可选）**：仓库 → Settings → Secrets and variables → Actions → New secret，名称 `ANTHROPIC_API_KEY`，值是你的 Claude API key。不配也能用（AI 内容降级为数据版）。
3. **配勾选框（要用勾选框必配，2 分钟）**：生成一个 classic PAT（勾 `repo` + `workflow` 两项），存成 secret `WORKFLOW_PAT`——配好第一次运行就自动生成你的勾选框；不配则对话框只有通用输入框（点名用 `projects_repos`）。详细步骤见下文「配置勾选框」。
4. **手动跑一次**：Actions 页面 → Update Profile README → Run workflow。完成后：
   - **overview 更新**直接出现在 README；
   - **项目介绍 / 简历素材**（AI 生成内容）会以 **Pull Request** 形式出现——在 PR 的 Diff 里审核、可直接编辑，点 **Merge** 才把文件放进 `docs/projects/`（你的简历素材库，**不会逐个链接到主页**，主页保持干净）；不想要就点 **Close**（同一内容之后不会重复打扰你）。

之后每周一定时自动更新（北京时间约 10:23），也可随时手动触发。所有可选项都在 `config.yaml` 里，网页上点 Edit 就能改。

## 本地运行

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
python -m profile_agent --no-ai            # 不调 AI，直接生成
python -m profile_agent                    # 完整运行（需 ANTHROPIC_API_KEY）
python -m profile_agent --dry-run          # 只打印不写文件
python -m profile_agent --force            # 忽略增量检测
python -m profile_agent --fixture tests/fixtures --no-ai --dry-run   # 完全离线演示
```

环境变量：`GITHUB_TOKEN`（可选，不填走匿名配额 60 次/小时；建议去 GitHub → Settings → Developer settings 生成一个 classic PAT）、AI key（见下一节，名字由 `ai.key_env` 决定，默认 `ANTHROPIC_API_KEY`）。

## 换用其他模型（DeepSeek / 千问 / Kimi / MiMo / Ollama）

AI 后端支持两种 provider：

```yaml
ai:
  provider: "anthropic"          # Claude 官方（默认）
  # ---- 或者用任意 OpenAI 兼容接口 ----
  provider: "openai"
  base_url: "https://api.deepseek.com"  # 服务商文档里的接口地址（通常带 /v1），照抄
  key_env: "AI_API_KEY"                # 建议统一叫 AI_API_KEY（见下方说明）
  model: "deepseek-chat"                # 服务商文档里的模型名
```

> **Secret 命名建议**：用 Claude 就叫 `ANTHROPIC_API_KEY`；用其他任何服务商统一叫 **`AI_API_KEY`**（workflow 会把它注入运行环境，任何服务商都通用，换服务商不用改 secret）。

常见服务商照抄这三行即可（地址以你开通时文档显示的为准）：

| 服务商 | base_url（示例） | model（示例） |
| --- | --- | --- |
| DeepSeek | `https://api.deepseek.com` | `deepseek-chat` |
| 通义千问 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` |
| Kimi（月之暗面） | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` |
| MiMo（小米） | 保持 `provider: "anthropic"` + `base_url: https://api.xiaomimimo.com/anthropic` | `mimo-v2.6-flash` |
| Ollama（本地，免费） | `http://localhost:11434/v1` | `qwen2.5:7b` 等 |

改完配置，在 Secrets 里添加对应名字的 key（如 `DEEPSEEK_API_KEY`），然后手动触发一次 force 即可。数据没变化时不会调用任何模型，换 provider 不会产生额外开销。

## 控制开销（重要）

你的 key 你做主，三层控制：

| 层级 | 怎么做 | 效果 |
| --- | --- | --- |
| **运行频率** | 改 `.github/workflows/update-profile.yml` 里的 `cron` | 见下方对照表 |
| **定时开关** | `config.yaml` 里 `schedule.enabled: false` | 定时运行第一步就结束（不装依赖不调 API）；手动触发不受影响。更彻底可在 Actions 页面点 "Disable workflow" |
| **AI 调用** | 增量检测 + `ai.max_age_days` | 数据没变化完全不调 API；简介 14 天内不重新生成（可调） |

### cron 对照表

| 频率 | cron（UTC） | 北京时间 |
| --- | --- | --- |
| 每天 | `23 2 * * *` | 每天 10:23 |
| 每周一（默认） | `23 2 * * 1` | 周一 10:23 |
| 每月 1 号 | `23 2 1 * *` | 每月 1 日 10:23 |

> GitHub 的 cron 按 UTC 计，且高峰期可能有几分钟延迟；避开整点（如 23 分）更稳定。`*/N` 之类的写法在 GitHub cron 里语义与直觉不同（每月 1 号重置），想更高频请用上表的明确写法。

## 灵活开关：只更新其中一部分

overview 和项目介绍（简历素材）互相独立，三种玩法：

| 想要的效果 | 配置（config.yaml） | 或手动触发时 |
| --- | --- | --- |
| 两者都更新 | 默认 | 默认 |
| 只更新 overview | `projects.enabled: false` | 取消勾选 update_projects |
| 只更新项目介绍 | `readme.enabled: false` | 取消勾选 update_readme |

**选择给哪些仓库生成介绍**（多选，也可以都不选），两种方式：

```yaml
projects:
  include: ["cool-tool"]   # 只给这些仓库生成（仓库名）
  exclude: ["old-thing"]   # 这些仓库永远跳过
```

在网页上编辑 `config.yaml` 保存即可，下次运行生效。

**勾选框选仓库（推荐，一步到位）**：Actions → Update Profile README → **Run workflow**，对话框里每个公开仓库都有一个独立勾选框（带最近推送日期，一眼看出哪个仓库最近活跃）——勾选想更新的仓库，点 Run workflow，完成。勾选的仓库会**强制重新生成**（即使数据没变、即使不在精选/白名单里）；一个都不勾则按 config 的 include/exclude 规则正常更新。

> **勾选框和增量检测各管一件事**：你在 GitHub 上改了仓库描述、推了代码、加了 topic——数据哈希变了，**不勾也会自动重新生成**（定时跑就是这样）。勾选框是用来对付增量检测管不到的情况：「数据没变，但我对 AI 写的这篇文案不满意，重写一版」。也就是说：数据变了等自动更新即可；想重写某篇介绍才勾它。成本上，不勾=增量零浪费，勾几个=花几次 AI 调用，明码选择。

> 勾选框从哪来？GitHub 的触发框本身不支持动态列表，勾选框是工具在每次运行结束时把当前仓库列表同步进 workflow 文件实现的（新仓库自动出现、删除的自动消失）。也正因如此，**模板不预置任何勾选框**——预置会把模板作者的仓库带给你；配置 `WORKFLOW_PAT` 后的第一次运行，就会生成**只含你自己仓库**的勾选框。

**配置勾选框（要用勾选框，必配一次，2 分钟）**：GitHub 有个硬性限制——Actions 内置的 `GITHUB_TOKEN` 永远无权修改 `.github/workflows/` 下的文件（实测确认：连推到新分支也被拒），所以勾选框的生成与自动同步都需要你自己的令牌：

1. 右上角头像 → Settings → Developer settings → Personal access tokens。这里有两种令牌，**选 `Tokens (classic)`**：
   - *Fine-grained tokens*（新）：按仓库、按权限项精细授权，配置繁琐，且部分老工具/场景兼容性一般；
   - *Tokens (classic)*（经典）：按「权限域」授权，简单通用。本工具只需要它。
2. **Generate new token (classic)**，勾选 **`repo`** 和 **`workflow`** 两项权限（`repo` 负责读写仓库内容，`workflow` 负责修改 workflow 文件；令牌还要替你把 README/统计提交推上去，所以不能只勾 workflow），有效期自选
3. 回到本仓库 → Settings → Secrets and variables → Actions → **New repository secret**，名称填 `WORKFLOW_PAT`，值粘贴刚生成的 token
4. 完成。第一次运行即生成只含你自己仓库的勾选框；之后新增/删除的仓库每次运行自动同步，无需再管

> **不配置也完全能用**：README、统计、定时更新都不受影响。区别只在勾选框：已有勾选框照常勾选，但新建/删除的仓库不会自动增删（运行日志有 warning 提醒）；从模板新建、还没有勾选框的仓库则完全不生成（保持纯净）。这两种情况都可用 `projects_repos` 输入框点名，或在本地跑 `--repos 仓库名`。另外注意：如果你编辑过 workflow 文件（比如改了 cron），内置 token 同样推不上去，也会需要这个 secret。

**命令行/脚本点名**（自动化用）：`gh workflow run "Update Profile README" -f projects_repos=shiyuan,agri-ecommerce-platform`，或在对话框的 `projects_repos` 输入框填逗号分隔仓库名（留空=按 config 规则；`all`=全部精选）。点名的仓库同样是强制重新生成语义。

**每次运行都有逐仓库明细**：运行完成后打开该次运行页面，Summary 里有一张表，逐仓库列出这次做了什么（项目介绍：跳过/已重新生成/数据版；主页描述补位：复用/新生成/失败）。

**主页「暂无描述」自动补位**：精选项目里 description 为空的仓库，会用它自己 README 的内容生成一句 ≤80 字的中文短描述顶上（不再显示「暂无描述」）。不想要这个行为：`ai.fill_empty_descriptions: false`。

介绍内容以**各仓库的 README 为主要事实来源**（只有真正要生成/更新某个仓库的介绍时才会去拉它的 README，无变化不调用）；仓库没有 README 时退回只用 GitHub 上的描述等事实信息。

## AI 内容审核流（Pull Request）

你没有直接审核窗口的问题不存在——**所有 AI 写的项目介绍都必须经过你的 PR 审核才会公开**：

- **PR 里只有 `docs/projects/` 一个目录**：README、统计、快照的常规更新都直接进 main，PR 开多久都不会和它们冲突，放心挂着慢慢审。
- 万一 PR 页显示 conflict（通常是 main 上有人手工改了同一批介绍文件），**重新运行一次工作流即可**——分支会基于最新 main 重建。

1. 运行发现介绍有变化（新仓库、数据变了、prompt 升级）→ 自动开一个 PR「AI 项目介绍更新（待审核）」
2. 你在 PR 的 **Files changed** 里看 Diff，不满意可以直接在网页上编辑文件
3. 点 **Merge** → 文件进入 `docs/projects/`（简历素材库，不占用主页）；点 **Close** → 不发布，且**同一批内容之后不会重复开 PR**打扰你（想强制重新生成：改 `config.yaml` 里 `ai.prompt_version` 为 2 再手动触发 force）

纯统计/overview 的机械更新不走 PR、直接生效，不打断你。想关掉审核流直接提交：`projects.review_via_pr: false`（不建议）。

## 配置项参考（config.yaml）

```yaml
schedule:
  enabled: true            # false = 关掉定时更新（手动仍可触发）
github:
  username: ""             # 留空 = 自动用仓库所有者（fork 即用）
  token_env: "GITHUB_TOKEN"  # token 读哪个环境变量
repos:
  exclude_forks: true      # 不展示 fork 的仓库
  exclude_archived: false  # 是否排除已归档仓库
  exclude: ["you/you"]     # 黑名单（建议排除 profile 仓库自身）
featured:
  count: 6                 # 精选项目个数
  min_stars: 0             # 入选最低 star
  recency_days: 365        # 只考虑一年内有 push 的仓库；0 = 不限
  sort_by: "score"         # score | stars | updated
  score_stars_weight: 1.0
  score_recency_weight: 0.5
readme:
  enabled: true            # false = 不更新 overview
  path: "README.md"
  template: "templates/profile.md.j2"
  cards:
    theme: "github_dark"   # github_dark | github_light 等
    show: ["stats", "top_langs", "streak"]  # 三种卡片任选
  include_update_time: true
  visitors_badge: false   # true = 主页嵌入「主页访问量」计数徽章（komarev.com 免费第三方服务，页面渲染一次 +1；GitHub 不提供官方的主页访问量 API，这是业界通行做法）
ai:
  enabled: true
  model: "claude-opus-5-5" # 降本可换 "claude-haiku-5-5"
  max_repos: 15            # 最多把多少仓库喂给 AI
  max_age_days: 14         # 简介最长复用天数
  prompt_version: 1        # 改了 prompt 后 +1 可强制重新生成
  fill_empty_descriptions: true  # 精选项目 description 为空时，用其 README 生成短描述补位
projects:
  enabled: false           # true = 生成项目介绍/简历素材（默认关闭，开启前先配好 AI key）
  dir: "docs/projects"
  featured_only: true      # false = 给全部仓库生成
  include: []              # 多选白名单，非空时只生成这些仓库
  exclude: []              # 黑名单
  review_via_pr: true      # AI 内容走 PR 审核（推荐）
```

改完 `ai.prompt_version` 或想立刻刷新简介时，手动触发并勾选 force。

## 常见问题

- **想关掉自动更新**：`config.yaml` 里 `schedule.enabled: false` 提交即可；或在 Actions 页面 Disable workflow。
- **Run workflow 对话框里没有仓库勾选框**：模板不预置勾选框（预置会把模板作者的仓库带给你）。按上文「配置勾选框」配好 `WORKFLOW_PAT` 后，第一次运行自动生成；已生成但缺了新建仓库的，说明自动同步未配置或令牌过期，同样按该节检查。急用可先在 `projects_repos` 里点名。
- **AI 简介没生成**：检查 secret 名称是否为 `ANTHROPIC_API_KEY`；工具在 AI 失败时会保留旧简介，不会让任务失败。
- **README 被我改坏了**：手动改动会被下次运行覆盖——所有可定制内容都在 `config.yaml` 与 `templates/profile.md.j2` 里改。
- **数据是旧的**：Actions 手动触发 + force 即可刷新。
- **把本工具分享给别人**：对方只需完成「快速开始」4 步；本仓库既是工具源码也是 profile 仓库。

## 开发

```bash
pip install -e ".[dev]"
pytest            # 全部离线运行，不碰网络
```

代码结构见 `src/profile_agent/`：`github_client`（取数）→ `aggregate`（过滤/统计/精选/指纹）→ `bio`（AI 简介）→ `render`（模板）→ `cli`（编排与增量门）。
