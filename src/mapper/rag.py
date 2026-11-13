import json
import math
import os
import re
from collections import Counter

from .redact import redact_data

_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
STORE = os.environ.get("RAG_STORE", os.path.join(_ROOT, "memory", "approved.jsonl"))
TOKEN = re.compile(r"[a-z0-9]+")
MIN_SIM = 0.2


def signature(rows):
    if not rows:
        return ""
    sample = redact_data(rows[:5])
    parts = []
    for col in rows[0].keys():
        parts.append(str(col))
        parts.extend(str(r.get(col, "")) for r in sample)
    return " ".join(parts)


def _tokens(text):
    return TOKEN.findall(text.lower())


def _load():
    if not os.path.exists(STORE):
        return []
    with open(STORE) as f:
        return [json.loads(line) for line in f if line.strip()]


def save(rows, schema, code):
    os.makedirs(os.path.dirname(STORE), exist_ok=True)
    with open(STORE, "a") as f:
        f.write(json.dumps({"signature": signature(rows), "schema": schema, "code": code}) + "\n")


def retrieve(rows, k=2):
    docs = _load()
    if not docs:
        return []
    doc_counts = [Counter(_tokens(d["signature"])) for d in docs]
    df = Counter()
    for c in doc_counts:
        df.update(c.keys())
    n = len(docs)

    def weigh(counts):
        return {w: tf * (math.log((n + 1) / (df[w] + 1)) + 1) for w, tf in counts.items() if df[w] > 0}

    q = weigh(Counter(_tokens(signature(rows))))
    qn = math.sqrt(sum(x * x for x in q.values())) or 1.0
    scored = []
    for d, c in zip(docs, doc_counts):
        v = weigh(c)
        vn = math.sqrt(sum(x * x for x in v.values())) or 1.0
        dot = sum(q[w] * v.get(w, 0.0) for w in q)
        scored.append((dot / (qn * vn), d))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [d for s, d in scored[:k] if s >= MIN_SIM]


def as_examples(hits):
    if not hits:
        return ""
    lines = ["Previously approved mappings for similar files. Reuse the same approach where it fits:"]
    for i, h in enumerate(hits, 1):
        lines.append(f"Example {i} target schema: {json.dumps(h['schema'])}")
        lines.append(f"Example {i} approved transform:\n{h['code']}")
    return "\n".join(lines)
