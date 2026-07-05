"""Msgspec model and decoder for `agy --output-format json` result envelope.

The Antigravity CLI (`agy`) emits a single, untagged JSON object at the end of a
`-p` / `--print` run when `--output-format json` is passed (verified on agy 1.0.16):

    {"conversation_id": "...", "status": "SUCCESS", "response": "...",
     "duration_seconds": 1.24, "num_turns": 1,
     "usage": {"input_tokens": ..., "output_tokens": ..., "thinking_tokens": ..., "total_tokens": ...}}

Unlike the streaming engines, this is a terminal result envelope (not a JSONL
event feed), so a single struct is decoded directly — no ``tag_field`` union.
All fields are optional and unknown fields are ignored for forward-compat.
"""

from __future__ import annotations

import msgspec


class AntigravityUsage(msgspec.Struct, forbid_unknown_fields=False):
    input_tokens: int | None = None
    output_tokens: int | None = None
    thinking_tokens: int | None = None
    total_tokens: int | None = None


class AntigravityResult(msgspec.Struct, forbid_unknown_fields=False):
    conversation_id: str | None = None
    status: str | None = None
    response: str | None = None
    duration_seconds: float | None = None
    num_turns: int | None = None
    usage: AntigravityUsage | None = None
    # `error` is speculative: a non-SUCCESS envelope shape was not reproducible
    # during probing (agy silently ignores invalid flag values). Kept optional so
    # translate() can surface it if a real failure ever populates it.
    error: str | None = None


_DECODER = msgspec.json.Decoder(AntigravityResult)


def decode_result(line: str | bytes) -> AntigravityResult:
    return _DECODER.decode(line)
