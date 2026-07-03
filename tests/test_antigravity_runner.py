from pathlib import Path

import msgspec
import pytest

from untether.config import ConfigError
from untether.model import CompletedEvent, ResumeToken, StartedEvent
from untether.runners.antigravity import (
    ENGINE,
    AntigravityRunner,
    AntigravityStreamState,
    build_runner,
    translate_antigravity_result,
)
from untether.schemas import antigravity as antigravity_schema


def _decode(payload: dict) -> antigravity_schema.AntigravityResult:
    return antigravity_schema.decode_result(msgspec.json.encode(payload))


def _load_fixture(name: str) -> antigravity_schema.AntigravityResult:
    path = Path(__file__).parent / "fixtures" / name
    line = next(ln for ln in path.read_text().splitlines() if ln.strip())
    return antigravity_schema.decode_result(line)


# --- resume ---------------------------------------------------------------

def test_resume_format_and_extract() -> None:
    runner = AntigravityRunner()
    token = ResumeToken(engine=ENGINE, value="c0d91872-52f3-4ff8-bc71-965b7a264c66")
    assert (
        runner.format_resume(token)
        == "`agy --conversation c0d91872-52f3-4ff8-bc71-965b7a264c66`"
    )
    assert runner.extract_resume(
        "agy --conversation abc123def45678"
    ) == ResumeToken(engine=ENGINE, value="abc123def45678")
    # foreign resume lines must not match
    assert runner.extract_resume("`gemini --resume xyz789`") is None
    assert runner.extract_resume("`claude --resume sid`") is None


# --- translate ------------------------------------------------------------

def test_translate_success_fixture() -> None:
    runner = AntigravityRunner(model="Gemini 3.5 Flash (Low)")
    evt = _load_fixture("antigravity_success.jsonl")
    state = AntigravityStreamState()
    events = runner.translate(evt, state=state, resume=None, found_session=None)

    assert isinstance(events[0], StartedEvent)
    assert isinstance(events[-1], CompletedEvent)
    assert len(events) == 2

    started, completed = events
    assert started.resume == ResumeToken(engine=ENGINE, value="test-conv-123")
    assert started.meta == {
        "model": "Gemini 3.5 Flash (Low)",
        "permissionMode": "full access",
    }
    assert completed.ok is True
    assert completed.answer == "hello"
    assert completed.resume == ResumeToken(engine=ENGINE, value="test-conv-123")
    assert completed.usage == {
        "usage": {"input_tokens": 10, "output_tokens": 2, "thinking_tokens": 0},
        "duration_ms": 500,
        "num_turns": 1,
    }
    assert completed.error is None


def test_translate_failure_fixture() -> None:
    runner = AntigravityRunner()
    evt = _load_fixture("antigravity_failure.jsonl")
    events = runner.translate(
        evt, state=AntigravityStreamState(), resume=None, found_session=None
    )
    completed = events[-1]
    assert isinstance(completed, CompletedEvent)
    assert completed.ok is False
    assert completed.error is not None
    assert "quota exhausted" in completed.error


def test_translate_started_emitted_once() -> None:
    state = AntigravityStreamState()
    evt = _decode({"conversation_id": "x", "status": "SUCCESS", "response": "a"})
    first = translate_antigravity_result(evt, title="antigravity", state=state, meta=None)
    second = translate_antigravity_result(evt, title="antigravity", state=state, meta=None)
    assert any(isinstance(e, StartedEvent) for e in first)
    assert not any(isinstance(e, StartedEvent) for e in second)


def test_translate_empty_conversation_id_falls_back_to_resume() -> None:
    # A failure envelope lacking conversation_id must reuse the resume token the
    # run was resumed from, not emit an empty-valued token.
    state = AntigravityStreamState()
    evt = _decode({"status": "ERROR", "response": "", "error": "boom"})
    prior = ResumeToken(engine=ENGINE, value="prior-conv-42")
    events = translate_antigravity_result(
        evt, title="antigravity", state=state, meta=None, resume_fallback=prior
    )
    started, completed = events
    assert isinstance(started, StartedEvent)
    assert isinstance(completed, CompletedEvent)
    assert started.resume == prior
    assert completed.resume == prior


def test_translate_empty_conversation_id_no_fallback_omits_footer() -> None:
    # No conversation_id and no fallback: the user-facing CompletedEvent must not
    # carry an empty-valued resume token (which would render `agy --conversation `).
    state = AntigravityStreamState()
    evt = _decode({"status": "ERROR", "response": "", "error": "boom"})
    events = translate_antigravity_result(
        evt, title="antigravity", state=state, meta=None
    )
    started, completed = events
    assert completed.resume is None
    # StartedEvent.resume is required; the placeholder is empty-valued but never
    # surfaced in the footer.
    assert isinstance(started, StartedEvent)
    assert started.resume.value == ""


def test_permission_mode_composition() -> None:
    runner = AntigravityRunner(auto_approve=True, sandbox=True)
    evt = _decode({"conversation_id": "x", "status": "SUCCESS", "response": "a"})
    started = runner.translate(
        evt, state=AntigravityStreamState(), resume=None, found_session=None
    )[0]
    assert isinstance(started, StartedEvent)
    assert started.meta is not None
    assert started.meta["permissionMode"] == "full access · sandbox"


