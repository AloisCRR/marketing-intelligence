# 02 — Remaining 3 sources on the same contract

**What to build:** All four V1 sources flowing through the unchanged adapter contract, each independently repairable, with one source's failure leaving the others intact.

**Blocked by:** 01 — Foundation + first source end-to-end.

**Status:** ready-for-agent

- [ ] MarTech, Professional Jeweller, and InfoMoney ingest via the 01 adapter contract with no contract changes
- [ ] Each source can be rerun independently of the others
- [ ] A failing source is recorded explicitly and does not block successful sources
