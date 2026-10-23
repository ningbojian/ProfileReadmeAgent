<!-- 这是模板仓库的说明页。当你用 "Use this template" 生成自己的仓库后，
     首次运行工作流会用你自己的主页 README 整体覆盖本文件。 -->

# ProfileReadmeAgent —— 自动维护你的 GitHub 个人主页 README
<!-- profile-agent:readme:start -->
# ProfileReadmeAgent —— 自动维护你的 GitHub 个人主页 README

一键获得会自己更新的个人主页：仓库统计、精选项目卡片、中文 AI 简介、项目介绍/简历素材，定时刷新，数据没变化绝不浪费额度。

**用法只有一步**：点右上角绿色 **Use this template → Create a new repository**，新仓库名改成 `你的用户名/你的用户名`，然后去 Actions 页手动跑一次 **Update Profile README**。约 3 分钟主页就是成品，之后每周自动更新，也可随时勾选指定仓库强制重写。

## 它能做什么

- 📊 **自动统计**：公开仓库数、总 Star、语言分布，加 github-readme-stats 可视化卡片（深色主题）
- ⭐ **全部项目上主页**：每个公开仓库一条展示位（按 star 与活跃度排序，带话题标签）；想只展示一部分，改 `featured` 配置即可
- 🤖 **中文 AI 简介**：100–150 字个人简介；Claude / DeepSeek / 千问 / Kimi / MiMo / 本地 Ollama 都能配
- 📄 **项目介绍与简历素材**：每个仓库一篇中文介绍，存放在 `docs/projects/`，可直接改写进简历
- 🔁 **增量更新**：数据没变化就不调 AI、不产生提交；简介 14 天内不重写，额度花在刀刃上
- ✅ **人工审核**：AI 写的内容一律以 Pull Request 形式提交，你在 Diff 里看过、点 Merge 才公开
- ☑️ **勾选框控制**：Run workflow 对话框里每个公开仓库一个勾选框（带最近推送日期），想重写哪篇就勾哪篇

## 不配置 Secret 也能先跑

不配任何 Secret 也会生成完整主页（统计、精选、数据版描述），AI 部分降级为占位注释，之后配好 key 会自动升级重写。想要 AI 内容时在 Settings → Secrets and variables → Actions 添加：

| 你用什么 | Secret 名 |
| --- | --- |
| Claude 官方 | `ANTHROPIC_API_KEY` |
| 其他服务商（DeepSeek / 千问 / Kimi / MiMo / Ollama…） | `AI_API_KEY` |

要使用勾选框自动同步，还需一个 `WORKFLOW_PAT`（classic PAT，勾选 `repo` 与 `workflow` 两项权限）。

## 完整文档

配置项、换模型、改更新频率、控制开销、常见问题，见 [docs/guide.md](docs/guide.md)；后续规划与待办，见 [docs/roadmap.md](docs/roadmap.md)。
<!-- profile-agent:readme:end -->

一键获得会自己更新的个人主页：仓库统计、精选项目卡片、中文 AI 简介、项目介绍/简历素材，定时刷新，数据没变化绝不浪费额度。

**用法只有一步**：点右上角绿色 **Use this template → Create a new repository**，新仓库名改成 `你的用户名/你的用户名`，然后去 Actions 页手动跑一次 **Update Profile README**。约 3 分钟主页就是成品，之后每周自动更新，也可随时勾选指定仓库强制重写。

## 它能做什么

- 📊 **自动统计**：公开仓库数、总 Star、语言分布，加 github-readme-stats 可视化卡片（深色主题）
- ⭐ **全部项目上主页**：每个公开仓库一条展示位（按 star 与活跃度排序，带话题标签）；想只展示一部分，改 `featured` 配置即可
- 🤖 **中文 AI 简介**：100–150 字个人简介；Claude / DeepSeek / 千问 / Kimi / MiMo / 本地 Ollama 都能配
- 📄 **项目介绍与简历素材**：每个仓库一篇中文介绍，存放在 `docs/projects/`，可直接改写进简历
- 🔁 **增量更新**：数据没变化就不调 AI、不产生提交；简介 14 天内不重写，额度花在刀刃上
- ✅ **人工审核**：AI 写的内容一律以 Pull Request 形式提交，你在 Diff 里看过、点 Merge 才公开
- ☑️ **勾选框控制**：Run workflow 对话框里每个公开仓库一个勾选框（带最近推送日期），想重写哪篇就勾哪篇

## 不配置 Secret 也能先跑

不配任何 Secret 也会生成完整主页（统计、精选、数据版描述），AI 部分降级为占位注释，之后配好 key 会自动升级重写。想要 AI 内容时在 Settings → Secrets and variables → Actions 添加：

| 你用什么 | Secret 名 |
| --- | --- |
| Claude 官方 | `ANTHROPIC_API_KEY` |
| 其他服务商（DeepSeek / 千问 / Kimi / MiMo / Ollama…） | `AI_API_KEY` |

要使用勾选框自动同步，还需一个 `WORKFLOW_PAT`（classic PAT，勾选 `repo` 与 `workflow` 两项权限）。

## 完整文档

配置项、换模型、改更新频率、控制开销、常见问题，见 [docs/guide.md](docs/guide.md)。
