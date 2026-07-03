# Antigravity `--output-format json` cheatsheet

`agy -p "<prompt>" --output-format json` emits a **single JSON object** (not JSONL) on stdout at
completion. Verified on agy 1.0.16.

## Result envelope

```json
{
  "conversation_id": "c0d91872-52f3-4ff8-bc71-965b7a264c66",
  "status": "SUCCESS",
  "response": "OK\n",
  "duration_seconds": 1.24537189,
  "num_turns": 1,
  "usage": {
    "input_tokens": 16795,
    "output_tokens": 6,
    "thinking_tokens": 0,
    "total_tokens": 16801
  }
}
```

| Field | Type | Notes |
|-------|------|-------|
| `conversation_id` | string (UUID) | stable across resume; the resume token value |
| `status` | string | `SUCCESS` observed; failure shape unconfirmed |
| `response` | string | the assistant's answer text (may end with `\n`) |
| `duration_seconds` | number | wall-clock |
| `num_turns` | number | increments across a resumed conversation |
| `usage.input_tokens` | number | large even for tiny prompts (system prompt + context) |
| `usage.output_tokens` | number | |
| `usage.thinking_tokens` | number | can be > 0 |
| `usage.total_tokens` | number | sum |

**No `total_cost_usd`** — tokens only.

## Resume

Reusing a captured `conversation_id`:

```
agy -p "<follow-up>" --conversation c0d91872-... --output-format json
→ {"conversation_id":"c0d91872-...","status":"SUCCESS","response":"...","num_turns":2,"usage":{...}}
```

`num_turns` increments and prior context is restored.

## Notes

- Output is a single physical line (newlines inside `response` are `\n`-escaped). The base line
  reader caps at 10 MB per line.
- On a non-TTY pipe, agy 1.0.16 (Linux) produces output normally — no PTY required. (The
  CHANGELOG's non-TTY discard fix was Windows-specific; re-verify on other platforms.)
- `--output-format` is a real flag but is **omitted from `agy --help`** in 1.0.16.
- A failure envelope (`status != "SUCCESS"`) was not reproducible during probing (agy silently
  ignores invalid flag values); confirm its shape from a genuinely failing run.
