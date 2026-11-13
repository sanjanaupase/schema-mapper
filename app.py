import csv
import io
import json
import os
import sys

import streamlit as st

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))
try:
    for _k in ("GEMINI_API_KEY", "GEMINI_MODEL", "CRM_URL", "DAILY_CALL_LIMIT"):
        if _k in st.secrets and _k not in os.environ:
            os.environ[_k] = str(st.secrets[_k])
except Exception:
    pass

from mapper import crm, limits, provenance, rag  # noqa: E402
from mapper.agent import SchemaMapperAgent  # noqa: E402
from mapper.infer_types import suggest_from_rows  # noqa: E402
from mapper.suggest import suggest_schema  # noqa: E402

st.set_page_config(page_title="Schema Mapper", layout="wide")
MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")

for k, v in {"run": None, "batch": None, "notice": None, "gen": 0, "schema_text": ""}.items():
    st.session_state.setdefault(k, v)


def read_rows(file):
    text = file.getvalue().decode("utf-8")
    if file.name.lower().endswith(".csv"):
        return list(csv.DictReader(io.StringIO(text)))
    data = json.loads(text)
    return [data] if isinstance(data, dict) else data


def to_csv(records):
    fields = list(dict.fromkeys(k for r in records for k in r))
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields)
    w.writeheader()
    w.writerows(records)
    return buf.getvalue()


def parse_schema(text):
    schema = json.loads(text)
    if not isinstance(schema, dict) or "properties" not in schema:
        raise ValueError("This is data, not a schema. A schema needs a 'properties' key.")
    return schema


def run_mapping(file, schema):
    rows = read_rows(file)
    hits = rag.retrieve(rows)
    result = SchemaMapperAgent().map_data(rows, schema, examples=rag.as_examples(hits)).model_dump()
    prov = provenance.check_provenance(rows, result["records"])
    return {"result": result, "rows": rows, "prov": prov, "retrieved": len(hits)}


def clear_results():
    st.session_state.run = None
    st.session_state.batch = None
    st.session_state.notice = None


def clear_all():
    st.session_state.gen += 1
    st.session_state.schema_text = ""
    clear_results()


def format_schema():
    try:
        st.session_state.schema_text = json.dumps(json.loads(st.session_state.schema_text), indent=2)
        st.session_state.notice = ("success", "Schema formatted.")
    except json.JSONDecodeError as e:
        st.session_state.notice = ("error", f"Not valid JSON, cannot format: {e}")


def infer_from_example(example):
    try:
        st.session_state.schema_text = json.dumps(suggest_from_rows(read_rows(example)), indent=2)
        st.session_state.notice = ("success", "Schema inferred from the example. Review it, then click Map data.")
    except Exception as e:
        st.session_state.notice = ("error", f"Inference failed: {str(e)[:300]}")


with st.sidebar:
    st.header("Settings")
    if os.environ.get("GEMINI_API_KEY"):
        st.success("Gemini key loaded from environment.")
    else:
        st.error("No Gemini key. Add GEMINI_API_KEY to .env (local) or Secrets (deployed).")
    st.caption(f"Model: `{MODEL}`")
    left = limits.remaining()
    st.caption("Model calls left today: " + ("unlimited" if left is None else str(left)))
    st.caption("CRM: " + ("real endpoint" if os.environ.get("CRM_URL") else "mock with simulated 429s"))

gen = st.session_state.gen
st.title("Schema Mapper")
st.write("Upload messy data and a target schema. The agent writes a transform, runs it in a sandbox, validates every row, and flags values not found in the source.")

files = st.file_uploader("1. Messy data files (CSV or JSON, one or many)", type=["csv", "json"],
                         accept_multiple_files=True, key=f"messy_{gen}") or []
messy = files[0] if len(files) == 1 else None
if len(files) == 1:
    try:
        preview = read_rows(files[0])
        with st.expander(f"Preview input: {len(preview)} rows"):
            st.dataframe(preview[:5], hide_index=True)
    except Exception as e:
        st.error(f"Cannot read this file: {e}")
elif len(files) > 1:
    st.info(f"{len(files)} files selected. Use Run batch to map them all with the same schema.")

st.subheader("2. Target schema")
mode = st.radio(
    "How to set the target schema",
    ["A. Infer from a clean example", "B. Draft with AI from the messy data", "C. I'll paste my own schema"],
    horizontal=True, key="mode", label_visibility="collapsed",
)
if mode.startswith("A."):
    example = st.file_uploader("One correct output file (CSV or JSON)", type=["csv", "json"], key=f"example_{gen}")
    st.button("Infer schema", on_click=infer_from_example, args=(example,), disabled=example is None)
elif mode.startswith("B."):
    if st.button("Draft schema with AI", disabled=not files or not os.environ.get("GEMINI_API_KEY")):
        try:
            limits.check_and_count()
            with st.spinner("Asking the model for a draft schema..."):
                st.session_state.schema_text = json.dumps(suggest_schema(read_rows(files[0])), indent=2)
            st.session_state.notice = ("success", "Draft ready. Review it, then click Map data.")
        except Exception as e:
            st.session_state.notice = ("error", f"Draft failed: {str(e)[:300]}")

st.subheader("3. Edit or paste the schema")
st.button("Format JSON", on_click=format_schema)
schema_text = st.text_area(
    "Target JSON Schema", key="schema_text", height=260, label_visibility="collapsed",
    placeholder='{"title": "...", "type": "object", "properties": {...}, "required": [...]}',
)

current_sig = (messy.name, messy.size, schema_text) if messy is not None else None
if st.session_state.run and st.session_state.run["sig"] != current_sig:
    st.session_state.run = None

