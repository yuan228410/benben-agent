import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    tests = sorted(Path(__file__).parent.glob("test_*.py"))
    if not tests:
        print("no tests found")
        return 1
    isolated_config = os.path.join(
        tempfile.mkdtemp(prefix="benben-tests-"),
        "config.json",
    )
    env = dict(os.environ, BENBEN_CONFIG=isolated_config)
    failed = []
    for test in tests:
        print(f"\n=== {test.name} ===", flush=True)
        result = subprocess.run([sys.executable, str(test)], env=env)
        if result.returncode:
            failed.append(test.name)
    print()
    if failed:
        print("FAILED:", ", ".join(failed))
        return 1
    print(f"all {len(tests)} test files passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
