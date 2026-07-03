from untether.ids import (
    RESERVED_CHAT_COMMANDS,
    RESERVED_COMMAND_IDS,
    RESERVED_ENGINE_IDS,
)


def test_clone_is_reserved_chat_command() -> None:
    assert "clone" in RESERVED_CHAT_COMMANDS


def test_clone_is_reserved_command_id() -> None:
    assert "clone" in RESERVED_COMMAND_IDS


def test_clone_is_reserved_engine_id() -> None:
    assert "clone" in RESERVED_ENGINE_IDS
