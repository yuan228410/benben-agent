# 零依赖 AI Agent

`benben-agent.py` 是一个只用 Python 3.8+ 标准库实现的命令行 AI agent，通过 OpenAI 兼容的 `POST /chat/completions` 接口驱动。支持多轮对话、会话持久化、Agent Skills、function calling（工具调用）和图片输入，模型可以读写工作区文件、执行命令，然后给出最终回答。

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
you> /image shot.png  # 附带图片，随下一条消息发送
you> /skill            # 列出已加载的 Agent Skills（启动时加 --skill）
you> /skill log-triage 排查 502  # 强制用指定技能回答问题
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
you> /session use 1   # 切换到第 1 个会话
you> /session new     # 开始新会话
you> /session info    # 查看当前会话
you> /session delete 2  # 删除非当前会话
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

系统提示词只在启动时生成一次，切换 shell 后不会重写，避免破坏 prompt 缓存。模型通过 `list_shells`、`switch_shell` 和 `run_command` 返回的 `shell:` 行获取实时 shell。

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

### 只读判断

- POSIX / Git Bash：保留命令白名单判断。
- PowerShell / cmd：一律视为可能修改状态。开启 `--confirm` 时所有命令都需要确认。

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
| `--cwd` | 当前目录 | 工具的根目录，文件访问被限制在该目录内 |
| `-i, --image` | 无 | 给任务附加图片，可重复，最多 4 张、单张 10 MB |
| `--confirm` / `--no-confirm` | 关 | 是否在写文件和会修改状态的命令前询问；只读命令从不询问，`--yes` 是 `--no-confirm` 的别名 |
| `--no-shell` | 关 | 禁用 `run_command` |
| `--shell` | `auto` | shell：`auto`、`pwsh`、`powershell`、`git-bash`、`cmd`、`bash`、`zsh`、`sh` 或绝对路径 |
| `--no-shell-fallback` | 关 | 当前 shell 不认识命令时，不自动尝试其他 shell |
| `--skill` / `--no-skill` | 关 | 是否扫描 Agent Skills 并向模型暴露技能工具；默认关闭，需显式加 `--skill` |
| `--only-own-skills` | 关 | 配合 `--skill`：只加载 `~/.benben-agent/skills/`，忽略 Claude、Codex 和跨客户端目录 |
| `--no-skill-scripts` | 关 | 启用技能时禁用 `run_skill_script`；技能说明和资源仍可用 |
| `--plain` | 关 | 不渲染 Markdown，直接输出原始文本 |
| `--thinking` | 关 | 在回答前用暗色打印模型的 `reasoning_content`（网关返回该字段时）；交互模式可用 `/thinking on\|off` 切换 |
| `--reasoning` | 不发送 | 思考控制，格式 `provider:level`；provider 支持 `deepseek`/`kimi`/`minimax`，level 支持 `off/minimal/low/medium/high/max`；详见下文 |
| `-c, --continue` | 无 | 恢复会话；省略 UUID 时取最新会话，没有会话则新建 |
| `--list` | 关 | 列出已保存的会话并退出，不联网、不需要 API key |
| `--web` | 关 | 启动浏览器界面；网页模式强制免确认 |
| `--host` | `127.0.0.1` | 网页监听地址 |
| `--port` | `8765` | 网页端口，`0` 表示由系统分配 |
| `--connect` | 关 | 打开连接配置向导；没有 prompt/`--web` 时配置完退出 |
| `--profile NAME` | 无 | 使用指定保存连接，不修改配置里的 active |
| `--config PATH` | `$BENBEN_CONFIG` 或 `~/.benben-agent/config.json` | 配置文件路径 |
| `--no-config` | 关 | 启动时忽略配置文件 |
| `--no-env` | 关 | 忽略 `OPENAI_BASE_URL` / `OPENAI_API_KEY` / `OPENAI_MODEL` |

> 有 active 保存连接时，`--base-url`、`--api-key`、`-m`、`--api-format` 的默认值来自保存连接；环境变量和显式参数会按上节优先级覆盖它。

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
- 非流式；支持 extended thinking（`--reasoning anthropic:low/medium/high`），不支持 prompt caching、PDF；
- 内网 A 系列本身走 OpenAI 兼容接口，不需要加 `--api-format anthropic-messages`。

