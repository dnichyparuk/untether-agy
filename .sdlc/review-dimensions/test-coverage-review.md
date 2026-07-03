---
name: test-coverage-review
description: "Reviews whether new source modules have tests matching the project's fixture + stub-CLI pattern, and whether the 80% coverage gate is maintained"
triggers:
  - "**/src/untether/**/*.py"
skip-when:
  - "**/tests/**"
  - "**/__pycache__/**"
severity: medium
model: haiku
---

# Test Coverage Review

The project enforces an 80% coverage gate (`pytest` `--cov-fail-under=80`) and has an
established test-fixture convention for runners (fake CLI scripts / JSONL fixtures under
`tests/fixtures/`). Review new/changed source files for matching test coverage.

## Checklist

- [ ] New modules under `src/untether/` have a corresponding `tests/test_<module>.py`
- [ ] New runners include: `build_args` tests (fresh/resume/model/flags), `translate` tests
      (success + failure envelopes/events), a 3-event-contract assertion
      (`events[0]` is `StartedEvent`, `events[-1]` is `CompletedEvent`), and error-path tests
      (`process_error_events`, `stream_end_events`)
- [ ] New schemas have a fixture file under `tests/fixtures/` exercised by at least one decode test
- [ ] Async code paths are tested with `anyio` test fixtures per this project's convention, not
      mocked away entirely
- [ ] Bug fixes include a regression test, not just the fix
- [ ] Tests assert on behavior (event contents, resume tokens, error messages) — not just "it
      didn't raise"
- [ ] No new source file drags overall coverage below the 80% gate

## Severity Guide

| Finding | Severity |
|---------|----------|
| New runner with no tests at all | high |
| Bug fix with no regression test | high |
| New public function/class with no tests | medium |
| Test only asserts "no exception raised" (too broad) | medium |
| Missing edge-case test (e.g. empty envelope, malformed JSON) | low |
| Test name doesn't describe the scenario | info |
