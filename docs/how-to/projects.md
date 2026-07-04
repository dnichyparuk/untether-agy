# Projects

Projects let you target any registered repo from Telegram — no need to restart Untether or change directories. Send `/myapp fix the tests` from your phone and the agent runs in the right repo.

## Register a repo as a project

```sh
cd ~/dev/happy-gadgets
untether init happy-gadgets
```

```
saved project 'happy-gadgets' to ~/.untether/untether.toml
```

<!-- TODO: capture screenshot -->
<!-- <img src="../assets/screenshots/project-init.jpg" alt="Terminal output of untether init showing project registration" width="360" loading="lazy" /> -->

This adds a project to your config:

=== "untether config"

    ```sh
    untether config set projects.happy-gadgets.path "~/dev/happy-gadgets"
    ```

=== "toml"

    ```toml
    [projects.happy-gadgets]
    path = "~/dev/happy-gadgets"
    ```

## Target a project from chat

Send:

```
/happy-gadgets pinky-link two threads
```

## Project-specific settings

Projects can override global defaults:

=== "untether config"

    ```sh
    untether config set projects.happy-gadgets.path "~/dev/happy-gadgets"
    untether config set projects.happy-gadgets.default_engine "claude"
    untether config set projects.happy-gadgets.worktrees_dir ".worktrees"
    untether config set projects.happy-gadgets.worktree_base "master"
    ```

=== "toml"

    ```toml
    [projects.happy-gadgets]
    path = "~/dev/happy-gadgets"
    default_engine = "claude"
    worktrees_dir = ".worktrees"
    worktree_base = "master"
    ```

If you expect to edit config while Untether is running, enable hot reload:

=== "untether config"

    ```sh
    untether config set watch_config true
    ```

=== "toml"

    ```toml
    watch_config = true
    ```

## Bootstrap a repo from Telegram with /clone

You don't need terminal access to onboard a new repo. Send:

```
/clone https://github.com/happy-org/happy-gadgets
```

or the scp-style form:

```
/clone git@github.com:happy-org/happy-gadgets.git @feat/flower-pin
```

Untether runs a native `git clone` (shallow by default — see `[clone] depth` below), derives an alias from the repo name (`happy-gadgets`), and writes a `[projects.happy-gadgets]` entry to `untether.toml` for you — the same shape `untether init` would produce. Grammar in full: `/clone <repo-url> [--dir <path>] [@<branch>]` — `--dir <path>` picks a destination under the configured clone root instead of the default, and `@<branch>` clones a specific branch.

In a forum-enabled group chat, `/clone` also creates a topic bound to the freshly-registered project — clone, register, and topic mapping in one step:

```
/clone https://github.com/happy-org/happy-gadgets
```

```
cloning `happy-org/happy-gadgets`...
cloned + registered `happy-gadgets`; created topic `happy-gadgets`.
```

Send `/happy-gadgets fix the tests` (or just talk in the new topic) right away — no restart, no manual `untether init` round-trip. In a private chat, or a group where topics aren't enabled, the clone and registration still happen but the topic step is skipped; the reply tells you the equivalent `/topic` command to run later if you want one.

Only hosts in `[clone] allowed_hosts` (default `["github.com"]`) are accepted, and the clone uses your host's existing git credentials (SSH keys, credential helpers) as-is — v1 does no token injection. See the [`[clone]` config reference](../reference/config.md#clone) for `root`, `allowed_hosts`, `default_engine`, and `depth`.

## Bootstrap a new project from Telegram with /project

When you want to start a brand-new project that has no repo to clone yet, send:

```
/project happy-gadgets
```

Untether sanitizes the name into an alias (`happy-gadgets`), creates an empty directory under the configured `[new_project] root` (`<root>/happy-gadgets`), and writes a `[projects.happy-gadgets]` entry to `untether.toml` — the same shape `untether init` would produce. Nothing is cloned; you populate the directory yourself afterwards (`git clone`, `git init`, or copy files in). Grammar: `/project <name>`.

Unlike `/clone`, `/project` does **not** dedupe the alias. If the name already maps to a registered project, it refuses and tells you the existing path rather than silently registering `happy-gadgets_1`:

```
/project happy-gadgets
```

```
error: project `happy-gadgets` already exists (path: /home/you/untether-projects/happy-gadgets). Pick a different name.
```

In a forum-enabled group chat, `/project` also creates a topic bound to the freshly-registered project:

```
registered `happy-gadgets`; created topic `happy-gadgets`.
```

In a private chat, or a group where topics aren't enabled, the registration still happens but the topic step is skipped; the reply tells you the equivalent `/topic` command to run later. See the [`[new_project]` config reference](../reference/config.md#new_project) for `enabled`, `root`, and `default_engine`.

## Set a default project

If you mostly work in one repo:

=== "untether config"

    ```sh
    untether config set default_project "happy-gadgets"
    ```

=== "toml"

    ```toml
    default_project = "happy-gadgets"
    ```

## Related

- [Context resolution](../reference/context-resolution.md)
- [Worktrees](worktrees.md)