## Responses 协议

`--api-format openai-responses` 使用 OpenAI Responses API（`/v1/responses`），支持文本、图片和工具调用；非流式、无状态（`store: false`）。

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
- 支持 `reasoning.effort` 映射（`--reasoning openai:low/medium/high`）；
- 暂不支持流式、内置工具（web_search/file_search/computer_use）；
- 内网网关也支持 `/v1/responses`，可以直接用 `--api-format openai-responses`。

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
- 加载时跳过损坏的行，进程被杀留下的半行不会导致失败。

列出和恢复：

```bash
python3 benben-agent.py --list
python3 benben-agent.py -c                             # 恢复最新会话；没有就新建
python3 benben-agent.py -c a1b2c3d4                    # 交互模式继续指定会话
python3 benben-agent.py -c a1b2c3d4 "继续刚才的任务"   # 一次性继续指定会话
python3 benben-agent.py -c "继续刚才的任务"            # 最新会话 + 一次性任务
```

`-c` 后面的值只有是 8 位小写十六进制时才当作会话 id，否则按 prompt 处理。

`--list` 显示 uuid、日期、消息数、模型、工作区和首条用户消息，按序号倒序。恢复只带回消息历史；模型仍由 `-m`/默认值决定，工具根目录仍是当前 `--cwd`。

交互模式里用 `/session` 管理会话：

```text
/session                    列出最近会话
/session list all           列出全部会话
/session list <关键词>      按标题/UUID/模型过滤
/session use <编号|uuid>    切换会话
/session new                开始新会话（同 /reset）
/session info               查看当前会话
/session info <编号|uuid>   查看指定会话
/session rename <新标题>    修改当前会话标题
/session delete <编号|uuid> 删除会话（不能删除当前会话）
```

`/reset` 仍会清空上下文并结束当前会话文件，下一条消息写入新文件。Web 侧边栏也提供会话列表、新建和删除；右键会话可以修改名称，聊天框输入 `/session` 也可以管理。

### 压缩上下文

`/compact [N]` 把较早的对话交给模型总结，默认保留最近 2 个用户回合（`N=0` 表示不保留）。压缩会在 JSONL 末尾追加一条 `_type: "compact"` 记录，后面再写一遍保留的轮次；原历史一行不删。`-c` 和网页加载时只从**最后一条**压缩记录开始重建上下文，所以文件仍然完整，但不会把旧历史发给模型。

- 摘要固定包含目标、已完成、关键决策、文件路径、执行过的命令、未完成事项和注意事项；
- 待压缩内容过长时会分块滚动总结；
- 图片只以 `[图片]` 占位进入摘要，base64 不会发进摘要请求；
- 网页输入框下方的「压缩」按钮等价于 `/compact`，压缩后刷新页面仍会显示摘要提示。

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

模型侧只看到技能清单，并按需调用：

| 工具 | 作用 | 需要批准 |
| --- | --- | --- |
| `load_skill` | 读取完整 SKILL.md | 否 |
| `read_skill_resource` | 读取技能目录内的参考文件 | 否 |
| `run_skill_script` | 执行 `scripts/` 下的脚本 | 是 |

脚本按 shebang、扩展名（py/sh/js/rb/pl/php/ps1）或可执行文件运行；cwd 是当前工作区，并注入 `BENBEN_SKILL_DIR` 和 `BENBEN_WORKSPACE`。启用技能时脚本默认可用，`--no-skill-scripts` 关闭；`--confirm` 下每次执行都要确认；web 模式没有审批，启动会打印警告。

技能默认关闭，用 `--skill` 开启；`--no-skill` 可显式关闭。关闭时不扫描技能目录，也不向模型暴露任何技能工具。交互模式里 `/skill` 列出已加载技能，`/skill <name> <问题>` 会强制加载指定技能并用它回答。

## 网页入口

```bash
python3 benben-agent.py --web
# web ui: http://127.0.0.1:8765/?token=...
```

