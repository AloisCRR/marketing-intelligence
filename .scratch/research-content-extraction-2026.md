# Research: clean-Markdown content extraction (2026-09-05)

Scope: V1 is RSS-only, deterministic-only, plain HTTP+RSS, no Firecrawl unless a
source needs it (`CONTEXT.md` V1 cuts). Goal: store post content clean as
Markdown in PG `documents.content` (TEXT) for MCP consult. Pasted reddit
opinions treated as leads only; findings below are verified against primary
sources (official docs, GitHub repos, pricing pages).

## 1. Firecrawl (incl. self-hosted)

- **What:** hosted API + open-source stack that scrapes/crawls and returns clean
  Markdown or structured JSON. Output modes include `formats: ['markdown']`,
  optional JSON-with-schema, `onlyMainContent`, proxy, `maxAge` cache. Python/JS
  SDK examples in official docs. Source: `firecrawl/firecrawl-docs`,
  `features/crawl.mdx` (scrape-options with `formats`, `proxy: 'auto'`).
- **Self-host requirements:** Git, Docker Engine/Desktop, Docker Compose v2,
  curl; port 3002 free; "sufficient resources" for the stack. `docker compose up
  --build -d`. Source: `firecrawl-docs/contributing/self-host.mdx`.
- **Self-host weight:** community edition spins up **six containers** (API,
  Playwright service, Redis, RabbitMQ, Postgres, …). Corroborated by Forage
  README's comparison ("six containers") — secondary for the count, but the
  official self-host guide confirms multi-service compose. API keys optional on
  self-host (only needed for `api.firecrawl.dev` cloud).
- **License:** AGPL-3.0 for the core repo (SDKs/MIT in subdirs). Source:
  `github.com/firecrawl/firecrawl` repo header + `LICENSE` (AGPL-3.0).
  Implication: network-copyleft — fine for internal/self-hosted use, but
  modifications served over network must have source available.
- **Cost/limits (cloud):** free 1,000 credits/mo, no card; Hobby $19/mo
  (5k/mo), Standard $99/mo (100k/mo), Growth $399/mo (500k/mo), Scale
  $749/mo (1M/mo); ~1 credit/page for scrape/crawl/map, 2 per 10 search
  results; pay-as-you-go auto-reload in $5 batches. Source:
  `firecrawl.dev/pricing` + `firecrawl-docs/billing.mdx`.
- **JS rendering / bot handling:** cloud has **Fire-engine** (IP-block / robot
  detection handling). **Self-hosted does NOT get Fire-engine** — "more complex
  scenarios might require additional configuration or might not be supported."
  Self-host `.env` exposes `PROXY_SERVER` (+ optional user/pass) for outbound
  proxy. Source: `firecrawl-docs/contributing/self-host.mdx` (§Considerations,
  §PROXY_SERVER).
- **Fit for 4 RSS sources:** overkill as primary; strongest as a *paid,
  per-source fallback* when RSS summary is thin/paywalled/botted and local
  extraction fails.

## 2. Crawl4AI (open-source, LLM-oriented)

- **What:** open-source LLM-friendly crawler; headline feature is clean Markdown
  (`raw_markdown` + heuristic-filtered `fit_markdown`), citations/references,
  BM25 filtering, custom `DefaultMarkdownGenerator(content_source=
  raw_html|cleaned_html|fit_html)`. Source: `unclecode/crawl4ai` README +
  `docs/md_v2/*`, `deploy/docker/c4ai-doc-context.md` (via Context7).
- **How it cleans:** scraping-strategy pipeline → `cleaned_html` (drops
  nav/footer/aside/overlays, `excluded_tags`, `remove_overlay_elements`,
  `word_count_threshold`) → Turndown-style Markdown generator; `fit` variant
  further prunes boilerplate. Handles shadow DOM (`flatten_shadow_dom`),
  iframes (`process_iframes`). Source: `crawl4ai` docs `core/content-selection`
  + quickstart notebook excerpts (via Context7).
- **Local-only run:** yes — `pip install crawl4ai` + `playwright install
  [--with-deps chromium]`; or Docker (`docker pull unclecode/crawl4ai`,
  `docker run -p 11235:11235 --shm-size=1g`, FastAPI server + playground +
  dashboard). No API keys for crawl path. Source: `github.com/unclecode/crawl4ai`
  README + `docs.crawl4ai.com/core/self-hosting`.
- **Deps:** Python 3, Playwright + Chromium (headless by default), optional
  torch/transformer extras, GPU flag; Docker needs `--shm-size=1g`, ~4GB RAM.
  Source: same as above.
- **License:** Apache-2.0 **with an added attribution clause** (NOTICE/README/
  About/Credits + help output must credit UncleCode/Crawl4AI). Source:
  `unclecode/crawl4ai/LICENSE`.
- **Throughput fit:** async browser pool + caching; trivially covers 4 RSS
  feeds even at full-body enrichment (tens–low-hundreds of pages/day). Heaviest
  cost is Chromium RAM, not throughput. No per-page fee.
