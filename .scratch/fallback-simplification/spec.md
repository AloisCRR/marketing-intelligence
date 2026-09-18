# Fallback Simplification — Spec

Right service for the right job. Feed URLs are fetched as raw bytes (impersonation only; candidate URLs cover dead feeds) — Markdown readers never touch them. Article content walks one shared three-leg chain (impersonation → Jina reader → Firecrawl scrape); provider Markdown is accepted as-is after a thin-check, never re-cleaned. HTML extraction is trafilatura-only. Total extraction failure is terminally marked `unrecoverable` through the existing Extraction Flag path. The robots Disallow filter is removed; crawl-delay, per-host pacing, and newest-first budgeting stay.

Tickets: `issues/01`–`06`. Frontier: 01, 02, 03, 06.
