# ProfileReadmeAgent 使用指南

自动维护你的 GitHub 个人主页 README（`你的用户名/你的用户名` 仓库）：拉取公开仓库数据、整理统计、项目列表（默认全部仓库）、用 AI 生成中文简介，并做增量检测省额度。

## 它做什么

1. **拉数据**：通过 GitHub API 获取你的公开仓库（star、语言、最近 push 等）
2. **项目列表**：公开仓库按 star 与活跃度排序，以对齐表格展示在主页（默认全部；想只展示一部分在 `featured` 里配）。**置顶跟随你在 GitHub 个人主页设置的 Pinned 仓库**（最多 6 个，按官方顺序）——工具读取后以 ⭐「📌 置顶项目」卡片排在表格前，其余仓库照常进表格
3. **生成统计**：低饱和统计/技术标签徽章 + github-readme-stats 可视化卡片
4. **AI 简介**：把仓库数据交给 Claude，生成 100–150 字中文简介
5. **增量更新**：数据没变化就不调 AI、不改文件、不产生提交
6. **各仓库 README 自动更新（可选，默认关闭）**：某个仓库有代码提交时，AI 顺手把它的 README 也更新了——开 PR 给你审；主页只有缺描述仓库的一句话简介会随后吸收，统计与其余内容不依赖合并（详见下文「各仓库 README 自动更新」）
7. **仓库描述自动写回（可选，默认关闭）**：仓库有新推送时，AI 重写 GitHub 上的仓库描述；第一次只记基线、你手改过就永久停手（详见下文「仓库描述自动写回」）

> **只扫公开仓库**：工具调用的 `/users/{用户名}/repos` 接口只会返回 public 仓库；即使返回里混入 private 条目也会被强制跳过，私有仓库永远不会出现在 README 或统计里。

## 快速开始（零克隆，全程网页操作，约 3 分钟）

**不需要 clone、不需要本地环境**，全程在浏览器里完成：

