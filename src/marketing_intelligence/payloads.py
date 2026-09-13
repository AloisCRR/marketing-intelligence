"""Raw retrieval payload side table (ADR-0013) — premium-lane evidence capture.

Domain contract (stable): ``write_document_payloads(pairs, conn=None)`` takes
``(NormalizedDocument, payload-dict)`` pairs **after** the documents exist
(call it once ``ingest.upsert_documents`` has run), resolves each document by
`url` then `canonical_url` — the flag-lane pattern — and upserts one JSONB row
per document into ``document_payloads``. Returns ``(inserted, skipped)``:
``inserted`` counts applied payload writes (a re-write of the same document
overwrites in place, never duplicates), ``skipped`` counts pairs whose
document is unknown plus per-pair DB failures. An unknown URL never raises —
one unattributable payload must not fail an ingest batch — mirroring
:func:`marketing_intelligence.ingest.upsert_documents` accounting.

The payload dict is serialized with ``json.dumps(..., ensure_ascii=False)``
(Spanish captions stay readable in the stored JSON) and cast with ``::jsonb``.
Payloads hold raw extraction output only: the Apify token is read from the
environment by the lane at call time and never reaches this module.

Connection handling mirrors ``upsert_documents``: when ``conn`` is None a
connection is opened via ``marketing_intelligence.db.get_connection`` and
closed here (commit on the way out); an injected connection is committed but
never closed by this module.
"""

from __future__ import annotations

import json
from typing import Any

try:  # foundation seam (preferred)
    from marketing_intelligence.db import get_connection  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - defensive fallback when absent

    def get_connection() -> Any:  # type: ignore[misc]
        raise RuntimeError(
            "No database connection available: marketing_intelligence.db.get_connection "
            "is missing and no fallback is configured."
        )


from marketing_intelligence.normalize import NormalizedDocument

#: Resolve the stored document id from the pair's URL, exact match first,
#: canonical second (same URL/canonical keying as the flag/read lanes).
_DOCUMENT_ID_SQL = """\
SELECT id FROM documents WHERE url = %s OR canonical_url = %s\
"""

#: One payload row per document; a re-write overwrites the stored payload and
#: refreshes `retrieved_at` (012 documents it as the write time, so the stamp
#: always describes the payload currently stored).
_PAYLOAD_UPSERT_SQL = """\
INSERT INTO document_payloads (document_id, payload)
VALUES (%s, %s::jsonb)
ON CONFLICT (document_id) DO UPDATE
    SET payload = EXCLUDED.payload, retrieved_at = now()\
"""


def write_document_payloads(
    pairs: list[tuple[NormalizedDocument, dict]], conn: Any | None = None
) -> tuple[int, int]:
    """Persist raw payloads beside their documents. Returns (inserted, skipped).

    Call after ``upsert_documents``: resolution is keyed by the document's
    ``url``/``canonical_url``, so the row must already exist. Unknown
    documents (and per-pair write failures) count as skipped, never raise.
    When ``conn`` is None a connection is opened via
    ``marketing_intelligence.db.get_connection`` and closed here; an injected
    connection is committed but left open (fake connections welcome in tests).
    """
    if not pairs:
        return (0, 0)
    owns_connection = False
    if conn is None:
        conn = get_connection()
        owns_connection = True
    assert conn is not None
    try:
        inserted = 0
        skipped = 0
        for doc, payload in pairs:
            try:
                cursor = conn.execute(_DOCUMENT_ID_SQL, (doc.url, doc.canonical_url))
                row = cursor.fetchone() if cursor is not None else None
                if row is None or row[0] is None:
                    skipped += 1
                    continue
                conn.execute(
                    _PAYLOAD_UPSERT_SQL,
                    (row[0], json.dumps(payload, ensure_ascii=False)),
                )
                inserted += 1
            except Exception:
                skipped += 1
        conn.commit()
        return (inserted, skipped)
    finally:
        if owns_connection:
            try:
                conn.close()
            except Exception:
                pass
