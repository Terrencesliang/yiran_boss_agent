# 招聘工作台交接

## 接手范围

接手本地招聘工作台的后续修改。当前用户反馈集中在「招聘任务」中解析后能否进入确认和预览。先复现最新反馈，再按实际失败点修改。工作台地址为 http://127.0.0.1:8766/#tasks，工作区为 `D:\BOSS直聘`。遵循仓库的 `AGENTS.md`，保留已有修改。

## 当前实现

- 岗位来源为最近一次同步的 BOSS「推荐牛人」岗位菜单。任务页按可核验的岗位 ID 绑定，招聘要求来自当前输入。若刷新后没有岗位，先检查 `/api/workspace` 的 `job_records`，再检查同步记录。
- 工作台目前以 `--model-only` 运行。模型 API 核对本地生成的结构化候选条件，返回接受的选项 ID。服务校验 ID、完整性和原文依据。此模式依赖本地候选条件覆盖，不提供模型自由生成新规则的能力。模型失败会明确报错，当前请求不会生成可执行草稿。
- 正常流程为选择岗位 → 填写要求 → 解析 → 确认筛选条件 → 开始筛选预览 → 查看结果。确认固定岗位、原文及条件哈希，预览用确认后的快照。解析成功与可确认是两个状态，须查看 `plan.blockers`。
- 最近一条用户截图中的指令把「男生」紧接在学历和专业后面。已修复按片段拆分及逗号后有空格时的原文关联。现在能展示其余有效条件，性别条件仍由后端单独阻断。页面会标出阻断原文，并提供「返回修改招聘要求」。

## 最近验证记录

使用真实模型 API，经工作台的带岗位 ID 的接口验证了两条输入。含「男生」版本成功生成模型解析结果，唯一 blocker 为 `unsupported_conditions`。删除该词的版本通过解析，且调用确认接口后快照状态为 `confirmed`。确认后按钮展开、浏览器内完整预览提交及结果页本轮仍需用模拟数据验收。最近一轮未启动筛选，也未发送招呼。

验证用语句可直接使用以下文本。预期识别本科与全日制、计算机类或人工智能、一年以内的人力经验，并将专业和工具实操放在优先项。

> 有计算机大类专业全日制本科（最好是人工智能，计算机科学与技术）、一年以内的人力经验，有 WorkBuddy，Agent 的实操经验（优先）

## 建议接手顺序

1. 先检查或复现用户最新的页面状态。记录模型模式、HTTP 状态、阻断原因、快照状态和当前步骤。用同一份原文排查，避免把解析、确认、预览三个问题混在一起。
2. 在模拟环境做浏览器端验收。覆盖正常路径、性别条件阻断、确认后出现预览按钮，以及修改原文使旧快照失效。若确认失败，检查标签页版本、快照过期、岗位 ID、规则哈希和接口返回。
3. 如果用户确认还需要模型自由解析，另行设计模型输出结构、原文定位及独立验收。当前的模型校验模式无法评测这项能力。保留模型异常可见、优先项独立、关键字段有原文、无法核实时不自动放行等行为。

## 代码入口

| 文件 | 接手时重点查看 |
| --- | --- |
| `recruitment-workbench/app.js` | `planResult`、`taskSteps`、`parseTask`、`changeSnapshot` |
| `recruitment-workbench/task-workflow.js` | 浏览器预览提交和结果展示 |
| `recruitment-workbench/server.py` | `POST /api/plan`、快照确认接口、`--model-only` |
| `recruitment-workbench/integration.py` | 岗位同步、运行队列、预览执行 |
| `boss-agent/src/boss_agent/semantic_task_parser.py` | 模型调用、`verified_choices`、`_confirmed_entries`、原文引用 |
| `boss-agent/src/boss_agent/task_parser.py` | 本地规则及同步用于校验的分句方式 |
| `boss-agent/src/boss_agent/task_snapshot.py` | 快照生命周期、来源校验、确认和执行绑定 |

## 前端结构（2026-11-21 · Tailwind 版）

- 组件库：**Tailwind CSS 3.4**。源码 `tailwind.css`（`@layer base/components/utilities`）+ `tailwind.config.js`（色板、圆角、阴影、关键帧），构建产物 `vendor/app.css`（约 52 KB，本地引入、无 CDN、无运行时依赖，符合 `style-src 'self'` 的 CSP）。
- 设计参考 21st.dev / shadcn：卡片、徽标、工具条、右侧 Sheet；动效参考 ReactBits（Spotlight Card、Count Up、Shiny Button、Aurora），由 `motion.js` 用原生 JS + CSS 实现，**不引入 React**。全部动效遵循 `prefers-reduced-motion`。
- 重新构建（改动 `index.html` / `*.js` 里出现的新类名后必须重建，否则 Tailwind 不会产出对应工具类）：

