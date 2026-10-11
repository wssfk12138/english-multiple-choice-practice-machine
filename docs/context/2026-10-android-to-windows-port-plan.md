# 2026-10 Android→Windows 移植方案：无损转移、练习计时、每日一学、词汇任务

> 2026-10-11 制定。来源基线：Android 开发树 `main / a8282d7`（含 S0–S4 综合开发与每日一学身份修复）。
> 本方案是 2026-09「Android 横屏→Windows 九批移植」的续篇：把 10 月平板新功能移植到两棵 Windows 树。
> 状态记录见文末「执行状态」，随批更新；本文档与各批代码分别在两棵树本地提交，不推送、不发布。

## 开工登记

| 项目 | 值 |
| --- | --- |
| 开发树（内测） | `E:\EnglishPracticeMachine\Codex\2026-08-02\ba`，main / `014a45a`（2026-09-25 移植遗留项） |
| 发布树（公测） | `E:\EnglishPracticeMachine\Codex\english-practice-machine-github`，main / `8daca1e`（2026-09-25 同批内容） |
| 源树（平板） | `E:\EnglishPracticeMachine\Codex\english-multiple-choice-practice-machine-android`，main / `a8282d7` |
| 门禁 | 后端 `pytest tests`；前端 `pnpm run build`（vue-tsc + vite）；`git diff --check`；每批新增/调整浏览器脚本 |
| 视觉验收 | 由模型用回环 Vite + Playwright/Chromium 完成渲染、交互与浅/深色截图检查；用户可随时自行复核，不作为阻塞项 |
| 完成标准 | 每批：ba 实现 + 门禁通过 + 浏览器验收 + 本地提交；公测按同文件范围带差异适配 + 门禁 + 本地提交。不包含：推送、Release、安装包构建、真机/真供应商验证 |

## 两树差异处理原则（统筹）

- 两树在以下文件存在**有意差异**（保留各自版本，不同步这些行）：`sync.py`（公测用 `LAN_SYNC_PORT`/`LAN_TLS_PORT` 常量，内测硬编码）、`ai_client.py`（内测多 `unprotect_text` 密钥保护）、`practice_snapshots.py`（错误文案/文档字符串小差异）、`routers/vocabulary.py` 与 `services/vocabulary.py`（LAN 策略相关）、前端 `ListeningPlayer.vue` 等约 20 个文件的小段差异。
- 同步方式：每批先在内测树实现并提交，然后把**同批改动区间**人工应用到公测树（不是整文件复制），落在差异附近时保留公测树自身写法。应用后跑同套门禁。
- 每批独立提交，回退按批进行，不硬重置。

## 批次 A：未完成练习无损转移到新题库（先做，风险最小）

**语义**（与平板 `a8282d7` 前的实现一致，源：`frontend/src/platform/android/practice-snapshots.ts` 185–300 行）：题库替换后选「用修正版重新练习」时不再一律清空作答，改为逐题判断——

- 题干/选项/答案在修正前后完全一致 → 原选择（user_answer、option_order）带入新会话；判分键一致，结果不变。
- 被修正过（题干/选项/答案任一变化）→ 该题重置为未作答，计入 reset。
- 仅 `mode='paper'`：已提交篇目整体原样带入（含 is_correct 与提交成绩），`practice_unit_submissions` 行复制到新会话；wrong/wrong_history 模式维持现状（重做本来就是重做）。
- 返回 `{id, carried, reset, carriedSubmissions}` 供前端提示；旧会话置 `abandoned`，数据原样保留。

**比对契约**：`snapshotQuestionUnchanged(saved, current, currentAnswer)` 对比稳定文本——题干 stem、number、question_type、score、metadata、选项（按 `stable_key` 排序，每项比较 key/content/metadata）、标准答案；`option_order` 不参与比对（顺序差异由携带旧顺序解决）。PC 侧 `serialize_question` 同样输出 `stable_key`/content/metadata 且 `user_answer` 存 stable_key（`backend/app/services/questions.py:55-107` 已核实），比对契约可直接对齐。

