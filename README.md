# Schema Mapper

Turns messy client CSV or JSON into a target schema. Every row is validated, and every value is checked against the source data.

**Live demo:** https://ai-schema-mapper.streamlit.app (free tier: the first load can take about 30 seconds)

## Quick start

Requires Python 3.12 or newer.

```
git clone https://github.com/sanjanaupase/schema-mapper.git
cd schema-mapper
bash run.sh
```

Then open http://localhost:8501.

## Try it

1. Upload `evals/cases/case6/input.json`.
2. Choose **C. Paste your schema** and paste the contents of `evals/cases/case6/schema.json`.
3. Click **Map data**.

Expected: 3 records, 0 errors, and weights of 2500, 2500, and 500.

## How it works

1. An LLM writes one `transform(row)` function from the schema and a few sample rows.
2. The function runs in a sandbox on every row.
3. Each output row is validated against the schema.
4. Each output value is checked against the source data.
5. A missing source field raises an error. Nothing is filled with `0` or an empty string.

Details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

## Results

Model: `gemini-3.5-flash-lite`, 9 hand-labeled cases.

| Run | First attempt | With retries |
|---|---|---|
| All 9 cases | 9/9 | 9/9 |
| 6 cases x 5 runs | 29/30 | 30/30 |

Retries are a safety net. They fired in 1 of 210 runs.

To reproduce:

```
DAILY_CALL_LIMIT=0 RUNS=1 python evals/run_evals.py
```

## Limitations

- The sandbox is not a security boundary. Production use needs a container or an isolated service such as E2B.
- Provenance checks that a value exists in the source, not which column it came from.
- Batches run one file at a time.

## Tests

```
pytest -q
```

Expected: `5 passed`. No model access or quota is needed.

## License

MIT
