---
name: configuration-management-review
description: "Reviews untether.toml config, hot-reload paths, and config migrations for backward compatibility"
triggers:
  - "**/config.py"
  - "**/settings.py"
  - "**/config_migrations.py"
  - "**/config_watch.py"
  - "**/config_reload_notification.py"
  - "**/*.toml"
  - "**/telegram/bridge.py"
skip-when:
  - "**/tests/**"
severity: medium
model: sonnet
---

# Configuration Management Review

Untether has a hot-reloadable TOML config (`untether.toml`) with per-project overrides,
a plugin allowlist, and a documented restart-only-vs-hot-reload key distinction. Review
config-touching changes against that model.

## Checklist

- [ ] New config keys have safe, backward-compatible defaults — an existing `untether.toml`
      without the new key must keep working unchanged
- [ ] If a new key is **restart-only**, it's added to the documented restart-only set
      (mirroring `bot_token`, `chat_id`, `session_mode`, `topics`, `message_overflow`) and
      `handle_reload()` warns rather than silently ignoring a live edit
- [ ] If a new key is **hot-reloadable**, `TelegramBridgeConfig.update_from()` (or the
      equivalent settings object) actually copies it — a key that's reloadable in schema but
      not wired into `update_from()` is a silent bug
- [ ] Config migrations (`config_migrations.py`) are additive — old config files must still
      parse, and migration logic must not throw on a file that predates the new key
- [ ] Per-project `engine_config` overrides don't leak between projects (each project's engine
      settings must resolve independently)
- [ ] `[plugins].enabled` allowlist changes are validated before being applied — an invalid
      plugin id in the allowlist should fail clearly, not silently no-op
- [ ] `[security] env_extra_allow` / `env_extra_prefix_allow` additions are documented and
      validated against the `[A-Z_][A-Z0-9_]*` pattern (cross-reference with security-review)

## Severity Guide

| Finding | Severity |
|---------|----------|
| New config key breaks parsing of existing `untether.toml` files | critical |
| Hot-reloadable key silently not wired into `update_from()` | high |
| Restart-only key missing from the documented restart-only set (silent stale config) | high |
| Migration throws on a pre-existing config file | high |
| Per-project override leaking across projects | high |
| Missing default causing a `KeyError`/`AttributeError` on old configs | medium |
| Undocumented new config key | low |
