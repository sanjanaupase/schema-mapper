import json
import os
import re
import time
from typing import Any, Dict, List, Union

from google import genai
from google.genai import types
from jsonschema import Draft202012Validator
from pydantic import BaseModel

from .redact import redact_data
from .sandbox import BaseSandbox, LocalSubprocessSandbox
from .schemas import CodeResponse, Metrics, MappingResult

MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")


class SchemaMapperAgent:
    def __init__(self, sandbox: BaseSandbox = None, client: genai.Client = None):
        self.sandbox = sandbox or LocalSubprocessSandbox()
        self.client = client or genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

    def map_data(
        self,
        rows: List[Dict[str, Any]],
        target_schema: Union[type, Dict[str, Any]],
        max_attempts: int = 3,
    ) -> MappingResult:
        start = time.time()
        in_tok = out_tok = 0
        attempts = 0
        code = ""

        if isinstance(target_schema, type) and issubclass(target_schema, BaseModel):
            schema_desc = target_schema.model_json_schema()
            validator = target_schema
        else:
            schema_desc = target_schema
            validator = Draft202012Validator(target_schema)

        sample = redact_data(rows[:5])
        base_prompt = (
            "Write a Python function `transform(row: dict) -> dict` that maps input rows "
            "to the target schema. Use ONLY the standard library. "
            "Never invent values: if a target field has no matching source column, "
            "raise ValueError(\"missing source for <field>\") instead of returning a default like 0 or \"\".\n\n"
            f"Target schema:\n{json.dumps(schema_desc, indent=2)}\n\n"
            f"Sample input rows (redacted):\n{json.dumps(sample, indent=2)}"
        )

        feedback = ""
        for attempt in range(1, max_attempts + 1):
            attempts = attempt
            prompt = base_prompt
            if feedback:
                prompt += f"\n\nPrevious attempt failed. Fix the code.\n{feedback}"

            print(f"    [attempt {attempt}/{max_attempts}] calling {MODEL} ...", flush=True)
            response = self._call_gemini(prompt)
            usage = getattr(response, "usage_metadata", None)
            if usage:
                in_tok += getattr(usage, "prompt_token_count", 0) or 0
                out_tok += getattr(usage, "candidates_token_count", 0) or 0

            try:
                code = CodeResponse(**json.loads(response.text)).python_code
            except Exception as e:
                feedback = redact_data(f"Could not parse model output: {e}")
                continue

            print(f"    [attempt {attempt}] got code, running sandbox", flush=True)
            results, sandbox_errors = self.sandbox.run_transform(code, rows)
            if any(e.get("index") == -1 for e in sandbox_errors):
                feedback = redact_data(f"Sandbox error: {json.dumps(sandbox_errors)}")
                continue

            records, errors = self._validate(results, validator)
            errors = sandbox_errors + errors
            if not errors:
                return self._result(records, [], code, attempts, start, in_tok, out_tok)

            feedback = redact_data(
                f"Validation errors on attempt {attempt}:\n{json.dumps(errors[:10], indent=2)}"
            )

        records, errors = [], []
        if code:
            results, sandbox_errors = self.sandbox.run_transform(code, rows)
            records, errors = self._validate(results, validator)
            errors = sandbox_errors + errors
        else:
            errors = [{"index": -1, "error": "No valid code generated"}]
        return self._result(records, errors, code, attempts, start, in_tok, out_tok)

    @staticmethod
    def _validate(results, validator):
        records, errors = [], []
        for res in results:
            try:
                if isinstance(validator, Draft202012Validator):
                    validator.validate(res["row"])
                    records.append(res["row"])
                else:
                    records.append(validator(**res["row"]).model_dump())
            except Exception as e:
                errors.append({"index": res["index"], "error": redact_data(str(e))})
        return records, errors

    @staticmethod
    def _result(records, errors, code, attempts, start, in_tok, out_tok) -> MappingResult:
        return MappingResult(
            records=records,
            errors=errors,
            generated_code=code,
            metrics=Metrics(
                attempts=attempts,
                latency_ms=(time.time() - start) * 1000,
                input_tokens=in_tok,
                output_tokens=out_tok,
            ),
        )

    def _call_gemini(self, prompt: str):
        import random
        models = [MODEL, "gemini-3.5-flash", "gemini-2.5-flash-lite"]
        last_err = None
        for model in models:
            delay = 3.0
            for _ in range(6):
                try:
                    return self.client.models.generate_content(
                        model=model,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json",
                            response_schema=CodeResponse,
                            temperature=0.1,
                        ),
                    )
                except Exception as e:
                    last_err = e
                    msg = str(e).upper()
                    if "404" in msg or "NOT_FOUND" in msg:
                        break
                    transient = any(k in msg for k in ("429", "RESOURCE_EXHAUSTED", "503", "UNAVAILABLE", "500", "INTERNAL"))
                    if not transient:
                        raise
                    time.sleep(delay + random.uniform(0, 1))
                    delay = min(delay * 2, 60)
        raise last_err
