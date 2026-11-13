import csv
import io
import json
import os
import sys

import streamlit as st

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))
try:
    for _k in ("GEMINI_API_KEY", "GEMINI_MODEL"):
        if _k in st.secrets and _k not in os.environ:
            os.environ[_k] = st.secrets[_k]
except Exception:
    pass

from mapper.agent import SchemaMapperAgent
from mapper.infer_types import suggest_from_rows
from mapper.suggest import suggest_schema

st.set_page_config(page_title="Schema Mapper", layout="wide")

MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")

for k, v in {"run": None, "notice": None, "gen": 0, "schema_text": ""}.items():
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
    writer = csv.DictWriter(buf, fieldnames=fields)
    writer.writeheader()
    writer.writerows(records)
    return buf.getvalue()


def clear_results():
    st.session_state.run = None
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
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if api_key:
        st.success("Gemini key loaded from environment.")
    else:
        st.error("No Gemini key found. Add GEMINI_API_KEY to .env (local) or Secrets (deployed).")
    st.caption(f"Model: `{MODEL}`")

gen = st.session_state.gen

st.title("Schema Mapper")
st.write("Upload messy data and a target schema. The agent writes a transform, runs it in a sandbox, and validates every row.")

messy = st.file_uploader("1. Messy data file (CSV or JSON)", type=["csv", "json"], key=f"messy_{gen}")

if messy is not None:
    try:
        preview = read_rows(messy)
        with st.expander(f"Preview input: {len(preview)} rows"):
            st.dataframe(preview[:5], hide_index=True)
    except Exception as e:
        st.error(f"Cannot read this file: {e}")

st.subheader("2. Target schema")
mode = st.radio(
    "How to set the target schema",
    ["A. Infer from a clean example", "B. Draft with AI from the messy data", "C. I'll paste my own schema"],
    horizontal=True,
    key="mode",
    label_visibility="collapsed",
)

if mode.startswith("A."):
    example = st.file_uploader("One correct output file (CSV or JSON)", type=["csv", "json"], key=f"example_{gen}")
    st.button("Infer schema", on_click=infer_from_example, args=(example,), disabled=example is None)
elif mode.startswith("B."):
    if st.button("Draft schema with AI", disabled=messy is None or not api_key):
        try:
            with st.spinner("Asking the model for a draft schema..."):
                st.session_state.schema_text = json.dumps(suggest_schema(read_rows(messy)), indent=2)
            st.session_state.notice = ("success", "Draft ready. Review it, then click Map data.")
        except Exception as e:
            st.session_state.notice = ("error", f"Draft failed: {str(e)[:300]}")

st.subheader("3. Edit or paste the schema")
st.button("Format JSON", on_click=format_schema)
schema_text = st.text_area(
    "Target JSON Schema",
    key="schema_text",
    height=260,
    label_visibility="collapsed",
    placeholder='{"title": "...", "type": "object", "properties": {...}, "required": [...]}',
)

current_sig = (messy.name, messy.size, schema_text) if messy is not None else None
if st.session_state.run and st.session_state.run["sig"] != current_sig:
    clear_results()

ready = messy is not None and schema_text.strip() != ""
b1, b2 = st.columns([1, 1])
with b1:
    run_clicked = st.button("Map data", type="primary", disabled=not ready, use_container_width=True)
with b2:
    st.button("Clear all", on_click=clear_all, use_container_width=True)
if run_clicked:
    clear_results()
    try:
        if not api_key:
            raise ValueError("Enter your Gemini API key in the sidebar.")
        schema = json.loads(schema_text)
        if not isinstance(schema, dict) or "properties" not in schema:
            raise ValueError("This is data, not a schema. A schema needs a 'properties' key.")
        rows = read_rows(messy)
        with st.spinner("Generating transform, running it in the sandbox, validating every row..."):
            result = SchemaMapperAgent().map_data(rows, schema)
        st.session_state.run = {"result": result.model_dump(), "sig": (messy.name, messy.size, schema_text)}
    except Exception as e:
        st.session_state.notice = ("error", f"{type(e).__name__}: {str(e)[:300]}")

notice = st.session_state.notice
st.session_state.notice = None
if notice:
    kind, text = notice
    if kind == "success":
        st.success(text)
    else:
        st.error(text)

run = st.session_state.run
if run:
    r = run["result"]
    m = r["metrics"]
    st.divider()
    st.subheader("Results")
    a, b, c, d = st.columns(4)
    a.metric("Records", len(r["records"]))
    b.metric("Errors", len(r["errors"]))
    c.metric("Attempts", m["attempts"])
    d.metric("Latency", f"{m['latency_ms'] / 1000:.1f} s")
    st.caption(f"Tokens in/out: {m['input_tokens']}/{m['output_tokens']} · model {MODEL}")
    if r["records"]:
        st.dataframe(r["records"], hide_index=True)
        d1, d2 = st.columns(2)
        d1.download_button("Download JSON", json.dumps(r["records"], indent=2), "mapped.json", "application/json")
        d2.download_button("Download CSV", to_csv(r["records"]), "mapped.csv", "text/csv")
    if r["errors"]:
        st.warning(f"{len(r['errors'])} problem(s). Details below.")
        st.json(r["errors"])
    with st.expander("Generated transform code"):
        st.code(r["generated_code"], language="python")
