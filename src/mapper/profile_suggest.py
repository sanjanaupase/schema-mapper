from collections import Counter
from typing import Any, Dict, List

from .infer_types import BOOL_WORDS, is_number, to_num


def suggest_from_profile(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(rows)
    if n == 0:
        return {"title": "ProfiledSchema", "type": "object", "properties": {}, "required": []}
    props, required = {}, []
    for col in rows[0].keys():
        values = [r.get(col) for r in rows]
        present = [str(v).strip() for v in values if v is not None and str(v).strip() != ""]
        null_rate = 1 - len(present) / n
        distinct = Counter(present)
        spec: Dict[str, Any] = {"type": "string"}
        if present and all(v.lower() in BOOL_WORDS for v in present):
            spec = {"type": "boolean"}
        elif present and all(is_number(v) for v in present):
            nums = [to_num(v) for v in present]
            spec = {
                "type": "number",
                "minimum": min(nums),
                "maximum": max(nums),
            }
        elif n >= 10 and 1 < len(distinct) <= max(3, n // 5):
            spec["enum"] = sorted(distinct)
        spec["description"] = f"profiled: null_rate={null_rate:.0%}, distinct={len(distinct)}"
        props[col] = spec
        if null_rate == 0:
            required.append(col)
    return {"title": "ProfiledSchema", "type": "object", "properties": props, "required": required}
