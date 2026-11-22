---
name: boss-agent
description: Controls a logged-in BOSS Chrome session for job-list traversal, AI/decision-maker screening, fixed-count communications, candidate screening, and conversation workflows. Use when the user asks to operate BOSS or invokes the fixed IT-support communication SOP.
---

# boss-agent

Use this tool when you need to inspect an already logged-in Boss web session in Chrome through a local remote debugging endpoint.

## 推荐牛人任务规划（Phase 00 / 01）

将用户的原始需求交给本地任务解析器，无需连接 Chrome：

```powershell
python -m boss_agent.cli plan-recruitment --request "职位：运营助理，本科，最多检查30份简历，向符合条件的10人打招呼"
```

输出包含 `task` 和 `plan`。先处理 `plan.blockers`，例如缺少岗位、冲突、无法解析或不适当的条件。
`max_checks` 是检查上限，`target_success_count` 是成功联系人数。未给定的上限保留为 `null`。
全日制是独立硬条件，需要明文证据。性别等非岗位相关敏感条件不得用于筛选或排名。

`action screen-recommend-detail` 是推荐牛人统一的候选人预览入口。浏览器模式生成预览。
`greet-recommendations`、`process-recommendations --send` 和岗位专用脚本的真实推荐招呼已停用。
`plan-recruitment` 只生成计划，即使用户请求打招呼，也不会打开浏览器或执行计划。
联系使用 `contact-recruitment`，明确提供预览报告及候选人 ID；只有 `--send` 才执行真实发送。
用户已明确授权的联系任务无需重复询问授权。先完成筛选，按照已授权的条件和人数确定 ID 名单。
沟通页已有会话工作流和求职侧职位 SOP 保持原有入口。

## 通用岗位离线评估（Phase 02.0）

```powershell
python -m boss_agent.cli evaluate-recruitment --request "岗位：财务会计，证书：初级会计资格，专业：会计学优先" --candidates candidates.json
```

候选人输入是 JSON 数组，每项含 `candidate_id` 和 `sources`。例如
`{"candidate_id":"mock-001","sources":{"resume":"证书：初级会计资格\n专业：会计学"}}`。
为确定性条件保留明确的原文字段，例如 `学历：本科`、`学习形式：全日制`、
`工具：Figma`、`运营经验：18个月`。证据保留来源键、原文和字符区间。

条件叶节点包含 `field`、`operator`、`value` 和 `source`。组合节点包含
`operator`（`AND`、`OR`、`NOT`）和 `conditions`。NOT 对缺少证据的条件保持 UNKNOWN。
有条件失败的候选人进入不符合名单，有未知项且无失败的进入待确认名单。

可通过 `--job-profile profile.json --template 模板名` 使用岗位配置。
岗位文件含 `name`、`description`、`requirements`、`priority_skills`、
`scoring`（每项含 `name`、`condition`、`weight`）及 `templates`。
各命名模板可单独覆盖要求、优先技能及评分，文件可直接修改。筛选优先按硬条件准入，
再按该岗位或模板的评分维度算分。评分是基于已见证据的规则分。
若未配置语义评估，待核实的项目和自定义能力保持 UNKNOWN。
只有显式传入 `--use-llm` 时，语义证据才提交给配置的模型服务。

此命令只评估本地资料。即使请求联系候选人，也只返回预览名单。

## 推荐牛人结构化预览（Phase 02.1）

```powershell
python -m boss_agent.cli action screen-recommend-detail --request "岗位：UI 设计师，全日制本科，工具：Figma优先，联系10人" --mock-dir fixtures --max-checks 30 --report-path reports/recommend_preview.json
```

可用 `--task-file` 代替 `--request`，接受 RecruitmentTask 或 `plan-recruitment` 的 JSON 输出。
显式传入 `--job-profile` 和可选的 `--template` 后，模板硬条件与请求硬条件取交集。出现冲突时要求澄清。
只有可直接映射的必选学历条件进入页面筛选，所有条件仍通过简历证据核实。
模拟目录包含 `recommend_candidates.json` 和按候选人 ID 索引的 `candidate_resumes.json`。
结构化预览生成 JSON，旧命令和现有 CSV/XLSX 报告格式保持兼容。

