# 发版流程

工具更新通过**移动标签 `v1`**下发：用户调用文件写 `@v1`，每次运行 GitHub 都会解析标签指向的最新提交。所以发版 = 移动标签，用户不用改任何文件。

## 版本约定

| 标签 | 用途 |
| --- | --- |
| `v1.0.0`、`v1.1.0`… | 不可变版本号，供需要固定的用户引用 |
| `v1`、`v2`… | 移动标签，指向该大版本最新版；用户默认写这个 |

- 修 bug、向后兼容的新功能 → 升次版本（`v1.0.0` → `v1.1.0`），移动 `v1`；
- 改坏现有配置语义、用户必须改文件才能升级 → 发新大版本（`v2.0.0`），新建移动标签 `v2`；`v1` 停在最后一个可用版本不动。

## 清单

1. 工作区干净，全量测试通过：

   ```bash
   ./.venv/Scripts/python.exe -m pytest -q        # Windows
   ./.venv/bin/python -m pytest -q                # macOS / Linux
   ```

2. 更新 `CHANGELOG.md`：新版本段落放最上面，写清「用户需要做什么」（通常：什么都不用做）。
3. 提交（中文信息 + `[skip ci]`），推到 `main`。
4. 打不可变版本标签，再移动大版本标签：

   ```bash
   git tag -a v1.1.0 -m "v1.1.0"
   git tag -fa v1 -m "v1 latest"
   git push origin v1.1.0
   git push --force origin v1
   ```

   一步到位：`git push --atomic --force origin main v1.1.0 v1`。

5. 在 GitHub 网页创建 Release（本仓库不使用 gh CLI）：Releases → Draft a new release → 选 `v1.1.0` → 正文贴 CHANGELOG 对应段落 → Publish。移动标签 `v1` 不建 Release。

## 发版前自检（针对 workflow 改动）

- 改了两份 workflow 中任意一份？确认 `tests/test_workflow_yaml.py`、`tests/test_template_repo.py` 全绿——它们锁住输入契约、事件判断、提交步骤与模板 guard。
- 改了 `reusable-update-profile.yml` 的 `workflow_call.inputs` / `secrets`？同步调用文件与 README / guide 的 secret 表；新增 secret 必须同时在 `on.workflow_call.secrets` 声明、在调用文件里按名字转发（跨账号不能用 `secrets: inherit`）。
- 改了默认模板 `templates/profile.md.j2`？确认机器模式首行注释（`<!-- ... profile-agent ... -->`）仍在第一行，模板里没有 custom 标记。
- 真实冒烟（本地替代不了）：在自己的主页仓库把调用文件临时指到 `@main`，手动 Run workflow 一次，确认生成、提交、定时周期改写都正常，再改回 `@v1`。

## 回滚

用户已经拿到坏版本时，最快的止损是把 `v1` 移回上一个好提交：

```bash
git tag -fa v1 <上一个好提交> -m "v1 rollback"
git push --force origin v1
```

用户下一次运行立刻用回旧代码，然后再发修复版本。不可变标签 `v1.x.y` 不要重打。
