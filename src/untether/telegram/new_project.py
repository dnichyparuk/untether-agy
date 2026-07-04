"""Command handler for the ``/project`` command — register a new local project.

Unlike ``/clone`` (which fetches a remote repo via ``git clone``),
``/project <name>`` registers a brand-new *empty* local directory as an
Untether project: no subprocess, no network. It is the sanitise -> collide ->
mkdir -> register -> topic-step tail of the clone flow, minus the git bits.

The pure helpers and orchestration mirror :mod:`untether.telegram.clone`:

- :func:`untether.telegram.clone.sanitize_alias` turns the user-supplied name
  into an :data:`untether.ids.ID_PATTERN`-valid alias (raising on empty /
  punctuation-only input).
- :func:`resolve_project_path` confines the destination under the configured
  ``[new_project] root`` (``resolve()`` + ``relative_to``), exactly as
  ``clone.resolve_destination`` does for clones.
- :func:`untether.telegram.clone.register_project` persists the
  ``[projects.<alias>]`` block and applies it to the live runtime.
- :func:`untether.telegram.clone.create_and_bind_topic` runs the gated,
  best-effort forum-topic create+bind shared with ``/clone``.

``[new_project]`` settings live on the top-level :class:`UntetherSettings`
(like ``[clone]``), so they are read fresh from ``cfg.runtime.config_path``
rather than off the transport config.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ..config import ConfigError
from ..context import RunContext
from ..logging import get_logger
from ..settings import NewProjectSettings, load_settings
from . import clone
from .commands.reply import make_reply
from .topic_state import TopicStateStore
from .types import TelegramIncomingMessage

if TYPE_CHECKING:
    from .bridge import TelegramBridgeConfig

logger = get_logger(__name__)

_NEW_PROJECT_USAGE = "usage: /project <name>"


def resolve_project_path(name_alias: str, root: Path) -> Path:
    """Resolve the on-disk directory for a new project *name_alias* under *root*.

    *root* is the configured (or default) ``[new_project] root``; it is
    expanded (``~``) and resolved. The final destination (``<root>/<alias>``)
    is resolved and MUST sit under *root* — a *name_alias* that walks out via
    ``..``, or a symlinked ``root`` entry that resolves elsewhere, is rejected
    with :class:`ValueError`. In normal use *name_alias* is already sanitised
    to ``[a-z0-9_]`` (no separators, no traversal), so this check is a
    defensive backstop mirroring ``clone.resolve_destination``.
    """
    root_expanded = root.expanduser()
    root_resolved = root_expanded.resolve()

    destination = (root_expanded / name_alias).resolve()

    try:
        destination.relative_to(root_resolved)
    except ValueError:
        raise ValueError(
            f"destination {destination} is outside project root {root_resolved}"
        ) from None

    return destination


def _load_new_project_settings(config_path: Path) -> NewProjectSettings:
    """Read ``[new_project]`` settings from *config_path*.

    ``[new_project]`` lives on the top-level :class:`UntetherSettings`, not on
    ``[transports.telegram]``, so it isn't carried on the
    :class:`TelegramBridgeConfig`. Load it from the config file the runtime was
    built against (already validated at startup, so this re-read is cheap and
    can't introduce a new failure the runtime didn't already survive).
    """
    settings, _ = load_settings(config_path)
    return settings.new_project


def _register_only_reply(alias: str) -> str:
    """Reply text when register succeeded but no topic was mapped."""
    return (
        f"registered `{alias}`; topics disabled/not a forum here — "
        f"run /topic {alias} in a forum to map a topic."
    )


async def handle_project_command(
    cfg: TelegramBridgeConfig,
    msg: TelegramIncomingMessage,
    args_text: str,
    topic_store: TopicStateStore | None,
    *,
    resolved_scope: str | None = None,
    scope_chat_ids: frozenset[int] | None = None,
) -> None:
    """Orchestrate ``/project``: sanitise -> collide -> mkdir -> register -> topic.

    Registers a new empty local project directory under the configured
    ``[new_project] root`` and persists a ``[projects.<alias>]`` block. Only
    the final topic step is gated on ``cfg.topics.enabled`` / an available
    *topic_store* / the chat being an in-scope forum, and it NEVER hard-fails
    the command — a missing or failing topic degrades to a register-only reply
    (KD4), mirroring ``/clone``.

    Unlike ``/clone``, an existing alias is a hard refusal (no numeric-suffix
    dedup): the user picked the name, so a collision names the existing
    project's path and asks for a different name without writing config or
    creating a directory.
    """
    reply = make_reply(cfg, msg)

    config_path = cfg.runtime.config_path
    if config_path is None:
        await reply(text="cannot register project: no config path available.")
        return

    np_cfg = _load_new_project_settings(config_path)
    if not np_cfg.enabled:
        await reply(text="/project is disabled in this deployment.")
        return

    name = args_text.strip()
    if not name:
        await reply(text=_NEW_PROJECT_USAGE)
        return

    try:
        alias = clone.sanitize_alias(name)
    except ValueError:
        await reply(
            text=f"error: cannot derive a valid project name from `{name}`.\n"
            f"{_NEW_PROJECT_USAGE}"
        )
        return

    if alias in set(cfg.runtime.project_aliases()):
        existing_path = cfg.runtime.resolve_run_cwd(RunContext(project=alias))
        await reply(
            text=f"error: project `{alias}` already exists (path: {existing_path}). "
            f"Pick a different name."
        )
        return

    root = Path(np_cfg.root)
    try:
        dest = resolve_project_path(alias, root)
    except ValueError as exc:
        await reply(text=f"error: {exc}")
        return

    try:
        dest.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        await reply(text=f"failed to create project directory {dest}: {exc}")
        return

    try:
        clone.register_project(
            config_path,
            cfg.runtime,
            alias=alias,
            path=dest,
            default_engine=np_cfg.default_engine,
        )
    except ConfigError as exc:
        await reply(text=f"created {dest} but failed to register project: {exc}")
        return
    except OSError as exc:
        # read_config/write_config touch the filesystem; an IO error (e.g.
        # permission denied, disk full) surfaces as OSError rather than
        # ConfigError. Reply gracefully — the directory already exists, so
        # this is a register-only failure, not a handler crash.
        await reply(text=f"created {dest} but failed to write config: {exc}")
        return

    # TOPIC STEP — gated; best-effort; never hard-fails the command.
    context = RunContext(project=alias, branch=None)
    title = await clone.create_and_bind_topic(
        cfg,
        msg,
        context,
        topic_store,
        resolved_scope=resolved_scope,
        scope_chat_ids=scope_chat_ids,
    )
    if title is None:
        await reply(text=_register_only_reply(alias))
        return
    await reply(text=f"registered `{alias}`; created topic `{title}`.")
