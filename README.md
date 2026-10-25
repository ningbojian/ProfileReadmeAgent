<!-- profile-agent 模板说明：首次运行工作流后，本文件会被自动生成的主页 README 整体替换。 -->

# ProfileReadmeAgent

自动维护 GitHub 个人主页 README：仓库统计、置顶项目、全部仓库列表、AI 简介，中英双语，定时增量更新。

> 效果示例：[ningbojian 的主页](https://github.com/ningbojian)——由本工具生成并自动维护。

> [!TIP]
> 用法只有一步：点 **Use this template → Create a new repository**，新仓库命名为 `你的用户名/你的用户名`（保持 **Public**），然后在 Actions 跑一次 **Update Profile README**。首次运行会直接用生成的主页替换本说明页，不会弹 PR。
>
> 建好之后**工具更新自动生效**：你的仓库只保留一份约 35 行的 workflow，执行逻辑按 `@v1` 标签引用本仓库，我发新版本你不用做任何事。

已经有 `你的用户名/你的用户名` 仓库？**先别删**：在它的 Settings → Rename 改成临时名字（如 `profile-backup`，历史与原 README 都保留），再从模板创建同名新仓库，把旧 README 全文粘贴进去再跑第一次——手写页默认只会在第一个标题后加一个 AI 简介块，其余内容逐字节保留。

## 它能做什么

大多数同类工具只生成统计卡，或者生成一次就结束；这个会真的替你写内容，并且持续更新。

- 🤖 **AI 写内容**：简介、仓库的一句话描述、各仓库 README 都能交给 AI；数据没变就不调用模型、不产生提交
- 📊 **仓库统计**：公开仓库数、Followers、总 Star，以及 github-readme-stats 可视化卡片
- 📌 **置顶项目**：跟随 GitHub 主页的原生 Pinned，按官方顺序展示
- 📦 **全部仓库列表**：按 star 与活跃度排序，带语言徽章与一句话描述
- 🌐 **中英双语**：同时生成 `README.md` 与 `README.en.md`，顶部一键切换
- 🔁 **增量更新**：数据没变不调 AI、不产生提交；默认每 3 天检查一次

> [!NOTE]
> 不配任何 Secret 也能跑：没有 AI key 时简介降级为占位注释，配好后自动补上。手写 README 默认走 `readme.mode: auto`，只在第一个标题后插入/更新一个 AI 简介块，**你的排版、图片、徽章一个字节都不动**；想整页交给工具排版，把 `readme.mode` 改成 `full`（会先开预览 PR，需要 `WORKFLOW_PAT`）。

## 安装与更新

两条路，效果一样，建好后都自动收工具更新：

| 方式 | 怎么做 |
| --- | --- |
| **Use this template**（推荐） | 上面的绿色按钮，一步建好 |
| **手动新建仓库** | 新建 `你的用户名/你的用户名`（Public），把 [`update-profile.yml`](.github/workflows/update-profile.yml) 复制到你的 `.github/workflows/`，再复制一份 [`config.yaml`](config.yaml) |

工具代码不在你的仓库里：全部逻辑在 [可复用 workflow](.github/workflows/reusable-update-profile.yml)，你的调用文件按版本标签引用它：

```yaml
uses: ningbojian/ProfileReadmeAgent/.github/workflows/reusable-update-profile.yml@v1
```

- `@v1`（默认）：跟随 v1 系列最新版，修 bug / 小功能自动生效；
- `@v1.0.0`：固定版本，永远不变；`@main`：最新开发版。

破坏性变更会发 `v2`，`v1` 继续可用——你不改就一直能跑。旧模板副本（仓库里还留着 `src/` 的那种）按[使用指南](docs/guide.md)里的迁移步骤换掉 workflow 即可，其余文件可留可删。

## 可选 Secret

| 用途 | Secret | 说明 |
| --- | --- | --- |
| AI 简介 / 仓库 README / 描述 | `ANTHROPIC_API_KEY` | Claude 官方 key |
| 其他 AI 服务商 | `AI_API_KEY` | DeepSeek / 千问 / Kimi / MiMo / Ollama 等；在 `config.yaml` 里改 `provider` |
| 推分支、开 PR、改 workflow | `WORKFLOW_PAT` | classic PAT，勾 `repo` + `workflow`；不配则「各仓库 README 自动更新 / `readme.mode: full` 预览 / 描述写回 / 改定时周期」跳过，其余功能不受影响 |

> [!WARNING]
> `WORKFLOW_PAT` 请用 **classic** 令牌。fine-grained 令牌若选了 "Only select repositories"，对未勾选的公开仓库也会返回 404。

## 完整文档

配置项、换模型、改更新频率、控制开销、板块开关、模式 B、常见问题：[docs/guide.md](docs/guide.md)。远期方向：[docs/roadmap.md](docs/roadmap.md)。发版流程：[docs/releasing.md](docs/releasing.md)。

## 本地运行

Actions 里跑不需要本地环境。想在本地跑，先装一次工具：

```bash
git clone https://github.com/ningbojian/ProfileReadmeAgent.git
cd ProfileReadmeAgent
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

cd /path/to/你的用户名        # 回到主页仓库（config.yaml 所在处）
profile-agent --fixture /path/to/ProfileReadmeAgent/tests/fixtures --no-ai --dry-run
```

本地运行前把 `config.yaml` 的 `github.username` 填成你的用户名；Actions 里留空即可（自动取仓库所有者）。

许可证：[MIT](LICENSE)。
