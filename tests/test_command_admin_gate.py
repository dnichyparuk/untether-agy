"""Tests for the dispatch-level admin-or-private gate on /planmode and /verbose.

Unlike /model, /reasoning, /clone, /project, and /printtimeout (which gate
inside their own Telegram-specific handler), /planmode and /verbose are
CommandBackend plugins whose ``handle(ctx: CommandContext)`` has no access to
the Telegram-specific ``cfg``/``msg`` needed by ``check_admin_or_private``.
The gate therefore lives in ``dispatch.py``'s ``_dispatch_command``, the one
chokepoint where both the generic ``CommandContext`` and the Telegram
``cfg``/``msg`` are available. These tests exercise that chokepoint directly.
"""

from __future__ import annotations

from dataclasses import replace

import anyio
import pytest

from tests.telegram_fakes import FakeTransport, make_cfg
from untether.scheduler import ThreadScheduler
from untether.telegram.api_schemas import ChatMember
from untether.telegram.commands.dispatch import _dispatch_command
from untether.telegram.commands.verbose import _VERBOSE_OVERRIDES
from untether.telegram.types import TelegramIncomingMessage


@pytest.fixture(autouse=True)
def _clear_verbose_overrides():
    _VERBOSE_OVERRIDES.clear()
    yield
    _VERBOSE_OVERRIDES.clear()


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


async def _run_dispatch(cfg, msg, command_id: str, args_text: str) -> None:
    async def _run_job(_job) -> None:
        return None

    async with anyio.create_task_group() as tg:
        scheduler = ThreadScheduler(task_group=tg, run_job=_run_job)
        await _dispatch_command(
            cfg,
            msg,
            msg.text,
            command_id,
            args_text,
            running_tasks={},
            scheduler=scheduler,
            on_thread_known=None,
            stateful_mode=False,
            default_engine_override=None,
            engine_overrides_resolver=None,
        )
        tg.cancel_scope.cancel()


def _non_admin_bot(cfg):
    async def _non_admin_member(chat_id: int, user_id: int):
        _ = chat_id, user_id
        return ChatMember(status="member")

    cfg.bot.get_chat_member = _non_admin_member
    return cfg


@pytest.mark.anyio
async def test_planmode_denied_for_non_admin() -> None:
    transport = FakeTransport()
    cfg = _non_admin_bot(make_cfg(transport))

    await _run_dispatch(cfg, _msg("/planmode on"), "planmode", "on")

    text = transport.send_calls[-1]["message"].text
    assert "restricted to group admins" in text


@pytest.mark.anyio
async def test_verbose_denied_for_non_admin() -> None:
    transport = FakeTransport()
    cfg = _non_admin_bot(make_cfg(transport))

    await _run_dispatch(cfg, _msg("/verbose on"), "verbose", "on")

    text = transport.send_calls[-1]["message"].text
    assert "restricted to group admins" in text
    # The backend never ran — no override was recorded.
    assert 123 not in _VERBOSE_OVERRIDES


@pytest.mark.anyio
async def test_verbose_allowed_for_admin() -> None:
    transport = FakeTransport()
    cfg = make_cfg(transport)  # default FakeBot -> administrator

    await _run_dispatch(cfg, _msg("/verbose on"), "verbose", "on")

    assert _VERBOSE_OVERRIDES.get(123) == "verbose"


@pytest.mark.anyio
async def test_verbose_allowed_in_private_chat_for_non_admin() -> None:
    transport = FakeTransport()
    cfg = _non_admin_bot(make_cfg(transport))
    msg = replace(_msg("/verbose on"), chat_type="private")

    await _run_dispatch(cfg, msg, "verbose", "on")

    assert _VERBOSE_OVERRIDES.get(123) == "verbose"
