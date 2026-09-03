# What OCP needs to have and run when they take on PolicyPulse

One page. The full operator manual lives in the repo at `docs/OPERATIONS.md`,
including the step-numbered adoption checklist.

## The shape of it

One Docker container on one small server. Inside it: the app, its single-file
SQLite database (the record), and the scheduled jobs. Outside it: the GitHub
repo (already OCP's), one Google Sheet, and the Anthropic API. That is the
entire estate. No database server, no queue, no Kubernetes, no vendor lock -
SQLite is public domain, the code is MIT.

## To HAVE (one-time setup)

| Item | Detail | Cost |
|---|---|---|
| A small VM | 1-2 vCPU, 2 GB RAM, ~10 GB disk, any provider | ~$5-10/month |
| A subdomain + DNS record | e.g. policypulse.opencompute.org | ~$0 |
| TLS certificate | automatic (Caddy/Let's Encrypt), nothing to manage | $0 |
| Anthropic API account | THE one vendor relationship; whoever holds the key holds the spend | usage-based, capped |
| Google service account | for the spreadsheet export, pointed at the real Heat Reuse Policies Database | $0 |
| Free source API keys | re-registered under an OCP email (LegiScan, GovInfo, regulations.gov, DIP, NZ PCO, HuggingFace, PISTE) | $0 |
| A fresh ADMIN_TOKEN | set on the server; gates all paid actions | $0 |

Adoption day itself: `docker compose up -d`, copy one database file over,
their secrets in, all interim credentials rotated out, DNS moved. Documented
step-by-step in the runbook. Until that day it runs at no cost to OCP, and
everything currently writes only to a scratch copy of the spreadsheet - the
real community sheet is untouched.

## To RUN (ongoing)

| Role | Time | Who |
|---|---|---|
| Operator | ~1-2 hours/month: check the weekly/monthly jobs ran, glance at costs, apply updates | any IT-comfortable volunteer |
| Curators | minutes/week: promote or reject new finds in the review inbox (~30 seconds each) | the CE Heat Reuse experts - the one role only they can fill |
| Maintainer | occasional: merge community PRs, dependency updates, fix a connector when a government changes its API | any contributor |

The honest core of the commitment: servers are nearly free and nearly
effortless. **Curation and source maintenance are the real ongoing work** -
a community habit, not a job.

## What it does by itself

- Every Monday: news sweep across 40+ countries -> new tips for curators.
- Every 1st: full scan of 400+ sources (23 of them structured legislation
  APIs queried directly) -> findings into the database, exported to the
  sheet, deduplicated.
- Nightly: backup (= one file copied).
- All AI spend hard-capped by built-in controls: scans are cost-bounded,
  the public Ask box has a daily ceiling and per-visitor rate limit, chases
  require a human click. Realistic total: **$15-40/month all-in**.

## What OCP does NOT take on

No DBA, no schema migrations run by hand (the app migrates itself), no
secrets in code, no scraping-liability surprises (official government
endpoints only; restrictively-licensed databases are deliberately excluded
from republication), and no dependence on any one person - the runbook is
written so a successor can take the whole thing over from scratch.