has_schema = schema_text.strip() != ""
b1, b2, b3 = st.columns(3)
with b1:
    run_clicked = st.button("Map data", type="primary", disabled=not (messy is not None and has_schema))
with b2:
    batch_clicked = st.button(f"Run batch ({len(files)} files)", disabled=not (len(files) > 1 and has_schema))
with b3:
    st.button("Clear all", on_click=clear_all)

if run_clicked:
    st.session_state.run = None
    try:
        if not os.environ.get("GEMINI_API_KEY"):
            raise ValueError("No Gemini key configured.")
        schema = parse_schema(schema_text)
        with st.spinner("Generating transform, running it in the sandbox, validating every row..."):
            out = run_mapping(messy, schema)
        out.update({"schema": schema, "sig": (messy.name, messy.size, schema_text), "push": None})
        st.session_state.run = out
    except Exception as e:
        st.session_state.notice = ("error", f"{type(e).__name__}: {str(e)[:300]}")

if batch_clicked:
    st.session_state.batch = None
    try:
        if not os.environ.get("GEMINI_API_KEY"):
            raise ValueError("No Gemini key configured.")
        schema = parse_schema(schema_text)
        summary, combined = [], []
        progress = st.progress(0.0)
        for i, f in enumerate(files):
            try:
                with st.spinner(f"Mapping {f.name} ({i + 1} of {len(files)})..."):
                    out = run_mapping(f, schema)
                r = out["result"]
                summary.append({"file": f.name, "records": len(r["records"]), "errors": len(r["errors"]),
                                "attempts": r["metrics"]["attempts"],
                                "seconds": round(r["metrics"]["latency_ms"] / 1000, 1),
                                "status": "ok" if not r["errors"] else "review"})
                combined += [dict(rec, source_file=f.name) for rec in r["records"]]
            except Exception as e:
                summary.append({"file": f.name, "records": 0, "errors": 1, "attempts": 0,
                                "seconds": 0, "status": f"failed: {str(e)[:80]}"})
            progress.progress((i + 1) / len(files))
        st.session_state.batch = {"summary": summary, "records": combined}
    except Exception as e:
        st.session_state.notice = ("error", f"{type(e).__name__}: {str(e)[:300]}")

notice = st.session_state.notice
st.session_state.notice = None
if notice:
    kind, text = notice
    st.success(text) if kind == "success" else st.error(text)

batch = st.session_state.batch
if batch:
    st.divider()
    st.subheader("Batch results")
    st.dataframe(batch["summary"], hide_index=True)
    if batch["records"]:
        d1, d2 = st.columns(2)
        d1.download_button("Download combined JSON", json.dumps(batch["records"], indent=2), "batch.json", "application/json")
        d2.download_button("Download combined CSV", to_csv(batch["records"]), "batch.csv", "text/csv")

run = st.session_state.run
if run:
    r, m, p = run["result"], run["result"]["metrics"], run["prov"]
    st.divider()
    st.subheader("Results")
    a, b, c, d = st.columns(4)
    a.metric("Records", len(r["records"]))
    b.metric("Errors", len(r["errors"]))
    c.metric("Attempts", m["attempts"])
    d.metric("Latency", f"{m['latency_ms'] / 1000:.1f} s")
    st.caption(f"Tokens in/out {m['input_tokens']}/{m['output_tokens']} · approved examples reused: {run['retrieved']}")

    totals = {"copied": 0, "normalized": 0, "unsupported": 0, "null": 0}
    for f in p["fields"].values():
        for k in totals:
            totals[k] += f[k]
    found = totals["copied"] + totals["normalized"]
    checked = found + totals["unsupported"]
    if checked:
        st.write(f"**Provenance:** {found} of {checked} values found in the source data ({found / checked:.0%}).")
    if totals["unsupported"]:
        st.warning(f"{totals['unsupported']} value(s) are not in the source data. They may be derived "
                   "(for example a unit conversion) or invented. Review them before use.")
    if p["fields"]:
        st.dataframe([{"field": k, **v} for k, v in p["fields"].items()], hide_index=True)
    if p["issues"]:
        with st.expander(f"Values not found in source ({len(p['issues'])} shown)"):
            st.json(p["issues"])

    if r["records"]:
        st.subheader("Mapped records")
        st.dataframe(r["records"], hide_index=True)
        d1, d2 = st.columns(2)
        d1.download_button("Download JSON", json.dumps(r["records"], indent=2), "mapped.json", "application/json")
        d2.download_button("Download CSV", to_csv(r["records"]), "mapped.csv", "text/csv")
        if st.button("Save as approved mapping (reused for similar files)", disabled=bool(r["errors"])):
            rag.save(run["rows"], run["schema"], r["generated_code"])
            st.success("Saved. Similar future files will reuse this transform as an example.")

        st.subheader("Send to CRM")
        if st.button(f"Push {len(r['records'])} records"):
            sender = crm.http_sender(os.environ["CRM_URL"]) if os.environ.get("CRM_URL") else crm.mock_sender(0.3)
            with st.spinner("Pushing with retries..."):
                run["push"] = crm.push(r["records"], sender)
        if run.get("push"):
            o = run["push"]
            st.success(f"Sent {o['sent']} · retried {o['retried']} · dead-letter {len(o['dead_letter'])}")
            if o["dead_letter"]:
                st.json(o["dead_letter"][:20])

    if r["errors"]:
        st.warning(f"{len(r['errors'])} problem(s). Details below.")
        st.json(r["errors"])
    with st.expander("Generated transform code"):
        st.code(r["generated_code"], language="python")