**目标改动（PC）**：

- `backend/app/services/practice_snapshots.py`：新增 `_stable_text()`、`_snapshot_question_unchanged()`；重写 `choose_snapshot` restart 分支（循环 unit 时按上述语义生成携带行/空行，复制提交行，返回计数）。注意 PC 无 `sync_id` 列，插入语句按 PC 现有列。
- `frontend/src/components/PracticeContentChoice.vue`：按钮描述改为「使用修正后的内容继续作答；未受修正影响的题目会保留你的选择，有改动的题目需重新作答」；成功后按计数显示结果提示（如「已保留 38 题作答，2 题因内容修正需重做；0 篇已提交成绩原样带入」），复用页面现有提示样式。
- `frontend/src/views/PracticeView.vue`：透传并展示计数提示。
- 测试：`tests/test_practice_snapshots.py` 扩展——未变题携带 user_answer/option_order、变更题置空、提交行复制且 wrong_stats 零重复计数、content_revisions 为当前值、旧会话 abandoned 且数据原样、非 paper 模式不携带；既有「restart 后 user_answer 为空」断言更新为新语义。

**验收**：pytest 全绿（新断言先证明旧实现失败再修复通过，至少覆盖上述 6 类）；浏览器打开有内容修正的合成会话，检查弹窗文案、选择后提示与练习页状态（1280×800 浅/深色各一）。

## 批次 B：练习计时改造（S2 语义）

**来源**：平板 `frontend/src/services/practiceTimer.ts`（单调时钟累计、手动/自动/恢复/提交暂停区分、v2 快照无运行起点→恢复为暂停、每 5 秒检查点、提交前冻结、失败不覆盖旧值）+ `PracticeView` 可见性联合判断 + `ListeningPlayer` 暂停协调 + 退出先结算保存。

**桌面语义适配**（唯一设计偏离，其余照平板）：

- 前台信号：平板用「页面可见 + 原生 active」联合；桌面改用 `document.visibilityState`（hidden→自动暂停，恢复可见不自动续计，须明确继续）。窗口失焦（blur）**不**暂停——允许边查资料边做题。
- 检查点持久化：走后端 API 存 `practice_sessions` 计时字段（桌面前后端同机，保持分层），字段与平板检查点语义一致（累计 elapsed、状态、代次）；保存失败提示但不承诺「最多丢 5 秒」。
- 退出答题界面（路由离开）先结算保存再触发词汇退出队列；旧 running 记录只保留有限累计，不补算离开时长；已膨胀累计不自动清零。
- 提交前冻结时间，整卷成功固化、失败按前台条件恢复；分篇提交不结束整卷计时。
- `ListeningPlayer`：自动暂停后回前台不自动播放；手动休息后明确继续才恢复原音频；阻止延迟 autoplay 与切轨绕过。

**目标改动（PC）**：新增 `frontend/src/services/practiceTimer.ts`；改 `frontend/src/views/PracticeView.vue`（替换现有 elapsedMs/startedAt 逻辑）、`frontend/src/components/ListeningPlayer.vue`；`backend/app/services/practice.py` + `backend/app/routers/practice.py` 增加检查点字段与 API（含旧数据一次性迁移：现有 localStorage 形态的计时迁为 v2 快照，无运行起点按恢复暂停处理）。

**验收**：pytest 检查点/冻结/恢复；浏览器模拟可见性切换、离开/返回、提交成功/失败两条路径、听力暂停协调、浅/深色计时提示不溢出。

## 批次 C：每日一学

**来源**：平板 `frontend/src/services/dailyStudy.ts`（目录冻结、详情缓存≤5、相邻预取≤2、导航窗口 41、每范围续看≤200 滚动位、≤8 范围、迟到回调与清理）+ `DailyStudyView.vue`（方案 A 双栏卡）+ `platform/android/vocabulary.ts` 的 `dailyStudyCatalog/dailyStudyStatus`。

