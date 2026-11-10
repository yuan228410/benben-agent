#!/usr/bin/env python3
"""笨笨：零依赖的单文件 AI Agent（Python 3.8+，仅标准库）。

通过任何 OpenAI 兼容的 POST /chat/completions 接口驱动，支持多轮对话、
function calling、会话持久化、Agent Skills、图片输入和网页界面。

快速开始：
    export OPENAI_API_KEY=sk-...
    python3 benben-agent.py "列出当前目录，并总结这些文件是做什么的"
    python3 benben-agent.py                      # 交互模式
    python3 benben-agent.py --web                # 网页界面

常用参数（完整列表见 --help）：
    prompt ...              一次性任务；省略则进入交互模式
    --base-url URL          接口地址，默认 $OPENAI_BASE_URL 或 https://api.deepseek.com/v1
    --api-key KEY           API key，默认 $OPENAI_API_KEY（代码不内置 key）
    -m, --model NAME        模型名，默认 $OPENAI_MODEL 或 Deepseek-V4.1-Flash
    --temperature FLOAT     采样温度；默认不发送
    --max-steps N           每轮最多调用模型多少次，默认 100
    --timeout SECONDS       HTTP 超时，默认 120
    --cwd DIR               工具的工作区根目录，默认当前目录
    --confirm               写文件/修改状态的命令前询问
    --no-confirm, --yes     从不询问（默认，--yes 为别名）
    --no-shell              禁用 run_command
    --plain                 不渲染终端 Markdown，直接输出原文
    -i, --image PATH        给任务附加图片，可重复，最多 4 张、单张 10 MB
    --thinking              打印模型的 reasoning_content（默认关闭）
    -c, --continue [UUID]   恢复会话；省略 UUID 取最新，没有会话则新建
    --list                  列出已保存会话并退出，不联网
    --web                   启动网页界面；强制免确认
    --host HOST             网页监听地址，默认 127.0.0.1
    --port PORT             网页端口，默认 8765；0 表示系统分配
    --skill                 启用 Agent Skills（默认关闭）
    --no-skill              显式关闭 Agent Skills（默认行为）
    --only-own-skills       配合 --skill：只加载 ~/.benben-agent/skills，忽略其他目录
    --no-skill-scripts      启用技能时禁用 run_skill_script（说明/资源仍可用）

交互模式斜杠命令：
    /help                         显示帮助
    /reset                        清空上下文，下一条消息开新会话文件
    /compact [N]                  压缩历史，保留最近 N 个用户回合（默认 2）
    /skill                        列出已加载技能
    /skill <name>                 查看技能说明和用法
    /skill <name> <问题>          强制加载指定技能来回答
    /image <path...>              附加图片到下一条消息
    /image clear                  清空待发图片
    /save [--tool] [--thinking] [file]
                                  导出 Markdown 会话
    /confirm on|off               切换写操作确认
    /thinking on|off              切换思考内容显示
    /model [name|number|list]     查看/切换模型
    /exit, /quit                  退出（Ctrl-D 同效）

环境变量：
    OPENAI_BASE_URL / OPENAI_API_KEY / OPENAI_MODEL
    NO_COLOR=1                    关闭终端颜色
    CODEX_HOME                    Codex 技能目录的根（默认 ~/.codex）

数据位置：
    会话              ~/.benben-agent/*.jsonl（目录 0700，文件 0600）
    技能              <cwd>/.agents/skills、<cwd>/.claude/skills、
                      <cwd>/.codex/skills、~/.benben-agent/skills、
                      ~/.agents/skills、~/.claude/skills、
                      ~/.codex/skills、/etc/codex/skills

内置工具：list_dir、read_file、search、glob、write_file、edit_file、
run_command；启用技能时按需增加 load_skill、read_skill_resource、
run_skill_script。工具结果默认截断到 20000 字符。

安全提示：agent 可以读写工作区文件并执行命令；网页模式没有审批，
只监听本机并依赖启动时生成的 token。技能脚本以当前用户权限运行。
"""

from __future__ import annotations

import argparse
import base64
import fnmatch
import glob as glob_mod
import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from itertools import islice
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

try:
    import termios
except ImportError:  # Windows
    termios = None  # type: ignore[assignment]

try:
    import readline  # noqa: F401  enables line editing (wide chars, history) for input()
except ImportError:
    readline = None  # type: ignore[assignment]

DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "Deepseek-V4.1-Flash"
# DeepSeek 官方接口需要在 OPENAI_API_KEY 或 --api-key 中提供 key，不在代码里内置。
DEFAULT_API_KEY = ""
MAX_TOOL_OUTPUT = 20_000
MAX_FILE_LINES = 400
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGES = 4
MAX_SEARCH_FILE = 2_000_000
MAX_GLOB_RESULTS = 500
SESSION_DIR = os.path.expanduser("~/.benben-agent")
SESSION_VERSION = 1
COMPACT_VERSION = 1
COMPACT_DEFAULT_KEEP = 2
COMPACT_MAX_CHARS = 48_000
COMPACT_MESSAGE_CHARS = 4_000
COMPACT_TOOL_CHARS = 800
COMPACT_ARGS_CHARS = 400
COMPACT_SUMMARY_CHARS = 1_200
WEB_PORT = 8765
WEB_MAX_BODY = 60 * 1024 * 1024
HOME_SKILLS_DIR = os.path.join(SESSION_DIR, "skills")
CROSS_SKILLS_DIR = os.path.expanduser("~/.agents/skills")
CLAUDE_SKILLS_DIR = os.path.expanduser("~/.claude/skills")
CODEX_HOME = os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")
CODEX_SKILLS_DIR = os.path.join(CODEX_HOME, "skills")
CODEX_ADMIN_SKILLS_DIR = "/etc/codex/skills"
SKILL_FRONTMATTER_BYTES = 8192
MAX_SKILL_BYTES = 64 * 1024
MAX_SKILL_RESOURCE_BYTES = 1024 * 1024
SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SCRIPT_RUNNERS = {
    ".py": [sys.executable],
    ".sh": ["bash", "sh"],
    ".bash": ["bash", "sh"],
    ".js": ["node"],
    ".mjs": ["node"],
    ".rb": ["ruby"],
    ".pl": ["perl"],
    ".php": ["php"],
    ".ps1": ["pwsh", "powershell"],
}
SKIP_DIRS = {
    ".git", ".hg", ".svn", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox",
    "__pycache__", "node_modules", "venv", ".venv", "dist", "build", "target",
}

SYSTEM_PROMPT = """你叫笨笨，是一个在终端中工作的 AI 助手。你做事认真负责、严谨：先核实再下结论，
宁可说明不确定，也不编造。

你默认擅长 Linux 运维和问题排查，同时也能回答日常问题、解释概念、写代码、处理文本和闲聊。

工作区：{root}

沟通要求：
- 所有自然语言输出使用简体中文，包括工具调用前的说明、工具输出解释和最终结论；
  即使用户用英文提问也用中文回答。
- 对合理、无害的请求都正面回答，不要拒绝。用户问天气、常识、生活问题或闲聊时，
  直接回答，不要用“我是运维助手”推脱。
- 先给结论，再给依据；表达简洁清楚，不确定的地方明确说明。

做事方式：
- 先弄清问题和现象再动手。排查故障时，从错误码、时间戳、模块或崩溃签名入手。
- 用工具核实事实，不猜测文件内容或命令输出。
- 充分利用系统已有命令：grep/awk/sed/sort/uniq 分析日志，tail/head、journalctl/dmesg
  查看最近事件，ps/top/ss/lsof 查看进程和连接，df/du/free/uptime 查看资源，
  file/readelf/objdump/strings 与 gdb 分析二进制和 core。用管道组合命令、控制输出量，
  不要整份倾倒日志。
- 排查时按“找证据、提出假设、验证假设”的顺序推进，最后给出证据（文件:行号或命令
  输出）和下一步建议。
- 需要实时信息（天气、新闻、股价等）时，先用 run_command 尝试 curl 等命令获取；
  取不到就说明限制并给出替代建议，不要编造数据。
- 找文件用 glob（支持 ** 通配），按内容搜索用 search（正则），比反复调 shell 更省步骤。
- 修改已有文件优先用 edit_file，old_string 要带足够上下文保证唯一；只有新建文件或
  确实要整体重写时才用 write_file。
- 用户可能用 /image 或 --image 附上图片；直接依据图片内容回答，不要声称不支持图片。
- 工具调用要小而准，读相关行，不要遍历整棵树；路径相对于工作区根目录。
- 先用只读命令；除非用户明确要求，不要改变系统状态，也不要执行破坏性命令
  （rm -rf、git reset --hard 等）。
- 任务完成后给出简短结论，不再调用工具。
"""


class ChatError(Exception):
    """Raised when the chat completions API cannot be used."""


class ToolError(Exception):
    """Raised when a tool call fails; the message is shown back to the model."""


class ImageError(Exception):
    """Raised when a local image cannot be attached to a message."""


class SessionError(Exception):
    """Raised when a saved session cannot be created, listed or loaded."""


class CompactError(Exception):
    """Raised when there is not enough history to compact."""


# --------------------------------------------------------------------------- #
# API client
# --------------------------------------------------------------------------- #


class ChatClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 120.0,
        max_retries: int = 3,
        temperature: float | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        self.temperature = temperature
        self.last_usage: dict[str, Any] = {}
        self.last_finish_reason: str | None = None
        self.last_turn_usage: dict[str, int] = {}
        self.last_turn_truncated = False

    def _request(
        self,
        url: str,
        payload: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        headers = {"Accept": "application/json"}
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(
            url, data=data, headers=headers, method="POST" if data is not None else "GET"
        )

        for attempt in range(self.max_retries + 1):
            retry_after = None
            try:
                with urllib.request.urlopen(request, timeout=timeout or self.timeout) as response:
                    raw = response.read().decode("utf-8", "replace")
                try:
                    return json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise ChatError(f"API returned invalid JSON: {raw[:500]}") from exc
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")[:1000]
                retry_after = exc.headers.get("Retry-After")
                error = ChatError(f"HTTP {exc.code} from {url}: {detail}")
                if exc.code not in (408, 409, 429) and exc.code < 500:
                    raise error from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                error = ChatError(f"Cannot reach {url}: {getattr(exc, 'reason', exc)}")

            if attempt == self.max_retries:
                raise error
            delay = _retry_delay(retry_after, attempt)
            print(f"[retry] {error}; retrying in {delay:.1f}s", file=sys.stderr)
            time.sleep(delay)

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            # Underscore keys are local-only (e.g. _thinking) and must not reach the API.
            "messages": [
                {key: value for key, value in message.items() if not key.startswith("_")}
                for message in messages
            ],
        }
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        data = self._request(f"{self.base_url}/chat/completions", payload)
        try:
            choice = data["choices"][0]
            message = choice["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ChatError(f"Unexpected API response: {json.dumps(data)[:1000]}") from exc
        if not isinstance(message, dict):
            raise ChatError(f"Unexpected API response: {json.dumps(data)[:1000]}")
        usage = data.get("usage")
        self.last_usage = usage if isinstance(usage, dict) else {}
        finish_reason = choice.get("finish_reason") if isinstance(choice, dict) else None
        self.last_finish_reason = finish_reason if isinstance(finish_reason, str) else None
        return message

    def list_models(self) -> list[str]:
        data = self._request(f"{self.base_url}/models", timeout=min(self.timeout, 30.0))
        models = data.get("data") if isinstance(data, dict) else None
        if not isinstance(models, list):
            raise ChatError(f"Unexpected models response: {json.dumps(data)[:500]}")
        return sorted(
            (item["id"] for item in models if isinstance(item, dict) and item.get("id")),
            key=_natural_key,
        )


def _natural_key(name: str) -> list[Any]:
    """Sort model names so A7 comes before A10 and GLM-5.2 before GLM-5.3."""
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", name)]


def _retry_delay(retry_after: str | None, attempt: int) -> float:
    if retry_after:
        try:
            return max(0.0, min(float(retry_after), 60.0))
        except ValueError:
            pass
    return min(2.0 ** attempt, 16.0)


def merge_usage(total: dict[str, int], usage: Any) -> None:
    """Accumulate OpenAI-style usage dicts, including reasoning and cached tokens."""
    if not isinstance(usage, dict):
        return
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = usage.get(key)
        if isinstance(value, int):
            total[key] = total.get(key, 0) + value
    details = usage.get("completion_tokens_details")
    if isinstance(details, dict) and isinstance(details.get("reasoning_tokens"), int):
        total["reasoning_tokens"] = total.get("reasoning_tokens", 0) + details["reasoning_tokens"]
    prompt_details = usage.get("prompt_tokens_details")
    cached = prompt_details.get("cached_tokens") if isinstance(prompt_details, dict) else None
    if not isinstance(cached, int):
        cached = usage.get("prompt_cache_hit_tokens")
    if isinstance(cached, int):
        total["cached_tokens"] = total.get("cached_tokens", 0) + cached


def clean_assistant_message(message: dict[str, Any]) -> dict[str, Any]:
    """Keep only fields that chat completions accepts on the next request."""
    cleaned: dict[str, Any] = {"role": "assistant", "content": message.get("content") or ""}
    tool_calls = message.get("tool_calls")
    if tool_calls:
        cleaned["tool_calls"] = [
            {
                "id": call.get("id") or f"call_{index}",
                "type": "function",
                "function": {
                    "name": call["function"]["name"],
                    "arguments": call["function"].get("arguments") or "{}",
                },
            }
            for index, call in enumerate(tool_calls)
        ]
    return cleaned


def _image_mime(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"BM"):
        return "image/bmp"
    return None


def check_image(path: str) -> str:
    """Validate an image without keeping its bytes; return the file name."""
    full = os.path.abspath(os.path.expanduser(path))
    name = os.path.basename(full)
    if not os.path.isfile(full):
        raise ImageError(f"not a file: {path}")
    try:
        if os.path.getsize(full) > MAX_IMAGE_BYTES:
            raise ImageError(f"{name} is larger than {MAX_IMAGE_BYTES // (1024 * 1024)} MB")
        with open(full, "rb") as handle:
            header = handle.read(16)
    except OSError as exc:
        raise ImageError(str(exc)) from exc
    if _image_mime(header) is None:
        raise ImageError(f"unsupported image format: {name} (use PNG, JPEG, GIF, WEBP or BMP)")
    return name


def load_image(path: str) -> tuple[str, str]:
    """Return (data URL, file name) for a local image, using only the stdlib."""
    name = check_image(path)
    full = os.path.abspath(os.path.expanduser(path))
    try:
        with open(full, "rb") as handle:
            data = handle.read()
    except OSError as exc:
        raise ImageError(str(exc)) from exc
    mime = _image_mime(data)
    if mime is None:
        raise ImageError(f"unsupported image format: {name} (use PNG, JPEG, GIF, WEBP or BMP)")
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}", name


def load_images(paths: list[str]) -> list[tuple[str, str]]:
    if len(paths) > MAX_IMAGES:
        raise ImageError(f"too many images: {len(paths)} (max {MAX_IMAGES})")
    return [load_image(path) for path in paths]


