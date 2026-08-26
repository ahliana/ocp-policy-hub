# Contributing to OCP CE HR Policy Searcher

Thanks for your interest in contributing! This project helps the [Open Compute Project](https://www.opencompute.org/) track global data center heat reuse policies, and community contributions are welcome.

## Quick Start

```bash
git clone https://github.com/ahliana/OCP-CE-HR-Policy-Searcher.git
cd OCP-CE-HR-Policy-Searcher
.\setup.ps1 -Dev        # Linux/macOS: ./setup.sh --dev
```

This installs all dependencies including dev tools (pytest, ruff).

## Development Workflow

1. **Fork the repo** and clone your fork
2. **Create a branch**: `git checkout -b my-feature`
3. **Make your changes**
4. **Run tests and lint**:
   ```bash
   pytest                    # All 1085+ tests must pass (plus 3 skipped)
   ruff check src/ tests/    # No lint errors
   ```
5. **Commit** with a clear message (e.g., `feat: add Bulgarian keyword support`)
6. **Push and open a PR**

## What to Contribute

### Add a New Country or Region

This is the most impactful contribution. Follow these steps:

1. **Add keywords** in `config/keywords.yaml` — add terms in the country's language to all 7 categories (subject, policy_type, incentives, enabling, off_takers, context, energy)
2. **Create domain configs** in `config/domains/` — add 2-3 government websites (energy ministry + legislation database). Use `config/domains/germany.yaml` as a template
3. **If the language has compound words** (like German, Dutch, Hungarian), add its code to `COMPOUND_LANGUAGES` in `src/core/keywords.py`
4. **Add the region** to `VALID_REGIONS` in `src/core/config.py`
5. **Add TLD mappings** to `TLD_REGION_MAP` in `src/agent/domain_generator.py`
6. **Update groups** in `config/groups.yaml` with the new domain IDs
7. **Add a registry row** in `config/jurisdictions.yaml` for any new place your domains reference (kind, aliases, iso codes) — `tests/unit/test_jurisdictions.py::test_every_domain_slug_resolves` fails CI if a domain's `region` slug has no registry row
8. **Add tests** to `tests/unit/test_keywords.py` — see `TestEuropeanExpansionLanguages` for examples

### Improve Keyword Accuracy

Edit `config/keywords.yaml` to add terms, adjust weights, or add boost/penalty phrases. Test your changes:

```bash
python -m src.agent "Match keywords: 'your test text in any language'"
```

### Fix Bugs or Improve Code

All source code is in `src/`, organized as:

```
src/
├── agent/          # AI agent (CLI entry point, tool dispatch, domain generator)
├── core/           # Business logic (keywords, LLM, crawler, models, config)
├── orchestration/  # Parallel scan management, events, auditor
├── api/            # FastAPI REST API + WebSocket
├── output/         # Google Sheets export
├── mcp/            # MCP server (advanced)
└── storage/        # JSON persistence
```

See the [Architecture section](README.md#architecture) in the README for how the pipeline works.

## Code Style

- **Linter**: [ruff](https://docs.astral.sh/ruff/) — run `ruff check src/ tests/` before committing
- **Type hints**: Use Python type hints for function signatures
- **Docstrings**: Add docstrings to all public functions and classes
- **Models**: Use [Pydantic](https://docs.pydantic.dev/) BaseModel for data structures (see `src/core/models.py`)
- **Async**: Use `async/await` for I/O-bound operations (HTTP, API calls)
- **Config**: All tunable values go in YAML config files, not hardcoded in Python

## Testing

### Running the tests

One-time setup, then the suite runs the same way everywhere:

```bash
pip install -e ".[dev,browser]"   # browser extra = the playwright module tests patch; no browser download needed
pytest                            # full backend suite
cd frontend && npm ci && CI=true npx react-scripts test --watchAll=false   # frontend suite
```

Two things you'll see that are normal: `1 xfailed` in every pytest summary
(a deliberately-failing canary test proving failures still get reported -
never "fix" it), and a `gates/` folder you can ignore entirely. The gates
are the maintainer's local commit hooks; git does not clone hooks, so they
do nothing on your machine unless you opt in with `python gates/wire.py`
(optional, requires Python 3.13). CI runs this same suite on every pull
request either way - your PR is checked without any of it.

```bash
pytest tests/unit/             # Unit tests only (~1,700)
pytest tests/integration/      # Integration tests (~165, all marked `large`)
pytest -m "not large"          # The fast tier (~80s) - handy while iterating
pytest tests/unit/test_keywords.py -v  # Specific file, verbose
pytest --cov=src               # With coverage report
```

Tests carry size markers (`small` / `medium` / `large`, registered in
`pyproject.toml`). New tests should declare one honestly: `small` means
hermetic - no network (enforced), no real clock, no `data/`.

### Test Categories

| Category | Location | What it covers |
|----------|----------|----------------|
| Unit tests | `tests/unit/` | Keywords, models, API routes, agent tools, cache, crawler, verifier, sheets |
| Integration tests | `tests/integration/` | Full pipeline, agent loop, discovery workflow, onboarding |
| Edge case tests | across both | Missing files, invalid input, duplicates, error handling (~95 tests) |
| Onboarding tests | `test_full_pipeline.py` | Setup scripts, env files, banner, error messages (~51 tests) |

### Writing Tests

- Follow existing patterns in the test files
- Use `pytest.fixture` for shared setup
- Use `@pytest.mark.asyncio` for async tests
- Use `unittest.mock.AsyncMock` for mocking async methods
- Use `PolicyAgent.__new__(PolicyAgent)` pattern for testing the agent without a real API key (see `test_agent_loop.py`)

## Domain YAML Format

```yaml
domains:
  - id: "agency_cc"              # Unique ID: agency_countrycode
    name: "Government Agency Name"
    enabled: true
    base_url: "https://www.agency.gov.xx"
    region: ["eu", "eu_south", "country_name"]
    language: "xx"
    category: "energy_ministry"   # or: legislation_db, energy_regulator
    tags: ["energy_efficiency", "waste_heat"]
    policy_types: ["law", "regulation"]
    start_paths:
      - "/energy/policies/"
    allowed_path_patterns:
      - "/energy/*"
    blocked_path_patterns:
      - "/news/*"
      - "/press/*"
    max_depth: 3
    max_pages: 100
```

## PR Checklist

Before submitting a pull request, verify:

- [ ] `pytest` — all tests pass (no failures; `1 xfailed` is expected)
- [ ] `ruff check src/ tests/` — no lint errors
- [ ] Frontend touched? `cd frontend && CI=true npx react-scripts test --watchAll=false` passes too
- [ ] New tests carry an honest size marker (`small` / `medium` / `large`)
- [ ] New functions have docstrings
- [ ] New features have corresponding tests
- [ ] YAML files are valid (no syntax errors)
- [ ] Domain IDs follow the `agency_countrycode` convention
- [ ] If adding a language, it's added to all 7 keyword categories

## Questions?

Open an issue on GitHub or check the [README](README.md) for detailed documentation on every feature.
