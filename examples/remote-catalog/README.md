# 远程题库目录发布说明（CET-4/CET-6）

本目录是 GitHub 远程下载题库的**发布模板**。`question-bank-catalog.json`
的格式与局域网内测通道的 `question-bank-catalog.json` 完全一致，
Windows 端（设置 → 远程题库目录）与 Android 端远程题库下载使用同一份清单。

## 包清单（以权威导出为准）

| 包 | 文件 | 内容版本 | 大小 | SHA-256 |
| --- | --- | --- | ---: | --- |
| `wssfk.cet4.complete` | `cet4-complete-v1.2.0.esq` | 1.2.0 | 429,043,776 | `976842e6…03f98` |
| `wssfk.cet6.complete` | `cet6-complete-v1.2.0.esq` | 1.2.0 | 844,861,464 | `f7c5a8b9…81a250` |

权威副本：Windows 开发版题库导出（哈希与局域网通道在发文件逐字节一致）。
发布前必须重新计算 SHA-256 并更新上表与本 JSON。

## 发布步骤（需用户授权后执行）

1. 在 `english-multiple-choice-practice-machine` 仓库创建 Release，
   标签建议 `question-banks-v1.2.0`（内容包与程序版本号分开）。
2. 上传两个 ESQ 文件与本 `question-bank-catalog.json`（共 3 个资产，
   单文件均低于 GitHub 2 GiB 上限）。
3. 若实际标签名与 JSON 中的 `downloadUrl` 不一致，先改 JSON 再上传。
4. 发布后把 JSON 资产的 URL 填入两端"远程题库目录"设置，
   用"检查目录"功能确认清单可读、大小与 SHA-256 校验通过，
   并各做一次真实下载导入验证。
5. 内容更新时递增 `contentVersion` 并新建对应标签的 Release，
   同步更新本目录文件。

## 许可边界

两包为考生回忆整理版真题，`license` 字段沿用局域网通道的
非官方内容声明；`NOASSERTION` 语义与 `docs/bundled-question-banks.md`
（Android 仓）一致：程序代码的 GPL-3.0-only 不适用于题库内容。
