import csv
import io
import json
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from .agent import SchemaMapperAgent
from .schemas import MappingResult

app = FastAPI(title="schema-mapper API", version="1.0.0")


class MapJsonRequest(BaseModel):
    rows: List[Dict[str, Any]]
    target_schema_json: Dict[str, Any]


@app.post("/map", response_model=MappingResult)
async def map_file(
    file: UploadFile = File(...),
    target_schema_json: str = Form(...),
):
    try:
        target_schema = json.loads(target_schema_json)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid target_schema_json: {e}")

    content = (await file.read()).decode("utf-8")
    name = (file.filename or "").lower()
    if name.endswith(".csv"):
        rows = list(csv.DictReader(io.StringIO(content)))
    elif name.endswith(".json"):
        loaded = json.loads(content)
        rows = [loaded] if isinstance(loaded, dict) else loaded
    else:
        raise HTTPException(status_code=400, detail="Unsupported file type. Use .csv or .json")

    return SchemaMapperAgent().map_data(rows, target_schema)


@app.post("/map_json", response_model=MappingResult)
async def map_json(payload: MapJsonRequest):
    return SchemaMapperAgent().map_data(payload.rows, payload.target_schema_json)