---
name: security-review
description: "Reviews Telegram/webhook auth, SSRF protections, and env-var handling for security regressions"
triggers:
  - "**/telegram/**"
  - "**/triggers/**"
  - "**/utils/env_policy.py"
  - "**/utils/env_audit.py"
  - "**/*auth*"
  - "**/*secret*"
  - "**/*token*"
  - "**/*webhook*"
  - "**/*credential*"
skip-when:
  - "**/*.test.*"
  - "**/tests/**"
  - "**/testdata/**"
severity: high
max-files: 50
model: sonnet
---

# Security Review

Untether is a Telegram bridge that spawns local subprocesses and receives inbound webhooks.
Review changes against this project's actual security surface — not generic OWASP boilerplate.

## Checklist

- [ ] Webhook signature verification (HMAC-SHA256/SHA1, bearer token — `triggers/auth.py`) uses
      **timing-safe comparison**; no new code path bypasses it
- [ ] SSRF protections (`triggers/ssrf.py`) are not weakened — outbound HTTP requests
      (cron data-fetch, http_forward action) must still validate against blocked IP ranges
      and resolve DNS before connecting; new URL-accepting fields must route through this check
- [ ] `allowed_user_ids` / chat authorization is enforced on every new command or callback path
      — no new Telegram handler skips the allowlist
- [ ] Environment variable exposure to subprocesses goes through the allowlist in
      `utils/env_policy.py` (`filtered_env`) — no runner or new integration inherits the full
      parent environment by default
- [ ] Secrets (bot tokens, webhook secrets, API keys, `BWS_ACCESS_TOKEN`-style credential-manager
      tokens) are never logged — check new `structlog` calls for accidental secret inclusion
- [ ] File upload / `/file put` and `/browse` paths still enforce path-traversal protection and
      deny-glob rules (`telegram/files.py`)
- [ ] New config keys under `[security]` (e.g. `env_extra_allow`, `env_extra_prefix_allow`) are
      validated against the `[A-Z_][A-Z0-9_]*` pattern before use, not accepted unchecked
- [ ] Rate limiting (`triggers/rate_limit.py`, per-webhook + global token bucket) is not
      bypassable by a new trigger type

## Severity Guide

| Finding | Severity |
|---------|----------|
| Webhook auth bypass or non-timing-safe comparison reintroduced | critical |
| SSRF check bypassed for a new outbound-URL field | critical |
| Secret/token logged in plaintext | high |
| `allowed_user_ids` check missing on a new command | high |
| Full env inheritance for a new subprocess spawn | high |
| Path traversal in file transfer | critical |
| Rate limit bypass | medium |
| Overly broad `env_extra_allow` pattern accepted without validation | medium |
