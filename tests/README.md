# 测试

全部测试只使用标准库和本地假服务器，不访问真实 API。

```bash
python3 tests/run_all.py
```

也可以单独运行：

```bash
python3 tests/test_usage.py
python3 tests/test_reasoning.py
python3 tests/test_anthropic.py
python3 tests/test_responses.py
```

测试会：

- 使用临时 `HOME`，不会读写真实的 `~/.benben-agent/`；
- 使用临时工作区，不会修改项目目录；
- 启动本地假 HTTP 服务模拟 OpenAI Chat、Anthropic Messages 和 OpenAI Responses；
- 失败时以非 0 退出码结束。

| 文件 | 覆盖内容 |
| --- | --- |
| `test_usage.py` | usage 汇总、截断警告 |
| `test_agent2.py` | 工具、搜索、编辑、命令审批 |
| `test_vision.py` | 图片校验、`history_view` |
| `test_skills.py` | 技能发现、技能工具、脚本 |
| `test_compact.py` | 会话压缩、compact 标记、恢复 |
| `test_reasoning.py` | `--reasoning` 映射和请求字段 |
| `test_anthropic.py` | Anthropic 协议转换、工具循环、Web |
| `test_responses.py` | Responses 协议转换、工具循环、Web |
| `test_sessions.py` | 会话列表统计、compact 计数、缓存失效 |
| `test_web_images.py` | 图片懒加载、`/api/image`、token 校验 |
| `test_robustness.py` | 协议响应容错、畸形工具调用容错 |
