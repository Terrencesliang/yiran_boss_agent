# 招聘工作台

## 离线演示

在 `D:\BOSS直聘` 启动，打开 [离线招聘工作台](http://127.0.0.1:8767/)。

```powershell
.\.venv\Scripts\python.exe .\recruitment-workbench\server.py --demo --port 8767
```

演示数据取自现有 30 人合成夹具，复用本地解析、确认快照、Worker、预览报告和人工复核接口。演示模式固定使用本地规则，独立存储于 `.demo-runtime`，不需要真实账号、浏览器连接或模型密钥。

1. 在「招聘任务」点「同步岗位」，选择一个模拟岗位，再点「查看填写示例」下的「使用示例」。
2. 等待自动解析。可点「修改条件」改学历、专业范围或经验月数，填写修改理由。保存后点「确认筛选条件」。
3. 点「开始筛选预览」，在弹窗确定。完成后点「查看结果」，打开候选人详情并保存人工复核。
4. 同一标签页刷新时，工作台恢复招聘原文和运行。已确认条件必须由服务端重新核验后才能继续使用。过期时用原文重新解析。

自动验收可执行 `python recruitment-workbench/workflow-browser.test.py`。脚本启动临时模拟服务和本机 Chromium，检查首次进入、同步、自动解析、表单修订、二次确认、运行、30 人结果、复核、竞争提交和过期恢复。Windows 使用项目 Python 执行时，可设置 `PHASE6_EVIDENCE_DIR` 保存截图，或设置 `PHASE6_CHROMIUM` 指定浏览器路径。

## 连接模式

在工作区根目录启动本地服务。

```powershell
.\.venv\Scripts\python.exe .\recruitment-workbench\server.py --port 8766
```

打开 [本地工作台](http://127.0.0.1:8766/)。

- 岗位管理支持读取 BOSS 岗位、本地编辑、启停和配置预览间隔。
- 任务解析接受自然语言，解析成功后可提交筛选预览。队列保留状态和错误，调度默认关闭，定时只运行预览。
- 候选人页可查看筛选证据、维护本地处置状态、导入匿名报告，并查询专业目录。
- 沟通页可读取会话、起草回复、查看附件和在线简历。发送招呼、发送回复和索取简历分别要求显式确认，会在操作前重新核对目标会话。
- 工具页提供 JD 辅助、模型连接测试和浏览器诊断。BOSS 浏览器调试端口可用 `--debug-port` 指定，默认为 9444。

服务仅监听本机。运行状态默认保存在 `.runtime`，可用 `--data-dir` 指定其他目录。用 `--mock-dir` 指向 Agent 模拟数据目录，并使用独立的 `--data-dir`，可在隔离状态下测试联系入口。

## 招聘需求 AI 解析

默认沿用现有规则。启用 AI 前，在启动服务的进程环境中设置以下变量，再重启工作台。

使用 `--model-only` 启动时，工作台优先读取 `.env`，调用模型 API，并且只接受通过结构和原文校验的模型结果。模型失败时返回错误，不创建可执行的本地规则草稿。

| 环境变量 | 用途 |
|---|---|
| `BOSS_TASK_AI_ENABLED=1` | 启用任务语义解析 |
| `BOSS_TASK_AI_MODEL` | 模型名称，也可沿用 `BOSS_AGENT_LLM_MODEL` |
| `BOSS_AGENT_LLM_BASE_URL` | 兼容 Chat Completions 的接口根地址 |
| `BOSS_AGENT_LLM_API_KEY` | 通过进程环境注入密钥 |
| `BOSS_TASK_AI_TIMEOUT_SECONDS` | 任务解析请求超时，默认 5 秒，最大 30 秒 |

`POST /api/plan` 保留 `task`、`plan` 和 `parsing_metadata`。条件引用使用原始输入的 Python 字符索引 `[start, end)`。模型异常时会报告降级原因。模型请求发送当前招聘要求，候选人证据不经过该解析接口。

## 确认规则与预览

升级后先在岗位管理同步岗位。任务解析页只显示在 BOSS 岗位菜单中可通过 `data-job-id` 核验的岗位。同名岗位都有不同的可核验 ID 时会显示 ID 供区分。如有同名项缺少 ID 或 ID 本身重复，该组岗位需要核验后再使用。

- `GET /api/workspace` 保留名称列表 `published_jobs`，并提供稳定岗位列表 `job_records`。
- `POST /api/plan` 接收 `request`、`job_id`、`tab` 和 `generation`，返回 `snapshot` 草稿与原有响应字段。旧的按名称调用仍可读取解析结果，差异结果仍带 `preview_requires_matching_rules`。
- `POST /api/plans/{plan_id}/confirm` 接收 `task_hash`。人工修订使用同一路径下的 `revise`，附条件数组及修改理由，返回新草稿。可用 `revoke` 撤销，浏览器编辑原文时调用 `POST /api/plans/invalidate` 使当前标签页的旧版本失效。
- `POST /api/runs` 的预览请求只接受 `action: "preview"`、`plan_id` 和 `job_id`。检查人数取自快照，未指定时为 20，提交时不能覆盖。旧 CLI 和内部调度仍使用原入口，内部调度需要唯一、可核验的岗位。

快照和领取记录存于数据目录的 `task-snapshots.sqlite3`。原有运行状态和预览报告仍使用 JSON。规则有效期一小时，过期未提交的快照再保留一小时；已提交的快照在有效期后保留 30 天，随新草稿写入清理。每份确认快照只创建一个运行记录，重复提交返回相同运行 ID。已入队的运行继续使用原确认版本，后续修订和到期不改变该运行的规则。撤销和岗位身份失效会阻止未领取的运行。服务重启时恢复排队未领取项，已开始的任务标记中断，避免重放浏览器动作。

## 筛选复核与合成盲标

新筛选批次按教育经历保存学校、专业、学历、学位、学习形式和就读年份。字段引用可定位至该批次保存的 DOM 或 OCR 原文片段。候选人详情的「复核教育字段」支持人工确认、修正与撤销，记录追加保存在本地 SQLite。字段复核会更新列表显示，历史筛选结论仍来自当时的报告。

连接模式以 `--model-only` 启动时，使用 `.env` 中配置的模型识别已确认筛选批次的学历、专业和人力资源经历。模型读取脱敏且有长度上限的简历文字，字段和经历必须带可核验的原文。目录映射和有证据日期的时长计算仍在本地完成。模型失败时，相应字段没有本地抽取结果可回退，会保留为待核实。普通连接模式可设置 `BOSS_EDUCATION_AI_ENABLED=1`，为本地抽取的歧义教育记录补充候选值。离线演示不会请求模型。

在「运行记录」打开一条已完成的确认快照运行，或从「候选人」选择运行。列表中的正式资格和人工复核分别统计。「规则差异」显示报告实际保存的影子结果，未运行影子引擎时保留该提示。身份或原文缺失时，页面只显示可核验的内容。

复核接口按运行所属的本地会话授权。列表、详情、差异和历史接口分别是 `/api/runs/{run_id}/candidates`、`/candidates/{candidate_id}`、`/differences` 和 `/reviews`，均在对应运行路径下。`POST /reviews` 需要候选人 ID、当前证据修订、预期复核版本、状态、理由及所选条件和引文。HTTP 409 表示另一个窗口已有更新，需要重新打开后提交。复核记录追加保存至同一 SQLite 中的 `candidate_reviews`，原筛选报告独立存储。

仅在模拟工作台可通过 `--shadow-preview` 记录影子分析、通过 `--review-study` 开启 44 个合成案例的独立盲标。这两个参数需要 `--mock-dir`。提交前只返回原文和条件，封存后该会话才可查看对照。不同浏览器会话可独立提交；分歧的人工裁决使用服务端配置的 `BOSS_REVIEW_ADJUDICATION_KEY`，在独立的 `study_adjudications` 表追加记录。原标注保存在 `study_annotations`。

真实浏览器离线验收命令为 `python recruitment-workbench/review-browser.test.py`。脚本启动隔离的本地模拟服务和临时 Chromium 配置。可用 `PHASE6_CHROMIUM` 指定本机浏览器路径，使用 `PHASE6_EVIDENCE_DIR` 保存合成验收截图。
