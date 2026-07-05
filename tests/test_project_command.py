"""Tests for the `/project` command's settings (Task 1) and core module (Task 3).

Covers `NewProjectSettings` (settings.py) — defaults, TOML loading,
unknown-field rejection, and empty-root rejection — mirroring the
`CloneSettings` test conventions in `tests/test_clone_command.py`, plus the
`new_project.py` helpers (`sanitize_alias`, `resolve_project_path`) and the
`handle_project_command` orchestration (sanitise -> collide -> mkdir ->
register -> gated topic step).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import anyio
import pytest

import untether.runtime_loader as runtime_loader
import untether.settings as settings_module
import untether.telegram.clone as clone_module
import untether.telegram.loop as loop_module
from tests.telegram_fakes import FakeBot, FakeTransport, make_cfg
from untether.config import ConfigError, read_config
from untether.context import RunContext
from untether.runner_bridge import RunningTask
from untether.runtime_loader import build_runtime_spec
from untether.settings import (
    NewProjectSettings,
    TelegramTopicsSettings,
    UntetherSettings,
    load_settings,
)
from untether.telegram.api_models import ForumTopic
from untether.telegram.backend import _load_new_project_settings
from untether.telegram.clone import derive_alias, sanitize_alias
from untether.telegram.loop import (
    TelegramCommandContext,
    _apply_new_project_hot_reload,
    _dispatch_builtin_command,
    _run_project_command_tracked,
)
from untether.telegram.new_project import (
    _NEW_PROJECT_USAGE,
    handle_project_command,
    resolve_project_path,
)
from untether.telegram.types import TelegramIncomingMessage
from untether.transport import MessageRef
from untether.transport_runtime import TransportRuntime

_TELEGRAM_BASE = (
    '[transports.telegram]\nbot_token = "tok"\nchat_id = 123\n'
    "allow_any_user = true\n"
)


def _ref(repo: str):
    """Minimal RepoRef stand-in for exercising derive_alias via sanitize_alias."""
    from untether.telegram.clone import RepoRef

    return RepoRef(
        host="github.com", owner="owner", repo=repo, url=f"u/{repo}", scheme="https"
    )


# ── NewProjectSettings ───────────────────────────────────────────────────


def test_new_project_settings_defaults() -> None:
    settings = NewProjectSettings()
    assert settings.enabled is True
    assert settings.root == "~/untether-projects"
    assert settings.default_engine is None


def test_new_project_settings_loads_from_toml(tmp_path: Path) -> None:
    config_path = tmp_path / "untether.toml"
    config_path.write_text(
        _TELEGRAM_BASE
        + "\n[new_project]\n"
        'root = "/srv/repos"\n'
        'default_engine = "codex"\n',
        encoding="utf-8",
    )
    settings, _ = load_settings(config_path)
    assert settings.new_project.root == "/srv/repos"
    assert settings.new_project.default_engine == "codex"


def test_untether_settings_default_new_project_is_present() -> None:
    settings = UntetherSettings.model_validate(
        {
            "transports": {
                "telegram": {"bot_token": "tok", "chat_id": 1, "allow_any_user": True}
            }
        }
    )
    assert isinstance(settings.new_project, NewProjectSettings)
    assert settings.new_project.enabled is True


def test_new_project_settings_rejects_empty_root(tmp_path: Path) -> None:
    config_path = tmp_path / "untether.toml"
    config_path.write_text(
        _TELEGRAM_BASE + "\n[new_project]\nroot = \"\"\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="root"):
        load_settings(config_path)


def test_new_project_settings_rejects_whitespace_root(tmp_path: Path) -> None:
    config_path = tmp_path / "untether.toml"
    config_path.write_text(
        _TELEGRAM_BASE + "\n[new_project]\nroot = \"   \"\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="root"):
        load_settings(config_path)


def test_new_project_settings_rejects_unknown_field(tmp_path: Path) -> None:
    config_path = tmp_path / "untether.toml"
    config_path.write_text(
        _TELEGRAM_BASE + "\n[new_project]\nbogus_field = 1\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="bogus_field"):
        load_settings(config_path)


# ── Command Reservation (Task 2) ────────────────────────────────────────────


def test_project_command_is_reserved() -> None:
    """Verify 'project' is reserved in RESERVED_CHAT_COMMANDS and RESERVED_COMMAND_IDS."""
    from untether.ids import RESERVED_CHAT_COMMANDS, RESERVED_COMMAND_IDS, is_valid_id

    assert "project" in RESERVED_CHAT_COMMANDS
    assert "project" in RESERVED_COMMAND_IDS
    # Sanity check: "project" is a valid command id
    assert is_valid_id("project")


# ── sanitize_alias (Task 3) ──────────────────────────────────────────────


class TestSanitizeAlias:
    def test_lowercases_and_replaces_punctuation(self) -> None:
        assert sanitize_alias("My Repo!") == "my_repo"

    def test_simple_name_unchanged(self) -> None:
        assert sanitize_alias("myrepo") == "myrepo"

    def test_strips_leading_trailing_underscores(self) -> None:
        assert sanitize_alias("-repo-") == "repo"

    def test_truncates_to_32_chars(self) -> None:
        assert sanitize_alias("a" * 50) == "a" * 32

    def test_collapses_unicode_to_underscore(self) -> None:
        # "é" -> "_" then stripped, leaving "caf".
        assert sanitize_alias("café") == "caf"

    @pytest.mark.parametrize("name", ["", "   ", "---", "!!!", "  @# "])
    def test_rejects_empty_or_punctuation_only(self, name: str) -> None:
        with pytest.raises(ValueError, match="valid project alias"):
            sanitize_alias(name)


class TestDeriveAliasStillWorks:
    """derive_alias must behave unchanged after being refactored to call
    sanitize_alias (falls back to "repo" instead of raising)."""

    def test_simple(self) -> None:
        assert derive_alias(_ref("repo"), existing=set()) == "repo"

    def test_falls_back_to_repo_on_empty_sanitisation(self) -> None:
        assert derive_alias(_ref("---"), existing=set()) == "repo"

    def test_dedupes_with_numeric_suffix(self) -> None:
        assert derive_alias(_ref("repo"), existing={"repo"}) == "repo_1"


# ── resolve_project_path (Task 3) ────────────────────────────────────────


class TestResolveProjectPath:
    def test_returns_root_child(self, tmp_path: Path) -> None:
        root = tmp_path / "projects"
        assert resolve_project_path("foo", root) == (root / "foo").resolve()

    def test_expands_tilde_in_root(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        dest = resolve_project_path("foo", Path("~/untether-projects"))
        assert dest == (tmp_path / "untether-projects" / "foo").resolve()

    def test_rejects_traversal_name(self, tmp_path: Path) -> None:
        root = tmp_path / "projects"
        with pytest.raises(ValueError, match="outside project root"):
            resolve_project_path("../../etc", root)

    def test_rejects_symlink_escape(self, tmp_path: Path) -> None:
        root = tmp_path / "projects"
        root.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (root / "escape").symlink_to(outside, target_is_directory=True)
        with pytest.raises(ValueError, match="outside project root"):
            resolve_project_path("escape/foo", root)


# ── handle_project_command (orchestration, Task 3) ───────────────────────


class _RecordingTopicStore:
    """Duck-typed TopicStateStore double that records set_context pins."""

    def __init__(self) -> None:
        self.set_context_calls: list[dict] = []

    async def set_context(
        self,
        chat_id: int,
        thread_id: int,
        context: RunContext,
        *,
        topic_title: str | None = None,
    ) -> None:
        self.set_context_calls.append(
            {
                "chat_id": chat_id,
                "thread_id": thread_id,
                "context": context,
                "topic_title": topic_title,
            }
        )


class _ProjectFakeBot(FakeBot):
    """FakeBot whose create_forum_topic result is controllable per-test."""

    def __init__(self, topic_result: ForumTopic | None) -> None:
        super().__init__()
        self._topic_result = topic_result
        self.create_topic_calls: list[dict] = []

    async def create_forum_topic(
        self, chat_id: int, name: str
    ) -> ForumTopic | None:
        self.create_topic_calls.append({"chat_id": chat_id, "name": name})
        return self._topic_result


def _project_msg(
    text: str, *, chat_id: int = 123, chat_type: str = "supergroup"
) -> TelegramIncomingMessage:
    return TelegramIncomingMessage(
        transport="telegram",
        chat_id=chat_id,
        message_id=1,
        text=text,
        reply_to_message_id=None,
        reply_to_text=None,
        sender_id=1,
        thread_id=None,
        chat_type=chat_type,
    )


def _write_config(tmp_path: Path, *, new_project_block: str = "") -> Path:
    config_path = tmp_path / "untether.toml"
    config_path.write_text(_TELEGRAM_BASE + new_project_block, encoding="utf-8")
    return config_path


def _build_runtime(
    config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> TransportRuntime:
    monkeypatch.setattr(runtime_loader.shutil, "which", lambda _cmd: "/bin/echo")
    settings, resolved_path = load_settings(config_path)
    spec = build_runtime_spec(settings=settings, config_path=resolved_path)
    return spec.to_runtime(config_path=resolved_path)


def _orch_cfg(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    topics_enabled: bool,
    topic_result: ForumTopic | None,
    new_project_block: str = "",
):
    config_path = _write_config(tmp_path, new_project_block=new_project_block)
    runtime = _build_runtime(config_path, monkeypatch)
    transport = FakeTransport()
    bot = _ProjectFakeBot(topic_result)
    # The handler reads `[new_project]` from cfg.new_project (threaded on the
    # bridge config + hot-reloaded), not from disk — so mirror the startup wiring
    # here by loading the same settings the runtime was built from.
    settings, _ = load_settings(config_path)
    cfg = replace(
        make_cfg(transport),
        runtime=runtime,
        bot=bot,
        topics=TelegramTopicsSettings(enabled=topics_enabled, scope="all"),
        new_project=settings.new_project,
    )
    return cfg, transport, bot, config_path


@pytest.mark.anyio
class TestHandleProjectCommand:
    async def test_no_config_path_replies_error(self) -> None:
        transport = FakeTransport()
        cfg = make_cfg(transport)
        assert cfg.runtime.config_path is None

        await handle_project_command(
            cfg, _project_msg("/project foo"), args_text="foo", topic_store=None
        )

        assert "no config path available" in transport.send_calls[-1]["message"].text

    async def test_disabled_short_circuits(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg, transport, bot, config_path = _orch_cfg(
            tmp_path,
            monkeypatch,
            topics_enabled=True,
            topic_result=ForumTopic(message_thread_id=7),
            new_project_block="\n[new_project]\nenabled = false\n",
        )

        await handle_project_command(
            cfg, _project_msg("/project foo"), args_text="foo", topic_store=None
        )

        assert "disabled" in transport.send_calls[-1]["message"].text
        assert "foo" not in read_config(config_path).get("projects", {})

    async def test_empty_name_replies_usage(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg, transport, bot, config_path = _orch_cfg(
            tmp_path, monkeypatch, topics_enabled=False, topic_result=None
        )

        await handle_project_command(
            cfg, _project_msg("/project"), args_text="   ", topic_store=None
        )

        assert transport.send_calls[-1]["message"].text == _NEW_PROJECT_USAGE
        assert read_config(config_path).get("projects", {}) == {}

    async def test_invalid_name_replies_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg, transport, bot, config_path = _orch_cfg(
            tmp_path, monkeypatch, topics_enabled=False, topic_result=None
        )

        await handle_project_command(
            cfg, _project_msg("/project ---"), args_text="---", topic_store=None
        )

        last = transport.send_calls[-1]["message"].text
        assert "cannot derive a valid project name" in last
        assert read_config(config_path).get("projects", {}) == {}

    async def test_existing_alias_refuses_naming_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        other_path = tmp_path / "existing-project"
        other_path.mkdir()
        config_path = _write_config(
            tmp_path, new_project_block=f'\n[projects.foo]\npath = "{other_path}"\n'
        )
        runtime = _build_runtime(config_path, monkeypatch)
        transport = FakeTransport()
        cfg = replace(make_cfg(transport), runtime=runtime)

        await handle_project_command(
            cfg, _project_msg("/project foo"), args_text="foo", topic_store=None
        )

        last = transport.send_calls[-1]["message"].text
        assert "already exists" in last
        assert str(other_path) in last
        # No config write beyond the pre-existing project; the alias still
        # points at the original path.
        raw = read_config(config_path)
        assert raw["projects"]["foo"]["path"] == str(other_path)

    async def test_success_register_only_non_forum(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        clone_root = tmp_path / "projects"
        cfg, transport, bot, config_path = _orch_cfg(
            tmp_path,
            monkeypatch,
            topics_enabled=False,
            topic_result=ForumTopic(message_thread_id=7),
            new_project_block=f'\n[new_project]\nroot = "{clone_root}"\n',
        )

        await handle_project_command(
            cfg,
            _project_msg("/project foo", chat_type="private"),
            args_text="foo",
            topic_store=_RecordingTopicStore(),  # type: ignore[arg-type]
        )

        # Directory created + project registered on disk + live runtime.
        dest = (clone_root / "foo").resolve()
        assert dest.is_dir()
        raw = read_config(config_path)
        assert raw["projects"]["foo"]["path"] == str(dest)
        assert "foo" in cfg.runtime.project_aliases()
        # No topic step attempted; register-only reply.
        assert bot.create_topic_calls == []
        assert "run /topic foo" in transport.send_calls[-1]["message"].text

    async def test_existing_nonempty_dir_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An unregistered but non-empty destination dir is refused, not adopted."""
        clone_root = tmp_path / "projects"
        cfg, transport, bot, config_path = _orch_cfg(
            tmp_path,
            monkeypatch,
            topics_enabled=False,
            topic_result=None,
            new_project_block=f'\n[new_project]\nroot = "{clone_root}"\n',
        )
        # Pre-create <root>/foo with contents but NEVER register it as a project.
        existing = clone_root / "foo"
        existing.mkdir(parents=True)
        (existing / "keep.txt").write_text("x", encoding="utf-8")

        await handle_project_command(
            cfg,
            _project_msg("/project foo", chat_type="private"),
            args_text="foo",
            topic_store=None,
        )

        assert "not empty" in transport.send_calls[-1]["message"].text
        # Nothing registered; the pre-existing file is left untouched.
        assert "foo" not in read_config(config_path).get("projects", {})
        assert "foo" not in cfg.runtime.project_aliases()
        assert (existing / "keep.txt").read_text(encoding="utf-8") == "x"

    async def test_success_creates_and_binds_topic(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        clone_root = tmp_path / "projects"
        cfg, transport, bot, config_path = _orch_cfg(
            tmp_path,
            monkeypatch,
            topics_enabled=True,
            topic_result=ForumTopic(message_thread_id=11),
            new_project_block=f'\n[new_project]\nroot = "{clone_root}"\n',
        )
        store = _RecordingTopicStore()
        msg = _project_msg("/project foo")

        await handle_project_command(
            cfg,
            msg,
            args_text="foo",
            topic_store=store,  # type: ignore[arg-type]
            resolved_scope="all",
            scope_chat_ids=frozenset({msg.chat_id}),
        )

        raw = read_config(config_path)
        assert "foo" in raw["projects"]
        assert bot.create_topic_calls, "expected create_forum_topic to be called"
        assert len(store.set_context_calls) == 1
        assert store.set_context_calls[0]["context"] == RunContext(
            project="foo", branch=None
        )
        assert store.set_context_calls[0]["thread_id"] == 11
        all_texts = [call["message"].text for call in transport.send_calls]
        assert any("created topic" in text and "foo" in text for text in all_texts)

    async def test_default_engine_written_from_new_project_settings(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        clone_root = tmp_path / "projects"
        cfg, transport, bot, config_path = _orch_cfg(
            tmp_path,
            monkeypatch,
            topics_enabled=False,
            topic_result=None,
            new_project_block=(
                f'\n[new_project]\nroot = "{clone_root}"\ndefault_engine = "claude"\n'
            ),
        )

        await handle_project_command(
            cfg,
            _project_msg("/project foo", chat_type="private"),
            args_text="foo",
            topic_store=None,
        )

        raw = read_config(config_path)
        assert raw["projects"]["foo"]["default_engine"] == "claude"

    async def test_out_of_scope_degrades_to_register_only(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        clone_root = tmp_path / "projects"
        cfg, transport, bot, config_path = _orch_cfg(
            tmp_path,
            monkeypatch,
            topics_enabled=True,
            topic_result=ForumTopic(message_thread_id=7),
            new_project_block=f'\n[new_project]\nroot = "{clone_root}"\n',
        )
        store = _RecordingTopicStore()
        msg = _project_msg("/project foo", chat_type="private")

        await handle_project_command(
            cfg,
            msg,
            args_text="foo",
            topic_store=store,  # type: ignore[arg-type]
            resolved_scope="main",
            scope_chat_ids=frozenset(),  # chat_id deliberately excluded
        )

        assert "foo" in read_config(config_path)["projects"]
        assert bot.create_topic_calls == []
        assert store.set_context_calls == []
        assert "run /topic foo" in transport.send_calls[-1]["message"].text

    async def test_topic_step_exception_degrades_gracefully(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _RaisingTopicBot(FakeBot):
            async def create_forum_topic(
                self, chat_id: int, name: str
            ) -> ForumTopic | None:
                raise RuntimeError("topic api boom")

        clone_root = tmp_path / "projects"
        config_path = _write_config(
            tmp_path, new_project_block=f'\n[new_project]\nroot = "{clone_root}"\n'
        )
        runtime = _build_runtime(config_path, monkeypatch)
        transport = FakeTransport()
        cfg = replace(
            make_cfg(transport),
            runtime=runtime,
            bot=_RaisingTopicBot(),
            topics=TelegramTopicsSettings(enabled=True, scope="all"),
        )
        store = _RecordingTopicStore()
        msg = _project_msg("/project foo")

        await handle_project_command(
            cfg,
            msg,
            args_text="foo",
            topic_store=store,  # type: ignore[arg-type]
            resolved_scope="all",
            scope_chat_ids=frozenset({msg.chat_id}),
        )

        # Registration survives even though the topic step blew up.
        assert "foo" in read_config(config_path)["projects"]
        assert store.set_context_calls == []
        assert "run /topic foo" in transport.send_calls[-1]["message"].text

    async def test_register_oserror_replies_gracefully(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        clone_root = tmp_path / "projects"
        cfg, transport, bot, config_path = _orch_cfg(
            tmp_path,
            monkeypatch,
            topics_enabled=False,
            topic_result=None,
            new_project_block=f'\n[new_project]\nroot = "{clone_root}"\n',
        )

        def _raise(*_a: object, **_k: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(clone_module, "register_project", _raise)

        await handle_project_command(
            cfg,
            _project_msg("/project foo", chat_type="private"),
            args_text="foo",
            topic_store=None,
        )

        # Directory was created; only the config write failed.
        assert (clone_root / "foo").resolve().is_dir()
        assert "failed to write config" in transport.send_calls[-1]["message"].text

    async def test_register_conflict_replies_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # sanitize_alias yields "foo"; force a collision at a different path by
        # having register_project raise the ConfigError it would on mismatch.
        clone_root = tmp_path / "projects"
        cfg, transport, bot, config_path = _orch_cfg(
            tmp_path,
            monkeypatch,
            topics_enabled=False,
            topic_result=None,
            new_project_block=f'\n[new_project]\nroot = "{clone_root}"\n',
        )

        def _raise(*_a: object, **_k: object) -> None:
            raise ConfigError("different path")

        monkeypatch.setattr(clone_module, "register_project", _raise)

        await handle_project_command(
            cfg,
            _project_msg("/project foo", chat_type="private"),
            args_text="foo",
            topic_store=None,
        )

        assert "failed to register project" in transport.send_calls[-1]["message"].text


@pytest.mark.anyio
class TestProjectDispatchTracking:
    """Regression coverage for the synchronous RunningTask registration and
    the `_run_project_command_tracked` cleanup contract — mirrors
    `TestCloneDispatchTracking` in `tests/test_clone_command.py`."""

    async def test_dispatch_registers_running_task_synchronously(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[int] = []

        async def _noop(*_a: object, **_k: object) -> None:
            calls.append(1)

        # Stub the actual handler so the scheduled wrapper is a no-op.
        monkeypatch.setattr(loop_module, "handle_project_command", _noop)

        running_tasks: dict[MessageRef, RunningTask] = {}

        async def _reply(*_a: object, **_k: object) -> None:
            return None

        msg = _project_msg("/project foo")

        async with anyio.create_task_group() as tg:
            ctx = TelegramCommandContext(
                cfg=make_cfg(FakeTransport()),
                msg=msg,
                args_text="foo",
                ambient_context=None,
                topic_store=None,
                chat_prefs=None,
                resolved_scope="all",
                scope_chat_ids=frozenset({msg.chat_id}),
                reply=_reply,
                task_group=tg,
                running_tasks=running_tasks,
            )
            result = _dispatch_builtin_command(ctx=ctx, command_id="project")
            # Synchronous invariant: the guard-entry is registered BEFORE the
            # scheduled coroutine runs (no await between guard check and
            # registration) — this is what closes the TOCTOU window.
            assert result is True
            assert any(ref.channel_id == msg.chat_id for ref in running_tasks)

        # After the task group drains, the no-op wrapper's finally popped it.
        assert not any(ref.channel_id == msg.chat_id for ref in running_tasks)
        assert calls == [1]

    async def test_dispatch_second_concurrent_project_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[int] = []

        async def _noop(*_a: object, **_k: object) -> None:
            calls.append(1)

        monkeypatch.setattr(loop_module, "handle_project_command", _noop)

        # A run is already in flight for chat 123 (different message id).
        running_tasks: dict[MessageRef, RunningTask] = {
            MessageRef(channel_id=123, message_id=99): RunningTask()
        }
        replies: list[dict] = []

        async def _reply(*_a: object, **kw: object) -> None:
            replies.append(kw)

        msg = _project_msg("/project foo")

        async with anyio.create_task_group() as tg:
            ctx = TelegramCommandContext(
                cfg=make_cfg(FakeTransport()),
                msg=msg,
                args_text="foo",
                ambient_context=None,
                topic_store=None,
                chat_prefs=None,
                resolved_scope="all",
                scope_chat_ids=frozenset({msg.chat_id}),
                reply=_reply,
                task_group=tg,
                running_tasks=running_tasks,
            )
            result = _dispatch_builtin_command(ctx=ctx, command_id="project")
            assert result is True

        # Guard fired: no new tracked run started, no extra entry registered,
        # and the "already in progress" reply was sent.
        assert calls == []
        assert len(running_tasks) == 1
        assert any(
            "already in progress" in str(kw.get("text", "")) for kw in replies
        )

    async def test_tracked_wrapper_cleans_up_on_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _noop(*_a: object, **_k: object) -> None:
            return None

        monkeypatch.setattr(loop_module, "handle_project_command", _noop)
        ref = MessageRef(channel_id=123, message_id=1)
        task = RunningTask()
        running_tasks: dict[MessageRef, RunningTask] = {ref: task}
        msg = _project_msg("/project foo")

        await _run_project_command_tracked(
            make_cfg(FakeTransport()),
            msg,
            "foo",
            None,
            running_tasks=running_tasks,
            running_task=task,
            resolved_scope="all",
            scope_chat_ids=frozenset({123}),
        )

        assert ref not in running_tasks
        assert task.done.is_set()

    async def test_tracked_wrapper_cleans_up_on_exception(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _boom(*_a: object, **_k: object) -> None:
            raise RuntimeError("boom")

        monkeypatch.setattr(loop_module, "handle_project_command", _boom)
        ref = MessageRef(channel_id=123, message_id=1)
        task = RunningTask()
        running_tasks: dict[MessageRef, RunningTask] = {ref: task}
        msg = _project_msg("/project foo")

        with pytest.raises(RuntimeError):
            await _run_project_command_tracked(
                make_cfg(FakeTransport()),
                msg,
                "foo",
                None,
                running_tasks=running_tasks,
                running_task=task,
                resolved_scope="all",
                scope_chat_ids=frozenset({123}),
            )

        # finally must still pop the entry and unblock resume waiters.
        assert ref not in running_tasks
        assert task.done.is_set()


# ── [new_project] hot-reload helper (loop.py) ─────────────────────────────


def test_apply_new_project_hot_reload_updates_on_change() -> None:
    cfg = make_cfg(FakeTransport())
    new = NewProjectSettings(root="/srv/new", default_engine="codex")
    changed = _apply_new_project_hot_reload(cfg, new)
    assert changed is True
    assert cfg.new_project is new
    assert cfg.new_project.root == "/srv/new"


def test_apply_new_project_hot_reload_noop_when_equal() -> None:
    cfg = make_cfg(FakeTransport())
    same = NewProjectSettings(
        enabled=cfg.new_project.enabled,
        root=cfg.new_project.root,
        default_engine=cfg.new_project.default_engine,
    )
    changed = _apply_new_project_hot_reload(cfg, same)
    assert changed is False


# ── _load_new_project_settings fallback branches (backend.py) ────────────


def test_load_new_project_settings_defaults_when_no_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        settings_module, "load_settings_if_exists", lambda *_a, **_k: None
    )
    assert _load_new_project_settings() == NewProjectSettings()


def test_load_new_project_settings_falls_back_on_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*_a: object, **_k: object) -> object:
        raise RuntimeError("malformed toml")

    monkeypatch.setattr(settings_module, "load_settings_if_exists", _boom)
    assert _load_new_project_settings() == NewProjectSettings()


def test_load_new_project_settings_reads_new_project_section(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_settings = UntetherSettings.model_validate(
        {
            "transports": {
                "telegram": {
                    "bot_token": "tok",
                    "chat_id": 1,
                    "allow_any_user": True,
                }
            },
            "new_project": {"root": "/srv/x"},
        }
    )
    monkeypatch.setattr(
        settings_module,
        "load_settings_if_exists",
        lambda *_a, **_k: (fake_settings, Path("/x/untether.toml")),
    )
    assert _load_new_project_settings().root == "/srv/x"


# ── Command Menu Integration (Task 5) ────────────────────────────────────


def test_build_bot_commands_includes_project_when_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With include_new_project=True, project command is in menu."""
    from untether.telegram.commands.menu import build_bot_commands

    config_path = _write_config(tmp_path)
    runtime = _build_runtime(config_path, monkeypatch)
    commands = build_bot_commands(
        runtime,
        include_file=False,
        include_topics=False,
        include_clone=False,
        include_new_project=True,
    )
    assert any(cmd["command"] == "project" for cmd in commands)
    assert any(
        cmd["command"] == "project"
        and cmd["description"] == "register a new local project"
        for cmd in commands
    )


def test_build_bot_commands_excludes_project_when_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With include_new_project=False, project command is absent from menu."""
    from untether.telegram.commands.menu import build_bot_commands

    config_path = _write_config(tmp_path)
    runtime = _build_runtime(config_path, monkeypatch)
    commands = build_bot_commands(
        runtime,
        include_file=False,
        include_topics=False,
        include_clone=False,
        include_new_project=False,
    )
    assert not any(cmd["command"] == "project" for cmd in commands)


def test_build_bot_commands_no_duplicate_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No duplicate project command when already seen (deduplication works)."""
    from untether.telegram.commands.menu import build_bot_commands

    config_path = _write_config(tmp_path)
    runtime = _build_runtime(config_path, monkeypatch)
    # Build with include_new_project=True twice; should have only one "project"
    commands = build_bot_commands(
        runtime,
        include_file=False,
        include_topics=False,
        include_clone=False,
        include_new_project=True,
    )
    project_commands = [cmd for cmd in commands if cmd["command"] == "project"]
    assert len(project_commands) == 1
