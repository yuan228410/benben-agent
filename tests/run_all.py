import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    tests = sorted(Path(__file__).parent.glob("test_*.py"))
    if not tests:
        print("no tests found")
        return 1
    # Every test runs against a throwaway home directory. The agent stores
    # sessions, job logs, skills and skill backups under ~/.benben-agent, so
    # without this the test-suite mixes its fixtures into the real profile
    # and leaves hundreds of files behind.
    sandbox = tempfile.mkdtemp(prefix="benben-tests-")
    env = dict(
        os.environ,
        HOME=sandbox,
        BENBEN_CONFIG=os.path.join(sandbox, "config.json"),
    )
    failed = []
    for test in tests:
        print(f"\n=== {test.name} ===", flush=True)
        result = subprocess.run([sys.executable, str(test)], env=env)
        if result.returncode:
            failed.append(test.name)
    print()
    if failed:
        print("FAILED:", ", ".join(failed))
        print(f"sandbox kept for inspection: {sandbox}")
        return 1
    shutil.rmtree(sandbox, ignore_errors=True)
    print(f"all {len(tests)} test files passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