- **Caveat:** browser fleet = ops surface (playwright updates, stealth arms
  race). For RSS-first use, keep it *behind* static extraction, invoked
  per-item on demand.

## 3. Forage (`aldemaroc/forage` — verify which "Forage"!)

- **Disambiguation:** there are at least two "Forage"s. The relevant one for
  this lane is **`aldemaroc/forage`** — "lightweight self-hosted web search &
  extract, drop-in Firecrawl alternative" (FastAPI + trafilatura, single
  container). The other (`derekurban/forage`, Windows Go CLI fronting Jina/
  Firecrawl/Browserbase APIs) is an API-key aggregator, **not** self-hosted
  extraction — reject for V1 (adds vendors, no local benefit).
- **What (aldemaroc/forage):** single Docker container (`:3672`): FastAPI +
  in-process Chromium pool + `httpx` static fetch + **trafilatura** (default)
  or **Readability.js+markdownify** per-domain/per-request; `POST /extract`
  `{urls, formats:[markdown|html], force_render, wait_for, only_main_content,
  timeout}` → `{url, title, content (markdown), raw_content, method:
  static|browser|browser+solver|pdf|docx|…}`; blocked pages return explicit
  error, not challenge junk. Source: `github.com/aldemaroc/forage` README +
  live fetch 2026-09-05.
- **Self-host vs API:** self-host only (no SaaS). Needs Docker Engine 24+ /
  Compose v2, ~1GB image (Chromium included), plus SearXNG container **only if
  you want `/search`** — extract-only use skips SearXNG. Config via
  `config.yaml` + env secrets; optional Bearer auth; in-memory TTL cache
  (extract cache off by default). Source: same README (§Requirements,
  §Configuration).
- **Decision logic (matches our RSS-first want):** static first; escalate to
  browser on 401/403/429, SPA markers (`#root`, `__NEXT_DATA__`), or thin
  trafilatura output (`< min_content_chars`); challenge detector + optional
  Scrapling-solver retry. Source: README §"How extraction decides".
- **Benchmark (author-reported, treat as lead):** 50 sites, playwright 48/50
  @3.3s, patchright 47/50 @3.2s, scrapling 48/50 @3.6s; scrapling only one
  passing all Cloudflare cases. Source: README §Benchmark → `docs/BENCHMARK.md`
  (not independently re-run here).
- **License / ops cost:** **GPL-3.0** (repo badge + §License). Stricter
  copyleft than Apache/MIT — self-hosted internal use is fine; distributing a
  modified image triggers source duties. Ops: one container vs Firecrawl's six.
  Maturity risk: young single-maintainer project (v0.8.1 at fetch), Hermes-agent
  origin — pin image digest, smoke-test per source.

## 4. Jina AI Reader (`https://r.jina.ai/URL`)

- **Markdown output:** prepend `https://r.jina.ai/` to any URL → LLM-friendly
  Markdown; headers control engine (`x-engine: browser|curl|auto`), selectors
  (`x-target-selector`, `x-wait-for-selector`), timeouts, token budget
  (`x-max-tokens` truncate / `x-token-budget` reject), GFM tweaks (`x-md-*`),
  frontmatter/JSON modes, `x-no-cache`/`x-cache-tolerance`, `DNT` (no
  cache/log). Also handles PDFs/Office/images, SPAs via Puppeteer/headless
  Chrome. Open-source branch (`github.com/jina-ai/reader`, Apache-2.0) is
  stateless/bucket-cache; SaaS storage layer stripped; self-host via
  `ghcr.io/jina-ai/reader:oss` (ports 8080 h2c / 8081 HTTP-1.1). Source: Jina
  Reader README + `jina.ai/reader` playground docs (fetched 2026-09-05).
- **Rate limits:** tracked RPM + TPM per key (or IP if anon); first threshold
  wins. Reader `r.jina.ai`: anon **20 RPM**; free key **500 RPM**; paid 500 RPM;
  premium 5000 RPM; avg latency ~7.9s. General tiers: free 100 RPM/100K TPM,
  paid 500/2M, premium 5000/50M, plus IP cap 10k/60s. `s.jina.ai` search is
  key-only. Source: `jina.ai/reader` (extracted pricing/rate-limit section).
- **API key / pricing:** no key for basic use; key raises limits. Token-based
  (output tokens for Reader; `s.jina.ai` fixed ≥10k/req); failed requests not
  billed; every new key ships **10M free tokens**, shared across Reader/
  Embeddings/Reranker/etc.; Stripe top-up. New model from 2025-05-06. Source:
  same page.
