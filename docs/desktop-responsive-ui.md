# 电脑端自适应显示与导入、每日一学界面

导入题库首页提供文档、ESQ 两个独立入口，以及最近导入记录。完整记录与智能标注中心位于页头右侧，窄空间下自然换行；入口图标与标题同行。可选答案与音频附件按需展开，已有草稿和题库包预览使用完整工作区宽度。

Windows 原有的多答案附件、文本型 PDF、逐字段校对、AI 辅助、题库导出、逐项年份冲突处理与标注范围/人工锁定能力保留。切换导入工作区保留文件、未保存编辑和冲突决策；离开页面仍遵循既有持久化规则。

每日一学突出显示记忆提示，按固定顺序连续展示已有学习内容。空间充足时均衡双栏，窄窗口和短释义使用单栏。鼠标与触摸拖动时卡片跟随，短拖与首尾越界回弹；保留键盘导航、文本选择、滚动、读取失败重试和续读身份保护。电脑窗口没有 Android 横屏限制。

显示保持浏览器正常基础字号，通过有上下限的标题/间距、容器宽度和栏目切换适应窗口，不缩小整个 1920px 画布。参考 [twbs/rfs](https://github.com/twbs/rfs)、[Bootstrap RFS](https://getbootstrap.com/docs/5.3/getting-started/rfs/) 与 [MDN 容器查询](https://developer.mozilla.org/en-US/docs/Web/CSS/Guides/Containment/Container_queries)，使用现有 CSS，无需额外依赖。

前端构建后可运行 `pnpm run test:daily-study-session`。安装可用的 Playwright 后运行 `pnpm run test:desktop-ui`；也可通过 `PLAYWRIGHT_MODULE` 指定既有 Playwright 模块路径。`DESKTOP_UI_OUTPUT` 可指定证据输出目录，默认位于系统临时目录。界面检查使用真实构建与隔离合成 API，覆盖宽窄窗口、日夜主题、极端内容、工作区状态保留和拖动/重试；不能替代实际安装与用户数据验收。

原有题库整改清单和安全检查不属于本次 UI 修改范围。若实际安装因未审查题库差异拒绝启动，应保留数据并单独核对，不能以删除清单或强制覆盖数据代替验收。
