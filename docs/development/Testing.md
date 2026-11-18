> Status: adopted | Audience: anyone writing or reviewing a change | See also:
> [Getting-Started.md](Getting-Started.md), [Architecture.md](Architecture.md)

# Testing

## The three tiers

```text
tests/unit/          Fast, isolated pure logic — frame selection math, crypto, timezones
tests/integration/   Real HTTP request/response cycles against real SQLite + Alembic + filesystem
tests/e2e/           Playwright browser workflows + axe accessibility checks against the running stack
```

The suite is weighted deliberately toward **integration**: real FastAPI
request/response cycles against a migrated SQLite database and real temporary
media directories catch the most failures for the least fragility.

## Acceptance-first workflow

Outside-in, in this order:

1. Write the acceptance test from the requirement itself and watch it fail.
2. Write the integration test for the API/storage slice underneath.
3. Write unit tests for pure logic with tricky edge cases (timezones, DST,
   frame matching).
4. Write code until everything is green.
5. Run `just schema-drift` — SQLAlchemy models and Alembic migrations must
   agree.

## Never mock the datastore

Tests run against real SQLite databases migrated via Alembic (using temporary
on-disk files with WAL enabled to match production concurrency semantics, not
unmigrated `create_all()` shortcuts).

## Guardian suites

Logic whose silent failure would produce wrong results or leak secrets gets a
**named guardian suite**: a dedicated, edge-case-heavy test module for that one
invariant.

**Standing rule: change the guarded logic, update its guardian in the same
change.**

| Suite | What it protects |
| --- | --- |
| `tests/unit/test_guardian_frame_selection.py` | Mode 1 & Mode 2 frame selection math: inclusive date/time bounds, closest-frame `±tolerance` selection, deterministic tie-breaking, missing-day skipping, and DST spring-forward / fall-back transitions |
| `tests/integration/test_guardian_secret_encryption.py` | Camera credential encryption at rest: verifies RTSP/HTTP credentials are stored encrypted in SQLite and never echoed in API responses or error payloads |
| `tests/integration/test_guardian_role_boundaries.py` | Role authorization matrix: verifies `Viewer` accounts can browse/play/export on enabled cameras and are rejected (`403`) on every camera-admin, user-admin, and system-settings mutation |

## Determinism

Freeze time in scheduler, TTL-expiration, and timezone tests; seed any
randomness. Never rely on wall-clock `sleep` for cache-expiry assertions.

## No silent skips

No test is skipped without a reason string linking the issue that will unskip
it (`@pytest.mark.skip(reason="#123: ...")`).

## Coverage is a ratchet

CI enforces a coverage floor that only ever rises.

## Accessibility gate

End-to-end Playwright runs include `@axe-core/playwright` checks against the
real built UI views as a hard CI gate on release PRs.

## Running the suite

```bash
just test               # unit + integration tiers (or pass 'unit' / 'integration')
just e2e                # Playwright end-to-end + accessibility tier against running stack
```
