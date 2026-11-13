from collections import Counter
from typing import Any, Dict, List, Tuple

from .suggest import suggest_schema


def consensus_schema(rows: List[Dict[str, Any]], runs: int = 3, client=None) -> Tuple[Dict[str, Any], List[str]]:
    drafts = [suggest_schema(rows, client=client) for _ in range(runs)]
    majority = runs // 2 + 1
    votes: Counter = Counter()
    type_votes: Dict[str, Counter] = {}
    first_desc: Dict[str, str] = {}
    for d in drafts:
        for name, spec in d["properties"].items():
            votes[name] += 1
            type_votes.setdefault(name, Counter())[spec["type"]] += 1
            first_desc.setdefault(name, spec.get("description", ""))
    props, required, review = {}, [], []
    for name, count in votes.items():
        if count < majority:
            review.append(name)
            continue
        t = type_votes[name].most_common(1)[0][0]
        props[name] = {"type": t, "description": first_desc[name]}
        if count == runs and all(name in d["required"] for d in drafts):
            required.append(name)
    schema = {"title": drafts[0]["title"], "type": "object", "properties": props, "required": required}
    return schema, review
