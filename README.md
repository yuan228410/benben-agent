# 零依赖 AI Agent

`benben-agent.py` 是一个只用 Python 3.8+ 标准库实现的命令行 AI agent，通过 OpenAI 兼容的 `POST /chat/completions` 接口驱动。支持多轮对话、会话持久化、Agent Skills、function calling（工具调用）和图片输入，模型可以读写文件、执行命令，然后给出最终回答。文件工具支持绝对路径和 `~`，工作区是默认目录和相对路径基准，不再是访问边界。

## 快速开始

```bash
# 默认使用 DeepSeek 官方接口，需要先提供 API key
export OPENAI_API_KEY=sk-...
python3 benben-agent.py "列出当前目录，并总结这些文件是做什么的"
```

默认地址和模型由 `benben-agent.py` 里的 `DEFAULT_BASE_URL` / `DEFAULT_MODEL` 决定，代码当前是 `https://api.deepseek.com/v1` 和 `Deepseek-V4.1-Flash`。也可以用 `OPENAI_BASE_URL` / `OPENAI_MODEL` 环境变量覆盖。

换其他网关、模型或 key：

```bash
python3 benben-agent.py --base-url https://your-gateway/v1 --api-key sk-... -m your-model "..."
```

交互模式：

```bash
python3 benben-agent.py
you> 把 hello.txt 里的内容改成 Hello World
...
you> /confirm on      # 需要时开启确认；只读命令不会询问
you> /thinking on     # 显示模型的思考内容（默认关闭）
you> /plan on         # 计划模式：先调查出计划，批准后再动手
you> /plan guard approve   # 计划模式下改状态的操作需要确认
you> /image shot.png  # 附带图片，随下一条消息发送
you> /skill            # 列出 Agent Skills（启动时加 --skill）
you> /skill log-triage 排查 502  # 强制用指定技能回答问题
you> /skill create     # 引导式创建或更新技能（默认全局）
you> /skill install    # 从目录、zip 或 git URL 安装技能
you> /save            # 把当前会话保存为工作区里的 chat-<时间戳>.md（只存对话）
you> /save --tool --thinking notes.md  # 连同工具调用/输出和思考一起保存
you> /compact         # 压缩历史，保留最近 2 个用户回合
you> /compact 4       # 压缩历史，保留最近 4 个用户回合
you> /model list      # 按名称排序列出网关可用模型（带编号，* 为当前模型）
you> /model 2         # 按编号切换
you> /model deepseek-reasoner  # 或直接写模型名
you> /model           # 查看当前模型
you> /shell           # 查看当前和可用 shell
you> /shell zsh       # 切换 shell
you> /status          # 查看连接、模型、shell、会话和工作区
you> /session         # 列出最近会话
you> /session use 1   # 切换到第 1 个会话，并恢复其工作空间
you> /session new     # 使用当前工作空间开始新会话
you> /session new /path/project  # 指定工作空间开始新会话
you> /workspace /path/project     # 切换工作空间并新建
you> /session info    # 查看当前会话
you> /session delete 2  # 移到回收站
you> /session trash     # 查看回收站
...
you> /exit
```

`/save` 默认只保存用户和助手的对话；`--tool` 加上工具调用和完整工具输出，`--thinking` 加上模型思考内容，两个选项可以同时用。导出内容不截断，文件名原样使用（默认 `chat-<时间戳>.md`），可以写到工作区外的路径；指定文件已存在时直接覆盖。

`/compact` 会让模型把较早的对话总结成一段结构化摘要，之后只发送摘要和最近保留的轮次，减少上下文占用。`/compact 0` 表示不保留最近轮次，只留摘要。压缩后会话文件不会变小，原历史仍然保留在磁盘上，只是 `-c` 加载时从最后一次压缩标记开始重建上下文。

常用写法：

```bash
# 本地模型（vLLM / Ollama / LM Studio 等）
python3 benben-agent.py --base-url http://localhost:11434/v1 --api-key ollama --model llama3.1 "..."

# 需要审批时开启（只读命令仍不会询问）
python3 benben-agent.py --confirm "运行测试并修复失败的用例"

# 禁用 shell 工具
python3 benben-agent.py --no-shell "..."

# 指定工作区
python3 benben-agent.py --cwd /path/to/project "..."
```

## 连接配置（`/connect`）

第一次使用可以直接运行向导：

```bash
python3 benben-agent.py --connect
```

保存后下次启动自动使用。配置文件在：

```text
~/.benben-agent/config.json
```

交互模式里也可以管理连接：

```text
/connect                    打开配置向导
/connect list               列出已保存的连接
/connect use <name>         切换连接，并设为 active
/connect delete <name>      删除连接（不能删除当前连接）
/connect deepseek           直接进入 DeepSeek 预设
/connect minimax            直接进入 MiniMax 预设
/connect custom             自定义 base URL、协议和模型
```

内置预设：

| 服务商 | 协议 | 地址 |
| --- | --- | --- |
| DeepSeek | `openai-chat-completions` | `https://api.deepseek.com/v1` |
| MiniMax | `openai-chat-completions` | `https://api.minimaxi.com/v1` |
| MiniMax | `openai-responses` | `https://api.minimaxi.com/v1` |
| MiniMax | `anthropic-messages` | `https://api.minimaxi.com/anthropic` |

向导会尝试通过 `/models` 拉取模型列表；接口不支持时可以直接输入模型名。MiniMax 默认使用 `bearer` 认证，其他 Anthropic 连接默认 `x-api-key`。

配置优先级：

```text
显式 CLI 参数 > 环境变量 > active 连接 > 内置默认值
```

因此 shell 里已有的 `OPENAI_BASE_URL` / `OPENAI_API_KEY` / `OPENAI_MODEL` 会覆盖保存的连接。启动时会打印提示；不想被环境变量覆盖时加 `--no-env`：

```bash
python3 benben-agent.py --no-env
python3 benben-agent.py --profile work       # 临时使用指定连接
python3 benben-agent.py --no-config          # 启动时忽略配置文件
python3 benben-agent.py --config /path/to/config.json
```

`--no-config` 只影响启动时的连接选择；`/connect` 和 Web 连接设置仍会读取原文件，保存时不会丢掉其他连接。

`--connect` 还可以配置后继续启动网页或执行一次任务：

```bash
python3 benben-agent.py --connect --web
python3 benben-agent.py --connect "检查当前目录"
```

Web 界面侧边栏的“连接设置”提供同样的能力：选择/新建连接、切换服务商、协议、Base URL、模型、API key。API key 不会回传到浏览器。

`/connect` 的 API Key 输入：

- 提示只有 `API Key: `，输入时用 `*` 回显，不显示明文。
- 输入了 key → 保存到配置文件。
- 新建连接直接回车 → 不保存 key，运行时读取 `OPENAI_API_KEY`。
- 已有 key 时直接回车 → 询问“保留现有 key”还是“清空已保存的 key”。

> 配置文件权限为 `0600`，但 API key 默认以明文保存。也可以把 key 留空，运行时读取 `OPENAI_API_KEY`。不要把配置文件提交到仓库。

## Shell 自动检测

启动时自动选择当前系统可用的 shell，`run_command` 和技能脚本都会使用它。

| 系统 | 自动检测顺序 |
| --- | --- |
| Linux | `$SHELL` → `bash` → `zsh` → `sh` → `/bin/sh` |
| macOS | `$SHELL` → `zsh` → `bash` → `sh` → `/bin/sh` |
| Windows | `pwsh` → `powershell` → Git Bash → `cmd` |

- `$SHELL` 只接受 `bash`、`zsh`、`sh`、`ksh`、`dash`；`fish`、`tcsh` 不自动选。
- Windows 下忽略 `$SHELL`，避免把 Git Bash 的 `/usr/bin/bash` 当成原生路径。
- Git Bash 只认 Git 安装目录下的 `bash.exe`，不会自动选 `System32\bash.exe`（WSL）。
- 用户显式指定永远优先：

```bash
python3 benben-agent.py --shell zsh
python3 benben-agent.py --shell git-bash
python3 benben-agent.py --shell "C:\Program Files\PowerShell\7\pwsh.exe"
```

交互模式：

```text
/shell                 查看当前和可用 shell
/shell list            同上
/shell zsh             切换
/shell auto            重新自动检测
```

模型也能回答“有哪些 shell”“当前用哪个”，并在用户要求时通过 `switch_shell` 工具切换。Web 聊天框输入 `/shell` 也可以查看或切换。

系统提示词只在启动时生成一次，切换 shell 后不会重写，避免破坏 prompt 缓存。模型通过 `list_shells`、`switch_shell` 和 `run_command` 返回的 `shell:` 行获取当前 shell。注意 `run_command` 工具描述里嵌入的可用 shell 列表是启动时的快照，`switch_shell` 或 `/shell` 切换后不会刷新，需要最新列表时调用 `list_shells`。

### 为单条命令指定 shell

`run_command` 支持可选的 `shell` 参数，由模型自己判断本次用哪个 shell 执行：

```json
{"command": "grep -rn TODO .", "shell": "git-bash", "timeout_seconds": 30}
```

- 不传 `shell` 就用当前默认 shell，行为和以前一致。
- 传了 `shell` 就只用这个 shell 执行，**不改动全局默认**，也不做自动回退——既然已经明确选了 shell，回退到别的 shell 会违背意图（比如 POSIX 命令在 PowerShell 里跑会出错）。
- 命令要按所选 shell 的语法写：POSIX 用 `grep`/`sed`/`awk`，PowerShell 用 `Get-ChildItem`/`Select-String`，cmd 用 `dir`/`findstr`。
- 只读判定按所选 shell 的语法计算：在 Git Bash 里 `cat` 属于只读命令，不需要批准；在 PowerShell 里所有命令都按可能修改状态处理。
- shell 名不存在时直接报错，并附上当前可用 shell 列表。
- 需要持久改变默认 shell 时用 `switch_shell` 或 `/shell`。

`run_command` 的工具描述里会动态注入当前默认 shell 和可用 shell 列表，模型据此自行选择：用哪个 shell 是模型自己的判断，不必每次问用户。

执行方式：

- POSIX / Git Bash：`shell -c <command>`
- PowerShell：`pwsh -NoLogo -NoProfile -NonInteractive -Command <command>`
- cmd：`cmd /d /s /c "chcp 65001>nul & <command>"`

