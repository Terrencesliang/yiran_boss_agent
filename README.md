# boss-agent

用于连接已开启远程调试的 Chrome 会话，逐步构建 Boss 招聘辅助助手。

## 招聘任务解析与预览

- 从当前指令开始，用 `plan-recruitment --request "原始指令"` 查看确定性规则的解析结果和阻断项。原文未包含岗位时，用 `--job-context "当前岗位"` 明确提供岗位。计划命令不会采集候选人。兼容旧调用时，需看 JSON 的 `plan.blockers`，仅看退出码无法判断计划是否就绪。
- 从原文运行模拟预览，用 `action screen-recommend-detail --request "原始指令" --job-context "当前岗位" --mock-dir <夹具目录> --report-path <新报告.json>`。旧 `--task-file` 入口仍可显式导入。工具不查找或自动选用历史任务。
- 成功、阻断与执行中止的结构化入口记录分别写入 `reports/recruitment_runs`，也可用 `BOSS_RECRUITMENT_AUDIT_DIR` 指定目录。记录含原始指令或显式导入文件哈希、解析、最终执行配置及阶段状态。
- 离线评估可用 `evaluate-recruitment --request "原始指令" --candidates <简历证据.json> --sample-kind synthetic`。已脱敏的真实样本单独使用 `real-deidentified`，分别运行和统计。旧调用默认标为 `unclassified`。
- `--use-llm` 用于简历证据判断，任务生成仍使用确定性规则。LLM 任务理解评估应在独立影子流程完成。

## 本科专业目录维护

专业映射集中在 `src/boss_agent/undergraduate_majors.json`。目录记录教育部版本、源 PDF 和校验哈希，按专业代码连接专业名、专业类和学科门类。2026 版包含 13 个门类、92 个专业类和 883 个专业。交叉学科下未列专业类的条目保留门类，专业类留空。

更新时依据新版正式目录同步更新版本、来源、哈希和代码表。只在 `aliases` 中维护能唯一对应到目录名称的简称。岗位准入、专业例外和优先项由招聘任务配置。验证时覆盖跨门类专业、别名和未收录值。

## Current Scope

- `health`: 输出服务状态
- `debug version`: 读取 `http://127.0.0.1:9222/json/version`
- `debug pages`: 读取 `http://127.0.0.1:9222/json/list`
- `debug launch-chrome`: 启动带 `--remote-debugging-port` 的独立 Chrome 实例
- `capture conversation-structured`: 提取聊天页结构化字段
- `score conversation`: 基于规则输出会话评分
- `draft reply`: 基于规则生成回复草稿
- `knowledge/`: 本地 `.txt` 知识库目录，支持按岗位路由公司、岗位、薪酬、部门架构问答

## Knowledge Layout

```text
knowledge/
  global/
    company/
      company_profile.txt
  jobs/
    电商运营经理/
      company/
        faq.txt
      job/
        faq.txt
      compensation/
        faq.txt
      department/
        faq.txt
```

建议使用固定段落格式，例如 `[岗位名称]`、`[常见问答]`。系统会先根据聊天岗位匹配 `knowledge/jobs/<岗位名>/`，再按问题意图路由到 `company`、`job`、`compensation`、`department` 对应 FAQ；公司问题会先查岗位内 `company`，再查 `global/company`。旧版 `knowledge/company/*.txt` 和 `knowledge/jobs/*.txt` 暂时兼容。

## Example

```bash
python -m boss_agent.cli health
python -m boss_agent.cli debug version --endpoint http://127.0.0.1:9222
python -m boss_agent.cli debug pages --endpoint http://127.0.0.1:9222
python -m boss_agent.cli debug launch-chrome --dry-run
python -m boss_agent.cli capture conversation-structured --endpoint http://127.0.0.1:9444
python -m boss_agent.cli score conversation --endpoint http://127.0.0.1:9444
python -m boss_agent.cli draft reply --endpoint http://127.0.0.1:9444 --knowledge-dir .\\knowledge
python -m boss_agent.cli action prepare-reply --endpoint http://127.0.0.1:9444 --knowledge-dir .\\knowledge
python -m boss_agent.cli action reply-unread-with-knowledge --endpoint http://127.0.0.1:9444 --job-text "电商运营经理" --knowledge-dir .\\knowledge
```
