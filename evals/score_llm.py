import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mapper.suggest import suggest_schema
from score_suggest import load, score, name_hits

CASES = os.path.join(os.path.dirname(__file__), "cases")


def main():
    type_t = [0, 0]
    name_t = [0, 0]
    for folder in sorted(glob.glob(os.path.join(CASES, "case*"))):
        name = os.path.basename(folder)
        with open(os.path.join(folder, "schema.json")) as f:
            truth = json.load(f)
        rows, _ = load(folder)
        sug = suggest_schema(rows)
        h, t = score(sug, truth)
        nh = name_hits(sug, truth)
        type_t[0] += h
        type_t[1] += t
        name_t[0] += nh
        name_t[1] += t
        print(f"{name:<7} types={h}/{t}  names={nh}/{t}")
    print(f"\nllm_suggest type accuracy: {type_t[0]}/{type_t[1]} = {type_t[0] / type_t[1]:.0%}")
    print(f"llm_suggest name accuracy: {name_t[0]}/{name_t[1]} = {name_t[0] / name_t[1]:.0%}")


if __name__ == "__main__":
    main()
