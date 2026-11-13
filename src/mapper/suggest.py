import json
import os
import time
from typing import Any, Dict, List

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from .redact import redact_data

MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
ALLOWED_TYPES = {"string", "integer", "number", "boolean"}


class SchemaField(BaseModel):
    name: str = Field(description="Output field name in snake_case.")
    type: str = Field(description="One of: string, integer, number, boolean.")
    required: bool = Field(description="True only if every input row has a usable value.")
    description: str = Field(description="How to derive this field from the input.")


class SuggestedSchema(BaseModel):
    title: str = Field(description="Short name for the record type, e.g. TargetProduct.")
    fields: List[SchemaField]


def to_json_schema(s: SuggestedSchema) -> Dict[str, Any]:
    props, required = {}, []
    for f in s.fields:
        if f.type not in ALLOWED_TYPES:
            raise ValueError(f"unsupported type for {f.name}: {f.type}")
        props[f.name] = {"type": f.type, "description": f.description}
        if f.required:
            required.append(f.name)
    return {"title": s.title, "type": "object", "properties": props, "required": required}


def suggest_schema(rows: List[Dict[str, Any]], client: genai.Client = None) -> Dict[str, Any]:
    client = client or genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
    sample = redact_data(rows[:5])
    prompt = (
        "Propose a clean target JSON Schema for these messy input rows. "
        "Use snake_case names, the most specific type, mark a field required only if every row has it, "
        "and describe how each field is derived from the input.\n\n"
        f"Sample input rows (redacted):\n{json.dumps(sample, indent=2)}"
    )
    delay = 3.0
    for attempt in range(6):
        try:
            response = client.models.generate_content(
                model=MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=SuggestedSchema,
                    temperature=0.1,
                ),
            )
            break
        except Exception as e:
            msg = str(e).upper()
            if attempt == 5 or not any(k in msg for k in ("429", "RESOURCE_EXHAUSTED", "503", "UNAVAILABLE")):
                raise
            time.sleep(delay)
            delay = min(delay * 2, 60)
    return to_json_schema(SuggestedSchema(**json.loads(response.text)))
