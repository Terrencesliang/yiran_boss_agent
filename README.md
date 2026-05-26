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
- `knowledge/`: 本地 `.txt` 知识库目录，支持岗位和公司问答

## Knowledge Layout

```text
knowledge/
  jobs/
    岗位案例模板.txt
    电商运营经理.txt
  company/
    公司介绍模板.txt
    company_profile.txt
```

建议使用固定段落格式，例如 `[岗位名称]`、`[常见问答]`。系统会优先从对应岗位和公司 `.txt` 中匹配问题答案，未命中时再退回通用规则。

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
```
