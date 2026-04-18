# SEPL eval fixtures

One JSON file per learnable prompt, named `<prompt_name>.json`. Each file is an
array of fixture objects with at least an `input` field and optionally a
`reference_verdict` for deterministic scoring:

```json
[
  {
    "input": "Ticker AAPL. Short interest 4.2%, RSI 58, volume 1.3× 30d avg.",
    "reference_verdict": "BUY"
  }
]
```

Rules:

* Each file should contain **5–20** representative ambiguous cases drawn from
  real historical analyses (redact ticker names if operating publicly).
* `reference_verdict` must match one of the target prompt's schema enum values
  (e.g. `STRONG BUY`, `BUY`, `NEUTRAL`, `SELL`, `STRONG SELL`) — see
  `backend/resources/prompts/<name>.yaml`.
* Fixtures are consumed by `SEPL.evaluate()` in `backend/sepl.py`. A candidate
  body must score strictly better than the active body by
  `SEPL_MIN_MARGIN` (default 5 percentage points) to be committed.
* If a fixture file is missing, SEPL will **refuse to commit** any candidate
  for that prompt — the `Evaluate` operator returns a zero-margin result.

Files are loaded at `SEPL` construction time via `fixtures_dir`, so adding new
fixtures takes effect on the next cycle.
