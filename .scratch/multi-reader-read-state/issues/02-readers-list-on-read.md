# 02: Readers list on every Document read

**What to build:** Every Document read shows who consumed it: each single read, search result, and period bundle item carries the sorted per-reader list alongside the existing anyone-read summary, so an agent can tell a Document it consumed itself from one another agent consumed without extra calls.

**Blocked by:** 01 — Per-reader mark and clear store (needs the stored per-reader entries to annotate from).

**Status:** ready-for-human — implemented, needs human verify + accept

- [ ] Single read, search results, and period bundle items each carry `readers` (sorted `{reader, read_at}` pairs; empty when unread) alongside the unchanged anyone-read summary
- [ ] A Document marked by two readers shows both entries; a Document cleared of one reader shows only the remaining entry; an unread Document shows an empty set
- [ ] Both caller surfaces return the list identically through the Service Adapter with no new endpoints or filters