1. **一键获取仓库**：打开模板仓库 [ningbojian/ProfileReadmeAgent](https://github.com/ningbojian/ProfileReadmeAgent)，点绿色 **"Use this template" → Create a new repository**，把新仓库改名为 `你的用户名/你的用户名`。
   > 用户名不用填：工具会自动读取仓库所有者作为用户名。
2. **配 AI key（可选）**：仓库 → Settings → Secrets and variables → Actions → New secret，名称 `ANTHROPIC_API_KEY`，值是你的 Claude API key。不配也能用（AI 内容降级为数据版）。
3. **配 PR / 写权限令牌（要用「各仓库 README 自动更新」「`readme.mode: full` 的整页接管预览」「仓库描述写回」必配，2 分钟）**：生成一个 classic PAT（勾 `repo` + `workflow` 两项），存成 secret `WORKFLOW_PAT`——没有它模式 B 无法推送分支/开 PR，整页接管会跳过本轮并提示，仓库描述写回同样跳过；其余功能不受影响。手写 README 默认走 auto 模式，只插一个 AI 简介块，**不需要 PAT**。详细步骤与常见坑见下文「配置 WORKFLOW_PAT」。
4. **手动跑一次**：Actions 页面 → Update Profile README → Run workflow。完成后 **overview 更新**直接出现在 README。

> **已经有 `你的用户名/你的用户名` 仓库？** 不要删。先在它的 Settings → Rename 改成临时名字（如 `profile-backup`——提交历史与原 README 都完整保留），再从模板创建新的同名仓库；把你的旧 README **全选后整份粘贴**进新仓库的 `README.md`（覆盖整个文件——别把模板第一行的说明注释留在上面，否则工具会当成机器页整页替换），然后跑第一次：默认 `readme.mode: auto`，只会在你的第一个标题后加一个 AI 简介块，其他内容原样保留（见「已有手写 README」）。确认无误后旧仓库留着当备份或删掉都行。

之后默认每 3 天定时自动更新（北京时间约 10:23 唤醒检查），也可随时手动触发。所有可选项都在 `config.yaml` 里，网页上点 Edit 就能改。

## 安装方式与版本更新（工具更新怎么进到你的仓库）

工具代码只有一份，放在工具仓库 [ningbojian/ProfileReadmeAgent](https://github.com/ningbojian/ProfileReadmeAgent)。你的主页仓库只保留一份约 35 行的**调用文件** `.github/workflows/update-profile.yml`，全部执行逻辑在工具仓库的可复用 workflow 里，按标签引用：

```yaml
uses: ningbojian/ProfileReadmeAgent/.github/workflows/reusable-update-profile.yml@v1
```

所以**你不需要复制代码、也不需要手动同步**：工具仓库更新后，你的下一次运行自动用上新版本。你的仓库里没有 `src/`，也就没有「代码过期」这回事。

| 你写的版本 | 行为 | 适合 |
| --- | --- | --- |
| `@v1`（推荐，默认） | 跟随 v1 系列的最新提交，修 bug / 小功能自动生效 | 绝大多数人 |
| `@v1.0.0` | 固定在某个版本，永远不变 | 需要绝对稳定 |
| `@main` | 最新开发版 | 尝鲜、帮忙验证 |

升级或降级只是把 `@v1` 改成 `@v1.0.0`（或反过来）再提交，下一次运行即生效。破坏性变更会发 `v2`，`v1` 继续可用——你不改就一直能跑。

### 两种安装方式

1. **Use this template（推荐，一键）**：工具仓库 → **Use this template → Create a new repository**，仓库名 `你的用户名/你的用户名`（保持 Public）。调用文件与 `config.yaml` 会一起带过来，跑一次 Actions 即可；之后自动收更新。
2. **手动新建仓库**：新建 `你的用户名/你的用户名` 公开仓库，只复制两份文件：
   - 工具仓库的 [`.github/workflows/update-profile.yml`](https://github.com/ningbojian/ProfileReadmeAgent/blob/main/.github/workflows/update-profile.yml) → 你的 `.github/workflows/update-profile.yml`
   - 工具仓库的 [`config.yaml`](https://github.com/ningbojian/ProfileReadmeAgent/blob/main/config.yaml) → 你的 `config.yaml`

   然后手动 Run workflow 一次。同样自动收更新。

> `config.yaml` 里的 `github.username` 留空即可（自动取仓库所有者）。调用文件里的 `permissions`（`contents: write` + `issues: write`）不要删，删了推不动 README。

### 从旧模板副本迁移（2026-10 之前建的仓库）

旧模板把整套代码（`src/`、`tests/`、`templates/`）复制进了你的仓库，那些是快照，收不到工具更新。迁移只做一件事：

1. 把 `.github/workflows/update-profile.yml` 的内容**整个替换**成工具仓库里的调用文件（约 35 行）；
2. 可选：删掉 `src/`、`tests/`、`templates/`、`pyproject.toml`、`docs/`——新机制不执行它们，留着也不影响；
3. 手动 Run workflow 一次确认能跑通。

之后你的仓库就是标准用户：工具更新自动生效，不用再同步代码。`config.yaml`、`README.md`、`data/` 都原样保留。

## 本地运行

Actions 里跑不需要任何本地环境。想在本地跑，装一次工具即可（工具代码不在你的主页仓库里）：

```bash
git clone https://github.com/ningbojian/ProfileReadmeAgent.git
cd ProfileReadmeAgent
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

cd /path/to/你的用户名        # 回到主页仓库目录（config.yaml 所在处）
profile-agent --no-ai --dry-run            # 只打印不写文件
profile-agent --no-ai                      # 不调 AI，直接生成
profile-agent                              # 完整运行（需 ANTHROPIC_API_KEY）
profile-agent --force                      # 忽略增量检测（只重写主页）
profile-agent --fixture /path/to/ProfileReadmeAgent/tests/fixtures --no-ai --dry-run  # 完全离线演示
```

`profile-agent` 与 `python -m profile_agent` 等价，`--config` 可指定其他路径。

> **模板路径规则**：`readme.template` 的默认值 `templates/profile.md.j2` **跟随工具安装目录**——工具升级，模板也一起更新。想用自己改过的模板：把模板文件复制到主页仓库里，并把 `readme.template` 改成那个文件名（默认路径之外的配置永远以本地文件为准，不会被上游覆盖）。

> 本地运行前，先把 `config.yaml` 的 `github.username` 填成你的用户名（Actions 里留空即可，会自动取仓库所有者）。

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
| **运行频率** | Run workflow 对话框里选「定时运行间隔」（不修改 / 每 1、3、5、7 天） | 选了天数，本轮结束时自动把定时改为每 N 天一次（需 WORKFLOW_PAT）；选「不修改」不影响本轮回调 |
| **定时开关** | `config.yaml` 里 `schedule.enabled: false` | 定时运行第一步就结束（不装依赖不调 API）；手动触发不受影响。更彻底可在 Actions 页面点 "Disable workflow" |
| **AI 调用** | 增量检测 + `ai.max_age_days` | 数据没变化完全不调 API；简介 14 天内不重新生成（可调） |

### 定时是怎么运作的（间隔门）

默认机制是「**每天唤醒 + 间隔门**」：cron 每天 02:23 UTC（北京 10:23）唤醒一次，第一步读**调用文件**（`.github/workflows/update-profile.yml`）里的两行标记决定今天是否真跑——

```yaml
# interval_days: 3       ← 每 N 天跑一次（Run workflow 下拉改写）
# interval_anchor: 2026-10-22  ← 间隔锚点日期（改间隔时自动重置为当天）
```

- **改间隔**：Run workflow → 「定时运行间隔」下拉选「每 3 天」之类 → 本轮结束时自动改写调用文件里的上面两行并提交（需 WORKFLOW_PAT）；没配 `WORKFLOW_PAT` 时会明确提示「改动未保存」，周期保持不变，其余更新照常；选「不修改」只跑本轮不改周期；
- 没到日子的唤醒会在第一步直接结束（绿色秒过，日志写明「非每 N 天的运行日」），不装依赖不调 API；
- 想完全手动控制：直接删掉这两行标记，恢复纯 cron 语义（调用文件改过名的话请保留标记，工具靠它定位文件）。

### cron 对照表（手动改 cron 时参考）

| 频率 | cron（UTC） | 北京时间 |
| --- | --- | --- |
| 每天 | `23 2 * * *` | 每天 10:23 |
| 每周一 | `23 2 * * 1` | 周一 10:23 |
| 每月 1 号 | `23 2 1 * *` | 每月 1 日 10:23 |

> GitHub 的 cron 按 UTC 计，且高峰期可能有几分钟延迟；避开整点（如 23 分）更稳定。`*/N` 之类的写法在 GitHub cron 里语义与直觉不同（每月 1 号重置），想每 N 天一次请用上面的间隔门，别用 `*/N`。

## 运行明细与描述补位

**每次运行都有逐仓库明细**：运行完成后打开该次运行页面，Summary 里有一张表，逐仓库列出这次做了什么（主页描述补位：已是最新/已生成补位文案/生成失败）。

**主页「暂无描述」自动补位**：项目列表里 description 为空的仓库，会用它自己 README 的内容生成一句 ≤80 字的中文短描述顶上（不再显示「暂无描述」）。不想要这个行为：`ai.fill_empty_descriptions: false`。profile 仓库自身（`用户名/用户名`）始终除外——它的 README 是工具自己的输出，若也补位会自引用导致每轮重生成、反复开 PR 烧额度。

## 配置 WORKFLOW_PAT（模式 B / 整页接管预览 / 描述写回 / 改 workflow 必配）

GitHub 有个硬性限制——Actions 内置的 `GITHUB_TOKEN` 无权修改 `.github/workflows/` 下的文件，也无权替你向其他仓库推送分支/开 PR，所以这两件事都需要你自己的令牌：

1. 右上角头像 → Settings → Developer settings → Personal access tokens。这里有两种令牌，**选 `Tokens (classic)`**：
   - *Fine-grained tokens*（新）：按仓库、按权限项精细授权。**注意有个隐蔽坑**：如果生成时选了 "Only select repositories"，令牌对**未勾选的公开仓库也会返回 404**（表现为模式 B 状态行「读取失败 N 仓」，其余仓库一切正常）——本工具读写的是你全部公开仓库，不建议用 fine-grained；
   - *Tokens (classic)*（经典）：按「权限域」授权，对全部仓库（含之后新建的）生效。本工具只需要它。
2. **Generate new token (classic)**，勾选 **`repo`** 和 **`workflow`** 两项权限（`repo` 负责读写仓库内容，`workflow` 负责修改 workflow 文件；令牌还要替你把 README/统计提交推上去，所以不能只勾 workflow），有效期自选
3. 回到本仓库 → Settings → Secrets and variables → Actions → **New repository secret**，名称填 `WORKFLOW_PAT`，值粘贴刚生成的 token

> **换令牌后必须重新粘贴进 secret**：secret 存的是你粘贴那一刻的值，在 PAT 页面重建/续期/改权限都**不会**自动同步到 `WORKFLOW_PAT`——换了令牌却忘了更新 secret，是「明明配了 classic PAT 却还是 404」的第一大原因。

> **不配置也完全能用**：overview、统计、定时更新都不受影响；手写 README 默认 auto 模式只插 AI 简介块，本来就不需要 PAT。需要 PAT 的是：模式 B 推送/开 PR、`readme.mode: full` 的整页接管预览、仓库描述写回——缺 PAT 时这三处跳过并明确提示，改了 workflow 文件（如 cron）也推不上去。

## 各仓库 README 自动更新（模式 B，默认关闭）

让每个仓库的 README 也跟着代码一起长：**某仓库有新提交 → AI 更新它的 README → 开 PR 给你审 → 你的个人主页 Overview 同轮吸收最新内容**。AI 生成前会先读取仓库现有的 README 全文作为修订基础——保留仍然准确的表述、把新变更融进去，而不是只看几条提交凭空另写。写回只动 README 里一对标记之间的「托管区」，标记外你手写的内容一个字节都不会碰。

### 三结局口诀（PR 开出来后只有三种处理，怎么选都不会坏）

- **合并** → 新 README 落进仓库；主页只有缺描述仓库的一句话简介会随后吸收（下一轮），统计与其余内容不受影响；
- **关闭** → 拒绝这批文案：主页将不再包含它（本轮或下一轮生效），同批内容不会自动重开，下次新提交会把新批次连同旧内容一起带回来；
- **先放着** → 完全没问题：PR 留着以后合，主页照样是新鲜的（期间有新批次时 PR 会被刷新，合并前请重读 diff）。

### 怎么启用

1. 需要已配置 `WORKFLOW_PAT` secret（classic PAT，`repo` + `workflow` 两个 scope，见上文「配置 WORKFLOW_PAT」）——没有它工具无法替你推送分支/开 PR，会静默跳过模式 B，其余功能不受影响。
2. `config.yaml` 打开开关。**灰度建议先放 1 个仓库试跑**：

   ```yaml
   repo_readmes:
     enabled: true
     include: ["你的某个仓库名"] # 空 = 全部仓库；先放 1 个灰度，稳了再清空
   ```

3. 手动触发 Run workflow 时有四个控件（全部不选/不勾 = 与定时跑完全一样的默认行为）：
   - **本次强制重跑**（单选下拉，四选一互斥）：
     - *不强制（默认）*：只更新有变化的内容；
     - *强制重写主页*：忽略「有变化才更新」，立即重建主页 overview（AI 简介/补位文案仍按缓存复用，不会额外调模型；想重新生成简介请改 `ai.prompt_version`）；
     - *强制重跑各仓库 README*：全部公开仓库 README 全量重跑一遍（AI 生成 → 开 PR → 主页吸收），新仓库/没提交过的仓库也会立即出第一份 README PR；主页仍只按有变化才更新；
     - *两者都强制*：以上两项一起做（消耗最多）。
   - **更新主页 overview**（默认勾选）：不勾 = 本轮完全不动主页；此时即使上面选了「强制重写主页」也会跳过（主页以这个勾选为准）。
   - **等待合并**：开 PR 后暂停等你合并（最多 30 分钟），合并完立刻继续；不勾则直接结束，PR 事后随时可合。
   - **定时运行间隔**：改定时周期（不修改 / 每 1、3、5、7 天），本轮结束后生效。

### 清单在哪、怎么通知你

- 每轮有 open PR 时，本仓库会出现/刷新一个 issue：**「🔔 README 更新待合并清单」**——逐条 PR 链接、新版 README 预览链接、三种处理方式说明，@你 且指派给你（自开的 PR 不会通知自己，这里特意用 @mention 制造通知）。
- 每条 PR 下有一行明细（改了几个文件、增删多少行、提交了什么；有冲突或被分支保护挡住会打 ⚠️ 徽章提醒）。**条目方框可以勾：勾选 = 授权自动合并**——把想采用的条目打上勾（可多选、批量），任务下次运行（定时到点，或在 GitHub 网页保存 README 触发）会自动合并它们，不用逐个手点；某条合并失败会给出原因（如暂有冲突），勾选会在清单刷新时清除，修复后重新勾选即可。
- 状态变化只原地改这个 issue 的 body，**永远不发评论**（不轰炸通知）；PR 全部处理完自动关闭（关闭本身就是「可以收工」的信号）。
- 本轮确实更新了 Overview（主 README 有新内容）时，收尾会在 issue 里追加「本轮 Overview 已进入 main」终态行；Overview 没变化则不加。
- **请勿关闭本仓库的 Issues**——它是模式 B 的通知通道；关了还有 Step Summary 快照兜底，但收不到 @提醒。

### 主页为什么永远是新鲜的（不用等你合并）

主页的统计与徽章直接来自 GitHub API，从不依赖 PR 合并。唯一会读各仓库 README 的是「缺描述仓库的一句话简介」（补位简介）：对这类仓库，B 跑完 A 立刻用最新可用内容，不跨轮——**已合并**（含等待窗口内合并）→ 读默认分支真源；**仍 open** → 用本轮刚生成的内容（跨轮静默跳过时直接读 PR 分支）；**等待期间被你关闭** → 回退旧文；**本轮没产出** → 旧文。每次运行的 Step Summary 末尾有一行「本轮 A 的 README 输入来源：…」对账，来源一目了然。

### 双语仓库 README（模式 B 同 PR 更新两个文件）

`readme.bilingual: true`（默认）时，模式 B 的同一个 PR 会同时更新 `README.md`（中文）与 `README.en.md`（英文），一次审核两个文件一起合并。要点：

- **只有一种语言的仓库会自动补全另一种**——比如新仓库只有中文 README，下一次真实提交的 PR 会顺带整份生成英文版（不用单独开「翻译 PR」）；反之只有英文 README 也会补中文；
- **两版托管区顶部都有一行切换**（`🇨🇳 中文 · 🇺🇸 English`，两文件互指）——切换行落在托管区内，marker 之外的手写内容仍然一个字节不碰；AI 正文里自带的同款切换行会被剔除，不会重复；
- 英文侧 marker 坏了只跳过英文（中文照常落地），不会整仓卡住；
- AI 没返回合法双语 JSON 时降级单语（只更中文），不会让任务失败；此时若英文文件尚不存在，中文版不会挂切换行（避免死链）；
- 不想维护英文文件：`readme.bilingual: false` 一键回退，行为与旧版一致。

### 不产出也是正常状态（静默路径）

Summary 里每轮必有一行「模式 B 状态」：没变化、首见记基线（新仓库默认等下一次真实提交才出第一份 README；不想等就在「本次强制重跑」里选「强制重跑各仓库 README」立即全量生成）、AI 缺 key 积压、与上次失败输入相同已跳过（新提交或强制重跑才重试）……这些都不是错误。读取失败（如某仓 token 不可见导致 404）、AI 失败、推送失败都被逐仓隔离——一个仓库出问题只会把它自己跳过并在「模式 B 状态」里显示「读取失败 N 仓」，绝不影响其他仓库与主页更新。

## 双语 README（中英互切）

主页自动生成两个文件：`README.md`（中文）与同目录的 `README.en.md`（英文），两文件顶部同一行互指切换（🇨🇳 中文 · 🇺🇸 English），点一下就切。要点：

- AI 一次调用同时产出中英双语内容，成本约为单语的 **1.3 倍**（不是 2 倍）；英文侧永远是英文原文，不会把中文塞进英文文件；
- 仓库描述也本地化，**切到哪版就读哪版的语言**：中文版把英文描述换成 AI 中译，英文版把中文描述换成 AI 英译；两边都按描述原文缓存（描述不变不重译、AI 失败沿用原文且下轮自动重试），中文描述在中文版、英文描述在英文版始终照搬原文；
- 手改英文文件的 custom 区、统计卡等同样按「保留规则」处理（见下节），中英两文件互不干扰；
- 仓库级双语见上文「双语仓库 README」：模式 B 的同一个 PR 同时更新两种语言；
- 不想维护英文文件：`config.yaml` 里 `readme.bilingual: false` 一键回退单文件（已生成的 README.en.md 不再更新，留着或删掉都不影响）。

## 板块开关（只隐藏，不删内容）

`readme.sections` 四个开关控制生成区显示哪几块，默认全开。想精简版面就关开关，**不要删 README 里的内容**（删了下次运行会恢复）：

```yaml
readme:
  sections:
    bio: true      # 简介
    stats: true    # 统计卡与技术标签
    pinned: true   # 📌 置顶项目
    table: true    # 📦 仓库列表
```

- 关掉 `pinned` 但保留 `table` → 原置顶仓库回到仓库列表，不会凭空消失；
- `pinned` 与 `table` 都关 → 不输出任何仓库区块（不会留下孤立分隔线）；
- 只影响工具生成的内容：custom 区、README.en.md 的手写内容照旧保留；
- 想恢复：把对应开关改回 `true`，下一轮运行自动出现。

## 已有手写 README：默认只加一个 AI 简介块

第一次接手一份**手写 README**（还没有 profile-agent 标记）时，默认 `readme.mode: auto`：工具只在你的第一个标题后面插入一个块，**块外内容逐字节保留**——图片、徽章、`<p align="center">`、`<details>`、表格、别的工具生成的排版都不动：

```markdown
<!-- profile-agent:readme:start -->
## 🤖 AI 简介

（AI 写的简介）
<!-- profile-agent:readme:end -->
```

- **只有简介**：没有统计卡、置顶板块、仓库列表。**Pinned 永远不会被修改**，块里也不会重新展示它——工具只通过 GraphQL 读取 Pinned，那是 GitHub 的用户设置，不在 README 里；
- 下一轮只更新块内简介；块外一个字节不改；
- 删掉这个块，下一轮会自动补回（和「删除会恢复」一致）；手改块里的文字，下一轮按 AI 简介恢复（块是托管区）；
- 块 marker 缺一半、多对或顺序颠倒：跳过该文件并在日志警告，绝不猜修复；
- 没有 AI key 或 `readme.sections.bio: false`：不插空块、文件一字节不改，配好后自动补上；
- 双语且 `README.en.md` 也是手写：两个文件各插一个对应语言的块（英文为 `## 🤖 AI Bio`）；`README.en.md` 不存在时不会凭空创建；
- 一对文件里只有一个手写、另一个是机器页：只动手写那个，机器页本轮不动并在日志说明。

标题定位会跳过代码围栏、跨行 HTML 注释和 `<details>` 里的假标题，也认 HTML `<h1>`（含跨行）和 setext 标题；完全没有标题时追加到文件末尾（不插文件头，避免破坏 front matter）。这是纯文本插入，任何 Markdown/HTML/图片格式都原样保留。

> 从模板仓库创建的新仓库是例外：模板说明页按机器文件处理，第一次运行会直接生成主页并替换它，**不会只插一个块**；只有你已经在 `用户名/用户名` 里写了手写 README，才走上面的保护路径。

### 想要整页接管：`readme.mode: full`

如果你就是想把主页交给工具排版，把 `config.yaml` 里的 `readme.mode` 改成 `"full"`。第一次遇到手写 README 时：

1. 新旧内容一起放进一个 PR（分支 `readme-auto/takeover`）。PR 里同时有 `README.md`、`README.en.md`（双语时）和 `data/snapshot.json`；
2. 你原来的手写内容会**逐字完整**放进 custom 标记区，联系方式、社交账号、HTML 都保留；
3. **合并 PR** → 新版面进入主页，下一轮开始正常自动更新；**关闭 PR** → main 完全不变；
4. PR 打开期间，每轮只提示「仍待合并」并跳过——不重新渲染、不调 AI、不改仓库描述；已在清单里打勾的 PR 仍会按你的授权合并（那是你手动授权的动作，不受接管暂停影响）。

需要注意：

- 关闭接管 PR 后，下一轮会重新开一个（main 上没有地方记录「已拒绝」）。想彻底不接管，把 `readme.mode` 改回 `auto`；
- 快照必须和 README 一起合并（已经打包在同一个 PR 里），否则下一轮会再次进入接管；
- PR 打开期间 main 又前进了，合并可能冲突：关闭重开，或手动解决冲突；**在分支上手动解决过冲突后，合并前别用「强制重写主页」**——它会按最新 main 重新生成并刷新该 PR，覆盖你在分支上的改动；
- 想主动刷新这个 PR：手动运行时选「强制重写主页」（或本地 `--force`）。工具会以最新 main 为基线重新生成并刷新同一个 PR，**main 仍然不改动**；
- **失败一律不写 main**：推送/开 PR 失败、连 HEAD 都读不到、没有配置 `WORKFLOW_PAT`，本轮都跳过并警告「main 未改动，下一轮自动重试」——要么把 `readme.mode` 改回 `auto`（只插简介块，不需要 PAT），要么配好 `WORKFLOW_PAT` 再整页接管；
- 从 `auto` 切到 `full`：旧简介块会先从 custom 区剥掉，再整页接管，不会出现两段简介；
- 旧的整页接管 PR 还开着时切回 `auto`：本轮跳过并提示——关闭那个 PR（推荐，保留你的排版）或把 `readme.mode` 改回 `full`，避免日后合并旧 PR 把简介块覆盖掉。

## 手写内容语言不匹配（只提醒，不翻译）

- `README.md` 的手写区看起来是英文，或 `README.en.md` 的手写区看起来是中文时，运行日志与 Summary 会出现一条 ⚠️ 提醒；
- **手写内容永远不翻译、不改写**：你可能故意混排、引用英文原句、放代码或 HTML，机器翻译会破坏原意；
- **AI 输出用更严格的判定**：模式 B 的英文版正文、英文简介、仓库描述里夹带成段中文（汉字约两成以上）会直接丢弃——宁可单语或沿用原内容，也不写混杂内容；下一次真实提交、数据变化或强制重跑时重试；
- 想调整内容请自己改文件；工具只提醒，不会替你做决定。

## 仓库描述自动写回（默认关闭）

GitHub 仓库的 description 不会随代码自动变化。打开开关后，某个仓库有新推送时，AI 会基于它的 README、主语言、topics 和当前描述重写一句，并通过 GitHub API 写回：

```yaml
repo_descriptions:
  enabled: true
  include: []   # 空 = 全部公开仓库（profile 自身始终排除）
  exclude: []   # 在 include 之上再排除
```

四道安全阀：

- **第一次见到仓库只记基线**，绝不批量改描述；从下一次真实推送才开始考虑更新；
- **`pushed_at` 没变就不动**：描述跟随代码演进，不按日历轮询；
- **你手动改过描述 → 永久交还控制权**：工具发现当前描述与上次写入值不同，就把该仓库标记为 manual，以后不再自动改。想恢复自动更新：删掉 `data/snapshot.json` 里该仓库的 `repo_descriptions` 条目（下次重新记基线，等新推送后再写）；
- **每轮最多 5 个**：这是**每轮上限**，不是总量上限——没轮到的下轮自动补，Summary 会显示「还有 N 个有推送的仓库本轮未处理」；
- **成功与「无需改动」都算处理完**：生成结果和当前描述一致时不写回，但会记住进度，下一轮不再为它重复调 AI；
- **连续 3 轮失败会暂停**：生成/写回一直不成功的仓库会先停下来，等它下次真实推送再自动恢复，避免长期占名额；

其他要点：

- 需要 `WORKFLOW_PAT`（classic，`repo` scope）；缺 PAT 或缺 AI key 时本轮跳过并提示，配好后自动重试；
- 旧值写在快照的 `previous` 字段，可据此手动回滚；
- 极端情况：描述已写回、但程序在保存快照前中断，下一轮会把这次写入误判为「你手动改过」而永久停手。GitHub 不支持给描述开 PR，这个瞬间无法完全避免；遇到就在快照里删掉该仓库条目恢复；
- GitHub 不支持给描述开 PR，所以这个功能默认关闭，并且只写有推送的仓库、手改即停；
- 整页接管 PR（`readme.mode: full`）打开期间暂停写回，合并或关闭后恢复。

## 置顶项目（跟随 GitHub 原生 Pinned）

置顶不再用 README 里的勾选面板（README 文件视图的勾选框是禁用的静态件，点不动——平台限制），改为**直接跟随 GitHub 个人主页的原生 Pinned**：

- **入口**：你的 GitHub 主页 → 头像下方「Customize your pins」→ 拖选/增删最多 6 个置顶仓库——这是 GitHub 官方界面，勾选即时生效；
- **工具侧**：每次运行经 GraphQL 读取你的 Pinned 列表，按官方顺序渲染成 ⭐「📌 置顶项目」板块，排在 📦 仓库列表之前；其余仓库照常进表格；
- **生效时机**：改完 Pinned 后手动 Run workflow（约半分钟）或等下一次定时轮——**Pinned 变更没有自动触发器**（GitHub 不提供该事件的 webhook）；
- **读取失败**：沿用上一次的置顶名单（防闪断）；首次运行/离线场景回退 `config.yaml` 的 `featured.pin` 种子；
- **旧面板迁移**：从带勾选面板的旧版升级，第一次运行会自动把 README 里的旧面板/指针一次性摘除（custom 区内容不受影响）；
- **工具从不修改 Pinned**：只读取，不写入；auto 模式的简介块里也不展示置顶，改 Pinned 不会触发任何运行；
- 页面结构不变：置顶在前、全量表格在后，只是置顶来源换成了官方界面。

## 保存即触发与成本

在 GitHub 网页上保存 `README.md` / `README.en.md`（点编辑图标 → 改 → Commit）会自动触发一次运行，几秒内生效——不用等定时、不用手动 Run workflow。成本放心：

- **只改 README、数据没变** → 增量门早退：**0 次 AI 调用、0 次写入**；
- **升级首跑摘除旧面板** → 只重写 README（一次性迁移），**0 次 AI**；改 GitHub 主页 Pinned 本身不触发任何运行（见上节生效时机）；
- **数据真变** → 与定时轮同价，push 只是提前到秒级；
- **防循环**：工具自己的提交带 `[skip ci]`，且触发只圈两个 README 文件，不会自己触发自己。

> **旧副本升级**：早于「可复用 workflow」上线的仓库（无论是 fork 还是 Use this template 建的），旧的 workflow 文件是快照，上游改动不会自动进来——按上文「从旧模板副本迁移」把调用文件换掉即可，之后自动收更新。

## 交互速查（每个控件一行后果）

| 你做的事 | 结果 | 何时生效 | 不会发生什么 |
| --- | --- | --- | --- |
| 在 GitHub 主页「Customize your pins」增删/排序 | 该仓库按官方顺序进「📌 置顶项目」 | 下次运行（手动 Run workflow 或定时轮；Pinned 变更不会自动触发） | 不改任何仓库内容、不合并 PR；README 里不再有勾选面板 |
| 清单勾选 PR 条目 | 授权自动合并 | 任务下次运行（定时到点或保存 README 触发） | 不会立即合并（想立即就手动点 Merge） |
| 手动点 Merge | 新 README 落进该仓库 | 立即（GitHub 完成） | 工具不会为同内容重开 PR |
| 点 Close | 拒绝这批文案 | 立即 | 同批内容不会自动重来（新提交才带新批次） |
| 接管 PR 打开时普通运行 | 跳过整轮并提示「仍待合并」 | — | 不重渲染、不调 AI、不改仓库描述；已勾选的清单 PR 仍会按授权合并 |
| 接管 PR 打开时选「强制重写主页」 | 按最新 main 重新生成并刷新该 PR | 立即 | 不写 main；分支上的手动改动会被覆盖 |
| 放着不动 | 无 | — | 完全没问题，主页照常新鲜 |
| 手改生成区文字 | 微调保留 | 下次运行后仍在 | 不会被整篇覆盖；同一行数据也变时以数据为准 |
| 手删生成区内容 | 下次运行恢复 | 下一轮 | 不会真的删掉（想隐藏请改 config 开关） |

## README 保留规则（哪些会保留、哪些会恢复）

改主页 README 不会整篇被覆盖，规则一句话版：**生成区随数据更新、手工微调会保留、删除会恢复；custom 内容永远保留**。展开：

- **生成区手工微调保留**：问候语、简介措辞等小改动，下次运行后原样在；与数据变化冲突的行以数据为准（比如你手改了 star 数，下轮会改回真实值）；
- **删除会恢复**：删掉生成区内容（表格行、板块）会在下次运行恢复——想隐藏内容请改 config 开关，别删文件；
- **custom 区永远保留**：custom 标记（`profile-agent:custom`）之间的自由文字，永远不被覆盖；旧版置顶面板会在升级首跑被一次性摘除（不适用「删除会恢复」——它已整体下线）；
- **手写 README 默认只加块**：第一次接手手写文件时（`readme.mode: auto`），只在第一个标题后插一个 AI 简介块，其余内容逐字节不动；`readme.mode: full` 才把整份内容放进 custom 区并先开预览 PR（见「已有手写 README」）；
- **Pinned 永远不改**：工具只读取 GitHub 原生 Pinned（GraphQL），从不写入；改 Pinned 也不会触发运行；
- **升级首跑**：从旧版升级的第一次运行会以新渲染为准重排一次（一次性，含旧面板摘除），之后手改永久保留。

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
  count: 0                 # 上主页的项目数上限；0 = 全部
  min_stars: 0             # 入选最低 star
  recency_days: 0          # 只考虑最近 N 天有 push 的仓库；0 = 不限（要「真·全部」保持 0）
  sort_by: "score"         # score | stars | updated
  score_stars_weight: 1.0
  score_recency_weight: 0.5
  pin: []                  # 兜底种子：仅当无法读取原生 Pinned（本地无 token、GraphQL 失败且无快照）时生效；
                           # 正常跟随 GitHub 主页 Pinned，如 ["you/cool-tool"]
readme:
  enabled: true            # false = 不更新 overview
  path: "README.md"
  template: "templates/profile.md.j2"
  cards:
    theme: "github_dark"   # github_dark | github_light 等
    show: ["stats", "top_langs", "streak"]  # 三种卡片任选
  include_update_time: true
  visitors_badge: false   # true = 主页嵌入「主页访问量」计数徽章（komarev.com 免费第三方服务，页面渲染一次 +1；GitHub 不提供官方的主页访问量 API，这是业界通行做法）
  bilingual: true         # true = 同时生成 README.en.md，两文件顶部互链切换
  mode: "auto"            # auto = 手写 README 只插/更新一个 AI 简介块，其余字节不动（默认）
                          # full = 整页接管（有 WORKFLOW_PAT 先开预览 PR，没有则跳过本轮）
  sections:               # 板块开关：只隐藏生成区，不动手写内容
    bio: true             # 简介
    stats: true           # 统计卡与技术标签
    pinned: true          # 📌 置顶项目（关掉后仓库回到列表）
    table: true           # 📦 仓库列表（与 pinned 都关 → 不输出仓库区块）
ai:
  enabled: true
  model: "claude-opus-5-5" # 降本可换 "claude-haiku-5-5"
  max_repos: 15            # 最多把多少仓库喂给 AI
  max_age_days: 14         # 简介最长复用天数
  prompt_version: 2        # 改了 prompt 后 +1 可强制重新生成
  fill_empty_descriptions: true  # 项目列表里 description 为空时，用其 README 生成短描述补位
repo_readmes:
  enabled: false           # true = 各仓库 README 托管区自动更新（需 WORKFLOW_PAT；默认关闭）
  include: []              # 空 = 全部仓库；灰度建议先放 1 个，如 ["shiyuan"]
  exclude: []              # 在 include 之上再排除
  max_diff_chars: 6000     # 喂给模型的 diff 字符上限
  prompt_version: 2        # 改了模式 B 的 prompt 后 +1，强制重新生成
  pr_branch: "readme-auto/update"  # PR 分支名；主页从这里读未合并内容
repo_descriptions:
  enabled: false           # true = 新推送时自动重写 GitHub 仓库描述（需 WORKFLOW_PAT；默认关闭）
  include: []              # 空 = 全部公开仓库；profile 自身始终排除
  exclude: []              # 在 include 之上再排除
```

改完 `ai.prompt_version` 后下一轮运行就会重新生成简介（不必额外勾强制重跑——强制重写主页不会重生成 AI 简介）。

## 常见问题

- **想关掉自动更新**：`config.yaml` 里 `schedule.enabled: false` 提交即可；或在 Actions 页面 Disable workflow。
- **想让所有仓库立刻出/更新 README PR**：Run workflow 的「本次强制重跑」选 **两者都强制**（主页 + 仓库 README 全量）或 **强制重跑各仓库 README**（只全量重跑仓库 README，主页仍按变化更新）——全部公开仓库（profile 自身除外）都会重新生成并开 PR，不选则只处理有新代码提交的仓库。
- **某仓「读取失败 404」但它明明是公开仓库**：几乎都是 `WORKFLOW_PAT` 的问题。按顺序查：① secret 里的值是不是 **classic PAT**（`ghp_` 开头）——fine-grained PAT 若选了 "Only select repositories"，对未勾选的公开仓一律 404；② 换了新令牌后**有没有把新值重新粘进 secret**（在 PAT 页面重建/续期不会自动同步到 secret，这是最常见的坑）；③ 令牌是否已过期。一个仓读取失败只跳过它自己，不影响其他仓库。
- **AI 简介没生成**：检查 secret 名称是否为 `ANTHROPIC_API_KEY`；工具在 AI 失败时会保留旧简介，不会让任务失败。
- **英文版里仓库描述还是中文**：三种情况——① 描述本身不含中文（纯英文等非中文描述直接照搬、不翻译）；② AI 翻译失败或超时（本轮沿用原文，下轮自动重试）；③ secret 里没配 `ANTHROPIC_API_KEY`（AI 能力整体降级）。
- **手改 README 会被覆盖吗**：不会整篇覆盖。手写页默认 `auto`：工具只在第一个标题后插/更新一个 AI 简介块，其余内容逐字节不动；块被删了会补回，块内文字是托管区。整页生成的文件（模板页 / `mode: full`）里，生成区随数据更新、手工微调保留（与数据冲突处以数据为准；删除生成区内容会在下次恢复，想隐藏请改 config），custom 标记之间的内容永远保留。
- **我用了别的工具、README 排版很复杂（HTML/图片/表格），会被魔改吗**：不会。`auto` 模式是纯文本插入——只在标题后加一个块，图片、徽章、居中 `<p>`、`<details>`、表格、代码块都原样保留；marker 损坏时宁可不写也不猜修复。只有你显式设 `readme.mode: full` 才会整页重排（而且先开预览 PR）。
- **只加一个托管块，我的 Pinned 会变吗**：不会。Pinned 是 GitHub 用户设置，工具只读不写；简介块里也不展示它。
- **数据是旧的**：Actions 手动触发 +「本次强制重跑」选「强制重写主页」即可刷新。
- **模式 B 没跑**：看 Summary 里的「模式 B 状态」行——最常见是没配 `WORKFLOW_PAT`（无法推送/开 PR，配好自动启用）或 `repo_readmes.enabled` 还是 false；选了「强制重跑各仓库 README」但 config 里是 false 也会给冲突警告。
- **@提醒没收到**：确认本仓库 Issues 没被关闭、GitHub 通知设置里勾了 Issues；清单入口固定在本仓「🔔 README 更新待合并清单」issue + 运行页 Summary 快照。
- **整页接管没开 PR**：整页接管（`readme.mode: full`）需要 classic `WORKFLOW_PAT`（fine-grained 选 "Only select repositories" 会对主页仓库 404）。没有 PAT、读不到 HEAD 或推送失败时都**不会**写 main——Summary 写明原因，下一轮自动重试；手写页默认 `auto` 模式不需要 PR。
- **仓库描述没更新**：按顺序查：① `repo_descriptions.enabled` 是否 true（必须是真正的 true/false，带引号的 `"false"` 会直接报配置错误）；② 该仓库是否第一次见到（只记基线，等下一条推送）；③ 是否在 GitHub 上手动改过描述（会永久跳过，删掉快照里对应条目可恢复）；④ 是否超过每轮 5 个的上限（其余下轮继续）；⑤ 是否连续 3 轮失败已暂停（下次推送后自动恢复）；⑥ 是否缺 `WORKFLOW_PAT` 或 AI key。
- **板块开关关了，手写内容会丢吗**：不会。`readme.sections` 只隐藏工具生成的简介/统计/置顶/列表；custom 区与 README.en.md 的手写内容不受影响。
- **把本工具分享给别人**：对方按「安装方式与版本更新」里两种方式之一建库即可；建好后跟着 `@v1` 走，工具更新自动生效，不需要对方同步代码。

## 开发

```bash
pip install -e ".[dev]"
pytest            # 全部离线运行，不碰网络
```

代码结构见 `src/profile_agent/`：`github_client`（取数）→ `aggregate`（过滤/统计/精选/指纹）→ `bio`（AI 简介）→ `render`（模板）→ `cli`（编排与增量门）。
