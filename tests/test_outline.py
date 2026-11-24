"""Tests for read_file_outline across the supported languages.

Covers: Python exact AST parsing, per-language declaration detection,
brace/string/comment handling, nesting depth, and the generic fallback.
No third-party libraries are used.
"""

import importlib.util
import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
AGENT_PATH = HERE.parent / "benben-agent.py"


def load_agent():
    spec = importlib.util.spec_from_file_location("benben_agent_outline", AGENT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


agent = load_agent()

results = []


def check(label, ok, detail=""):
    results.append((label, bool(ok), detail))
    print(("ok   " if ok else "FAIL ") + label + ((" -> " + detail) if detail and not ok else ""))


def lines_of(sample):
    return sample.splitlines()


def labels(items):
    return [label for _line, _depth, label in items]


def test_language_mapping():
    expected = {
        ".go": "go", ".rs": "rust", ".ts": "ts", ".tsx": "ts", ".js": "js",
        ".java": "java", ".kt": "kotlin", ".cs": "csharp", ".c": "c",
        ".cpp": "cpp", ".rb": "ruby", ".php": "php", ".swift": "swift",
        ".sh": "shell", ".lua": "lua", ".sql": "sql", ".dart": "dart",
    }
    for ext, language in expected.items():
        check(f"maps {ext} -> {language}", agent._outline_language("x" + ext) == language)


def test_python_ast():
    source = (
        "import os\n"
        "\n"
        "class Base:\n"
        "    pass\n"
        "\n"
        "class Child(Base):\n"
        "    def method(self, x=1):\n"
        "        def nested():\n"
        "            pass\n"
        "        return nested\n"
        "\n"
        "async def top():\n"
        "    pass\n"
    )
    items = agent._python_outline(source)
    found = labels(items)
    check("python finds classes", any("class Base" in item for item in found), str(found))
    check("python shows base class", any("class Child(Base)" in item for item in found), str(found))
    check("python finds methods", any("def method()" in item for item in found), str(found))
    check("python finds nested def", any("def nested()" in item for item in found), str(found))
    check("python marks async", any("async def top()" in item for item in found), str(found))
    depths = {label: depth for _line, depth, label in items}
    check(
        "python nesting depth for method",
        depths.get("def method()") == 1,
        str(depths),
    )
    check(
        "python nesting depth for nested def",
        depths.get("def nested()") == 2,
        str(depths),
    )


def test_go():
    sample = (
        "package main\n"
        "type S struct {\n"
        "    N int\n"
        "}\n"
        "func (s *S) Method() error {\n"
        "    return nil\n"
        "}\n"
        "func main() {}\n"
    )
    found = labels(agent._generic_outline(lines_of(sample), "go"))
    check("go finds type", any("type S struct" in item for item in found), str(found))
    check("go finds method", any("Method()" in item for item in found), str(found))
    check("go finds func", any("func main" in item for item in found), str(found))
    check("go ignores statements", not any(item.startswith("return") for item in found), str(found))


def test_rust():
    sample = (
        "pub struct Config { name: String }\n"
        "impl Config {\n"
        "    pub fn new(name: String) -> Self { Config { name } }\n"
        "    async fn load(&self) {\n"
        "        let s = \"{ not a brace }\";\n"
        "    }\n"
        "}\n"
        "fn free() {}\n"
    )
    items = agent._generic_outline(lines_of(sample), "rust")
    found = labels(items)
    check("rust finds struct", any("struct Config" in item for item in found), str(found))
    check("rust finds impl", any("impl Config" in item for item in found), str(found))
    check("rust finds fn", any("fn new" in item for item in found), str(found))
    check("rust finds async fn", any("async fn load" in item for item in found), str(found))
    check("rust finds free fn", any("fn free" in item for item in found), str(found))
    depths = {label: depth for _line, depth, label in items}
    new_depth = next((d for l, d in depths.items() if "fn new" in l), None)
    check("rust method nested under impl", new_depth == 1, str(depths))


def test_string_and_comment_safety():
    sample = (
        "// a comment with { brace\n"
        "class A {\n"
        "    String s = \"{ string brace }\";\n"
        "    void run() { }\n"
        "}\n"
    )
    items = agent._generic_outline(lines_of(sample), "java")
    found = labels(items)
    check("ignores braces in comments/strings", any("void run" in item for item in found), str(found))
    run_depth = next((d for _l, d, label in items if "void run" in label), None)
    check("string/comment braces do not corrupt depth", run_depth == 1, str(items))


def test_typescript():
    sample = (
        "// comment {\n"
        "export class Api {\n"
        "  private base: string = \"{x}\";\n"
        "  async fetch(url: string) {\n"
        "    return null;\n"
        "  }\n"
        "}\n"
        "export function helper(a: number) { return a; }\n"
        "export const arrow = async (x) => x;\n"
        "interface Shape { area(): number; }\n"
        "type Alias = string;\n"
    )
    found = labels(agent._generic_outline(lines_of(sample), "ts"))
    check("ts finds class", any("export class Api" in item for item in found), str(found))
    check("ts finds method", any("async fetch" in item for item in found), str(found))
    check("ts finds function", any("export function helper" in item for item in found), str(found))
    check("ts finds arrow const", any("export const arrow" in item for item in found), str(found))
    check("ts finds interface", any("interface Shape" in item for item in found), str(found))
    check("ts finds type alias", any("type Alias" in item for item in found), str(found))


def test_java():
    sample = (
        "package com.x;\n"
        "/* block {\n"
        "   still comment } */\n"
        "public class Main {\n"
        "    private int x;\n"
        "    public static void main(String[] args) { }\n"
        "}\n"
        "interface Repo { void save(); }\n"
    )
    found = labels(agent._generic_outline(lines_of(sample), "java"))
    check("java finds class", any("class Main" in item for item in found), str(found))
    check("java finds method", any("void main" in item for item in found), str(found))
    check("java finds interface", any("interface Repo" in item for item in found), str(found))
    check("java ignores field decls", not any(item.endswith("private int x;") for item in found), str(found))


def test_c_family():
    sample = (
        "#include <stdio.h>\n"
        "struct Point { int x, y; };\n"
        "static int add(int a, int b) {\n"
        "    return a + b;\n"
        "}\n"
        "int main(void) {\n"
        "    return 0;\n"
        "}\n"
    )
    found = labels(agent._generic_outline(lines_of(sample), "c"))
    check("c finds include", any("include" in item for item in found), str(found))
    check("c finds struct", any("struct Point" in item for item in found), str(found))
    check("c finds function", any("static int add" in item for item in found), str(found))
    check("c finds main", any("int main" in item for item in found), str(found))


def test_ruby():
    sample = (
        "# comment\n"
        "module Foo\n"
        "  class Bar\n"
        "    def self.build\n"
        '      new("x")\n'
        "    end\n"
        "  end\n"
        "end\n"
    )
    items = agent._generic_outline(lines_of(sample), "ruby")
    found = labels(items)
    check("ruby finds module", any("module Foo" in item for item in found), str(found))
    check("ruby finds class", any("class Bar" in item for item in found), str(found))
    check("ruby finds def", any("def self.build" in item for item in found), str(found))
    check("ruby ignores calls", not any(item.strip().startswith("new(") for item in found), str(found))
    depths = {label.strip(): depth for _l, depth, label in items}
    check("ruby indentation depth", depths.get("class Bar") == 1, str(depths))


def test_shell():
    sample = (
        "#!/bin/bash\n"
        "setup() {\n"
        '    echo "hi"\n'
        "}\n"
        "function cleanup {\n"
        "    rm -f /tmp/x\n"
        "}\n"
    )
    found = labels(agent._generic_outline(lines_of(sample), "shell"))
    check("shell finds posix function", any(item.startswith("setup()") for item in found), str(found))
    check("shell finds function keyword", any("function cleanup" in item for item in found), str(found))


def test_unknown_language_fallback():
    sample = (
        "class Thing {\n"
        "    def method(self):\n"
        "        pass\n"
        "}\n"
    )
    found = labels(agent._generic_outline(lines_of(sample), ""))
    check("unknown language still finds declarations", len(found) >= 2, str(found))


def test_outline_via_workspace():
    root = tempfile.mkdtemp()
    try:
        files = {
            "a.py": "class A:\n    def m(self):\n        pass\n",
            "a.go": "package main\nfunc main() {}\n",
            "notes.txt": "just some prose\nwith no declarations\n",
        }
        for name, content in files.items():
            with open(os.path.join(root, name), "w") as handle:
                handle.write(content)
        workspace = agent.Workspace(root=root)

        py = workspace.read_file_outline("a.py")
        check("workspace python uses ast", "parser=ast" in py, py)
        check("workspace python has header", "declarations" in py, py)

        go = workspace.read_file_outline("a.go")
        check("workspace go uses go parser", "parser=go" in go, go)

        prose = workspace.read_file_outline("notes.txt")
        check("prose file reports no declarations", "no declarations found" in prose, prose)

        try:
            workspace.read_file_outline("missing.py")
            check("missing file rejected", False, "no error raised")
        except agent.ToolError:
            check("missing file rejected", True)

        limited = workspace.read_file_outline("a.py", max_items=1)
        check("max_items truncates", "truncated" in limited, limited)
    finally:
        shutil.rmtree(root)


def test_broken_python_falls_back():
    root = tempfile.mkdtemp()
    try:
        with open(os.path.join(root, "bad.py"), "w") as handle:
            handle.write("class Broken(\n    pass\n")
        workspace = agent.Workspace(root=root)
        output = workspace.read_file_outline("bad.py")
        check("broken python does not raise", isinstance(output, str), output)
    finally:
        shutil.rmtree(root)


def test_schema_and_read_only():
    root = tempfile.mkdtemp()
    try:
        workspace = agent.Workspace(root=root)
        names = [item["function"]["name"] for item in agent.tool_schemas(workspace)]
        check("outline tool exposed", "read_file_outline" in names, str(names))
        check("outline counts as read-only", "read_file_outline" in agent.READ_ONLY_TOOL_NAMES)
    finally:
        shutil.rmtree(root)


test_language_mapping()
test_python_ast()
test_go()
test_rust()
test_string_and_comment_safety()
test_typescript()
test_java()
test_c_family()
test_ruby()
test_shell()
test_unknown_language_fallback()
test_outline_via_workspace()
test_broken_python_falls_back()
test_schema_and_read_only()

failures = [label for label, ok, _ in results if not ok]
print()
if failures:
    print(f"FAILURES: {len(failures)}")
    for label in failures:
        print("  -", label)
    sys.exit(1)
print("FAILURES: none")