- 默认只监听 `127.0.0.1`，`--host` / `--port` 可改，`--port 0` 由系统分配；
- 启动时生成随机 token，页面和所有 API 都必须带 token，重启后失效；
- 页面支持对话、新建/切换/删除历史会话（删除有确认弹框）、上传图片，左侧下拉框可以切换模型（等价于 CLI 的 `/model`，切换后立即生效）；
- 会话列表支持右键菜单修改名称；聊天框输入 `/session` 也可以管理会话；
- 输入框下方有「显示思考」开关和「思考等级」选择，旁边是「压缩」按钮；思考等级只对 `openai-chat-completions` 生效；
- 生成过程中显示「停止」按钮，点击后立即中断浏览器端等待，并请求服务端停止后续模型调用和工具执行；
- 在输入框直接输入 `/compact` 或 `/compact 3` 也会被前端拦截；
- 双击对话里的图片可以全屏查看，点击遮罩或按 Esc 关闭；
- 历史会话里的图片默认只显示「点击加载」占位，点击后才请求 `/api/image` 并打开灯箱，避免首屏传输大量 base64；刚上传的图片仍直接显示本地预览；
- 网页模式强制免确认，`run_command` 是否可用仍由 `--no-shell` 决定；shell 开启时启动会打印提醒；
- 监听非回环地址会打印警告；
- `--web` 与位置参数 prompt 同时出现时忽略 prompt 并提示。

页面全部内嵌在 `benben-agent.py` 里，无 CDN、无前端构建，离线可用。assistant 回复会渲染常用 Markdown（标题、粗体、行内代码、代码块、列表、引用、链接、分隔线和表格），`![](...)` 图片也会直接显示；用户上传的图片在发送后显示缩略图，重新打开历史会话时图片以「点击加载」占位显示。网页通过 `/api/chat/stream` 做步骤级流式：工具调用和每个步骤的中间回答会逐步出现，但单次模型请求仍是非流式的，思考和正文会一起到达。旧的 `/api/chat` JSON 接口保留。

## 内置工具

| 工具 | 作用 | 需要批准 |
| --- | --- | --- |
| `list_dir` | 列出目录内容 | 否 |
| `read_file` | 按行读取文本文件（带行号，分页） | 否 |
| `search` | 在工作区按正则搜索文件内容（返回 `文件:行号: 内容`） | 否 |
| `glob` | 按通配符查找文件，支持 `**` 递归 | 否 |
| `write_file` | 创建或覆盖文件 | 是 |
| `edit_file` | 替换文件中的一段文本（类似 patch） | 是 |
| `run_command` | 在工作区执行 shell 命令 | 是 |
| `load_skill` | 加载技能的完整 SKILL.md（仅启用技能时提供） | 否 |
| `read_skill_resource` | 读取技能目录内的参考文件（仅存在资源时提供） | 否 |
| `run_skill_script` | 执行技能 `scripts/` 下的脚本（仅在启用脚本且存在脚本时提供） | 是 |

默认不询问，写文件和命令直接执行。加 `--confirm` 后，写文件和会修改状态的命令会询问 `Allow? [Y/n]`（直接回车即允许）；只读命令（grep、tail、ps、df、journalctl、curl 等）任何时候都不询问。判断是保守白名单：未知命令、包装器（sudo、timeout、env）、重定向、命令替换和危险参数都按修改状态处理；`curl` 带 `-o/-d/-X` 等写文件或发数据的参数、`wget` 不带 `-O -`/`--spider` 时都要求批准。标准输入不是终端（例如管道、CI）时直接拒绝，并把原因返回给模型。交互模式里可以随时用 `/confirm on` 和 `/confirm off` 切换。模型最多执行 `--max-steps` 次模型调用，一次性任务达到上限会以退出码 1 结束，交互模式只提示后继续。交互模式下 Ctrl+C 需要连按两次才退出：第一次取消当前输入或中断正在执行的任务，2 秒内再按一次才退出。

## 图片识别

模型需要支持多模态。识图时请确认默认模型 `Deepseek-V4.1-Flash` 支持图片输入，否则通过 `--base-url` / `-m` 换成支持图片的模型。

```bash
python3 benben-agent.py -i screenshot.png "这个报错是什么原因"
```

交互模式用 `/image` 把图片挂到下一条消息，发送后自动清空；`/image` 查看待发列表，`/image clear` 清空，`/reset` 也会清空。只给图片不给文字时，会自动补一句「请描述这张图片」。