**目标改动（PC）**：

- 后端：`backend/app/services/daily_study.py`（新）+ `routers/vocabulary.py`（或独立 router）暴露目录/状态端点。目录语义对齐平板：按当前题库的词汇范围生成 `scope_key + revision`，成员为 `{id, term, identity}`；**身份用 PC 自有键**（`id` + `normalized_term` 唯一键组合），不引入 `daily_study_identity` 列（那是平板生产库无 sync_id 的特有修复；PC 表结构不同，LAN 载荷本就不含此身份）。内容版本变化只更新详情不重排目录。
- 前端：`frontend/src/services/dailyStudy.ts` 从平板移植（Storage 语义一致）；新增 `frontend/src/views/DailyStudyView.vue` 桌面宽屏双栏布局（左目录右详情；平板的左右滑动改为键盘/按钮为主，触摸滑动保留）；`router.ts` 注册路由；首页/词汇页入口按平板位置对齐。
- 只读：不新增生成请求、不改复习字段。

**验收**：pytest 目录/身份/续看/空态；浏览器宽屏双栏渲染、导航边界、续看恢复、浅/深色、无横向溢出。

## 批次 D：词汇翻译/补缺任务调度（S1+S3 语义，最后做）

**来源**：平板 `platform/android/vocabulary-request-{runner,store,result,recovery,config}.ts`（队列、额度、重试、停止门控、截断分类）、`vocabulary-network-policy.ts`（FastAI 域名精确匹配，连接 15s/读取空闲 180s/总期限 240s；其他 15/60/240）、`vocabulary-native-http.ts`（一次性请求、禁重试/重定向、重复调用拒绝、4MiB 上限、安全错误脱敏）。

**桌面适配**：请求由 Python 后端发出（httpx），无原生桥——总期限用「连接 15s + 读取 180s + 整体 240s 截止」实现；禁自动重试与重定向；重复任务拒绝；响应上限 4MiB；安全错误不带 URL/Key/供应商正文。

- 额度：两类任务共享本机额度——10 分钟窗口最多 4 次请求、翻译每批最多 8 词、业务等待边界 240 秒（桌面单用户，按时间窗计数即可，无设备槽）。
- 停止语义：未知结果保持停止不自动重发；仅用户点击重试入口才按对应 ID 与任务类型重试（可能重复计费，需提示）；四种不完整完成信号统一 `vocabulary_response_truncated`，调度与持久原因独立为 `truncated`；截断不当有效词汇、不降级补发。
- 隐私：主翻译输入仅 entryId/term/sentence，补缺另加 commonMeaning；不发送学习次数、错误次数、复习日期或用户笔记。
- 目标改动：`backend/app/services/vocabulary_tasks.py`（新，调度+额度+分类）；`backend/app/services/ai_client.py`（请求策略参数化，保留公测树无 `unprotect_text` 的形态）；`backend/app/routers/vocabulary.py` 任务端点；`frontend/src/views/VocabularyView.vue` 与相关组件入口/额度/重试/停止原因展示。

**验收**：pytest 用本机回环假供应商覆盖——额度拒绝、240s 截止、502/非法 JSON/字段错误/保存失败/unknown 分类、截断四种、重试只发一次；浏览器验收入口、重试提示与停止原因显示。

## 执行顺序与提交计划

1. A → B：两者都动 `PracticeView.vue`，A 先行、B 叠加，避免同文件两轮返工。
2. C 独立新文件为主，与 A/B 无冲突，可紧随其后。
3. D 最后（面最大、涉计费语义）。
4. 每批完成：内测树提交（`feat(win-internal): ...`）→ 同文件范围应用公测树 → 公测门禁 → 公测提交（`feat: ...`）。全部批次结束后统一更新本文档执行状态并通知。

## 边界与不做的事

