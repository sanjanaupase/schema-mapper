import json
import os
import random
import time
from typing import Any, Dict, List, TypedDict

from langchain_google_genai import ChatGoogleGenerativeAI
from jsonschema import Draft202012Validator
from langchain_core.prompts import PromptTemplate
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from .limits import check_and_count
from .redact import redact_data
from .sandbox import BaseSandbox, LocalSubprocessSandbox
from .schemas import CodeResponse, Metrics, MappingResult

MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
CHUNK_SIZE = int(os.environ.get("CHUNK_SIZE", "500"))

PROMPT = PromptTemplate.from_template(
    "Write a Python function `transform(row: dict) -> dict` that maps input rows to the target schema. "
    "Use ONLY the standard library. Follow every field description in the schema exactly, especially format conversions. Never invent values: if a target field has no matching source column, "
    "raise ValueError(\"missing source for <field>\") instead of returning a default like 0 or \"\".\n\n"
    "Target schema:\n{schema}\n\nSample input rows (redacted):\n{sample}\n\n{examples}"
)


class State(TypedDict, total=False):
    attempt: int
    code: Any
    feedback: str
    sandbox_results: List[Dict[str, Any]]
    sandbox_errors: List[Dict[str, Any]]
    records: List[Dict[str, Any]]
    errors: List[Dict[str, Any]]
    in_tok: int
    out_tok: int


def _check_row(row, validator, is_pydantic):
    if is_pydantic:
        return validator(**row).model_dump()
    validator.validate(row)
    return row


class _Reply:
    """Gives LangChain's structured output the .text and .usage_metadata shape the graph expects."""

    def __init__(self, parsed, raw):
        self.text = parsed.model_dump_json() if parsed is not None else ""
        meta = getattr(raw, "usage_metadata", None) or {}
        self.usage_metadata = type("Usage", (), {
            "prompt_token_count": int(meta.get("input_tokens", 0) or 0),
            "candidates_token_count": int(meta.get("output_tokens", 0) or 0),
        })()