已获用户授权的真实账号预览需要显式添加 `--allow-live-preview`，并预先打开唯一的推荐牛人标签页。
浏览器模式将核对可见的旧筛选标签，检测到旧筛选时会停止。仅可读取当前可见列表和绑定稳定 ID 的详情。
采集异常以及证据不足单独列出。无论请求是否包含联系意图，预览都不会发送招呼。

## 推荐牛人采集与证据核实（Phase 02.2）

结构化预览可用 `--max-scrolls`（默认 8，允许 0 到 40）和
`--no-new-viewports`（默认 2，允许 1 到 5）限制翻动范围。
检查上限仍由任务的 `max_checks` 或 CLI 的 `--max-checks` 单独控制。
模拟滚动可提供 `recommend_viewports.json`，其中每个数组元素是一批含 `candidates` 的视口。
报告列出已访问候选人 ID、滚动次数和停止原因。检测到挑战页或岗位变化时停止。

工作经验按简历区间覆盖的日历月取并集，并以任务日期作为在职截止日期。
`experience_months` 是已见总工作时间，`experience_months:role:<岗位>`、
`experience_months:industry:<行业>`、`experience_months:duty:<职责>`
和 `experience_months:kind:<类型>` 分别用于限定范围。
类型支持 `internship`、`part_time`。无法确定的日期或职责用范围或 UNKNOWN 表示。
这是日历月覆盖量，兼职和实习不会换算成全职当量。

DOM 和 OCR 单独保留。长文本按来源及简历章节分块，证据偏移指向局部脱敏后的原文来源。
仅在显式启用模型时执行语义核对；分块结论冲突或超过预算时进入人工复核。
浏览器预览使用已获授权的真实账号。真实发送走下列统一入口。

## 预览名单联系

```powershell
python -m boss_agent.cli contact-recruitment `
  --preview-report reports/recommend_preview.json `
  --candidate-id "<报告中通过硬条件的稳定ID>" `
  --endpoint http://127.0.0.1:9445
```

不加 `--send` 时只检查名单。用户明确要求联系后添加 `--send`，可重复提供 `--candidate-id`。
人数不能超过任务目标，ID 必须属于报告的合格预览名单。
发送前再次读取当前简历核实硬条件，核对岗位和候选人 ID，并检查已联系状态。
状态记录默认位于 `reports/recommend_contact_state.json`。
发送前先持久化进行中记录，已成功或发送结果不明的对象均不会自动重发。
只有同一 ID 的按钮出现明确成功状态才计入成功人数，结果不明时停止本次发送。
旧的按姓名、列表位置发送入口继续停用。

## Commands

- `python -m boss_agent.cli health`
- `python -m boss_agent.cli debug open-boss`
- `python -m boss_agent.cli debug version --endpoint http://127.0.0.1:9222`
- `python -m boss_agent.cli debug pages --endpoint http://127.0.0.1:9222`
- `python -m boss_agent.cli capture conversation-structured --endpoint http://127.0.0.1:9444`
- `python -m boss_agent.cli score conversation --endpoint http://127.0.0.1:9444`
- `python -m boss_agent.cli draft reply --endpoint http://127.0.0.1:9444`

When the user says "打开 boss" or "打开BOSS", use `python -m boss_agent.cli debug open-boss` so Chrome starts with remote debugging enabled and opens the BOSS 沟通页. Use `--page recommend` when they specifically ask for 推荐牛人.

## 职位页双栏遍历 SOP

### 固定调用话术

When the user says exactly `执行IT技术支持AI沟通SOP`, treat that phrase as explicit authorization for this single bounded sending run and execute the following SOP:

