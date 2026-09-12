# 23: Ingest-time extraction auto-checks

**What to build:** The next source-wide extraction rot is caught at ingest instead of by the digest consumer — suspect Documents arrive pre-flagged with a reason, and the monthly flag rate trends toward zero.

**Blocked by:** 15 — National Jeweler extraction repair (generalizes its heuristics).

**Status:** ready-for-agent

- [ ] Ingest applies deterministic quality heuristics (link-density threshold, known boilerplate markers, missing-lede detection, short-body floor) and files an Extraction Flag on hits at store time
- [ ] Auto-flagged Documents respect the same 0.3 importance cap as agent-flagged ones from ticket 19
- [ ] A regression corpus pins each heuristic with a before/after fixture so tuning never silently loosens detection
- [ ] Agent `flag_extraction` remains as refinement on top, not the first line of defense
