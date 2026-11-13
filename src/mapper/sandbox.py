import abc
import json
import os
import subprocess
import sys
import tempfile
import resource
from typing import List, Dict, Tuple, Any

_UNSHARE_OK = None


def _unshare_works() -> bool:
    global _UNSHARE_OK
    if _UNSHARE_OK is None:
        try:
            r = subprocess.run(["unshare", "-n", "true"], capture_output=True, timeout=5)
            _UNSHARE_OK = r.returncode == 0
        except Exception:
            _UNSHARE_OK = False
    return _UNSHARE_OK


class BaseSandbox(abc.ABC):
    @abc.abstractmethod
    def run_transform(self, code: str, rows: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Execute transform(row) on rows. Returns (success_records, error_records)."""


class LocalSubprocessSandbox(BaseSandbox):
    """
    WARNING: not a security boundary. Runs generated code in a subprocess with
    memory/CPU limits, a cleared environment, and network isolation when
    `unshare -n` is available.
    """

    def __init__(self, timeout: int = 5):
        self.timeout = timeout
        self.network_isolated = _unshare_works()

    def run_transform(self, code: str, rows: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        with tempfile.TemporaryDirectory() as tmpdir:
            script_path = os.path.join(tmpdir, "transform_script.py")
            input_path = os.path.join(tmpdir, "input.json")
            output_path = os.path.join(tmpdir, "output.json")

            with open(input_path, "w") as f:
                json.dump(rows, f)

            runner = f"""
import json, traceback

{code}

def main():
    with open({json.dumps(input_path)}) as f:
        rows = json.load(f)
    results, errors = [], []
    for idx, row in enumerate(rows):
        try:
            results.append({{"index": idx, "row": transform(row)}})
        except Exception as e:
            errors.append({{"index": idx, "error": str(e), "traceback": traceback.format_exc()}})
    with open({json.dumps(output_path)}, "w") as f:
        json.dump({{"results": results, "errors": errors}}, f)

if __name__ == "__main__":
    main()
"""
            with open(script_path, "w") as f:
                f.write(runner)

            def limit_resources():
                resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
                resource.setrlimit(resource.RLIMIT_CPU, (10, 10))

            cmd = [sys.executable, script_path]
            if self.network_isolated:
                cmd = ["unshare", "-n"] + cmd

            try:
                res = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                    env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
                    preexec_fn=limit_resources,
                )
            except subprocess.TimeoutExpired:
                return [], [{"index": -1, "error": f"Execution timed out after {self.timeout} seconds"}]
            except Exception as e:
                return [], [{"index": -1, "error": f"Subprocess execution error: {e}"}]

            if res.returncode != 0 or not os.path.exists(output_path):
                return [], [{"index": -1, "error": f"Subprocess failed (code {res.returncode}): {res.stderr[-500:]}"}]

            with open(output_path) as f:
                data = json.load(f)
            return data["results"], data["errors"]