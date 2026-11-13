import re
from typing import Any, Dict, List

TYPE_RULES = [
    (r"(^|_)(price|cost|amount|total|salary|weight|fee|rate)($|_)", "number"),
    (r"(^|_)(qty|quantity|count|age|year|num)($|_)", "integer"),
    (r"^(is|has|in|can)_|(^|_)(active|enabled|flag)($|_)", "boolean"),
]


def snake_case(header: str) -> str:
    s = re.sub(r"[^0-9a-zA-Z]+", "_", header.strip()).strip("_").lower()
    return s or "field"


def guess_type(name: str) -> str:
    for pattern, t in TYPE_RULES:
        if re.search(pattern, name):
            return t
    return "string"


def suggest_from_headers(headers: List[str]) -> Dict[str, Any]:
    props = {}
    for h in headers:
        name = snake_case(h)
        props[name] = {"type": guess_type(name), "description": f"derived from input column '{h}'"}
    return {"title": "HeaderSchema", "type": "object", "properties": props, "required": list(props)}
