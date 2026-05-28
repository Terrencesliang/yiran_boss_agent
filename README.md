# boss-agent

用于连接已开启远程调试的 Chrome 会话，逐步构建 Boss 招聘辅助助手。

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
