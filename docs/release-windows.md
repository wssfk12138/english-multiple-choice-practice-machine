# Windows 发布流程

## 自动流水线（推荐）

`.github/workflows/release-windows.yml` 提供手动触发的发布流水线：
构建前端 → 组装 zip（backend + frontend/dist + 启动脚本 + 文档）→
计算 SHA-256 → 生成 `windows-update.json` → 创建 GitHub Release 并上传
zip 与更新清单两个资产。

发布步骤（每次发布都是对外动作，逐项确认后执行）：

1. 确认 `backend/app/services/updates.py` 的
   `CURRENT_VERSION_NAME` / `CURRENT_VERSION_CODE` 默认值已更新为本次
   版本（应用内"当前版本"以此为准，更新中心据此比较）。
2. 推送包含工作流文件的提交后，在 GitHub → Actions →
   **Windows Release** → Run workflow，填写：
   - `tag`：如 `v0.1.0`；
   - `version_name`：与上一步一致；
   - `version_code`：在上一次基础上递增；
   - `release_notes`：本版更新说明（≤6000 字符）。
3. 发布完成后：
   - 用浏览器访问
     `https://github.com/wssfk12138/english-multiple-choice-practice-machine/releases/latest/download/windows-update.json`
     确认清单可读且字段正确；
   - 下载 zip 抽验：`packageSha256` 与清单一致、解压后 `frontend/dist/index.html`
     存在、按 README 安装可启动；
   - 在旧版本应用里点"检查更新"，确认更新中心能发现新版本。

## 约束

- 更新清单字段是严格模式（多字段/缺字段都会被客户端拒绝），
  由 `backend/app/services/updates.py::validate_manifest` 定义；
- 更新包仅支持 `zip` / `exe` / `msix`，最大 1 GiB；
- `packageUrl` 必须是 GitHub 域名的 HTTPS 直链；
- 仓库 pre-push 钩子要求 `CODEX_APPROVED_RELEASE=1`，
  发布版推送必须走用户批准的流程。

## 手动发布（流水线不可用时）

在本地按同样顺序执行：构建前端（`pnpm run build`）→ 组装并压缩
package → 计算哈希 → 手写 `windows-update.json`（字段见
`validate_manifest`）→ `gh release create <tag> <zip> windows-update.json`。