```powershell
npm i -D tailwindcss@3.4.17
node node_modules/tailwindcss/lib/cli.js -c recruitment-workbench/tailwind.config.js -i recruitment-workbench/tailwind.css -o recruitment-workbench/vendor/app.css --minify
```

- `vendor/app.css` 由 CLI 生成，**不要手改**；改样式改 `tailwind.css` 或配置里的令牌。
- 模板仍使用语义类名（`.panel`/`.stat`/`.pill`/`.review-table`/`.task-steps` 等），在 `@layer components` 里用 `@apply` 定义，因此模板无需铺满工具类，测试钩子也不变。
- **新增静态文件必须登记到 `server.py` 的 `assets` 白名单**，否则 404。`.pill`/`.review-table`/`.review-advanced`/`#review-*`/`#plan-result` 这些选择器被浏览器测试依赖，改名会直接破测试。
- 截图验收：`PHASE6_EVIDENCE_DIR=<目录>` 后运行 `workflow-browser.test.py` 与 `review-browser.test.py`，输出任务页、条件确认、结果页、抽屉等截图（本机 Chromium，headless）。注意入场动效约 0.4s，截图前需等一帧稳定。

## 筛选后打招呼（2026-11-22）

- 入口只在 `#tasks`：筛选预览完成后，同一张「筛选预览」卡片里出现「向符合要求的候选人打招呼」，默认全选、可逐个取消，二次确认后才发送。
- 名单只来自预览结果 `result.shortlist`（即 `contact_preview`：硬性条件 PASS + 证据完整，并已截断到 `target_success_count`）。前端不会自己再找人，也不会在预览之外补人。
- **任务动作必须是 `greet`**：招聘要求里要写明「打招呼 / 联系 N 人」，否则解析出的 `action` 是 `screen`。此时面板只显示引导文案，不给发送按钮（`preview` 结果新增 `task_action` 字段供前端判断）。
- 安全链（不要绕过）：二次确认列出名单并声明不可撤回 → `POST /api/runs {action:"greet", preview, candidates, confirmed:true}` → `enqueue` 拒绝未确认、拒绝并发写操作 → `run_recruitment_contacts` 再核验 `candidates ⊆ contact_preview ∩ PASS`、数量 ≤ `target_success_count`、`data_source` 必须是 BOSS 真实页面，并用 `contact-state.json` 记录已联系，重复点击只会得到 `already_recorded`。
- 新增运行字段：greet 运行带 `preview_id` 与 `candidate_count`，UI 据此显示发送进度、逐人结果（已发送/未发送 + 原因），并在「页面验证 / 岗位上下文变化 / 详情未关闭」三类中断后提供只含未发送者的重试名单。
- 离线演示不显示该面板（demo 模式禁止联系操作）。

## 招聘 SOP（2026-11-22）

1. **选岗位 + 写要求**：招聘要求只写筛选条件即可。是否需要联系候选人由页面开关「筛选完成后自动向符合要求的人打招呼」决定（默认开启）。开关开启时，解析结果的任务动作会被记为 `greet`，人数上限取要求里写的人数，没写则默认 10 人。
2. **解析 → 确认并开始**：底部只有一个操作条——计划检查人数 +「确认并开始筛选」。点一次会依次完成「按需调整人数 → 确认规则快照 → 起跑预览」，只弹一次确认框；「修改条件」在旁边。判定按条件匹配百分比（硬性与优先条件等权，100% 才算符合）。
3. **开始筛选预览**：任务页可见进度（已核验 x/y）与「中断本轮」，150 秒无进展会自动中断。
4. **查看结果**：候选人页按匹配度降序列出，整行可点开证据抽屉；不符合的人写明原因（如「未提供实操工具的证据」）。
5. **自动打招呼**：预览完成且存在「证据完整 + 100% 符合」的候选人时，工作台**自动**向名单发送 BOSS 招呼，不需要再点确认。
   - 名单只取 `contact_preview`（PASS + 证据完整 + 截断到目标人数/默认 10 人），不会在名单外补人。
   - 开关关闭时，任务动作为 `screen`，不会联系任何人，也不会显示打招呼区。
   - 发送前后端仍逐人复核当前证据、校验人数上限，`contact-state.json` 保证同一人不会重复打招呼。
   - 中断/失败时只对未发送者提供重试；离线演示模式不会发送。
6. **人工复核**：自动结论可被「人工复核」覆盖，复核记录单独保存。

## 岗位判定规则（2026-11-23）

判定规则按**岗位**配置，存在工作台 `state.job_policies`（key = job_id），在招聘任务页「判定规则」里编辑，解析时套用到任务快照：

