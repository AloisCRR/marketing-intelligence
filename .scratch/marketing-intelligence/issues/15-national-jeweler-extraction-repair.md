# 15: National Jeweler extraction repair

**What to build:** A digest consumer reading a National Jeweler Document gets a clean body — lede first, sentence text intact, no related-article links, no "The Latest" sidebar dump — so weekly summaries stop quoting navigation chrome as content.

**Blocked by:** None (can start immediately).

**Status:** done

- [x] Per-domain post-processing strips related-content blocks and nav sidebars and converts inline editorial anchors to plain words for the affected Source family
- [x] Every returned Document still carries full provenance and its lede is present
- [x] Regression fixtures pin one polluted body before and the cleaned body after, including the Gen Z self-purchase item and the store-closure item from the review
- [x] Existing Extraction Flag round-trip (set → read back → clear) still passes; previously flagged items re-ingest clean
