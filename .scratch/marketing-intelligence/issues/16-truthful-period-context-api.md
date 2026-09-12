# 16: Truthful Period Context API

**What to build:** A caller pulling a week gets a Period Context bundle that says only what is true — a recency-ordered article list under an honest name, with zero empty promise fields — so no consumer can mistake newest-first for ranked-by-importance.

**Blocked by:** None (can start immediately).

**Status:** ready-for-agent

- [ ] The recency-ordered list is exposed under a truthful name and every item carries its rank position or equivalent ordering signal, never an implied score
- [ ] Empty analytics placeholders (top stories, emerging topics, movements, entities, convergence) are gone from the bundle until a later ticket computes them for real
- [ ] Both caller surfaces stay in parity by construction through the Service Adapter, with caller-side validation errors still surfacing as 422 over HTTP
- [ ] The digest and investigate prompt scaffolds describe only what the bundle actually contains