Windows 乱码处理：

- PowerShell 自动设置 `[Console]::OutputEncoding` 和 `$OutputEncoding` 为 UTF-8。
- cmd 自动执行 `chcp 65001`。
- Python 捕获字节后按 UTF-8 解码，失败时回退到 UTF-16、OEM/ANSI 代码页和系统 locale。
- 非法字节用替换字符处理，不会因为编码异常中断工具调用。
- 少数老程序仍可能忽略代码页；可以改用 PowerShell 7 或 Git Bash。

### 自动回退

`run_command` 只在命令被当前 shell 明确判定为“不认识”，或当前 shell 无法启动时尝试备用 shell，例如：

- POSIX：exit 127 + `command not found`
- PowerShell：`is not recognized as the name of a cmdlet`
- cmd：exit 9009 + `is not recognized as an internal or external command`

行为：

- 按当前可用 shell 的顺序尝试，成功时在结果里标注 `[fallback]`。
- 回退只对本次命令生效，不改变当前 shell；需要持久切换用 `/shell` 或 `switch_shell`。
- 所有 shell 都不认识时，结果会给出 `[fallback] reason`、每个 shell 的简短错误，以及安装工具/更换命令的建议。
- 普通失败（权限、文件不存在、语法错误等）不会触发回退，避免重复执行。
- 显式指定了 `shell` 的命令不做回退；失败时提示换一个 shell 或改写成该 shell 的语法。

### 只读判断

- POSIX / Git Bash：保留命令白名单判断。
- PowerShell / cmd：一律视为可能修改状态。开启 `--confirm` 时所有命令都需要确认。
- 判断按本次实际使用的 shell 走：指定 `shell: "git-bash"` 时用 POSIX 白名单，指定 PowerShell 时一律视为可能修改状态。

## 参数

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `prompt` | 无 | 一次性任务；省略则进入交互模式 |
| `--base-url` | `$OPENAI_BASE_URL` 或 `https://api.deepseek.com/v1` | 接口地址，末尾的 `/v1` 由此参数给出 |
| `--api-key` | `$OPENAI_API_KEY` | DeepSeek key，也可用环境变量或此参数提供 |
| `-m, --model` | `$OPENAI_MODEL` 或 `Deepseek-V4.1-Flash` | 模型名 |
| `--temperature` | 不发送 | 采样温度；部分推理模型不接受该字段，因此默认省略 |
| `--max-tokens` | 不发送；Anthropic/Responses 默认 8192 | 最大输出 token |
| `--api-format` | `openai-chat-completions` | `openai-chat-completions`（`/chat/completions`）、`openai-responses`（`/responses`）、`anthropic-messages`（`/v1/messages`）；默认值可在代码顶部 `DEFAULT_API_FORMAT` 修改 |
| `--anthropic-version` | `2023-06-01` | Anthropic API 版本头 |
| `--anthropic-auth` | `x-api-key` | Anthropic 认证方式：`x-api-key` 或 `bearer` |
| `--max-steps` | `100` | 每轮最多调用模型多少次 |
| `--timeout` | `120` | HTTP 超时（秒） |
| `--cwd` | 当前目录 | 默认工作目录、相对路径基准和新会话的默认工作空间；文件工具支持绝对路径访问其他目录 |
| `-i, --image` | 无 | 给任务附加图片，可重复，最多 4 张、单张 10 MB |
| `--confirm` / `--no-confirm` | 关 | 是否在写文件和会修改状态的命令前询问；只读命令从不询问，`--yes` 是 `--no-confirm` 的别名 |
| `--no-shell` | 关 | 禁用 `run_command` |
| `--shell` | `auto` | shell：`auto`、`pwsh`、`powershell`、`git-bash`、`cmd`、`bash`、`zsh`、`sh` 或绝对路径 |
| `--no-shell-fallback` | 关 | 当前 shell 不认识命令时，不自动尝试其他 shell |
| `--command-timeout` / `--max-command-timeout` | `60` / `600` | 命令默认超时和最大允许超时 |
| `--background-after` / `--no-auto-background` | `30` | 前台命令超过多少秒自动转后台；`--no-auto-background` 关闭 |
| `--max-background-jobs` / `--background-output-chars` | `8` / `20000` | 后台任务并发上限和每路输出保留字符数 |
| `--job-follow` / `--no-job-follow` | 开 | 后台任务是否默认跟随输出 |
| `--job-poll-seconds` / `--job-follow-lines` | `2` / `5` | 跟随轮询间隔和每次显示行数 |
| `--job-log` / `--no-job-log` | 开 | 是否把后台任务输出写入日志文件 |
| `--job-log-dir` / `--job-log-keep` | `~/.benben-agent/jobs` / `20` | 日志目录和保留数量 |
| `--stream` / `--no-stream` | 交互终端开 | 流式读取模型输出；管道和脚本默认关闭，`--no-stream` 保留完整 Markdown 渲染 |
| `--stream-idle-timeout` | `300` | 流式期间多少秒没有任何事件视为超时 |
| `--stream-flush-ms` | `40` | CLI 流式刷新间隔；`0` 表示每块都刷新 |
| `--debug-stream` / `--no-debug-stream` | 关 | 只向 stderr 打印 SSE 事件类型，不打印正文 |
| `--mcp` / `--no-mcp` | 关 | 是否启用配置文件里的 MCP 服务；默认关闭，需要显式开启 |
| `--mcp-timeout` | `60` | MCP 请求超时（秒） |
| `--skill` / `--no-skill` | 关 | 是否扫描 Agent Skills 并向模型暴露技能工具；默认关闭，需显式加 `--skill` |
| `--only-own-skills` | 关 | 配合 `--skill`：只加载 `~/.benben-agent/skills/`，忽略 Claude、Codex 和跨客户端目录 |
| `--no-skill-scripts` | 关 | 启用技能时禁用 `run_skill_script`；技能说明和资源仍可用 |
| `--schedule` / `--no-schedule` | 关 | 是否启用定时任务并暴露调度工具；默认关闭，需显式加 `--schedule` |
| `--memory` / `--no-memory` | 关 | 是否启用长期记忆和情景记忆工具；默认关闭 |
| `--memory-daily` / `--no-memory-daily` | 关 | 启动时补写昨天的自动情景记忆 |
| `--memory-daily-time` | 无 | 配合 `--schedule`：每天到这个本地时间生成情景记忆 |
| `--auto-compact` / `--no-auto-compact` | 关 | 上下文达到阈值时自动压缩 |
| `--auto-compact-level` | `normal` | 8 档预设之一 |
| `--auto-compact-threshold` / `--auto-compact-target` / `--auto-compact-keep` | 档位预设 | 覆盖触发百分比、目标保留百分比和最多保留回合 |
| `--idle-compact` / `--no-idle-compact` | 关 | 空闲后下一次对话前自动压缩 |
| `--idle-compact-minutes` | `10` | 空闲分钟数 |
| `--idle-compact-level` | 跟随 auto | 空闲压缩档位 |
| `--idle-compact-min-percent` / `--idle-compact-min-messages` | `20` / `6` | 空闲压缩的最低上下文和消息门槛 |
| `--plain` / `--no-plain` | 关 | 非流式时是否渲染 Markdown；`--no-plain` 可覆盖配置文件 |
| `--verbose-tools` / `--no-verbose-tools` | 关 | 显示完整工具参数和工具结果（最多 20000 字符） |
| `--thinking` / `--no-thinking` | 关 | 在回答前用暗色打印模型的 `reasoning_content`；交互模式可用 `/thinking on\|off` 切换 |
| `--all-skill-dirs` | 关 | 关闭 `only_own_skills`，重新扫描全部技能目录 |
| `--reasoning` | 不发送 | 思考控制，格式 `provider:level`；provider 支持 `deepseek`/`kimi`/`minimax`，level 支持 `off/minimal/low/medium/high/max`；详见下文 |
| `--plan` / `--no-plan` | 关 | 计划模式：先调查出计划，批准后再动手；详见下文 |
| `--plan-guard` | `off` | 计划模式守卫：`off`（只靠提示词）/ `approve`（改状态要确认）/ `deny`（直接拒绝） |
| `-c, --continue` | 无 | 恢复会话；省略 UUID 时取最新会话，没有会话则新建 |
| `--list` | 关 | 列出已保存的会话并退出，不联网、不需要 API key |
| `--version` |  | 显示版本并退出 |
| `--check` | 关 | 离线自检配置、目录、权限、会话、调度和模型能力，不联网 |
| `--doctor` | 关 | 在 `--check` 基础上测试模型 API 连通性 |
| `--web` | 关 | 启动浏览器界面；默认免确认，加 `--confirm` 后启用审批弹窗 |
| `--host` | `127.0.0.1` | 网页监听地址 |
| `--port` | `8765` | 网页端口，`0` 表示由系统分配 |
| `--connect` | 关 | 打开连接配置向导；没有 prompt/`--web` 时配置完退出 |
| `--profile NAME` | 无 | 使用指定保存连接，不修改配置里的 active |
| `--config PATH` | `$BENBEN_CONFIG` 或 `~/.benben-agent/config.json` | 配置文件路径；文件不存在时写入完整默认值 |
| `--no-config` | 关 | 启动时忽略配置文件，也不创建配置文件 |
| `--no-env` | 关 | 忽略 `OPENAI_BASE_URL` / `OPENAI_API_KEY` / `OPENAI_MODEL` |

> 有 active 保存连接时，`--base-url`、`--api-key`、`-m`、`--api-format` 的默认值来自保存连接；环境变量和显式参数会按上节优先级覆盖它。

## 配置文件默认值

`~/.benben-agent/config.json` 除了连接，还会保存一组 `defaults`：

