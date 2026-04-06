# Session: gate path antipattern check

**Date:** 2026-04-06

## What happened

Implemented a gate check that detects the `Path(__file__).resolve().parent.parent` antipattern in gateway and dashboard Python files. This is the gate's first "semantic" check — it reads file contents for structural problems, moving beyond compilation/syntax validation. All 8 gateway files + `dashboard/app.py` use this pattern to derive `project_root`, which breaks test isolation when real state files exist (caused 3 test failures in `test_transcripts.py` in the previous session).

The check reads changed `.py` files under `gateways/` and `dashboard/` using `std::fs::read_to_string` and rejects any containing the exact antipattern string. Scoped to those directories to avoid false positives on safe `__file__` usage in tests (binary lookup) and harness code (already correct). Acts as a ratchet — only checks changed files, so existing violations don't block until someone modifies those files. Added 4 integration tests to `test_gate.py`. All 104 tests pass.

## Key decisions

1. **Rust in-process** over Python delegation — `read_to_string` + `contains()` avoids subprocess overhead, no regex crate needed
2. **Exact string match** `Path(__file__).resolve().parent.parent` — catches all 9 instances, no false positives on safe single `.parent` usage (dashboard TEMPLATES_DIR)
3. **Universal** (all branches) — code quality check, not agent-specific like sovereignty/migrations
4. **Silent skip** on unreadable files (`if let Ok(content)`) — fail only on positive evidence

## Commits

- (pending) — Path antipattern gate check + 4 tests + 2 rules updates

## State at session end

- Changes not yet committed or pushed
- 9 existing files still have the antipattern (ratchet: won't trigger until modified)
- Next step: fix existing gateway files to use `resolve_project_root()` from `harness/common.py` (separate task)
