import re

from .infer_types import BOOL_WORDS, to_num

_NUM = re.compile(r"\d[\d.,]*")


def _norm(v):
    return re.sub(r"[\s,$€£¥₹]", "", str(v).strip().lower())


def _numbers(s):
    out = []
    for tok in _NUM.findall(str(s)):
        try:
            out.append(round(to_num(tok), 2))
        except ValueError:
            pass
    return out


def _status(value, exact, norm, nums, bool_seen):
    if value is None or value == "":
        return "null"
    if isinstance(value, bool):
        return "normalized" if bool_seen else "unsupported"
    if isinstance(value, (int, float)):
        return "normalized" if round(float(value), 2) in nums else "unsupported"
    s = str(value).strip()
    if s in exact:
        return "copied"
    if _norm(s) in norm:
        return "normalized"
    return "unsupported"


def check_provenance(source_rows, records, max_rows=2000):
    cols = list(source_rows[0].keys()) if source_rows else []
    cells = [r.get(c) for r in source_rows[:max_rows] for c in cols if r.get(c) not in (None, "")]
    exact = {str(c).strip() for c in cells}
    norm = {_norm(c) for c in cells}
    nums = {n for c in cells for n in _numbers(c)}
    bool_seen = any(str(c).strip().lower() in BOOL_WORDS for c in cells)
    fields, issues = {}, []
    for i, rec in enumerate(records):
        for field, value in rec.items():
            status = _status(value, exact, norm, nums, bool_seen)
            fields.setdefault(field, {"copied": 0, "normalized": 0, "unsupported": 0, "null": 0})[status] += 1
            if status == "unsupported" and len(issues) < 50:
                issues.append({"record": i, "field": field, "value": value})
    return {"fields": fields, "issues": issues}
