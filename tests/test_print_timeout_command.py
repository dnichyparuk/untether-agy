"""Tests for the ``/printtimeout`` command (Task 4).

Covers the lenient duration validator (`parse_duration`) and the
`handle_print_timeout_command` orchestration: show / set / clear, the
out-of-topic degrade path, the non-Antigravity engine note, and graceful
write-failure handling. Runtime construction mirrors the conventions in
`tests/test_project_command.py`.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import anyio
import pytest

import untether.runtime_loader as runtime_loader
import untether.telegram.loop as loop_module
import untether.telegram.print_timeout as pt_module
from tests.telegram_fakes import FakeTransport, make_cfg
from untether.config import ConfigError, read_config
from untether.context import RunContext
from untether.ids import RESERVED_COMMAND_IDS
from untether.runtime_loader import build_runtime_spec
from untether.settings import load_settings
from untether.telegram.commands.menu import build_bot_commands
from untether.telegram.loop import TelegramCommandContext, _dispatch_builtin_command
from untether.telegram.print_timeout import (
    handle_print_timeout_command,
    parse_duration,
)
from untether.telegram.types import TelegramIncomingMessage
from untether.transport_runtime import TransportRuntime

_TELEGRAM_BASE = (
    '[transports.telegram]\nbot_token = "tok"\nchat_id = 123\n'
    "allow_any_user = true\n"
)

# A registered antigravity project `foo`, a claude project `bar`, and a global
# [antigravity] print_timeout so show/clear can report the global default.
_CONFIG_BODY = (
    '\n[antigravity]\nprint_timeout = "20m"\n'
    '\n[projects.foo]\npath = "foo"\ndefault_engine = "antigravity"\n'
    '\n[projects.bar]\npath = "bar"\ndefault_engine = "claude"\n'
)


# ── parse_duration ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value",
    ["30m", "1h30m", "0.5h", "5s", "500ms", "1h", "2h45m30s", "100us", "10micros"],
)
def test_parse_duration_accepts_valid(value: str) -> None:
    assert parse_duration(value) is True


@pytest.mark.parametrize(
    "value",
    ["banana", "", "30", "m", "1x", "30 m", "abc1h", "-5m"],
)
def test_parse_duration_rejects_garbage(value: str) -> None:
    assert parse_duration(value) is False


# ── handler scaffolding ─────────────────────────────────────────────────────


def _msg(text: str, *, chat_id: int = 123) -> TelegramIncomingMessage:
    return TelegramIncomingMessage(
        transport="telegram",
        chat_id=chat_id,
        message_id=1,
        text=text,
        reply_to_message_id=None,
        reply_to_text=None,
        sender_id=1,
        thread_id=None,
        chat_type="supergroup",
    )


def _write_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "untether.toml"
    config_path.write_text(_TELEGRAM_BASE + _CONFIG_BODY, encoding="utf-8")
    return config_path


def _build_runtime(
    config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> TransportRuntime:
    monkeypatch.setattr(runtime_loader.shutil, "which", lambda _cmd: "/bin/echo")
    settings, resolved_path = load_settings(config_path)
    spec = build_runtime_spec(settings=settings, config_path=resolved_path)
    return spec.to_runtime(config_path=resolved_path)


def _cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config_path = _write_config(tmp_path)
    runtime = _build_runtime(config_path, monkeypatch)
    transport = FakeTransport()
    cfg = replace(make_cfg(transport), runtime=runtime)
    return cfg, transport, config_path


def _last_text(transport: FakeTransport) -> str:
    return transport.send_calls[-1]["message"].text


# ── handle_print_timeout_command ────────────────────────────────────────────


@pytest.mark.anyio
class TestHandlePrintTimeout:
    async def test_set_writes_project_override_and_confirms(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg, transport, config_path = _cfg(tmp_path, monkeypatch)

        await handle_print_timeout_command(
            cfg,
            _msg("/printtimeout 30m"),
            args_text="30m",
            ambient_context=RunContext(project="foo"),
            topic_store=None,
            chat_prefs=None,
        )

        raw = read_config(config_path)
        assert raw["projects"]["foo"]["print_timeout"] == "30m"
        # NOTE: replies render markdown to plain text (** and backticks stripped),
        # so assert on the plain content, not the markdown source.
        text = _last_text(transport)
        assert "✅" in text
        assert "foo" in text
        assert "30m" in text
        assert "Antigravity" in text
        # foo IS antigravity, so the "currently only affects" caveat is absent.
        assert "only affects" not in text

    async def test_show_reports_effective_and_global_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg, transport, config_path = _cfg(tmp_path, monkeypatch)

        # First set an override so the effective value differs from the default.
        await handle_print_timeout_command(
            cfg,
            _msg("/printtimeout 45m"),
            args_text="45m",
            ambient_context=RunContext(project="foo"),
            topic_store=None,
            chat_prefs=None,
        )
        await handle_print_timeout_command(
            cfg,
            _msg("/printtimeout"),
            args_text="",
            ambient_context=RunContext(project="foo"),
            topic_store=None,
            chat_prefs=None,
        )

        text = _last_text(transport)
        assert "ℹ️" in text
        assert "foo" in text
        assert "45m" in text  # effective (project override)
        assert "20m" in text  # global default
        assert "project override" in text

    async def test_show_without_override_uses_global_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg, transport, _ = _cfg(tmp_path, monkeypatch)

        await handle_print_timeout_command(
            cfg,
            _msg("/printtimeout"),
            args_text="",
            ambient_context=RunContext(project="foo"),
            topic_store=None,
            chat_prefs=None,
        )

        text = _last_text(transport)
        assert "20m" in text
        assert "no override set" in text

    async def test_clear_removes_key_and_confirms(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg, transport, config_path = _cfg(tmp_path, monkeypatch)

        await handle_print_timeout_command(
            cfg,
            _msg("/printtimeout 30m"),
            args_text="30m",
            ambient_context=RunContext(project="foo"),
            topic_store=None,
            chat_prefs=None,
        )
        assert "print_timeout" in read_config(config_path)["projects"]["foo"]

        await handle_print_timeout_command(
            cfg,
            _msg("/printtimeout clear"),
            args_text="clear",
            ambient_context=RunContext(project="foo"),
            topic_store=None,
            chat_prefs=None,
        )

        assert "print_timeout" not in read_config(config_path)["projects"]["foo"]
        text = _last_text(transport)
        assert "♻️" in text
        assert "removed" in text
        assert "20m" in text  # global default now in effect

    async def test_non_admin_denied_for_set_and_clear(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg, transport, config_path = _cfg(tmp_path, monkeypatch)

        async def _non_admin_member(chat_id: int, user_id: int):
            _ = chat_id, user_id
            from untether.telegram.api_schemas import ChatMember

            return ChatMember(status="member")

        monkeypatch.setattr(cfg.bot, "get_chat_member", _non_admin_member)

        await handle_print_timeout_command(
            cfg,
            _msg("/printtimeout 30m"),
            args_text="30m",
            ambient_context=RunContext(project="foo"),
            topic_store=None,
            chat_prefs=None,
        )
        assert "print_timeout" not in read_config(config_path)["projects"]["foo"]
        assert "restricted to group admins" in _last_text(transport)

        await handle_print_timeout_command(
            cfg,
            _msg("/printtimeout clear"),
            args_text="clear",
            ambient_context=RunContext(project="foo"),
            topic_store=None,
            chat_prefs=None,
        )
        assert "restricted to group admins" in _last_text(transport)

    async def test_non_admin_allowed_for_show(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg, transport, _ = _cfg(tmp_path, monkeypatch)

        async def _non_admin_member(chat_id: int, user_id: int):
            _ = chat_id, user_id
            from untether.telegram.api_schemas import ChatMember

            return ChatMember(status="member")

        monkeypatch.setattr(cfg.bot, "get_chat_member", _non_admin_member)

        await handle_print_timeout_command(
            cfg,
            _msg("/printtimeout"),
            args_text="",
            ambient_context=RunContext(project="foo"),
            topic_store=None,
            chat_prefs=None,
        )

        text = _last_text(transport)
        assert "ℹ️" in text
        assert "20m" in text

    async def test_invalid_duration_replies_usage_and_writes_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg, transport, config_path = _cfg(tmp_path, monkeypatch)

        await handle_print_timeout_command(
            cfg,
            _msg("/printtimeout banana"),
            args_text="banana",
            ambient_context=RunContext(project="foo"),
            topic_store=None,
            chat_prefs=None,
        )

        text = _last_text(transport)
        assert "invalid duration" in text
        # Usage line is present (markdown backticks are stripped in the render).
        assert "usage" in text
        assert "clear" in text
        assert "print_timeout" not in read_config(config_path)["projects"]["foo"]

    async def test_out_of_topic_degrades_without_writing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg, transport, config_path = _cfg(tmp_path, monkeypatch)

        # ambient_context.project is None -> degrade.
        await handle_print_timeout_command(
            cfg,
            _msg("/printtimeout 30m"),
            args_text="30m",
            ambient_context=RunContext(project=None),
            topic_store=None,
            chat_prefs=None,
        )

        text = _last_text(transport)
        assert "⚠️" in text
        assert "project topic" in text
        assert "print_timeout" not in read_config(config_path)["projects"]["foo"]

    async def test_none_ambient_context_degrades(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg, transport, config_path = _cfg(tmp_path, monkeypatch)

        # The WHOLE object is None -> degrade (guard the whole ambient object).
        await handle_print_timeout_command(
            cfg,
            _msg("/printtimeout 30m"),
            args_text="30m",
            ambient_context=None,
            topic_store=None,
            chat_prefs=None,
        )

        assert "⚠️" in _last_text(transport)
        assert "print_timeout" not in read_config(config_path)["projects"]["foo"]

    async def test_non_antigravity_project_gets_caveat_note(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg, transport, config_path = _cfg(tmp_path, monkeypatch)

        # `bar` resolves to the claude engine.
        await handle_print_timeout_command(
            cfg,
            _msg("/printtimeout 30m"),
            args_text="30m",
            ambient_context=RunContext(project="bar"),
            topic_store=None,
            chat_prefs=None,
        )

        # Value is STILL written even though the engine isn't antigravity.
        assert read_config(config_path)["projects"]["bar"]["print_timeout"] == "30m"
        text = _last_text(transport)
        assert "only affects" in text
        assert "claude" in text

    async def test_write_oserror_replies_gracefully(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg, transport, _ = _cfg(tmp_path, monkeypatch)

        def _boom(*_args, **_kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(pt_module, "write_config", _boom)

        # Should not raise — the handler catches OSError and replies.
        await handle_print_timeout_command(
            cfg,
            _msg("/printtimeout 30m"),
            args_text="30m",
            ambient_context=RunContext(project="foo"),
            topic_store=None,
            chat_prefs=None,
        )

        assert "failed to write config" in _last_text(transport)

    async def test_write_configerror_replies_gracefully(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg, transport, _ = _cfg(tmp_path, monkeypatch)

        def _boom(*_args, **_kwargs):
            raise ConfigError("invalid document")

        monkeypatch.setattr(pt_module, "validate_settings_data", _boom)

        await handle_print_timeout_command(
            cfg,
            _msg("/printtimeout 30m"),
            args_text="30m",
            ambient_context=RunContext(project="foo"),
            topic_store=None,
            chat_prefs=None,
        )

        assert "failed to set print_timeout" in _last_text(transport)


# ── routing (_dispatch_builtin_command) ─────────────────────────────────────
# Mirrors TestProjectDispatchTracking in tests/test_project_command.py: build a
# TelegramCommandContext, monkeypatch the handler that loop.py imported by
# name, and assert the dispatch branch schedules it with the right arguments.


@pytest.mark.anyio
class TestPrintTimeoutRouting:
    async def test_printtimeout_routes_to_handler_with_ambient_context(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg, _transport, _config_path = _cfg(tmp_path, monkeypatch)
        calls: list[dict] = []

        async def _fake_handler(
            cfg_arg,
            msg_arg,
            args_text_arg,
            ambient_context_arg,
            topic_store_arg,
            chat_prefs_arg,
            *,
            resolved_scope=None,
            scope_chat_ids=None,
        ) -> None:
            calls.append(
                {
                    "cfg": cfg_arg,
                    "msg": msg_arg,
                    "args_text": args_text_arg,
                    "ambient_context": ambient_context_arg,
                    "topic_store": topic_store_arg,
                    "chat_prefs": chat_prefs_arg,
                    "resolved_scope": resolved_scope,
                    "scope_chat_ids": scope_chat_ids,
                }
            )

        monkeypatch.setattr(loop_module, "handle_print_timeout_command", _fake_handler)

        msg = _msg("/printtimeout 30m")
        ambient_context = RunContext(project="foo")

        async def _reply(*_a: object, **_k: object) -> None:
            return None

        async with anyio.create_task_group() as tg:
            ctx = TelegramCommandContext(
                cfg=cfg,
                msg=msg,
                args_text="30m",
                ambient_context=ambient_context,
                topic_store=None,
                chat_prefs=None,
                resolved_scope="all",
                scope_chat_ids=frozenset({msg.chat_id}),
                reply=_reply,
                task_group=tg,
            )
            result = _dispatch_builtin_command(ctx=ctx, command_id="printtimeout")
            assert result is True

        assert len(calls) == 1
        call = calls[0]
        assert call["ambient_context"] is ambient_context
        assert call["ambient_context"].project == "foo"
        assert call["args_text"] == "30m"
        assert call["resolved_scope"] == "all"
        assert call["scope_chat_ids"] == frozenset({msg.chat_id})


# ── menu (build_bot_commands) ────────────────────────────────────────────────


class TestPrintTimeoutMenu:
    def test_printtimeout_reserved_but_in_static_list(self) -> None:
        # Reserved ids are excluded from the plugin entry-point loop in
        # build_bot_commands, so "printtimeout" must reach Telegram's
        # autocomplete only via the unconditional static list.
        assert "printtimeout" in RESERVED_COMMAND_IDS

    def test_printtimeout_appears_exactly_once(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_path = _write_config(tmp_path)
        runtime = _build_runtime(config_path, monkeypatch)

        commands = build_bot_commands(runtime)
        matches = [cmd for cmd in commands if cmd["command"] == "printtimeout"]
        assert len(matches) == 1

        # Calling it a second time must not accumulate duplicates.
        commands_again = build_bot_commands(runtime)
        matches_again = [
            cmd for cmd in commands_again if cmd["command"] == "printtimeout"
        ]
        assert len(matches_again) == 1

    def test_printtimeout_present_regardless_of_include_flags(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_path = _write_config(tmp_path)
        runtime = _build_runtime(config_path, monkeypatch)

        for include_file in (True, False):
            for include_topics in (True, False):
                for include_clone in (True, False):
                    for include_new_project in (True, False):
                        commands = build_bot_commands(
                            runtime,
                            include_file=include_file,
                            include_topics=include_topics,
                            include_clone=include_clone,
                            include_new_project=include_new_project,
                        )
                        assert any(
                            cmd["command"] == "printtimeout" for cmd in commands
                        ), (
                            f"printtimeout missing for include_file={include_file}, "
                            f"include_topics={include_topics}, "
                            f"include_clone={include_clone}, "
                            f"include_new_project={include_new_project}"
                        )