1. Use the current logged-in debug Chrome page. Enter/click the top “职位” page if necessary.
2. Click the expectation tab `IT技术支持(深圳)`; allow the shorter requested label `IT技术支持` to match its city-suffixed tab.
3. Wait until the left job list and right detail pane are both stable.
4. Traverse and deduplicate jobs. Extract the full job description and the visible recruiter name/role.
5. Reject ordinary HR, HRBP, recruiters, consultants, specialists, assistants, or ambiguous identities before model work. Use the configured LLM Prompt to judge the remaining job and recruiter authority.
6. Communicate only when the job is relevant to AI automation, Agent/RPA/workflows, LLM applications, or AI software/platform engineering; the recruiter is a supervisor, manager, director, responsible lead, founder, owner, partner, CEO/CTO/VP or equivalent; and both semantic scores are at least 75.
7. Click “立即沟通”, wait for the success dialog, click “留在此页”, and verify the split-pane page is ready again.
8. Wait 1.5 seconds before processing the next job. Stop after exactly 5 successful communications, list exhaustion, a security verification page, or an unrecoverable page/model error. Never bypass security verification or fall back to unjudged sending.

Run:

```powershell
$env:PYTHONPATH='src'
python -m boss_agent.cli action traverse-job-search `
  --endpoint http://127.0.0.1:9222 `
  --expectation "IT技术支持(深圳)" `
  --max-count 150 `
  --max-scrolls 80 `
  --operation-delay-seconds 1.5 `
  --max-communications 5 `
  --send
```

Count only `communication.clicked=true` as a successful communication. Report each successful job title and recruiter role, plus whether `stayOnPageClicked` and `continuedOnJobPage` are true. A number other than 5 is an incomplete run and must be reported honestly with its stop reason.

Use `action traverse-job-search` when the user asks to enter the homepage 职位 view and traverse the split job-list/job-detail layout:

```powershell
$env:PYTHONPATH='src'
python -m boss_agent.cli action traverse-job-search `
  --endpoint http://127.0.0.1:9222 `
  --max-count 20 `
  --max-scrolls 20 `
  --operation-delay-seconds 1.5 `
  --send
```

The SOP finds and clicks the visible homepage “职位” navigation button when necessary, waits until `document.readyState` is complete and both panes are stable, then clicks each left-side job card and waits for the right-side detail to settle. It records title, salary, company, location, experience, education, tags, description, recruiter name/role, raw text, and detail URL. An LLM prompt judges both job relevance (AI automation, Agent/RPA/workflow, LLM application, AI software/platform/engineering) and recruiter authority. Communication is allowed only when the visible recruiter role explicitly identifies a supervisor, manager, director, department head, founder, owner, partner, CEO/CTO/VP or equivalent decision maker, and both model scores are at least 75. Ordinary HR, HRBP, recruitment consultants, recruiters, specialists, assistants, ambiguous roles, missing LLM configuration, and LLM errors fail closed. With `--send`, the SOP clicks “立即沟通” only after both gates pass, waits for the “已向BOSS发送消息” dialog, clicks “留在此页”, confirms that the split job page is ready again, waits the configured operation delay (1.5 seconds by default), and continues with the next card. Without `--send`, it reports `wouldClick: true` without contacting anyone. Use `--model` to override `BOSS_AGENT_LLM_MODEL` and `--operation-delay-seconds` to adjust the pacing.

## 推荐牛人页筛选条件

Use `action search-recommendations` to apply filters on the Boss 推荐牛人 page. For example, when the user requests a job-related education filter, run:

```powershell
$env:PYTHONPATH='src'
python -m boss_agent.cli action search-recommendations `
  --endpoint http://127.0.0.1:9222 `
  --url-contains /web/chat/recommend `
  --job-title "" `
  --filter "院校=公办本科"
```

This opens/uses the current 推荐牛人 page, opens the filter panel, clicks the matching panel options, and confirms the filter. The same pattern supports other filters:

```powershell
python -m boss_agent.cli action search-recommendations `
  --url-contains /web/chat/recommend `
  --job-title "" `
  --filter "学历=本科"
```

For multi-select filters, repeat `--filter` or pass comma-separated values:

