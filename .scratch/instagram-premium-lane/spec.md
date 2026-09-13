# Instagram premium lane (sabrikolod pilot) — Spec

Pointer-based caption-first ingestion of one Instagram account via Apify, per ADR-0013. Fetch full post JSON, store raw in a side table, expose caption only. No full-history pull: 10-post bootstrap, then `MAX(published_at)` pointer with 15-cap and 60s overlap.

Tickets: `issues/01`–`04`. Frontier: 01.
