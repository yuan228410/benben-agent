import subprocess
import sys
from pathlib import Path


def main() -> int:
    tests = sorted(Path(__file__).parent.glob("test_*.py"))
    if not tests:
        print("no tests found")
        return 1
    failed = []
    for test in tests:
        print(f"\n=== {test.name} ===", flush=True)
        result = subprocess.run([sys.executable, str(test)])
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