```json
{
  "version": 2,
  "active": "deepseek",
  "connections": {},
  "mcpServers": {},
  "defaults": {
    "cwd": null,
    "shell": "auto",
    "shell_enabled": true,
    "shell_fallback": true,
    "confirm": false,
    "thinking": false,
    "reasoning": null,
    "plan_mode": false,
    "plan_guard": "off",
    "max_steps": 100,
    "timeout": 120.0,
    "temperature": null,
    "max_tokens": null,
    "plain": false,
    "verbose_tools": false,
    "skills": false,
    "only_own_skills": false,
    "skill_scripts": true,
    "schedule": false,
    "memory": false,
    "memory_daily": false,
    "memory_daily_time": null,
    "auto_compact": {
      "enabled": false,
      "level": "normal",
      "threshold": null,
      "target_ratio": null,
      "keep": null
    },
    "idle_compact": {
      "enabled": false,
      "minutes": 10,
      "level": null,
      "min_context_percent": 20,
      "min_messages": 6
    },
    "command_timeout": 60,
    "max_command_timeout": 600,
    "background_after_seconds": 30,
    "max_background_jobs": 8,
    "background_output_chars": 20000,
    "job_follow": true,
    "job_poll_seconds": 2,
    "job_follow_lines": 5,
    "job_log": true,
    "job_log_dir": null,
    "job_log_keep": 20,
    "stream": true,
    "stream_idle_timeout": 300,
    "stream_flush_ms": 40,
    "debug_stream": false,
    "mcp": false,
    "mcp_timeout": 60,
    "web": {"host": "127.0.0.1", "port": 8765}
  }
}
```

规则：

- 配置文件不存在时，启动会写入完整默认值，只写一次
- 已有配置缺少字段时使用内置默认值，不会自动改写
- 优先级：显式 CLI 参数 > `config.json` 的 `defaults` > 内置默认值
- `--no-config` 启动时忽略保存的默认值；配置文件仍然保留给 `/connect` 和 Web 设置页，不会自动创建
- `cwd` 默认 `null`，表示使用当前目录
- `/config save` 会把当前有效设置写回 `defaults`，包括当前 `cwd` 和 shell
- `/config save` 会保留 `connections` 和 `mcpServers`，不会保存 API key 之外的临时连接状态
- `--no-thinking`、`--no-plain`、`--no-verbose-tools`、`--all-skill-dirs` 可以覆盖配置文件里的 true
- Web 端「工具与帮助 → 默认设置」可以编辑这些默认值；保存时立即生效项会更新当前进程，其余标记为重启生效
- 设置 API：`GET /api/settings` 读取默认值和内置值，`POST /api/settings` 保存；响应不会包含连接或 MCP 的密钥
- 对话进行中保存设置会返回 409，先停止当前请求再保存

交互模式：

```text
/config          查看配置文件路径和已保存默认值
/config path     只打印配置文件路径
/config save     保存当前有效设置
/config backup   创建带时间戳的配置备份
/config backups  列出配置备份
/config restore [file]
                 恢复备份，默认恢复 config.json.bak
```

例如先运行一次：

```bash
python3 benben-agent.py --memory --thinking --max-steps 50
```

然后在交互模式执行：

```text
/config save
```

下次直接启动就会使用这些设置，不必重复加参数。注意 `cwd` 也会被保存为当前工作区的绝对路径；如果希望跟随当前目录，请保持 `cwd` 为 `null`。

每次 `save_config` 覆盖前会自动保留一份 `config.json.bak`。需要多个历史版本时用 `/config backup`；恢复后需要重启 agent 才能完全生效。

## 模型能力信息

模型名称之外，还可以配置上下文大小、最大输出、输入/输出模态、工具和 reasoning 支持。存储在：

```text
~/.benben-agent/models.json
```

首次启动且没有该文件时会写入兜底配置。形状：

```json
{
  "version": 1,
  "fallback": {
    "context_window": 256000,
    "max_output_tokens": 8192,
    "supports_tools": true,
    "supports_reasoning": null,
    "notes": "Used when the model is not listed in models.json"
  },
  "models": {
    "deepseek/deepseek-chat": {
      "name": "DeepSeek Chat",
      "context_window": 128000,
      "max_output_tokens": 8192,
      "input": ["text"],
      "output": ["text"],
      "supports_tools": true,
      "supports_parallel_tools": true,
      "supports_reasoning": false,
      "reasoning_providers": [],
      "api_formats": ["openai-chat-completions"],
      "notes": "按实际网关修改",
      "updated": "2026-11-12"
    }
  }
}
```

查找顺序：

1. `provider/model` 精确匹配
2. 模型名匹配
3. 连接 profile 里的 `model_info` 覆盖
4. `models.json` 的 `fallback`
5. 内置兜底

当前兜底上下文为 **256000 tokens**，最大输出 `8192`。模型能力信息不包含价格。

连接级覆盖示例：

```json
{
  "connections": {
    "work": {
      "model": "custom-model",
      "model_info": {
        "context_window": 64000,
        "max_output_tokens": 4096,
        "input": ["text", "image"]
      }
    }
  }
}
```

使用方式：

```text
/model info                查看当前模型能力
/model info <名称|编号>     查看指定模型能力
/model config              配置/更新当前模型能力
/model config <名称|编号>   配置指定模型能力
/model list all            列出所有已保存连接和模型
/model use <连接> [模型]    切换连接，可同时切换模型
/model use --save <连接> [模型]
                           切换并写入 active 连接
/context                   查看当前上下文用量（优先用真实 token 推算）
/status                    显示模型能力和上下文
```

Web 侧边栏增加连接下拉框和模型下拉框：先选连接，再选该连接的模型；切换后如果模型能力未配置，会自动打开模型能力对话框。

配置模型时：

- `/connect` 选完模型后会询问上下文、最大输出、文本/图像支持、工具、reasoning 等
- `/model` 切换到未配置的模型时会询问是否配置
- 已有配置只展示摘要，默认不重复询问
- 非交互终端不阻塞，使用 fallback 并提示运行 `/model config`

Web 端：

- 侧边栏「模型能力」按钮可以编辑当前模型
- 切换未配置模型或保存新连接后，会自动打开模型能力对话框
- 保存写入 `~/.benben-agent/models.json`
- `GET /api/model/info` 读取
- `POST /api/model/info` 保存

字段：

```text
context_window
max_output_tokens
input: text / image / audio
output: text / image
supports_tools
supports_reasoning
reasoning_providers
notes
```

行为：

- 上下文接近 80% 时提示 `/compact`
- 模型信息明确为 text-only 时，CLI 和 Web 会拒绝发送图片
- 模型信息未知时允许图片，但会显示 fallback 来源
- `--max-tokens` 超过模型最大输出时打印警告
- `supports_reasoning=false` 或 provider 不匹配时打印警告
- `/api/models` 会尝试从上游 `/models` 响应解析上下文和模态信息；解析结果只在当前进程内使用，不写回 `models.json`

## 思考参数

`--thinking` 只控制是否显示 `reasoning_content`，不改变模型是否思考；`--reasoning` 才真正控制发给模型的思考参数。

```bash
python3 benben-agent.py --reasoning deepseek:off "..."
python3 benben-agent.py --reasoning kimi:low "..."
python3 benben-agent.py --reasoning minimax:low "..."
python3 benben-agent.py --reasoning openai:high "..."       # Chat/Responses
python3 benben-agent.py --api-format anthropic-messages \
  --reasoning anthropic:medium "..."                        # Claude extended thinking
```

| provider | `off` | 级别 | 实际发送 |
| --- | --- | --- | --- |
| `deepseek` | `thinking: {"type":"disabled"}` | `minimal`→low，`low/medium/high`，`max`→high | `thinking` / `reasoning_effort` |
| `kimi` | `thinking: {"type":"disabled"}` | 同 DeepSeek | `thinking` / `reasoning_effort` |
| `minimax` | **不支持，启动报错** | `minimal`→low，`low/medium/high/max` | `reasoning_effort` |
| `openai` | 不发送 | `minimal/low`→low，`medium`→medium，`high/max`→high | Chat：`reasoning_effort`；Responses：`reasoning.effort` |
| `anthropic` | 不发送 | 按级别映射 `budget_tokens` | `thinking: {"type":"enabled","budget_tokens":...}` |

- 不设置 `--reasoning` 时不发送任何思考字段，使用模型默认值；MiniMax-M3.1-Flash 默认是 `max`。
- DeepSeek/Kimi 的 `off` 实测能把 reasoning token 归零；MiniMax-M3.1-Flash 强制 adaptive thinking，不能关闭。
- `none` 可以作为 `off` 的别名，但 `minimax:none` 同样会报错。
- 交互模式用 `/reasoning provider:level` 设置，`/reasoning clear` 清除，`/reasoning` 查看当前值。
- 如果网关忽略了 `off`，运行时会打印一次警告。
- `qwen`、`glm`、`claude`、`gemini`、`grok` 暂不支持；指定这些 provider 会直接报错，避免误以为生效。
- Provider 要和协议匹配：
  - `openai-chat-completions`：deepseek / kimi / minimax / openai
  - `anthropic-messages`：anthropic
  - `openai-responses`：openai
- Anthropic 的 `budget_tokens` 会根据 `--max-tokens` 自动限制，确保小于输出上限。
- 切换模型时如果 provider 与模型名明显不匹配，会给出警告，但不会自动清除配置。
- `/compact` 的摘要调用复用同一份配置，`off` 时摘要也更省 token。

## Anthropic 协议

默认使用 OpenAI Chat Completions（`/chat/completions`）。加 `--api-format anthropic-messages` 后改用 Anthropic Messages API（`/v1/messages`），消息、工具和图片会在客户端内部转换；会话、技能、压缩、网页等功能保持不变。

```bash
python3 benben-agent.py \
  --api-format anthropic-messages \
  --base-url https://api.anthropic.com \
  --api-key sk-ant-... \
  -m claude-sonnet-4-5 \
  "解释这个报错"
```

- 默认认证是 `x-api-key` + `anthropic-version: 2023-06-01`；只认 Bearer 的兼容网关用 `--anthropic-auth bearer`；
- Anthropic 必须传 `max_tokens`，默认 8192，可用 `--max-tokens` 调整；
- 支持文本、工具调用、PNG/JPEG/GIF/WEBP 图片；BMP 会直接报错；
- 支持流式文本、思考和工具调用；支持 extended thinking（`--reasoning anthropic:low/medium/high`），不支持 prompt caching、PDF；
- 内网 A 系列本身走 OpenAI 兼容接口，不需要加 `--api-format anthropic-messages`。

## Responses 协议

`--api-format openai-responses` 使用 OpenAI Responses API（`/v1/responses`），支持文本、图片、工具调用和流式输出；无状态（`store: false`）。

```bash
python3 benben-agent.py \
  --api-format openai-responses \
  --base-url https://api.openai.com/v1 \
  --api-key sk-... \
  -m gpt-5 \
  --max-tokens 4096 \
  "..."
```

