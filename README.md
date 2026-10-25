<!-- profile-agent 模板说明：首次运行工作流后，本文件会被自动生成的主页 README 整体替换。 -->

# ProfileReadmeAgent

自动维护 GitHub 个人主页 README：仓库统计、置顶项目、全部仓库列表、AI 简介，中英双语，定时增量更新。

> [!TIP]
> 用法只有一步：点 **Use this template → Create a new repository**，新仓库命名为 `你的用户名/你的用户名`（保持 **Public**），然后在 Actions 跑一次 **Update Profile README**。首次运行会直接用生成的主页替换本说明页，不会弹 PR。

## 它能做什么

- 📊 **仓库统计**：公开仓库数、Followers、总 Star，以及 github-readme-stats 可视化卡片
- 📌 **置顶项目**：跟随 GitHub 主页的原生 Pinned，按官方顺序展示
- 📦 **全部仓库列表**：按 star 与活跃度排序，带语言徽章与一句话描述
- 🤖 **AI 简介**：100–150 字中文简介；Claude / DeepSeek / 千问 / Kimi / MiMo / Ollama 都能配
- 🌐 **中英双语**：同时生成 `README.md` 与 `README.en.md`，顶部一键切换
- 🔁 **增量更新**：数据没变不调 AI、不产生提交；默认每 3 天检查一次

> [!NOTE]
> 不配任何 Secret 也能跑：没有 AI key 时简介降级为占位注释，配好后自动补上。已有手写 README 的仓库会在首次运行时先开一个预览 PR，看过再合并。

## 可选 Secret

| 用途 | Secret | 说明 |
| --- | --- | --- |
| AI 简介 / 仓库 README / 描述 | `ANTHROPIC_API_KEY` | Claude 官方 key |
| 其他 AI 服务商 | `AI_API_KEY` | DeepSeek / 千问 / Kimi / MiMo / Ollama 等；在 `config.yaml` 里改 `provider` |
| 推分支、开 PR、改 workflow | `WORKFLOW_PAT` | classic PAT，勾 `repo` + `workflow`；不配则「各仓库 README 自动更新 / 首次接管预览 / 描述写回 / 改定时周期」跳过，其余功能不受影响 |

> [!WARNING]
> `WORKFLOW_PAT` 请用 **classic** 令牌。fine-grained 令牌若选了 "Only select repositories"，对未勾选的公开仓库也会返回 404。

## 完整文档

配置项、换模型、改更新频率、控制开销、板块开关、模式 B、常见问题：[docs/guide.md](docs/guide.md)。远期方向：[docs/roadmap.md](docs/roadmap.md)。

## 本地运行

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
python -m profile_agent --fixture tests/fixtures --no-ai --dry-run
pytest
```

本地运行前把 `config.yaml` 的 `github.username` 填成你的用户名；Actions 里留空即可（自动取仓库所有者）。
