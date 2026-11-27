# 测试

全部测试只使用标准库和本地假服务器，不访问真实 API。

```bash
python3 tests/run_all.py
```

也可以单独运行：

```bash
python3 tests/test_agent2.py
python3 tests/test_vision.py
python3 tests/test_skills.py
python3 tests/test_compact.py
python3 tests/test_sessions.py
python3 tests/test_web_images.py
python3 tests/test_robustness.py
python3 tests/test_usage.py
python3 tests/test_reasoning.py
python3 tests/test_anthropic.py
python3 tests/test_responses.py
python3 tests/test_config.py
python3 tests/test_connect.py
python3 tests/test_web_connections.py
python3 tests/test_shell.py
python3 tests/test_shell_choice.py
python3 tests/test_plan_mode.py
python3 tests/test_scheduler.py
python3 tests/test_schedule_web.py
python3 tests/test_memory.py
python3 tests/test_config_defaults.py
python3 tests/test_model_info.py
python3 tests/test_auto_compact.py
python3 tests/test_command_timeout.py
python3 tests/test_session_repair.py
python3 tests/test_model_switch.py
python3 tests/test_streaming.py
python3 tests/test_workspace.py
python3 tests/test_maintenance.py
python3 tests/test_session_features.py
python3 tests/test_memory_cleanup.py
python3 tests/test_mcp.py
python3 tests/test_skills_manage.py
python3 tests/test_skills_web.py
python3 tests/test_web_js.py
python3 tests/test_web_approval.py
python3 tests/test_settings_web.py
python3 tests/test_plan_sessions_web.py
python3 tests/test_context_meter.py
python3 tests/test_outline_cache.py
python3 tests/test_read_files_diff.py
python3 tests/test_web_hardening.py
python3 tests/test_web_markdown.py
python3 tests/test_web_stream_render.py
python3 tests/test_docs_consistency.py
python3 tests/test_plan_controller.py
python3 tests/test_compact_single_request.py
python3 tests/test_estimate_perf.py
python3 tests/test_chat_lock_scope.py
python3 tests/test_background_and_http.py
python3 tests/test_compact_prompt.py
python3 tests/test_usage_protocol.py
python3 tests/test_gui_tools.py
python3 tests/test_stream_cancel.py
python3 tests/test_stream_timeout_settings.py
```

测试会：

- 使用临时 `HOME`，不会读写真实的 `~/.benben-agent/`；
- 通过 `BENBEN_CONFIG` 使用临时配置文件，`run_all.py` 会统一隔离；
- 使用临时工作区，不会修改项目目录；
- 启动本地假 HTTP 服务模拟 OpenAI Chat、Anthropic Messages 和 OpenAI Responses；
- 失败时以非 0 退出码结束。

