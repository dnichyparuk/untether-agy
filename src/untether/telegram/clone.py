"""Helpers and subprocess runner for the `/clone` command.

The pure helpers (`RepoRef`, `parse_repo_url`, `derive_alias`,
`resolve_destination`, `host_is_allowed`) do no I/O — no subprocess, no
filesystem writes, no network calls. They only parse/validate a repo URL,
derive a project alias, and resolve a destination path under the configured
clone root. `run_git_clone` spawns the actual `git clone` subprocess;
`register_project` persists the resulting project to config and applies it
to the live runtime. `handle_clone_command` (the remaining command-wiring
piece) lands in a later task.

Grammar handled by :func:`parse_repo_url` (see fact sheet for the full
`/clone` message grammar):

- ``https://github.com/OWNER/REPO`` or ``https://github.com/OWNER/REPO.git``
- ``git@github.com:OWNER/REPO`` or ``git@github.com:OWNER/REPO.git``

Both forms are validated against a caller-supplied allowlist of hosts
(``clone.allowed_hosts``) via :func:`host_is_allowed` — this mirrors the
scheme/host validation pattern in ``triggers/ssrf.py``, though git URLs
don't go through an HTTP client so the SSRF helpers themselves don't apply
here (no DNS resolution / redirect following involved in a `git clone`
argument check).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import anyio

from ..config import ConfigError, read_config, write_config
from ..context import RunContext
from ..ids import ID_PATTERN
from ..logging import get_logger
from ..markdown import MarkdownParts
from ..runner import _stderr_excerpt
from ..runtime_loader import build_runtime_spec
from ..settings import validate_settings_data
from ..transport import RenderedMessage, SendOptions
from ..transport_runtime import TransportRuntime
from ..utils.env_policy import filtered_env
from .commands.reply import make_reply
from .render import prepare_telegram
from .topic_state import TopicStateStore
from .topics import _topic_title, _topics_command_error
from .types import TelegramIncomingMessage

if TYPE_CHECKING:
    from .bridge import TelegramBridgeConfig

logger = get_logger(__name__)

_ID_RE = re.compile(ID_PATTERN)

# Maximum project-alias length, matching the ``{1,32}`` bound baked into
# :data:`untether.ids.ID_PATTERN`. Kept as a named constant so the alias
# truncation and dedup-suffix arithmetic in :func:`derive_alias` stay in sync.
_MAX_ALIAS_LENGTH = 32

# A path segment (owner or repo name) must not contain a slash (that would
# smuggle extra path components past the regex split) and must otherwise
# look like a normal GitHub-style segment: letters, digits, dot, underscore,
# hyphen. This also rejects the literal traversal segments "." and "..".
_SAFE_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")

# https://<host>/<owner>/<repo>[.git][/]
_HTTPS_RE = re.compile(
    r"^https://(?P<host>[^/@\s]+)/(?P<owner>[^/\s]+)/(?P<repo>[^/\s]+?)(?:\.git)?/?$"
)
# git@<host>:<owner>/<repo>[.git]
_SCP_RE = re.compile(
    r"^git@(?P<host>[^:/\s]+):(?P<owner>[^/\s]+)/(?P<repo>[^/\s]+?)(?:\.git)?/?$"
)


@dataclass(frozen=True)
class RepoRef:
    """A parsed, validated reference to a remote git repository."""

    host: str
    owner: str
    repo: str
    url: str
    scheme: str  # "https" | "scp"


def host_is_allowed(host: str, allowed: tuple[str, ...]) -> bool:
    """Case-insensitive membership check against `clone.allowed_hosts`."""
    host_lower = host.lower()
    return any(host_lower == candidate.lower() for candidate in allowed)


def _validate_segment(segment: str, *, label: str) -> str:
    if segment in {"", ".", ".."}:
        raise ValueError(f"invalid {label}: {segment!r}")
    if not _SAFE_SEGMENT_RE.match(segment):
        raise ValueError(f"invalid {label}: {segment!r}")
    return segment


def parse_repo_url(
    url: str, *, allowed_hosts: tuple[str, ...] = ("github.com",)
) -> RepoRef:
    """Parse and validate an https or scp-style GitHub repo URL.

    Raises :class:`ValueError` for anything that isn't a well-formed URL of
    one of the two supported shapes, whose host is in *allowed_hosts*, and
    whose owner/repo segments are safe path components (no traversal, no
    embedded slashes).
    """
    candidate = url.strip()
    if not candidate:
        raise ValueError("empty repo URL")

    match = _HTTPS_RE.match(candidate)
    if match is not None:
        host = match.group("host")
        owner = _validate_segment(match.group("owner"), label="owner")
        repo = _validate_segment(match.group("repo"), label="repo")
        if not host_is_allowed(host, allowed_hosts):
            raise ValueError(f"host not allowed: {host}")
        normalised = f"https://{host}/{owner}/{repo}.git"
        return RepoRef(
            host=host, owner=owner, repo=repo, url=normalised, scheme="https"
        )

    match = _SCP_RE.match(candidate)
    if match is not None:
        host = match.group("host")
        owner = _validate_segment(match.group("owner"), label="owner")
        repo = _validate_segment(match.group("repo"), label="repo")
        if not host_is_allowed(host, allowed_hosts):
            raise ValueError(f"host not allowed: {host}")
        normalised = f"git@{host}:{owner}/{repo}.git"
        return RepoRef(host=host, owner=owner, repo=repo, url=normalised, scheme="scp")

    raise ValueError(f"unsupported or unsafe repo URL: {url!r}")


def sanitize_alias(name: str) -> str:
    """Sanitise *name* to :data:`untether.ids.ID_PATTERN` (``^[a-z0-9_]{1,32}$``).

    Lowercases *name*, replaces every character outside ``[a-z0-9_]`` with
    ``_``, strips leading/trailing underscores, and truncates to 32 chars.
    Raises :class:`ValueError` when nothing valid remains (empty input or an
    input made entirely of punctuation / separators) — the caller decides how
    to handle that: :func:`derive_alias` falls back to ``"repo"``, while
    ``new_project.handle_project_command`` surfaces the rejection to the user.
    """
    lowered = name.lower()
    sanitised = re.sub(r"[^a-z0-9_]", "_", lowered)
    sanitised = sanitised.strip("_")[:_MAX_ALIAS_LENGTH]
    if not sanitised or not _ID_RE.fullmatch(sanitised):
        raise ValueError(f"cannot derive a valid project alias from {name!r}")
    return sanitised


def derive_alias(ref: RepoRef, existing: set[str]) -> str:
    """Derive a project alias from a repo name, deduped against *existing*.

    The alias is sanitised via :func:`sanitize_alias` (lowercased, non-matching
    characters replaced with ``_``, leading/trailing underscores stripped,
    truncated to 32 chars), and falls back to ``"repo"`` if sanitisation
    rejects the name (nothing valid left). If the resulting alias collides with
    one in *existing*, a numeric suffix (``_1``, ``_2``, ...) is appended (and
    the base is re-truncated so the 32-char cap still holds).
    """
    try:
        base = sanitize_alias(ref.repo)
    except ValueError:
        base = "repo"

    if base not in existing:
        return base

    n = 1
    while True:
        suffix = f"_{n}"
        alias = f"{base[: _MAX_ALIAS_LENGTH - len(suffix)]}{suffix}"
        if alias not in existing and _ID_RE.fullmatch(alias):
            return alias
        n += 1


def resolve_destination(ref: RepoRef, root: Path, override: Path | None) -> Path:
    """Resolve the on-disk clone destination for *ref*.

    *root* is the configured (or built-in default) clone root; *override*
    is the optional ``--dir`` argument naming a destination parent to use
    instead of *root*. Both are expanded (``~``) and the final destination
    (``<parent>/<repo>``) is resolved and MUST sit under *root* — a
    relative override that walks out via ``..``, an absolute override
    pointing elsewhere (e.g. ``/etc``), or a symlink that resolves outside
    *root* are all rejected with :class:`ValueError`. This containment
    check is what keeps ``--dir`` safe to expose to any allowlisted
    Telegram user: it can only pick a subdirectory of the operator's
    configured clone root, never an arbitrary filesystem path.
    """
    root_expanded = root.expanduser()
    root_resolved = root_expanded.resolve()

    if override is not None:
        override_expanded = override.expanduser()
        parent = (
            override_expanded
            if override_expanded.is_absolute()
            else root_expanded / override_expanded
        )
    else:
        parent = root_expanded

    destination = (parent / ref.repo).resolve()

    try:
        destination.relative_to(root_resolved)
    except ValueError:
        raise ValueError(
            f"destination {destination} is outside clone root {root_resolved}"
        ) from None

    return destination


# Backstop timeout for the `git clone` subprocess. A shallow clone of any
# reasonably-sized repo finishes well under this; it exists so a hung
# network call (or a git credential helper that somehow still blocks despite
# GIT_TERMINAL_PROMPT=0 / GIT_SSH_COMMAND) can't wedge the event loop
# forever.
_CLONE_TIMEOUT_SECONDS = 120.0

# Sentinel returncode used when `git` was never invoked (e.g. the
# destination already exists and is non-empty, or the clone timed out
# before the subprocess reported an exit code).
_NO_SUBPROCESS_RETURNCODE = -1


@dataclass(frozen=True)
class CloneOutcome:
    """Result of a :func:`run_git_clone` attempt."""

    ok: bool
    dest: Path
    branch: str | None
    returncode: int
    stderr_excerpt: str


def _build_clone_argv(
    ref: RepoRef, dest: Path, *, branch: str | None, depth: int | None
) -> list[str]:
    """Render the `git clone` argv for *ref* per the clone-command grammar.

    - ``depth`` (when a positive int) adds ``--depth <n> --single-branch``.
    - ``branch`` (when given) adds ``--branch <branch>``.
    - ``--`` terminates option parsing before the positional url/dest, so a
      malicious branch/url value can't be interpreted as a flag.
    """
    argv = ["git", "clone"]
    if depth is not None and depth > 0:
        argv.extend(["--depth", str(depth), "--single-branch"])
    if branch:
        argv.extend(["--branch", branch])
    argv.extend(["--", ref.url, str(dest)])
    return argv


def _destination_conflict(dest: Path) -> str | None:
    """Return a refusal reason if *dest* is a non-empty existing path.

    `git clone` itself refuses to clone into an existing non-empty
    directory (or onto an existing file) — this check short-circuits
    before spawning `git` at all (KD3).
    """
    if not dest.exists():
        return None
    if not dest.is_dir():
        return f"destination already exists and is not a directory: {dest}"
    if any(dest.iterdir()):
        return f"destination already exists and is not empty: {dest}"
    return None


async def run_git_clone(
    repo: RepoRef,
    dest: Path,
    *,
    branch: str | None,
    depth: int | None,
) -> CloneOutcome:
    """Clone *repo* into *dest* via a `git clone` subprocess.

    Runs under anyio so the event loop isn't blocked, with
    :data:`_CLONE_TIMEOUT_SECONDS` as a hard backstop. The subprocess env is
    :func:`filtered_env` (no bot token or unrelated engine credentials leak
    into the child) plus ``GIT_TERMINAL_PROMPT=0`` and
    ``GIT_SSH_COMMAND="ssh -o BatchMode=yes"`` so both https- and
    ssh-auth-required clones fail fast instead of hanging on a
    password/passphrase/known-hosts prompt. Host git credentials (existing
    SSH keys, credential helpers) are otherwise used as-is — v1 does no
    token injection.

    Refuses (without running git) when *dest* already exists and is a
    non-empty directory, or an existing non-directory path.
    """
    conflict = _destination_conflict(dest)
    if conflict is not None:
        return CloneOutcome(
            ok=False,
            dest=dest,
            branch=branch,
            returncode=_NO_SUBPROCESS_RETURNCODE,
            stderr_excerpt=conflict,
        )

    argv = _build_clone_argv(repo, dest, branch=branch, depth=depth)

    env = filtered_env()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes"

    try:
        with anyio.fail_after(_CLONE_TIMEOUT_SECONDS):
            result = await anyio.run_process(argv, env=env, check=False)
    except TimeoutError:
        return CloneOutcome(
            ok=False,
            dest=dest,
            branch=branch,
            returncode=_NO_SUBPROCESS_RETURNCODE,
            stderr_excerpt=f"git clone timed out after {_CLONE_TIMEOUT_SECONDS:.0f}s",
        )

    stderr_text = result.stderr.decode("utf-8", errors="replace")
    excerpt = _stderr_excerpt(stderr_text.splitlines()) or ""
    return CloneOutcome(
        ok=result.returncode == 0,
        dest=dest,
        branch=branch,
        returncode=result.returncode,
        stderr_excerpt=excerpt,
    )


def register_project(
    config_path: Path,
    runtime: TransportRuntime,
    *,
    alias: str,
    path: Path,
    default_engine: str | None,
) -> None:
    """Persist a ``[projects.<alias>]`` block and apply it to the live runtime.

    Mirrors the read -> mutate -> validate -> write sequence used by
    ``cli/config.py::config_set`` (around line 211-257): read the raw TOML
    dict, mutate ``config["projects"][alias]``, validate the whole document
    via :func:`untether.settings.validate_settings_data` (raises
    :class:`ConfigError` on anything invalid, so a bad write never lands on
    disk), then persist via :func:`untether.config.write_config`.

    After a successful write, a fresh :class:`~untether.runtime_loader.RuntimeSpec`
    is built from the just-validated settings and applied directly to
    *runtime* — the same construction ``config_watch.py`` uses on a file
    change, just invoked synchronously here so the new project is
    immediately resolvable (KD2) without waiting on ``watch_config`` (which
    may be disabled, or simply hasn't noticed the write yet).

    Only ``path`` and (optionally) ``default_engine`` are written in v1;
    ``worktrees_dir``/``worktree_base``/``chat_id`` are left at their
    :class:`untether.settings.ProjectSettings` defaults.

    Alias deduplication against already-registered projects happens
    upstream, in :func:`derive_alias`. This function still guards
    defensively: if *alias* already names a project whose recorded ``path``
    differs from *path*, it raises :class:`ConfigError` rather than
    silently overwriting a different project's registration.
    """
    config = read_config(config_path)
    projects = config.setdefault("projects", {})
    if not isinstance(projects, dict):
        raise ConfigError(f"Invalid `projects` in {config_path}; expected a table.")

    resolved_path = str(path)
    existing = projects.get(alias)
    if isinstance(existing, dict) and existing.get("path") not in (None, resolved_path):
        raise ConfigError(
            f"Refusing to register project {alias!r}: an existing project at "
            f"this alias has a different path ({existing.get('path')!r} != "
            f"{resolved_path!r})."
        )

    entry: dict[str, object] = {"path": resolved_path}
    if default_engine is not None:
        entry["default_engine"] = default_engine
    projects[alias] = entry

    settings = validate_settings_data(config, config_path=config_path)
    write_config(config, config_path)

    spec = build_runtime_spec(settings=settings, config_path=config_path)
    spec.apply(runtime, config_path=config_path)


_CLONE_USAGE = "usage: /clone <repo-url> [--dir <path>] [@<branch>]"


def _parse_clone_args(
    args_text: str,
) -> tuple[str | None, str | None, str | None, str | None]:
    """Parse ``/clone`` args into ``(url, dir_override, branch, error)``.

    Grammar (see fact sheet): ``/clone <repo-url> [--dir <path>] [@<branch>]``.
    Tokens are split on whitespace; ``--dir <path>`` and a leading/trailing
    ``@<branch>`` may appear in any order around the single positional URL.
    On any parse problem *error* is a human-readable string and the other
    three are ``None``.
    """
    tokens = args_text.split()
    if not tokens:
        return None, None, None, "missing repo URL"

    url: str | None = None
    dir_override: str | None = None
    branch: str | None = None

    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--dir":
            if i + 1 >= len(tokens):
                return None, None, None, "--dir requires a path"
            dir_override = tokens[i + 1]
            i += 2
            continue
        if tok.startswith("@"):
            branch = tok[1:]
            if not branch:
                return None, None, None, "empty branch after @"
            i += 1
            continue
        if url is None:
            url = tok
            i += 1
            continue
        return None, None, None, f"unexpected argument: {tok}"

    if url is None:
        return None, None, None, "missing repo URL"
    return url, dir_override, branch, None


def _register_only_reply(alias: str, branch: str | None) -> str:
    """Reply text when the clone+register succeeded but no topic was mapped."""
    topic_hint = f"/topic {alias} @{branch}" if branch else f"/topic {alias}"
    return (
        f"cloned + registered `{alias}`; topics disabled/not a forum here — "
        f"run {topic_hint} in a forum to map a topic."
    )


async def handle_clone_command(
    cfg: TelegramBridgeConfig,
    msg: TelegramIncomingMessage,
    args_text: str,
    topic_store: TopicStateStore | None,
    *,
    resolved_scope: str | None = None,
    scope_chat_ids: frozenset[int] | None = None,
) -> None:
    """Orchestrate ``/clone``: parse -> git clone -> register -> topic step.

    The clone and project registration ALWAYS run (routed as a top-level
    branch in ``loop.py``, outside the topics-enabled guard) so ``/clone``
    works in private and non-forum chats. Only the final topic step is gated
    on ``cfg.topics.enabled``, an available *topic_store*, and the chat being
    a topics-enabled forum in scope. A missing or failing topic NEVER
    hard-fails the command — it degrades to a register-only reply (KD4).
    """
    reply = make_reply(cfg, msg)
    clone_cfg = cfg.clone

    if not clone_cfg.enabled:
        await reply(text="/clone is disabled in this deployment.")
        return

    url, dir_override, branch, parse_error = _parse_clone_args(args_text)
    if parse_error is not None or url is None:
        detail = parse_error or "missing repo URL"
        await reply(text=f"error: {detail}\n{_CLONE_USAGE}")
        return

    allowed_hosts = tuple(clone_cfg.allowed_hosts)
    try:
        ref = parse_repo_url(url, allowed_hosts=allowed_hosts)
    except ValueError as exc:
        await reply(text=f"error: {exc}\n{_CLONE_USAGE}")
        return

    config_path = cfg.runtime.config_path
    if config_path is None:
        await reply(text="cannot register project: no config path available.")
        return

    root = Path(clone_cfg.root)
    override_path = Path(dir_override) if dir_override is not None else None
    try:
        dest = resolve_destination(ref, root, override_path)
    except ValueError as exc:
        await reply(text=f"error: {exc}")
        return

    alias = derive_alias(ref, set(cfg.runtime.project_aliases()))

    await reply(text=f"cloning `{ref.owner}/{ref.repo}`...")

    try:
        outcome = await run_git_clone(ref, dest, branch=branch, depth=clone_cfg.depth)
    except OSError as exc:
        # e.g. the `git` binary is missing (FileNotFoundError) or the spawn
        # otherwise fails at the OS level. run_git_clone converts a non-zero
        # exit or timeout into an ``ok=False`` outcome, but a failure to spawn
        # the subprocess at all raises OSError — catch it here so /clone
        # replies gracefully instead of crashing the handler task.
        await reply(text=f"git clone failed: {exc}")
        return
    if not outcome.ok:
        await reply(text=f"git clone failed:\n{outcome.stderr_excerpt}")
        return

    try:
        register_project(
            config_path,
            cfg.runtime,
            alias=alias,
            path=dest,
            default_engine=clone_cfg.default_engine,
        )
    except ConfigError as exc:
        await reply(text=f"cloned to {dest} but failed to register project: {exc}")
        return
    except OSError as exc:
        # read_config/write_config touch the filesystem; an IO error (e.g.
        # permission denied, disk full) surfaces as OSError rather than
        # ConfigError. Reply gracefully — the clone already succeeded, so
        # this is a register-only failure, not a handler crash.
        await reply(text=f"cloned to {dest} but failed to write config: {exc}")
        return

    # TOPIC STEP — gated; best-effort; never hard-fails the command.
    context = RunContext(project=alias, branch=branch)
    title = await create_and_bind_topic(
        cfg,
        msg,
        context,
        topic_store,
        resolved_scope=resolved_scope,
        scope_chat_ids=scope_chat_ids,
    )
    if title is None:
        await reply(text=_register_only_reply(alias, branch))
        return
    await reply(text=f"cloned + registered `{alias}`; created topic `{title}`.")


async def create_and_bind_topic(
    cfg: TelegramBridgeConfig,
    msg: TelegramIncomingMessage,
    context: RunContext,
    topic_store: TopicStateStore | None,
    *,
    resolved_scope: str | None,
    scope_chat_ids: frozenset[int] | None,
) -> str | None:
    """Create a forum topic for *context* and bind it, best-effort.

    Shared tail of ``/clone`` and ``/project``. The step is gated on
    ``cfg.topics.enabled``, an available *topic_store*, and the chat being a
    topics-enabled forum in scope; if any gate fails it returns ``None``
    without touching the Telegram API. On success it pins *context* via
    ``topic_store.set_context`` and sends the "topic bound to ..." confirmation
    into the freshly-created thread, then returns the created topic title.

    It NEVER raises (KD4): any failure from ``create_forum_topic`` /
    ``set_context`` — Telegram API error, network error, store I/O error, or a
    ``create_forum_topic`` that returns ``None`` because the chat isn't
    actually a forum — degrades to a ``None`` return so the caller can fall
    back to a register-only reply. The caller owns the top-level success reply
    (``/clone`` and ``/project`` phrase their own confirmation), so this helper
    deliberately does not send it.
    """
    if (
        not cfg.topics.enabled
        or topic_store is None
        or _topics_command_error(
            cfg,
            msg.chat_id,
            resolved_scope=resolved_scope,
            scope_chat_ids=scope_chat_ids,
        )
        is not None
    ):
        return None

    title = _topic_title(runtime=cfg.runtime, context=context)
    try:
        created = await cfg.bot.create_forum_topic(msg.chat_id, title)
        if created is None:
            return None
        thread_id = created.message_thread_id
        await topic_store.set_context(
            msg.chat_id,
            thread_id,
            context,
            topic_title=title,
        )
        branch_note = f" @{context.branch}" if context.branch else ""
        bound_text = f"topic bound to `{context.project}`{branch_note}"
        rendered_text, entities = prepare_telegram(MarkdownParts(header=bound_text))
        await cfg.exec_cfg.transport.send(
            channel_id=msg.chat_id,
            message=RenderedMessage(text=rendered_text, extra={"entities": entities}),
            options=SendOptions(thread_id=thread_id),
        )
        return title
    except Exception as exc:  # noqa: BLE001 — intentionally broad: the
        # topic step is best-effort (see the "NEVER hard-fails" contract in
        # this function's docstring / KD4). Any failure from create_forum_topic
        # or set_context — Telegram API error, network error, or a store
        # I/O error — degrades to a None return rather than risking a raise
        # that would abort an already-successful clone/registration.
        logger.warning(
            "clone.topic.failed",
            chat_id=msg.chat_id,
            alias=context.project,
            error=str(exc),
            error_type=exc.__class__.__name__,
        )
        return None
