import csv
import glob
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mapper.infer_types import suggest_from_rows
from mapper.header_suggest import suggest_from_headers
from mapper.profile_suggest import suggest_from_profile

CASES = os.path.join(os.path.dirname(__file__), "cases")


def load(folder):
    csv_path = os.path.join(folder, "input.csv")
    if os.path.exists(csv_path):
        with open(csv_path, newline="") as f:
            reader = csv.DictReader(f)
            return list(reader), list(reader.fieldnames or [])
    with open(os.path.join(folder, "input.json")) as f:
        rows = json.load(f)
    return rows, list(rows[0].keys()) if rows else []


def base_type(t):
    if isinstance(t, list):
        non_null = [x for x in t if x != "null"]
        return non_null[0] if non_null else "null"
    return t


def score(suggested, truth):
    s_props = suggested["properties"]
    t_props = truth["properties"]
    hits = sum(
        1 for k, v in t_props.items()
        if k in s_props and base_type(s_props[k]["type"]) == base_type(v["type"])
    )
    return hits, len(t_props)


def name_hits(suggested, truth):
    return sum(1 for k in truth["properties"] if k in suggested["properties"])


def main():
    methods = {
        "infer_types": lambda rows, headers: suggest_from_rows(rows),
        "header_names": lambda rows, headers: suggest_from_headers(headers),
        "profile": lambda rows, headers: suggest_from_profile(rows),
    }
    totals = {m: [0, 0] for m in methods}
    for folder in sorted(glob.glob(os.path.join(CASES, "case*"))):
        name = os.path.basename(folder)
        with open(os.path.join(folder, "schema.json")) as f:
            truth = json.load(f)
        rows, headers = load(folder)
        line = [f"{name:<7}"]
        for m, fn in methods.items():
            sug = fn(rows, headers)
            h, t = score(sug, truth)
            totals.setdefault(m + "_names", [0, 0])
            totals[m + "_names"][0] += name_hits(sug, truth)
            totals[m + "_names"][1] += t
            totals[m][0] += h
            totals[m][1] += t
            line.append(f"{m}={h}/{t}")
        print("  ".join(line))
    print()
    for m, (h, t) in totals.items():
        print(f"{m:<13} field-type accuracy: {h}/{t} = {h / t:.0%}")


if __name__ == "__main__":
    main()
