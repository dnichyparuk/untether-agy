# Antigravity → Untether event mapping

`agy --output-format json` returns one terminal result envelope, which the runner translates into
the standard 3-event contract in a single `translate()` call: one `StartedEvent` followed by one
`CompletedEvent` (there are no intermediate `ActionEvent`s).

## Mapping

| Untether event / field | Source in the agy envelope |
|------------------------|----------------------------|
| `StartedEvent.resume` | `ResumeToken(engine="antigravity", value=conversation_id)` |
| `StartedEvent.title` | configured model name (or `"antigravity"`) |
| `StartedEvent.meta.model` | configured / `/model` override (envelope has no model echo) |
| `StartedEvent.meta.permissionMode` | `"full access"` (auto-approve) and/or `"sandbox"`, joined by `" · "` |
| `CompletedEvent.ok` | `status == "SUCCESS"` |
| `CompletedEvent.answer` | `response` |
| `CompletedEvent.resume` | same `ResumeToken` as Started |
| `CompletedEvent.usage.usage.{input,output,thinking}_tokens` | `usage.*` |
| `CompletedEvent.usage.duration_ms` | `duration_seconds * 1000` |
| `CompletedEvent.usage.num_turns` | `num_turns` |
| `CompletedEvent.error` | `error` or `"agy status: <status>"` when not SUCCESS |

## No `ActionEvent`s

Because the envelope is terminal-only, no tool/file/command progress is available — the progress
message stays "working…" until the final answer. This is the primary difference from streaming
engines like Gemini (`stream-json`).

## Terminal fallbacks

| Condition | Runner behaviour |
|-----------|------------------|
| Non-zero exit code | `process_error_events` → note + `CompletedEvent(ok=False)` with rc label + stderr excerpt |
| No envelope on stdout | `stream_end_events` → `CompletedEvent(ok=False, error="agy produced no result envelope")` |
| Undecodable JSON line | `decode_error_events` drops the line (logs `jsonl.msgspec.invalid`) |
