"""Tests for the `/clone` command's pure helpers (Task 2), the
`run_git_clone` subprocess runner (Task 3), `register_project`
(Task 4), and the `handle_clone_command` orchestration (Task 5).

Covers `CloneSettings` (settings.py), the URL/alias/destination helpers,
`run_git_clone`/`CloneOutcome`, `register_project`, and the end-to-end
`handle_clone_command` flow (clone -> register -> gated forum-topic step)
in `untether.telegram.clone`.
"""

from __future__ import annotations

import json
import os
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
    CloneSettings,
    TelegramTopicsSettings,
    UntetherSettings,
    load_settings,
    validate_settings_data,
)
from untether.telegram.api_models import ForumTopic
from untether.telegram.backend import _load_clone_settings
from untether.telegram.clone import (
    CloneOutcome,
    RepoRef,
    derive_alias,
    handle_clone_command,
    host_is_allowed,
    parse_repo_url,
    register_project,
    resolve_destination,
    run_git_clone,
)
from untether.telegram.loop import (
    TelegramCommandContext,
    _apply_clone_hot_reload,
    _dispatch_builtin_command,
    _run_clone_command_tracked,
)
from untether.telegram.types import TelegramIncomingMessage
from untether.transport import MessageRef
from untether.transport_runtime import TransportRuntime

_TELEGRAM_BASE = (
    '[transports.telegram]\nbot_token = "tok"\nchat_id = 123\n'
    "allow_any_user = true\n"
)


# ── CloneSettings ────────────────────────────────────────────────────────


def test_clone_settings_defaults() -> None:
    settings = CloneSettings()
    assert settings.enabled is True
    assert settings.root == "~/untether-projects"
    assert settings.allowed_hosts == ["github.com"]
    # Default is None: cloned projects inherit the global default_engine
    # rather than being pinned to a hardcoded engine at clone time.
    assert settings.default_engine is None
    assert settings.depth == 1


def test_clone_settings_loads_from_toml(tmp_path: Path) -> None:
    config_path = tmp_path / "untether.toml"
    config_path.write_text(
        _TELEGRAM_BASE
        + "\n[clone]\n"
        'root = "/srv/repos"\n'
        'allowed_hosts = ["github.com", "gitlab.example.com"]\n'
        'default_engine = "codex"\n'
        "depth = 5\n",
        encoding="utf-8",
    )
    settings, _ = load_settings(config_path)
    assert settings.clone.root == "/srv/repos"
    assert settings.clone.allowed_hosts == ["github.com", "gitlab.example.com"]
    assert settings.clone.default_engine == "codex"
    assert settings.clone.depth == 5


def test_untether_settings_default_clone_is_present() -> None:
    settings = UntetherSettings.model_validate(
        {
            "transports": {
                "telegram": {"bot_token": "tok", "chat_id": 1, "allow_any_user": True}
            }
        }
    )
    assert isinstance(settings.clone, CloneSettings)
    assert settings.clone.enabled is True


