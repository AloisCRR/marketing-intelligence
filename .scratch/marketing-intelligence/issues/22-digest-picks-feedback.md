# 22: Digest picks and prompt rebuild

**What to build:** Each weekly digest leaves a trace — which Documents were selected for it — so a later review can ask "what did we miss" and future ranking has a labeled dataset; the digest prompt itself is rebuilt around the score → tag → select → summarize funnel.

**Blocked by:** 21 — Annotation-aware retrieval (picks reference the filtered week).

**Status:** done

- [x] Recording digest selection per Document round-trips (set → read back → clear) with digest date, reporter, and timestamp
- [x] The rebuilt digest prompt drives the full funnel (recall week → score → tag → source-balanced select → summarize) against the new filters, with the importance rubric versioned in the prompt, not server code
- [x] Human edits to a published digest are capturable as re-scoring signal against the recorded picks
- [x] Both caller surfaces expose picks identically through the Service Adapter