# --- build_args -----------------------------------------------------------

def test_build_args_fresh() -> None:
    runner = AntigravityRunner()
    args = runner.build_args("do a thing", None, state=None)
    assert args[:4] == ["-p", "do a thing", "--output-format", "json"]
    assert "--dangerously-skip-permissions" in args  # auto_approve default True
    assert "--continue" not in args and "--conversation" not in args


def test_build_args_model_and_sandbox() -> None:
    runner = AntigravityRunner(
        model="Gemini 3.1 Pro (High)", sandbox=True, print_timeout="10m"
    )
    args = runner.build_args("p", None, state=None)
    assert "--model" in args and "Gemini 3.1 Pro (High)" in args
    assert "--sandbox" in args
    assert args[args.index("--print-timeout") + 1] == "10m"


def test_build_args_continue_and_conversation() -> None:
    runner = AntigravityRunner()
    cont = runner.build_args(
        "p", ResumeToken(engine=ENGINE, value="v", is_continue=True), state=None
    )
    assert "--continue" in cont and "--conversation" not in cont

    conv = runner.build_args(
        "p", ResumeToken(engine=ENGINE, value="conv-9"), state=None
    )
    assert conv[conv.index("--conversation") + 1] == "conv-9"


def test_build_args_no_auto_approve() -> None:
    runner = AntigravityRunner(auto_approve=False)
    args = runner.build_args("p", None, state=None)
    assert "--dangerously-skip-permissions" not in args


def test_build_args_sanitizes_prompt() -> None:
    runner = AntigravityRunner()
    args = runner.build_args("--not-a-flag do it", None, state=None)
    # sanitized prompt must not be passed as a bare leading dash
    assert args[0] == "-p"
    assert not args[1].startswith("-")


def test_add_dirs_and_extra_args() -> None:
    runner = AntigravityRunner(add_dirs=("/a", "/b"), extra_args=("--foo",))
    args = runner.build_args("p", None, state=None)
    assert args.count("--add-dir") == 2
    assert "--foo" in args


# --- env ------------------------------------------------------------------

def test_env_is_filtered() -> None:
    runner = AntigravityRunner()
    env = runner.env(state=AntigravityStreamState())
    assert isinstance(env, dict)
    assert env.get("NO_COLOR") == "1"


# --- stream end / errors --------------------------------------------------

def test_stream_end_no_envelope() -> None:
    runner = AntigravityRunner()
    events = runner.stream_end_events(
        resume=None, found_session=None, state=AntigravityStreamState()
    )
    assert len(events) == 1
    assert isinstance(events[0], CompletedEvent)
    assert events[0].ok is False


def test_invalid_json_events_truncates_excerpt() -> None:
    runner = AntigravityRunner()
    raw = "x" * 5000
    events = runner.invalid_json_events(
        raw=raw, line=raw, state=AntigravityStreamState()
    )
    assert len(events) == 1
    line = events[0].action.detail["line"]
    # Bounded excerpt (500 chars + ellipsis), not the full 5000-char blob.
    assert line.endswith("…")
    assert len(line) <= 501


def test_invalid_json_events_short_line_not_truncated() -> None:
    runner = AntigravityRunner()
    raw = '{"partial": true'
    events = runner.invalid_json_events(
        raw=raw, line=raw, state=AntigravityStreamState()
    )
    assert events[0].action.detail["line"] == raw


def test_process_error_nonzero_rc() -> None:
    runner = AntigravityRunner()
    events = runner.process_error_events(
        1,
        resume=None,
        found_session=None,
        state=AntigravityStreamState(),
        stderr_lines=["boom"],
    )
    completed = [e for e in events if isinstance(e, CompletedEvent)]
    assert completed and completed[0].ok is False


# --- build_runner / config ------------------------------------------------

def test_build_runner_defaults(tmp_path: Path) -> None:
    runner = build_runner({}, tmp_path / "untether.toml")
    assert isinstance(runner, AntigravityRunner)
    assert runner.auto_approve is True
    assert runner.engine == ENGINE


def test_build_runner_rejects_reserved_flag(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        build_runner({"extra_args": ["--model", "x"]}, tmp_path / "untether.toml")


def test_build_runner_rejects_permission_flags(tmp_path: Path) -> None:
    # Permission-relevant flags are derived from the auto_approve/sandbox config
    # booleans; extra_args must not be able to re-enable or contradict them.
    for flag in ("--dangerously-skip-permissions", "--sandbox"):
        with pytest.raises(ConfigError):
            build_runner({"extra_args": [flag]}, tmp_path / "untether.toml")


def test_build_runner_rejects_bad_model(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        build_runner({"model": 123}, tmp_path / "untether.toml")


def test_backend_registration() -> None:
    from untether.runners.antigravity import BACKEND

    assert BACKEND.id == "antigravity"
    assert BACKEND.cli_cmd == "agy"
