"""Antigravity CLI runner.

Integrates Google's Antigravity CLI (`agy`, https://antigravity.google/docs/cli-overview).

`agy` is a **non-interactive, structured-result** engine: a headless run
(`agy -p "<prompt>" --output-format json`) emits a single JSON result envelope at
completion — not a streaming event feed. Consequently this runner produces a real
resume token (from `conversation_id`), the answer, and token usage, but **no live
ActionEvent progress** and **no interactive approval** (agy has no control channel).

Verified against agy 1.0.16. See docs/reference/runners/antigravity/ for the protocol.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import msgspec

from ..backends import EngineBackend, EngineConfig
from ..config import ConfigError
from ..logging import get_logger
from ..model import (
    CompletedEvent,
    EngineId,
    ResumeToken,
    StartedEvent,
    UntetherEvent,
)
from ..runner import (
    JsonlSubprocessRunner,
    ResumeTokenMixin,
    Runner,
    _rc_label,
    _session_label,
    _stderr_excerpt,
)
from ..schemas import antigravity as antigravity_schema
from .run_options import get_run_options

logger = get_logger(__name__)

ENGINE: EngineId = "antigravity"

# Matches the resume footer `agy --conversation <uuid>` so a reply resumes the
# right conversation via AutoRouter.
_RESUME_RE = re.compile(
    r"(?im)^\s*`?agy\s+--conversation\s+(?P<token>[0-9A-Za-z-]{8,})`?\s*$"
)

# Flags Untether manages itself — reject in [antigravity] extra_args so users
# can't break the I/O contract (mirrors the claude runner's reserved-flag guard).
_RESERVED_FLAGS: frozenset[str] = frozenset(
    {
        "-p",
        "--print",
        "--prompt",
        "--output-format",
        "--continue",
        "-c",
        "--conversation",
        "--model",
    }
)


@dataclass(slots=True)
class AntigravityStreamState:
    """State tracked while reading the (single-line) agy result envelope."""

    session_id: str | None = None
    emitted_started: bool = False
    model: str | None = None
    saw_result: bool = False
    last_text: str | None = None
    note_seq: int = 0


def _build_usage(evt: antigravity_schema.AntigravityResult) -> dict[str, Any] | None:
    """Map the envelope's token accounting to Untether's usage shape.

    Note: agy reports tokens only — there is no `total_cost_usd`, so cost budgets
    are not enforced for this engine.
    """
    out: dict[str, Any] = {}
    usage = evt.usage
    if usage is not None:
        token_usage: dict[str, Any] = {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
        }
        if usage.thinking_tokens is not None:
            token_usage["thinking_tokens"] = usage.thinking_tokens
        out["usage"] = token_usage
    if isinstance(evt.duration_seconds, (int, float)):
        out["duration_ms"] = int(evt.duration_seconds * 1000)
    if isinstance(evt.num_turns, int):
        out["num_turns"] = evt.num_turns
    return out or None


def translate_antigravity_result(
    evt: antigravity_schema.AntigravityResult,
    *,
    title: str,
    state: AntigravityStreamState,
    meta: dict[str, Any] | None,
) -> list[UntetherEvent]:
    """Translate the single agy result envelope into Started + Completed events."""
    out: list[UntetherEvent] = []
    state.saw_result = True
    conversation_id = evt.conversation_id or ""
    state.session_id = conversation_id or state.session_id
    resume = ResumeToken(engine=ENGINE, value=conversation_id)

    if not state.emitted_started:
        state.emitted_started = True
        logger.info(
            "antigravity.session.started",
            conversation_id=state.session_id,
            model=state.model,
            title=title,
        )
        out.append(
            StartedEvent(
                engine=ENGINE,
                resume=resume,
                title=title,
                meta=meta or None,
            )
        )

    ok = (evt.status or "").upper() == "SUCCESS"
    answer = evt.response or ""
    state.last_text = answer
    usage = _build_usage(evt)
    error: str | None = None
    if not ok:
        error = evt.error or f"agy status: {evt.status or 'unknown'}"
    logger.info(
        "antigravity.completed",
        conversation_id=state.session_id,
        status=evt.status,
        ok=ok,
        answer_len=len(answer),
    )
    out.append(
        CompletedEvent(
            engine=ENGINE,
            ok=ok,
            answer=answer,
            resume=resume,
            usage=usage,
            error=error,
        )
    )
    return out


@dataclass(slots=True)
class AntigravityRunner(ResumeTokenMixin, JsonlSubprocessRunner):
    """Runner for the Antigravity CLI (`agy`)."""

    engine: EngineId = ENGINE
    resume_re: re.Pattern[str] = _RESUME_RE
    agy_cmd: str = "agy"
    model: str | None = None
    sandbox: bool = False
    auto_approve: bool = True
    print_timeout: str | None = None
    add_dirs: tuple[str, ...] = ()
    extra_args: tuple[str, ...] = ()
    session_title: str = "antigravity"
    logger = logger

    def format_resume(self, token: ResumeToken) -> str:
        if token.engine != ENGINE:
            raise RuntimeError(f"resume token is for engine {token.engine!r}")
        return f"`agy --conversation {token.value}`"

    def command(self) -> str:
        return self.agy_cmd

    def _resolved_model(self) -> str | None:
        run_options = get_run_options()
        if run_options is not None and run_options.model:
            return str(run_options.model)
        return self.model

    def build_args(
        self,
        prompt: str,
        resume: ResumeToken | None,
        *,
        state: Any,
    ) -> list[str]:
        args: list[str] = [
            "-p",
            self.sanitize_prompt(prompt),
            "--output-format",
            "json",
        ]
        model = self._resolved_model()
        if model:
            args.extend(["--model", str(model)])
        if resume is not None:
            if resume.is_continue:
                args.append("--continue")
            else:
                args.extend(["--conversation", resume.value])
        if self.sandbox:
            args.append("--sandbox")
        if self.auto_approve:
            args.append("--dangerously-skip-permissions")
        if self.print_timeout:
            args.extend(["--print-timeout", str(self.print_timeout)])
        for directory in self.add_dirs:
            args.extend(["--add-dir", str(directory)])
        args.extend(self.extra_args)
        return args

    def stdin_payload(
        self,
        prompt: str,
        resume: ResumeToken | None,
        *,
        state: Any,
    ) -> bytes | None:
        return None

    def env(self, *, state: AntigravityStreamState) -> dict[str, str] | None:
        # #198: allowlist filter — the agy subprocess does not inherit the full
        # daemon environment (bot tokens, other engines' API keys). agy auths via
        # the OS keyring, so no API-key env is threaded here.
        from ..utils.env_policy import filtered_env

        env = filtered_env()
        env.setdefault("NO_COLOR", "1")
        return env

    def new_state(
        self, prompt: str, resume: ResumeToken | None
    ) -> AntigravityStreamState:
        return AntigravityStreamState()

    def start_run(
        self,
        prompt: str,
        resume: ResumeToken | None,
        *,
        state: AntigravityStreamState,
    ) -> None:
        pass

    def _meta(self) -> dict[str, Any] | None:
        meta: dict[str, Any] = {}
        model = self._resolved_model()
        if model is not None:
            meta["model"] = str(model)
        labels: list[str] = []
        if self.auto_approve:
            labels.append("full access")
        if self.sandbox:
            labels.append("sandbox")
        if labels:
            meta["permissionMode"] = " · ".join(labels)
        return meta or None

    def translate(
        self,
        data: antigravity_schema.AntigravityResult,
        *,
        state: AntigravityStreamState,
        resume: ResumeToken | None,
        found_session: ResumeToken | None,
    ) -> list[UntetherEvent]:
        return translate_antigravity_result(
            data,
            title=self.session_title,
            state=state,
            meta=self._meta(),
        )

    def decode_jsonl(
        self, *, line: bytes
    ) -> antigravity_schema.AntigravityResult:
        return antigravity_schema.decode_result(line)

    def invalid_json_events(
        self,
        *,
        raw: str,
        line: str,
        state: AntigravityStreamState,
    ) -> list[UntetherEvent]:
        message = "invalid JSON from agy; ignoring line"
        return [self.note_event(message, state=state, detail={"line": raw})]

    def decode_error_events(
        self,
        *,
        raw: str,
        line: str,
        error: Exception,
        state: AntigravityStreamState,
    ) -> list[UntetherEvent]:
        if isinstance(error, msgspec.DecodeError):
            self.get_logger().warning(
                "jsonl.msgspec.invalid",
                tag=self.tag(),
                error=str(error),
                error_type=error.__class__.__name__,
            )
            return []
        return super().decode_error_events(
            raw=raw,
            line=line,
            error=error,
            state=state,
        )

    def process_error_events(
        self,
        rc: int,
        *,
        resume: ResumeToken | None,
        found_session: ResumeToken | None,
        state: AntigravityStreamState,
        stderr_lines: list[str] | None = None,
    ) -> list[UntetherEvent]:
        parts = [f"agy failed ({_rc_label(rc)})."]
        session = _session_label(found_session, resume)
        if session:
            parts.append(f"session: {session}")
        excerpt = _stderr_excerpt(stderr_lines)
        if excerpt:
            parts.append(excerpt)
        message = "\n".join(parts)
        logger.error("antigravity.process.failed", rc=rc, session_id=state.session_id)
        return [
            self.note_event(message, state=state, ok=False),
            CompletedEvent(
                engine=ENGINE,
                ok=False,
                answer=state.last_text or "",
                resume=found_session or resume,
                error=message,
            ),
        ]

    def stream_end_events(
        self,
        *,
        resume: ResumeToken | None,
        found_session: ResumeToken | None,
        state: AntigravityStreamState,
    ) -> list[UntetherEvent]:
        # No envelope arrived (empty stdout). agy 1.0.16 on Linux produces output
        # on a pipe, but defend the "succeeded but did nothing" non-TTY trap.
        parts = ["agy produced no result envelope"]
        session = _session_label(found_session, resume)
        if session:
            parts.append(f"session: {session}")
        message = "\n".join(parts)
        logger.warning("antigravity.stream.no_result")
        return [
            CompletedEvent(
                engine=ENGINE,
                ok=False,
                answer=state.last_text or "",
                resume=found_session or resume,
                error=message,
            )
        ]


def _find_reserved_flag(args: list[str]) -> str | None:
    for arg in args:
        head = arg.split("=", 1)[0]
        if head in _RESERVED_FLAGS:
            return head
    return None


def build_runner(config: EngineConfig, config_path: Path) -> Runner:
    """Build an AntigravityRunner from configuration."""
    model = config.get("model")
    if model is not None and not isinstance(model, str):
        raise ConfigError(
            f"Invalid `antigravity.model` in {config_path}; expected a string."
        )

    sandbox = config.get("sandbox", False)
    if not isinstance(sandbox, bool):
        raise ConfigError(
            f"Invalid `antigravity.sandbox` in {config_path}; expected a boolean."
        )

    auto_approve = config.get("auto_approve", True)
    if not isinstance(auto_approve, bool):
        raise ConfigError(
            f"Invalid `antigravity.auto_approve` in {config_path}; expected a boolean."
        )

    print_timeout = config.get("print_timeout")
    if print_timeout is not None and not isinstance(print_timeout, str):
        raise ConfigError(
            f"Invalid `antigravity.print_timeout` in {config_path}; expected a string."
        )

    raw_add_dirs = config.get("add_dirs", [])
    if not isinstance(raw_add_dirs, list) or not all(
        isinstance(d, str) for d in raw_add_dirs
    ):
        raise ConfigError(
            f"Invalid `antigravity.add_dirs` in {config_path}; expected a list of strings."
        )

    raw_extra_args = config.get("extra_args", [])
    if not isinstance(raw_extra_args, list) or not all(
        isinstance(a, str) for a in raw_extra_args
    ):
        raise ConfigError(
            f"Invalid `antigravity.extra_args` in {config_path}; expected a list of strings."
        )
    reserved = _find_reserved_flag(raw_extra_args)
    if reserved is not None:
        raise ConfigError(
            f"`antigravity.extra_args` in {config_path} may not include the "
            f"Untether-managed flag {reserved!r}."
        )

    title = str(model) if model is not None else "antigravity"

    return AntigravityRunner(
        model=model,
        sandbox=sandbox,
        auto_approve=auto_approve,
        print_timeout=print_timeout,
        add_dirs=tuple(raw_add_dirs),
        extra_args=tuple(raw_extra_args),
        session_title=title,
    )


BACKEND = EngineBackend(
    id="antigravity",
    build_runner=build_runner,
    cli_cmd="agy",
    install_cmd="curl -fsSL https://antigravity.google/cli/install.sh | bash",
)