| 文件 | 覆盖内容 |
| --- | --- |
| `test_usage.py` | usage 汇总、截断警告 |
| `test_agent2.py` | 工具、搜索、编辑、工作区外绝对路径和 `~`、glob 扫描预算、审批回调、命令审批、工具详情历史、取消 |
| `test_vision.py` | 图片校验、`history_view` |
| `test_skills.py` | 技能发现、技能工具、脚本 |
| `test_skills_manage.py` | 技能创建/更新/删除、默认全局作用域、目录/zip/git 安装、风险扫描、待确认批准、备份、工具暴露、CLI |
| `test_skills_web.py` | Web 技能列表/搜索/详情/创建/编辑/删除/安装/批准/拒绝、禁用模式 |
| `test_web_js.py` | 用 Node 假 DOM 执行内嵌页面脚本，捕获初始化顺序和运行时错误（`web_js_harness.js`） |
| `test_web_approval.py` | Web 审批：允许/拒绝/停止/超时、计划模式守卫弹窗、非流式 409、前端弹窗 HTML |
| `test_settings_web.py` | Web 默认设置：读取/保存、立即生效、重启项、校验、密钥隐藏、对话中 409 |
| `test_plan_sessions_web.py` | 计划状态按会话隔离、切换后不串计划、删除后计划清理、恢复会话不还原已删计划 |
| `test_compact.py` | 会话压缩、compact 标记、恢复 |
| `test_reasoning.py` | `--reasoning` 映射和请求字段 |
| `test_anthropic.py` | Anthropic 协议转换、工具循环、Web |
| `test_responses.py` | Responses 协议转换、工具循环、Web |
| `test_sessions.py` | 会话列表、标题、`/session` CLI、导出、迁移、缓存 |
| `test_web_images.py` | 图片懒加载、`/api/image`、token 校验 |
| `test_robustness.py` | 协议响应容错、畸形工具调用容错 |
| `test_config.py` | 配置读写、权限、校验、优先级、模型解析、client 工厂 |
| `test_connect.py` | `/connect` 向导、预设、切换/删除、`--profile`、`--no-env` |
| `test_web_connections.py` | Web 连接设置、会话标题/导出、工具详情、思考/停止控件、会话回收站恢复/永久删除/清空 |
| `test_shell.py` | shell 检测顺序、`$SHELL` 优先、Windows 编码、工具切换、自动回退、系统提示；只读判定的隐藏写入通道（换行绕过、`sed` 的 `e`/`w`/`s///w`/`s///e`/`-f`、`git -c` 的危险配置键）以及正常命令不被误伤 |
| `test_shell_choice.py` | `run_command` 的 `shell` 参数：按指定 shell 执行、不改默认、不回退、只读判定、工具描述 |
| `test_plan_mode.py` | 计划模式：工具不削减、`submit_plan`、守卫三档、提示词、CLI、Web API、配置持久化、会话级计划状态 |
| `test_scheduler.py` | 时间表达式、任务存储、只读/完整权限、运行记录、补跑一次、CLI `/schedule` |
| `test_schedule_web.py` | Web 定时任务 API、轮询通知、启停/运行/记录/删除、禁用模式 |
| `test_memory.py` | 长期记忆、情景记忆、自动日总结、内置定时任务、只读权限、CLI `/memory` |
| `test_config_defaults.py` | 配置默认值、全部 defaults 优先级、首次创建、CLI 优先级、`/config save`、`--no-config` |
| `test_model_info.py` | 模型能力目录、fallback、模态、上下文用量、远程解析、图片预检、模型能力向导、`/model info` |
| `test_auto_compact.py` | 8 档自动压缩、自定义覆盖、空闲压缩、配置持久化、`/compact auto|idle` |
| `test_command_timeout.py` | 超时必填、超时提示、部分输出、子进程树清理 |
| `test_session_repair.py` | 未完成 tool_call 修复、半行修复、修复幂等 |
| `test_model_switch.py` | `/model list all`、跨连接切换、`--save` 持久化 |
| `test_streaming.py` | OpenAI/Anthropic/Responses 流式组装、UTF-8、降级、重试、流式和非流式取消、CLI `/stream`、Web 事件 |
| `test_workspace.py` | 工作空间校验、`/session new <path>`、`/workspace`、`-c` 恢复自动切换 |
| `test_maintenance.py` | 会话回收站、恢复、单个永久删除、prune、`--check`/`--doctor`、SIGTERM 退出 |
| `test_session_features.py` | 会话置顶、归档、大小、CLI 列表标记 |
| `test_memory_cleanup.py` | 长期记忆去重、按数量/时间清理、情景记忆清理 |
| `test_mcp.py` | MCP stdio/HTTP 握手、工具注入、增删改、启停、资源/提示词、只读过滤、计划门控、配置持久化、CLI 向导、端到端调用 |
| `test_outline.py` | `read_file_outline` 多语言解析：Python `ast` 嵌套、Go/Rust/TS/JS 等括号计数、通用回退、截断 |
| `test_outline_cache.py` | outline 解析缓存：命中后结果一致、不同 `max_items` 不串、文件改动失效、LRU 上限、并发安全、二进制拒绝 |
| `test_read_files_diff.py` | `read_files` 多文件读取（共享行预算、单文件出错不中断、数量上限）与 `edit_file`/`multi_edit` 的 unified diff 回显 |
| `test_list_dir.py` | `list_dir` 排序、大小/条目数显示、隐藏文件、非目录报错 |
| `test_multi_edit.py` | `multi_edit` 跨文件原子提交、顺序应用、失败全回滚 |
| `test_search_context.py` | `search` 的 `context=N` 前后行、`output=lines`、`max_results` 上限 |
| `test_compress.py` | 工具输出压缩：保留报错/警告/声明行、JSON 结构、`--no-compress` 回退 |
| `test_web_compress.py` | Web 工具输出压缩设置、GET/POST、范围校验、持久化 |
| `test_web_markdown.py` | Web Markdown 渲染：基础语法、行内代码跨边界的强调/链接、引用块内列表、HTML 转义 |
| `test_web_stream_render.py` | 流式增量渲染：中途出现标题/加粗/列表/表格/代码块、节流、结束时内容完整、取消清理 |
| `test_context_meter.py` | 输入框上方的上下文指示器：格式、分段着色、窄屏降级、无悬浮、`usage_live` 驱动实时刷新 |
| `test_web_hardening.py` | Web 健壮性：`Content-Length` 撒谎/负数不挂死线程、半开连接不拖死服务、顶层异常返回 500 |
| `test_docs_consistency.py` | 文档与代码一致：README 工具表/工具总数/行数上限/CLI 参数、模型上下文的查找顺序与覆盖语义、兜底数值、压缩参数、tests/README 覆盖所有测试文件、已移除功能无残留 |
| `test_plan_controller.py` | `PlanModeController`：切换/保存/恢复计划模式、无状态会话回落默认值、切模式清挂起计划、CLI 与 Web 各持一份互不干扰 |
| `test_compact_single_request.py` | 压缩改单次请求：只发一次模型调用、超窗口时丢最早的消息、承接旧摘要、预算计算与截断 |
| `test_estimate_perf.py` | token 估算改造：正则数宽字符与旧实现逐字节等价、`context_usage` 只扫描一次历史、`project_context_tokens` 复用传入的本地值 |
| `test_chat_lock_scope.py` | 对话回合不持有全局会话锁：`run_turn` 与 SSE 写入在锁外、锁块足够窄、替换 state 的端点（`/api/open`、`/api/new`、`/api/delete`）在忙时返回 409、回合进行中其他请求仍能快速响应 |
| `test_background_and_http.py` | 后台任务与 HTTP 工具：`background=true` 起任务、`job_output` 读尾部、`job_cancel` 杀进程树、数量上限与自动超时、`http_request` 的 GET/POST/4xx/重定向/非法 URL |
| `test_compact_prompt.py` | 压缩摘要模板：8 个小节齐全、规则要点（省略系统指令/路径原样保留/不调用工具）、散文回复也照收且不额外发请求、滚动压缩的合并说明且不重复旧摘要 |
| `test_usage_protocol.py` | 前后端 usage 协议：跑一次真实回合拿到后端发出的 usage_live 文本和结构化 data，再喂给页面真实函数——结构化 data 能直接渲染，文本兜底能解出上下文百分比且与快照一致。防止两边字段名再次静默脱节 |
| `test_gui_tools.py` | 屏幕工具：默认关闭且 `dispatch` 拒绝执行、`--gui-use` 才暴露、无显示时不给工具、`view_screen` 返回 `ToolImage`、截图作为 tool 结果之后的独立 user 消息发回模型（tool 消息保持纯文本、本地标记不上线）、确认开关控制 `click` 等写操作、只读与定时任务下的工具裁剪、参数校验、PNG/JPEG 尺寸解析、压缩切分不把图片消息当用户回合 |
| `test_stream_cancel.py` | 停止流式请求：流中途静默能被停止且报 `ChatCancelled`（而不是"流提前结束"）、请求一直不返回响应头也能停止（不再卡到 idle 超时）、重试退避期间停止不被延迟、正常流不受影响 |
| `test_stream_timeout_settings.py` | 流式超时：连接超时与空闲超时是两个独立值（默认 30/60）、两项均可热更新、保存即时生效、换连接后保留新值而不是回到默认 |