- **Reliability + data-sharing:** SaaS means curated-source URLs + cookies flow
  through Jina infra; cache default 3600s/5-min reuse; mitigations: `DNT`,
  `x-no-cache`, `x-set-cookie` (never cached). Jina states API inputs/outputs
  are **not used to train models**; `DNT` prevents caching/logging per docs.
  Operational risk: third-party dependency, rate-limit/backoff handling (429
  with `retryAfter`), latency variance, and ToS exposure if a source forbids
  proxying. Prefer `DNT` + no-cookie for sensitive items; never send creds.
  Source: `jina.ai/reader` pricing/privacy extract + repo `cookbooks.md`.

## 5. Fit for THIS repo + recommendation

Constraints: RSS-first, deterministic (no LLM at ingest), 4 sources, PG TEXT
`documents.content`, per-item failure isolation (never block Ingestion Run),
stdlib-only service adapter, Prefect `@flow/@task` ingest.

| Option | Self-host | Deterministic MD | Bot/JS escape | Cost | License | Verdict |
|---|---|---|---|---|---|---|
| trafilatura/Readability (local lib, no browser) | yes (pip) | yes | weak | infra only | MIT/Apache | **Stage 0 — default cleaner** (not in the 4, but implied: RSS HTML → MD locally first) |
| Crawl4AI | yes | yes (`raw`/`fit`) | strong (Playwright, stealth) | infra only | Apache-2.0+attribution | **Primary enrichment fallback** |
| Forage (aldemaroc) | yes (1 container) | yes (trafilatura/readability) | strong (3 engines + solver) | infra only | GPL-3.0 | **Alt-primary if team wants a service, not a lib** |
| Firecrawl cloud | no (SaaS) | yes | strongest (Fire-engine) | per-page $$ | AGPL-3.0 (self-host) / ToS (cloud) | **Paid last-resort fallback** |
| Jina Reader | SaaS (+oss image) | yes | strong (browser+curl, proxy) | free 10M tokens → metered | Apache-2.0 (code) / ToS (SaaS) | **Zero-ops spike/fallback only** |

- **Recommended: Crawl4AI (primary) + Jina Reader (fallback).**
  - Primary Crawl4AI because: local-only (no data-sharing), Apache-2.0,
    deterministic `cleaned_html → markdown` with `fit` variant, Playwright
    already in Python ecosystem, throughput trivially fits 4 feeds, no per-page
    cost, failure-isolatable per-item inside a Prefect task.
  - Fallback Jina because: zero-ops (`GET https://r.jina.ai/<url>` + key),
    generous free tokens, handles the odd JS/bot page Crawl4AI misses; gate
    behind per-source opt-in + `DNT`/no-cookie.
  - If the team prefers an HTTP service over embedding a crawler lib, swap
    primary to **Forage (aldemaroc)** — same static-first→browser pattern as a
    single container; accept GPL-3.0 + younger-project risk.
  - Reserve **Firecrawl cloud** for persistent paywall/bot sources only
    (per-source flag), given cost + self-host weight + missing Fire-engine
    when self-hosted.
- **Per-source opt-in trigger (deterministic, no LLM):** attempt enrichment
  only when `len(rss_summary_text) < N chars` (e.g. <500) **or** HTTP fetch of
  article URL returns paywall/bot signals (401/403/429, challenge selectors,
  `< min_content_chars` after static clean). Record `method` + `extracted_at`
  + source URL alongside `documents.content`; on any enrichment exception, keep
  RSS summary and continue the Ingestion Run (partial failure explicit).
- **Storage:** no migration — Markdown fits existing PG TEXT; optionally add
  `content_format='markdown'`/provenance columns later (out of scope for this
  lane; no code changed here).

## Sources (primary only)

- `https://github.com/firecrawl/firecrawl` (AGPL-3.0, stars/issues header)
- `https://github.com/firecrawl/firecrawl/blob/main/LICENSE`
- `https://docs.firecrawl.dev/contributing/self-host` (`self-host.mdx` — prereqs, compose, Fire-engine limits, PROXY_SERVER)
- `https://www.firecrawl.dev/pricing` + `firecrawl-docs/billing.mdx` (credits, concurrency)
- `https://github.com/unclecode/crawl4ai` (Apache-2.0+attribution, README, quickstart, content-selection, self-hosting guide)
- `https://github.com/unclecode/crawl4ai/blob/main/LICENSE`
- `https://docs.crawl4ai.com/core/self-hosting/` + `deploy/docker/README.md`
- `https://github.com/aldemaroc/forage` (README fetched 2026-09-05: features, /extract schema, decision logic, benchmark, GPL-3.0)
- `https://github.com/derekurban/forage` (counter-evidence: different tool, API aggregator — rejected)
- `https://github.com/jina-ai/reader` (README: usage, headers, self-host image, Apache-2.0)
- `https://jina.ai/reader` (pricing/rate-limit/privacy extract 2026-09-05: 10M free tokens, RPM/TPM table, DNT/cache)
- `https://r.jina.ai/docs` + `src/dto/crawler-options.ts` (header surface; cited via README)
- Local: `CONTEXT.md` (V1 cuts), `src/brain/*.py` (ingest/normalize/service layout — read-only grounding)