- 不移植平板竖屏专用页面；不引入 `sync_id`/`daily_study_identity` 到 PC 表结构。
- 不动 LAN 协议、ESQ、更新通道；每日一学身份与续看位置不入 LAN 出站载荷。
- 不调用真实付费供应商；请求策略验证一律用本机回环假服务器。
- 不推送、不发布、不构建安装包；Windows 独立版本号/签名/发布节奏不变。
- 临时验收截图与探针脚本不入库；`git diff --check` 保持干净。

## 执行状态（随批更新）

- 2026-10-11 方案制定；同日前置工作：Android 树身份修复已提交 `a8282d7`、文档 `7b29bc3`。
- 批次 A：已完成（内测树）。后端 `choose_snapshot` restart 分支按携带/重置/提交带入重写并返回计数；前端弹窗文案与结果提示条（`.transfer-notice`）落地；`tests/test_practice_snapshots.py` 10/10、全量 309 过/1 既有跳过；浏览器验收 `frontend/tests/practice-content-transfer.mjs`（真实 PracticeView 挂载 + 合成会话，浅/深色 1280×800 截图于 work-logs/batchA-shots）。公测树已同步。
- 批次 B：已完成（内测树）。`services/practiceTimer.ts` 状态机原样移植（单调时钟、暂停原因、v2 快照、legacy 迁移）；PracticeView 换用 performance.now() + 每 5 秒检查点 + 可见性自动暂停/恢复 + 路由离开结算（abandon-if-empty）+ 提交前冻结（整卷/分篇，失败按前台条件恢复）；休息遮罩仅手动暂停显示；ListeningPlayer 对齐平板防绕过语义（暂停期禁播放、清延迟自动播放、切轨不排队）。状态机测试 `practice-timer.mjs` 6/6、浏览器验收 `practice-timer-ui.mjs` 四场景通过、全量 309 过、构建过。桌面适配两处：检查点沿用前端本地存储（桌面单机可靠，未加后端字段）；自动暂停回前台自动续计、手动休息需显式继续（与平板一致，修正方案初稿中"恢复可见不自动续计"的表述）。公测树已同步。
- 批次 C：已完成（内测树）。后端新增 `services/daily_study.py`（目录/状态，browse-order-v1 排序与平板一致）+ `routers/vocabulary.py` 的 `/vocabulary/daily-study/catalog|status` 端点；详情 `_serialize_entry` 注入 `daily_study_identity`（桌面身份 = `nt:<normalized_term>`，不入同步载荷）；前端 `services/dailyStudy.ts` 原样移植会话/目录/缓存/续看，`views/DailyStudyView.vue` 桌面宽屏版（去掉横屏门控与旋转提示，键盘←→/Esc、触摸滑动保留），路由 `/vocabulary/daily-study` 注册，单词本页头新增「每日一学」入口。后端测试 5/5、全量 314 过；浏览器验收 `daily-study-ui.mjs`（目录/导航/键盘/续看持久化/空态/浅深色/无溢出）。公测树已同步。
- 批次 D：已完成（内测树，公测树已同步同批改动并复跑门禁）。新增 `services/vocabulary_tasks.py`：额度台账（`vocabulary_ai_requests` 表，10 分钟窗口 4 次、翻译每批 8 词、原子消耗）、FastAI 精确域名策略（连接 15s/读取空闲 180s，其他 15/60）、总期限 240s 看门狗、4MiB 响应上限、截断分类（`VocabularyTruncatedError`，安全代码 vocabulary_response_truncated）、安全错误文案（不带 URL/Key/供应商正文）。`ai_client.chat_completion` 增加 `vocabulary_execution` 一次性请求通道（不自动重试、不跟随重定向、不 response_format 兼容重发；额度在真实请求发出前原子消耗，测试 mock 请求层不消耗额度）；翻译与补缺 worker 接入额度预检（耗尽时保持排队不标失败）与词汇请求通道；截断/HTTP/解析/校验按原因停止，重试沿用既有入口。后端单测 5/5、全量套件、构建通过；视觉面无新增组件（停止原因经既有 translation_error/enrichment error 展示，入口与重试按钮为 9 月批次已验收形态）。公测树已同步。
