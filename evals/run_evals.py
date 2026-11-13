import csv
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mapper.agent import SchemaMapperAgent  # noqa: E402

CASES_DIR = os.path.join(os.path.dirname(__file__), "cases")


def load_case(folder):
    with open(os.path.join(folder, "schema.json")) as f:
        schema = json.load(f)
    csv_path = os.path.join(folder, "input.csv")
    if os.path.exists(csv_path):
        with open(csv_path, newline="") as f:
            rows = list(csv.DictReader(f))
    else:
        with open(os.path.join(folder, "input.json")) as f:
            rows = json.load(f)
    return rows, schema


def _match(records, expected):
    if len(records) != len(expected):
        return False
    for rec, exp in zip(records, expected):
        for key, val in exp.items():
            got = rec.get(key)
            if val is None:
                if got not in (None, ""):
                    return False
            elif isinstance(val, (int, float)) and not isinstance(val, bool):
                try:
                    if abs(float(got) - float(val)) > 0.01:
                        return False
                except (TypeError, ValueError):
                    return False
            elif got != val:
                return False
    return True


def main():
    print(f"Model: {os.environ.get('GEMINI_MODEL', 'gemini-3.8-flash')}")
    folders = sorted(
        os.path.join(CASES_DIR, d)
        for d in os.listdir(CASES_DIR)
        if os.path.isdir(os.path.join(CASES_DIR, d))
    )
    print(f"{'case':<8}{'mode':<10}{'attempts':<10}{'mapped':<8}{'errors':<8}{'latency_ms':<12}{'result'}")

    totals = {"baseline": 0, "agent": 0}
    for folder in folders:
        name = os.path.basename(folder)
        rows, schema = load_case(folder)
        for mode, max_attempts in (("baseline", 1), ("agent", 3)):
          for _ in range(int(os.environ.get("RUNS", "1"))):
            result = SchemaMapperAgent().map_data(rows, schema, max_attempts=max_attempts)
            expected_path = os.path.join(folder, "expected.json")
            with open(expected_path) as f:
                expected = json.load(f)
            ok = not result.errors and _match(result.records, expected)
            totals[mode] += ok
            m = result.metrics
            print(
                f"{name:<8}{mode:<10}{m.attempts:<10}{len(result.records):<8}"
                f"{len(result.errors):<8}{m.latency_ms:<12.0f}{'PASS' if ok else 'FAIL'}"
            )
            if result.errors:
                print(f"  first error: {result.errors[0].get('error', '')[:200]}")
            if not ok:
                print(f"  got:      {json.dumps(result.records, ensure_ascii=False)[:300]}")
                print(f"  expected: {json.dumps(expected, ensure_ascii=False)[:300]}")

    total = len(folders) * int(os.environ.get("RUNS", "1"))
    print(f"\nBaseline (1 attempt): {totals['baseline']}/{total}")
    print(f"Agent (3 attempts):   {totals['agent']}/{total}")


if __name__ == "__main__":
    main()
