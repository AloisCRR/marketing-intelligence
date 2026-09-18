# 01: Per-reader mark and clear store

**What to build:** An agent marking a Document as itself no longer overwrites other readers. Each reader's mark is stored separately with its timestamp; re-marking by the same reader refreshes only its own timestamp, and clearing can remove one reader or all readers, with the existing anyone-read summary continuing to reflect the latest mark.

**Blocked by:** None (can start immediately).

**Status:** ready-for-human — implemented, needs human verify + accept

- [ ] Marking a Document with a reader tag stores one entry per (Document, reader); a second reader's mark adds a row instead of overwriting the first, and re-marking by the same reader updates only its timestamp
- [ ] Blank or missing reader tag on mark is rejected as a caller error (422 on both surfaces); previously stored anonymous marks still read as read with an empty readers set until re-marked
- [ ] Clear with a reader tag removes only that reader's entry; clear without a reader tag removes all readers; clearing the last reader returns the Document to unread
- [ ] The legacy anyone-read summary (`read` bool plus latest `read_at`/`read_by`) keeps working as the latest mark across all readers, and `exclude_read` still means read-by-anyone
- [ ] Both caller surfaces expose the write identically through the Service Adapter with 422 on invalid input
