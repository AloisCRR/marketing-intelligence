# 18: Source inventory listing

**What to build:** An operator or agent can list every curated Source with its stored Document count, last successful Ingestion Run, and cadence — so quota design and pipeline diagnosis stop flying blind.

**Blocked by:** None (can start immediately).

**Status:** done

- [x] One read-only listing returns name, article count, last-ingest time, and cadence per Source across all 20 V1 Sources
- [x] Available identically on both caller surfaces through the Service Adapter
- [x] Empty or never-ingested Sources report explicitly instead of disappearing from the list