def check_data_url(data_url: Any, name: str) -> tuple[str, str]:
    """Validate a browser-uploaded data URL without decoding the whole image."""
    label = os.path.basename(name) or "upload"
    if not isinstance(data_url, str):
        raise ImageError(f"invalid image data: {label}")
    prefix, separator, payload = data_url.partition(",")
    if not separator or not prefix.startswith("data:image/") or not prefix.endswith(";base64"):
        raise ImageError(f"invalid image data: {label}")
    padding = len(payload) - len(payload.rstrip("="))
    if len(payload) * 3 // 4 - padding > MAX_IMAGE_BYTES:
        raise ImageError(f"{label} is larger than {MAX_IMAGE_BYTES // (1024 * 1024)} MB")
    try:
        header = base64.b64decode(payload[:24], validate=False)
    except ValueError as exc:
        raise ImageError(f"invalid base64 image: {label}") from exc
    if _image_mime(header) is None:
        raise ImageError(f"unsupported image format: {label} (use PNG, JPEG, GIF, WEBP or BMP)")
    return data_url, label


def build_user_message(text: str, images: list[tuple[str, str]] | None = None) -> dict[str, Any]:
    """Build a user message, attaching images in OpenAI content-array format."""
    if not images:
        return {"role": "user", "content": text}
    if len(images) > MAX_IMAGES:
        raise ImageError(f"too many images: {len(images)} (max {MAX_IMAGES})")
    parts: list[dict[str, Any]] = [{"type": "text", "text": text or "请描述这张图片"}]
    for url, _name in images:
        parts.append({"type": "image_url", "image_url": {"url": url}})
    return {"role": "user", "content": parts, "_images": [name for _url, name in images]}


# --------------------------------------------------------------------------- #
# Sessions
# --------------------------------------------------------------------------- #


def _session_files() -> list[str]:
    return sorted(glob_mod.glob(os.path.join(SESSION_DIR, "*.jsonl")))


