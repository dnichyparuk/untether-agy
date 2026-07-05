"""Command handler for ``/printtimeout`` — per-project Antigravity print timeout.

``/printtimeout`` reads and writes the ``[projects.<alias>].print_timeout``
override. Antigravity (``agy``) forwards this string to its ``--print-timeout``
flag (Go ``time.ParseDuration`` syntax) so long headless runs aren't cut off
mid-run by ``agy``'s own 5-minute default.

Grammar::

    /printtimeout            -> show the effective value + global default
    /printtimeout 30m        -> set [projects.<alias>].print_timeout = "30m"
    /printtimeout 1h30m      -> multi-unit Go duration, accepted
    /printtimeout clear      -> remove the project override

The command is modelled on ``/model`` (always available, no section-enabled
gate) rather than on ``/clone``. It resolves the project from the ambient
:class:`~untether.context.RunContext`; outside a project topic it degrades to a
plain hint without writing config.

The persist sequence mirrors
:func:`untether.telegram.clone.register_project` verbatim: ``read_config`` ->
mutate ``config["projects"][alias]`` -> :func:`validate_settings_data` ->
``write_config`` -> ``build_runtime_spec(...).apply(...)`` so the new value is
immediately resolvable without waiting on the config watcher.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..config import ConfigError, read_config, write_config
from ..context import RunContext
from ..logging import get_logger
from ..runners.antigravity import _DEFAULT_PRINT_TIMEOUT
from ..runners.antigravity import ENGINE as _ANTIGRAVITY_ENGINE
from ..runtime_loader import build_runtime_spec
from ..settings import validate_settings_data
from .chat_prefs import ChatPrefsStore
from .commands.overrides import require_admin_or_private
from .commands.reply import make_reply
from .topic_state import TopicStateStore
from .types import TelegramIncomingMessage

if TYPE_CHECKING:
    from .bridge import TelegramBridgeConfig

logger = get_logger(__name__)

PRINT_TIMEOUT_USAGE = (
    "usage: `/printtimeout`, `/printtimeout <duration>` (e.g. `30m`, `1h30m`), "
    "or `/printtimeout clear`"
)

# Lenient Go-duration validator. `agy` forwards the raw string to Go's
# time.ParseDuration and validates at run time, so this only needs to reject
# obvious garbage (e.g. the word "banana"), not fully reimplement the Go
# duration grammar. Accepts multi-unit values (1h30m), fractional values
# (0.5h), and the ASCII spelling "micros" alongside the real Go unit spellings.
_DURATION_RE = re.compile(
    r"^\d+(\.\d+)?(ns|us|micros|ms|s|m|h)([\d.]+(ns|us|micros|ms|s|m|h))*$"
)


def parse_duration(value: str) -> bool:
    """Return True if *value* is a plausible Go-style duration string.

    Lenient by design (see module docstring): rejects obvious garbage while
    accepting multi-unit (``1h30m``) and fractional (``0.5h``) durations. The
    authoritative validation happens inside ``agy`` at run time.
    """
    return bool(_DURATION_RE.match(value.strip()))


def _out_of_topic_reply() -> str:
    return (
        "⚠️ Run `/printtimeout` inside a project topic — it sets the "
        "per-project Antigravity (`agy`) print timeout."
    )


async def handle_print_timeout_command(
    cfg: TelegramBridgeConfig,
    msg: TelegramIncomingMessage,
    args_text: str,
    ambient_context: RunContext | None,
    topic_store: TopicStateStore | None,
    chat_prefs: ChatPrefsStore | None,
    *,
    resolved_scope: str | None = None,
    scope_chat_ids: frozenset[int] | None = None,
) -> None:
    """Orchestrate ``/printtimeout``: resolve project -> show / set / clear.

    Degrades (no config write) when there is no ambient project. Otherwise the
    show branch reports the effective value and global default, the set branch
    validates and persists the duration, and the clear branch removes the
    project override — all via the same read -> validate -> write -> apply
    recipe used by ``register_project``.
    """
    reply = make_reply(cfg, msg)

    # Guard the WHOLE ambient object: degrade when there is no ambient context
    # or no project bound to it. This is NOT a topic-setup error, so we do not
    # use `_topics_command_error`; a plain hint is the right degrade path.
    if ambient_context is None or ambient_context.project is None:
        await reply(text=_out_of_topic_reply())
        return

    project = cfg.runtime.project_for_alias(ambient_context.project)
    if project is None:
        await reply(text=_out_of_topic_reply())
        return

    config_path = cfg.runtime.config_path
    if config_path is None:
        await reply(text="cannot update print_timeout: no config path available.")
        return

    # Global default = [antigravity].print_timeout, falling back to the runner's
    # built-in default. Read from disk so it reflects the persisted config.
    try:
        raw = read_config(config_path)
    except (OSError, ConfigError) as exc:
        await reply(text=f"failed to read config: {exc}")
        return
    antigravity_section = raw.get("antigravity")
    global_default = _DEFAULT_PRINT_TIMEOUT
    if isinstance(antigravity_section, dict):
        candidate = antigravity_section.get("print_timeout")
        if isinstance(candidate, str) and candidate:
            global_default = candidate

    tokens = args_text.split()
    action = tokens[0].lower() if tokens else "show"
    alias = project.alias

    # ── show ─────────────────────────────────────────────────────────────
    if not tokens:
        override = project.print_timeout
        effective = override or global_default
        source = "project override" if override else "global default (no override set)"
        await reply(
            text=(
                f"ℹ️ project **{alias}** print_timeout: **{effective}** ({source})\n"
                f"global default: **{global_default}**"
            )
        )
        return

    # Mutating branches (clear/set) require admin-or-private, matching /model
    # and /reasoning — the closest analogues that also persist per-project
    # config overrides. The read-only "show" branch above is not gated.
    if not await require_admin_or_private(
        cfg,
        msg,
        missing_sender="cannot verify sender for print_timeout overrides.",
        failed_member="failed to verify print_timeout override permissions.",
        denied="changing print_timeout overrides is restricted to group admins.",
    ):
        return

    # ── clear ────────────────────────────────────────────────────────────
    if action == "clear":
        try:
            _persist(config_path, cfg, alias=alias, value=None)
        except ConfigError as exc:
            await reply(text=f"failed to clear print_timeout: {exc}")
            return
        except OSError as exc:
            await reply(text=f"failed to write config: {exc}")
            return
        logger.info(
            "print_timeout.cleared", chat_id=msg.chat_id, project=alias
        )
        await reply(
            text=(
                f"♻️ project **{alias}** print_timeout override **removed**; "
                f"global default **{global_default}** now in effect."
            )
        )
        return

    # ── set ──────────────────────────────────────────────────────────────
    value = tokens[0].strip()
    if len(tokens) > 1 or not parse_duration(value):
        await reply(text=f"invalid duration `{value}`.\n{PRINT_TIMEOUT_USAGE}")
        return

    try:
        _persist(config_path, cfg, alias=alias, value=value)
    except ConfigError as exc:
        await reply(text=f"failed to set print_timeout: {exc}")
        return
    except OSError as exc:
        await reply(text=f"failed to write config: {exc}")
        return

    logger.info(
        "print_timeout.set", chat_id=msg.chat_id, project=alias, value=value
    )

    engine = cfg.runtime.resolve_engine(
        engine_override=None, context=ambient_context
    )
    lines = [
        f"✅ project **{alias}** print_timeout **set to** **{value}**.",
        "This affects Antigravity (`agy`) runs.",
    ]
    if engine != _ANTIGRAVITY_ENGINE:
        lines.append(
            f"Note: `{engine}` is this project's engine — `print_timeout` "
            f"currently only affects Antigravity (`agy`) runs."
        )
    await reply(text="\n".join(lines))


def _persist(
    config_path,
    cfg: TelegramBridgeConfig,
    *,
    alias: str,
    value: str | None,
) -> None:
    """Read -> mutate -> validate -> write -> apply, per ``register_project``.

    Sets ``config["projects"][alias]["print_timeout"]`` to *value*, or pops the
    key when *value* is ``None`` (clear). Validation runs before the write, so a
    bad document never lands on disk; the freshly-validated settings are applied
    to the live runtime so the change is immediately resolvable.
    """
    config = read_config(config_path)
    projects = config.setdefault("projects", {})
    if not isinstance(projects, dict):
        raise ConfigError(f"Invalid `projects` in {config_path}; expected a table.")

    entry = projects.get(alias)
    if not isinstance(entry, dict):
        raise ConfigError(
            f"Project {alias!r} is not defined in {config_path}; cannot update "
            f"print_timeout."
        )

    if value is None:
        entry.pop("print_timeout", None)
    else:
        entry["print_timeout"] = value

    settings = validate_settings_data(config, config_path=config_path)
    write_config(config, config_path)

    spec = build_runtime_spec(settings=settings, config_path=config_path)
    spec.apply(cfg.runtime, config_path=config_path)