| 模式 | 含义 | 结论 |
|---|---|---|
| `strict_all` | 全部条件（核心+加分）满足才符合（旧默认） | 任一未满足/未核实 → 不符合/待核实 |
| `strict_core` | 核心要求全满足，加分项越多越靠前 | 核心全 PASS → 符合；核心有未核实 → 待核实；否则不符合 |
| `weighted` | 核心可部分满足，加分项补足 | 核心满足比例 ≥ min_core_percent 且 加分项 ≥ min_bonus_count → 符合 |

- 字段：`RecruitmentTask.decision_mode / min_core_percent / min_bonus_count`，随快照进入报告（`decision_mode` 等写在报告顶层）。
- 候选行新增 `core_pass/core_total/bonus_pass/bonus_total`，抽屉里显示「核心 x/y · 加分 a/b」和本次判定口径。
- 改规则后会自动重新解析；已确认的旧快照不受影响。
- 保存接口：`POST /api/job-policy`；工作台概览返回 `job_policies`。

## 岗位匹配口径（2026-11-22）

- 判定改为**条件匹配百分比**：硬性条件与优先条件等权，每满足一项计一份，`match_percent = round(100 × 满足数 / 总数)`（顶层条件计数，OR 组按 1 项算）。
- 阈值：**100% 才算符合**；100% 以下若存在未核实项 → 待核实，若存在明确不符合项 → 不符合。
- 报告字段：候选行新增 `match_percent` / `matched_conditions` / `total_conditions`；报告顶层新增 `strict_all_conditions`，历史报告没有该字段，前端据此隐藏新口径说明、沿用旧文案。
- 前端：候选人表新增「匹配度」列（80% · 4/5），抽屉徽标旁显示匹配度，并注明「硬性与优先条件等权，100% 才算符合，未核实项按未满足计入」。
- 开关：`boss-agent/src/boss_agent/recommendation.py` 的 `REQUIRE_ALL_CONDITIONS`（True = 优先条件参与判定）。改阈值只需调 `rank_recruitment_preview` 里 100% 的判定。
- 注意：历史运行不会重算，要按新口径看结果必须重新跑一轮筛选。

## 运行状态、进度与中断（2026-11-22）

- 预览每核验一位候选人就回写一次 `run.progress`（`phase`/`checked`/`budget`/`candidate`）和 `heartbeat`；前端每 2.5s 轮询渲染进度条与「已用」时长。
- 看门狗：`RUN_STALL_SECONDS = 150`，running 超过该时长没有心跳就自动标为 `interrupted`（同一进程内）。服务启动时，上一次进程遗留的 `queued`/`running` 一律标为中断并写入「上次运行在服务退出时中断」。
- 手动中断：`POST /api/runs/<id>/cancel`（需 `X-Workbench: local-ui`，预览运行校验 owner）。标记后 worker 在下一次进度回调抛 `RunCancelled`，`perform` 不覆盖状态；同时 `release` 未被认领的快照预留，避免同一 plan 卡住。
- 服务断开时面板显示「本地服务已断开 · 状态可能已过期 · 最后已知进度」，并隐藏「中断本轮」，不再继续假装在执行。
- **CSP 坑**：`style-src 'self'` 会丢弃 HTML 里的 `style="width:…"`，进度条必须用原生 `<progress value max>`（或 JS 的 CSSOM）来实现，不要写内联样式。
- 测试桩 `workflow-browser.test.py::PausedDemo.operation` 已同步 `progress=None` 形参，改签名时记得一起改。

## 运行与验收

以下命令从工作区根目录运行。日常排查使用旁路演示端口，演示模式使用独立数据目录。

```powershell
.\boss-agent\.venv\Scripts\python.exe .\recruitment-workbench\server.py --port 8767 --demo
node --check recruitment-workbench/app.js
.\boss-agent\.venv\Scripts\python.exe -m pytest boss-agent/tests/test_semantic_task_parser.py boss-agent/tests/test_recruitment_task.py boss-agent/tests/test_phase03_task_intake.py -q
```

工作台浏览器流程测试在 `recruitment-workbench/workflow-browser.test.py`。演示模式不调用真实模型。需要单独验证真实模型时，用工作台当前设置和普通招聘要求，仅调用解析和确认接口。正常启动命令为 `.\boss-agent\.venv\Scripts\python.exe .\recruitment-workbench\server.py --port 8766 --model-only`。

验证期间使用本地或模拟数据完成浏览器流程，真实招聘联系次数为 0。保留历史审计、运行记录和规则快照，避免将候选人个人信息和 API 密钥写入交接材料、截图或测试日志。工作台使用的模型服务配置见 [README](README.md)。
