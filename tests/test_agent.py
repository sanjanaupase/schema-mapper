import json
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

from mapper.agent import SchemaMapperAgent
from mapper.redact import redact_text
from mapper.sandbox import LocalSubprocessSandbox


class TargetUser(BaseModel):
    full_name: str
    age: int


def fake_response(code: str):
    resp = MagicMock()
    resp.text = json.dumps({"reasoning": "test", "python_code": code})
    resp.usage_metadata.prompt_token_count = 10
    resp.usage_metadata.candidates_token_count = 5
    return resp


def test_redaction_removes_email_phone_and_key():
    text = "mail user@example.com phone 555-123-4567 key AQ.Abcdefghijklmnopqrstuvwxyz123456"
    out = redact_text(text)
    assert "user@example.com" not in out and "[REDACTED_EMAIL]" in out
    assert "555-123-4567" not in out and "[REDACTED_PHONE]" in out
    assert "AQ.Abcdef" not in out and "[REDACTED_API_KEY]" in out


def test_sandbox_timeout_enforced():
    sandbox = LocalSubprocessSandbox(timeout=1)
    code = "def transform(row):\n    while True:\n        pass\n"
    _, errors = sandbox.run_transform(code, [{"a": 1}])
    assert any("timed out" in e["error"] for e in errors)


def test_retry_loop_recovers_after_validation_error():
    client = MagicMock()
    bad = "def transform(row):\n    return {'wrong': row['name']}"
    good = "def transform(row):\n    return {'full_name': row['name'], 'age': int(row['age'])}"
    client.models.generate_content.side_effect = [fake_response(bad), fake_response(good)]

    agent = SchemaMapperAgent(client=client)
    result = agent.map_data([{"name": "Alice", "age": "30"}], TargetUser)

    assert result.metrics.attempts == 2
    assert result.records == [{"full_name": "Alice", "age": 30}]
    assert result.errors == []