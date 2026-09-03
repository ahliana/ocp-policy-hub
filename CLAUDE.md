# CLAUDE.md

## Project
OCP CE HR Policy Searcher — automated discovery of government data center waste heat reuse policies.
Built for the Open Compute Project (OCP) Heat Reuse subproject.

## Repos
- OCP org: `opencomputeproject/OCP-CE-HR-Policy-Searcher` (primary)
- Personal: `ahliana/OCP-CE-HR-Policy-Searcher` (mirror)
- `git push origin main` pushes to BOTH via dual push URLs
- Branch: `main` (not master)

## Commands
- `python -m src.agent` — run CLI agent (interactive mode)
- `python -m src.agent "message"` — single command mode
- `pytest` — run all tests (1085+, plus 3 skipped; must all pass before commits)
- `cd frontend && CI=true npx react-scripts test --watchAll=false` — run frontend tests (153, across 18 suites)
- `cd frontend && npm run e2e` — real-pointer Playwright smoke (needs the dev stack running and `npx playwright install chromium` once). **If your `.env` sets `ADMIN_TOKEN`, pass the same value as `E2E_ADMIN_TOKEN`** or the 13 admin-gated specs fail on the sign-in dialog rather than on anything real: `E2E_ADMIN_TOKEN=$(grep ^ADMIN_TOKEN= ../.env | cut -d= -f2-) npm run e2e`. One spec, `map.spec.js`'s admin toggle, assumes the opposite — a backend with no `ADMIN_TOKEN`, where clicking Admin reveals the panels directly — so it fails either way on an authed stack. Measured 2026-08-28: 26 of 27 pass with the token, 14 of 27 without.
- `ruff check src/ tests/` — lint (must pass before commits)
- `uvicorn src.api.app:app --port 8000` — run FastAPI server (there is no src.api __main__)
- `npm run dev` — run backend + React frontend together
- `python -m src.output.import_sheet [--dry-run]` — seed/refresh `data/policies.json` from the Google Sheets Staging worksheet (idempotent, dedupes by URL; `--dry-run` previews without writing)

## Architecture
```
src/
├── agent/        # CLI entry point (__main__.py), orchestrator (AI agent loop), tools
├── core/         # Business logic: crawler, keywords, llm, scanner, config, models, log_setup, jurisdictions
├── orchestration/ # Parallel scan_manager, events, auditor
├── api/          # FastAPI REST API + WebSocket (routes/ subpackage)
├── output/       # Google Sheets export (gspread + tenacity retry), Staging sheet import
├── mcp/          # MCP server (11 tools)
└── storage/      # JSON persistence (PolicyStore with atomic writes)
```
Config: `config/` — YAML files for domains, keywords, groups, settings, jurisdictions.
Data: `data/` — runtime output (policies.json, logs/, gitignored).

`src/core/jurisdictions.py` + `config/jurisdictions.yaml` are the jurisdiction registry — the single source of truth for what a place *is*: its kind (country/us_state/subnational/supranational/group), ISO codes, and parent rollup. `iso_numeric` is the join key into the world atlas (the world map's country fills); ISO 3166-2 `code` is the join key into admin-1 geometry (the drill-down). Adding a new source's region means adding one row here — `tests/unit/test_jurisdictions.py::test_every_domain_slug_resolves` fails CI if a domain config references a region slug with no registry row.

**Frontend:** React 19 (Create React App) in `frontend/`. The world map is precomputed TopoJSON→SVG assets in `frontend/src/assets/` (`worldAtlas110m.json` plus per-country admin-1 files under `assets/admin1/`) joined at render time to live `/api/coverage` data — nothing is computed client-side beyond that join. `frontend/src/config/drillableCountries.js` is the registry of which countries have admin-1 geometry to drill into; admin-1 chunks are lazy-loaded (dynamic `import()`) so they never bloat the initial bundle.

## Key Patterns
- Python 3.11+, async/await throughout, Pydantic models for all data structures
- `structlog` over stdlib logging — JSON file logs + human console output
- `log_audit_event()` for crash-safe audit trail (fsync to `data/logs/audit.jsonl`)
- `SESSION_ID` per-process for log correlation across concurrent agents
- Rate limit retry with exponential backoff at 3 layers: agent loop, scanner analysis, scanner screening
- Per-domain persistence: save after each domain completes, not just at scan end
- Incremental Google Sheets export: write per-domain, reconcile at end
- `PolicyAgent.__new__(PolicyAgent)` pattern to test agent without API key
- `anthropic.RateLimitError.__new__()` with mock response to create catchable test exceptions
- `AdminGateMiddleware`: `ADMIN_TOKEN` gates non-GET `/api`; unset = loopback-only (forwarded headers count as remote)
- Map testing: jsdom's synthetic pointer events carry no real pointer geometry, so drag/pointer-capture bugs on the map (see `usePanZoom.js`'s `setPointerCapture` handling and the "drill-dead bug" regression test in `WorldMap.test.js`) aren't fully caught by the unit suite — run the real-input e2e smoke (`cd frontend && npm run e2e`, against a live `npm run dev` stack) for any pointer-interaction change