class SchemaMapperAgent:
    def __init__(self, sandbox: BaseSandbox = None, client=None):
        self.sandbox = sandbox or LocalSubprocessSandbox()
        self.metered = client is None
        self.client = client

    def map_data(self, rows, target_schema, max_attempts=3, examples=""):
        start = time.time()
        is_pydantic = isinstance(target_schema, type) and issubclass(target_schema, BaseModel)
        if is_pydantic:
            schema_desc, validator = target_schema.model_json_schema(), target_schema
        else:
            schema_desc, validator = target_schema, Draft202012Validator(target_schema)
        base = PROMPT.format(
            schema=json.dumps(schema_desc, indent=2),
            sample=json.dumps(redact_data(rows[:5]), indent=2),
            examples=examples,
        )
        ctx = {"rows": rows, "base": base, "validator": validator, "is_pydantic": is_pydantic, "max": max_attempts}

        def decide(state):
            return "generate" if state.get("attempt", 0) < ctx["max"] else "finish"

        def generate(state):
            attempt = state.get("attempt", 0) + 1
            prompt = ctx["base"]
            if state.get("feedback"):
                prompt += f"\n\nPrevious attempt failed. Fix the code.\n{state['feedback']}"
            resp = self._call_gemini(prompt)
            in_tok, out_tok = state.get("in_tok", 0), state.get("out_tok", 0)
            usage = getattr(resp, "usage_metadata", None)
            if usage:
                in_tok += getattr(usage, "prompt_token_count", 0) or 0
                out_tok += getattr(usage, "candidates_token_count", 0) or 0
            try:
                code = CodeResponse(**json.loads(resp.text)).python_code
            except Exception as e:
                return {"attempt": attempt, "code": None, "in_tok": in_tok, "out_tok": out_tok,
                        "feedback": redact_data(f"Could not parse model output: {e}"),
                        "records": [], "errors": [{"index": -1, "error": "Model output could not be parsed"}]}
            return {"attempt": attempt, "code": code, "in_tok": in_tok, "out_tok": out_tok, "feedback": ""}

        def execute(state):
            code = state["code"]
            results, errs = [], []
            for offset in range(0, len(ctx["rows"]), CHUNK_SIZE):
                chunk = ctx["rows"][offset:offset + CHUNK_SIZE]
                r, e = self.sandbox.run_transform(code, chunk)
                for x in r:
                    x["index"] += offset
                for x in e:
                    if x.get("index", -1) != -1:
                        x["index"] += offset
                results += r
                errs += e
                if any(x.get("index") == -1 for x in e):
                    break
            if any(x.get("index") == -1 for x in errs):
                return {"sandbox_results": [], "sandbox_errors": errs, "records": [], "errors": errs,
                        "feedback": redact_data(f"Sandbox error: {json.dumps(errs)}")}
            return {"sandbox_results": results, "sandbox_errors": errs}

        def validate(state):
            records, errors = [], list(state.get("sandbox_errors", []))
            for res in state.get("sandbox_results", []):
                try:
                    records.append(_check_row(res["row"], ctx["validator"], ctx["is_pydantic"]))
                except Exception as e:
                    errors.append({"index": res["index"], "error": redact_data(str(e))})
            update = {"records": records, "errors": errors}
            if errors:
                update["feedback"] = redact_data(
                    f"Validation or execution errors on attempt {state.get('attempt')}:\n"
                    f"{json.dumps(errors[:10], indent=2)}")
            return update

        def finish(state):
            return {}

        def after_generate(state):
            return "execute" if state.get("code") else decide(state)

        def after_execute(state):
            if any(e.get("index") == -1 for e in state.get("sandbox_errors", [])):
                return decide(state)
            return "validate"

        def after_validate(state):
            return decide(state) if state.get("errors") else END

        g = StateGraph(State)
        g.add_node("generate", generate)
        g.add_node("execute", execute)
        g.add_node("validate", validate)
        g.add_node("finish", finish)
        g.add_edge(START, "generate")
        g.add_conditional_edges("generate", after_generate,
                                {"execute": "execute", "generate": "generate", "finish": "finish"})
        g.add_conditional_edges("execute", after_execute,
                                {"validate": "validate", "generate": "generate", "finish": "finish"})
        g.add_conditional_edges("validate", after_validate,
                                {"generate": "generate", "finish": "finish", END: END})
        g.add_edge("finish", END)

        final = g.compile().invoke({"attempt": 0, "feedback": "", "in_tok": 0, "out_tok": 0,
                                    "records": [], "errors": [], "code": None})

        code = final.get("code") or ""
        records = final.get("records") or []
        errors = final.get("errors") or []
        if not code:
            records, errors = [], [{"index": -1, "error": "No valid code generated"}]
        return MappingResult(
            records=records,
            errors=errors,
            generated_code=code,
            metrics=Metrics(
                attempts=final.get("attempt", 0),
                latency_ms=(time.time() - start) * 1000,
                input_tokens=final.get("in_tok", 0),
                output_tokens=final.get("out_tok", 0),
            ),
        )

    def _call_gemini(self, prompt: str):
        models = list(dict.fromkeys([MODEL, "gemini-3.5-flash-lite", "gemini-2.5-flash-lite"]))
        last = None
        for model in models:
            delay = 3.0
            for _ in range(6):
                if self.metered:
                    check_and_count()
                try:
                    llm = self.client or ChatGoogleGenerativeAI(
                        model=model,
                        google_api_key=os.environ.get("GEMINI_API_KEY"),
                        max_retries=0,
                    )
                    out = llm.with_structured_output(CodeResponse, include_raw=True).invoke(prompt)
                    return _Reply(out.get("parsed"), out.get("raw"))
                except Exception as e:
                    last = e
                    msg = str(e).upper()
                    if "404" in msg or "NOT_FOUND" in msg:
                        break
                    if not any(k in msg for k in ("429", "RESOURCE_EXHAUSTED", "503", "UNAVAILABLE", "500", "INTERNAL")):
                        raise
                    time.sleep(delay + random.uniform(0, 1))
                    delay = min(delay * 2, 60)
        raise last