- 系统提示放在顶层 `instructions`；
- 工具使用 Responses 的扁平 function schema，工具结果用 `function_call_output` 回传；
- 不使用 `previous_response_id`，每次发送完整历史，保证 `-c` 恢复一致；
- `max_output_tokens` 默认 8192，可用 `--max-tokens` 调整；
- 支持 PNG/JPEG/GIF/WEBP 图片，BMP 会报错；
- `store` 固定为 `false`；
- 支持流式输出和 `reasoning.effort` 映射（`--reasoning openai:low/medium/high`）；
- 不支持内置工具（web_search/file_search/computer_use）；
- 内网网关也支持 `/v1/responses`，可以直接用 `--api-format openai-responses`。

## MCP

MCP 客户端默认关闭，需要显式开启：

```bash
python3 benben-agent.py --mcp
```

配置写在 `config.json` 顶层的 `mcpServers`：

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/home/yzx/mycode"],
      "env": {},
      "cwd": "/home/yzx/mycode"
    },
    "remote": {
      "type": "http",
      "url": "https://example.com/mcp",
      "headers": {"Authorization": "Bearer ..."}
    }
  },
  "defaults": {"mcp": true, "mcp_timeout": 60}
}
```

- 支持 stdio 和 Streamable HTTP
- 接入 MCP `tools`、`resources` 和 `prompts`
- 工具名使用 `mcp__server__tool`
- 资源工具：`mcp_list_resources`、`mcp_read_resource`
- 资源按需读取，不自动注入上下文
- 提示词只填入输入框，不自动发送
- 工具带 `readOnlyHint=true` 时可被只读任务使用
- 定时任务可以使用 MCP 工具；只读任务只暴露只读工具
- 计划模式按 `off` / `approve` / `deny` 门控
- 服务启动失败只打印 warning，不影响主程序
- 退出时关闭 HTTP session 和 stdio 子进程
- `/config save` 会保留 `mcpServers`
- MCP 全局关闭时不向模型暴露任何 MCP 工具
- 添加/修改/删除服务立即写回配置
- 添加服务不会自动打开全局 MCP；需要 `/mcp on` 或 Web 顶部开关
- Web 不返回 header/env 的敏感值

Web 管理：

```text
左侧栏 MCP → 管理 MCP
```

支持增删改、单个服务启停、全局开关、重启、日志和测试连接。

CLI 命令：

```text
/mcp                          查看服务状态
/mcp on|off                   全局启用/关闭
/mcp add [服务]               引导式添加
/mcp edit <服务>              引导式编辑
/mcp remove <服务>            删除
/mcp enable|disable <服务>    单个服务启停
/mcp test <服务>              测试连接
/mcp tools [服务]             查看工具
/mcp resources [服务]         列出资源
/mcp resource <服务> <uri>    读取资源
/mcp prompts [服务]           列出提示词模板
/mcp prompt <服务> <名称> [key=value ...]
                              渲染提示词