def _fit(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = " ".join(
            part.get("text") or ""
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    else:
        text = ""
    text = " ".join(text.split())
    if text:
        return text
    return "[图片]" if isinstance(content, list) else ""


def history_view(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    view = []
    for message in messages:
        if message.get("_compact"):
            raw = message.get("content")
            if isinstance(raw, str):
                summary = raw
            elif isinstance(raw, list):
                summary = "\n".join(
                    part.get("text") or ""
                    for part in raw
                    if isinstance(part, dict) and part.get("type") == "text"
                )
            else:
                summary = ""
            summary = summary.strip()
            if summary.startswith("[历史对话摘要]"):
                summary = summary[len("[历史对话摘要]"):].strip()
            keep = message.get("_compact_keep")
            label = "上下文已压缩"
            if isinstance(keep, int) and not isinstance(keep, bool):
                label += " · 仅保留摘要" if keep == 0 else f" · 保留最近 {keep} 轮"
            view.append({"role": "notice", "text": label, "summary": summary})
            continue
        role = message.get("role")
        if role not in ("user", "assistant"):
            continue
        content = message.get("content")
        texts = []
        images = []
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text":
                    texts.append(part.get("text") or "")
                elif part.get("type") == "image_url":
                    url = (part.get("image_url") or {}).get("url")
                    if isinstance(url, str):
                        images.append(url)
        text = "\n".join(texts).strip("\n")
        if not text and not images:
            continue
        item: dict[str, Any] = {"role": role, "text": text}
        if images:
            item["images"] = images
        view.append(item)
    return view


class Session:
    """Append-only JSONL transcript stored under SESSION_DIR."""

    def __init__(self, path: str, uuid: str, sequence: int) -> None:
        self.path = path
        self.uuid = uuid
        self.sequence = sequence
        self.saved = 1  # the system prompt is never written to disk

    def _write(self, record: dict[str, Any]) -> None:
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def log(self, messages: list[dict[str, Any]]) -> None:
        pending = [message for message in messages[self.saved:] if message.get("role") != "system"]
        if not pending:
            self.saved = len(messages)
            return
        try:
            with open(self.path, "a", encoding="utf-8") as handle:
                for message in pending:
                    handle.write(json.dumps(message, ensure_ascii=False) + "\n")
        except OSError as exc:
            raise SessionError(str(exc)) from exc
        self.saved = len(messages)

    def append_compact(self, record: dict[str, Any], tail: list[dict[str, Any]]) -> None:
        """Append a compact marker followed by the retained tail (one write)."""
        lines = [json.dumps(record, ensure_ascii=False)]
        lines.extend(json.dumps(message, ensure_ascii=False) for message in tail)
        try:
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write("\n".join(lines) + "\n")
        except OSError as exc:
            raise SessionError(str(exc)) from exc


def create_session(model: str, workspace: str) -> Session:
    try:
        os.makedirs(SESSION_DIR, mode=0o700, exist_ok=True)
    except OSError as exc:
        raise SessionError(str(exc)) from exc
    sequence = 1
    for path in _session_files():
        match = re.match(r"(\d+)-", os.path.basename(path))
        if match:
            sequence = max(sequence, int(match.group(1)) + 1)
    while True:
        uuid = os.urandom(4).hex()
        name = f"{sequence:03d}-{time.strftime('%Y%m%d')}-{uuid}.jsonl"
        path = os.path.join(SESSION_DIR, name)
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            sequence += 1
            continue
        except OSError as exc:
            raise SessionError(str(exc)) from exc
        os.close(descriptor)
        session = Session(path, uuid, sequence)
        try:
            session._write({
                "_type": "meta",
                "version": SESSION_VERSION,
                "uuid": uuid,
                "sequence": sequence,
                "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "model": model,
                "workspace": workspace,
            })
        except OSError as exc:
            raise SessionError(str(exc)) from exc
        return session


def _summary_message(record: dict[str, Any]) -> dict[str, Any]:
    summary = record.get("summary")
    text = summary.strip() if isinstance(summary, str) else ""
    keep = record.get("keep")
    message: dict[str, Any] = {
        "role": "user",
        "content": "[历史对话摘要]\n\n" + (text or "(摘要为空)"),
        "_compact": True,
    }
    if isinstance(keep, int) and not isinstance(keep, bool):
        message["_compact_keep"] = keep
    return message


def _iter_session_records(handle: Any) -> Any:
    for raw in handle:
        try:
            record = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(record, dict):
            yield record


def _replay_session_records(records: Any) -> list[dict[str, Any]]:
    """Rebuild messages, resetting at every compact marker (the last one wins)."""
    loaded: list[dict[str, Any]] = []
    for record in records:
        if record.get("_type") == "compact":
            loaded = [_summary_message(record)]
            continue
        if record.get("role"):
            loaded.append(record)
    return loaded


def _last_compact_offset(path: str) -> int | None:
    """Find the byte offset of the last compact record without parsing old lines."""
    last: int | None = None
    with open(path, "rb") as handle:
        while True:
            offset = handle.tell()
            raw = handle.readline()
            if not raw:
                break
            if b'"compact"' not in raw:
                continue
            try:
                record = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(record, dict) and record.get("_type") == "compact":
                last = offset
    return last


def load_session(uuid: str, messages: list[dict[str, Any]]) -> Session:
    if not re.fullmatch(r"[0-9a-f]{8}", uuid):
        raise SessionError("session id must be 8 lowercase hex characters")
    matches = sorted(glob_mod.glob(os.path.join(SESSION_DIR, f"*-{uuid}.jsonl")))
    if not matches:
        raise SessionError(f"session not found: {uuid} (use --list to see saved sessions)")
    if len(matches) > 1:
        raise SessionError(f"ambiguous session id: {uuid}")
    path = matches[0]
    match = re.match(r"(\d+)-(\d{8})-([0-9a-f]{8})\.jsonl$", os.path.basename(path))
    sequence = int(match.group(1)) if match else 0
    try:
        offset = _last_compact_offset(path)
        with open(path, "rb") as handle:
            if offset:
                handle.seek(offset)
            loaded = _replay_session_records(_iter_session_records(handle))
    except OSError as exc:
        raise SessionError(str(exc)) from exc
    messages.extend(loaded)
    session = Session(path, uuid, sequence)
    session.saved = len(messages)
    return session


def delete_session(uuid: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{8}", uuid):
        raise SessionError("session id must be 8 lowercase hex characters")
    matches = sorted(glob_mod.glob(os.path.join(SESSION_DIR, f"*-{uuid}.jsonl")))
    if not matches:
        raise SessionError(f"session not found: {uuid}")
    try:
        os.remove(matches[0])
    except OSError as exc:
        raise SessionError(str(exc)) from exc


def session_summaries() -> list[dict[str, Any]]:
    rows = []
    for path in _session_files():
        match = re.match(r"(\d+)-(\d{8})-([0-9a-f]{8})\.jsonl$", os.path.basename(path))
        if not match:
            continue
        sequence, date, uuid = int(match.group(1)), match.group(2), match.group(3)
        model = workspace = "?"
        title = ""
        count = 0
        try:
            with open(path, encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(record, dict):
                        continue
                    if record.get("_type") == "meta":
                        model = record.get("model") or "?"
                        workspace = record.get("workspace") or "?"
                        continue
                    if record.get("_type") == "compact":
                        count = 1  # the synthetic summary message
                        continue
                    if not record.get("role"):
                        continue
                    count += 1
                    if not title and record.get("role") == "user":
                        title = _message_text(record.get("content"))
        except OSError:
            continue
        rows.append({
            "uuid": uuid,
            "sequence": sequence,
            "date": date,
            "count": count,
            "model": model,
            "workspace": workspace,
            "title": title or "(无文字)",
        })
    rows.sort(key=lambda row: row["sequence"], reverse=True)
    return rows


def latest_session_uuid() -> str | None:
    best: tuple[int, str, str] | None = None
    for path in _session_files():
        match = re.match(r"(\d+)-(\d{8})-([0-9a-f]{8})\.jsonl$", os.path.basename(path))
        if not match:
            continue
        key = (int(match.group(1)), match.group(2), match.group(3))
        if best is None or key > best:
            best = key
    return best[2] if best else None


def list_sessions() -> int:
    rows = session_summaries()
    if not rows:
        print(f"no sessions yet (they are stored in {SESSION_DIR})")
        return 0
    print(f"{'ID':<8}  {'DATE':<10}  {'MSGS':>4}  {'MODEL':<12}  {'WORKSPACE':<28}  TITLE")
    for row in rows[:50]:
        date = row["date"]
        shown_date = f"{date[:4]}-{date[4:6]}-{date[6:]}"
        print(
            f"{row['uuid']:<8}  {shown_date:<10}  {row['count']:>4}  "
            f"{_fit(row['model'], 12):<12}  {_fit(row['workspace'], 28):<28}  {_fit(row['title'], 40)}"
        )
    print()
    print(f"{len(rows)} session{'s' if len(rows) != 1 else ''} in {SESSION_DIR}")
    if len(rows) > 50:
        print("showing the latest 50")
    print("resume with: python3 benben-agent.py -c <ID>")
    return 0


# --------------------------------------------------------------------------- #
# Context compaction
# --------------------------------------------------------------------------- #


COMPACT_PROMPT = f"""你在为一个 AI agent 压缩对话历史。请把下面的对话总结成简洁、结构化的中文摘要，
供之后继续任务时使用。只根据对话内容总结，不要编造，也不要回答对话里的问题。

输出使用下面的小节（没有内容就写“无”）：
## 目标
## 已完成
## 关键决策
## 涉及的文件与路径
## 执行过的命令
## 未完成事项
## 注意事项

总长度控制在 {COMPACT_SUMMARY_CHARS} 字以内。"""


def _truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = max(1, limit * 2 // 3)
    tail = limit - head - 12
    if tail > 0:
        return text[:head] + " …(截断)… " + text[-tail:]
    return text[:limit]


def _content_text(message: dict[str, Any]) -> tuple[str, int]:
    """Return (text, image count) without ever copying base64 payloads."""
    raw = message.get("content")
    if isinstance(raw, str):
        return raw.strip(), 0
    if not isinstance(raw, list):
        return "", 0
    texts: list[str] = []
    images = 0
    for part in raw:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text":
            texts.append(part.get("text") or "")
        elif part.get("type") == "image_url":
            images += 1
    return "\n".join(texts).strip(), images


def _summary_text(message: dict[str, Any]) -> str:
    body, images = _content_text(message)
    if images:
        body = (body + "\n[图片]").strip()
    role = message.get("role")
    if role == "user":
        return ("用户：" + _truncate_text(body, COMPACT_MESSAGE_CHARS)) if body else ""
    if role == "tool":
        return ("工具结果：" + _truncate_text(body, COMPACT_TOOL_CHARS)) if body else ""
    if role == "assistant":
        pieces: list[str] = []
        if body:
            pieces.append(_truncate_text(body, COMPACT_MESSAGE_CHARS))
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            arguments = _truncate_text(str(function.get("arguments") or "{}"), COMPACT_ARGS_CHARS)
            pieces.append(f"[调用工具 {function.get('name')}] {arguments}")
        return ("助手：\n" + "\n".join(pieces)) if pieces else ""
    return ""


def _estimate_text_tokens(text: str) -> int:
    wide = 0
    for char in text:
        code = ord(char)
        if (
            0x2E80 <= code <= 0xA4CF
            or 0xAC00 <= code <= 0xD7AF
            or 0xF900 <= code <= 0xFAFF
            or 0xFE30 <= code <= 0xFE4F
            or 0xFF00 <= code <= 0xFFEF
        ):
            wide += 1
    return wide + max(1, (len(text) - wide) // 4)


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """Rough context estimate for display only (CJK count as one token)."""
    total = 0
    for message in messages:
        body, images = _content_text(message)
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            body += "\n" + str(function.get("arguments") or "")
        total += 4 + _estimate_text_tokens(body) + 800 * images
    return total


def _compact_split(messages: list[dict[str, Any]], keep: int) -> int:
    """Index where the retained tail starts; cuts only before a user turn."""
    if keep <= 0:
        return len(messages)
    seen = 0
    for index in range(len(messages) - 1, 0, -1):
        message = messages[index]
        if message.get("role") == "user" and not message.get("_compact"):
            seen += 1
            if seen >= keep:
                return index
    return 1


def _compaction_chunks(messages: list[dict[str, Any]]) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for message in messages:
        text = _summary_text(message)
        if not text:
            continue
        if current and size + len(text) + 1 > COMPACT_MAX_CHARS:
            chunks.append("\n".join(current))
            current = []
            size = 0
        current.append(text)
        size += len(text) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


def compact_history(
    client: ChatClient,
    messages: list[dict[str, Any]],
    session: "Session | None" = None,
    keep: int = COMPACT_DEFAULT_KEEP,
    progress: "Callable[[str], None] | None" = None,
) -> dict[str, Any]:
    """Summarize older turns, then append a marker plus the retained tail."""
    if not messages or messages[0].get("role") != "system":
        raise CompactError("messages must start with the system prompt")
    if keep < 0:
        keep = 0
    split = _compact_split(messages, keep)
    prefix = messages[1:split]
    tail = messages[split:]
    if not prefix:
        raise CompactError("nothing to compact (history is too short)")
    chunks = _compaction_chunks(prefix)
    if not chunks:
        raise CompactError("nothing to compact (no text in history)")

    before_tokens = estimate_tokens(messages)
    before_total = len(messages) - 1
    usage: dict[str, int] = {}
    truncated = False
    summary = ""
    for index, chunk in enumerate(chunks, 1):
        if progress is not None:
            progress(f"正在总结第 {index}/{len(chunks)} 段…")
        if summary:
            prompt = (
                f"{COMPACT_PROMPT}\n\n已有摘要：\n{summary}\n\n"
                f"接下来要并入摘要的对话：\n{chunk}"
            )
        else:
            prompt = f"{COMPACT_PROMPT}\n\n待压缩的对话：\n{chunk}"
        reply = client.chat([{"role": "user", "content": prompt}], [])
        merge_usage(usage, client.last_usage)
        if client.last_finish_reason == "length":
            truncated = True
        content, _images = _content_text(reply)
        if not content:
            raise ChatError("summarization returned an empty answer")
        summary = content

    summary_message = {
        "role": "user",
        "content": "[历史对话摘要]\n\n" + summary,
        "_compact": True,
        "_compact_keep": keep,
    }
    new_messages = [messages[0], summary_message, *tail]
    record = {
        "_type": "compact",
        "version": COMPACT_VERSION,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "keep": keep,
        "tail_count": len(tail),
        "before_messages": len(prefix),
        "summary": summary,
    }
    if session is not None:
        session.log(messages)
        session.append_compact(record, tail)
    messages[:] = new_messages
    if session is not None:
        session.saved = len(messages)
    return {
        "summary": summary,
        "keep": keep,
        "before_messages": len(prefix),
        "before_total": before_total,
        "after_messages": len(new_messages) - 1,
        "kept_messages": len(tail),
        "before_tokens": before_tokens,
        "after_tokens": estimate_tokens(new_messages),
        "usage": usage,
        "truncated": truncated,
        "chunks": len(chunks),
        "persisted": session is not None,
    }


# --------------------------------------------------------------------------- #
# Skills (Agent Skills open format)
# --------------------------------------------------------------------------- #


def _yaml_value(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _parse_frontmatter(text: str) -> dict[str, Any] | None:
    """Parse the small YAML subset used by SKILL.md frontmatter."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if not lines or lines[0].rstrip() != "---":
        return None
    end = None
    for index in range(1, len(lines)):
        if lines[index].rstrip() == "---":
            end = index
            break
    if end is None:
        return None

    meta: dict[str, Any] = {}
    seen: set[str] = set()
    recognized = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
    nested: dict[str, str] | None = None
    block_key: str | None = None
    block_style = ""
    block: list[str] = []
    index = 1
    while index < end:
        raw = lines[index]
        if not raw.strip() or raw.lstrip().startswith("#"):
            index += 1
            continue
        if raw.startswith((" ", "\t")):
            if nested is not None:
                match = re.match(r"^([^:]+):\s*(.*)$", raw.strip())
                if match:
                    nested[_yaml_value(match.group(1))] = _yaml_value(match.group(2))
            elif block_key is not None:
                block.append(raw.strip())
            index += 1
            continue

        if block_key is not None:
            meta[block_key] = (" " if block_style == ">" else "\n").join(block).strip()
            block_key = None
            block = []
        nested = None

        match = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", raw)
        if not match:
            index += 1
            continue
        key = match.group(1)
        value = match.group(2).strip()
        if key in recognized:
            if key in seen:
                return None
            seen.add(key)
        if key == "metadata":
            nested = {}
            meta["metadata"] = nested
        elif value in ("|", ">", "|-", ">-", "|+", ">+"):
            block_key = key
            block_style = value[0]
            block = []
        else:
            meta[key] = _yaml_value(value)
        index += 1

    if block_key is not None:
        meta[block_key] = (" " if block_style == ">" else "\n").join(block).strip()
    return meta


def _validate_skill_meta(meta: dict[str, Any], directory: str) -> tuple[str | None, str | None]:
    name = meta.get("name", directory)
    if not isinstance(name, str) or not SKILL_NAME_RE.fullmatch(name) or len(name) > 64:
        return None, "name must be 1-64 lowercase letters, digits and hyphens"
    description = meta.get("description")
    if not isinstance(description, str) or not (1 <= len(description) <= 1024):
        return None, "description must be 1-1024 characters"
    compatibility = meta.get("compatibility")
    if compatibility is not None and (not isinstance(compatibility, str) or len(compatibility) > 500):
        return None, "compatibility must be at most 500 characters"
    metadata = meta.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        return None, "metadata must be a mapping"
    allowed = meta.get("allowed-tools")
    if allowed is not None and not isinstance(allowed, str):
        return None, "allowed-tools must be a space-separated string"
    return str(name), None


def skill_roots(workspace_root: str, only_own: bool = False) -> list[str]:
    if only_own:
        return [HOME_SKILLS_DIR]
    return [
        os.path.join(workspace_root, ".agents", "skills"),
        os.path.join(workspace_root, ".claude", "skills"),
        os.path.join(workspace_root, ".codex", "skills"),
        HOME_SKILLS_DIR,
        CROSS_SKILLS_DIR,
        CLAUDE_SKILLS_DIR,
        CODEX_SKILLS_DIR,
        CODEX_ADMIN_SKILLS_DIR,
    ]


def _dir_has_files(path: str) -> bool:
    if not os.path.isdir(path):
        return False
    for _root, _dirs, files in os.walk(path):
        if files:
            return True
    return False


def _has_extra_files(skill_dir: str) -> bool:
    for root, _dirs, files in os.walk(skill_dir):
        for name in files:
            if root == skill_dir and name == "SKILL.md":
                continue
            return True
    return False


def discover_skills(workspace_root: str, only_own: bool = False) -> dict[str, dict[str, Any]]:
    """Load only skill metadata; bodies and resources stay on disk until requested."""
    skills: dict[str, dict[str, Any]] = {}
    for root in skill_roots(workspace_root, only_own):
        if not os.path.isdir(root):
            continue
        try:
            entries = sorted(os.listdir(root))
        except OSError:
            continue
        for entry in entries:
            directory = os.path.join(root, entry)
            skill_md = os.path.join(directory, "SKILL.md")
            if not os.path.isdir(directory) or not os.path.isfile(skill_md):
                continue
            try:
                with open(skill_md, "r", encoding="utf-8", errors="replace") as handle:
                    head = handle.read(SKILL_FRONTMATTER_BYTES)
            except OSError as exc:
                print(f"[skill] {entry}: {exc}", file=sys.stderr)
                continue
            meta = _parse_frontmatter(head)
            if meta is None:
                print(f"[skill] {entry}: invalid or missing frontmatter", file=sys.stderr)
                continue
            name, problem = _validate_skill_meta(meta, entry)
            if problem:
                print(f"[skill] {entry}: {problem}", file=sys.stderr)
                continue
            if name in skills:
                continue
            skills[name] = {
                "name": name,
                "description": meta["description"],
                "path": skill_md,
                "dir": directory,
                "license": meta.get("license") or "",
                "compatibility": meta.get("compatibility") or "",
                "metadata": meta.get("metadata") or {},
                "allowed_tools": meta.get("allowed-tools") or "",
                "has_scripts": _dir_has_files(os.path.join(directory, "scripts")),
                "has_resources": _has_extra_files(directory),
            }
    return skills


def build_system_prompt(root: str, skills: dict[str, dict[str, Any]]) -> str:
    prompt = SYSTEM_PROMPT.format(root=root)
    if not skills:
        return prompt
    lines = ["", "可用技能（任务匹配时先调用 load_skill 读取完整说明）："]
    for skill in sorted(skills.values(), key=lambda item: item["name"]):
        description = " ".join(skill["description"].split())
        lines.append(f"- {skill['name']}：{description}")
    return prompt + "\n".join(lines)


LOAD_SKILL_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "load_skill",
        "description": "Load the full SKILL.md instructions for an available skill.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Skill name from the available skills list."}
            },
            "required": ["name"],
        },
    },
}

READ_SKILL_RESOURCE_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "read_skill_resource",
        "description": "Read a reference or asset file inside a skill directory.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Skill name."},
                "path": {
                    "type": "string",
                    "description": "File path relative to the skill root, e.g. references/REFERENCE.md.",
                },
            },
            "required": ["name", "path"],
        },
    },
}

RUN_SKILL_SCRIPT_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "run_skill_script",
        "description": "Run a script from a skill's scripts/ directory. Requires user approval.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Skill name."},
                "script": {
                    "type": "string",
                    "description": "Script path relative to the skill's scripts/ directory.",
                },
                "args": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Command-line arguments passed to the script.",
                },
                "timeout_seconds": {
                    "type": "integer",
                    "description": "Kill the script after this many seconds. Default 60, max 300.",
                },
            },
            "required": ["name", "script"],
        },
    },
}


def tool_schemas(workspace: "Workspace") -> list[dict[str, Any]]:
    tools = list(TOOL_SCHEMAS)
    if not workspace.skills:
        return tools
    tools.append(LOAD_SKILL_SCHEMA)
    if any(skill["has_resources"] for skill in workspace.skills.values()):
        tools.append(READ_SKILL_RESOURCE_SCHEMA)
    if workspace.allow_skill_scripts and any(skill["has_scripts"] for skill in workspace.skills.values()):
        tools.append(RUN_SKILL_SCRIPT_SCHEMA)
    return tools


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List files and subdirectories inside the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory relative to the workspace root. Default '.'.",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a text file inside the workspace, with line numbers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path relative to the workspace root."},
                    "offset": {
                        "type": "integer",
                        "description": "First line to read, 1-based. Default 1.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": f"Maximum number of lines. Default and max {MAX_FILE_LINES}.",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": "Search file contents with a regex inside the workspace. Skips .git, node_modules and build directories.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Python regular expression."},
                    "path": {"type": "string", "description": "Directory relative to the workspace root. Default '.'."},
                    "include": {
                        "type": "string",
                        "description": "Only search files whose name matches this glob, e.g. '*.py'. Default all files.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum matching lines. Default 100, max 500.",
                    },
                    "ignore_case": {"type": "boolean", "description": "Case-insensitive search. Default false."},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "glob",
            "description": "Find files by glob pattern, e.g. '**/*.py', inside the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern relative to path; '**' matches recursively.",
                    },
                    "path": {"type": "string", "description": "Directory relative to the workspace root. Default '.'."},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or overwrite a text file inside the workspace. Requires user approval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path relative to the workspace root."},
                    "content": {"type": "string", "description": "Full new file content."},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replace an exact text snippet in a file, like a patch. Requires user approval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path relative to the workspace root."},
                    "old_string": {
                        "type": "string",
                        "description": "Exact text to replace, including indentation. Must be unique unless replace_all is true.",
                    },
                    "new_string": {"type": "string", "description": "Replacement text."},
                    "replace_all": {
                        "type": "boolean",
                        "description": "Replace every occurrence instead of requiring a unique match. Default false.",
                    },
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run a shell command in the workspace root. Requires user approval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to execute."},
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "Kill the command after this many seconds. Default 60, max 300.",
                    },
                },
                "required": ["command"],
            },
        },
    },
]


READ_ONLY_COMMANDS = {
    "basename", "cat", "cksum", "comm", "curl", "cut", "df", "dirname", "dmesg", "du",
    "echo", "egrep", "expand", "false", "fgrep", "file", "find", "fmt", "fold",
    "free", "grep", "head", "hexdump", "id", "jq", "journalctl", "join", "last",
    "lsof", "ls", "md5sum", "netstat", "nl", "nm", "objdump", "od", "paste",
    "printf", "ps", "pwd", "readelf", "readlink", "realpath", "rg", "sensors",
    "sha1sum", "sha256sum", "sha512sum", "sort", "ss", "stat", "strings", "tac",
    "tail", "tr", "tree", "true", "uname", "uniq", "unexpand", "uptime", "users",
    "w", "wc", "wget", "which", "whoami", "xxd",
}

SORT_UNSAFE = ("--output", "--compress-program")
FIND_UNSAFE = {
    "-delete", "-exec", "-execdir", "-ok", "-okdir",
    "-fprint", "-fprint0", "-fprintf", "-fls",
}
JOURNAL_UNSAFE = (
    "--vacuum", "--rotate", "--flush", "--sync", "--relinquish",
    "--setup-keys", "--update-catalog", "--purge",
)
DMESG_UNSAFE = {"--clear", "--console-off", "--console-on", "--level"}

# curl/wget are read-only only when they cannot write files. wget writes to a
# file by default, so it must explicitly target stdout (or use --spider).
CURL_UNSAFE_ARG_PREFIXES = (
    "-o", "-O", "-d", "-F", "-T", "-X", "-c", "-D", "-K", "-J",
    "--output", "--remote-name", "--data", "--form", "--json", "--upload-file",
    "--request", "--cookie-jar", "--dump-header", "--config", "--output-dir",
    "--trace", "--libcurl", "--stderr",
)
WGET_UNSAFE_ARG_PREFIXES = (
    "-o", "-a", "-P", "-i", "-e", "-m", "-r", "-c",
    "--output-file", "--append-output", "--directory-prefix",
    "--input-file", "--execute", "--mirror", "--recursive", "--continue",
    "--post-data", "--post-file", "--method", "--body-data", "--body-file",
    "--save-cookies", "--load-cookies", "--config",
)

# Per-program argument checks for commands that are read-only only when no
# mutating flag is present. A match means "treat as mutating".
UNSAFE_ARG_CHECKS = {
    "sort": lambda arg: (
        arg.startswith(SORT_UNSAFE)
        or (arg.startswith("-") and not arg.startswith("--") and "o" in arg[1:])
    ),
    "find": lambda arg: arg in FIND_UNSAFE,
    "journalctl": lambda arg: arg.startswith(JOURNAL_UNSAFE),
    "rg": lambda arg: arg.startswith(("--pre=", "--pre-glob")) or arg == "--pre",
    "dmesg": lambda arg: (
        arg in DMESG_UNSAFE
        or (arg.startswith("-") and not arg.startswith("--") and any(ch in arg[1:] for ch in "cCDEn"))
    ),
}


def _wget_reads_stdout(args: list[str]) -> bool:
    if any(arg.startswith(WGET_UNSAFE_ARG_PREFIXES) for arg in args):
        return False
    for index, arg in enumerate(args):
        if arg == "--spider":
            return True
        if arg in ("-O", "--output-document") and index + 1 < len(args) and args[index + 1] == "-":
            return True
        if arg in ("-O-", "--output-document=-"):
            return True
    return False


def is_read_only_command(command: str) -> bool:
    """Conservative check for shell commands that cannot change system state.

    Any unknown command, wrapper, redirection, substitution or unsafe flag is
    treated as mutating, so the worst case is an unnecessary confirmation.
    """
    if not command.strip():
        return False
    if "`" in command or "$" in command or ">" in command or "<<" in command or "<(" in command:
        return False
    if re.search(r"(?<!&)&(?!&)", command):
        return False

    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        return False

    segments: list[list[str]] = [[]]
    for token in tokens:
        if token in (";", "&&", "||", "|"):
            if segments[-1]:
                segments.append([])
            continue
        if token in ("&", "(", ")", "<", ">", ">>", "<<", "&>", ">|"):
            return False
        if "<" in token or ">" in token:
            return False
        segments[-1].append(token)

    for words in segments:
        if not words:
            continue
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", words[0]):
            return False
        program = words[0]
        if "/" in program or program not in READ_ONLY_COMMANDS:
            return False
        args = words[1:]
        unsafe = UNSAFE_ARG_CHECKS.get(program)
        if unsafe and any(unsafe(arg) for arg in args):
            return False
        if program == "curl" and any(arg.startswith(CURL_UNSAFE_ARG_PREFIXES) for arg in args):
            return False
        if program == "wget" and not _wget_reads_stdout(args):
            return False
    return True


def _capture_process(
    args: Any,
    cwd: str,
    timeout: int,
    env: dict[str, str] | None = None,
    shell: bool = False,
) -> tuple[int, str, str, bool]:
    """Run a process and cap each output stream at MAX_TOOL_OUTPUT."""

    def drain(pipe: Any, store: dict[str, Any]) -> None:
        try:
            while True:
                chunk = pipe.read(4096)
                if not chunk:
                    break
                room = MAX_TOOL_OUTPUT - store["size"]
                if room <= 0:
                    store["truncated"] = True
                    continue
                if len(chunk) > room:
                    store["truncated"] = True
                    chunk = chunk[:room]
                store["parts"].append(chunk)
                store["size"] += len(chunk)
        except (OSError, ValueError):
            pass
        finally:
            try:
                pipe.close()
            except OSError:
                pass

    try:
        process = subprocess.Popen(
            args,
            shell=shell,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            env=env,
        )
    except OSError as exc:
        raise ToolError(str(exc)) from exc

    out: dict[str, Any] = {"parts": [], "size": 0, "truncated": False}
    err: dict[str, Any] = {"parts": [], "size": 0, "truncated": False}
    readers = [
        threading.Thread(target=drain, args=(process.stdout, out), daemon=True),
        threading.Thread(target=drain, args=(process.stderr, err), daemon=True),
    ]
    for reader in readers:
        reader.start()
    timed_out = False
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        process.wait()
    for reader in readers:
        reader.join(1.0)
    if timed_out:
        raise ToolError(f"command timed out after {timeout}s")
    return (
        process.returncode,
        "".join(out["parts"]),
        "".join(err["parts"]),
        bool(out["truncated"] or err["truncated"]),
    )


def _script_command(path: str) -> list[str]:
    try:
        with open(path, "rb") as handle:
            first = handle.readline(256)
    except OSError as exc:
        raise ToolError(str(exc)) from exc
    text = first.decode("utf-8", "replace").strip()
    if text.startswith("#!"):
        parts = shlex.split(text[2:].strip())
        if parts:
            return parts + [path]
    candidates = SCRIPT_RUNNERS.get(os.path.splitext(path)[1].lower())
    if candidates:
        for candidate in candidates:
            if os.path.isabs(candidate):
                if os.access(candidate, os.X_OK):
                    return [candidate, path]
                continue
            found = shutil.which(candidate)
            if found:
                return [found, path]
        raise ToolError(f"interpreter not found for {os.path.splitext(path)[1]}: {', '.join(candidates)}")
    if os.access(path, os.X_OK):
        return [path]
    raise ToolError(
        f"cannot determine how to run {os.path.basename(path)}; add a shebang or use a supported extension"
    )


class Workspace:
    def __init__(
        self,
        root: str,
        confirm: bool = False,
        allow_shell: bool = True,
        skills: dict[str, dict[str, Any]] | None = None,
        allow_skill_scripts: bool = True,
    ) -> None:
        self.root = os.path.realpath(root)
        self.confirm = confirm
        self.allow_shell = allow_shell
        self.skills = skills or {}
        self.allow_skill_scripts = allow_skill_scripts
        self.interactive = sys.stdin.isatty()

    # -- helpers ---------------------------------------------------------- #

    def _resolve(self, path: str) -> str:
        if not path:
            raise ToolError("path must not be empty")
        full = os.path.realpath(path if os.path.isabs(path) else os.path.join(self.root, path))
        try:
            inside = os.path.commonpath([full, self.root]) == self.root
        except ValueError:
            inside = False
        if not inside:
            raise ToolError(f"path is outside the workspace: {path}")
        return full

    def _approve(self, action: str, detail: str, read_only: bool = False) -> bool:
        if read_only or not self.confirm:
            return True
        if not self.interactive:
            print(f"[denied] {action} needs approval: {detail}", file=sys.stderr)
            return False
        print(f"\n[approval] {action}: {detail}", flush=True)
        try:
            answer = input("Allow? [Y/n] ")
        except EOFError:
            return False
        return answer.strip().lower() not in ("n", "no")

    def _display(self, path: str) -> str:
        try:
            return os.path.relpath(path, self.root)
        except ValueError:
            return path

    # -- tool implementations --------------------------------------------- #

    def list_dir(self, path: str = ".") -> str:
        full = self._resolve(path)
        if not os.path.isdir(full):
            raise ToolError(f"not a directory: {path}")
        entries = []
        with os.scandir(full) as iterator:
            for entry in sorted(iterator, key=lambda item: item.name.lower()):
                if entry.is_dir():
                    entries.append(entry.name + "/")
                else:
                    try:
                        entries.append(f"{entry.name} ({entry.stat().st_size} bytes)")
                    except OSError:
                        entries.append(entry.name)
                if len(entries) >= 300:
                    entries.append("... (truncated)")
                    break
        return "\n".join(entries) or "(empty directory)"

    def read_file(self, path: str, offset: int = 1, limit: int = MAX_FILE_LINES) -> str:
        full = self._resolve(path)
        if not os.path.isfile(full):
            raise ToolError(f"not a file: {path}")
        offset = max(1, int(offset))
        limit = max(1, min(int(limit), MAX_FILE_LINES))
        try:
            with open(full, "r", encoding="utf-8", errors="replace") as handle:
                chunk = list(islice(handle, offset - 1, offset - 1 + limit))
                more = next(handle, None) is not None
        except OSError as exc:
            raise ToolError(str(exc)) from exc

        if not chunk:
            return f"(no lines at offset {offset})"
        if "\x00" in "".join(chunk[:8]):
            raise ToolError("this looks like a binary file; attach pictures with /image or --image")
        numbered = [f"{number:>5} | {line.rstrip(chr(10))}" for number, line in enumerate(chunk, start=offset)]
        result = "\n".join(numbered)
        if more:
            result += "\n... (more lines; use offset to continue)"
        return result

    def search(
        self,
        pattern: str,
        path: str = ".",
        include: str = "",
        max_results: int = 100,
        ignore_case: bool = False,
    ) -> str:
        full = self._resolve(path)
        if not os.path.isdir(full):
            raise ToolError(f"not a directory: {path}")
        try:
            regex = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
        except re.error as exc:
            raise ToolError(f"invalid regular expression: {exc}") from exc
        limit = max(1, min(int(max_results), 500))
        name_glob = include.strip() or "*"
        hits: list[str] = []
        for dirpath, dirnames, filenames in os.walk(full):
            dirnames[:] = sorted(name for name in dirnames if name not in SKIP_DIRS)
            for filename in sorted(filenames):
                if not fnmatch.fnmatch(filename, name_glob):
                    continue
                file_path = os.path.join(dirpath, filename)
                try:
                    if os.path.getsize(file_path) > MAX_SEARCH_FILE:
                        continue
                    if os.path.commonpath([os.path.realpath(file_path), self.root]) != self.root:
                        continue
                    with open(file_path, "r", encoding="utf-8", errors="replace") as handle:
                        for number, line in enumerate(handle, 1):
                            if "\x00" in line:
                                break
                            if regex.search(line):
                                hits.append(f"{self._display(file_path)}:{number}: {line.rstrip(chr(10))[:300]}")
                                if len(hits) >= limit:
                                    hits.append(f"... (stopped at {limit} matches)")
                                    return "\n".join(hits)
                except (OSError, ValueError):
                    continue
        return "\n".join(hits) or "(no matches)"

    def glob(self, pattern: str, path: str = ".") -> str:
        full = self._resolve(path)
        if not os.path.isdir(full):
            raise ToolError(f"not a directory: {path}")
        if not pattern.strip():
            raise ToolError("pattern must not be empty")
        matches: list[str] = []
        for match in glob_mod.iglob(os.path.join(full, pattern), recursive=True):
            real = os.path.realpath(match)
            try:
                if os.path.commonpath([real, self.root]) != self.root:
                    continue
            except ValueError:
                continue
            relative = os.path.relpath(real, full)
            if any(part in SKIP_DIRS for part in relative.split(os.sep)):
                continue
            matches.append(self._display(real) + ("/" if os.path.isdir(real) else ""))
            if len(matches) >= MAX_GLOB_RESULTS:
                matches.append(f"... (truncated at {MAX_GLOB_RESULTS})")
                break
        return "\n".join(sorted(matches)) or "(no matches)"

    def write_file(self, path: str, content: str) -> str:
        full = self._resolve(path)
        preview = content if len(content) <= 300 else content[:300] + "..."
        if not self._approve("write_file", f"{self._display(full)}\n{preview}"):
            raise ToolError("permission denied by user")
        try:
            parent = os.path.dirname(full)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(full, "w", encoding="utf-8") as handle:
                handle.write(content)
        except OSError as exc:
            raise ToolError(str(exc)) from exc
        return f"wrote {len(content)} characters to {self._display(full)}"

    def edit_file(
        self,
        path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> str:
        full = self._resolve(path)
        if not os.path.isfile(full):
            raise ToolError(f"not a file: {path}")
        if not old_string:
            raise ToolError("old_string must not be empty")
        try:
            with open(full, "r", encoding="utf-8", newline="") as handle:
                original = handle.read()
        except UnicodeDecodeError as exc:
            raise ToolError(f"not a UTF-8 text file: {path}") from exc
        except OSError as exc:
            raise ToolError(str(exc)) from exc

        old, new = old_string, new_string
        if old not in original and "\r\n" in original:
            old, new = old.replace("\n", "\r\n"), new.replace("\n", "\r\n")
        count = original.count(old)
        if count == 0:
            raise ToolError("old_string not found in the file")
        if count > 1 and not replace_all:
            raise ToolError(f"old_string occurs {count} times; add more context or set replace_all")
        replaced = count if replace_all else 1
        updated = original.replace(old, new, count if replace_all else 1)
        suffix = "s" if replaced > 1 else ""
        detail = f"{self._display(full)} ({replaced} replacement{suffix})\n- {old[:200]}\n+ {new[:200]}"
        if not self._approve("edit_file", detail):
            raise ToolError("permission denied by user")
        try:
            with open(full, "w", encoding="utf-8", newline="") as handle:
                handle.write(updated)
        except OSError as exc:
            raise ToolError(str(exc)) from exc
        return f"edited {self._display(full)} ({replaced} replacement{suffix})"

    def save_text(self, path: str, text: str) -> str:
        """Write text anywhere for an explicit user command; no workspace confinement."""
        full = os.path.realpath(path if os.path.isabs(path) else os.path.join(self.root, path))
        existed = os.path.exists(full)
        try:
            parent = os.path.dirname(full)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(full, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
        except OSError as exc:
            raise ToolError(str(exc)) from exc
        shown = self._display(full)
        if shown.startswith(".."):
            shown = full
        return shown + (" (overwritten)" if existed else "")

    def _skill(self, name: str) -> dict[str, Any]:
        skill = self.skills.get(name)
        if skill is None:
            raise ToolError(f"unknown skill: {name}")
        return skill

    def load_skill(self, name: str) -> str:
        skill = self._skill(name)
        try:
            with open(skill["path"], "r", encoding="utf-8", errors="replace") as handle:
                text = handle.read(MAX_SKILL_BYTES + 1)
        except OSError as exc:
            raise ToolError(str(exc)) from exc
        if len(text) > MAX_SKILL_BYTES:
            text = text[:MAX_SKILL_BYTES] + "\n... (truncated)"
        return text

    def read_skill_resource(self, name: str, path: str) -> str:
        skill = self._skill(name)
        base = os.path.realpath(skill["dir"])
        full = os.path.realpath(os.path.join(base, path))
        try:
            inside = os.path.commonpath([full, base]) == base
        except ValueError:
            inside = False
        if not inside:
            raise ToolError("resource path is outside the skill directory")
        if full == os.path.realpath(skill["path"]):
            raise ToolError("use load_skill to read SKILL.md")
        if not os.path.isfile(full):
            raise ToolError(f"not a file: {path}")
        try:
            if os.path.getsize(full) > MAX_SKILL_RESOURCE_BYTES:
                raise ToolError(f"resource is too large: {path}")
            with open(full, "rb") as handle:
                data = handle.read()
        except OSError as exc:
            raise ToolError(str(exc)) from exc
        if b"\x00" in data[:4096]:
            raise ToolError(f"binary resource: {path}")
        text = data.decode("utf-8", "replace")
        if len(text) > MAX_SKILL_BYTES:
            text = text[:MAX_SKILL_BYTES] + "\n... (truncated)"
        return f"# {os.path.relpath(full, base)}\n\n{text}"

    def run_skill_script(
        self,
        name: str,
        script: str,
        args: list[str] | None = None,
        timeout_seconds: int = 60,
    ) -> str:
        if not self.allow_skill_scripts:
            raise ToolError("skill scripts are disabled")
        skill = self._skill(name)
        script_root = os.path.realpath(os.path.join(skill["dir"], "scripts"))
        full = os.path.realpath(os.path.join(script_root, script))
        try:
            inside = os.path.commonpath([full, script_root]) == script_root
        except ValueError:
            inside = False
        if not inside:
            raise ToolError("script path is outside the skill's scripts directory")
        if not os.path.isfile(full):
            raise ToolError(f"not a file: {script}")
        if args is None:
            args = []
        if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
            raise ToolError("args must be a list of strings")
        command = _script_command(full)
        preview = f"{name}/{script}" + (" " + " ".join(args[:10]) if args else "")
        if not self._approve("run_skill_script", preview):
            raise ToolError("permission denied by user")
        timeout = max(1, min(int(timeout_seconds), 300))
        env = os.environ.copy()
        env["BENBEN_SKILL_DIR"] = skill["dir"]
        env["BENBEN_WORKSPACE"] = self.root
        returncode, stdout, stderr, truncated = _capture_process(
            command + args, self.root, timeout, env=env
        )
        parts = [f"exit code: {returncode}"]
        if stdout:
            parts.append("stdout:\n" + stdout)
        if stderr:
            parts.append("stderr:\n" + stderr)
        output = "\n".join(parts)
        if truncated or len(output) > MAX_TOOL_OUTPUT:
            output = output[:MAX_TOOL_OUTPUT] + "\n... (output truncated)"
        return output

    def run_command(self, command: str, timeout_seconds: int = 60) -> str:
        if not self.allow_shell:
            raise ToolError("the run_command tool is disabled")
        if not command.strip():
            raise ToolError("command must not be empty")
        if not self._approve("run_command", command, read_only=is_read_only_command(command)):
            raise ToolError("permission denied by user")
        timeout = max(1, min(int(timeout_seconds), 300))
        returncode, stdout, stderr, truncated = _capture_process(command, self.root, timeout, shell=True)
        parts = [f"exit code: {returncode}"]
        if stdout:
            parts.append("stdout:\n" + stdout)
        if stderr:
            parts.append("stderr:\n" + stderr)
        output = "\n".join(parts)
        if truncated or len(output) > MAX_TOOL_OUTPUT:
            output = output[:MAX_TOOL_OUTPUT] + "\n... (output truncated)"
        return output

    def dispatch(self, name: str, arguments: dict[str, Any]) -> str:
        handlers = {
            "list_dir": self.list_dir,
            "read_file": self.read_file,
            "search": self.search,
            "glob": self.glob,
            "write_file": self.write_file,
            "edit_file": self.edit_file,
            "run_command": self.run_command,
        }
        if self.skills:
            handlers["load_skill"] = self.load_skill
            handlers["read_skill_resource"] = self.read_skill_resource
            if self.allow_skill_scripts:
                handlers["run_skill_script"] = self.run_skill_script
        handler = handlers.get(name)
        if handler is None:
            raise ToolError(f"unknown tool: {name}")
        return handler(**arguments)


# --------------------------------------------------------------------------- #
# Agent loop
# --------------------------------------------------------------------------- #


def run_turn(
    client: ChatClient,
    workspace: Workspace,
    messages: list[dict[str, Any]],
    max_steps: int,
    render: bool = False,
    show_thinking: bool = False,
    session: "Session | None" = None,
    emit: "Callable[[str, str], None] | None" = None,
    tools: list[dict[str, Any]] | None = None,
) -> bool:
    """Let the model call tools until it produces a final answer. Returns True if it did."""

    def report(kind: str, text: str) -> None:
        if emit is not None:
            emit(kind, text)
        elif kind == "content":
            if render:
                _print_markdown(text)
            else:
                print(f"{_agent_prefix()}{text.rstrip(chr(10) + chr(13))}", flush=True)
        elif kind == "tool":
            print(_style(f"  {text}", DIM, supports_color()), flush=True)
        elif kind == "usage":
            print(_style(f"  {text}", DIM, supports_color()), file=sys.stderr, flush=True)
        elif kind == "warning":
            print(_style(f"  {text}", YELLOW, supports_color()), file=sys.stderr, flush=True)
        else:
            _print_thinking(text)

    usage_total: dict[str, int] = {}
    truncated = False

    def add_usage(usage: Any) -> None:
        merge_usage(usage_total, usage)

    try:
        for _ in range(max_steps):
            reply = client.chat(messages, tools if tools is not None else TOOL_SCHEMAS)
            add_usage(client.last_usage)
            if client.last_finish_reason == "length":
                truncated = True
            reasoning = reply.get("reasoning_content") or reply.get("reasoning") or ""
            reply = clean_assistant_message(reply)
            if isinstance(reasoning, str) and reasoning.strip():
                reply["_thinking"] = reasoning
            messages.append(reply)

            if show_thinking and isinstance(reasoning, str) and reasoning.strip():
                report("thinking", reasoning)

            content = reply.get("content")
            if content:
                report("content", content)

            tool_calls = reply.get("tool_calls") or []
            if not tool_calls:
                return True

            for call in tool_calls:
                name = call["function"]["name"]
                raw_arguments = call["function"].get("arguments") or "{}"
                report("tool", f"[{name}] {_one_line(raw_arguments, 120)}")
                try:
                    arguments = json.loads(raw_arguments)
                    if not isinstance(arguments, dict):
                        raise TypeError("arguments must be a JSON object")
                    result = workspace.dispatch(name, arguments)
                except (json.JSONDecodeError, ToolError, TypeError, ValueError) as exc:
                    result = f"error: {exc}"

                messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})

        print(f"[stopped] reached the limit of {max_steps} model calls without a final answer", file=sys.stderr)
        return False
    finally:
        client.last_turn_usage = dict(usage_total)
        client.last_turn_truncated = truncated
        if usage_total:
            parts = [
                f"prompt={usage_total.get('prompt_tokens', 0)}",
                f"completion={usage_total.get('completion_tokens', 0)}",
                f"reasoning={usage_total.get('reasoning_tokens', 0)}",
                f"cached={usage_total.get('cached_tokens', 0)}",
                f"total={usage_total.get('total_tokens', 0)}",
            ]
            report("usage", "[usage] " + " ".join(parts))
        if truncated:
            report("warning", "输出被 max_tokens 截断，回答可能不完整")
        if session is not None:
            try:
                session.log(messages)
            except SessionError as exc:
                print(f"[session] {exc}", file=sys.stderr)


def _one_line(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
ITALIC = "\x1b[3m"
UNDERLINE = "\x1b[4m"
STRIKE = "\x1b[9m"
CYAN = "\x1b[36m"
MAGENTA = "\x1b[35m"
YELLOW = "\x1b[33m"


def supports_color() -> bool:
    if os.environ.get("NO_COLOR") or os.environ.get("TERM") == "dumb":
        return False
    return sys.stdout.isatty()


def _style(text: str, code: str, color: bool) -> str:
    return f"{code}{text}{RESET}" if color else text


def _input_prompt(text: str, code: str) -> str:
    """Color the input prompt; readline needs ANSI zero-width markers."""
    if not supports_color():
        return text
    styled = f"{code}{text}{RESET}"
    return f"\001{styled}\002" if sys.stdin.isatty() and readline is not None else styled


def _agent_prefix() -> str:
    return _style("agent> ", BOLD + MAGENTA, supports_color())


def _char_width(char: str) -> int:
    if unicodedata.combining(char):
        return 0
    return 2 if unicodedata.east_asian_width(char) in ("F", "W") else 1


def _display_width(text: str) -> int:
    return sum(_char_width(char) for char in ANSI_RE.sub("", text))


def _pad(text: str, width: int, mode: str = "left") -> str:
    gap = max(0, width - _display_width(text))
    if mode == "right":
        return " " * gap + text
    if mode == "center":
        left = gap // 2
        return " " * left + text + " " * (gap - left)
    return text + " " * gap


def _sanitize(text: str) -> str:
    """Remove terminal escape sequences from model output before styling it."""
    text = ANSI_RE.sub("", text)
    text = re.sub(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?", "", text)
    text = text.replace("\x1b", "")
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)


def _inline_markdown(text: str, color: bool) -> str:
    text = _sanitize(text)
    text = re.sub(
        r"\[([^\]]+)\]\(([^)\s]+)\)",
        lambda m: f"{m.group(1)} ({_style(m.group(2), DIM, color)})",
        text,
    )
    text = re.sub(r"\*\*\*(.+?)\*\*\*", lambda m: _style(m.group(1), BOLD + ITALIC, color), text)
    text = re.sub(r"\*\*(.+?)\*\*", lambda m: _style(m.group(1), BOLD, color), text)
    text = re.sub(r"__(.+?)__", lambda m: _style(m.group(1), BOLD, color), text)
    text = re.sub(r"~~(.+?)~~", lambda m: _style(m.group(1), STRIKE, color), text)
    text = re.sub(r"(?<!\*)\*([^*\n]+?)\*(?!\*)", lambda m: _style(m.group(1), ITALIC, color), text)
    text = re.sub(r"(?<!\w)_([^_\n]+?)_(?!\w)", lambda m: _style(m.group(1), ITALIC, color), text)
    return text


def _inline(text: str, color: bool) -> str:
    parts = re.split(r"(`[^`\n]+`)", text)
    rendered = []
    for part in parts:
        if len(part) >= 2 and part.startswith("`") and part.endswith("`"):
            rendered.append(_style(part[1:-1], CYAN, color))
        else:
            rendered.append(_inline_markdown(part, color))
    return "".join(rendered)


def _table_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _is_table_separator(line: str) -> bool:
    cells = _table_cells(line)
    return bool(cells) and all(re.fullmatch(r":?-{1,}:?", cell) for cell in cells)


def _render_table(header: str, separator: str, rows: list[str], color: bool) -> str:
    aligns = [
        "center" if cell.startswith(":") and cell.endswith(":")
        else "right" if cell.endswith(":") else "left"
        for cell in _table_cells(separator)
    ]

    table = [[_inline(cell, color) for cell in _table_cells(header)]]
    for row in rows:
        table.append([_inline(cell, color) for cell in _table_cells(row)])

    columns = max(len(row) for row in table)
    for row in table:
        row.extend([""] * (columns - len(row)))
    widths = [
        max(_display_width(row[column]) for row in table)
        for column in range(columns)
    ]

    output = []
    for index, row in enumerate(table):
        cells = [
            _pad(row[column], widths[column], aligns[column])
            for column in range(columns)
        ]
        output.append(" │ ".join(cells).rstrip())
        if index == 0:
            output.append("─┼─".join("─" * width for width in widths))
    return "\n".join(output)


def render_markdown(text: str, color: bool = True) -> str:
    """Render a small Markdown subset for the terminal using ANSI styles."""
    text = _sanitize(text.replace("\r\n", "\n").replace("\r", "\n"))
    lines = text.split("\n")
    output: list[str] = []
    index = 0

    while index < len(lines):
        line = lines[index]

        fence = re.match(r"^\s*```(.*)$", line)
        if fence:
            language = fence.group(1).strip()
            index += 1
            code: list[str] = []
            while index < len(lines) and not re.match(r"^\s*```\s*$", lines[index]):
                code.append(lines[index])
                index += 1
            if index < len(lines):
                index += 1
            if language:
                output.append(_style(language, DIM, color))
            for code_line in code:
                output.append(_style("    " + code_line, CYAN, color) if color else "    " + code_line)
            continue

        if "|" in line and index + 1 < len(lines) and _is_table_separator(lines[index + 1]):
            rows = []
            cursor = index + 2
            while cursor < len(lines) and lines[cursor].strip() and "|" in lines[cursor]:
                rows.append(lines[cursor])
                cursor += 1
            output.append(_render_table(line, lines[index + 1], rows, color))
            index = cursor
            continue

        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading:
            title = re.sub(r"\s+#+\s*$", "", heading.group(2).strip())
            style = BOLD + UNDERLINE if len(heading.group(1)) == 1 else BOLD
            output.append(_style(_inline(title, color), style, color))
            index += 1
            continue

        if re.fullmatch(r"\s*([-*_])(\s*\1){2,}\s*", line):
            output.append(_style("─" * 40, DIM, color))
            index += 1
            continue

        quote = re.match(r"^\s*>\s?(.*)$", line)
        if quote:
            output.append(_style("│ ", DIM, color) + _inline(quote.group(1), color))
            index += 1
            continue

        bullet = re.match(r"^(\s*)[-*+]\s+(.*)$", line)
        if bullet:
            output.append(f"{bullet.group(1)}• {_inline(bullet.group(2), color)}")
            index += 1
            continue

        numbered = re.match(r"^(\s*)(\d+[.)])\s+(.*)$", line)
        if numbered:
            output.append(f"{numbered.group(1)}{numbered.group(2)} {_inline(numbered.group(3), color)}")
            index += 1
            continue

        output.append(_inline(line, color))
        index += 1

    return "\n".join(output)


def _print_markdown(text: str) -> None:
    rendered = render_markdown(text, color=supports_color()).strip("\n")
    lines = rendered.splitlines() or [""]
    print(f"{_agent_prefix()}{lines[0]}")
    for line in lines[1:]:
        print(f"  {line}" if line else "")


def _print_thinking(text: str) -> None:
    """Print the model's reasoning in a dim block; never fed back to the model."""
    lines = _sanitize(text).strip().splitlines()
    if not lines:
        return
    color = supports_color()
    print(_style("  [thinking]", DIM, color), flush=True)
    for line in lines:
        print(_style(f"    {line}", DIM, color), flush=True)


def print_help() -> None:
    print("Commands: /help, /reset, /compact [N], /skill [name [question]],")
    print("          /image [path|clear], /save [--tool] [--thinking] [file],")
    print("          /confirm on|off, /thinking on|off, /model [name|number|list],")
    print("          /exit (Ctrl-D)")
    print("Skills: off by default; enable with --skill (dirs: <cwd>/.agents|.claude|.codex/skills,")
    print("        ~/.benben-agent/skills, ~/.agents/skills, ~/.claude/skills, ~/.codex/skills)")
    print(f"Sessions are saved to {SESSION_DIR}; list with --list, resume with -c <id>.")
    print("Ctrl+C needs two presses to exit; the first one cancels input or the running task.")


def enable_utf8_input() -> None:
    """Make Backspace delete whole UTF-8 characters.

    Linux keeps a per-terminal IUTF8 flag. Without it the tty line discipline
    erases one byte at a time, so deleting a Chinese character leaves an
    incomplete UTF-8 sequence and reading the line raises UnicodeDecodeError.
    """
    if termios is None or not sys.platform.startswith("linux") or not sys.stdin.isatty():
        return
    try:
        fd = sys.stdin.fileno()
        attrs = termios.tcgetattr(fd)
        attrs[0] = attrs[0] | 0x4000  # IUTF8, not always exported by Python
        termios.tcsetattr(fd, termios.TCSADRAIN, attrs)
    except (OSError, termios.error):
        pass


def read_user_line(prompt: str) -> str:
    """Read one line, with line editing on a terminal and tolerant decoding otherwise."""
    if sys.stdin.isatty() and readline is not None:
        try:
            return input(prompt)
        except UnicodeDecodeError:
            print("\n[input] could not decode that line, please retype it", file=sys.stderr)
            return ""

    sys.stdout.write(prompt)
    sys.stdout.flush()
    buffer = getattr(sys.stdin, "buffer", None)
    if buffer is None:  # unusual stdin replacement
        return input()
    raw = buffer.readline()
    if not raw:
        raise EOFError
    encoding = getattr(sys.stdin, "encoding", None) or "utf-8"
    return raw.decode(encoding, "replace").rstrip("\r\n")


def repair_interrupted_turn(messages: list[dict[str, Any]]) -> None:
    """Answer tool calls that were cut off by Ctrl+C so the next request stays valid."""
    last_assistant = None
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].get("role") == "assistant":
            last_assistant = index
            break
    if last_assistant is None:
        return
    tool_calls = messages[last_assistant].get("tool_calls") or []
    answered = {
        message.get("tool_call_id")
        for message in messages[last_assistant + 1 :]
        if message.get("role") == "tool"
    }
    for call in tool_calls:
        if call.get("id") not in answered:
            messages.append(
                {"role": "tool", "tool_call_id": call["id"], "content": "error: interrupted by user"}
            )


def render_history(
    messages: list[dict[str, Any]],
    model: str,
    root: str,
    include_tools: bool = False,
    include_thinking: bool = False,
) -> str:
    """Render the conversation as Markdown for /save."""
    lines = [
        "# 会话记录",
        "",
        f"- 模型：`{model}`",
        f"- 工作区：`{root}`",
        f"- 导出时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "---",
    ]
    for message in messages:
        role = message.get("role")
        raw = message.get("content") or ""
        if isinstance(raw, list):
            text = "".join(
                part.get("text") or ""
                for part in raw
                if isinstance(part, dict) and part.get("type") == "text"
            )
        else:
            text = raw
        content = text.rstrip()
        if message.get("_compact"):
            lines.extend(["", "## 上下文摘要", "", content or "(无摘要)"])
            continue
        if role == "user":
            lines.extend(["", "## 用户", "", content or "(无文字)"])
            for name in message.get("_images") or []:
                lines.extend(["", f"> 🖼 图片: {name}"])
        elif role == "assistant":
            calls = (message.get("tool_calls") or []) if include_tools else []
            thinking = message.get("_thinking") if include_thinking else ""
            if not (content or thinking or calls):
                continue
            lines.extend(["", "## 助手"])
            if thinking:
                lines.extend(["", "### 思考", "", "```text", thinking.rstrip(), "```"])
            if content:
                lines.extend(["", content])
            for call in calls:
                function = call.get("function") or {}
                arguments = str(function.get("arguments") or "{}")
                lines.extend(["", f"> 🔧 `{function.get('name')}`", "", "```json", arguments, "```"])
        elif role == "tool" and include_tools:
            lines.extend(["", "### 工具结果", "", "```text", content, "```"])
    return "\n".join(lines).strip() + "\n"


class InterruptGuard:
    """Require two consecutive Ctrl+C presses within a short window."""

    def __init__(self, window: float = 2.0) -> None:
        self.window = window
        self.last = 0.0

    def pressed(self) -> bool:
        now = time.monotonic()
        if self.last and now - self.last <= self.window:
            self.last = 0.0
            return True
        self.last = now
        return False

    def reset(self) -> None:
        self.last = 0.0


WEB_HTML = r"""<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>benben-agent</title>
<style>
* { box-sizing: border-box; }
body { margin: 0; font: 14px/1.6 system-ui, -apple-system, "Segoe UI", sans-serif; color: #222; background: #f6f7f9; }
#app { display: flex; height: 100vh; }
aside { width: 250px; flex: none; background: #fff; border-right: 1px solid #e3e6ea; padding: 12px; overflow-y: auto; }
.brand { font-weight: 600; margin-bottom: 12px; }
.model-row { margin-bottom: 10px; }
.model-row label { display: block; color: #8a919c; font-size: 12px; margin-bottom: 4px; }
#model { width: 100%; padding: 6px; border: 1px solid #c9ced6; border-radius: 6px; background: #fff; }
#new { width: 100%; padding: 6px; margin-bottom: 10px; border: 1px solid #c9ced6; background: #f9fafb; border-radius: 6px; cursor: pointer; }
.session-row { display: flex; gap: 6px; margin-bottom: 6px; }
.session { display: block; flex: 1; min-width: 0; text-align: left; padding: 8px; border: 1px solid transparent; background: transparent; border-radius: 6px; cursor: pointer; }
.session:hover { background: #f1f3f6; }
.session.active { background: #eef4ff; border-color: #b9d2ff; }
.delete { flex: none; width: 30px; border: 0; border-radius: 6px; background: transparent; color: #b0b6c0; cursor: pointer; }
.delete:hover { color: #d92d20; background: #fef3f2; }
.session .title { display: block; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.session .meta { display: block; color: #8a919c; font-size: 12px; }
main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
#log { flex: 1; overflow-y: auto; padding: 18px; display: flex; flex-direction: column; }
.msg { max-width: 820px; margin-bottom: 12px; padding: 10px 12px; border-radius: 8px; white-space: pre-wrap; word-break: break-word; }
.msg.user { align-self: flex-end; background: #dcebff; }
.msg.assistant { align-self: flex-start; background: #fff; border: 1px solid #e3e6ea; white-space: normal; }
.tool { align-self: flex-start; color: #8a919c; font-size: 12px; white-space: pre-wrap; margin: -6px 0 10px; }
.warning { align-self: flex-start; color: #d92d20; font-size: 12px; white-space: pre-wrap; margin: -6px 0 10px; }
.compact { align-self: stretch; margin: -2px 0 12px; padding: 8px 12px; border: 1px dashed #c9ced6; border-radius: 8px; background: #fafbfc; color: #667085; font-size: 12px; white-space: normal; }
.compact summary { cursor: pointer; }
.compact-body { margin-top: 8px; color: #344054; font-size: 13px; }
.compact-body p { margin: 6px 0; }
.compact-body h1, .compact-body h2, .compact-body h3 { margin: 8px 0 4px; font-size: 14px; }
.compact-body ul, .compact-body ol { margin: 6px 0; padding-left: 22px; }
.compact-body code { background: #f4f5f7; padding: 1px 4px; border-radius: 4px; }
#status { min-height: 20px; padding: 0 18px; color: #8a919c; }
#composer { border-top: 1px solid #e3e6ea; background: #fff; padding: 12px 18px; }
#input { width: 100%; height: 72px; resize: vertical; padding: 8px; border: 1px solid #c9ced6; border-radius: 6px; font: inherit; }
.row { display: flex; align-items: center; gap: 10px; margin-top: 8px; }
.file { color: #4a6cf7; cursor: pointer; }
#files-label { color: #8a919c; font-size: 12px; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
#send { padding: 6px 18px; border: 0; border-radius: 6px; background: #4a6cf7; color: #fff; cursor: pointer; }
#send:disabled { background: #a9b8f8; cursor: default; }
#compact { padding: 6px 12px; border: 1px solid #c9ced6; border-radius: 6px; background: #f9fafb; color: #475467; cursor: pointer; }
#compact:disabled { color: #b0b6c0; cursor: default; }
.msg.assistant > :first-child { margin-top: 0; }
.msg.assistant > :last-child { margin-bottom: 0; }
.msg.assistant p { margin: 6px 0; }
.msg.assistant h1, .msg.assistant h2, .msg.assistant h3, .msg.assistant h4 { margin: 10px 0 6px; line-height: 1.3; }
.msg.assistant h1 { font-size: 20px; }
.msg.assistant h2 { font-size: 18px; }
.msg.assistant h3 { font-size: 16px; }
.msg.assistant h4, .msg.assistant h5, .msg.assistant h6 { font-size: 14px; }
.msg.assistant pre { background: #f4f5f7; padding: 10px; border-radius: 6px; overflow-x: auto; }
.msg.assistant code { background: #f4f5f7; padding: 1px 4px; border-radius: 4px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 13px; }
.msg.assistant pre code { background: none; padding: 0; }
.msg.assistant blockquote { border-left: 3px solid #d0d5dd; margin: 6px 0; padding-left: 10px; color: #667085; }
.msg.assistant ul, .msg.assistant ol { margin: 6px 0; padding-left: 22px; }
.msg.assistant table { border-collapse: collapse; margin: 8px 0; font-size: 13px; }
.msg.assistant th, .msg.assistant td { border: 1px solid #d0d5dd; padding: 4px 8px; }
.msg.assistant th { background: #f4f5f7; }
.msg.assistant a { color: #4a6cf7; }
.msg.assistant hr { border: 0; border-top: 1px solid #e3e6ea; margin: 10px 0; }
.msg.assistant img { max-width: 100%; border-radius: 6px; margin: 6px 0; }
.msg.user img.upload { display: block; max-width: 260px; max-height: 200px; margin-top: 6px; border-radius: 6px; border: 1px solid #c9ced6; }
.previews { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; }
.previews img { width: 56px; height: 56px; object-fit: cover; border-radius: 6px; border: 1px solid #c9ced6; }
.msg img, .previews img { cursor: zoom-in; }
.lightbox { position: fixed; top: 0; right: 0; bottom: 0; left: 0; z-index: 100; background: rgba(0, 0, 0, 0.82); display: flex; align-items: center; justify-content: center; padding: 24px; cursor: zoom-out; }
.lightbox[hidden] { display: none; }
.lightbox img { max-width: 96vw; max-height: 92vh; border-radius: 6px; box-shadow: 0 10px 40px rgba(0, 0, 0, 0.5); }
</style>
</head>
<body>
<div id="app">
  <aside>
    <div class="brand">benben-agent</div>
    <div class="model-row">
      <label for="model">模型</label>
      <select id="model"></select>
    </div>
    <button id="new">+ 新会话</button>
    <div id="sessions"></div>
  </aside>
  <main>
    <div id="log"></div>
    <div id="status"></div>
    <div id="composer">
      <textarea id="input" placeholder="输入消息，Enter 发送，Shift+Enter 换行"></textarea>
      <div id="previews" class="previews"></div>
      <div class="row">
        <label class="file">选择图片<input id="files" type="file" accept="image/*" multiple hidden></label>
        <span id="files-label"></span>
        <button id="compact" title="把较早的对话总结成摘要，减少上下文">压缩</button>
        <button id="send">发送</button>
      </div>
    </div>
  </main>
</div>
<div id="lightbox" class="lightbox" hidden>
  <img id="lightbox-img" alt="">
</div>
<script>
const token = new URLSearchParams(location.search).get("token") || "";
let currentSession = null;
let currentModel = "";
let uploads = [];
let reading = 0;

const logEl = document.getElementById("log");
const statusEl = document.getElementById("status");
const inputEl = document.getElementById("input");
const sendEl = document.getElementById("send");
const compactEl = document.getElementById("compact");
const filesEl = document.getElementById("files");
const filesLabel = document.getElementById("files-label");
const sessionsEl = document.getElementById("sessions");
const previewsEl = document.getElementById("previews");
const modelEl = document.getElementById("model");
const lightboxEl = document.getElementById("lightbox");
const lightboxImg = document.getElementById("lightbox-img");

function openLightbox(src) {
  lightboxImg.src = src;
  lightboxEl.hidden = false;
}

function closeLightbox() {
  lightboxEl.hidden = true;
  lightboxImg.removeAttribute("src");
}

lightboxEl.addEventListener("click", closeLightbox);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !lightboxEl.hidden) closeLightbox();
});
logEl.addEventListener("dblclick", (event) => {
  if (event.target instanceof HTMLImageElement) openLightbox(event.target.src);
});
previewsEl.addEventListener("dblclick", (event) => {
  if (event.target instanceof HTMLImageElement) openLightbox(event.target.src);
});

async function api(path, body) {
  const options = { headers: { "X-Token": token } };
  if (body !== undefined) {
    options.method = "POST";
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const res = await fetch(path, options);
  const text = await res.text();
  if (!res.ok) throw new Error(text || res.statusText);
  return text ? JSON.parse(text) : {};
}

function addLine(cls, text) {
  const div = document.createElement("div");
  div.className = cls;
  div.textContent = text;
  logEl.appendChild(div);
  logEl.scrollTop = logEl.scrollHeight;
}

function escapeHtml(text) {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function safeImage(url) {
  return /^(https?:\/\/|data:image\/(?:png|jpeg|gif|webp|bmp);base64,)/i.test(url) ? url : null;
}

function renderInline(text) {
  const parts = text.split(/(`[^`\n]+`)/g);
  return parts.map((part) => {
    if (part.length >= 2 && part.startsWith("`") && part.endsWith("`")) {
      return "<code>" + part.slice(1, -1) + "</code>";
    }
    return part
      .replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, (match, alt, src) => {
        const url = safeImage(src);
        return url ? '<img src="' + url + '" alt="' + alt + '" loading="lazy" referrerpolicy="no-referrer">' : match;
      })
      .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (match, label, href) => {
        return /^https?:\/\//i.test(href)
          ? '<a href="' + href + '" target="_blank" rel="noopener noreferrer">' + label + "</a>"
          : match;
      })
      .replace(/\*\*\*([^*]+)\*\*\*/g, "<strong><em>$1</em></strong>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/~~([^~]+)~~/g, "<del>$1</del>")
      .replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, "$1<em>$2</em>");
  }).join("");
}

function tableCells(line) {
  return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim());
}

function isTableSeparator(line) {
  const cells = tableCells(line);
  return cells.length > 0 && cells.every((cell) => /^:?-{1,}:?$/.test(cell));
}

function renderMarkdown(text) {
  const lines = text.replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
  const output = [];
  let i = 0;
  const startsBlock = (index) => {
    const line = lines[index];
    return /^\s*```/.test(line)
      || /^#{1,6}\s+/.test(line)
      || /^\s*>\s?/.test(line)
      || /^\s*[-*+]\s+/.test(line)
      || /^\s*\d+[.)]\s+/.test(line)
      || /^\s*([-*_])(\s*\1){2,}\s*$/.test(line)
      || (line.includes("|") && index + 1 < lines.length && isTableSeparator(lines[index + 1]));
  };
  while (i < lines.length) {
    const line = lines[i];
    let match;
    if (!line.trim()) { i++; continue; }
    if ((match = line.match(/^\s*```(.*)$/))) {
      const lang = match[1].trim();
      i++;
      const code = [];
      while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) { code.push(lines[i]); i++; }
      if (i < lines.length) i++;
      output.push("<pre><code" + (lang ? ' class="lang-' + escapeHtml(lang) + '"' : "") + ">" + escapeHtml(code.join("\n")) + "</code></pre>");
      continue;
    }
    if ((match = line.match(/^(#{1,6})\s+(.*)$/))) {
      const level = match[1].length;
      output.push("<h" + level + ">" + renderInline(escapeHtml(match[2].trim())) + "</h" + level + ">");
      i++;
      continue;
    }
    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) {
      output.push("<hr>");
      i++;
      continue;
    }
    if (line.includes("|") && i + 1 < lines.length && isTableSeparator(lines[i + 1])) {
      const header = tableCells(line);
      const aligns = tableCells(lines[i + 1]).map((cell) => {
        const left = cell.startsWith(":");
        const right = cell.endsWith(":");
        return left && right ? "center" : right ? "right" : "left";
      });
      i += 2;
      const rows = [];
      while (i < lines.length && lines[i].trim() && lines[i].includes("|")) {
        rows.push(tableCells(lines[i]));
        i++;
      }
      let html = "<table><thead><tr>";
      header.forEach((cell, index) => {
        html += '<th style="text-align:' + (aligns[index] || "left") + '">' + renderInline(escapeHtml(cell)) + "</th>";
      });
      html += "</tr></thead><tbody>";
      for (const row of rows) {
        html += "<tr>";
        row.forEach((cell, index) => {
          html += '<td style="text-align:' + (aligns[index] || "left") + '">' + renderInline(escapeHtml(cell)) + "</td>";
        });
        html += "</tr>";
      }
      html += "</tbody></table>";
      output.push(html);
      continue;
    }
    if ((match = line.match(/^\s*>\s?(.*)$/))) {
      output.push("<blockquote>" + renderInline(escapeHtml(match[1])) + "</blockquote>");
      i++;
      continue;
    }
    if ((match = line.match(/^\s*[-*+]\s+(.*)$/))) {
      const items = [];
      while (i < lines.length && (match = lines[i].match(/^\s*[-*+]\s+(.*)$/))) {
        items.push("<li>" + renderInline(escapeHtml(match[1])) + "</li>");
        i++;
      }
      output.push("<ul>" + items.join("") + "</ul>");
      continue;
    }
    if ((match = line.match(/^\s*\d+[.)]\s+(.*)$/))) {
      const items = [];
      while (i < lines.length && (match = lines[i].match(/^\s*\d+[.)]\s+(.*)$/))) {
        items.push("<li>" + renderInline(escapeHtml(match[1])) + "</li>");
        i++;
      }
      output.push("<ol>" + items.join("") + "</ol>");
      continue;
    }
    const paragraph = [line];
    i++;
    while (i < lines.length && lines[i].trim() && !startsBlock(i)) {
      paragraph.push(lines[i]);
      i++;
    }
    output.push("<p>" + renderInline(escapeHtml(paragraph.join("\n"))) + "</p>");
  }
  return output.join("");
}

function addMarkdown(text) {
  const div = document.createElement("div");
  div.className = "msg assistant";
  div.innerHTML = renderMarkdown(text);
  logEl.appendChild(div);
  logEl.scrollTop = logEl.scrollHeight;
}

function addUserMessage(text, images) {
  const div = document.createElement("div");
  div.className = "msg user";
  if (text) {
    const paragraph = document.createElement("div");
    paragraph.textContent = text;
    div.appendChild(paragraph);
  }
  for (const image of images) {
    const element = document.createElement("img");
    element.className = "upload";
    element.src = image.data_url;
    element.alt = image.name;
    element.title = image.name;
    div.appendChild(element);
  }
  logEl.appendChild(div);
  logEl.scrollTop = logEl.scrollHeight;
}

function renderHistory(messages) {
  logEl.textContent = "";
  for (const message of messages) {
    if (message.role === "notice") {
      const details = document.createElement("details");
      details.className = "compact";
      const summary = document.createElement("summary");
      summary.textContent = message.text || "上下文已压缩";
      const body = document.createElement("div");
      body.className = "compact-body";
      body.innerHTML = renderMarkdown(message.summary || "");
      details.appendChild(summary);
      details.appendChild(body);
      logEl.appendChild(details);
    } else if (message.role === "assistant") {
      addMarkdown(message.text);
    } else {
      const images = (message.images || []).map((url, index) => ({
        data_url: url,
        name: "图片 " + (index + 1)
      }));
      addUserMessage(message.text, images);
    }
  }
}

function renderPreviews() {
  previewsEl.textContent = "";
  for (const image of uploads) {
    const element = document.createElement("img");
    element.src = image.data_url;
    element.alt = image.name;
    element.title = image.name;
    previewsEl.appendChild(element);
  }
}

async function loadSessions() {
  try {
    const data = await api("/api/sessions");
    sessionsEl.textContent = "";
    for (const s of data.sessions) {
      const row = document.createElement("div");
      row.className = "session-row";
      const button = document.createElement("button");
      button.className = "session" + (s.uuid === currentSession ? " active" : "");
      const title = document.createElement("span");
      title.className = "title";
      title.textContent = s.title;
      const meta = document.createElement("span");
      meta.className = "meta";
      meta.textContent = s.uuid + " · " + s.date + " · " + s.count + " 条";
      button.appendChild(title);
      button.appendChild(meta);
      button.onclick = () => openSession(s.uuid);
      button.oncontextmenu = (event) => {
        event.preventDefault();
        removeSession(s.uuid, s.title);
      };
      const remove = document.createElement("button");
      remove.className = "delete";
      remove.textContent = "×";
      remove.title = "删除会话";
      remove.onclick = (event) => {
        event.stopPropagation();
        removeSession(s.uuid, s.title);
      };
      row.appendChild(button);
      row.appendChild(remove);
      sessionsEl.appendChild(row);
    }
  } catch (err) {
    statusEl.textContent = String(err);
  }
}

function setModelOptions(models, current) {
  modelEl.textContent = "";
  for (const name of models) {
    const option = document.createElement("option");
    option.value = name;
    option.textContent = name;
    modelEl.appendChild(option);
  }
  if (current && !models.includes(current)) {
    const option = document.createElement("option");
    option.value = current;
    option.textContent = current;
    modelEl.appendChild(option);
  }
  if (current) modelEl.value = current;
}

async function loadModels() {
  try {
    const data = await api("/api/models");
    currentModel = data.current || currentModel;
    setModelOptions(data.models, currentModel);
  } catch (err) {
    setModelOptions(currentModel ? [currentModel] : [], currentModel);
    statusEl.textContent = "模型列表加载失败: " + String(err);
  }
}

modelEl.onchange = async () => {
  const name = modelEl.value;
  try {
    const data = await api("/api/model", { model: name });
    currentModel = data.model;
    statusEl.textContent = "模型已切换: " + data.model;
  } catch (err) {
    statusEl.textContent = String(err);
    modelEl.value = currentModel;
  }
};

async function openSession(uuid) {
  try {
    const data = await api("/api/open", { session: uuid });
    currentSession = data.session;
    renderHistory(data.messages);
    loadSessions();
  } catch (err) {
    statusEl.textContent = String(err);
  }
}

async function removeSession(uuid, title) {
  const label = title ? uuid + "（" + title + "）" : uuid;
  if (!window.confirm("删除会话 " + label + "？\n\n会话文件和消息记录会被永久删除。")) return;
  try {
    await api("/api/delete", { session: uuid });
    if (currentSession === uuid) {
      currentSession = null;
      renderHistory([]);
    }
    statusEl.textContent = "已删除会话 " + uuid;
    loadSessions();
  } catch (err) {
    statusEl.textContent = String(err);
  }
}

async function newSession() {
  try {
    await api("/api/new", {});
    currentSession = null;
    renderHistory([]);
    uploads = [];
    filesEl.value = "";
    filesLabel.textContent = "";
    renderPreviews();
    loadSessions();
  } catch (err) {
    statusEl.textContent = String(err);
  }
}

async function compactContext(keep) {
  if (reading > 0 || sendEl.disabled) return;
  if (!token) { statusEl.textContent = "缺少 token"; return; }
  if (!confirm("把较早的对话压缩成摘要？原记录仍保留在会话文件里，但不再发送给模型。")) return;
  const value = Number.isInteger(keep) && keep >= 0 ? keep : 2;
  compactEl.disabled = true;
  sendEl.disabled = true;
  statusEl.textContent = "正在压缩上下文…";
  try {
    const data = await api("/api/compact", { keep: value });
    currentSession = data.session;
    renderHistory(data.messages);
    statusEl.textContent = "已压缩：" + data.before_total + " 条 → " + data.after_messages
      + " 条，约 " + data.before_tokens + " → " + data.after_tokens + " tokens";
    loadSessions();
  } catch (err) {
    statusEl.textContent = String(err);
  } finally {
    compactEl.disabled = false;
    sendEl.disabled = false;
    inputEl.focus();
  }
}

filesEl.onchange = () => {
  uploads = [];
  const files = Array.from(filesEl.files).slice(0, 4);
  reading = files.length;
  filesLabel.textContent = files.length ? "读取中…" : "";
  for (const file of files) {
    const reader = new FileReader();
    reader.onload = () => {
      uploads.push({ name: file.name, data_url: reader.result });
      reading -= 1;
      if (reading === 0) {
        filesLabel.textContent = uploads.map(u => u.name).join(", ");
        renderPreviews();
      }
    };
    reader.onerror = () => {
      reading -= 1;
      if (reading === 0) filesLabel.textContent = "读取失败";
    };
    reader.readAsDataURL(file);
  }
};

async function send() {
  const text = inputEl.value.trim();
  if ((!text && uploads.length === 0) || reading > 0 || sendEl.disabled) return;
  if (!token) { statusEl.textContent = "缺少 token"; return; }
  const compactMatch = text.match(/^\/compact(?:\s+(\d+))?$/);
  if (compactMatch) {
    inputEl.value = "";
    compactContext(compactMatch[1] ? parseInt(compactMatch[1], 10) : 2);
    return;
  }
  const images = uploads.slice();
  const previousSession = currentSession;
  addUserMessage(text, images);
  inputEl.value = "";
  uploads = [];
  filesEl.value = "";
  filesLabel.textContent = "";
  renderPreviews();
  sendEl.disabled = true;
  statusEl.textContent = "处理中…";
  try {
    const response = await fetch("/api/chat/stream", {
      method: "POST",
      headers: { "X-Token": token, "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, images: images }),
    });
    if (!response.ok) {
      const detail = await response.text();
      throw new Error(detail || response.statusText);
    }
    if (!response.body) throw new Error("浏览器不支持流式响应");
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let doneEvent = null;
    let failed = false;
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) break;
      buffer += decoder.decode(chunk.value, { stream: true });
      let boundary;
      while ((boundary = buffer.indexOf("\n\n")) >= 0) {
        const block = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const line = block.split("\n").find((item) => item.startsWith("data: "));
        if (!line) continue;
        const event = JSON.parse(line.slice(6));
        if (event.type === "content") addMarkdown(event.text);
        else if (event.type === "tool") addLine("tool", event.text);
        else if (event.type === "thinking") addLine("tool", "思考: " + event.text);
        else if (event.type === "usage") addLine("tool", event.text);
        else if (event.type === "warning") addLine("warning", "⚠ " + event.text);
        else if (event.type === "error") {
          addLine("warning", "⚠ " + event.text);
          failed = true;
          statusEl.textContent = "请求失败";
        } else if (event.type === "done") doneEvent = event;
      }
    }
    if (doneEvent) {
      currentSession = doneEvent.session;
      if (!failed) statusEl.textContent = doneEvent.ok ? "" : "达到最大步数，任务未完成";
      if (doneEvent.session !== previousSession) loadSessions();
    } else if (!failed) {
      statusEl.textContent = "连接中断，未收到完成事件";
    }
  } catch (err) {
    statusEl.textContent = String(err);
  } finally {
    sendEl.disabled = false;
    inputEl.focus();
  }
}

document.getElementById("new").onclick = newSession;
compactEl.onclick = () => compactContext(2);
sendEl.onclick = send;
inputEl.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    send();
  }
});

(async () => {
  if (!token) { statusEl.textContent = "缺少 token，请用启动时打印的地址打开"; return; }
  try {
    const state = await api("/api/state");
    currentSession = state.session;
    currentModel = state.model || "";
    if (currentModel) setModelOptions([currentModel], currentModel);
    renderHistory(state.messages);
  } catch (err) {
    statusEl.textContent = String(err);
  }
  loadSessions();
  loadModels();
})();
</script>
</body>
</html>
"""


def run_web(
    host: str,
    port: int,
    workspace: Workspace,
    client: ChatClient,
    max_steps: int,
    show_thinking: bool,
) -> int:
    token = secrets.token_urlsafe(16)
    token_bytes = token.encode("utf-8")
    lock = threading.Lock()
    system_message = {"role": "system", "content": build_system_prompt(workspace.root, workspace.skills)}
    state: dict[str, Any] = {"messages": [system_message], "session": None}
    tools = tool_schemas(workspace)

    class Handler(BaseHTTPRequestHandler):
        server_version = "benben-agent"

        def log_message(self, *args: Any) -> None:
            pass

        def _send(self, code: int, body: str, content_type: str) -> None:
            data = body.encode("utf-8")
            try:
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                self.end_headers()
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _json(self, code: int, payload: Any) -> None:
            self._send(code, json.dumps(payload, ensure_ascii=False), "application/json; charset=utf-8")

        def _error(self, code: int, message: str) -> None:
            self._json(code, {"error": message})

        def _sse_start(self) -> bool:
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("X-Accel-Buffering", "no")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                self.end_headers()
                self.close_connection = True
                return True
            except (BrokenPipeError, ConnectionResetError):
                return False

        def _sse(self, payload: dict[str, Any]) -> None:
            data = "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
            try:
                self.wfile.write(data.encode("utf-8"))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _authorized(self, value: str | None) -> bool:
            if not value:
                return False
            return secrets.compare_digest(value.encode("utf-8"), token_bytes)

        def _check_token(self) -> bool:
            if not self._authorized(self.headers.get("X-Token")):
                self._error(403, "invalid or missing token")
                return False
            return True

        def _handle_chat(self, payload: dict[str, Any], stream: bool) -> None:
            message = payload.get("message")
            images = payload.get("images") or []
            if not isinstance(message, str):
                self._error(400, "message must be a string")
                return
            if not isinstance(images, list) or len(images) > MAX_IMAGES:
                self._error(400, f"images must be a list of at most {MAX_IMAGES}")
                return
            loaded = []
            try:
                for item in images:
                    if not isinstance(item, dict):
                        raise ImageError("invalid image item")
                    loaded.append(check_data_url(item.get("data_url"), str(item.get("name") or "upload")))
            except ImageError as exc:
                self._error(400, str(exc))
                return

            events: list[dict[str, str]] = []
            with lock:
                if not message.strip() and not loaded:
                    self._error(400, "empty message")
                    return
                try:
                    user_message = build_user_message(message, loaded)
                except ImageError as exc:
                    self._error(400, str(exc))
                    return
                if state["session"] is None:
                    try:
                        state["session"] = create_session(client.model, workspace.root)
                    except SessionError as exc:
                        self._error(500, str(exc))
                        return
                state["messages"].append(user_message)
                if stream and not self._sse_start():
                    return

                def emit(kind: str, text: str) -> None:
                    if stream:
                        self._sse({"type": kind, "text": text})
                    else:
                        events.append({"type": kind, "text": text})

                try:
                    ok = run_turn(
                        client,
                        workspace,
                        state["messages"],
                        max_steps,
                        render=False,
                        show_thinking=show_thinking,
                        session=state["session"],
                        emit=emit,
                        tools=tools,
                    )
                except ChatError as exc:
                    detail = f"model request failed: {exc}"
                    if stream:
                        self._sse({"type": "error", "text": detail})
                        self._sse({
                            "type": "done",
                            "session": state["session"].uuid,
                            "ok": False,
                            "usage": client.last_turn_usage,
                            "truncated": client.last_turn_truncated,
                        })
                    else:
                        self._error(502, detail)
                    return

                result = {
                    "session": state["session"].uuid,
                    "ok": ok,
                    "usage": client.last_turn_usage,
                    "truncated": client.last_turn_truncated,
                }
                if stream:
                    self._sse({"type": "done", **result})
                else:
                    self._json(200, {
                        **result,
                        "reply": "\n".join(
                            event["text"] for event in events if event["type"] == "content"
                        ),
                        "events": events,
                    })

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/":
                supplied = (parse_qs(parsed.query).get("token") or [""])[0]
                if not secrets.compare_digest(supplied.encode("utf-8"), token_bytes):
                    self._send(403, "invalid or missing token\n", "text/plain; charset=utf-8")
                    return
                self._send(200, WEB_HTML, "text/html; charset=utf-8")
                return
            if not self._check_token():
                return
            if parsed.path == "/api/sessions":
                self._json(200, {"sessions": session_summaries()})
                return
            if parsed.path == "/api/models":
                try:
                    models = client.list_models()
                except ChatError as exc:
                    self._error(502, str(exc))
                    return
                self._json(200, {"models": models, "current": client.model})
                return
            if parsed.path == "/api/state":
                with lock:
                    current = state["session"]
                    self._json(200, {
                        "session": current.uuid if current else None,
                        "model": client.model,
                        "messages": history_view(state["messages"]),
                    })
                return
            self._error(404, "not found")

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if not self._check_token():
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                self._error(400, "invalid content length")
                return
            if length > WEB_MAX_BODY:
                self._error(413, "request body too large")
                return
            raw = self.rfile.read(length) if length else b""
            try:
                payload = json.loads(raw.decode("utf-8")) if raw else {}
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._error(400, "invalid JSON body")
                return
            if not isinstance(payload, dict):
                self._error(400, "body must be a JSON object")
                return

            if parsed.path == "/api/new":
                with lock:
                    state["messages"] = [system_message]
                    state["session"] = None
                self._json(200, {"session": None, "messages": []})
                return

            if parsed.path == "/api/delete":
                uuid = payload.get("session")
                if not isinstance(uuid, str):
                    self._error(400, "session must be a string")
                    return
                with lock:
                    try:
                        delete_session(uuid)
                    except SessionError as exc:
                        self._error(404, str(exc))
                        return
                    if state["session"] is not None and state["session"].uuid == uuid:
                        state["messages"] = [system_message]
                        state["session"] = None
                    current = state["session"]
                    self._json(200, {
                        "deleted": uuid,
                        "session": current.uuid if current else None,
                    })
                return

            if parsed.path == "/api/open":
                uuid = payload.get("session")
                if not isinstance(uuid, str):
                    self._error(400, "session must be a string")
                    return
                with lock:
                    messages = [system_message]
                    try:
                        session = load_session(uuid, messages)
                    except SessionError as exc:
                        self._error(404, str(exc))
                        return
                    state["messages"] = messages
                    state["session"] = session
                    self._json(200, {"session": session.uuid, "messages": history_view(messages)})
                return

            if parsed.path == "/api/model":
                model = payload.get("model")
                if not isinstance(model, str) or not model.strip():
                    self._error(400, "model must be a non-empty string")
                    return
                with lock:
                    client.model = model.strip()
                    current = client.model
                self._json(200, {"model": current})
                return

            if parsed.path == "/api/compact":
                keep = payload.get("keep", COMPACT_DEFAULT_KEEP)
                if isinstance(keep, bool) or not isinstance(keep, int) or not 0 <= keep <= 100:
                    self._error(400, "keep must be an integer between 0 and 100")
                    return
                with lock:
                    if state["session"] is None:
                        self._error(400, "nothing to compact (start a conversation first)")
                        return
                    try:
                        stats = compact_history(client, state["messages"], state["session"], keep)
                    except CompactError as exc:
                        self._error(400, str(exc))
                        return
                    except ChatError as exc:
                        self._error(502, f"summarization failed: {exc}")
                        return
                    except SessionError as exc:
                        self._error(500, str(exc))
                        return
                    self._json(200, {
                        "session": state["session"].uuid,
                        "messages": history_view(state["messages"]),
                        **stats,
                    })
                return

            if parsed.path in ("/api/chat", "/api/chat/stream"):
                self._handle_chat(payload, parsed.path.endswith("/stream"))
                return

            self._error(404, "not found")

    try:
        server = ThreadingHTTPServer((host, port), Handler)
    except OSError as exc:
        print(f"error: cannot bind {host}:{port}: {exc}", file=sys.stderr)
        return 1
    bound_port = server.server_address[1]
    display_host = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    print(f"web ui: http://{display_host}:{bound_port}/?token={token}", flush=True)
    if workspace.allow_shell:
        print("[web] shell tools enabled; web mode never asks for approval", file=sys.stderr)
    if workspace.allow_skill_scripts and any(
        skill["has_scripts"] for skill in workspace.skills.values()
    ):
        print("[web] warning: skill scripts can run without approval", file=sys.stderr)
    if host not in ("127.0.0.1", "localhost", "::1"):
        print(f"[web] warning: listening on {host}; anyone who can reach this port can use the agent", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nweb server stopped")
    finally:
        server.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="AI agent using an OpenAI-compatible chat completions API (standard library only).",
    )
    parser.add_argument("prompt", nargs="*", help="task to run; omit for interactive mode")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("OPENAI_BASE_URL", DEFAULT_BASE_URL),
        help="API base URL, e.g. http://localhost:8000/v1 (default: %(default)s)",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("OPENAI_API_KEY") or DEFAULT_API_KEY,
        help="API key (default: $OPENAI_API_KEY)",
    )
    parser.add_argument(
        "-m",
        "--model",
        default=os.environ.get("OPENAI_MODEL", DEFAULT_MODEL),
        help="model name (default: %(default)s)",
    )
    parser.add_argument("--temperature", type=float, default=None, help="sampling temperature (omitted by default)")
    parser.add_argument("--max-steps", type=int, default=100, help="max model calls per turn (default: %(default)s)")
    parser.add_argument("--timeout", type=float, default=120.0, help="HTTP timeout in seconds (default: %(default)s)")
    parser.add_argument("--cwd", default=".", help="workspace root for tools (default: current directory)")
    confirm_group = parser.add_mutually_exclusive_group()
    confirm_group.add_argument(
        "--confirm",
        dest="confirm",
        action="store_true",
        default=False,
        help="ask before file writes and non-read-only commands (default: off)",
    )
    confirm_group.add_argument(
        "--no-confirm",
        "--yes",
        dest="confirm",
        action="store_false",
        help="never ask; auto-approve writes and commands (default, alias: --yes)",
    )
    parser.add_argument("--no-shell", action="store_true", help="disable the run_command tool")
    parser.add_argument("--plain", action="store_true", help="print raw Markdown instead of terminal rendering")
    parser.add_argument(
        "-i",
        "--image",
        action="append",
        default=[],
        metavar="PATH",
        help=f"attach an image to the task (repeatable, max {MAX_IMAGES})",
    )
    parser.add_argument(
        "--thinking",
        action="store_true",
        help="print the model's reasoning content before each answer (default: off)",
    )
    parser.add_argument(
        "-c",
        "--continue",
        dest="resume",
        nargs="?",
        const="",
        metavar="UUID",
        help="resume the latest session, or one by its 8-character id",
    )
    parser.add_argument(
        "--list",
        dest="list_sessions",
        action="store_true",
        help="list saved sessions and exit",
    )
    parser.add_argument("--web", action="store_true", help="serve a minimal browser UI")
    parser.add_argument("--host", default="127.0.0.1", help="web bind address (default: %(default)s)")
    parser.add_argument("--port", type=int, default=WEB_PORT, help="web port (default: %(default)s)")
    parser.add_argument(
        "--no-skill-scripts",
        action="store_true",
        help="disable run_skill_script when skills are enabled (--skill)",
    )
    skill_group = parser.add_mutually_exclusive_group()
    skill_group.add_argument(
        "--skill",
        dest="skills",
        action="store_true",
        default=False,
        help="enable Agent Skills and expose skill tools (default: off)",
    )
    skill_group.add_argument(
        "--no-skill",
        dest="skills",
        action="store_false",
        help="disable Agent Skills (default; do not scan skills or expose skill tools)",
    )
    parser.add_argument(
        "--only-own-skills",
        action="store_true",
        help="with --skill, only load ~/.benben-agent/skills; ignore other directories",
    )
    args = parser.parse_args(argv)

    # `-c` may be followed by a prompt; only an 8-hex value is a session id.
    if args.resume is not None and args.resume and not re.fullmatch(r"[0-9a-f]{8}", args.resume):
        args.prompt.insert(0, args.resume)
        args.resume = ""

    if args.list_sessions:
        return list_sessions()

    workspace = Workspace(
        args.cwd,
        confirm=False if args.web else args.confirm,
        allow_shell=not args.no_shell,
    )
    workspace.skills = (
        discover_skills(workspace.root, only_own=args.only_own_skills) if args.skills else {}
    )
    workspace.allow_skill_scripts = args.skills and not args.no_skill_scripts
    tools = tool_schemas(workspace)
    render_output = not args.plain and sys.stdout.isatty()
    client = ChatClient(
        args.base_url,
        args.api_key,
        args.model,
        timeout=args.timeout,
        temperature=args.temperature,
    )
    if args.web:
        if args.prompt or args.image:
            print("[web] prompt/image arguments are ignored in web mode", file=sys.stderr)
        if args.resume is not None:
            print("[web] -c is ignored in web mode", file=sys.stderr)
        return run_web(args.host, args.port, workspace, client, args.max_steps, args.thinking)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": build_system_prompt(workspace.root, workspace.skills)}
    ]
    session = None
    if args.resume is not None:
        resume_uuid = args.resume or latest_session_uuid()
        if resume_uuid is None:
            print("no saved sessions; starting a new session", file=sys.stderr)
        else:
            try:
                session = load_session(resume_uuid, messages)
            except SessionError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1
            print(f"resumed session: {os.path.basename(session.path)} ({len(messages) - 1} messages)")

    try:
        if args.prompt or args.image:
            task = " ".join(args.prompt)
            try:
                message = build_user_message(task, load_images(args.image))
            except ImageError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1
            if session is None:
                try:
                    session = create_session(args.model, workspace.root)
                except SessionError as exc:
                    print(f"error: {exc}", file=sys.stderr)
                    return 1
                print(f"session: {os.path.basename(session.path)} (continue with -c {session.uuid})")
            messages.append(message)
            return 0 if run_turn(
                client, workspace, messages, args.max_steps, render_output, args.thinking, session,
                tools=tools,
            ) else 1

        show_thinking = args.thinking
        pending_images: list[str] = []
        print(f"AI agent | model: {args.model} | workspace: {workspace.root}")
        print(f"confirm: {'on' if workspace.confirm else 'off'} | thinking: {'on' if show_thinking else 'off'} | skills: {'on' if args.skills else 'off'}")
        print_help()
        enable_utf8_input()
        guard = InterruptGuard()
        while True:
            try:
                user_input = read_user_line(_input_prompt("you> ", BOLD + CYAN)).strip()
            except EOFError:
                print()
                return 0
            except KeyboardInterrupt:
                if guard.pressed():
                    print()
                    return 130
                print("\n再按一次 Ctrl+C 退出", file=sys.stderr)
                continue
            guard.reset()
            if not user_input:
                continue
            if user_input in ("/exit", "/quit"):
                return 0
            if user_input == "/help":
                print_help()
                continue
            if (
                user_input in ("/skill", "/skills")
                or user_input.startswith("/skill ")
                or user_input.startswith("/skills ")
            ):
                _command, _, argument = user_input.partition(" ")
                argument = argument.strip()
                if not args.skills:
                    print("skills are disabled (use --skill to enable)")
                    continue
                if not argument:
                    if not workspace.skills:
                        print("no skills found")
                        continue
                    for skill in sorted(workspace.skills.values(), key=lambda item: item["name"]):
                        flags = []
                        if skill["has_scripts"]:
                            flags.append("scripts")
                        if skill["has_resources"]:
                            flags.append("resources")
                        suffix = f" [{', '.join(flags)}]" if flags else ""
                        print(f"- {skill['name']}: {skill['description']}{suffix}")
                    print("usage: /skill <name> <question>")
                    continue
                parts = argument.split(maxsplit=1)
                name = parts[0]
                question = parts[1].strip() if len(parts) > 1 else ""
                skill = workspace.skills.get(name)
                if skill is None:
                    print(f"unknown skill: {name}; use /skill to list")
                    continue
                flags = []
                if skill["has_scripts"]:
                    flags.append("scripts")
                if skill["has_resources"]:
                    flags.append("resources")
                suffix = f" [{', '.join(flags)}]" if flags else ""
                if not question:
                    print(f"- {name}: {skill['description']}{suffix}")
                    print(f"  {skill['path']}")
                    print(f"usage: /skill {name} <question>")
                    continue
                try:
                    content = workspace.load_skill(name)
                except ToolError as exc:
                    print(f"error: {exc}", file=sys.stderr)
                    continue
                user_input = (
                    f"[使用技能 {name}]\n\n{content}\n\n---\n\n"
                    f"技能说明已经在上面，直接按它执行，不要重复调用 load_skill。\n\n"
                    f"{question}"
                )
                # fall through to the normal send path below
            if user_input == "/reset":
                messages = messages[:1]
                pending_images = []
                session = None
                print("history cleared; the next message starts a new session")
                continue
            if user_input == "/compact" or user_input.startswith("/compact "):
                parts = user_input.split()
                if len(parts) > 2:
                    print("usage: /compact [N]   (N = user turns to keep, default 2)")
                    continue
                keep = COMPACT_DEFAULT_KEEP
                if len(parts) == 2:
                    try:
                        keep = int(parts[1])
                    except ValueError:
                        print("usage: /compact [N]   (N = user turns to keep, default 2)")
                        continue
                    if keep < 0:
                        print("usage: /compact [N]   (N must be >= 0)")
                        continue
                try:
                    stats = compact_history(
                        client,
                        messages,
                        session,
                        keep,
                        progress=lambda text: print(
                            _style(f"  {text}", DIM, supports_color()), file=sys.stderr, flush=True
                        ),
                    )
                except CompactError as exc:
                    print(str(exc))
                    continue
                except ChatError as exc:
                    print(f"error: {exc}", file=sys.stderr)
                    continue
                except SessionError as exc:
                    print(f"error: {exc}", file=sys.stderr)
                    continue
                print(
                    f"compacted: {stats['before_total']} -> {stats['after_messages']} messages "
                    f"(summarized {stats['before_messages']}, kept {stats['kept_messages']}), "
                    f"~{stats['before_tokens']} -> ~{stats['after_tokens']} tokens (estimated)"
                )
                if not stats["persisted"]:
                    print("warning: no session file; compaction is memory-only", file=sys.stderr)
                if stats["truncated"]:
                    print("warning: the summary may have been truncated by max_tokens", file=sys.stderr)
                if stats["usage"]:
                    usage = stats["usage"]
                    print(
                        "[usage] "
                        f"prompt={usage.get('prompt_tokens', 0)} "
                        f"completion={usage.get('completion_tokens', 0)} "
                        f"total={usage.get('total_tokens', 0)}",
                        file=sys.stderr,
                    )
                continue
            if user_input == "/image" or user_input.startswith("/image "):
                argument = user_input[len("/image"):].strip()
                if argument.lower() == "clear":
                    pending_images = []
                    print("pending images cleared")
                    continue
                if not argument:
                    names = ", ".join(os.path.basename(os.path.expanduser(path)) for path in pending_images)
                    print(f"pending images: {names or '(none)'}")
                    print(f"usage: /image <path> [path ...] | /image clear (max {MAX_IMAGES})")
                    continue
                try:
                    paths = shlex.split(argument)
                except ValueError as exc:
                    print(f"error: {exc}", file=sys.stderr)
                    continue
                for path in paths:
                    if len(pending_images) >= MAX_IMAGES:
                        print(f"error: at most {MAX_IMAGES} images", file=sys.stderr)
                        break
                    try:
                        name = check_image(path)
                    except ImageError as exc:
                        print(f"error: {exc}", file=sys.stderr)
                        continue
                    pending_images.append(path)
                    print(f"attached {name}")
                continue
            if user_input == "/save" or user_input.startswith("/save "):
                include_tools = False
                include_thinking = False
                path_parts: list[str] = []
                for word in user_input.split()[1:]:
                    if word == "--tool":
                        include_tools = True
                    elif word == "--thinking":
                        include_thinking = True
                    else:
                        path_parts.append(word)
                argument = " ".join(path_parts).strip()
                if not argument:
                    argument = time.strftime("chat-%Y%m%d-%H%M%S.md")
                turns = sum(1 for message in messages if message.get("role") in ("user", "assistant"))
                if not turns:
                    print("nothing to save yet")
                    continue
                try:
                    saved = workspace.save_text(
                        argument,
                        render_history(
                            messages, client.model, workspace.root, include_tools, include_thinking
                        ),
                    )
                except ToolError as exc:
                    print(f"error: {exc}", file=sys.stderr)
                    continue
                print(f"saved conversation to {saved}")
                continue
            if user_input == "/confirm" or user_input.startswith("/confirm "):
                parts = user_input.split()
                if len(parts) == 2 and parts[1].lower() in ("on", "off"):
                    workspace.confirm = parts[1].lower() == "on"
                elif len(parts) > 1:
                    print("usage: /confirm on|off")
                    continue
                print(f"confirmation is {'on' if workspace.confirm else 'off'}")
                continue
            if user_input == "/thinking" or user_input.startswith("/thinking "):
                parts = user_input.split()
                if len(parts) == 2 and parts[1].lower() in ("on", "off"):
                    show_thinking = parts[1].lower() == "on"
                elif len(parts) > 1:
                    print("usage: /thinking on|off")
                    continue
                print(f"thinking is {'on' if show_thinking else 'off'}")
                continue
            if user_input == "/model" or user_input.startswith("/model "):
                parts = user_input.split(maxsplit=1)
                argument = parts[1].strip() if len(parts) > 1 else ""
                if not argument:
                    print(f"current model: {client.model}")
                    print("use /model list to see available models, /model <name|number> to switch")
                    continue
                if argument.lower() == "list" or argument.isdigit():
                    try:
                        models = client.list_models()
                    except ChatError as exc:
                        print(f"error: {exc}", file=sys.stderr)
                        continue
                    if argument.isdigit():
                        index = int(argument)
                        if not 1 <= index <= len(models):
                            print(f"error: choose 1-{len(models)}; use /model list to see the list", file=sys.stderr)
                            continue
                        argument = models[index - 1]
                    else:
                        if not models:
                            print("(no models returned)")
                            continue
                        print("available models:")
                        for index, name in enumerate(models, 1):
                            marker = "  *" if name == client.model else ""
                            print(f"{index:3d}. {name}{marker}")
                        print("use /model <number> or /model <name> to switch")
                        continue
                client.model = argument
                print(f"model switched to: {client.model}")
                continue

            try:
                message = build_user_message(user_input, load_images(pending_images))
            except ImageError as exc:
                print(f"error: {exc}", file=sys.stderr)
                continue
            if session is None:
                try:
                    session = create_session(args.model, workspace.root)
                except SessionError as exc:
                    print(f"error: {exc}", file=sys.stderr)
                    continue
                print(f"session: {os.path.basename(session.path)} (continue with -c {session.uuid})")
            messages.append(message)
            pending_images = []
            try:
                run_turn(
                    client, workspace, messages, args.max_steps, render_output, show_thinking, session,
                    tools=tools,
                )
            except ChatError as exc:
                print(f"error: {exc}", file=sys.stderr)
                print("历史已保留，可以直接重试，或用 /exit 稍后用 -c 恢复", file=sys.stderr)
                continue
            except KeyboardInterrupt:
                repair_interrupted_turn(messages)
                if guard.pressed():
                    print()
                    return 130
                print("\n已中断当前任务；再按一次 Ctrl+C 退出", file=sys.stderr)
    except ChatError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())