```powershell
python -m boss_agent.cli action search-recommendations `
  --url-contains /web/chat/recommend `
  --job-title "" `
  --filter "学历=大专,本科"
```

If PowerShell corrupts Chinese arguments, call `ChromeDebugClient.apply_recommend_filters()` directly from Python with Unicode strings and write JSON using `sys.stdout.buffer.write(...encode("utf-8"))`.

## 推荐牛人能力筛选、打招呼、写表

Use `action screen-and-greet-capability` when the user asks to inspect resumes on the Boss 推荐牛人 page, use the agent to judge operations/admin capabilities, greet matching candidates, and write the reasons to a local spreadsheet.

Current behavior:

- Captures resume card text from `/web/chat/recommend`.
- Uses `analyze_operations_capability()` to judge whether the candidate shows evidence of:
  - 独立推进
  - 跨部门协调
  - 降本增效
  - 流程优化
  - 活动落地
  - project evidence such as 年会/团建活动组织、采购与库存优化
- Previews candidates who meet the capability criteria. Live recommendation contact is disabled.
- Writes evaluated matches to the preview report.
- Records candidate name, matched capabilities, selection reasons, risks/gaps, project evidence, summary, greet status, criteria, and resume snippet.

Example:

```powershell
$env:PYTHONPATH='src'
python -m boss_agent.cli action screen-and-greet-capability `
  --endpoint http://127.0.0.1:9222 `
  --url-contains /web/chat/recommend `
  --criteria "满足独立推进、跨部门协调、降本增效、流程优化、活动落地的能力。如：公司年会/团建活动组织项目，采购与库存优化项目" `
  --max-scrolls 22 `
  --target-resumes 30 `
  --max-greetings 30 `
  --report-path reports/recommend_capability_greeted_success_only.xlsx
```

Use `--dry-run` to analyze and generate a preview report without greeting candidates:

```powershell
python -m boss_agent.cli action screen-and-greet-capability `
  --url-contains /web/chat/recommend `
  --criteria "满足独立推进、跨部门协调、降本增效、流程优化、活动落地的能力" `
  --dry-run
```

Before running this flow, apply any page filters the user requested, such as the degree level required by the role:

```powershell
python -m boss_agent.cli action search-recommendations `
  --url-contains /web/chat/recommend `
  --job-title "" `
  --filter "院校=公办本科"
```

If PowerShell has trouble with Chinese arguments, call the Python helper directly and emit JSON through `sys.stdout.buffer.write(...encode("utf-8"))`.

## 沟通页：新招呼按职位筛未读并索要简历

Use `action request-resume-new-greetings` when the user asks to go to the BOSS 沟通页, click `新招呼`, filter a specified job, switch to `未读`, send a resume-request message, and click the chat toolbar `求简历` button plus its confirmation.

This is a real sending/clicking workflow. Only run it with `--send` when the user explicitly asks to send or execute. Without `--send`, it is a dry-run preview.

Recommended command:

```powershell
$env:PYTHONPATH='src'
$env:PYTHONIOENCODING='utf-8'
python -m boss_agent.cli action request-resume-new-greetings `
  --endpoint http://127.0.0.1:9222 `
  --url-contains /web/chat/index `
  --job-text "天猫运营（服饰） _ 深圳 11-15K" `
  --send
```

Important behavior:

- The job text must be the full option text from the chat job dropdown, for example `天猫运营（服饰） _ 深圳 11-15K` or `hrbp _ 深圳 13-18K`.
- If the user gives a short job name such as `天猫运营` or `hrbp`, first resolve it to the full dropdown option with `capture job-filter-options`.
- Default message: `您好，方便发一份简历吗？我这边先看一下。`
- Default operation delay is `2.0` seconds to better mimic human pacing. Override with `--operation-delay-seconds 2`.
- The workflow skips conversations that already contain resume-related content such as `简历请求已发送`, `对方想发送附件简历`, `附件简历`, or `这是我的简历`.
- The `求简历` action must complete both clicks: toolbar `求简历`, then confirmation `确定`. The result should include `requestResume.confirm.clicked=true`.

Preview before sending:

```powershell
python -m boss_agent.cli action request-resume-new-greetings `
  --endpoint http://127.0.0.1:9222 `
  --url-contains /web/chat/index `
  --job-text "天猫运营（服饰） _ 深圳 11-15K"
```

