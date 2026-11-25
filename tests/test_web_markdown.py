"""检查 Web 端 Markdown 渲染器对常见语法的处理。

重点是“行内语法跨行内代码边界”这类曾经出错的情况：`renderInline` 早期实现
先按反引号把文本切成几段再分别处理，导致 **a `b` c** 这种强调里含行内代码的
写法永远配不上对，星号会原样漏到页面上。
"""
from pathlib import Path
import json
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "benben-agent.py"
failures = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else f" {detail}"))
    if not cond:
        failures.append(name)


node = shutil.which("node")
if node is None:
    print("skip web markdown runtime (node not found)")
    print()
    print("FAILURES: none")
    raise SystemExit(0)

text = AGENT.read_text(encoding="utf-8")
scripts = re.findall(r"<script>\n(.*?)\n</script>", text, re.S)
check("single inline script", len(scripts) == 1, len(scripts))

# 只抽 renderMarkdown 及其依赖（escapeHtml / safeImage / renderInline 等），
# 避免为了拿一个纯函数去桩整个页面。
js = scripts[-1]
wanted = [
    "escapeHtml",
    "safeImage",
    "splitInlineCode",
    "renderInline",
    "tableCells",
    "isTableSeparator",
    "renderQuoteBody",
    "renderMarkdown",
]
constants = re.findall(r"^const INLINE_CODE_\w+ = .*$", js, re.M)

blocks = []
for name in wanted:
    match = re.search(
        r"^function %s\(.*?\n^\}" % re.escape(name), js, re.S | re.M
    )
    if match is None:
        print(f"FAIL cannot extract {name} from page script")
        failures.append(f"extract {name}")
        continue
    blocks.append(match.group(0))

check("extracted all markdown helpers", len(blocks) == len(wanted), len(blocks))

CASES = [
    # (名称, 输入, 期望包含的片段, 期望不包含的片段)
    ("heading", "## 标题", ["<h2>标题</h2>"], []),
    ("bold", "**粗体**", ["<strong>粗体</strong>"], ["**"]),
    ("italic", "*斜体*", ["<em>斜体</em>"], []),
    ("bold italic", "***又粗又斜***", ["<strong><em>又粗又斜</em></strong>"], []),
    ("strikethrough", "~~删掉~~", ["<del>删掉</del>"], []),
    ("inline code", "用 `grep -n` 搜", ["<code>grep -n</code>"], []),
    ("unordered list", "- 一\n- 二", ["<ul>", "<li>一</li>"], ["- 一"]),
    ("ordered list", "1. 一\n2. 二", ["<ol>", "<li>一</li>"], []),
    ("blockquote", "> 引用", ["<blockquote>引用</blockquote>"], []),
    ("hr", "---", ["<hr>"], []),
    ("link", "[例子](https://example.com)", ['<a href="https://example.com"'], ["["]),
    ("image", "![图](https://example.com/a.png)", ["<img", "alt=\"图\""], []),
    ("code block", "```py\nprint(1)\n```", ["<pre><code", "lang-py"], []),
    ("table", "| A | B |\n| --- | --- |\n| 1 | 2 |",
     ["<table>", "<th", "<td"], ["| A |"]),
    ("html escaped", "<script>x</script>", ["&lt;script&gt;"], ["<script>"]),
    # 下面这些是本次修复的回归点
    ("bold containing code", "**数据会被 POST 到 `server` 渲染**",
     ["<strong>", "<code>server</code>", "</strong>"], ["**"]),
    ("italic containing code", "*斜体含 `code` 文字*",
     ["<em>", "<code>code</code>", "</em>"], []),
    ("strike containing code", "~~删掉 `code` 这段~~",
     ["<del>", "<code>code</code>", "</del>"], ["~~"]),
    ("bold italic containing code", "***又粗又斜 `x` 结尾***",
     ["<strong><em>"], ["***"]),
    ("link label is code", "[`antvis/GPT-Vis`](https://github.com/a/b)",
     ["<a href=", "<code>antvis/GPT-Vis</code>", "</a>"], ["[`"]),
    ("code adjacent to bold", "`a` **粗体** `b`",
     ["<code>a</code>", "<strong>粗体</strong>", "<code>b</code>"], []),
    ("quote with list", "> - 第一项\n> - 第二项",
     ["<blockquote>", "<ul>", "<li>第一项</li>", "</ul>", "</blockquote>"],
     ["&gt;", "- 第一项"]),
    ("quote with ordered list", "> 1. 一\n> 2. 二",
     ["<blockquote>", "<ol>", "<li>一</li>"], ["1. 一"]),
    ("quote plain text unchanged", "> 纯文本引用",
     ["<blockquote>纯文本引用</blockquote>"], ["<ul>"]),
    ("quote with inline syntax", "> 含 **粗体** 和 `代码`",
     ["<blockquote>", "<strong>粗体</strong>", "<code>代码</code>"], []),
]

runner = tempfile.mkdtemp(prefix="web-md-")
runner_path = str(Path(runner) / "run.js")
Path(runner_path).write_text(
    "\n\n".join(constants + blocks)
    + "\nconst CASES = " + json.dumps(CASES, ensure_ascii=False) + ";\n"
    + """
const out = [];
for (const [name, input, want, mustNot] of CASES) {
  let html;
  try {
    html = renderMarkdown(input);
  } catch (err) {
    out.push({ name, error: String(err) });
    continue;
  }
  const missing = want.filter((w) => !html.includes(w));
  const leaked = mustNot.filter((m) => html.includes(m));
  out.push({ name, html, missing, leaked });
}
console.log(JSON.stringify(out));
""",
    encoding="utf-8",
)

result = subprocess.run(
    [node, runner_path],
    capture_output=True,
    text=True,
    encoding="utf-8",
    errors="replace",
    timeout=60,
)
if result.returncode != 0:
    check("markdown runner executes", False, (result.stdout + result.stderr)[-1500:])
else:
    for row in json.loads(result.stdout):
        if row.get("error"):
            check(row["name"], False, row["error"])
        elif row["missing"]:
            check(row["name"], False, f"missing={row['missing']} html={row['html'][:200]}")
        elif row["leaked"]:
            check(row["name"], False, f"leaked={row['leaked']} html={row['html'][:200]}")
        else:
            check(row["name"], True)

# 占位符方案的一个前提：页面里不能出现裸露的 NUL 字符，否则会退化成只渲染行内代码
check("no raw NUL in page script", "\x00" not in js)

print()
if failures:
    print("FAILURES: %s" % ", ".join(failures))
    raise SystemExit(1)
print("all markdown checks passed")
