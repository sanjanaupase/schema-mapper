import re
from datetime import datetime
from typing import Any, Dict, List, Tuple

BOOL_WORDS = {"true", "false", "yes", "no", "y", "n"}
DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%b %d %Y", "%Y/%m/%d"]
SYMBOLS = re.compile(r"[€$£¥₹\s]")


def to_num(value: Any) -> float:
    s = SYMBOLS.sub("", str(value).strip())
    if s.count(",") == 1 and "." not in s and len(s.split(",")[1]) <= 2:
        s = s.replace(",", ".")
    elif "," in s and "." in s and s.rfind(".") < s.rfind(","):
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", "")
    return float(s)


def is_number(value: Any) -> bool:
    try:
        to_num(value)
        return True
    except ValueError:
        return False


def is_date(value: str) -> bool:
    for fmt in DATE_FORMATS:
        try:
            datetime.strptime(value.strip(), fmt)
            return True
        except ValueError:
            continue
    return False


def infer_column(values: List[Any]) -> Tuple[str, str]:
    present = [str(v).strip() for v in values if v is not None and str(v).strip() != ""]
    if not present:
        return "string", "no values in sample"
    if all(v.lower() in BOOL_WORDS for v in present):
        return "boolean", "true/false, yes/no, y/n normalized to boolean"
    if all(is_number(v) for v in present):
        return "number", "numeric; accepts integers and decimals, strips currency symbols"
    if all(is_date(v) for v in present):
        return "string", "date; normalize to ISO 8601 (YYYY-MM-DD)"
    return "string", "text; trim whitespace"


def suggest_from_rows(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not rows:
        return {"title": "InferredSchema", "type": "object", "properties": {}, "required": []}
    props, required = {}, []
    for col in rows[0].keys():
        values = [r.get(col) for r in rows]
        t, desc = infer_column(values)
        props[col] = {"type": t, "description": desc}
        if all(v is not None and str(v).strip() != "" for v in values):
            required.append(col)
    return {"title": "InferredSchema", "type": "object", "properties": props, "required": required}