## Storage
`src/storage/db.py` is the SQLite foundation behind `PolicyStore` and `LeadStore` — schema DDL, connection factory, and the JSON→SQLite migration. A single file, `data/policypulse.db` (WAL mode, foreign keys on), replaces `policies.json`/`leads.json`/the small `*_usage.json`/`*_seen.json` bookkeeping files. Store constructors and method signatures are unchanged; only the internals moved.
- **Tables:** `policies` and `leads` keep a handful of typed columns for SQL filtering plus a `raw` JSON column that is the source of truth — `get_all()` round-trips the exact dict the JSON version returned. `kv` consolidates `ask_usage.json`, `legiscan_usage.json`, `legiscan_seen.json`, `nim_seen.json` (name/data JSON blob; `url_cache.json` stays a plain file, out of scope). `jurisdictions` is a read-only mirror of `config/jurisdictions.yaml`, rebuilt from the YAML on every connection — never written to by the app.
- **Migration:** the first store constructed against a `data_dir` with legacy JSON but no `policypulse.db` triggers `migrate_json_to_db()` automatically, once. It writes everything in one transaction, then verifies (count, key-set, and full dict-equality per record) before considering it done — any mismatch raises and deletes the partial db. The legacy JSON files are never modified or deleted; they're the rollback path, and a failed migration gets a clean retry next boot.
- **FTS5 + fallback:** `policies_fts` indexes `policy_name`, `summary`, `key_requirements`, `jurisdiction` (external-content table, kept in sync by insert/update/delete triggers). `search()`'s jurisdiction filter runs as an FTS5 prefix `MATCH` when the local SQLite build has FTS5 (checked at runtime via `fts5_supported()`/`fts5_enabled()`), and falls back to a `LIKE` substring query otherwise — same signature, same result shape either way.

## Testing
- Unit tests: `tests/unit/` — mock everything, no network/API calls
- Integration tests: `tests/integration/` — full pipeline, agent loop, discovery
- Use `pytest.fixture`, `@pytest.mark.asyncio`, `unittest.mock.AsyncMock`
- Test file naming: `test_{module}.py` matching `src/{package}/{module}.py`

## Gotchas
- Windows: can't rename directories while processes have open handles — close sessions first
- `MagicMock` used as filesystem path creates junk directories (e.g., `MagicMock/`) — clean up after tests
- `MagicMock(spec=Exception)` can't be raised/caught — use `ExceptionClass.__new__(ExceptionClass)` instead
- `log_audit_event()` does NOT auto-include `session_id` — only includes explicitly passed `**fields`
- `ruff check` before commit — line length 100 chars (`pyproject.toml`)
- The project was renamed from `ocp-policy-hub` in March 2026 — no old-name references should exist
- License: MIT (matches OCP org standard)

## Read before changing the pipeline

- `docs/HOW_IT_WORKS.md` explains every filter, why it exists, the reviewed row that justified it, and where to change it.
- `docs/decisions/` holds one record per decision. A changed mind is a new record that supersedes the old one, never an edit.
- `docs/LESSONS.md` holds one entry per defect that cost time, with the test that fails if it comes back.
- `tests/unit/test_lessons_traceability.py` fails a commit when a lesson names a missing test, a test cites a missing record, or a link points nowhere. The Proofmark changelog gate refuses a `feat:` commit that changes source without a `docs/CHANGELOG.md` line.

The rule: a change to a filter, a prompt, a source's allow-list, the estimator or the review flow lands in the same PR as its decision record (new or superseding), its lesson if it came from a defect, and its `HOW_IT_WORKS.md` paragraph. Two traps worth reading before anything else: PL-001 (rules must run on source text, never a model summary) and PL-003 (check whether a thing ran before asking why it failed).

<!-- proofmark:begin -->
## Quality gates (Proofmark) - read before your first commit

This repo is gated. Four git hooks in `.git/hooks/` run on every commit and push,
whoever is driving. They are not optional and not something a session turns on.

**Fresh clone? You have NO enforcement yet.** Git does not clone `.git/hooks`,
so a clone holds the gate files and zero gating, silently. One command fixes
it: `python gates/wire.py` - creates the gate venv, installs the pinned
tools, wires the four hooks, and proves the toolchain with doctor.

- **pre-commit** runs ruff, vulture, the canary pair, and the fast test suite,
  and enforces a test-count floor that only ever goes up.
- **pre-push** runs the **full** suite (including tests marked `large`, which
  pre-commit skips) and refuses a push whose commits have no filled-in
  end-of-work report.

**When you get blocked, read the message - it names the gate and the fix.** Do
not work around it. The usual two:

- *"a `fix:` commit must add or change a test"* - write the test. That is the
  point: a fix without a test is a defect that can come back.
- *"no end-of-work report covers the commit being pushed"* - run
  `.venv-proofmark\Scripts\python.exe gates\gate.py ship`, answer the four questions it leaves
  blank, commit the report, push again.

`--no-verify` is **Ahliana's** break-glass, not an agent's. Every use is logged
and reviewed. If a gate is wrong, say so and stop - do not bypass it.

Slow suite? Mark the slow tests `@pytest.mark.large`. They sit out pre-commit
and still run in full at push.

Interpreters: `.venv\Scripts\python.exe` runs the tests;
`.venv-proofmark\Scripts\python.exe` runs the gate tooling. Bare `python` is
neither and will give you a different pytest.

Fuller detail, including this repo's layout and deploy procedure:
`docs/SESSION_BRIEF.md`.
<!-- proofmark:end -->
