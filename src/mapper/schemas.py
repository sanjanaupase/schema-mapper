from typing import Any, Dict, List

from pydantic import BaseModel, Field


class Metrics(BaseModel):
    attempts: int
    latency_ms: float
    input_tokens: int
    output_tokens: int


class MappingResult(BaseModel):
    records: List[Dict[str, Any]]
    errors: List[Dict[str, Any]]
    generated_code: str
    metrics: Metrics


class CodeResponse(BaseModel):
    reasoning: str = Field(description="Brief reasoning for the mapping strategy.")
    python_code: str = Field(description="Python code defining transform(row: dict) -> dict using only the standard library.")