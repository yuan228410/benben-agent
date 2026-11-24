"""Tests for multi_edit: ordering, atomicity, CRLF handling and validation."""

import importlib.util
import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
AGENT_PATH = HERE.parent / "benben-agent.py"


def load_agent():
    spec = importlib.util.spec_from_file_location("benben_agent_multi_edit", AGENT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


agent = load_agent()

results = []


def check(label, ok, detail=""):
    results.append((label, bool(ok), detail))
    print(("ok   " if ok else "FAIL ") + label + ((" -> " + detail) if detail and not ok else ""))


class Fixture:
    def __init__(self, content, newline=""):
        self.root = tempfile.mkdtemp()
        self.path = os.path.join(self.root, "target.txt")
        with open(self.path, "w", encoding="utf-8", newline=newline) as handle:
            handle.write(content)
        self.workspace = agent.Workspace(root=self.root)

    def read(self):
        with open(self.path, "r", encoding="utf-8", newline="") as handle:
            return handle.read()

    def close(self):
        shutil.rmtree(self.root)


def basic_order_case():
    fixture = Fixture("alpha\nbeta\ngamma\n")
    try:
        result = fixture.workspace.multi_edit(
            "target.txt",
            [
                {"old_string": "alpha", "new_string": "ALPHA"},
                {"old_string": "gamma", "new_string": "GAMMA"},
            ],
        )
        content = fixture.read()
        check("applies all edits", content == "ALPHA\nbeta\nGAMMA\n", repr(content))
        check("reports edit count", "2 edits" in result, result)
    finally:
        fixture.close()


def sequential_case():
    """The second edit must see the first edit's output."""
    fixture = Fixture("start middle end\n")
    try:
        fixture.workspace.multi_edit(
            "target.txt",
            [
                {"old_string": "middle", "new_string": "MIDDLE"},
                {"old_string": "start MIDDLE", "new_string": "START MIDDLE"},
            ],
        )
        content = fixture.read()
        check("edits chain on prior result", content == "START MIDDLE end\n", repr(content))
    finally:
        fixture.close()


def atomicity_case():
    """A failing edit must leave the file byte-identical."""
    fixture = Fixture("keep this\n")
    original = fixture.read()
    try:
        try:
            fixture.workspace.multi_edit(
                "target.txt",
                [
                    {"old_string": "keep", "new_string": "KEPT"},
                    {"old_string": "absent", "new_string": "nope"},
                ],
            )
            check("atomicity aborts the batch", False, "no error raised")
        except agent.ToolError as exc:
            check("atomicity aborts the batch", True, str(exc))
            check("atomicity names the failing edit", "edit 2" in str(exc), str(exc))
        check("atomicity leaves file untouched", fixture.read() == original, repr(fixture.read()))
    finally:
        fixture.close()


def ambiguity_case():
    fixture = Fixture("dup\ndup\n")
    try:
        try:
            fixture.workspace.multi_edit(
                "target.txt",
                [{"old_string": "dup", "new_string": "one"}],
            )
            check("ambiguous match rejected", False, "no error raised")
        except agent.ToolError as exc:
            check("ambiguous match rejected", "2 times" in str(exc), str(exc))
        check("ambiguity leaves file untouched", fixture.read() == "dup\ndup\n")
    finally:
        fixture.close()


def replace_all_case():
    fixture = Fixture("dup\ndup\n")
    try:
        fixture.workspace.multi_edit(
            "target.txt",
            [{"old_string": "dup", "new_string": "one", "replace_all": True}],
        )
        check("replace_all edits every occurrence", fixture.read() == "one\none\n", repr(fixture.read()))
    finally:
        fixture.close()


def crlf_case():
    fixture = Fixture("line one\r\nline two\r\n", newline="")
    try:
        fixture.workspace.multi_edit(
            "target.txt",
            [{"old_string": "line one\nline two", "new_string": "replaced\nlines"}],
        )
        content = fixture.read()
        check("crlf edit applied", "replaced" in content, repr(content))
        check("crlf preserved in output", "replaced\r\nlines" in content, repr(content))
        check(
            "crlf has no mixed endings",
            "\n" not in content.replace("\r\n", ""),
            repr(content),
        )
    finally:
        fixture.close()


def validation_case():
    fixture = Fixture("content\n")
    try:
        for label, edits, expect in (
            ("empty edits rejected", [], "non-empty"),
            ("non-list edits rejected", "nope", "non-empty"),
            ("empty old_string rejected", [{"old_string": "", "new_string": "x"}], "old_string"),
            ("non-string new_string rejected", [{"old_string": "content", "new_string": 5}], "new_string"),
            ("non-object edit rejected", ["nope"], "object"),
        ):
            try:
                fixture.workspace.multi_edit("target.txt", edits)
                check(label, False, "no error raised")
            except agent.ToolError as exc:
                check(label, expect in str(exc), str(exc))

        try:
            fixture.workspace.multi_edit("missing.txt", [{"old_string": "a", "new_string": "b"}])
            check("missing file rejected", False, "no error raised")
        except agent.ToolError:
            check("missing file rejected", True)
    finally:
        fixture.close()


def single_approval_case():
    """A batch must prompt once, not once per edit."""
    calls = []
    fixture = Fixture("a\nb\nc\n")
    try:
        fixture.workspace.approval_handler = lambda action, detail: calls.append(action) or True
        fixture.workspace.confirm = True
        fixture.workspace.multi_edit(
            "target.txt",
            [
                {"old_string": "a", "new_string": "A"},
                {"old_string": "b", "new_string": "B"},
                {"old_string": "c", "new_string": "C"},
            ],
        )
        check("single approval for whole batch", calls == ["multi_edit"], str(calls))
    finally:
        fixture.close()


def denial_case():
    fixture = Fixture("original\n")
    try:
        fixture.workspace.approval_handler = lambda action, detail: False
        fixture.workspace.confirm = True
        try:
            fixture.workspace.multi_edit(
                "target.txt", [{"old_string": "original", "new_string": "changed"}]
            )
            check("denied batch writes nothing", False, "no error raised")
        except agent.ToolError as exc:
            check("denied batch writes nothing", "permission denied" in str(exc), str(exc))
        check("denied batch leaves file untouched", fixture.read() == "original\n")
    finally:
        fixture.close()


def edit_file_regression_case():
    """edit_file must keep working through the shared helper."""
    fixture = Fixture("hello world\n")
    try:
        fixture.workspace.edit_file("target.txt", "world", "there")
        check("edit_file still works", fixture.read() == "hello there\n", repr(fixture.read()))
        try:
            fixture.workspace.edit_file("target.txt", "absent", "x")
            check("edit_file reports missing snippet", False, "no error raised")
        except agent.ToolError:
            check("edit_file reports missing snippet", True)
    finally:
        fixture.close()


def schema_case():
    root = tempfile.mkdtemp()
    try:
        workspace = agent.Workspace(root=root)
        names = [item["function"]["name"] for item in agent.tool_schemas(workspace)]
        check("multi_edit exposed", "multi_edit" in names, str(names))
        schemas = {item["function"]["name"]: item for item in agent.tool_schemas(workspace)}
        required = schemas["multi_edit"]["function"]["parameters"]["required"]
        check("schema requires edits only", required == ["edits"], str(required))
        item_props = (
            schemas["multi_edit"]["function"]["parameters"]["properties"]["edits"]["items"]["properties"]
        )
        check("edit item accepts path", "path" in item_props, str(item_props))
    finally:
        shutil.rmtree(root)


def cross_file_case():
    """One call must be able to edit several files atomically."""
    root = tempfile.mkdtemp()
    try:
        names = ["a.txt", "b.txt", "c.txt"]
        for name in names:
            with open(os.path.join(root, name), "w", encoding="utf-8") as handle:
                handle.write(f"{name} old value\n")
        workspace = agent.Workspace(root=root)
        result = workspace.multi_edit(
            edits=[
                {"path": "a.txt", "old_string": "old value", "new_string": "NEW A"},
                {"path": "b.txt", "old_string": "old value", "new_string": "NEW B"},
                {"path": "c.txt", "old_string": "old value", "new_string": "NEW C"},
            ]
        )
        contents = {}
        for name in names:
            with open(os.path.join(root, name), encoding="utf-8") as handle:
                contents[name] = handle.read()
        check("cross-file edits all applied", contents["a.txt"].startswith("a.txt NEW A"), str(contents))
        check("cross-file edits b", contents["b.txt"].startswith("b.txt NEW B"), str(contents))
        check("cross-file edits c", contents["c.txt"].startswith("c.txt NEW C"), str(contents))
        check("cross-file reports file count", "3 files" in result, result)
    finally:
        shutil.rmtree(root)


def cross_file_atomicity_case():
    """A failure in the last file must leave every file untouched."""
    root = tempfile.mkdtemp()
    try:
        origins = {}
        for name in ["a.txt", "b.txt"]:
            text = f"{name} original\n"
            origins[name] = text
            with open(os.path.join(root, name), "w", encoding="utf-8") as handle:
                handle.write(text)
        workspace = agent.Workspace(root=root)
        try:
            workspace.multi_edit(
                edits=[
                    {"path": "a.txt", "old_string": "original", "new_string": "CHANGED"},
                    {"path": "b.txt", "old_string": "absent-token", "new_string": "x"},
                ]
            )
            check("cross-file atomicity aborts", False, "no error raised")
        except agent.ToolError as exc:
            check("cross-file atomicity aborts", True, str(exc))
            check("cross-file names the file", "b.txt" in str(exc), str(exc))
        for name, expected in origins.items():
            with open(os.path.join(root, name), encoding="utf-8") as handle:
                check(f"cross-file untouched {name}", handle.read() == expected)
    finally:
        shutil.rmtree(root)


def default_path_case():
    """The tool-level path must still work as a fallback."""
    root = tempfile.mkdtemp()
    try:
        path = os.path.join(root, "solo.txt")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("alpha beta\n")
        workspace = agent.Workspace(root=root)
        workspace.multi_edit(
            path="solo.txt",
            edits=[
                {"old_string": "alpha", "new_string": "ALPHA"},
                {"old_string": "beta", "new_string": "BETA"},
            ],
        )
        with open(path, encoding="utf-8") as handle:
            check("default path fallback works", handle.read() == "ALPHA BETA\n")
    finally:
        shutil.rmtree(root)


def missing_path_case():
    root = tempfile.mkdtemp()
    try:
        with open(os.path.join(root, "x.txt"), "w", encoding="utf-8") as handle:
            handle.write("data\n")
        workspace = agent.Workspace(root=root)
        try:
            workspace.multi_edit(
                edits=[{"old_string": "data", "new_string": "y"}]
            )
            check("edit without any path rejected", False, "no error raised")
        except agent.ToolError as exc:
            check("edit without any path rejected", "path" in str(exc), str(exc))
    finally:
        shutil.rmtree(root)


def read_only_case():
    root = tempfile.mkdtemp()
    try:
        workspace = agent.Workspace(root=root)
        workspace.read_only = True
        names = [item["function"]["name"] for item in agent.tool_schemas(workspace)]
        check("multi_edit hidden in read-only mode", "multi_edit" not in names, str(names))
    finally:
        shutil.rmtree(root)


basic_order_case()
sequential_case()
atomicity_case()
ambiguity_case()
replace_all_case()
crlf_case()
validation_case()
single_approval_case()
denial_case()
edit_file_regression_case()
schema_case()
cross_file_case()
cross_file_atomicity_case()
default_path_case()
missing_path_case()
read_only_case()

failures = [label for label, ok, _ in results if not ok]
print()
if failures:
    print(f"FAILURES: {len(failures)}")
    for label in failures:
        print("  -", label)
    sys.exit(1)
print("FAILURES: none")
