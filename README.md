# OCP CE HR Policy Searcher

**Automated discovery of government data center heat reuse policies across 380+ domains in 40+ countries.**

OCP CE HR Policy Searcher crawls government websites and legislation databases (HTML and PDF), extracts policy content, scores it with multi-language keyword matching, and uses Claude AI for structured policy analysis. Talk to it in natural language through the CLI agent or the web interface, and it handles everything — discovering websites, scanning pages, and delivering organized results.

Built for the [Open Compute Project](https://www.opencompute.org/) to track global policy developments around data center waste heat recovery, energy efficiency mandates, and district heating integration. The web front end originated as an Uppsala University bachelor's thesis by Valdemar Jeirud, Elliot Loewenhielm, David Fors, and Alexander Stephanson.

> **⚙️ Everything is configurable.** Crawl depth, keyword weights, scoring thresholds, AI models, per-domain overrides — all controlled through simple YAML files in `config/`. No code changes needed. See [Configuration](#configuration) for the full list of knobs.

### Try it now

**Windows (PowerShell):**
```powershell
git clone https://github.com/opencomputeproject/OCP-CE-HR-Policy-Searcher.git
cd OCP-CE-HR-Policy-Searcher
.\setup.ps1
python -m src.agent
```

**Linux / macOS (bash):**
```bash
git clone https://github.com/opencomputeproject/OCP-CE-HR-Policy-Searcher.git
cd OCP-CE-HR-Policy-Searcher
./setup.sh
python -m src.agent
```

**Web interface** (after setup, requires Node.js):
```bash
npm run dev
# Frontend: http://localhost:3000   Backend API: http://localhost:8000
```

> **Browser engine:** Setup automatically installs Playwright Chromium for JavaScript-rendered sites. If it fails, run manually: `playwright install chromium`

```
You: Find heat reuse policies in Germany

  [Browsing available domains...]
  [Estimating cost for 'eu' scan...]

I found 15 German government websites in the database. A scan would
cost approximately $0.45. Let me scan them now...

  [Starting scan of 'germany' domains...]
  [Checking scan progress...]

Found 3 policies:

1. **Energy Efficiency Act (EnEfG)** - Germany
   Requires data centers above 500kW to reuse waste heat.
   Relevance: 9/10

2. ...
```

---

## Using the App (Visitors)

Anyone can browse the deployed web interface for free - no account, API key, or sign-in required:

- **Explore the map** — click a country to see the policies found there; double-click to drill into its states or provinces.
- **View found policies** — filter, search, and expand any result in the policy list below the map.
- **Ask questions** — the "Ask about found policies" box answers in your own language, using only what has already been discovered.

Scanning for *new* policies costs API credits (Anthropic + LegiScan) and lives behind the collapsible **Admin** area — that part is for operators running their own deployment. See [Quick Start](#quick-start) to set one up.

---

## World Map

The map colors every tracked place by what's been found there — but there are two grays, deliberately: an **untracked** country (no coverage record — nobody has looked yet) never gets confused with a **tracked-empty** one (sources are watched there, but no qualifying policy has surfaced yet). Green depth then scales with how many policies were found, from a handful to a couple dozen. Micro-dot markers keep small countries (Singapore, the Benelux states) clickable even when their outline is too small to hit reliably, and off-map places (the EU, other supranational bodies) sit in a chip tray beside the map instead of a fill, since they have no shape of their own.

Countries with subnational data drill down one level further: double-click (or use the panel's "Explore regions" action) to break the country out into its states or provinces, each shaded the same way. A federal/nationwide policy is kept visually and numerically distinct from a single-state one, and the two reconcile — national plus every region adds up to the country's total. Today that drill-down works for the US, Germany, and Belgium; it's data-driven, so a country lights up automatically once it has both admin-1 map geometry and state/province-level coverage.

Clicking any tracked place opens a side panel with its top policies and a **"View found policies"** button that jumps straight to the existing results list — that's the free, no-credentials action every visitor gets. A **"Scan for new policies"** link sits alongside it, but that one spends API credits and is admin-only (see [Admin/reader mode](#key-features)).

A stat strip above the map keeps the big picture honest at a glance: total tracked sources, how many places have any coverage at all, and how many policies have been found so far.

![World map](docs/images/world-map.png)
![Drill-down](docs/images/us-drilldown.png)

---

## Table of Contents

- [Using the App (Visitors)](#using-the-app-visitors)
- [World Map](#world-map)
- [Key Features](#key-features)
- [Geographic Coverage](#geographic-coverage) — 40+ countries, coverage depth
- [What This Tool Produces](#what-this-tool-produces) — the two surfaces, and who sees unreviewed records
- [Documentation](#documentation) — how it works, decisions, lessons, changelog
- [Architecture](#architecture)
- [Quick Start](#quick-start)
- [CLI Reference](#cli-reference) — every mode and flag
- [AI Agent](#ai-agent)
- [Logging & Observability](#logging--observability) — structured logs, audit trail, CLI viewer
- [Data Persistence](#data-persistence) — where results are stored, crash recovery
- [**⚙️ Configuration**](#configuration) — all the knobs you can tweak
- [Domain Groups](#domain-groups)
- [Keyword System](#keyword-system)
- [Running the Server](#running-the-server)
- [Production Deployment](#production-deployment)
- [API Reference](#api-reference)
- [WebSocket Events](#websocket-events)
- [Cost Estimation](#cost-estimation)
- [Examples](#examples)
- [Project Structure](#project-structure)
- [Development](#development)
- [MCP Server (Advanced)](#mcp-server-advanced)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [License](#license)

---

## Key Features

- **Interactive world map** — a coverage choropleth (Equal Earth projection, no tiles or map-provider keys, ~130KB precomputed asset) shows what's tracked before anyone types a word. Click a country for its found policies; double-click to drill into states/provinces (US, Germany, Belgium today — drillability is data-driven, so more countries light up as subnational data arrives). Pan, zoom, and micro-dot markers keep small countries clickable
- **Natural language AI agent** — ask questions in plain English, the agent handles scanning, discovery, and analysis
- **Web interface** — React front end with chat, region-based scanning, a filterable policy list, and API key management (`npm run dev`)
- **Web search + auto-discovery** — finds new government websites via web search and permanently adds them to the database
- **380+ government domains** across 40+ countries, including searchable legislation databases (EUR-Lex, Legifrance, wetten.overheid.nl, RIS, Finlex, Riigi Teataja, e-Gov Japan, law.go.kr, and US state legislatures)
- **PDF ingestion** — statutes published as PDFs are fetched and text-extracted, not skipped
- **Recall-first screening** — policies that merely AFFECT heat reuse (district heating mandates, building codes, EED transpositions, permitting rules) are kept, not only pages that name data centers; low-confidence rejections escalate to the stronger model
- **Structured sources (early signals)** — query legislation APIs directly instead of crawling: Sweden, UK, Canada, Denmark work with no keys; LegiScan (50 US states), GovInfo, regulations.gov, and Germany's DIP activate when their free API keys are set. The EU transposition tracker diffs each directive's national implementing measures and surfaces newly notified national laws
- **News tripwire** — `python -m src.agent --signals` sweeps GDELT, Google News, and trade feeds into a lead queue; leads are chased (full analysis) or dismissed by a human, so model spend stays deliberate. A weekly GitHub Actions workflow automates the sweep
- **Lifecycle stages** — every policy is tagged proposed / consultation / in committee / passed / enacted / transposition notified, filterable in the UI as Upcoming vs Enacted
- **Scan channels** — choose which source families a scan consults: website crawling (the main cost driver), law databases, EU transposition, news signals
- **Admin/reader mode** — set ADMIN_TOKEN and scans, chat, and review actions require the token while browsing stays open to everyone; unset restricts those actions to loopback clients (local single-user use). Public deployments must still set ADMIN_TOKEN: behind a same-host reverse proxy, remote traffic reaches the app from a local address, so the loopback restriction alone is not a substitute for the token
- **Ask about policies (public natural language)** — every visitor gets an "Ask about policies" box that answers questions from the stored policy library in their own language, citing official URLs. It uses a restricted read-only agent (cannot scan, search the web, or add domains), and spend is bounded by per-IP rate limiting plus an admin-set daily question cap
- **Cost levels** — the admin picks low / standard / high in Settings; it selects the models used by scans, discovery, and reader answers, and applies immediately to API-, chat-, and cron-triggered jobs
- **Priority crawling** — law-like URLs are fetched first so the page budget reaches legislation instead of press pages
- **Rejection observability** — every dropped page is counted by stage and logged with score and matched terms; near misses are tracked for tuning
- **Parallel scanning** — scan multiple domains concurrently with configurable workers
- **Multi-language keyword matching** — 7 categories across 20 languages (EN, DE, FR, NL, SV, DA, IT, ES, NO, FI, IS, PL, PT, CS, EL, HU, RO, JA, KO, AR) including statute vocabulary (EED, EnEfG, Warmtewet, Varmeforsyningsloven) and compound word support
- **⚙️ Fully configurable** — crawl depth, keyword weights, scoring thresholds, AI models, and per-domain overrides via YAML files ([details](#configuration))
- **Two-stage AI analysis** — cheap Haiku screening filters irrelevant pages before expensive Sonnet extraction
- **Real-time progress** — WebSocket events stream scan progress to your frontend
- **Deterministic verification** — catches jurisdiction mismatches, impossible dates, generic names, and duplicates without LLM calls
- **Post-scan auditor** — one bounded LLM call per scan generates strategic recommendations
- **JavaScript SPA support** — Playwright headless Chromium renders JavaScript-heavy sites (e.g., Virginia LIS) that httpx can't handle
- **URL caching** — full-content change detection; positive verdicts cached 30 days, negative verdicts 7 days so screening improvements take effect quickly
- **Cost-aware** — cost estimation before you run; the recall improvements route more pages to the AI models, so budget the first full scan accordingly
- **Google Sheets export** — automatically writes discovered policies to a Google Spreadsheet after each scan
- **Three entry points** — interactive CLI + web UI, REST API for frontends, MCP server for Claude Desktop

---

## Geographic Coverage

381 government domains across 40+ countries, organized by depth of coverage:

| Coverage | Countries / Regions | Domains |
|----------|-------------------|---------|
| **Deep** | EU (22 institutions + member states), UK (incl. Scotland, Wales, NI), US (all 50 states + federal), Germany (federal + 8 Länder), Switzerland (federal + Zurich), Nordic (5 countries), Canada (federal + 4 provinces) | ~300 |
| **Moderate** | India (national + 4 states), Australia (national + 2 states), UAE (federal + Abu Dhabi, Dubai), Brazil, Saudi Arabia | ~25 |
| **Basic** | Austria, Belgium, Ireland, Singapore, Japan, South Korea, Mexico, South Africa, Estonia, Luxembourg, plus most individual EU member states | ~40 |

**Legislation databases** (search-endpoint seeded, added 2026-07): EUR-Lex, Legifrance (FR), wetten.overheid.nl (NL), RIS (AT), Irish Statute Book, Finlex (FI), Riigi Teataja (EE), Legilux (LU), e-Gov laws (JP), law.go.kr (KR), retsinformation (DK), lovdata (NO), and bill search for the NY, WA, GA, OH, AZ, IL, VA, TX legislatures.

**Not seeing your country?** Use the `discover` workflow — the agent will web-search for government websites, generate domain configs, and add them permanently. Keywords work in 20 languages.

---

## What This Tool Produces

PolicyPulse writes to two places, for two different audiences. They do not hold the same thing, and the difference matters.

### 1. The Google Sheet — the workstream's working surface

| Worksheet | What lands there | How it is written |
|-----------|------------------|-------------------|
| `Staging` | Every policy a scan keeps, deduplicated by URL and tagged `PolicyPulse (automated)` | Appended each run |
| `Tips` | Leads from the weekly news sweep | Full snapshot, rewritten each run |

The `Staging` tab is a **review queue, not a list of conclusions**. Curators triage it there, and the `Review Status` column syncs both ways: statuses set in the sheet are imported back into the database, and statuses set in the app are written out to the sheet.

### 2. The web app — the public surface

The world map, the policy list, and the "Ask about found policies" box. No account, no API key, no sign-in.

### Review states

Every record carries one: `new` → `reviewed` → `promoted`, or `rejected`.

- `new` means machine output that no human has read yet.
- Non-admin visitors **never** see `rejected` records.

### Who sees unreviewed records

Both surfaces can show `new` records. The difference is that a curator opening the `Staging` tab knows it is a queue, and a member of the public looking at the map does not.

The `public_visibility` posture setting controls what the public sees:

| Mode | What the public sees by default | Reviewed-only toggle |
|------|----------------------------------|----------------------|
| `default_all` *(built-in default)* | Everything except rejected | Shown |
| `default_reviewed` | Reviewed only | Shown |
| `reviewed_only` | Reviewed only | Hidden |

**If you run a public deployment, choose this deliberately.** `default_all` suits an instance whose readers are curators working the queue. For a general audience, `default_reviewed` or `reviewed_only` means you are not publishing unreviewed machine claims about what governments require.

---

## Documentation

- [How it works, and why it filters what it filters](docs/HOW_IT_WORKS.md): the pipeline stage by stage with the numbers from the first real monthly scan, the reviewer's vocabulary mapped to the gate that answers each reason, how sources are found and added, how scans run and what they cost. The **Assumptions** and **Reading the Output** sections that used to live here moved there, so there is one place that explains the filters.
- [Decision records](docs/decisions/README.md): one file per decision that would be expensive to relearn, never edited after the fact.
- [Lessons register](docs/LESSONS.md): one entry per defect that cost time, with the test that now fails if it comes back.
- [What changed](docs/CHANGELOG.md), for reviewers and visitors.
- [Operations runbook](docs/OPERATIONS.md) and [what OCP needs to run it](docs/ADOPTION.md).
- [Session brief](docs/SESSION_BRIEF.md), for an AI session or a new contributor before the first commit.

A test (`tests/unit/test_lessons_traceability.py`) fails when a lesson names a test that does not exist, a test cites a decision record that does not exist, or a link on those pages points nowhere; a `feat:` commit that changes source must add a line to the changelog. That is how the documentation stays true.

---

## Architecture

```
   User (natural language)         React Frontend
       |                                  |
       | Agent CLI or                     | REST + WebSocket
       | POST /api/agent/run              | /api/agent/ws
       v                                  v
+------------------+             +-------------------+
| PolicyAgent      |             | FastAPI Server    |
| (Anthropic API   |             | /api/...          |
|  tool use loop)  |             |                   |
| Rate limit retry |             |                   |
+---------|--------+             +--------|----------+
          |                               |
          +----------- SHARED -----------+
                         |
              +----------|----------+
              |    SCAN MANAGER     |
              | Parallel dispatch   |
              | asyncio.Semaphore   |
              | Progress tracking   |
              +----------|----------+
                         |
           asyncio.gather() + Semaphore
          +------+------+------+------+
          |      |      |      |      |
       Worker Worker Worker Worker  ...
          |      |      |      |
          v      v      v      v
       Per-domain pipeline (deterministic):
       crawl -> extract -> url_filter -> keywords
       -> cache_check -> haiku_screen -> sonnet_analyze
       -> verify -> PolicyStore.save()  ← per-domain persistence
                    |
                    v
             +------|------+
             |  POST-SCAN  |
             | Verifier    |  (deterministic)
             | Auditor     |  (1 LLM call)
             | Sheets      |  (Google Sheets export)
             +-------------+
                    |
                    v
           data/policies.json     (crash-resilient)
```

The **AI agent** is the primary entry point. It uses the Anthropic API's tool use feature to orchestrate 15 tools (13 policy tools + web search + add domain) in a conversation loop. Users ask questions in natural language and the agent handles everything — including discovering new government websites via web search. All activity is logged to structured JSON files with crash-safe audit events for critical operations.

**Why this design:** Per-page agent reasoning costs 5-10x more (~$17-35 vs ~$3.50/full scan) with negligible accuracy gain. The multi-stage funnel drops 90% of pages before any LLM call. The agent drives the system at a *strategic* level (discover sites, start scans, investigate URLs, review audit insights), while the pipeline stays deterministic for reliability and cost.

---

## Quick Start

### Prerequisites

- Python 3.11+
- An [Anthropic API key](https://console.anthropic.com/) (for LLM analysis)

### Install

```powershell
git clone https://github.com/opencomputeproject/OCP-CE-HR-Policy-Searcher.git
cd OCP-CE-HR-Policy-Searcher
.\setup.ps1             # Linux/macOS: ./setup.sh
```

The setup script creates a virtual environment, installs all dependencies, and walks you through configuration — it prompts for your Anthropic API key and (optionally) Google Sheets credentials. On Windows, if you get a script execution error, run `Set-ExecutionPolicy RemoteSigned -Scope CurrentUser` first.

### Configure

The setup script prompts you for credentials interactively. If you need to change them later, edit `.env` directly:

```
ANTHROPIC_API_KEY=sk-ant-api03-your-real-key-here
```

Get your key at [console.anthropic.com](https://console.anthropic.com/).

The `.env` file is auto-loaded on startup — no need to manually `source` or `export`. Credentials are resolved from the project root regardless of working directory.

#### Model Selection (optional)

Models are validated on startup. If a model is retired, the system auto-resolves to the newest model in the same family and logs a warning. To override the defaults, add to `.env`:

```
ANALYSIS_MODEL=claude-sonnet-4-6
SCREENING_MODEL=claude-haiku-4-5-20251001
```

### Google Sheets Setup

To export discovered policies to Google Sheets (in addition to `data/policies.json`):

1. **Create a Google Cloud service account** with Sheets API access
2. **Download the JSON key file**
3. **Provide credentials** — the setup script accepts either format:

   **Option A: File path** (easiest)
   ```
   GOOGLE_CREDENTIALS_FILE=path/to/service-account.json
   ```

   **Option B: Base64-encoded string**
   ```bash
   # Linux/macOS
   base64 -i service-account.json | tr -d '\n'

   # PowerShell
   [Convert]::ToBase64String([IO.File]::ReadAllBytes("service-account.json"))
   ```
   ```
   GOOGLE_CREDENTIALS=<paste the base64 string as one unbroken line>
   ```

   **Option C: Raw JSON** (auto-detected)
   ```
   GOOGLE_CREDENTIALS={"type":"service_account","project_id":"..."}
   ```

4. **Add your spreadsheet ID:**
   ```
   SPREADSHEET_ID=1aBcDeFgHiJkLmNoPqRsTuVwXyZ
   ```
   The spreadsheet ID is the long string in your Google Sheet URL between `/d/` and `/edit`.

5. **Share the spreadsheet** with the service account email (found in the JSON key file under `client_email`)

Without these variables, policies are saved to `data/policies.json` only.

The Staging sheet doubles as the canonical cross-machine dataset: once it holds
reviewed policies, a fresh deployment can seed its local store from it instead
of re-scanning — see `python -m src.output.import_sheet` in [CLI Reference](#cli-reference).

### Run the AI Agent (recommended)

```bash
python -m src.agent
```

This starts an interactive session where you can ask questions in plain English. See [AI Agent](#ai-agent) for details.

> **⚙️ Want to tweak how it searches?** All scanning behavior is controlled by YAML files in `config/`:
> - **`config/settings.yaml`** — crawl depth, AI models, scoring thresholds, cost controls
> - **`config/keywords.yaml`** — keyword terms, weights, boost/penalty phrases, required category combos
> - **`config/domains/*.yaml`** — per-domain crawl targets, path filters, score overrides
> - **`config/url_filters.yaml`** — URL skip/block rules
>
> See [Configuration](#configuration) for the full reference.

### Run the REST API (for frontend development)

```bash
uvicorn src.api.app:app --port 8000
```

Open [http://localhost:8000/docs](http://localhost:8000/docs) for the interactive API documentation.

---

## CLI Reference

All agent modes and flags at a glance:

| Command | Description |
|---------|-------------|
| `python -m src.agent` | Interactive mode — chat with the agent |
| `python -m src.agent "message"` | Single command — run one query and exit |
| `python -m src.agent --discover Poland` | Discover mode — find government websites for a country |
| `python -m src.agent --deep` | Deep scanning mode — wider/deeper crawling (combine with any mode) |
| `python -m src.agent --deep --discover Japan` | Deep discovery — combine flags |
| `python -m src.agent --logs` | View recent log entries |
| `python -m src.agent --logs audit` | View audit trail (scan starts, policy finds) |
| `python -m src.agent --logs --level error` | View only errors |
| `python -m src.agent --help` | Show full CLI help |
| `python -m src.output.import_sheet` | Seed/refresh `data/policies.json` from the Google Sheets Staging worksheet |
| `python -m src.output.import_sheet --dry-run` | Preview the import (map + summarize) without writing |
| `python -m src.output.import_sheet --data-dir /path/to/data` | Import into a non-default data directory |

`import_sheet` is the deployment-seeding path: a fresh install with the Staging
sheet already populated runs it once and the store (and therefore the map/list
UI) light up without a re-scan. It requires the same `GOOGLE_CREDENTIALS` /
`SPREADSHEET_ID` as the writer and is idempotent — re-running only imports rows
new since the last run (deduped by URL). Rows missing a URL or Name, or with
values that fail Policy validation, are skipped and reported by row number
rather than aborting the import.

### Deep Scanning Mode

The `--deep` flag overrides default settings for more thorough scanning at higher cost (~3-4x):

| Setting | Default | With `--deep` | Effect |
|---------|---------|--------------|--------|
| `max_depth` | 3 | 5 | Follow links 5 levels deep instead of 3 |
| `max_pages_per_domain` | 200 | 500 | Crawl up to 500 pages per site |
| `min_keyword_score` | 3.0 | 2.0 | Lower threshold catches more marginal pages |

Use `--deep` when you want to cast a wider net for policies that might be buried deeper in government websites. The tradeoff is higher API costs and longer scan times.

```bash
# Deep scan of Nordic countries
python -m src.agent --deep "Scan Nordic countries for new policies"

# Deep discovery of a new country
python -m src.agent --deep --discover "Czech Republic"
```

---

## AI Agent

The AI agent is the primary way to interact with OCP CE HR Policy Searcher. It uses natural language — no need to learn API endpoints or write code.

### Interactive Mode

```bash
python -m src.agent
```

```
OCP CE HR Policy Searcher
====================
I help you find data center heat reuse policies worldwide.

Try asking:
  "What countries are covered?"
  "Find heat reuse policies in Germany"
  "Scan Nordic countries for new policies"
  "How much would it cost to scan all EU domains?"

Type 'quit' to exit. Press Ctrl+C to interrupt a running operation.

You: _
```

### Single Command Mode

```bash
python -m src.agent "What countries have heat reuse mandates?"
```

### Discover Mode

Automatically search for and add government websites for a specific country:

```bash
python -m src.agent --discover Poland
python -m src.agent --discover "Czech Republic"
```

The agent will search for energy ministries, legislation databases, and policy documents in the country's native language, add relevant domains to the database (auto-assigned to the correct regional groups), and analyze the most promising pages.

### What the Agent Can Do

**Discover new websites** — The agent can search the web for government websites about heat reuse policies in any country, even ones not yet in the database. It permanently saves discovered sites for future scans.

```
You: Find government websites about heat reuse in Japan
  [Searching the web...]
  [Adding new domain: go.jp energy agency...]
I found 3 Japanese government websites with heat reuse content
and added them to the database for future scanning.
```

**Scan known websites** — The database has 360+ government websites. The agent can scan them to discover policies.

```
You: Scan Nordic countries for policies
  [Estimating cost for 'nordic' scan...]
  [Starting scan...]
  [Checking scan progress...]
Found 5 policies across Denmark, Sweden, and Finland...
```

**Analyze individual URLs** — Check any webpage for policy content without a full scan.

```
You: Analyze this page: https://www.bmwk.de/Redaktion/DE/Gesetze/Energie/EnEfG.html
  [Analyzing URL...]
This page contains the German Energy Efficiency Act (EnEfG)...
Relevance: 9/10
```

**Search existing results** — Query previously discovered policies by country, type, or keywords.

### Agent REST API

For frontend integration, the agent is also available via REST and WebSocket:

```bash
# REST endpoint
curl -X POST http://localhost:8000/api/agent/run \
  -H "Content-Type: application/json" \
  -d '{"message": "List Nordic domains"}'
```

**Response:**
```json
{
  "response": "Here are the Nordic domains...",
  "iterations": 3,
  "tools_called": ["list_domains"]
}
```

### Agent WebSocket

For real-time streaming (React frontend integration):

```javascript
const ws = new WebSocket('ws://localhost:8000/api/agent/ws');

ws.send(JSON.stringify({ message: "Scan quick domains" }));

ws.onmessage = (event) => {
  const msg = JSON.parse(event.data);
  switch (msg.type) {
    case 'text':       // Agent's reasoning text
    case 'tool_call':  // Tool being called (name + input)
    case 'tool_result': // Tool result
    case 'complete':   // Final response
    case 'error':      // Error occurred
  }
};
```

### Agent Tools (15 total)

| Tool | Description |
|------|-------------|
| `list_domains` | Browse available domains by group, region, category |
| `list_groups` | List all groups, regions, countries, and sub-national scan targets |
| `get_domain_config` | Full configuration for a specific domain |
| `start_scan` | Start a parallel scan of domain groups |
| `get_scan_status` | Check scan progress and results |
| `list_scans` | List all scans in this session (running, completed, failed) |
| `stop_scan` | Cancel a running scan |
| `analyze_url` | Run the full pipeline on any URL |
| `match_keywords` | Test keyword scoring on any text |
| `search_policies` | Search discovered policies with filters |
| `get_policy_stats` | Aggregate statistics across all scans |
| `get_audit_advisory` | Post-scan strategic recommendations |
| `estimate_cost` | Predict API costs before scanning |
| `web_search` | Search the web for new government websites |
| `add_domain` | Add a discovered website to the database permanently |

---

## Logging & Observability

All activity is logged to structured JSON files for debugging, auditing, and monitoring. Logs work identically across all entry points (CLI agent, REST API, MCP server).

### Log Files

| File | Format | Contents |
|------|--------|----------|
| `data/logs/agent.log` | JSON-lines | All application logs (rotated, 10 MB × 5 backups) |
| `data/logs/audit.jsonl` | JSON-lines | Critical events only — scan starts, completions, policy finds, session ends (crash-safe with fsync) |

### CLI Log Viewer

View logs without needing an API key:

```bash
python -m src.agent --logs                  # Last 30 log entries
python -m src.agent --logs audit            # Audit trail events
python -m src.agent --logs --level error    # Only errors
python -m src.agent --logs --level warning  # Warnings and above
python -m src.agent --logs --lines 100      # Show 100 entries
python -m src.agent --logs --scan-id abc    # Filter by scan ID
python -m src.agent --logs --json           # Raw JSON (for piping/scripting)
```

Flags can be combined: `python -m src.agent --logs audit --scan-id abc123`

### API Log Endpoints (for React frontend)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/logs` | Recent log entries (filterable by level, scan_id, session_id) |
| GET | `/api/logs/audit` | Audit trail events (filterable by event_type, scan_id) |
| GET | `/api/logs/info` | Log file paths, sizes, and current session ID |

**Example — fetch recent errors:**
```bash
curl "http://localhost:8000/api/logs?level=error&lines=20"
```

**Example — audit events for a specific scan:**
```bash
curl "http://localhost:8000/api/logs/audit?scan_id=abc123"
```

**Response format:**
```json
{
  "entries": [
    {
      "event": "policy_found",
      "level": "info",
      "scan_id": "abc123",
      "domain_id": "de_bmwk",
      "policy_name": "EnEfG",
      "timestamp": "2024-01-15T10:30:00Z",
      "session_id": "a1b2c3d4"
    }
  ],
  "count": 1
}
```

### Key Features

- **Structured JSON** — machine-parseable, grep-friendly, one JSON object per line
- **Crash-safe audit log** — `os.fsync()` after every write, survives power loss
- **Session IDs** — each process gets a unique ID, so concurrent agents sharing one log file can be distinguished
- **Correlation IDs** — `scan_id` and `domain_id` propagate through async tasks automatically via `structlog.contextvars`
- **Sensitive data redaction** — API keys, JWTs, and Google keys are stripped before reaching any log handler
- **Log rotation** — 10 MB per file, 5 backups, 60 MB max disk usage
- **Noisy library silencing** — httpx, anthropic SDK, asyncio debug messages suppressed

### Multiple Agents / Concurrent Sessions

When multiple agents or API workers share the same log file, each process writes a unique `session_id` into every log entry. Use it to filter:

```bash
# CLI: filter to one session
python -m src.agent --logs --session-id a1b2c3d4

# API: filter to one session
curl "http://localhost:8000/api/logs?session_id=a1b2c3d4"
```

---

## Data Persistence

Results are saved automatically and survive crashes, network errors, and rate limit interruptions.

### Where Results Are Stored

| Location | Contents | Written When |
|----------|----------|-------------|
| `data/policies.json` | All discovered policies (deduplicated by URL) | After each domain completes scanning |
| `data/url_cache.json` | Cached URLs with 30-day TTL | Periodically during scan + at scan end |
| Google Sheets (if configured) | Policies exported to staging sheet | After each domain completes scanning |
| `data/logs/audit.jsonl` | Critical events: scan start/complete, policies found, session end | Immediately (fsync'd to disk) |

### Crash Recovery

Both `data/policies.json` and Google Sheets are updated **per-domain** as each domain finishes scanning — not at the end of the full scan. This means:

- If the process crashes at 9/12 domains, the first 9 domains' policies are safe on disk **and** in Google Sheets
- If a rate limit error interrupts the agent conversation, background scans continue running
- If you quit mid-scan (typing `quit` or pressing Ctrl+C), all policies found so far are already saved
- You can always check what was found with `search_policies` or by reading `data/policies.json`
- A reconciliation step at scan completion catches any policies that slipped through the per-domain export

### Rate Limit Handling

The agent automatically retries on Anthropic API rate limits (429) and overload errors (529):

- Up to 3 retries with exponential backoff (10s → 40s → 120s)
- Uses the `retry-after` header from the API when available
- Shows a friendly "waiting..." message during retries
- If all retries are exhausted, returns a helpful error message reminding you that scan data is saved

### Smart Polling

During scans, the agent uses adaptive polling intervals to avoid burning API calls:

| Scan Progress | Wait Between Checks |
|--------------|-------------------|
| < 25% complete | 30 seconds |
| 25-75% complete | 45 seconds |
| > 75% complete | 20 seconds |

---

## Running the Server

### REST API Server

```bash
# Development (auto-reload)
uvicorn src.api.app:app --reload --port 8000

# Production
uvicorn src.api.app:app --host 0.0.0.0 --port 8000 --workers 4
```

The server provides:
- Interactive API docs at `/docs` (Swagger UI)
- Alternative docs at `/redoc`
- Health check at `/health`

---

## Production Deployment

The Dockerfile builds the React frontend and the API into one image; one
FastAPI process serves both `/api/*` and the built app on port 8000.

```bash
cp config/example.env .env    # fill in ANTHROPIC_API_KEY, ADMIN_TOKEN, etc.
docker compose up -d
```

`docker-compose.yml` binds the container to `127.0.0.1:8000` — put a
reverse proxy (Caddy) in front of it for TLS and the public hostname. The
full operator runbook — every environment variable, backups, the
signals/scan schedule, cost controls, and the OCP ownership-transfer
checklist — is in **[docs/OPERATIONS.md](docs/OPERATIONS.md)**.

---

## API Reference

### Domains

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/domains` | List all domains |
| GET | `/api/domains?group=eu` | Filter by group or region |
| GET | `/api/domains?category=energy_ministry` | Filter by category |
| GET | `/api/domains?tag=mandates` | Filter by tag |
| GET | `/api/domains/{domain_id}` | Get full config for one domain |
| GET | `/api/groups` | List available domain groups |
| GET | `/api/regions` | List available regions |
| GET | `/api/categories` | List valid categories |
| GET | `/api/tags` | List valid tags |

**Example:**
```bash
# Get all EU energy ministry domains
curl "http://localhost:8000/api/domains?group=eu&category=energy_ministry"
```

**Response:**
```json
{
  "domains": [
    {
      "id": "bmwk_de",
      "name": "German Federal Ministry for Economic Affairs",
      "base_url": "https://www.bmwk.de",
      "region": ["eu", "germany", "eu_central"],
      "category": "energy_ministry",
      "tags": ["mandates", "energy_efficiency", "waste_heat"]
    }
  ],
  "count": 1
}
```

### Scans

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/scans` | Start a new parallel scan |
| GET | `/api/scans` | List all scans |
| GET | `/api/scans/{scan_id}` | Get scan status with per-domain progress |
| DELETE | `/api/scans/{scan_id}` | Cancel a running scan |
| WebSocket | `/api/scans/{scan_id}/ws` | Real-time progress stream |
| POST | `/api/cost-estimate?domains=eu` | Estimate scan costs |

**Start a scan:**
```bash
curl -X POST http://localhost:8000/api/scans \
  -H "Content-Type: application/json" \
  -d '{"domains": "eu", "max_concurrent": 5}'
```

**Request body (ScanRequest):**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `domains` | string | `"quick"` | Domain group to scan |
| `max_concurrent` | integer (1-20) | `5` | Parallel workers |
| `skip_llm` | boolean | `false` | Skip LLM analysis (keywords only) |
| `dry_run` | boolean | `false` | Resolve domains without scanning |
| `deep` | boolean | `false` | Use deeper crawl defaults (`max_depth=5`, `max_pages=500`, keyword score `2.0`) |
| `discover` | boolean | `false` | Run the agent discovery workflow for `domains` instead of a direct scan |
| `category` | string | `null` | Additional category filter |
| `tags` | string[] | `null` | Additional tag filters |
| `policy_type` | string | `null` | Additional policy type filter |

Choose one scan mode per request: standard (`deep=false`, `discover=false`),
deep (`deep=true`), or discover (`discover=true`).

**Response:**
```json
{
  "scan_id": "a1b2c3d4",
  "status": "running",
  "domain_count": 10
}
```

**Check scan status:**
```bash
curl http://localhost:8000/api/scans/a1b2c3d4
```

**Response (detailed):**
```json
{
  "scan_id": "a1b2c3d4",
  "status": "completed",
  "domain_count": 10,
  "policy_count": 7,
  "progress": {
    "total": 10,
    "completed": 10,
    "domains": [
      {
        "domain_id": "bmwk_de",
        "domain_name": "German Federal Ministry",
        "status": "completed",
        "pages_crawled": 45,
        "pages_filtered": 38,
        "keywords_matched": 12,
        "policies_found": 3,
        "errors": 0
      }
    ]
  },
  "policies": [ ... ],
  "cost": {
    "input_tokens": 125000,
    "output_tokens": 8500,
    "screening_calls": 12,
    "analysis_calls": 7,
    "total_usd": 0.45
  },
  "audit_advisory": "## Key Findings\n..."
}
```

### Policies

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/policies` | Search policies with filters |
| GET | `/api/policies/stats` | Aggregate statistics |

**Search policies:**
```bash
# Find German laws with relevance >= 7
curl "http://localhost:8000/api/policies?jurisdiction=Germany&policy_type=law&min_score=7"
```

`place` is a jurisdiction-registry slug (see `src/core/jurisdictions.py`) and composes with the other filters. A country slug is descendant-inclusive — `place=us` also returns federal policies plus every US state's; a subnational or supranational slug (`place=california`, `place=eu`) matches exactly. An unknown slug 404s.

**Response:**
```json
{
  "policies": [
    {
      "url": "https://www.bmwk.de/...",
      "policy_name": "Energy Efficiency Act (EnEfG)",
      "jurisdiction": "Germany",
      "policy_type": "law",
      "summary": "Requires data centers above 500kW to reuse waste heat...",
      "relevance_score": 9,
      "effective_date": "2024-03-01",
      "key_requirements": "Data centers must achieve PUE of 1.2 by 2030...",
      "verification_flags": []
    }
  ],
  "count": 1
}
```

### Coverage (World Map)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/coverage` | Countries, supranational entries, and totals for the world map |
| GET | `/api/coverage/children?parent=<slug>` | One country broken out by state/province |

**Get the world coverage aggregate:**
```bash
curl "http://localhost:8000/api/coverage"
```

**Response:**
```json
{
  "countries": [
    {
      "name": "Germany",
      "slug": "germany",
      "iso_numeric": "276",
      "sources": 23,
      "policies": 5,
      "top_policy_names": ["Energy Efficiency Act (EnEfG)"],
      "children_with_data": 3
    }
  ],
  "supranational": [
    {
      "name": "European Union",
      "slug": "eu",
      "sources": 12,
      "policies": 4,
      "top_policy_names": ["EED Recast"]
    }
  ],
  "totals": {"sources": 361, "policies": 42}
}
```

`children_with_data` is the drill affordance: it counts the country's `us_state`/`subnational` children (per the jurisdiction registry) with at least one policy or source resolved directly to them, and drives whether the frontend offers "Explore this country's regions." The `supranational` bucket also carries jurisdictions the registry has no map shape for — group entities like the EU, and any country the registry tracks without an `iso_numeric` (e.g. Kosovo) — so nothing tracked is ever left off the response.

**Break one country out by state/province:**
```bash
curl "http://localhost:8000/api/coverage/children?parent=us"
```

**Response:**
```json
{
  "parent": {"slug": "us", "name": "United States", "iso_numeric": "840"},
  "national": {"sources": 6, "policies": 2, "top_policy_names": ["..."]},
  "children": [
    {
      "slug": "minnesota",
      "name": "Minnesota",
      "kind": "us_state",
      "code": "US-MN",
      "sources": 3,
      "policies": 1,
      "top_policy_names": ["..."]
    }
  ],
  "totals": {"sources": 154, "policies": 7}
}
```

Unlike the world view, nothing is rolled up here: a policy or source lands under `national` only when it resolves to the country itself, and under a child only when it resolves to that exact state/province — never both. `totals` reconciles with the country's entry in `/api/coverage`: policies sum exactly (`national + every child`), while `totals.sources` counts distinct domains (a domain tagged for both the country and one of its states can appear in both buckets, so per-bucket sums may exceed the total). A `parent` slug that isn't a known country jurisdiction 404s.

### Ask (Public Q&A)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/ask` | Answer a reader's question from stored policies only |

**Ask a question:**
```bash
curl -X POST http://localhost:8000/api/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "What has Germany required for data center heat reuse?"}'
```

**Response:**
```json
{
  "answer": "Germany's Energy Efficiency Act (EnEfG) requires...",
  "tool_calls": 2,
  "remaining_today": 187
}
```

This endpoint is open to every visitor — no admin token required, even when `ADMIN_TOKEN` is set (it's one of two explicit exemptions in `AdminGateMiddleware`, alongside `POST /api/leads`). A small Haiku-powered reader agent answers strictly from stored policies, citing official URLs — it has no web search or scanning tools, so it can only report what's already been found. Spend is bounded three ways: the reader agent's own 5-iteration cap, a per-IP sliding-window rate limit (`429` with `Retry-After` when exceeded, default 5/minute), and a persisted daily question cap the admin controls in Settings (default 200/day, also `429` when exhausted). Returns `503` if the question service isn't configured (no `ANTHROPIC_API_KEY`) or if an admin has disabled it.

### Analysis

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/analyze` | Full pipeline on a single URL |
| GET | `/api/config/keywords` | View keyword configuration |
| GET | `/api/config/settings` | View application settings |

**Analyze a single URL:**
```bash
curl -X POST http://localhost:8000/api/analyze \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.bmwk.de/Redaktion/DE/Gesetze/Energie/EnEfG.html"}'
```

**Response:**
```json
{
  "url": "https://www.bmwk.de/...",
  "title": "Energy Efficiency Act",
  "language": "de",
  "word_count": 3200,
  "crawl_status": "success",
  "keyword_score": 14.5,
  "keyword_matches": [
    {"term": "data center", "category": "context", "weight": 1.0, "language": "en"},
    {"term": "waste heat", "category": "subject", "weight": 3.0, "language": "en"}
  ],
  "categories_matched": ["context", "subject", "policy_type"],
  "passes_keyword_threshold": true,
  "screening": {"relevant": true, "confidence": 9},
  "policy": { ... },
  "verification_flags": []
}
```

### Logs

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/logs` | Recent log entries (query: `level`, `scan_id`, `session_id`, `lines`) |
| GET | `/api/logs/audit` | Audit trail events (query: `event_type`, `scan_id`, `lines`) |
| GET | `/api/logs/info` | Log file paths, sizes, and current session ID |

See [Logging & Observability](#logging--observability) for full details and examples.

---

## WebSocket Events

Connect to `/api/scans/{scan_id}/ws` for real-time scan progress.

```javascript
const ws = new WebSocket('ws://localhost:8000/api/scans/a1b2c3d4/ws');

ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  console.log(data.type, data.data);
};
```

### Event Types

| Type | Data | Description |
|------|------|-------------|
| `scan_started` | `{domain_count}` | Scan begins |
| `domain_started` | `{domain_name}` | Domain processing starts |
| `page_fetched` | `{url, status, response_ms}` | Page downloaded |
| `keyword_match` | `{url, score, categories}` | Keywords matched |
| `policy_found` | `{url, policy_name, relevance}` | Policy extracted |
| `domain_complete` | `{pages, policies, errors}` | Domain finished |
| `verification_complete` | `{flagged, passed}` | Verification done |
| `audit_complete` | `{advisory}` | Auditor recommendation ready |
| `scan_complete` | `{total_policies, cost_usd}` | Scan finished |
| `error` | `{error, domain_id?}` | Error occurred |

Late-connecting clients receive full event history on connect.

---

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | — | **Required** for LLM analysis |
| `ANALYSIS_MODEL` | `claude-sonnet-4-6` | Model for full policy analysis. Auto-validated on startup |
| `SCREENING_MODEL` | `claude-haiku-4-5-20251001` | Cheapest model for initial page filtering. Auto-validated on startup |
| `OCP_HOST` | `0.0.0.0` | Server bind address |
| `OCP_PORT` | `8000` | Server port |
| `OCP_MAX_CONCURRENT` | `5` | Default parallel workers |
| `OCP_CONFIG_DIR` | `config` | Configuration directory |
| `OCP_DATA_DIR` | `data` | Data/cache directory |
| `GOOGLE_CREDENTIALS_FILE` | — | Path to Google service account JSON key file. See [Google Sheets Setup](#google-sheets-setup) |
| `GOOGLE_CREDENTIALS` | — | Base64-encoded or raw JSON service account key (alternative to file path) |
| `SPREADSHEET_ID` | — | Google Spreadsheet ID from the sheet URL (for Sheets export) |

### Settings (config/settings.yaml)

**Crawler:**

| Setting | Default | Description |
|---------|---------|-------------|
| `max_depth` | `3` | How many links deep to crawl (1-10) |
| `max_pages_per_domain` | `200` | Page budget per domain |
| `delay_seconds` | `3.0` | Delay between requests (min 0.5) |
| `timeout_seconds` | `30` | HTTP timeout |
| `max_concurrent` | `3` | Concurrent requests per domain |
| `user_agent` | `OCP-PolicyHub/1.0` | HTTP user agent |
| `respect_robots_txt` | `true` | Honor robots.txt |
| `max_retries` | `3` | Retry failed requests |
| `force_playwright` | `false` | Use Playwright headless Chromium for all pages (normally per-domain via `requires_playwright`) |

**Analysis:**

| Setting | Default | Description |
|---------|---------|-------------|
| `min_keyword_score` | `3.0` | Minimum score to pass keyword filter |
| `min_relevance_score` | `5` | Minimum LLM relevance (1-10) |
| `min_keyword_matches` | `2` | Minimum distinct keyword matches |
| `enable_llm_analysis` | `true` | Enable Claude analysis |
| `analysis_model` | `claude-sonnet-4-6` | Model for full analysis (override via `ANALYSIS_MODEL` env var) |
| `screening_model` | `claude-haiku-4-5-20251001` | Model for screening (override via `SCREENING_MODEL` env var) |
| `enable_two_stage` | `true` | Haiku screening before Sonnet |
| `screening_min_confidence` | `5` | Minimum screening confidence (1-10) |

### URL Filters (config/url_filters.yaml)

Controls which URLs are crawled and analyzed:

- **`skip_paths`** — Paths skipped after fetching (substring match): `/login`, `/contact`, `/privacy`, `/cart`, `/careers`, etc.
- **`skip_patterns`** — Regex patterns skipped after fetching: date archives, pagination, UTM params
- **`crawl_blocked_patterns`** — Paths blocked *before* fetching (saves page budget): `/admin/*`, `/api/*`, `/search`, `/developer/*`
- **`skip_extensions`** — File types never fetched: `.pdf`, `.jpg`, `.css`, `.js`, `.zip`, etc.
- **`domain_overrides`** — Per-domain skip rules

### Tuning for Deeper Scanning

If the default settings miss policies buried deep in government websites, you can adjust three key knobs. Here's what each does and the cost tradeoff:

| Setting | Default | Wider Net | Effect | Cost Impact |
|---------|---------|-----------|--------|------------|
| `crawl.max_depth` | 3 | 5 | Follow links deeper into sites | ~2x more pages |
| `crawl.max_pages_per_domain` | 200 | 500 | Crawl more pages per domain | ~2.5x more pages |
| `analysis.min_keyword_score` | 3.0 | 2.0 | Lower relevance threshold | ~2x more LLM calls |

**Quick way:** Use `python -m src.agent --deep` to temporarily apply all three overrides for a single session.

**Permanent change:** Edit `config/settings.yaml` directly:

```yaml
crawl:
  max_depth: 5              # was 3
  max_pages_per_domain: 500  # was 200

analysis:
  min_keyword_score: 2.0    # was 3.0
```

**Cost estimate:** A full scan of all 360+ domains at default settings costs ~$3.50. With `--deep`, expect ~$10-15. Use `estimate_cost` before scanning to check.

### Domain Configuration (config/domains/*.yaml)

Each domain YAML defines crawl targets:

```yaml
domains:
  - id: "bmwk_de"
    name: "German Federal Ministry for Economic Affairs"
    enabled: true
    base_url: "https://www.bmwk.de"
    region: ["eu", "germany", "eu_central"]
    category: "energy_ministry"
    tags: ["mandates", "energy_efficiency", "waste_heat"]
    policy_types: ["law", "regulation"]
    start_paths:
      - "/Redaktion/DE/Dossier/energieeffizienz.html"
    allowed_path_patterns:
      - "/Redaktion/DE/*"
    blocked_path_patterns:
      - "/Redaktion/DE/Pressemitteilungen/*"
    max_depth: 3
    max_pages: 100
    requires_playwright: false
```

---

## Domain Groups

Use groups to scan related sets of domains. Pass the group name as the `domains` parameter.

### Testing & Development

| Group | Domains | Description |
|-------|---------|-------------|
| `test` | 1 | Single domain for quick testing |
| `quick` | 2 | Germany + US federal (diverse test) |
| `sample_nordic` | 3 | Nordic country sample |
| `sample_apac` | 2 | Asia-Pacific sample |

### Regional

| Group | Domains | Description |
|-------|---------|-------------|
| `all` | 361 | Every enabled domain |
| `eu` | 46 | EU institutions + member states |
| `nordic` | 12 | Sweden, Denmark, Finland, Norway, Iceland |
| `eu_central` | 21 | Germany, Switzerland, Austria, France |
| `eu_west` | 3 | Netherlands, Belgium, Ireland |
| `eu_south` | 8 | Spain, Italy, Portugal, Greece |
| `eu_east` | 8 | Poland, Czech Republic, Hungary, Romania |
| `us` | 154 | US federal + all 50 states |
| `us_federal` | 6 | Federal agencies only |
| `us_states` | 148 | US state governments |
| `uk` | 74 | UK — England, Scotland, Wales, Northern Ireland |
| `germany` | 23 | Germany — federal + 8 Länder |
| `canada` | 13 | Canada — federal + 4 provinces |
| `india` | 10 | India — national + 4 states |
| `apac` | 7 | Singapore, Japan, South Korea, Australia |
| `middle_east` | 5 | UAE + Saudi Arabia |
| `north_america` | 10 | US federal + Canada national |

### Thematic

| Group | Domains | Description |
|-------|---------|-------------|
| `federal` | 8 | National/EU-level only (no states/provinces) |
| `leaders` | 9 | Countries with most advanced heat reuse policies |
| `emerging` | 7 | Countries with emerging regulations |
| `pending_legislation` | 24 | Active/pending bills and legislative tracking |

You can also scan by **country name** (`germany`, `canada`, `india`, `brazil`), **sub-national region** (`scotland`, `hessen`, `ontario`), **region name** (`middle_east`, `north_america`), or **individual domain ID**.

---

## Keyword System

The keyword matcher scores page content across 7 weighted categories in 20 languages.

### Categories

| Category | Weight | Example Terms |
|----------|--------|---------------|
| `subject` | 3.0 | waste heat recovery, heat reuse, Abwärmenutzung |
| `policy_type` | 2.0 | regulation, directive, mandate, Verordnung |
| `incentives` | 2.0 | grant, tax credit, subsidy, Förderung |
| `enabling` | 1.5 | roadmap, pilot program, strategy |
| `off_takers` | 1.5 | district heating, greenhouse, swimming pool |
| `context` | 1.0 | data center, server farm, hyperscale |
| `energy` | 1.0 | PUE, energy efficiency, decarbonization |

### Languages (20)

English (en), German (de), French (fr), Dutch (nl), Swedish (sv), Danish (da), Italian (it), Spanish (es), Norwegian (no), Finnish (fi), Icelandic (is), Polish (pl), Portuguese (pt), Czech (cs), Greek (el), Hungarian (hu), Romanian (ro), Japanese (ja), Korean (ko), Arabic (ar)

German, Dutch, Swedish, Danish, Norwegian, Finnish, Icelandic, Hungarian, Japanese, Korean, and Arabic use **substring matching** instead of word boundaries to handle compound words and scripts without word separators (e.g., "Rechenzentrumsabwärmenutzungsverordnung", "データセンター排熱利用", "مركز البيانات").

### Scoring

```
base_score = sum(category_weight for each matched keyword)

url_bonus:
  +1.0  government TLD (.gov, .gov.uk, .gouv.fr, .admin.ch)
  +1.5  legislation path (/bills/, /legislation/, /acts/)
  +1.0  bill number in URL (H.B., S.B., H.R., S.J.R.)

adjustments:
  +3.0  per boost keyword (high-value phrases like "data center heat reuse")
  -2.0  per penalty keyword (generic terms like "job opening")

final_score = max(0, base_score + url_bonus + boosts - penalties)
```

Default threshold: score >= 5.0 with >= 2 distinct matches, plus at least one required category combination (e.g., context + subject).

### Verification Flags

After LLM extraction, the deterministic verifier checks for:

| Flag | Description |
|------|-------------|
| `jurisdiction_mismatch` | Extracted jurisdiction doesn't match domain's region |
| `future_date` | Effective date more than 2 years in the future |
| `generic_name` | Policy name too generic (e.g., "Energy Policy") without bill number |
| `duplicate_url` | Same URL already found in this scan |
| `low_confidence_high_score` | Relevance 9+ on /about, /contact, /team pages |

---

## Examples

### Estimate costs before scanning

```bash
curl -X POST "http://localhost:8000/api/cost-estimate?domains=eu"
```

```json
{
  "domain_count": 10,
  "estimated_pages": 1000,
  "estimated_keyword_passes": 100,
  "estimated_screening_calls": 100,
  "estimated_analysis_calls": 50,
  "estimated_cost_usd": 1.23
}
```

### Run a quick scan

```bash
# Start scan
curl -X POST http://localhost:8000/api/scans \
  -H "Content-Type: application/json" \
  -d '{"domains": "quick", "max_concurrent": 2}'

# Check status (use the scan_id from the response)
curl http://localhost:8000/api/scans/a1b2c3d4

# View discovered policies
curl http://localhost:8000/api/policies?scan_id=a1b2c3d4
```

### Scan with filters

```bash
# Scan only energy ministries with waste heat tags
curl -X POST http://localhost:8000/api/scans \
  -H "Content-Type: application/json" \
  -d '{
    "domains": "eu",
    "category": "energy_ministry",
    "tags": ["waste_heat", "mandates"],
    "max_concurrent": 3
  }'
```

### Analyze a single URL

```bash
curl -X POST http://localhost:8000/api/analyze \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.bmwk.de/Redaktion/DE/Gesetze/Energie/EnEfG.html"}'
```

### Connect to WebSocket from JavaScript

```javascript
const scanId = 'a1b2c3d4';
const ws = new WebSocket(`ws://localhost:8000/api/scans/${scanId}/ws`);

ws.onmessage = (event) => {
  const msg = JSON.parse(event.data);

  switch (msg.type) {
    case 'domain_started':
      console.log(`Scanning: ${msg.data.domain_name}`);
      break;
    case 'policy_found':
      console.log(`Found: ${msg.data.policy_name} (relevance: ${msg.data.relevance})`);
      break;
    case 'scan_complete':
      console.log(`Done! ${msg.data.total_policies} policies, $${msg.data.cost_usd}`);
      break;
  }
};
```

### Keyword-only scan (no LLM costs)

```bash
curl -X POST http://localhost:8000/api/scans \
  -H "Content-Type: application/json" \
  -d '{"domains": "all", "skip_llm": true, "max_concurrent": 10}'
```

### Dry run (resolve domains only)

```bash
curl -X POST http://localhost:8000/api/scans \
  -H "Content-Type: application/json" \
  -d '{"domains": "nordic", "dry_run": true}'
```

---

## Project Structure

```
OCP-CE-HR-Policy-Searcher/
├── pyproject.toml              # Dependencies & build config
├── setup.sh                    # One-command setup (Linux/macOS)
├── setup.ps1                   # One-command setup (Windows PowerShell)
├── config/
│   ├── domains/                # 80+ YAML files defining 360+ domains
│   │   ├── eu.yaml
│   │   ├── us_states/          # 51 US state domain files
│   │   └── ...
│   ├── groups.yaml             # Domain group definitions
│   ├── keywords.yaml           # 7 categories x 20 languages
│   ├── settings.yaml           # Runtime settings
│   ├── url_filters.yaml        # URL skip/block rules
│   ├── content_extraction.yaml # HTML boilerplate removal rules
│   └── example.env             # Environment variables template
├── src/
│   ├── agent/                  # AI agent (primary entry point)
│   │   ├── __main__.py         # CLI: python -m src.agent
│   │   ├── orchestrator.py     # Agent loop (Anthropic API tool use + rate limit retry)
│   │   ├── tools.py            # 15 tool definitions + dispatch
│   │   └── domain_generator.py # Auto-generate domain YAML from URLs
│   ├── core/                   # Shared business logic
│   │   ├── models.py           # All Pydantic data models
│   │   ├── config.py           # YAML config loading & domain resolution
│   │   ├── log_setup.py        # Structured logging (structlog + JSON + audit)
│   │   ├── crawler.py          # Async BFS web crawler
│   │   ├── extractor.py        # HTML content extraction
│   │   ├── keywords.py         # Multi-language keyword matcher
│   │   ├── llm.py              # Two-stage Claude client
│   │   ├── cache.py            # URL cache with TTL
│   │   ├── scanner.py          # Single-domain pipeline
│   │   └── verifier.py         # Deterministic validation
│   ├── orchestration/          # Parallel scan management
│   │   ├── scan_manager.py     # Job dispatch, progress tracking, per-domain persistence
│   │   ├── auditor.py          # Post-scan LLM advisory
│   │   └── events.py           # WebSocket broadcasting
│   ├── api/                    # FastAPI REST API
│   │   ├── app.py              # FastAPI app & middleware
│   │   ├── deps.py             # Dependency injection
│   │   └── routes/
│   │       ├── domains.py      # Domain endpoints
│   │       ├── scans.py        # Scan + WebSocket endpoints
│   │       ├── policies.py     # Policy endpoints
│   │       ├── analysis.py     # Single URL analysis
│   │       ├── agent.py        # Agent REST + WebSocket endpoints
│   │       └── logs.py         # Log viewer endpoints (for React frontend)
│   ├── output/                  # Export integrations
│   │   └── sheets.py            # Google Sheets export
│   ├── mcp/
│   │   └── server.py           # MCP server (11 tools, advanced)
│   └── storage/
│       └── store.py            # JSON persistence
├── tests/                      # 1085+ tests (+ 3 skipped)
│   ├── unit/
│   │   ├── test_agent.py       # Agent tool + dispatch + rate limit tests
│   │   ├── test_api.py         # FastAPI endpoint tests
│   │   ├── test_cache.py       # URL cache tests
│   │   ├── test_crawler.py     # Web crawler tests
│   │   ├── test_domain_generator.py  # Domain ID/region tests
│   │   ├── test_extractor.py   # HTML extraction tests
│   │   ├── test_keywords.py    # Keyword matcher tests (53 tests, 20 languages)
│   │   ├── test_llm.py         # Claude client tests
│   │   ├── test_logging.py     # Logging, audit, redaction, API endpoints, CLI viewer
│   │   ├── test_scanner.py     # Domain scanner tests
│   │   ├── test_sheets.py      # Sheets export + Policy row tests
│   │   ├── test_store.py       # JSON persistence tests
│   │   └── test_verifier.py    # Verification flag tests
│   └── integration/
│       ├── test_agent_loop.py  # Agent loop tests (mocked API)
│       ├── test_discovery.py   # Discovery workflow + auto-group tests
│       └── test_full_pipeline.py  # End-to-end pipeline + onboarding tests
└── data/                       # Runtime data (gitignored)
    ├── logs/                   # Structured logs (auto-created)
    │   ├── agent.log           # JSON-lines log (rotated)
    │   └── audit.jsonl         # Crash-safe audit trail
    ├── url_cache.json
    └── policies.json
```

---

## Development

### Setup

```powershell
git clone https://github.com/opencomputeproject/OCP-CE-HR-Policy-Searcher.git
cd OCP-CE-HR-Policy-Searcher
.\setup.ps1 -Dev        # Linux/macOS: ./setup.sh --dev
```

### Linting

```bash
ruff check src/
ruff format src/
```

### Testing

```bash
pytest                    # Run all 1085+ backend tests (plus 3 skipped)
pytest tests/unit/        # Unit tests only
pytest tests/integration/ # Integration tests only
pytest --cov=src          # With coverage report

cd frontend && CI=true npx react-scripts test --watchAll=false  # 153 frontend tests across 18 suites
```

### Adding a New Domain

**Via the agent (easiest):** Just ask the agent to add a URL — it auto-detects the domain ID, region, and language.

```
You: Add this site to the database: https://www.bmwk.de/energy/policies
```

**Manually:** Create a YAML file in `config/domains/` (or add to an existing one):

```yaml
domains:
  - id: "my_domain"
    name: "My Government Agency"
    enabled: true
    base_url: "https://www.example.gov"
    region: ["us"]
    category: "energy_ministry"
    tags: ["mandates"]
    start_paths: ["/energy/policies/"]
    max_depth: 2
```

Optionally add it to a group in `config/groups.yaml`.

### Adding Keywords

Edit `config/keywords.yaml` to add terms to any category/language:

```yaml
keywords:
  subject:
    weight: 3.0
    terms:
      en:
        - "heat recovery mandate"  # Add new English terms
      de:
        - "Wärmerückgewinnung"      # Add new German terms
```

---

## Cost Estimation

Approximate costs per full scan (all 361 domains):

| Stage | Model | Est. Calls | Est. Cost |
|-------|-------|-----------|-----------|
| Keyword filtering | — | ~27,500 pages | $0.00 |
| Haiku screening | claude-haiku | ~2,750 | ~$0.50 |
| Sonnet analysis | claude-sonnet | ~1,375 | ~$3.00 |
| Post-scan auditor | claude-sonnet | 1 | ~$0.05 |
| **Total** | | | **~$3.55** |

Use the cost estimate endpoint before scanning: `POST /api/cost-estimate?domains=all`

---

## MCP Server (Advanced)

For users with [Claude Desktop](https://claude.ai/download) or [Claude Code](https://docs.anthropic.com/en/docs/claude-code), the system also provides an MCP server with the same 11 policy tools. This is optional — most users should use the [AI Agent](#ai-agent) instead.

```bash
python -m src.mcp.server
```

Add to your Claude Desktop config (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "OCP-CE-HR-Policy-Searcher": {
      "command": "python",
      "args": ["-m", "src.mcp.server"],
      "cwd": "/path/to/OCP-CE-HR-Policy-Searcher",
      "env": {
        "ANTHROPIC_API_KEY": "sk-ant-..."
      }
    }
  }
}
```

---

## Troubleshooting

### "ANTHROPIC_API_KEY looks like the placeholder value"

The `.env` file still has the example key from setup. Open `.env` and replace the `ANTHROPIC_API_KEY` value with your real key from [console.anthropic.com](https://console.anthropic.com/). Real keys are 100+ characters starting with `sk-ant-`.

### "Authentication failed — your API key is invalid"

Your key is being sent but rejected. Common causes:
- The key was copied with extra spaces or missing characters
- The key has been revoked — generate a new one at [console.anthropic.com](https://console.anthropic.com/)
- A stale empty `ANTHROPIC_API_KEY` in your system environment is overriding `.env` — close and reopen your terminal, or run `Remove-Item Env:ANTHROPIC_API_KEY` (PowerShell) / `unset ANTHROPIC_API_KEY` (bash)

### "ANTHROPIC_API_KEY is not set"

The `.env` file is missing or doesn't have the key. The setup script should have created it — if not, copy it manually:

```powershell
copy config\example.env .env      # Linux/macOS: cp config/example.env .env
```

Then edit `.env` and add your key.

### "Google Sheets connection failed: Incorrect padding"

The `GOOGLE_CREDENTIALS` value in `.env` is not valid base64. Common causes:
- The base64 string was truncated when pasting (it's ~3000+ characters)
- Extra whitespace or line breaks were introduced — the value must be a single unbroken line
- The `.env` file wasn't found because the process started from a different directory

**Easiest fix:** Use a file path instead of base64:
```
GOOGLE_CREDENTIALS_FILE=path/to/service-account.json
```

Or re-encode your credentials:
```bash
# Linux/macOS
base64 -i service-account.json | tr -d '\n'

# PowerShell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("service-account.json"))
```

Paste the result as a single line in `.env` after `GOOGLE_CREDENTIALS=`.

### "GOOGLE_CREDENTIALS looks invalid (length=0)"

The `.env` file exists but Google credentials are empty or missing. Set one of:
- `GOOGLE_CREDENTIALS_FILE=path/to/service-account.json` (easiest)
- `GOOGLE_CREDENTIALS=<base64 or raw JSON>`

See [Google Sheets Setup](#google-sheets-setup).

### JavaScript pages return empty content (keyword score 0.0)

The site is a JavaScript SPA that requires browser rendering. Set `requires_playwright: true` in the domain YAML config. The crawler will use headless Chromium instead of httpx.

If Playwright isn't installed:

```bash
pip install "playwright>=1.40"
playwright install chromium
```

### Script execution error on Windows

If `.\setup.ps1` fails with a security error, run this once:

```powershell
Set-ExecutionPolicy RemoteSigned -Scope CurrentUser
```

---

## Contributing

Contributions are welcome! See **[CONTRIBUTING.md](CONTRIBUTING.md)** for the full guide, including:

- Step-by-step instructions for adding a new country or region
- Code style expectations (ruff, type hints, Pydantic models)
- How to run the 1085+-test backend suite, the 153-test frontend suite, and lint checks
- Domain YAML format template
- PR checklist

**Quick start:**

```bash
git clone https://github.com/opencomputeproject/OCP-CE-HR-Policy-Searcher.git
cd OCP-CE-HR-Policy-Searcher
.\setup.ps1 -Dev        # Linux/macOS: ./setup.sh --dev
pytest                   # All tests must pass
ruff check src/ tests/   # No lint errors
```

---

## License

[MIT](LICENSE)

### Map data

The world map's boundaries come from three sources: [Natural Earth](https://www.naturalearthdata.com/) (public domain) for the base world atlas, the [US Census Bureau](https://www.census.gov/geographies/mapping-files/time-series/geo/carto-boundary-file.html) cartographic boundary files (public domain) for US state geometry, and [geoBoundaries](https://www.geoboundaries.org/) for Germany's and Belgium's admin-1 geometry. geoBoundaries data is CC BY 4.0 — attribution is required:

> Runfola, D. et al. (2020) geoBoundaries: A global database of political administrative boundaries. *PLoS ONE* 15(4): e0231866. [www.geoboundaries.org](https://www.geoboundaries.org/)
