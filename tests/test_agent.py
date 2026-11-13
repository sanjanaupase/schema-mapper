from unittest.mock import MagicMock

from pydantic import BaseModel

from mapper.agent import SchemaMapperAgent
from mapper.redact import redact_text
from mapper.sandbox import LocalSubprocessSandbox
from mapper.schemas import CodeResponse


class TargetUser(BaseModel):
    full_name: str
    age: int


GOOD = "def transform(row):\n    return {'full_name': row['name'], 'age': int(row['age'])}"


def fake_llm(*replies):
    llm = MagicMock()
    llm.with_structured_output.return_value.invoke.side_effect = list(replies)
    return llm


def reply(code):
    return {"parsed": CodeResponse(reasoning="test", python_code=code), "raw": None}


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
    bad = "def transform(row):\n    return {'wrong': row['name']}"
    agent = SchemaMapperAgent(client=fake_llm(reply(bad), reply(GOOD)))
    result = agent.map_data([{"name": "Alice", "age": "30"}], TargetUser)
    assert result.metrics.attempts == 2
    assert result.records == [{"full_name": "Alice", "age": 30}]
    assert result.errors == []


def test_graph_recovers_from_unparseable_model_output():
    agent = SchemaMapperAgent(client=fake_llm({"parsed": None, "raw": None}, reply(GOOD)))
    result = agent.map_data([{"name": "Alice", "age": "30"}], TargetUser)
    assert result.metrics.attempts == 2
    assert result.records == [{"full_name": "Alice", "age": 30}]


def test_graph_recovers_from_sandbox_syntax_error():
    broken = "def transform(row)\n    return row"
    agent = SchemaMapperAgent(client=fake_llm(reply(broken), reply(GOOD)))
    result = agent.map_data([{"name": "Alice", "age": "30"}], TargetUser)
    assert result.metrics.attempts == 2
    assert result.records == [{"full_name": "Alice", "age": 30}]
