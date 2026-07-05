---
name: code-quality-review
description: "Reviews Python code for clarity, idiomatic style, and common code smells in Untether's runner/bridge codebase"
triggers:
  - "**/*.py"
skip-when:
  - "**/tests/**"
  - "**/__pycache__/**"
  - "**/dist/**"
  - "**/build/**"
  - "**/*.pyi"
severity: medium
model: haiku
---

# Code Quality Review

Review for code clarity, maintainability, and Python-idiomatic style in Untether's async
Telegram-bridge codebase (Python 3.12+, anyio, msgspec, structlog).

## Checklist

- [ ] Function and variable names are clear and intention-revealing
- [ ] Functions do one thing (single responsibility)
- [ ] Error cases are handled explicitly — no silent `except: pass`
- [ ] No magic numbers or strings (use named constants / enums)
- [ ] No dead code or commented-out code blocks
- [ ] No unnecessary complexity (YAGNI) — no premature abstractions for a single use site
- [ ] No deeply nested conditionals that could be simplified
- [ ] Async functions (`async def`) handle errors explicitly — no unhandled awaits that can raise silently in a background task
- [ ] Dataclasses/msgspec structs use `slots=True` where the project convention does (see existing runner `*StreamState` classes)
- [ ] Comments are absent unless they explain a non-obvious WHY (per this project's stated convention — no restating what code does)
- [ ] Consistent style with surrounding code (ruff-formatted)

## Severity Guide

| Finding | Severity |
|---------|----------|
| Silent error swallowing / lost error context | high |
| Resource leak (unclosed FDs, subprocess handles) | high |
| Inconsistent/misleading naming that could cause bugs | medium |
| Dead code | low |
| Magic number without explanation | low |
| Overly nested code (>3 levels deep) | low |
| Commented-out code blocks | info |
| Unnecessary WHAT-comment (restates the code) | info |