图片会以 base64 保存在会话 JSONL 里，重新加载历史会话时可以直接显示；代价是带图片的会话文件会明显变大。

支持 PNG、JPEG、GIF（按静态图）、WEBP、BMP；单张不超过 10 MB，每条消息最多 4 张。全程只用标准库，不缩放、不转码，超过限制会直接报错。

## 终端 Markdown 渲染

标准输出是终端时，agent 的回复会渲染 Markdown：标题、粗体/斜体、行内代码、代码块、
列表、引用、链接和表格；表格按中文显示宽度对齐。管道输出保持原始 Markdown，方便写入
文件。加 `--plain` 可强制输出原文，`NO_COLOR=1` 可关闭颜色。渲染只影响终端显示，不改变
发给模型的历史内容。颜色约定：输入提示符 `you> ` 为青色，回复标记 `agent> ` 为品红色，
思考内容为灰色，工具调用行用暗色。

## 实现要点

- HTTP 使用 `urllib.request`，JSON 使用 `json`，命令使用 `subprocess`，没有任何第三方依赖。
- `429`、`5xx` 和网络错误会按指数退避重试（最多 3 次），遵守 `Retry-After`。
- 模型返回的工具调用参数如果不是合法 JSON，错误会作为工具结果回传给模型，让它自行修正。
- 工具结果超过 20000 字符会截断；`run_command` 在读取输出时就按上限截断，不会把整份输出缓冲进内存；文件读取默认最多 400 行。
- `search`/`glob` 是纯 Python 实现，跳过 `.git`、`node_modules`、`__pycache__` 等目录，不跟随符号链接逃出工作区；`glob` 在遍历阶段就剪枝，`search` 跳过常见二进制扩展名，并限制单文件 2 MB、默认最多 20k 文件/200 MB/10 秒。
- `write_file`、`edit_file` 和 `/save` 使用同目录临时文件 + `os.replace` 原子替换，中断时不会留下半个文件。
- 文件路径经过 `realpath` 规范化，符号链接也无法逃逸出 `--cwd`。
- 交互模式会自动开启 Linux 终端的 `IUTF8` 标志，并容错解码输入，避免中文退格留下残缺字节导致 `UnicodeDecodeError`。

## 测试

```bash
python3 tests/run_all.py
```

测试只用标准库和本地假服务器，使用临时 HOME 和工作区，不访问真实 API。详见 `tests/README.md`。

## 限制

- 只使用非流式模型请求，单次调用里的思考和正文会一起到达；网页的步骤级 SSE 只解决工具调用和中间回答的逐步显示。
- 历史消息不会自动裁剪；长对话需要手动 `/compact`，否则可能超出模型上下文。
- 模型必须支持 OpenAI 风格的 `tools` / `tool_calls`；不支持 function calling 的模型只能当聊天机器人用。
- 图片不会缩放或转码，单张上限 10 MB，不支持 HEIC；图片会占用较多上下文 token。
- MiniMax-M3.1-Flash 不能关闭思考，只能降低 `reasoning_effort`；Qwen、GLM、Claude、OpenAI/Codex、Gemini、Grok 未纳入思考映射。
- Anthropic 协议目前是非流式的，不支持 Claude extended thinking、prompt caching 和 PDF 输入。
- Responses 协议目前是非流式、无状态的，不支持内置工具（web_search/file_search/computer_use）和 `reasoning.effort` 映射。
- 会话文件默认只增不减，`/compact` 也只在末尾追加压缩记录，不删除原历史；文件包含完整对话内容，注意 `~/.benben-agent/sessions/` 的访问权限。
- 交互模式下网络/API 错误只打印原因并保留历史，不会退出；可以直接重试，或 `/exit` 后用 `-c` 恢复。
- 网页使用步骤级 SSE，长回答仍要等单次模型请求返回；只适合本机单用户使用，不做多用户隔离。
- 网页里 assistant 输出的远程 `https` 图片会直接加载，会向图片服务器暴露本机 IP；上传的图片以 base64 写入会话文件，带图会话会明显变大。

## 许可

本项目基于 [MIT License](LICENSE) 开源。