## Notes

- Default endpoint is `http://127.0.0.1:9222`.
- Some actions are read-only or draft-only, but recommendation greeting and chat send actions can click buttons and send messages. Confirm intent before using non-dry-run greeting/sending flows.

## 推荐牛人详情简历筛选预览

Use `action screen-recommend-detail` when the user wants to work on the BOSS 推荐牛人 page by switching the top-right job selector, applying page filters, opening each candidate card, reading the resume detail page, judging the resume against a natural-language requirement, previewing matching candidates, closing the detail page, and writing an Excel/CSV report with the reasons.

Recommended fixed prompt format:

Current wording: the number in the prompt means target resume details to traverse/check, not target successful greetings. Map `目标遍历简历数：30` to `--target-resumes 30` or JSON `targetResumes: 30`. Keep `--max-greetings` only as a safety cap for successful greetings.

Filter key aliases are supported for the recommendation page. For example, `薪资=10-20k`, `薪资待遇=10-20k`, and `薪资待遇[单选]=10-20k` all target the page group `薪资待遇[单选]`. Common aliases also include `学历`, `活跃度`, `经验要求`, `院校`, `专业`, and `关键词`.

```text
在推荐牛人页预览详情简历筛选：
职位：<职位名称>
页面筛选：<筛选项1>=<值1>，<筛选项2>=<值2>
牛人要求：<用自然语言描述要找什么样的人>
目标遍历简历数：<数字>
```

Example:

```text
在推荐牛人页预览详情简历筛选：
职位：电商运营经理（美妆）
页面筛选：学历=本科
牛人要求：有天猫或淘宝美妆运营经验，做过店铺增长、活动策划或投放协同，最好有独立负责项目的经历，不要纯客服或纯主播
目标遍历简历数：30
```

Run:

```powershell
$env:PYTHONPATH='src'
python -m boss_agent.cli action screen-recommend-detail `
  --endpoint http://127.0.0.1:9222 `
  --url-contains /web/chat/recommend `
  --job-title "电商运营经理（美妆）" `
  --filter "学历=本科" `
  --criteria "有天猫或淘宝美妆运营经验，做过店铺增长、活动策划或投放协同，最好有独立负责项目的经历，不要纯客服或纯主播" `
  --target-resumes 30 `
  --dry-run `
  --report-path reports/recommend_detail_greeted.xlsx
```

For long Chinese criteria or repeated jobs, prefer a UTF-8 JSON config file to avoid PowerShell argument encoding issues:

```powershell
python -m boss_agent.cli action screen-recommend-detail --config tasks/recommend_detail.json
```

Config shape:

```json
{
  "endpoint": "http://127.0.0.1:9222",
  "urlContains": "/web/chat/recommend",
  "jobTitle": "电商运营经理（美妆）",
  "filters": {
    "学历": "本科"
  },
  "criteria": "有天猫或淘宝美妆运营经验，做过店铺增长、活动策划或投放协同，最好有独立负责项目的经历，不要纯客服或纯主播",
  "targetResumes": 30,
  "maxGreetings": 30,
  "reportPath": "reports/recommend_detail_greeted.xlsx"
}
```

Important defaults:

- The command previews real-browser runs even when `--dry-run` is omitted. Fixture-based runs can still simulate contact.
- The primary stop condition is checked resume details. Use `--target-resumes` or config `targetResumes`; `--max-greetings` is only a safety cap for successful greetings.
- The report records all checked candidates, including skipped candidates and risks. Matched and greeted candidates are sorted first.
- For nuanced natural-language screening, configure `BOSS_AGENT_LLM_API_KEY` or `OPENAI_API_KEY`; otherwise the rules fallback only handles simpler requirements.