def test_clone_settings_rejects_bad_depth_type(tmp_path: Path) -> None:
    config_path = tmp_path / "untether.toml"
    config_path.write_text(
        _TELEGRAM_BASE + "\n[clone]\ndepth = \"not-a-number\"\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="depth"):
        load_settings(config_path)


def test_clone_settings_rejects_depth_below_minimum(tmp_path: Path) -> None:
    config_path = tmp_path / "untether.toml"
    config_path.write_text(
        _TELEGRAM_BASE + "\n[clone]\ndepth = 0\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="depth"):
        load_settings(config_path)


def test_clone_settings_rejects_empty_root(tmp_path: Path) -> None:
    config_path = tmp_path / "untether.toml"
    config_path.write_text(
        _TELEGRAM_BASE + "\n[clone]\nroot = \"\"\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="root"):
        load_settings(config_path)


def test_clone_settings_rejects_unknown_field(tmp_path: Path) -> None:
    config_path = tmp_path / "untether.toml"
    config_path.write_text(
        _TELEGRAM_BASE + "\n[clone]\nbogus_field = 1\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="bogus_field"):
        load_settings(config_path)


# ── host_is_allowed ──────────────────────────────────────────────────────


def test_host_is_allowed_exact_match() -> None:
    assert host_is_allowed("github.com", ("github.com",)) is True


def test_host_is_allowed_case_insensitive() -> None:
    assert host_is_allowed("GitHub.com", ("github.com",)) is True


def test_host_is_allowed_rejects_unlisted_host() -> None:
    assert host_is_allowed("evil.example.com", ("github.com",)) is False


def test_host_is_allowed_empty_allowlist() -> None:
    assert host_is_allowed("github.com", ()) is False


# ── parse_repo_url ───────────────────────────────────────────────────────


class TestParseRepoUrlValid:
    def test_https_no_git_suffix(self) -> None:
        ref = parse_repo_url("https://github.com/owner/repo")
        assert ref == RepoRef(
            host="github.com",
            owner="owner",
            repo="repo",
            url="https://github.com/owner/repo.git",
            scheme="https",
        )

    def test_https_with_git_suffix(self) -> None:
        ref = parse_repo_url("https://github.com/owner/repo.git")
        assert ref.repo == "repo"
        assert ref.url == "https://github.com/owner/repo.git"
        assert ref.scheme == "https"

    def test_https_trailing_slash(self) -> None:
        ref = parse_repo_url("https://github.com/owner/repo/")
        assert ref.repo == "repo"

    def test_scp_no_git_suffix(self) -> None:
        ref = parse_repo_url("git@github.com:owner/repo")
        assert ref == RepoRef(
            host="github.com",
            owner="owner",
            repo="repo",
            url="git@github.com:owner/repo.git",
            scheme="scp",
        )

    def test_scp_with_git_suffix(self) -> None:
        ref = parse_repo_url("git@github.com:owner/repo.git")
        assert ref.url == "git@github.com:owner/repo.git"
        assert ref.scheme == "scp"

    def test_repo_name_with_dots_and_hyphens(self) -> None:
        ref = parse_repo_url("https://github.com/owner/my-repo.js")
        assert ref.repo == "my-repo.js"

    def test_custom_allowed_hosts(self) -> None:
        ref = parse_repo_url(
            "https://gitlab.example.com/owner/repo",
            allowed_hosts=("gitlab.example.com",),
        )
        assert ref.host == "gitlab.example.com"

    def test_strips_surrounding_whitespace(self) -> None:
        ref = parse_repo_url("  https://github.com/owner/repo  ")
        assert ref.owner == "owner"


class TestParseRepoUrlInvalid:
    @pytest.mark.parametrize(
        "url",
        [
            "ftp://github.com/owner/repo",
            "http://github.com/owner/repo",  # http (not https) not accepted
            "ssh://git@github.com/owner/repo",
            "https://github.com/owner",  # missing repo segment
            "https://github.com/owner/repo/extra",  # extra path segment
            "not a url at all",
            "github.com/owner/repo",  # missing scheme
        ],
    )
    def test_malformed_or_unsafe_scheme(self, url: str) -> None:
        with pytest.raises(ValueError, match="unsupported or unsafe repo URL"):
            parse_repo_url(url)

    @pytest.mark.parametrize("url", ["", "   "])
    def test_rejects_empty_url(self, url: str) -> None:
        with pytest.raises(ValueError, match="empty repo URL"):
            parse_repo_url(url)

    def test_rejects_non_allowed_host_https(self) -> None:
        with pytest.raises(ValueError, match="host not allowed"):
            parse_repo_url("https://evil.example.com/owner/repo")

    def test_rejects_non_allowed_host_scp(self) -> None:
        with pytest.raises(ValueError, match="host not allowed"):
            parse_repo_url("git@evil.example.com:owner/repo")

    def test_rejects_path_traversal_owner(self) -> None:
        with pytest.raises(ValueError, match="invalid owner"):
            parse_repo_url("https://github.com/../repo")

    def test_rejects_path_traversal_repo(self) -> None:
        with pytest.raises(ValueError, match="invalid repo"):
            parse_repo_url("https://github.com/owner/..")

    def test_rejects_dot_segment(self) -> None:
        with pytest.raises(ValueError, match="invalid owner"):
            parse_repo_url("https://github.com/./repo")


def test_parse_repo_url_rejects_invalid_segment_characters() -> None:
    """Owner/repo segments that pass the URL regex but fail the
    stricter `_SAFE_SEGMENT_RE` character check (clone.py line 96) — the
    ``".."``/``"."``/empty checks above catch traversal, but a character
    like ``!`` slips past those and must be caught by the regex match.
    """
    with pytest.raises(ValueError, match="invalid owner"):
        parse_repo_url("https://github.com/ow!ner/repo")


# ── derive_alias ─────────────────────────────────────────────────────────


def _ref(repo: str, *, scheme: str = "https") -> RepoRef:
    return RepoRef(
        host="github.com", owner="owner", repo=repo, url=f"u/{repo}", scheme=scheme
    )


class TestDeriveAlias:
    def test_simple_repo_name(self) -> None:
        assert derive_alias(_ref("repo"), existing=set()) == "repo"

    def test_sanitises_uppercase_and_punctuation(self) -> None:
        assert derive_alias(_ref("My-Repo.js"), existing=set()) == "my_repo_js"

    def test_strips_leading_trailing_underscores(self) -> None:
        assert derive_alias(_ref("-repo-"), existing=set()) == "repo"

    def test_falls_back_to_repo_when_sanitised_empty(self) -> None:
        assert derive_alias(_ref("---"), existing=set()) == "repo"

    def test_truncates_to_32_chars(self) -> None:
        long_name = "a" * 50
        alias = derive_alias(_ref(long_name), existing=set())
        assert len(alias) == 32
        assert alias == "a" * 32

    def test_dedupes_with_numeric_suffix(self) -> None:
        alias = derive_alias(_ref("repo"), existing={"repo"})
        assert alias == "repo_1"

    def test_dedupes_increments_past_multiple_collisions(self) -> None:
        alias = derive_alias(_ref("repo"), existing={"repo", "repo_1", "repo_2"})
        assert alias == "repo_3"

    def test_dedupe_keeps_result_within_32_chars(self) -> None:
        long_name = "a" * 32
        existing = {"a" * 32}
        alias = derive_alias(_ref(long_name), existing=existing)
        assert len(alias) <= 32
        assert alias not in existing


def test_derive_alias_replaces_unicode_characters_with_underscore() -> None:
    # "é" isn't in [a-z0-9_], so it becomes "_"; that trailing "_" is then
    # stripped, leaving "caf" — distinct from the punctuation-only cases in
    # TestDeriveAlias above.
    assert derive_alias(_ref("café"), existing=set()) == "caf"


# ── resolve_destination ──────────────────────────────────────────────────


class TestResolveDestination:
    def test_default_uses_root(self, tmp_path: Path) -> None:
        root = tmp_path / "projects"
        dest = resolve_destination(_ref("repo"), root=root, override=None)
        assert dest == (root / "repo").resolve()

    def test_relative_override_nests_under_root(self, tmp_path: Path) -> None:
        root = tmp_path / "projects"
        dest = resolve_destination(
            _ref("repo"), root=root, override=Path("subdir")
        )
        assert dest == (root / "subdir" / "repo").resolve()

    def test_expands_tilde_in_root(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        root = Path("~/untether-projects")
        dest = resolve_destination(_ref("repo"), root=root, override=None)
        assert dest == (tmp_path / "untether-projects" / "repo").resolve()

    def test_rejects_relative_traversal_override(self, tmp_path: Path) -> None:
        root = tmp_path / "projects"
        with pytest.raises(ValueError, match="outside clone root"):
            resolve_destination(
                _ref("repo"), root=root, override=Path("../../etc")
            )

    def test_rejects_absolute_override_outside_root(self, tmp_path: Path) -> None:
        root = tmp_path / "projects"
        with pytest.raises(ValueError, match="outside clone root"):
            resolve_destination(_ref("repo"), root=root, override=Path("/etc"))

    def test_rejects_symlink_escape(self, tmp_path: Path) -> None:
        root = tmp_path / "projects"
        root.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        escape_link = root / "escape"
        escape_link.symlink_to(outside, target_is_directory=True)
        with pytest.raises(ValueError, match="outside clone root"):
            resolve_destination(
                _ref("repo"), root=root, override=Path("escape")
            )

    def test_absolute_override_inside_root_is_allowed(self, tmp_path: Path) -> None:
        root = tmp_path / "projects"
        nested = root / "team-a"
        dest = resolve_destination(_ref("repo"), root=root, override=nested)
        assert dest == (nested / "repo").resolve()


def test_resolve_destination_default_and_override(tmp_path: Path) -> None:
    """Contract-named test covering both the no-override and --dir-override
    shapes in one place (individual edge cases are covered in more depth by
    TestResolveDestination above)."""
    root = tmp_path / "projects"

    default_dest = resolve_destination(_ref("repo"), root=root, override=None)
    assert default_dest == (root / "repo").resolve()

    override_dest = resolve_destination(
        _ref("repo"), root=root, override=Path("team-a")
    )
    assert override_dest == (root / "team-a" / "repo").resolve()


# ── run_git_clone ────────────────────────────────────────────────────────

# Fake `git` script: a stand-in subprocess that records the argv/env it was
# invoked with (via `UNTETHER_TEST_CAPTURE`, whose `UNTETHER_` prefix is on
# the env-policy allowlist so it survives `filtered_env()`), optionally
# writes to stderr and exits non-zero (`UNTETHER_TEST_STDERR` /
# `UNTETHER_TEST_EXITCODE`), and otherwise emulates a successful clone by
# creating the destination directory (its last argv element).
_FAKE_GIT_SCRIPT = """#!/usr/bin/env python3
import json
import os
import sys

capture_path = os.environ.get("UNTETHER_TEST_CAPTURE")
if capture_path:
    with open(capture_path, "w", encoding="utf-8") as f:
        json.dump({"argv": sys.argv[1:], "env": dict(os.environ)}, f)

stderr_msg = os.environ.get("UNTETHER_TEST_STDERR")
if stderr_msg:
    sys.stderr.write(stderr_msg)

exit_code = int(os.environ.get("UNTETHER_TEST_EXITCODE", "0"))
if exit_code == 0:
    os.makedirs(sys.argv[-1], exist_ok=True)
sys.exit(exit_code)
"""


def _install_fake_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Put a fake `git` executable on PATH and return it."""
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    git_path = bin_dir / "git"
    git_path.write_text(_FAKE_GIT_SCRIPT, encoding="utf-8")
    git_path.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    return git_path


@pytest.mark.anyio
class TestRunGitClone:
    async def test_success_returns_ok_true_with_resolved_dest(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_fake_git(tmp_path, monkeypatch)
        capture_path = tmp_path / "capture.json"
        monkeypatch.setenv("UNTETHER_TEST_CAPTURE", str(capture_path))
        # A secret that must NOT reach the git subprocess env.
        monkeypatch.setenv("FAKE_BOT_TOKEN", "should-not-leak")

        ref = _ref("repo")
        dest = tmp_path / "dest" / "repo"

        outcome = await run_git_clone(ref, dest, branch="main", depth=1)

        assert outcome == CloneOutcome(
            ok=True, dest=dest, branch="main", returncode=0, stderr_excerpt=""
        )
        assert dest.is_dir()

        captured = json.loads(capture_path.read_text(encoding="utf-8"))
        assert captured["argv"] == [
            "clone",
            "--depth",
            "1",
            "--single-branch",
            "--branch",
            "main",
            "--",
            ref.url,
            str(dest),
        ]
        env = captured["env"]
        assert env["GIT_TERMINAL_PROMPT"] == "0"
        assert env["GIT_SSH_COMMAND"] == "ssh -o BatchMode=yes"
        assert "FAKE_BOT_TOKEN" not in env

    async def test_full_clone_no_branch_omits_depth_flags(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_fake_git(tmp_path, monkeypatch)
        capture_path = tmp_path / "capture.json"
        monkeypatch.setenv("UNTETHER_TEST_CAPTURE", str(capture_path))

        ref = _ref("repo")
        dest = tmp_path / "dest" / "repo"

        outcome = await run_git_clone(ref, dest, branch=None, depth=None)

        assert outcome.ok is True
        captured = json.loads(capture_path.read_text(encoding="utf-8"))
        assert captured["argv"] == ["clone", "--", ref.url, str(dest)]

    async def test_nonzero_exit_returns_ok_false_with_bounded_stderr(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_fake_git(tmp_path, monkeypatch)
        monkeypatch.setenv("UNTETHER_TEST_EXITCODE", "128")
        monkeypatch.setenv("UNTETHER_TEST_STDERR", "x" * 5000)

        ref = _ref("repo")
        dest = tmp_path / "dest" / "repo"

        outcome = await run_git_clone(ref, dest, branch=None, depth=1)

        assert outcome.ok is False
        assert outcome.returncode == 128
        # Bounded: not the full 5000-char stderr blob.
        assert len(outcome.stderr_excerpt) < 400
        assert outcome.stderr_excerpt != "x" * 5000
        assert not dest.exists()

    async def test_refuses_nonempty_existing_destination_without_running_git(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_fake_git(tmp_path, monkeypatch)
        capture_path = tmp_path / "capture.json"
        monkeypatch.setenv("UNTETHER_TEST_CAPTURE", str(capture_path))

        ref = _ref("repo")
        dest = tmp_path / "dest" / "repo"
        dest.mkdir(parents=True)
        (dest / "existing-file.txt").write_text("already here", encoding="utf-8")

        outcome = await run_git_clone(ref, dest, branch=None, depth=1)

        assert outcome.ok is False
        assert outcome.dest == dest
        assert "exists" in outcome.stderr_excerpt
        # git was never invoked: the fake script would have written this.
        assert not capture_path.exists()
        # The pre-existing file must survive untouched.
        assert (dest / "existing-file.txt").read_text(encoding="utf-8") == (
            "already here"
        )

    async def test_empty_existing_destination_is_allowed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_fake_git(tmp_path, monkeypatch)
        capture_path = tmp_path / "capture.json"
        monkeypatch.setenv("UNTETHER_TEST_CAPTURE", str(capture_path))

        ref = _ref("repo")
        dest = tmp_path / "dest" / "repo"
        dest.mkdir(parents=True)

        outcome = await run_git_clone(ref, dest, branch=None, depth=1)

        assert outcome.ok is True
        assert capture_path.exists()


@pytest.mark.anyio
async def test_run_git_clone_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_git(tmp_path, monkeypatch)
    ref = _ref("repo")
    dest = tmp_path / "dest" / "repo"

    outcome = await run_git_clone(ref, dest, branch=None, depth=None)

    assert outcome.ok is True
    assert outcome.dest == dest
    assert outcome.returncode == 0
    assert dest.is_dir()


@pytest.mark.anyio
async def test_run_git_clone_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Destination exists as a *file* (not a directory) — a refusal shape
    distinct from the "exists and is a non-empty directory" case already
    covered by `test_refuses_nonempty_existing_destination_without_running_git`
    (clone.py line 259 vs 261)."""
    _install_fake_git(tmp_path, monkeypatch)
    ref = _ref("repo")
    dest = tmp_path / "dest" / "repo"
    dest.parent.mkdir(parents=True)
    dest.write_text("i am a file, not a directory", encoding="utf-8")

    outcome = await run_git_clone(ref, dest, branch=None, depth=1)

    assert outcome.ok is False
    assert outcome.returncode == -1
    assert "not a directory" in outcome.stderr_excerpt


_FAKE_GIT_SLEEP_SCRIPT = """#!/usr/bin/env python3
import time
time.sleep(5)
"""


@pytest.mark.anyio
async def test_run_git_clone_timeout_returns_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `git` subprocess that never returns must be cut off by the
    `_CLONE_TIMEOUT_SECONDS` backstop (clone.py lines 306-307) rather than
    hanging the event loop forever."""
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    git_path = bin_dir / "git"
    git_path.write_text(_FAKE_GIT_SLEEP_SCRIPT, encoding="utf-8")
    git_path.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setattr(clone_module, "_CLONE_TIMEOUT_SECONDS", 0.05)

    ref = _ref("repo")
    dest = tmp_path / "dest" / "repo"

    outcome = await run_git_clone(ref, dest, branch=None, depth=1)

    assert outcome.ok is False
    assert outcome.returncode == -1
    assert "timed out" in outcome.stderr_excerpt


# ── register_project ─────────────────────────────────────────────────────

# `register_project` builds a real `RuntimeSpec` via `build_runtime_spec` and
# applies it to the live runtime, which means it exercises the real engine
# backend loader (`load_backends`/`build_router`). We patch `shutil.which`
# (in `runtime_loader`, where the CLI-on-PATH check actually runs) so these
# tests don't depend on any engine CLI being installed on the test host —
# mirrors the pattern in `tests/test_runtime_loader.py`.


def _write_base_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "untether.toml"
    config_path.write_text(_TELEGRAM_BASE, encoding="utf-8")
    return config_path


def _build_runtime(
    config_path: Path, monkeypatch: pytest.MonkeyPatch
) -> TransportRuntime:
    monkeypatch.setattr(runtime_loader.shutil, "which", lambda _cmd: "/bin/echo")
    settings, resolved_path = load_settings(config_path)
    spec = build_runtime_spec(settings=settings, config_path=resolved_path)
    return spec.to_runtime(config_path=resolved_path)


class TestRegisterProject:
    def test_writes_valid_projects_block(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_path = _write_base_config(tmp_path)
        runtime = _build_runtime(config_path, monkeypatch)
        dest = tmp_path / "untether-projects" / "myrepo"
        dest.mkdir(parents=True)

        register_project(
            config_path,
            runtime,
            alias="myrepo",
            path=dest,
            default_engine="claude",
        )

        raw = read_config(config_path)
        assert raw["projects"]["myrepo"] == {
            "path": str(dest),
            "default_engine": "claude",
        }
        # The written config must itself validate cleanly.
        validate_settings_data(raw, config_path=config_path)

    def test_omits_default_engine_when_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_path = _write_base_config(tmp_path)
        runtime = _build_runtime(config_path, monkeypatch)
        dest = tmp_path / "untether-projects" / "myrepo"
        dest.mkdir(parents=True)

        register_project(
            config_path, runtime, alias="myrepo", path=dest, default_engine=None
        )

        raw = read_config(config_path)
        assert raw["projects"]["myrepo"] == {"path": str(dest)}

    def test_applies_to_live_runtime_without_restart(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_path = _write_base_config(tmp_path)
        runtime = _build_runtime(config_path, monkeypatch)
        dest = tmp_path / "untether-projects" / "myrepo"
        dest.mkdir(parents=True)

        assert "myrepo" not in runtime.project_aliases()

        register_project(
            config_path,
            runtime,
            alias="myrepo",
            path=dest,
            default_engine="claude",
        )

        assert "myrepo" in runtime.project_aliases()
        resolved_cwd = runtime.resolve_run_cwd(
            RunContext(project="myrepo", branch=None)
        )
        assert resolved_cwd == dest
        assert runtime.project_default_engine(RunContext(project="myrepo")) == "claude"

    def test_does_not_require_watch_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_path = _write_base_config(tmp_path)
        runtime = _build_runtime(config_path, monkeypatch)
        assert runtime.watch_config is False

        dest = tmp_path / "untether-projects" / "myrepo"
        dest.mkdir(parents=True)
        register_project(
            config_path, runtime, alias="myrepo", path=dest, default_engine=None
        )

        # No watcher involved at all; the alias is resolvable immediately.
        assert "myrepo" in runtime.project_aliases()

    def test_reregistering_same_alias_same_path_is_idempotent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_path = _write_base_config(tmp_path)
        runtime = _build_runtime(config_path, monkeypatch)
        dest = tmp_path / "untether-projects" / "myrepo"
        dest.mkdir(parents=True)

        register_project(
            config_path, runtime, alias="myrepo", path=dest, default_engine="claude"
        )
        # Re-registering the same alias at the same path must not raise.
        register_project(
            config_path, runtime, alias="myrepo", path=dest, default_engine="claude"
        )

        raw = read_config(config_path)
        assert raw["projects"]["myrepo"]["path"] == str(dest)

    def test_refuses_to_overwrite_different_project_at_same_alias(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_path = tmp_path / "untether.toml"
        other_path = tmp_path / "existing-project"
        other_path.mkdir()
        config_path.write_text(
            _TELEGRAM_BASE + f'\n[projects.myrepo]\npath = "{other_path}"\n',
            encoding="utf-8",
        )
        runtime = _build_runtime(config_path, monkeypatch)
        new_dest = tmp_path / "untether-projects" / "myrepo"
        new_dest.mkdir(parents=True)

        with pytest.raises(ConfigError, match="different path"):
            register_project(
                config_path,
                runtime,
                alias="myrepo",
                path=new_dest,
                default_engine=None,
            )

        # Config on disk must be untouched by the refused write.
        raw = read_config(config_path)
        assert raw["projects"]["myrepo"]["path"] == str(other_path)
        # Live runtime must not have been touched either.
        resolved_cwd = runtime.resolve_run_cwd(
            RunContext(project="myrepo", branch=None)
        )
        assert resolved_cwd == other_path


def test_register_project_rejects_non_table_projects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`register_project`'s own defensive guard against a malformed
    top-level `projects` value (clone.py line 363) — build the runtime
    against a *valid* config first (a `projects` array would fail
    `load_settings` outright), then corrupt the file on disk directly so
    `register_project`'s raw `read_config` sees the bad shape."""
    config_path = _write_base_config(tmp_path)
    runtime = _build_runtime(config_path, monkeypatch)
    # `projects` must be a bare top-level key *before* the `[transports...]`
    # table header, otherwise TOML would nest it under that table instead.
    config_path.write_text(
        'projects = ["a", "b"]\n\n' + _TELEGRAM_BASE, encoding="utf-8"
    )
    dest = tmp_path / "untether-projects" / "myrepo"
    dest.mkdir(parents=True)

    with pytest.raises(ConfigError, match="expected a table"):
        register_project(
            config_path, runtime, alias="myrepo", path=dest, default_engine=None
        )


# ── handle_clone_command (orchestration) ─────────────────────────────────

# These exercise the end-to-end command flow: parse -> git clone (fake git on
# PATH) -> register_project (real config write + runtime apply) -> the gated
# forum-topic step. A controllable fake bot decides whether create_forum_topic
# yields a topic or None; a recording store captures the set_context pin so we
# can assert the RunContext(project=alias, branch=...) contract.


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


class _CloneFakeBot(FakeBot):
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


def _clone_msg(
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


def _orch_cfg(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    topics_enabled: bool,
    topic_result: ForumTopic | None,
    capture_path: Path | None = None,
):
    _install_fake_git(tmp_path, monkeypatch)
    if capture_path is not None:
        monkeypatch.setenv("UNTETHER_TEST_CAPTURE", str(capture_path))
    config_path = _write_base_config(tmp_path)
    runtime = _build_runtime(config_path, monkeypatch)
    transport = FakeTransport()
    bot = _CloneFakeBot(topic_result)
    clone_root = tmp_path / "projects"
    cfg = replace(
        make_cfg(transport),
        runtime=runtime,
        bot=bot,
        clone=CloneSettings(root=str(clone_root)),
        topics=TelegramTopicsSettings(enabled=topics_enabled, scope="all"),
    )
    return cfg, transport, bot, config_path


@pytest.mark.anyio
class TestHandleCloneCommand:
    async def test_clone_pins_context(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        capture_path = tmp_path / "capture.json"
        cfg, transport, bot, config_path = _orch_cfg(
            tmp_path,
            monkeypatch,
            topics_enabled=True,
            topic_result=ForumTopic(message_thread_id=7),
            capture_path=capture_path,
        )
        store = _RecordingTopicStore()
        msg = _clone_msg("/clone https://github.com/owner/myrepo @dev")

        await handle_clone_command(
            cfg,
            msg,
            args_text="https://github.com/owner/myrepo @dev",
            topic_store=store,  # type: ignore[arg-type]
            resolved_scope="all",
            scope_chat_ids=frozenset({msg.chat_id}),
        )

        # Project registered on disk.
        raw = read_config(config_path)
        assert "myrepo" in raw["projects"]
        # With the default (unset) clone engine, no default_engine is pinned
        # onto the project — it inherits the global default_engine instead.
        assert "default_engine" not in raw["projects"]["myrepo"]
        # Topic created and context pinned with the branch honoured.
        assert bot.create_topic_calls, "expected create_forum_topic to be called"
        assert len(store.set_context_calls) == 1
        pin = store.set_context_calls[0]
        assert pin["context"] == RunContext(project="myrepo", branch="dev")
        assert pin["thread_id"] == 7
        # The bound-to message was sent into the new topic thread.
        thread_sends = [
            call
            for call in transport.send_calls
            if call["options"] is not None and call["options"].thread_id == 7
        ]
        assert thread_sends, "expected a message sent into the new topic thread"
        # @dev reached the git checkout too.
        captured = json.loads(capture_path.read_text(encoding="utf-8"))
        assert "--branch" in captured["argv"]
        assert captured["argv"][captured["argv"].index("--branch") + 1] == "dev"

    async def test_non_forum_degrades_to_register_only(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # topics enabled + in scope, but create_forum_topic returns None
        # (chat isn't actually a forum) -> register-only degrade, no pin.
        cfg, transport, bot, config_path = _orch_cfg(
            tmp_path,
            monkeypatch,
            topics_enabled=True,
            topic_result=None,
        )
        store = _RecordingTopicStore()
        msg = _clone_msg("/clone https://github.com/owner/myrepo")

        await handle_clone_command(
            cfg,
            msg,
            args_text="https://github.com/owner/myrepo",
            topic_store=store,  # type: ignore[arg-type]
            resolved_scope="all",
            scope_chat_ids=frozenset({msg.chat_id}),
        )

        raw = read_config(config_path)
        assert "myrepo" in raw["projects"]
        assert bot.create_topic_calls, "topic creation was attempted"
        assert store.set_context_calls == []
        last = transport.send_calls[-1]["message"].text
        assert "run /topic myrepo" in last

    async def test_handle_clone_topics_disabled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg, transport, bot, config_path = _orch_cfg(
            tmp_path,
            monkeypatch,
            topics_enabled=False,
            topic_result=ForumTopic(message_thread_id=9),
        )
        store = _RecordingTopicStore()
        msg = _clone_msg("/clone https://github.com/owner/myrepo", chat_type="private")

        await handle_clone_command(
            cfg,
            msg,
            args_text="https://github.com/owner/myrepo",
            topic_store=store,  # type: ignore[arg-type]
            resolved_scope=None,
            scope_chat_ids=frozenset(),
        )

        raw = read_config(config_path)
        assert "myrepo" in raw["projects"]
        # No topic step at all when topics are disabled.
        assert bot.create_topic_calls == []
        assert store.set_context_calls == []
        last = transport.send_calls[-1]["message"].text
        assert "run /topic myrepo" in last

    async def test_disallowed_host_replies_error_no_config_write(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg, transport, bot, config_path = _orch_cfg(
            tmp_path,
            monkeypatch,
            topics_enabled=True,
            topic_result=ForumTopic(message_thread_id=7),
        )
        store = _RecordingTopicStore()
        msg = _clone_msg("/clone https://evil.example/owner/myrepo")

        await handle_clone_command(
            cfg,
            msg,
            args_text="https://evil.example/owner/myrepo",
            topic_store=store,  # type: ignore[arg-type]
            resolved_scope="all",
            scope_chat_ids=frozenset({msg.chat_id}),
        )

        # No clone, no registration, no topic.
        raw = read_config(config_path)
        assert "myrepo" not in raw.get("projects", {})
        assert bot.create_topic_calls == []
        assert store.set_context_calls == []
        assert "error" in transport.send_calls[-1]["message"].text.lower()

    async def test_git_failure_does_not_register(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg, transport, bot, config_path = _orch_cfg(
            tmp_path,
            monkeypatch,
            topics_enabled=True,
            topic_result=ForumTopic(message_thread_id=7),
        )
        monkeypatch.setenv("UNTETHER_TEST_EXITCODE", "128")
        monkeypatch.setenv("UNTETHER_TEST_STDERR", "fatal: repository not found")
        store = _RecordingTopicStore()
        msg = _clone_msg("/clone https://github.com/owner/myrepo")

        await handle_clone_command(
            cfg,
            msg,
            args_text="https://github.com/owner/myrepo",
            topic_store=store,  # type: ignore[arg-type]
            resolved_scope="all",
            scope_chat_ids=frozenset({msg.chat_id}),
        )

        raw = read_config(config_path)
        assert "myrepo" not in raw.get("projects", {})
        assert bot.create_topic_calls == []
        assert store.set_context_calls == []
        assert "git clone failed" in transport.send_calls[-1]["message"].text

    async def test_disabled_clone_setting_short_circuits(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg, transport, bot, config_path = _orch_cfg(
            tmp_path,
            monkeypatch,
            topics_enabled=True,
            topic_result=ForumTopic(message_thread_id=7),
        )
        cfg = replace(cfg, clone=cfg.clone.model_copy(update={"enabled": False}))
        store = _RecordingTopicStore()
        msg = _clone_msg("/clone https://github.com/owner/myrepo")

        await handle_clone_command(
            cfg,
            msg,
            args_text="https://github.com/owner/myrepo",
            topic_store=store,  # type: ignore[arg-type]
            resolved_scope="all",
            scope_chat_ids=frozenset({msg.chat_id}),
        )

        raw = read_config(config_path)
        assert "myrepo" not in raw.get("projects", {})
        assert "disabled" in transport.send_calls[-1]["message"].text

    async def test_handle_clone_forum(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Distinct from `test_clone_pins_context`: no branch is given here,
        and the assertion targets the final confirmation reply text
        ("created topic ...") rather than the pin/argv details."""
        cfg, transport, bot, config_path = _orch_cfg(
            tmp_path,
            monkeypatch,
            topics_enabled=True,
            topic_result=ForumTopic(message_thread_id=11),
        )
        store = _RecordingTopicStore()
        msg = _clone_msg("/clone https://github.com/owner/myrepo")

        await handle_clone_command(
            cfg,
            msg,
            args_text="https://github.com/owner/myrepo",
            topic_store=store,  # type: ignore[arg-type]
            resolved_scope="all",
            scope_chat_ids=frozenset({msg.chat_id}),
        )

        assert bot.create_topic_calls, "expected create_forum_topic to be called"
        assert len(store.set_context_calls) == 1
        assert store.set_context_calls[0]["context"] == RunContext(
            project="myrepo", branch=None
        )
        # The confirmation reply (not the final "topic bound to" message sent
        # into the new thread) is the one that names the created topic.
        all_texts = [call["message"].text for call in transport.send_calls]
        assert any("created topic" in text and "myrepo" in text for text in all_texts)

    async def test_handle_clone_private(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Topics are enabled globally, but this chat is out of the
        configured scope (e.g. a private DM when scope="main" is bound to a
        different chat) — the topic step must be skipped entirely (no
        `create_forum_topic` attempt at all), which is distinct from both
        `test_handle_clone_topics_disabled` (topics.enabled is False there)
        and `test_non_forum_degrades_to_register_only` (there the bot IS
        asked, but returns None)."""
        cfg, transport, bot, config_path = _orch_cfg(
            tmp_path,
            monkeypatch,
            topics_enabled=True,
            topic_result=ForumTopic(message_thread_id=7),
        )
        store = _RecordingTopicStore()
        msg = _clone_msg(
            "/clone https://github.com/owner/myrepo", chat_type="private"
        )

        await handle_clone_command(
            cfg,
            msg,
            args_text="https://github.com/owner/myrepo",
            topic_store=store,  # type: ignore[arg-type]
            resolved_scope="main",
            scope_chat_ids=frozenset(),  # chat_id deliberately not included
        )

        raw = read_config(config_path)
        assert "myrepo" in raw["projects"]
        assert bot.create_topic_calls == []
        assert store.set_context_calls == []
        last = transport.send_calls[-1]["message"].text
        assert "run /topic myrepo" in last

    @pytest.mark.parametrize(
        ("args_text", "expected_substring"),
        [
            ("", "missing repo URL"),
            ("--dir foo", "missing repo URL"),
            ("https://github.com/owner/repo --dir", "--dir requires a path"),
            ("https://github.com/owner/repo @", "empty branch after @"),
            ("https://github.com/owner/repo extra-token", "unexpected argument"),
        ],
    )
    async def test_handle_clone_command_arg_parse_errors(
        self, args_text: str, expected_substring: str
    ) -> None:
        """Covers every `_parse_clone_args` error branch (clone.py lines
        402, 412-416, 420, 427, 430) via the public `handle_clone_command`
        entry point, plus the reply formatting at lines 470-472."""
        transport = FakeTransport()
        cfg = make_cfg(transport)
        msg = _clone_msg(f"/clone {args_text}".rstrip())

        await handle_clone_command(cfg, msg, args_text=args_text, topic_store=None)

        last = transport.send_calls[-1]["message"].text
        assert expected_substring in last
        assert "usage: /clone" in last

    async def test_handle_clone_command_no_config_path_replies_error(self) -> None:
        """`make_cfg`'s default runtime has `config_path=None` — clone.py
        lines 483-484 must degrade gracefully instead of crashing when no
        config path is available to register the project against."""
        transport = FakeTransport()
        cfg = make_cfg(transport)
        assert cfg.runtime.config_path is None
        msg = _clone_msg("/clone https://github.com/owner/myrepo")

        await handle_clone_command(
            cfg,
            msg,
            args_text="https://github.com/owner/myrepo",
            topic_store=None,
        )

        last = transport.send_calls[-1]["message"].text
        assert "no config path available" in last

    async def test_handle_clone_command_dir_override_escape_replies_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A `--dir` override that resolves outside the clone root must
        reply with the `resolve_destination` ValueError (clone.py lines
        490-492) — distinct from TestResolveDestination's direct-call
        coverage of the same helper."""
        config_path = _write_base_config(tmp_path)
        runtime = _build_runtime(config_path, monkeypatch)
        transport = FakeTransport()
        clone_root = tmp_path / "projects"
        cfg = replace(
            make_cfg(transport),
            runtime=runtime,
            clone=CloneSettings(root=str(clone_root)),
        )
        args_text = "https://github.com/owner/myrepo --dir ../../etc"
        msg = _clone_msg(f"/clone {args_text}")

        await handle_clone_command(cfg, msg, args_text=args_text, topic_store=None)

        last = transport.send_calls[-1]["message"].text
        assert "outside clone root" in last
        raw = read_config(config_path)
        assert "myrepo" not in raw.get("projects", {})

    async def test_handle_clone_command_register_conflict_replies_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Forces `register_project`'s alias-collision `ConfigError` (clone.py
        lines 511-513) by patching `derive_alias` to collide with a
        pre-existing project at a different path — the real `derive_alias`
        already dedupes against `runtime.project_aliases()` so this
        collision can't arise through the normal flow."""
        other_path = tmp_path / "existing-project"
        other_path.mkdir()
        _install_fake_git(tmp_path, monkeypatch)
        config_path = tmp_path / "untether.toml"
        config_path.write_text(
            _TELEGRAM_BASE + f'\n[projects.myrepo]\npath = "{other_path}"\n',
            encoding="utf-8",
        )
        runtime = _build_runtime(config_path, monkeypatch)
        transport = FakeTransport()
        bot = _CloneFakeBot(None)
        clone_root = tmp_path / "projects"
        cfg = replace(
            make_cfg(transport),
            runtime=runtime,
            bot=bot,
            clone=CloneSettings(root=str(clone_root)),
            topics=TelegramTopicsSettings(enabled=False, scope="all"),
        )
        monkeypatch.setattr(
            clone_module, "derive_alias", lambda ref, existing: "myrepo"
        )
        store = _RecordingTopicStore()
        msg = _clone_msg("/clone https://github.com/owner/newrepo")

        await handle_clone_command(
            cfg,
            msg,
            args_text="https://github.com/owner/newrepo",
            topic_store=store,  # type: ignore[arg-type]
            resolved_scope=None,
            scope_chat_ids=frozenset(),
        )

        last = transport.send_calls[-1]["message"].text
        assert "failed to register project" in last
        # Original project registration must remain untouched.
        raw = read_config(config_path)
        assert raw["projects"]["myrepo"]["path"] == str(other_path)

    async def test_handle_clone_command_topic_step_exception_degrades_gracefully(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An unexpected exception from `create_forum_topic` (clone.py lines
        553-561) must not fail the whole command — it degrades to the
        register-only reply, same as the None-result and out-of-scope
        cases."""

        class _RaisingTopicBot(FakeBot):
            async def create_forum_topic(
                self, chat_id: int, name: str
            ) -> ForumTopic | None:
                raise RuntimeError("topic api boom")

        _install_fake_git(tmp_path, monkeypatch)
        config_path = _write_base_config(tmp_path)
        runtime = _build_runtime(config_path, monkeypatch)
        transport = FakeTransport()
        bot = _RaisingTopicBot()
        clone_root = tmp_path / "projects"
        cfg = replace(
            make_cfg(transport),
            runtime=runtime,
            bot=bot,
            clone=CloneSettings(root=str(clone_root)),
            topics=TelegramTopicsSettings(enabled=True, scope="all"),
        )
        store = _RecordingTopicStore()
        msg = _clone_msg("/clone https://github.com/owner/myrepo")

        await handle_clone_command(
            cfg,
            msg,
            args_text="https://github.com/owner/myrepo",
            topic_store=store,  # type: ignore[arg-type]
            resolved_scope="all",
            scope_chat_ids=frozenset({msg.chat_id}),
        )

        # Project registration must survive even though the topic step blew up.
        raw = read_config(config_path)
        assert "myrepo" in raw["projects"]
        assert store.set_context_calls == []
        last = transport.send_calls[-1]["message"].text
        assert "run /topic myrepo" in last


# ── handle_clone_command OSError paths (review-fix regression coverage) ────


@pytest.mark.anyio
class TestHandleCloneCommandErrorPaths:
    """Cover the two OSError catch branches added in the review-fix pass.

    A missing `git` binary surfaces as ``FileNotFoundError`` from
    ``run_git_clone``, and a config-write IO error surfaces as ``OSError``
    from ``register_project``. Both must reply gracefully rather than crash
    the handler task.
    """

    async def test_git_spawn_oserror_replies_gracefully(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg, transport, bot, config_path = _orch_cfg(
            tmp_path,
            monkeypatch,
            topics_enabled=True,
            topic_result=ForumTopic(message_thread_id=7),
        )

        async def _raise(*_a: object, **_k: object) -> CloneOutcome:
            raise FileNotFoundError("git")

        monkeypatch.setattr(clone_module, "run_git_clone", _raise)
        store = _RecordingTopicStore()
        msg = _clone_msg("/clone https://github.com/owner/myrepo")

        await handle_clone_command(
            cfg,
            msg,
            args_text="https://github.com/owner/myrepo",
            topic_store=store,  # type: ignore[arg-type]
            resolved_scope="all",
            scope_chat_ids=frozenset({msg.chat_id}),
        )

        # Graceful reply, no partial registration, no topic attempt.
        assert "git clone failed" in transport.send_calls[-1]["message"].text
        assert "myrepo" not in read_config(config_path).get("projects", {})
        assert bot.create_topic_calls == []
        assert store.set_context_calls == []

    async def test_register_oserror_replies_gracefully(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # _orch_cfg installs a fake git, so run_git_clone succeeds and the
        # handler reaches register_project — which we force to raise OSError.
        cfg, transport, bot, config_path = _orch_cfg(
            tmp_path,
            monkeypatch,
            topics_enabled=True,
            topic_result=ForumTopic(message_thread_id=7),
        )

        def _raise(*_a: object, **_k: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(clone_module, "register_project", _raise)
        store = _RecordingTopicStore()
        msg = _clone_msg("/clone https://github.com/owner/myrepo")

        await handle_clone_command(
            cfg,
            msg,
            args_text="https://github.com/owner/myrepo",
            topic_store=store,  # type: ignore[arg-type]
            resolved_scope="all",
            scope_chat_ids=frozenset({msg.chat_id}),
        )

        # Clone already happened; only registration failed → graceful reply,
        # no topic step.
        assert "failed to write config" in transport.send_calls[-1]["message"].text
        assert store.set_context_calls == []


@pytest.mark.anyio
async def test_run_git_clone_propagates_when_git_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`run_git_clone` must let a spawn failure propagate (not swallow it).

    The graceful-reply contract in `handle_clone_command` depends on
    `run_git_clone` raising `OSError`/`FileNotFoundError` when `git` can't be
    spawned, rather than converting it into an `ok=False` outcome.
    """
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))
    ref = parse_repo_url(
        "https://github.com/owner/myrepo", allowed_hosts=("github.com",)
    )
    dest = tmp_path / "dest"  # does not exist → no destination conflict

    with pytest.raises((FileNotFoundError, OSError)):
        await run_git_clone(ref, dest, branch=None, depth=1)


# ── /clone dispatch: TOCTOU guard + tracked-task cleanup (loop.py) ─────────


@pytest.mark.anyio
class TestCloneDispatchTracking:
    """Regression coverage for the synchronous RunningTask registration and
    the `_run_clone_command_tracked` cleanup contract."""

    async def test_dispatch_registers_running_task_synchronously(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[int] = []

        async def _noop(*_a: object, **_k: object) -> None:
            calls.append(1)

        # Stub the actual clone so the scheduled wrapper is a no-op.
        monkeypatch.setattr(loop_module, "handle_clone_command", _noop)

        running_tasks: dict[MessageRef, RunningTask] = {}

        async def _reply(*_a: object, **_k: object) -> None:
            return None

        msg = _clone_msg("/clone https://github.com/owner/myrepo")

        async with anyio.create_task_group() as tg:
            ctx = TelegramCommandContext(
                cfg=make_cfg(FakeTransport()),
                msg=msg,
                args_text="https://github.com/owner/myrepo",
                ambient_context=None,
                topic_store=None,
                chat_prefs=None,
                resolved_scope="all",
                scope_chat_ids=frozenset({msg.chat_id}),
                reply=_reply,
                task_group=tg,
                running_tasks=running_tasks,
            )
            result = _dispatch_builtin_command(ctx=ctx, command_id="clone")
            # Synchronous invariant: the guard-entry is registered BEFORE the
            # scheduled coroutine runs (no await between guard check and
            # registration) — this is what closes the TOCTOU window.
            assert result is True
            assert any(ref.channel_id == msg.chat_id for ref in running_tasks)

        # After the task group drains, the no-op wrapper's finally popped it.
        assert not any(ref.channel_id == msg.chat_id for ref in running_tasks)
        assert calls == [1]

    async def test_dispatch_second_concurrent_clone_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[int] = []

        async def _noop(*_a: object, **_k: object) -> None:
            calls.append(1)

        monkeypatch.setattr(loop_module, "handle_clone_command", _noop)

        # A clone is already in flight for chat 123 (different message id).
        running_tasks: dict[MessageRef, RunningTask] = {
            MessageRef(channel_id=123, message_id=99): RunningTask()
        }
        replies: list[dict] = []

        async def _reply(*_a: object, **kw: object) -> None:
            replies.append(kw)

        msg = _clone_msg("/clone https://github.com/owner/myrepo")

        async with anyio.create_task_group() as tg:
            ctx = TelegramCommandContext(
                cfg=make_cfg(FakeTransport()),
                msg=msg,
                args_text="https://github.com/owner/myrepo",
                ambient_context=None,
                topic_store=None,
                chat_prefs=None,
                resolved_scope="all",
                scope_chat_ids=frozenset({msg.chat_id}),
                reply=_reply,
                task_group=tg,
                running_tasks=running_tasks,
            )
            result = _dispatch_builtin_command(ctx=ctx, command_id="clone")
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

        monkeypatch.setattr(loop_module, "handle_clone_command", _noop)
        ref = MessageRef(channel_id=123, message_id=1)
        task = RunningTask()
        running_tasks: dict[MessageRef, RunningTask] = {ref: task}
        msg = _clone_msg("/clone https://github.com/owner/myrepo")

        await _run_clone_command_tracked(
            make_cfg(FakeTransport()),
            msg,
            "https://github.com/owner/myrepo",
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

        monkeypatch.setattr(loop_module, "handle_clone_command", _boom)
        ref = MessageRef(channel_id=123, message_id=1)
        task = RunningTask()
        running_tasks: dict[MessageRef, RunningTask] = {ref: task}
        msg = _clone_msg("/clone https://github.com/owner/myrepo")

        with pytest.raises(RuntimeError):
            await _run_clone_command_tracked(
                make_cfg(FakeTransport()),
                msg,
                "https://github.com/owner/myrepo",
                None,
                running_tasks=running_tasks,
                running_task=task,
                resolved_scope="all",
                scope_chat_ids=frozenset({123}),
            )

        # finally must still pop the entry and unblock resume waiters.
        assert ref not in running_tasks
        assert task.done.is_set()


# ── [clone] hot-reload helper (loop.py) ───────────────────────────────────


def test_apply_clone_hot_reload_updates_on_change() -> None:
    cfg = make_cfg(FakeTransport())
    new = CloneSettings(
        root="/srv/new", allowed_hosts=["github.com", "gitlab.example.com"]
    )
    changed = _apply_clone_hot_reload(cfg, new)
    assert changed is True
    assert cfg.clone is new
    assert cfg.clone.root == "/srv/new"


def test_apply_clone_hot_reload_noop_when_equal() -> None:
    cfg = make_cfg(FakeTransport())
    same = cfg.clone.model_copy()
    changed = _apply_clone_hot_reload(cfg, same)
    assert changed is False
    assert cfg.clone.root == CloneSettings().root


# ── _load_clone_settings fallback branches (backend.py) ───────────────────


def test_load_clone_settings_defaults_when_no_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        settings_module, "load_settings_if_exists", lambda *_a, **_k: None
    )
    assert _load_clone_settings() == CloneSettings()


def test_load_clone_settings_falls_back_on_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*_a: object, **_k: object) -> object:
        raise RuntimeError("malformed toml")

    monkeypatch.setattr(settings_module, "load_settings_if_exists", _boom)
    assert _load_clone_settings() == CloneSettings()


def test_load_clone_settings_reads_clone_section(
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
            "clone": {"root": "/srv/x"},
        }
    )
    monkeypatch.setattr(
        settings_module,
        "load_settings_if_exists",
        lambda *_a, **_k: (fake_settings, Path("/x/untether.toml")),
    )
    assert _load_clone_settings().root == "/srv/x"