/mcp logs <服务>              查看 stderr / 通知
/mcp restart <服务>           重启服务
```

## 会话持久化

每个会话保存为 `~/.benben-agent/sessions/` 下的 JSONL 文件，一行一条消息：

```text
~/.benben-agent/sessions/001-20260911-a1b2c3d4.jsonl
```

- 旧版本放在 `~/.benben-agent/*.jsonl` 的会话会在启动时自动移动到 `sessions/`；
- 文件名：全局递增序号 + 日期 + 8 位 uuid；
- 目录权限 `0700`，文件 `0600`；
- system prompt 不保存，启动时按当前配置重新生成；
- 思考内容和图片的 base64 都会保存，带图片的会话文件会明显变大；
- 加载时跳过损坏的行，进程被杀留下的半行不会导致失败；
- 启动恢复或 Web 打开会话时自动修复未完成的 tool_call：补一条 `error: interrupted` 工具结果，避免下一轮 API 请求因历史不完整而失败；
- 文件末尾如果没有换行，加载时会自动补一个换行，避免新记录和半行拼接；
- 定时任务定义和运行记录单独保存在 `~/.benben-agent/schedules/`，见下文「定时任务」；
- 长期记忆和情景记忆保存在 `~/.benben-agent/memory/`，见下文「长期记忆与情景记忆」；
- 模型能力信息保存在 `~/.benben-agent/models.json`，见下文「模型能力信息」。

列出和恢复：

```bash
python3 benben-agent.py --list
python3 benben-agent.py -c                             # 恢复最新会话；没有就新建
python3 benben-agent.py -c a1b2c3d4                    # 交互模式继续指定会话
python3 benben-agent.py -c a1b2c3d4 "继续刚才的任务"   # 一次性继续指定会话
python3 benben-agent.py -c "继续刚才的任务"            # 最新会话 + 一次性任务
```

`-c` 后面的值只有是 8 位小写十六进制时才当作会话 id，否则按 prompt 处理。

`--list` 显示 uuid、日期、消息数、模型、工作区和首条用户消息，按序号倒序。恢复会带回消息历史，并自动切换到会话记录的工作空间；`--cwd` 只作为新会话的默认工作空间。

交互模式里用 `/session` 管理会话：

```text
/session                    列出最近会话
/session list all           列出全部会话
/session list <关键词>      按标题/UUID/模型过滤
/session use <编号|uuid>    切换会话，并恢复其工作空间
/session new                使用当前工作空间开始新会话（默认为启动全局工作空间）
/session new <工作空间>     指定工作空间开始新会话
/session info               查看当前会话
/session info <编号|uuid>   查看指定会话
/session export <编号|uuid> [--tool] [--thinking] [文件]
                            导出 Markdown
/session rename <新标题>    修改当前会话标题
/session delete <编号|uuid> 移到回收站（不能删除当前会话）
/session pin|unpin <编号|uuid>
                            置顶或取消置顶
/session archive|unarchive <编号|uuid>
                            归档或取消归档
/session trash              打开或查看回收站
/session trash delete <uuid> 从回收站永久删除单个会话
/session trash empty        清空回收站
/session restore <uuid>     从回收站恢复会话
/session prune [--keep N] [--older-than 30d] [--max-size 500M] [--yes]
                            批量移动旧会话到回收站
```

### 回收站和 prune

删除会话不会立刻删除文件，而是移到：

```text
~/.benben-agent/sessions/trash/
```

- `/session trash` 列出回收站，包含大小和删除时间
- `/session restore <uuid>` 恢复
- `/session trash delete <uuid>` 永久删除单个会话
- `/session trash empty` 永久清空
- `/session prune` 默认 dry-run，只列出候选
- 加 `--yes` 才真正移动到回收站
- 当前会话始终受保护，不会被 prune

示例：

```text
/session prune --keep 100
/session prune --older-than 90d
/session prune --max-size 500M
/session prune --keep 100 --yes
```

置顶、归档和列表：

```text
/session pin <uuid>       置顶
/session unpin <uuid>     取消置顶
/session archive <uuid>   归档
/session unarchive <uuid> 取消归档
```

- 置顶会话排在最前
- 归档会话默认在 Web 隐藏，勾选「显示归档」后出现
- 列表显示标记 `P`（置顶）、`A`（归档）和会话大小
- Web 会话区按「置顶 / 今天 / 昨天 / 日期」分组
- 当前会话不能被归档

工作空间指令：

```text
/workspace                  查看当前和最近工作空间
/workspace list             列出最近工作空间
/workspace use <编号>       切换到最近工作空间并开始新会话
/workspace <路径>           指定工作空间并开始新会话
```

工作空间规则：

- 支持 `~` 和相对路径，相对路径按当前工作空间解析；
- 必须是已存在的目录；
- 不指定时使用当前全局工作空间（启动时的 `--cwd` 或配置文件里的 `cwd`）；
- 切换工作空间会清空当前会话历史，下一次消息写入新会话文件；
- 旧工作空间里仍在运行的后台任务会继续运行，并给出提示；
- `/config save` 会把当前工作空间保存为默认 `cwd`。

Web 端点击「+ 新建」会打开工作空间对话框，默认填当前全局工作空间，也可以从最近工作空间里选择或手动输入。Web 输入 `/session new <路径>` 或 `/workspace <路径>` 行为相同。

`/session export` 默认只导出对话正文；`--tool` 同时导出工具调用和工具结果，`--thinking` 导出思考内容。不指定文件时写入当前工作区的 `session-<uuid>.md`。

`/reset` 仍会清空上下文并结束当前会话文件，下一条消息写入新文件。Web 侧边栏提供会话列表和新建，新建时可以选择工作空间；删除会话放在右键菜单里，右键还可以修改名称或导出 Markdown，聊天框输入 `/session` 也可以管理。

### 用量统计

每次回答结束会在 stderr 打印一行用量（Web 端同样透传到聊天界面）：

```text
[usage] last: input=2757 cached=2656 new=101 hit=96% | context=2848/1000000 (0%) | total: calls=2 input=5413 new=2571 cached=2842 hit=52% output=91 reasoning=0 tokens=5504
```

| 段 | 字段 | 含义 |
| --- | --- | --- |
| `last:` | `input` / `cached` / `new` / `hit` | 最后一次模型调用的用量；`new = input - cached` 是实际新读入，`hit` 是缓存命中率 |
| `context=` | `tokens/window (percent)` | 下一轮上下文占用；以最后一次真实 prompt token 为锚点，加上本轮输出和本地估算增量。没有可用基线时才回退到字符估算并带 `~` |
| `total:` | `calls` / `input` / `cached` / `hit` / `output` / `reasoning` / `tokens` | 本次回答内所有模型调用的累计；`calls` 是调用次数，`tokens = input + output` |

- 只调用一次模型时 `last` 与 `total` 数值相同；触发工具调用后 `calls` 增长，`total` 是累加值；
- `last` 是“最后一次调用”，不是整轮累计；
- `hit` 里 `last` 反映最后一次调用命中情况，`total` 会被首轮冷启动拉低；
- `reasoning` 是 `output` 的子集，不会再次计入 `tokens`；
- `/context` 命令同样优先用真实值推算；压缩会重置基线并回退到估算。

### 压缩上下文

`/compact [N]` 把较早的对话交给模型总结，默认保留最近 2 个用户回合（`N=0` 表示不保留）。压缩会在 JSONL 末尾追加一条 `_type: "compact"` 记录，后面再写一遍保留的轮次；原历史一行不删。`-c` 和网页加载时只从**最后一条**压缩记录开始重建上下文，所以文件仍然完整，但不会把旧历史发给模型。

- 摘要固定包含目标、已完成、关键决策、文件路径、执行过的命令、未完成事项和注意事项；
- 待压缩内容过长时会分块滚动总结；
- 图片只以 `[图片]` 占位进入摘要，base64 不会发进摘要请求；
- 网页输入框下方的「压缩」按钮等价于 `/compact`，压缩后刷新页面仍会显示摘要提示。

### 自动压缩

默认关闭。开启后，上下文达到阈值时，在发送下一次请求前自动压缩：

```bash
python3 benben-agent.py --auto-compact
python3 benben-agent.py --auto-compact --auto-compact-level aggressive
```

8 档预设：

| level | 触发阈值 | 目标保留 | 最多保留用户回合 |
| --- | ---: | ---: | ---: |
| `minimal` | 95% | 85% | 16 |
| `very-light` | 90% | 75% | 12 |
| `light` | 85% | 65% | 8 |
| `normal` | 80% | 50% | 5 |
| `aggressive` | 70% | 35% | 3 |
| `very-aggressive` | 60% | 20% | 2 |
| `extreme` | 50% | 12% | 1 |
| `maximum` | 40% | 5% | 1 |

自动压缩至少保留当前用户回合。自定义覆盖：

```bash
--auto-compact-threshold 75
--auto-compact-target 40
--auto-compact-keep 3
```

Web 端可以不重启直接改：侧边栏「压缩设置」，或 `POST /api/compact/config`。请求体形如：

```json
{
  "auto": {"enabled": true, "level": "aggressive", "threshold": null, "target_ratio": null, "keep": null},
  "idle": {"enabled": false, "minutes": 10, "level": null, "min_context_percent": 20, "min_messages": 6},
  "persist": false
}
```

- `null` 表示跟随档位预设；`level` 必须是上表 8 档之一
- 阈值、目标、保留回合取值 1-100；空闲分钟数 1-1440
- `persist` 为 `true` 时同时写入 `config.json` 的 `defaults.auto_compact` / `defaults.idle_compact`
- `GET /api/compact/config` 读取当前档位表、解析后的生效值和原始覆盖值

### 空闲压缩

默认关闭。开启后，如果距离上一次对话已经超过设定时间，用户下一次发消息前会先压缩旧上下文，再处理新消息：

```bash
python3 benben-agent.py --idle-compact --idle-compact-minutes 10
```

保护条件：

- 上下文至少占窗口 `--idle-compact-min-percent`，默认 20%
- 消息数至少 `--idle-compact-min-messages`，默认 6
- 空闲档位默认跟随自动压缩档位
- 同一轮最多自动压缩一次
- 压缩失败只提示，不阻塞对话

交互模式：

```text
/compact auto                    查看状态
/compact auto on|off             开关
/compact auto presets            列出档位
/compact auto level aggressive   切换档位
/compact auto threshold 80       覆盖触发百分比
/compact auto target 50          覆盖目标保留百分比
/compact auto keep 5             覆盖最多保留回合

/compact idle                    查看状态
/compact idle on|off             开关
/compact idle minutes 10         设置空闲分钟数
/compact idle level normal       设置档位
/compact idle min 20             设置最低上下文百分比
/compact idle messages 6         设置最少消息数
```

`/config save` 会保存自动压缩和空闲压缩配置。

注意：压缩只减少发送给模型的 token，不会删除或缩短磁盘上的会话 JSONL。

## 技能（Agent Skills）

兼容 [Agent Skills](https://agentskills.io/specification) 开放格式。技能默认关闭，加 `--skill` 才扫描并向模型暴露技能工具；启用后按需加载，不会在启动时把全部说明和脚本塞进上下文：

```text
<workspace>/.agents/skills/      # 项目级，跨客户端
<workspace>/.claude/skills/      # Claude Code 项目级
<workspace>/.codex/skills/       # Codex 旧路径（项目级）
~/.benben-agent/skills/          # 本项目全局
~/.agents/skills/                # 跨客户端 / Codex 现行用户目录
~/.claude/skills/                # Claude Code 个人目录
~/.codex/skills/                 # Codex 旧用户目录（$CODEX_HOME/skills）
/etc/codex/skills/               # Codex 管理员目录
```

同名技能按上面的顺序优先，先加载的生效。Codex 的 `$CODEX_HOME` 环境变量会生效；Claude 的技能可以省略 `name`，此时直接用目录名。

只扫描工作区根目录和上述用户目录，不会像 Codex/Claude 那样向上遍历父目录；需要在父目录共享技能时，用软链接或把技能放进工作区。加 `--only-own-skills` 后只扫描 `~/.benben-agent/skills/`，不会加载其他客户端的技能。

每个技能是一个目录：

```text
log-triage/
├── SKILL.md          # 必需：YAML frontmatter + 指令
├── scripts/          # 可选：可执行脚本
├── references/       # 可选：参考文档
└── assets/           # 可选：模板和资源
```

```markdown
---
name: log-triage
description: 按时间线归纳日志。处理日志排查、异常定位时使用。
---

## 步骤
1. 查看最近日志…
```

- `name` 可省略（Claude 风格，默认用目录名）；提供时必须是小写字母/数字/连字符；`description` 为 1-1024 字符；
- 启动只读 frontmatter；`load_skill` 才加载完整 SKILL.md；资源和脚本只在真正需要时读取；
- 支持 `license`、`compatibility`、`metadata`；`allowed-tools` 会解析但暂不生效；
- Claude 的 `!` 命令注入、`$ARGUMENTS` 等宿主特性不会执行，技能正文按普通文本加载。

模型侧看到技能清单和管理工具，并按需调用：

| 工具 | 作用 | 需要批准 |
| --- | --- | --- |
| `load_skill` | 读取完整 SKILL.md | 否 |
| `read_skill_resource` | 读取技能目录内的参考文件 | 否 |
| `run_skill_script` | 执行 `scripts/` 下的脚本 | 是 |
| `create_skill` | 创建或更新技能 | 否 |
| `install_skill` | 安装目录、zip 或 git 技能 | 中高风险需要 |
| `remove_skill` | 请求删除技能 | 总是需要 |

### 创建和更新

`create_skill` 默认写入全局技能目录：

```text
~/.benben-agent/skills/<name>/
```

需要工作区技能时显式传：

```json
{"scope": "workspace"}
```

写入 `<workspace>/.agents/skills/<name>/`。同名技能视为更新：只替换 `SKILL.md`，保留已有的 `scripts/`、`references/` 和 `assets/`，并保留 `license`、`compatibility`、`metadata`、`allowed-tools` 等其他 frontmatter 字段。

### 安装

`install_skill` 支持：

- 本地技能目录
- `.zip` 文件
- git URL（`https://`、`ssh://`、`git@`、`git://`）
- 直接下载的 `.zip` URL

安装会先复制/解压/克隆到暂存目录，校验 `SKILL.md` 和 frontmatter，再做风险扫描。同名技能会替换整个技能目录，旧版本先移动到备份目录；移动失败会自动回滚。

git 使用参数数组执行：

```bash
git clone --depth 1 --single-branch <url> <temp>
```

不经过 shell，不执行仓库里的安装脚本，默认超时 120 秒。zip 会拒绝绝对路径、`..` 路径穿越、符号链接、超大解压体积和过多文件。

### 风险扫描与批准

自动扫描内容：

- 脚本、可执行文件、二进制文件
- `curl`、`wget`、外链、网络访问
- `rm -rf`、`sudo`、`chmod 777`、`dd`、fork bomb、`/dev/tcp`
- `eval`、`base64 -d`、编码 PowerShell
- 环境变量、token、password、secret
- 提示词注入语句
- 符号链接、路径穿越、隐藏文件、异常大小或文件数

风险分级：

| 等级 | 示例 | 行为 |
| --- | --- | --- |
| `low` | 纯 Markdown/文本 | 直接安装 |
| `medium` | 含脚本、网络、外链、二进制 | 进入待确认 |
| `high` | 危险命令、提示词注入、符号链接 | 进入待确认 |
| `unknown` | 扫描失败 | 进入待确认 |

模型调用 `install_skill` 遇到中高风险时，返回 `approval_required`，包含 `approval_id`、风险等级和原因。模型必须向用户说明风险并等待批准，不能自行批准。

`remove_skill` 总是返回 `approval_required`，不会直接删除。批准后技能目录移动到备份目录，而不是永久删除。

批准入口只属于用户：

```text
/skill pending
/skill approve <id>
/skill reject <id>
```

Web 端在「技能 → 管理技能 → 待确认」里批准或拒绝。

### 备份与暂存

```text
~/.benben-agent/skill-backups/    # 每个技能保留最近 5 个版本
~/.benben-agent/skill-staging/    # 安装暂存
~/.benben-agent/skill-pending/    # 待确认记录
```

待确认记录默认 24 小时过期。批准前会重新校验暂存内容或技能目录指纹，内容变化时要求重新确认。备份目录是普通技能目录，需要恢复时手动复制回对应的技能根目录即可。

### CLI

```text
/skill                        列出技能
/skill <name>                 查看技能说明
/skill <name> <问题>          强制加载技能来回答
/skill create                 引导式创建或更新（默认全局）
/skill install                从目录、zip 或 git URL 安装
/skill remove <name> [scope]  删除技能（移动到备份）
/skill reload                 重新扫描技能目录
/skill pending                查看待确认操作
/skill approve <id>           批准安装或删除
/skill reject <id>            拒绝并清理
```

### Web

左侧栏「技能 → 管理技能」支持：

- 列表、搜索、按作用域过滤
- 查看详情和文件列表
- 创建技能
- 编辑描述和正文
- 安装本地目录、zip 或 git URL
- 删除（确认后移动到备份）
- 查看待确认并批准/拒绝
- 刷新和重载

Web API：

```text
GET  /api/skills?q=&scope=
GET  /api/skills/detail?name=&scope=
GET  /api/skills/pending
POST /api/skills/create
POST /api/skills/update
POST /api/skills/install
POST /api/skills/remove
POST /api/skills/reload
POST /api/skills/approve
POST /api/skills/reject
```

脚本按 shebang、扩展名（py/sh/js/rb/pl/php/ps1）或可执行文件运行；cwd 是当前工作区，并注入 `BENBEN_SKILL_DIR` 和 `BENBEN_WORKSPACE`。启用技能时脚本默认可用，`--no-skill-scripts` 关闭；`--confirm` 下每次执行都要确认；web 模式没有脚本审批，启动会打印警告。

技能默认关闭，用 `--skill` 开启；`--no-skill` 可显式关闭。关闭时不扫描技能目录，也不向模型暴露任何技能工具，CLI 和 Web 管理同样不可用。`--only-own-skills` 下只加载全局技能，创建/安装工作区技能会被拒绝。

## 定时任务（Scheduler）

定时任务默认关闭，和技能一样需要显式启用：

```bash
python3 benben-agent.py --schedule                 # 交互模式 + 定时任务
python3 benben-agent.py --schedule --web           # 网页模式 + 定时任务
```

- 调度器是**进程内**的：只有交互或网页进程运行时任务才会触发，进程退出后不会执行；
- 不做外部通知（`notify-send`、`osascript`、Windows Toast、邮件、Webhook），结果只通过网页面板、CLI 和运行记录反馈；
- 默认关闭时不会读取 `~/.benben-agent/schedules/`，也不会向模型暴露任何调度工具；`/schedule` 会提示用 `--schedule` 开启。

启用后，模型会多出这些工具：

| 工具 | 作用 |
| --- | --- |
| `create_schedule` | 创建任务：名称、提示词、时间表达式、可选 `read_only` |
| `list_schedules` | 列出任务、下次运行、上次状态 |
| `delete_schedule` | 删除任务及其运行记录 |
| `enable_schedule` | 启用或暂停任务 |
| `run_schedule_now` | 立即排队执行一次 |
| `schedule_history` | 查看最近运行记录 |

可以直接用对话创建，例如：

```text
用户：每天早上 9 点检查磁盘使用率，超过 85% 就给出告警

模型：创建定时任务
  ID: a1b2c3d4
  计划: daily 09:00
  下次运行: 2026-11-13 09:00
  模式: 完整权限（定时触发时无人审批）
```

`when` 支持：

```text
every 30s / every 10m / every 2h / every 1d
daily 09:00
once 2026-11-12T15:00
once 2026-11-12 15:00
```

时间按本机本地时间计算，不支持时区后缀和 cron 表达式。权限默认是**完整权限**：定时触发时无人审批，可以读写文件和执行命令。创建时传 `read_only=true` 则只暴露 `list_dir`、`read_file`、`search`、`glob`。定时任务运行在独立会话里，不插入当前聊天；任务内部不再暴露调度工具，避免递归创建任务。

数据位置：

```text
~/.benben-agent/schedules/tasks.json          # 任务定义（0600）
~/.benben-agent/schedules/runs/<id>.jsonl     # 每次运行的时间、状态、输出、耗时（0600）
```

- 单 worker 串行执行，同一任务不会并发；
- 错过执行只补一次，不会在启动时补跑所有历史周期；
- 运行结果写入 JSONL，并在 CLI 下一次输入前提示，网页每 5 秒轮询任务面板和新结果。

交互模式管理命令：

```text
/schedule                    查看任务
/schedule history <id> [n]   查看运行记录
/schedule run <id>           立即排队执行
/schedule on <id>            启用
/schedule off <id>           暂停
/schedule delete <id>        删除任务和记录
```

网页侧边栏启用后会显示「定时任务」按钮，可以查看任务、运行、启停、查看记录和删除。运行完成后，新结果会以提示形式出现在对话区。

## 计划模式（Plan Mode）

计划模式让模型先调查、再给计划，等你批准后才动手改东西。

```bash
python3 benben-agent.py --plan
python3 benben-agent.py --plan --plan-guard approve
```

交互模式：

```text
/plan                          查看当前模式和守卫档位
/plan on|off                   切换计划模式
/plan guard off|approve|deny   设置守卫档位
```

Web 端：输入框下方有「计划模式」开关和「守卫」下拉框，聊天框输入 `/plan` 也能切换。

### 工具不会被削减

计划模式**不砍工具**。读文件、搜索、跑命令、写文件全都可用，区别只在于系统提示词要求模型这一轮只做调查和规划。

- 可以跑调查性命令：看文件内容、查日志、看 git 状态和差异、跑构建或测试看报错、查依赖版本
- 不要做：写改文件、删除移动文件、安装升级依赖、改变 git 状态、改系统配置、写数据库、创建定时任务
- 例外：确实需要产生副作用才能得到结论的命令（例如必须跑一次构建才能看到编译错误），写进计划里在批准环节一并确认

约束靠提示词，不是权限封锁，所以默认档位是 `off`。

### 提交与批准

调查完成后模型调用 `submit_plan` 提交计划，内容包含目标、步骤（改哪个文件、跑什么命令）、风险与影响范围、验证方式。提交后本轮立即停止。

- CLI：打印计划后提示 `Approve and execute? [Y/n]`。批准后自动退出计划模式、重建提示词和工具，并**立即按计划执行**，不用再发第二条消息
- Web：渲染一个计划块，带「批准执行」和「继续规划」按钮。点「批准执行」同样自动执行
- 一次性模式（`--prompt`）：没有人能批准，模型提交计划后程序直接结束并打印计划，退出码 0

### 守卫档位

| 档位 | 行为 |
| --- | --- |
| `off`（默认） | 只靠提示词约束，工具全部可用 |
| `approve` | 改状态的操作需要用户批准。CLI 弹交互确认；网页端无法实时确认，改为拒绝并让模型转述给用户，等你批准计划后写操作自然恢复 |
| `deny` | 计划模式下直接拒绝改状态的操作（写文件、改文件、非只读命令、写记忆、改定时任务等） |

只读命令在 `approve` 和 `deny` 档下都不受影响。Windows 上 PowerShell/cmd 的命令一律按"会改状态"处理，POSIX 只读白名单只在 Git Bash 等 POSIX shell 下生效。

### 注意事项

- 切换模式会重建系统提示词和工具列表，会击穿一次 prompt 缓存（代价很小）
- 计划模式下模型仍能调用写工具，`off` 档完全依赖模型遵守提示词；不放心就开 `approve` 或 `deny`
- 计划保存在会话历史里，`-c` 恢复后仍能看到「计划 → 批准」的脉络；模式开关本身不随会话恢复
- 定时任务不受影响：定时运行永远按任务自己的 `read_only` 设置执行

## 流式输出

模型回复支持逐块读取：

- CLI 交互终端默认流式，正文按纯文本实时输出，不再做终端 Markdown 渲染
- CLI 管道、重定向和脚本默认非流式，保持原有完整渲染
- `--stream` 强制流式，`--no-stream` 强制非流式
- Web 默认流式，可在输入框底部取消「流式输出」或执行 `/stream off`
- 思考内容开启 `--thinking` 时同样流式显示
- 工具参数在完整后显示，不实时打印半截 JSON

运行时可切换：

```text
/stream              查看当前模式
/stream on           后续轮次流式
/stream off          后续轮次等待完整回复
/stream auto         恢复本次启动时的默认值
```

CLI 和 Web 使用同一组指令。`/config save` 会把当前值写入 `defaults.stream`。

支持三种协议：

- OpenAI Chat Completions：`stream: true` + `stream_options.include_usage`
- Anthropic Messages：`content_block_delta`
- OpenAI Responses：`response.output_text.delta` 和 `response.completed`

兼容处理：

- 网关拒绝 `stream_options` 时自动去掉并重试
- 网关不支持流式时自动回退非流式
- 网关忽略 `stream` 直接返回 JSON 时按完整响应处理
- 首字节前失败可重试；已经输出后中断会报错，不自动续写
- 流式无事件超过 `stream_idle_timeout` 会断开
- 取消或 Ctrl+C 不会把半截回复写入会话历史

配置：

```json
{
  "defaults": {
    "stream": true,
    "stream_idle_timeout": 300,
    "stream_flush_ms": 40,
    "debug_stream": false
  }
}
```

排障：

```bash
BENBEN_DEBUG_STREAM=1 python3 benben-agent.py --stream ...
```

只输出事件类型，不输出正文。

## 后台任务与跟随

耗时命令不会一直阻塞会话。`run_command` 支持 `background` 和 `follow`：

```json
{
  "command": "npm run build",
  "timeout_seconds": 600,
  "background": true,
  "follow": true
}
```

- 前台命令超过 `background_after_seconds`（默认 30 秒）会自动转后台
- 交互式 CLI 和 Web 默认跟随
- 每 2 秒读取一次新增输出，只显示最近 5 行
- 输出过多时显示省略行数
- 跟随期间不调用模型，不消耗模型 token
- 任务结束后返回退出码、耗时和输出尾部
- 成功显示最后 5 行，失败显示最后 10 行
- 完整输出同时写入 `~/.benben-agent/jobs/<job-id>.log`
- `/jobs clean` 删除已完成任务和对应日志
- 日志保留数量默认 20，可用 `--job-log-keep` 调整

CLI/Web 指令：

```text
/jobs                       列出任务
/jobs status <id>           查看状态和最近输出
/jobs output <id> [n]       查看输出
/jobs follow <id>           开始跟随
/jobs detach <id>           停止跟随，任务继续
/jobs stop <id>             取消任务，杀进程树
/jobs clean                 清理已完成任务
/jobs poll 2                设置轮询间隔
/jobs lines 5               设置跟随显示行数
```

Ctrl+C 或 Web「停止跟随」只停止观察，后台任务继续运行。取消任务要使用 `/jobs stop` 或 `job_cancel`。agent 退出时会停止所有后台任务；后台任务不跨进程持久化。

Web 端启动后台任务时会显示任务块，包含最近输出、「停止跟随」和「取消任务」按钮。

## 长期记忆与情景记忆

记忆默认关闭，和技能、定时任务一样需要显式启用：

```bash
python3 benben-agent.py --memory
python3 benben-agent.py --memory --memory-daily
python3 benben-agent.py --memory --memory-daily --schedule --memory-daily-time 23:50
```

- `--memory`：启用记忆工具
- `--memory-daily`：启动时补写昨天的自动情景记忆
- `--memory-daily-time HH:MM`：配合 `--schedule`，由内置定时任务每天生成当天情景记忆
- 自动生成失败不会影响正常聊天；失败不写完成标记，下次可以重试

数据位置：

```text
~/.benben-agent/memory/MEMORY.md                    # 长期记忆（0600）
~/.benben-agent/memory/episodes/YYYY-MM-DD.md       # 情景记忆（0600）
```

长期记忆适合保存稳定偏好、环境事实、项目信息和重要决策；不要保存临时任务细节、猜测、密钥或隐私数据。情景记忆一天一个 Markdown 文件，同一天可以追加多个小节。

启用后，模型会多出这些工具：

| 工具 | 作用 |
| --- | --- |
| `remember` | 追加一条长期记忆 |
| `recall` | 搜索长期记忆和情景记忆 |
| `save_episode` | 追加当天情景总结 |
| `list_episodes` | 列出最近的情景文件 |
| `read_episode` | 读取某天的情景文件 |
| `generate_daily_episode` | 生成某天的自动总结，已生成时返回 `exists` |

示例：

```text
用户：记住，我偏好简洁的中文回答

模型调用 remember(text="用户偏好简洁的中文回答", tags="偏好")
→ remembered in MEMORY.md:3
```

```text
用户：把今天完成的事情记一下

模型调用 save_episode(summary="...", tags="开发")
→ saved episode 2026-11-12 to ~/.benben-agent/memory/episodes/2026-11-12.md
```

自动情景记忆有三种入口：

- 模型主动调用 `save_episode`
- 启用 `--memory-daily` 后，启动时后台检查并补写昨天
- 同时启用 `--schedule` 和 `--memory-daily-time` 后，内置 daily 任务调用 `generate_daily_episode`

自动总结会写入标记：

```markdown
<!-- benben:auto-summary:2026-11-12 -->
```

同一个日期已经生成过时不会重复写入。自动总结会从会话 JSONL 中读取当天的用户和助手文本，忽略图片 base64，并对压缩后重复保留的 tail 去重。系统提示词不会自动注入记忆内容，模型需要时调用 `recall`。

交互模式管理命令：

```text
/memory                      查看记忆目录、条目数和自动生成配置
/memory path                 打印记忆目录
/memory search <关键词>       搜索长期记忆和情景记忆
/memory read <日期|today>     读取某天情景记忆
/memory summarize [日期]      手动生成某天自动总结
/memory dedupe [--yes]        去重长期记忆
/memory prune [--keep N] [--older-than 30d] [--episodes] [--yes]
                              清理长期记忆和/或旧情景记忆
```

`/memory dedupe` 和 `/memory prune` 默认 dry-run；加 `--yes` 才写入。
`--episodes` 配合 `--older-than` 删除更早的情景记忆文件。清理前会保留其他行和头部，只移除过期/重复条目。

## 网页入口

```bash
python3 benben-agent.py --web
# web ui: http://127.0.0.1:8765/?token=...
```

- 默认只监听 `127.0.0.1`，`--host` / `--port` 可改，`--port 0` 由系统分配；
- 启动时生成随机 token，页面和所有 API 都必须带 token，重启后失效；`GET /healthz` 无 token 返回 `ok`，`GET /api/health` 带 token 返回版本、运行时间和状态；
- `--version` 输出当前版本；`--check` / `--doctor` 用于部署前后自检；
- 页面支持对话、新建/切换/移入回收站历史会话（新建时可选择工作空间）、上传图片；侧边栏会话区占据主要高度，连接和模型收在底部「连接与模型」折叠组里；
- 侧边栏「帮助 / 指令」打开帮助弹窗，列出 Web 支持的斜杠命令和常用操作；输入 `/help` 也可以打开；
- 会话列表支持右键置顶、归档、修改名称、导出 Markdown、移到回收站；支持搜索标题、uuid、模型和工作空间，默认每次加载 50 个，可继续加载；按「置顶 / 今天 / 昨天 / 日期」分组；
- 归档会话默认隐藏，勾选「显示归档」后显示；多个标签页切换会话或工作空间时会提示刷新；
- 会话区顶部有「回收站」按钮，可查看已删除会话、恢复、永久删除单个会话或清空回收站；
- 启用 `--schedule` 后「工具与帮助」里显示「定时任务」，可查看/运行/启停/删除任务和查看运行记录，后台每 5 秒轮询一次并提示新结果；
- 工具调用显示为可展开块，默认折叠；展开后可查看完整参数和工具结果，历史会话也会回放；
- 输入框下方有「流式输出」「显示思考」开关和「思考等级」选择，旁边是「压缩」按钮；思考等级按协议显示 DeepSeek/Kimi/MiniMax/OpenAI/Anthropic 选项；
- 输入框下方还有「计划模式」开关和「守卫」下拉框；计划模式下模型提交的计划会渲染成带「批准执行」「继续规划」按钮的计划块，聊天框输入 `/plan` 也能切换；
- 生成过程中显示「停止」按钮，点击后立即中断浏览器端等待，并请求服务端停止后续模型调用和工具执行；
- 在输入框直接输入 `/compact` 或 `/compact 3` 也会被前端拦截；
- 侧边栏「工具与帮助」里显示「压缩设置」，可配置自动压缩和空闲压缩的档位、阈值、目标百分比和保留回合数，改动立即生效，可勾选同时写入配置文件；聊天框输入 `/compact auto` 也会打开这个设置；
- 侧边栏「工具与帮助 → 默认设置」可以编辑 `config.json` 的 `defaults`：常用开关、生成参数、Shell、后台任务、技能、定时、记忆、MCP、Web 和 CLI 行为；保存后立即生效项会更新当前进程，其余下次启动生效；
- 双击对话里的图片可以全屏查看，点击遮罩或按 Esc 关闭；
- 历史会话里的图片默认只显示「点击加载」占位，点击后才请求 `/api/image` 并打开灯箱，避免首屏传输大量 base64；刚上传的图片仍直接显示本地预览；
- Web 默认免确认；加 `--confirm --web` 后，写文件、修改状态的命令和技能脚本会弹出审批框。`run_command` 是否可用仍由 `--no-shell` 决定；shell 开启时启动会打印提醒；
- 监听非回环地址会打印警告；
- `--web` 与位置参数 prompt 同时出现时忽略 prompt 并提示。

### Web 审批

默认 Web 不弹审批。加 `--confirm` 后：

```bash
python3 benben-agent.py --web --confirm
```

写文件、修改状态的命令、技能脚本会暂停，浏览器弹出「需要确认」：

```text
动作：write_file / run_command / run_skill_script
内容：完整路径、命令或脚本
[允许一次] [拒绝]
```

- 只允许当前这一次，不提供“永久允许”
- 120 秒内没有响应按拒绝处理
- 点击停止、关闭页面或连接中断也按拒绝处理
- 计划模式守卫为 `approve` 时使用同一个弹窗，不要求额外加 `--confirm`
- Web 聊天始终使用 SSE 事件通道，审批事件可以中途送达；「流式输出」开关只影响正文显示方式
- 非流式 `/api/chat` 遇到需要审批的配置会返回 409，提示改用 `/api/chat/stream`

审批响应接口：

```text
POST /api/approvals/respond
{"id": "...", "allow": true}
```

接口需要 `X-Token`，且只对当前正在运行的聊天有效。

### Web 端斜杠命令

Web 聊天框会拦截以下命令，其余斜杠命令会被当成普通消息发给模型：

| 命令 | 作用 |
| --- | --- |
| `/help` | 打开帮助弹窗 |
| `/status` | 查看连接、模型、上下文、shell 和会话 |
| `/connect` | 打开连接设置 |
| `/model` | 查看当前模型和能力 |
| `/model 名称` | 切换当前连接的模型 |
| `/model list all` | 列出所有已保存连接和模型 |
| `/model use 连接 [模型]` | 切换连接，可同时切换模型 |
| `/shell` `/shell 名称` | 查看或切换 shell |
| `/session ...` | 会话列表、切换、重命名、导出、删除 |
| `/jobs ...` | 后台任务列表、状态、输出、跟随、停止 |
| `/plan ...` | 计划模式开关和守卫档位 |
| `/skill` | 打开技能管理；`/skill create`、`/skill install` 直达表单 |
| `/stream ...` | 流式输出开关 |
| `/compact` `/compact N` | 压缩上下文 |
| `/compact auto` | 打开压缩设置 |

`/model info`、`/model config`、`/memory`、`/schedule`、`/config`、`/confirm`、`/thinking`、`/reasoning`、`/save`、`/image`、`/exit` 是 CLI 专属，Web 用对应按钮或设置项代替。

页面全部内嵌在 `benben-agent.py` 里，无 CDN、无前端构建，离线可用。assistant 回复在流式开启时以纯文本逐块出现，结束后再重绘常用 Markdown（标题、粗体、行内代码、代码块、列表、引用、链接、分隔线和表格），`![](...)` 图片也会直接显示；用户上传的图片在发送后显示缩略图，重新打开历史会话时图片以「点击加载」占位显示。网页通过 `/api/chat/stream` 做 token 级流式：思考、正文、工具调用和每个步骤的中间回答都会逐步出现；关闭流式后事件在完成后一次性返回。旧的 `/api/chat` JSON 接口保留。

## 内置工具

| 工具 | 作用 | 需要批准 |
| --- | --- | --- |
| `list_dir` | 列出目录内容 | 否 |
| `read_file` | 按行读取文本文件（带行号，可分页） | 否 |
| `search` | 按正则搜索文件内容，支持工作区外路径（`输出=content` 返回 `文件:行号: 内容`；`输出=lines` 只返回 `文件:行号`） | 否 |
| `glob` | 按通配符查找文件，支持 `**` 递归 | 否 |
| `write_file` | 创建或覆盖文件 | 是 |
| `edit_file` | 替换文件中的一段文本（类似 patch） | 是 |
| `run_command` | 在工作区执行 shell 命令（可用 `shell` 参数指定本次用哪个 shell） | 是 |
| `list_shells` | 列出检测到的 shell 和当前 shell | 否 |
| `switch_shell` | 切换默认 shell | 否 |
| `job_status` | 查看后台任务状态和最近输出 | 否 |
| `job_output` | 读取后台任务的输出 | 否 |
| `job_cancel` | 取消后台任务并杀进程树 | 否 |
| `list_jobs` | 列出后台任务 | 否 |
| `submit_plan` | 提交实施计划并等待用户批准（仅计划模式下提供） | 否 |
| `load_skill` | 加载技能的完整 SKILL.md（仅启用技能时提供） | 否 |
| `read_skill_resource` | 读取技能目录内的参考文件（仅存在资源时提供） | 否 |
| `run_skill_script` | 执行技能 `scripts/` 下的脚本（仅在启用脚本且存在脚本时提供） | 是 |
| `create_skill` | 创建或更新技能，默认写入全局技能目录（仅启用技能时提供） | 否 |
| `install_skill` | 从目录、zip 或 git URL 安装技能（仅启用技能时提供） | 中高风险需要 |
| `remove_skill` | 请求删除技能，批准后移动到备份（仅启用技能时提供） | 总是需要 |
| `create_schedule` | 创建定时任务（仅启用定时任务时提供） | 是 |
| `list_schedules` | 列出定时任务（仅启用定时任务时提供） | 否 |
| `delete_schedule` | 删除定时任务及运行记录（仅启用定时任务时提供） | 是 |
| `enable_schedule` | 启用或暂停定时任务（仅启用定时任务时提供） | 是 |
| `run_schedule_now` | 立即排队执行一次定时任务（仅启用定时任务时提供） | 否 |
| `schedule_history` | 查看定时任务运行记录（仅启用定时任务时提供） | 否 |
| `remember` | 写入长期记忆（仅启用记忆时提供） | 否 |
| `recall` | 搜索长期记忆和情景记忆（仅启用记忆时提供） | 否 |
| `save_episode` | 追加当天情景总结（仅启用记忆时提供） | 否 |
| `list_episodes` | 列出情景记忆文件（仅启用记忆时提供） | 否 |
| `read_episode` | 读取情景记忆文件（仅启用记忆时提供） | 否 |
| `generate_daily_episode` | 生成自动日总结（仅启用记忆时提供） | 否 |

全部功能开启时共 29 个工具。

`run_command` 的 `timeout_seconds` 是必填参数：快速查询建议 10-30 秒，构建/测试/安装 60-180 秒，最大 600 秒（`--max-command-timeout`）。命令超时或用户按 Ctrl+C 时会杀掉整个进程树，保留已捕获的部分输出，并提示模型提高超时或拆分任务。不要在前台运行服务器、守护进程、`tail -f`、`watch` 或交互式编辑器。

默认不询问，写文件和命令直接执行。加 `--confirm` 后，写文件和会修改状态的命令会询问 `Allow? [Y/n]`（直接回车即允许）；只读命令（grep、tail、ps、df、journalctl、curl 等）任何时候都不询问。判断是保守白名单：未知命令、包装器（sudo、timeout、env）、重定向、命令替换和危险参数都按修改状态处理；`curl` 带 `-o/-d/-X` 等写文件或发数据的参数、`wget` 不带 `-O -`/`--spider` 时都要求批准。标准输入不是终端（例如管道、CI）时直接拒绝，并把原因返回给模型。交互模式里可以随时用 `/confirm on` 和 `/confirm off` 切换。模型最多执行 `--max-steps` 次模型调用，一次性任务达到上限会以退出码 1 结束，交互模式只提示后继续。交互模式下 Ctrl+C 需要连按两次才退出：第一次取消当前输入或中断正在执行的任务，2 秒内再按一次才退出。

### 读取策略

读代码文件不要一段一段地磨。下面是推荐的三步：

1. **先看结构**：用 `search` 配 `pattern="^(class |def )"`、`include="*.py"`、`output="lines"`，一次拿到全部定义的 `文件:行号`。`output="lines"` 只返回行号不带正文，适合先把文件结构铺开。
2. **再按行号取正文**：用 `read_file` 带上 `offset`/`limit` 精确取需要的段落。
3. **一次读够**：`read_file` 单次最多 1000 行，能一次读完就别拆；确实读不完再带 `offset` 续读。`numbered=false` 可以拿到不带行号前缀的纯文本，token 更省。

参数：

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `read_file.limit` | `1000` | 一次最多读多少行，硬上限 1000 |
| `read_file.numbered` | `true` | 是否给每行加行号前缀 |
| `search.output` | `content` | `content` 返回 `文件:行号: 内容`；`lines` 只返回 `文件:行号` |
| `search.max_results` | `100` | 最大匹配条数，上限 500 |

系统提示词里也写了对应的预算规则：同一个文件 `read_file` 超过 3 次就要换方法，同一段内容不读第三遍。

### 预览项目

用户说“看下这个项目”“介绍下这个仓库”这类模糊请求时，默认按**预览**处理，只读关键文件，不通读：

1. `list_dir` / `glob` 看清目录结构
2. 挑 3-8 个关键文件读：README、入口文件、依赖清单、配置文件、主要模块目录
3. 读完直接给结论：项目做什么、代码怎么组织、从哪跑起来、入口在哪

系统提示词同时约束了：读之前先说明打算读哪几个；够用就停，不要逐个文件读或翻页看实现细节；整轮工具调用控制在十几次以内。用户点名要看的文件或明确要细节时才深入。

## 图片识别

模型需要支持多模态。识图时请确认默认模型 `Deepseek-V4.1-Flash` 支持图片输入，否则通过 `--base-url` / `-m` 换成支持图片的模型。

```bash
python3 benben-agent.py -i screenshot.png "这个报错是什么原因"
```

交互模式用 `/image` 把图片挂到下一条消息，发送后自动清空；`/image` 查看待发列表，`/image clear` 清空，`/reset` 也会清空。只给图片不给文字时，会自动补一句「请描述这张图片」。

图片会以 base64 保存在会话 JSONL 里，重新加载历史会话时可以直接显示；代价是带图片的会话文件会明显变大。

支持 PNG、JPEG、GIF（按静态图）、WEBP、BMP；单张不超过 10 MB，每条消息最多 4 张。全程只用标准库，不缩放、不转码，超过限制会直接报错。

## 终端输出与 Markdown 渲染

交互终端默认流式输出，正文按纯文本实时显示，不做终端 Markdown 渲染。使用 `--no-stream`
时等待完整响应，并按 `--plain` / `--no-plain` 决定是否渲染 Markdown：标题、粗体/斜体、
行内代码、代码块、列表、引用、链接和表格；表格按中文显示宽度对齐。管道和重定向默认
非流式，保持原始 Markdown，方便写入文件；`--stream` 可强制流式，`NO_COLOR=1` 可关闭颜色。
渲染只影响终端显示，不改变发给模型的历史内容。颜色约定：输入提示符 `you> ` 为青色，
回复标记 `agent> ` 为品红色，思考内容为灰色，工具调用行用暗色。

## 实现要点

- HTTP 使用 `urllib.request`，JSON 使用 `json`，命令使用 `subprocess`，没有任何第三方依赖。
- `429`、`5xx` 和网络错误会按指数退避重试（最多 3 次），遵守 `Retry-After`。
- 模型返回的工具调用参数如果不是合法 JSON，错误会作为工具结果回传给模型，让它自行修正。
- 工具结果超过 20000 字符会截断；`run_command` 在读取输出时就按上限截断，不会把整份输出缓冲进内存；文件读取默认最多 1000 行。
- `search`/`glob` 是纯 Python 实现，跳过 `.git`、`node_modules`、`__pycache__` 等目录，支持工作区外路径；`glob` 在遍历阶段就剪枝并限制 10 秒扫描预算，`search` 跳过常见二进制扩展名，并限制单文件 2 MB、默认最多 20k 文件/200 MB/10 秒。
- 文件工具不再限制在工作区内：`write_file`、`edit_file` 可以写任意可写路径。默认 `--confirm` 关闭，Web 模式没有工具审批；需要写操作确认时请加 `--confirm`，不要让不可信的网页或文件内容驱动写系统配置。
- `write_file`、`edit_file` 和 `/save` 使用同目录临时文件 + `os.replace` 原子替换，中断时不会留下半个文件。
- 文件路径经过 `realpath` 规范化，符号链接也无法逃逸出当前工作空间根目录。
- 交互模式会自动开启 Linux 终端的 `IUTF8` 标志，并容错解码输入，避免中文退格留下残缺字节导致 `UnicodeDecodeError`。

## 测试

```bash
python3 tests/run_all.py
```

测试只用标准库和本地假服务器，使用临时 HOME 和工作区，不访问真实 API。详见 `tests/README.md`。

## 限制

- 流式输出依赖网关支持；网关拒绝时会自动回退非流式，CLI 管道和脚本默认非流式。
- 历史消息默认不裁剪；长对话需要手动 `/compact`，或开启 `--auto-compact` / `--idle-compact`，否则可能超出模型上下文。
- 模型必须支持 OpenAI 风格的 `tools` / `tool_calls`；不支持 function calling 的模型只能当聊天机器人用。
- 图片不会缩放或转码，单张上限 10 MB，不支持 HEIC；图片会占用较多上下文 token。
- MiniMax-M3.1-Flash 不能关闭思考，只能降低 `reasoning_effort`；Qwen、GLM、Claude、OpenAI/Codex、Gemini、Grok 未纳入思考映射。
- Anthropic 协议支持流式文本、思考和工具调用，不支持 prompt caching 和 PDF 输入。
- Responses 协议支持流式文本、reasoning 和函数调用，但无状态，不支持内置工具（web_search/file_search/computer_use）。
- 会话文件默认只增不减，`/compact` 也只在末尾追加压缩记录，不删除原历史；文件包含完整对话内容，注意 `~/.benben-agent/sessions/` 的访问权限。
- 交互模式下网络/API 错误只打印原因并保留历史，不会退出；可以直接重试，或 `/exit` 后用 `-c` 恢复。
- 网页使用 SSE 传输，流式关闭时会在完整响应后一次性返回；只适合本机单用户使用，不做多用户隔离。
- 网页里 assistant 输出的远程 `https` 图片会直接加载，会向图片服务器暴露本机 IP；上传的图片以 base64 写入会话文件，带图会话会明显变大。
- `run_command` 工具描述里嵌入的可用 shell 列表在启动时生成，`switch_shell` 或 `/shell` 切换后不会刷新；需要实时列表时调用 `list_shells`。
- 计划模式默认只靠提示词约束模型（`--plan-guard off`），模型理论上仍能改动文件；开 `approve` 或 `deny` 才有硬性拦截，网页端的 `approve` 会降级为"拒绝并让模型转述给你"。
- 切换计划模式会重建系统提示词和工具列表，击穿一次 prompt 缓存。

## 许可

本项目基于 [MIT License](LICENSE) 开源。

