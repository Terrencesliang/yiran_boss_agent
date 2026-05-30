---
name: boss-agent
description: Connects to a remote-debugging Chrome session and helps extract candidate, conversation, and scoring data for Boss recruitment workflows.
---

# boss-agent

Use this tool when you need to inspect an already logged-in Boss web session in Chrome through a local remote debugging endpoint.

## Commands

- `python -m boss_agent.cli health`
- `python -m boss_agent.cli debug version --endpoint http://127.0.0.1:9222`
- `python -m boss_agent.cli debug pages --endpoint http://127.0.0.1:9222`
- `python -m boss_agent.cli capture conversation-structured --endpoint http://127.0.0.1:9444`
- `python -m boss_agent.cli score conversation --endpoint http://127.0.0.1:9444`
- `python -m boss_agent.cli draft reply --endpoint http://127.0.0.1:9444`

## 推荐牛人页筛选条件

Use `action search-recommendations` to apply filters on the Boss 推荐牛人 page. For example, when the user asks: “推荐牛人页筛选性别女，院校为公办本科”, run:

```powershell
$env:PYTHONPATH='src'
python -m boss_agent.cli action search-recommendations `
  --endpoint http://127.0.0.1:9222 `
  --url-contains /web/chat/recommend `
  --job-title "" `
  --filter "性别=女" `
  --filter "院校=公办本科"
```

This opens/uses the current 推荐牛人 page, opens the filter panel, clicks the matching panel options, and confirms the filter. The same pattern supports other filters:

```powershell
python -m boss_agent.cli action search-recommendations `
  --url-contains /web/chat/recommend `
  --job-title "" `
  --filter "学历=本科" `
  --filter "年龄=24-35"
```

Age filters use the recommendation page's draggable slider. The first drag may land one year off if the page only exposes an "不限" endpoint, so verify the returned `specialFilters.年龄.calibratedFrom` value. If the page displays `26-29` when the target is `25-28`, first drag by the observed offset, then apply the exact range again:

```powershell
# Initial request
python -m boss_agent.cli action search-recommendations `
  --url-contains /web/chat/recommend `
  --job-title "" `
  --filter "年龄=25-28"

# If the page actually shows 26-29, apply 24-27 once to calibrate from the visible values,
# then apply 25-28 again. The final response should include:
# specialFilters.年龄.calibratedFrom = ["25", "28"]
python -m boss_agent.cli action search-recommendations `
  --url-contains /web/chat/recommend `
  --job-title "" `
  --filter "年龄=24-27"

python -m boss_agent.cli action search-recommendations `
  --url-contains /web/chat/recommend `
  --job-title "" `
  --filter "年龄=25-28"
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
- Greets only candidates that meet the capability criteria.
- Writes only successfully greeted candidates (`greeted=true`) to the Excel report.
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

Before running this flow, apply any page filters the user requested, such as 性别=女 and 院校=公办本科:

```powershell
python -m boss_agent.cli action search-recommendations `
  --url-contains /web/chat/recommend `
  --job-title "" `
  --filter "性别=女" `
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

## 推荐牛人详情简历筛选并直接打招呼

Use `action screen-recommend-detail` when the user wants to work on the BOSS 推荐牛人 page by switching the top-right job selector, applying page filters, opening each candidate card, reading the resume detail page, judging the resume against a natural-language requirement, greeting matched candidates, closing the detail page, and writing an Excel/CSV report with the reasons.

Recommended fixed prompt format:

Current wording: the number in the prompt means target resume details to traverse/check, not target successful greetings. Map `目标遍历简历数：30` to `--target-resumes 30` or JSON `targetResumes: 30`. Keep `--max-greetings` only as a safety cap for successful greetings.

Filter key aliases are supported for the recommendation page. For example, `薪资=10-20k`, `薪资待遇=10-20k`, and `薪资待遇[单选]=10-20k` all target the page group `薪资待遇[单选]`. Common aliases also include `学历`, `年龄`, `活跃度`, `经验要求`, `院校`, `专业`, and `关键词`.

```text
在推荐牛人页执行详情简历筛选并直接打招呼：
职位：<职位名称>
页面筛选：<筛选项1>=<值1>，<筛选项2>=<值2>
牛人要求：<用自然语言描述要找什么样的人>
目标打招呼人数：<数字>
```

Example:

```text
在推荐牛人页执行详情简历筛选并直接打招呼：
职位：电商运营经理（美妆）
页面筛选：性别=女，学历=本科，年龄=24-35
牛人要求：有天猫或淘宝美妆运营经验，做过店铺增长、活动策划或投放协同，最好有独立负责项目的经历，不要纯客服或纯主播
目标打招呼人数：30
```

Run:

```powershell
$env:PYTHONPATH='src'
python -m boss_agent.cli action screen-recommend-detail `
  --endpoint http://127.0.0.1:9222 `
  --url-contains /web/chat/recommend `
  --job-title "电商运营经理（美妆）" `
  --filter "性别=女" `
  --filter "学历=本科" `
  --filter "年龄=24-35" `
  --criteria "有天猫或淘宝美妆运营经验，做过店铺增长、活动策划或投放协同，最好有独立负责项目的经历，不要纯客服或纯主播" `
  --max-greetings 30 `
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
    "性别": "女",
    "学历": "本科",
    "年龄": "24-35"
  },
  "criteria": "有天猫或淘宝美妆运营经验，做过店铺增长、活动策划或投放协同，最好有独立负责项目的经历，不要纯客服或纯主播",
  "targetResumes": 30,
  "maxGreetings": 30,
  "reportPath": "reports/recommend_detail_greeted.xlsx"
}
```

Important defaults:

- Without `--dry-run`, this command clicks the detail-page greeting button for candidates that meet the criteria.
- The primary stop condition is checked resume details. Use `--target-resumes` or config `targetResumes`; `--max-greetings` is only a safety cap for successful greetings.
- The report records all checked candidates, including skipped candidates and risks. Matched and greeted candidates are sorted first.
- For nuanced natural-language screening, configure `BOSS_AGENT_LLM_API_KEY` or `OPENAI_API_KEY`; otherwise the rules fallback only handles simpler requirements.
