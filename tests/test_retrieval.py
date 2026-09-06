"""Per-source feed retrieval lane tests (oracle ora-1 fallback lane).

Observable behavior (not privates):
- get_retrieval_policy: allowlist validation, defaults, never raises
- curated JSON carries retrieval+enrichment stanzas for the 4 V1 sources only
- fetch_rss stdlib-only lane keeps current behavior byte-identical
- fetch_rss impersonated-feed lane retries once via curl_cffi on 403/challenge
  evidence, else raises an explicit fetch error (no Firecrawl/Playwright)
- fetch_task threads source_name -> retrieval policy; the leaf flow passes it
"""

from __future__ import annotations

import io
import json
import urllib.error
from email.message import Message
from pathlib import Path
from typing import Any

import pytest

import brain.flows as flows
import brain.ingest as ingest
from brain.flows import fetch_task, ingest_source_flow
from brain.ingest import fetch_rss
from brain.normalize import NormalizedDocument
from brain.sources import get_retrieval_policy, get_source

FIXTURES = Path(__file__).parent / "fixtures"
CURATED = (
    Path(__file__).resolve().parents[1]
    / ".scratch"
    / "trend-intelligence-brain"
    / "curated-sources.json"
)


# --- policy validation -------------------------------------------------------


def test_v1_policies_match_oracle_design() -> None:
    assert get_retrieval_policy("Social Media Today") == {
        "type": "rss",
        "policy": "impersonated-feed",
    }
    assert get_retrieval_policy("InfoMoney") == {"type": "rss", "policy": "impersonated-feed"}
    assert get_retrieval_policy("MarTech") == {"type": "rss", "policy": "stdlib-only"}
    assert get_retrieval_policy("Professional Jeweller") == {
        "type": "rss",
        "policy": "stdlib-only",
    }


def test_policy_never_raises_and_defaults() -> None:
    default = {"type": "rss", "policy": "stdlib-only"}
    assert get_retrieval_policy("No Such Source") == default
    assert get_retrieval_policy(None) == default
    # Extra registry sources without stanzas stay stdlib-only.
    assert get_retrieval_policy("JCK Online") == default


def test_policy_result_is_a_copy() -> None:
    first = get_retrieval_policy("Social Media Today")
    first["policy"] = "mutated"
    assert get_retrieval_policy("Social Media Today")["policy"] == "impersonated-feed"


# --- curated JSON stanzas ------------------------------------------------------


def test_curated_v1_stanzas() -> None:
    entries = {e["source_name"]: e for e in json.loads(CURATED.read_text(encoding="utf-8"))}
    assert entries["Social Media Today"]["retrieval"] == {
        "type": "rss",
        "policy": "impersonated-feed",
    }
    assert entries["InfoMoney"]["retrieval"] == {"type": "rss", "policy": "impersonated-feed"}
    assert entries["MarTech"]["retrieval"] == {"type": "rss", "policy": "stdlib-only"}
    assert entries["Professional Jeweller"]["retrieval"] == {
        "type": "rss",
        "policy": "stdlib-only",
    }
    for name in (
        "Social Media Today",
        "InfoMoney",
        "MarTech",
        "Professional Jeweller",
    ):
        assert entries[name]["enrichment"] == {"threshold": 500, "mode": "auto"}
    # Stanzas exist for the 4 V1 sources only.
    for name in ("JCK Online", "Swarovski PR Newswire"):
        assert "retrieval" not in entries[name]
        assert "enrichment" not in entries[name]


# --- fetch_rss fakes -----------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self, *args: Any) -> bytes:
        return self._payload

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *args: Any) -> bool:
        return False


def _http_error(url: str, code: int, body: bytes) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, "Forbidden", Message(), io.BytesIO(body))


class _FakeCurlResponse:
    def __init__(self, status_code: int, content: bytes) -> None:
        self.status_code = status_code
        self.content = content


class _FakeCurl:
    def __init__(self, status_code: int = 200, content: bytes = b"<rss/>") -> None:
        self.calls: list[dict[str, Any]] = []
        self._status_code = status_code
        self._content = content

    def get(self, url: str, **kwargs: Any) -> _FakeCurlResponse:
        self.calls.append({"url": url, **kwargs})
        return _FakeCurlResponse(self._status_code, self._content)


def _stub_urlopen(monkeypatch: pytest.MonkeyPatch, payload_or_exc: Any) -> None:
    def fake_open(request: Any, timeout: int = 30) -> _FakeResponse:
        if isinstance(payload_or_exc, Exception):
            raise payload_or_exc
        return _FakeResponse(bytes(payload_or_exc))

    monkeypatch.setattr("urllib.request.urlopen", fake_open)


# --- stdlib-only lane: current behavior ---------------------------------------


def test_stdlib_lane_passes_bytes_through(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_urlopen(monkeypatch, b"<rss>ok</rss>")
    assert fetch_rss("http://example.com/feed") == b"<rss>ok</rss>"
    assert fetch_rss("http://example.com/feed", policy="stdlib-only") == b"<rss>ok</rss>"


def test_stdlib_lane_for_unknown_policy_and_unregistered_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_urlopen(monkeypatch, b"<rss>ok</rss>")
    assert fetch_rss("http://example.com/feed", policy="bogus") == b"<rss>ok</rss>"


def test_stdlib_source_uses_plain_path_without_curl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeCurl()
    monkeypatch.setattr(ingest, "_curl_cffi_requests", fake)
    _stub_urlopen(monkeypatch, b"<rss>ok</rss>")
    assert fetch_rss(get_source("MarTech")["rss_url"]) == b"<rss>ok</rss>"
    assert fake.calls == []


# --- impersonated-feed lane ----------------------------------------------------


def test_impersonated_lane_first_try_success_needs_no_curl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeCurl()
    monkeypatch.setattr(ingest, "_curl_cffi_requests", fake)
    _stub_urlopen(monkeypatch, b"<rss>clean</rss>")
    assert fetch_rss("http://example.com/feed", policy="impersonated-feed") == b"<rss>clean</rss>"
    assert fake.calls == []


def test_impersonated_lane_retries_on_403(monkeypatch: pytest.MonkeyPatch) -> None:
    url = "http://example.com/feed"
    _stub_urlopen(monkeypatch, _http_error(url, 403, b""))
    fake = _FakeCurl(content=b"<rss>via-impersonation</rss>")
    monkeypatch.setattr(ingest, "_curl_cffi_requests", fake)
    assert fetch_rss(url, policy="impersonated-feed") == b"<rss>via-impersonation</rss>"
    assert len(fake.calls) == 1
    assert fake.calls[0]["impersonate"] == "chrome"
    assert fake.calls[0]["url"] == url


def test_impersonated_lane_retries_on_challenge_body(monkeypatch: pytest.MonkeyPatch) -> None:
    url = "http://example.com/feed"
    body = b"<html>Attention Required! | Cloudflare challenge</html>"
    _stub_urlopen(monkeypatch, _http_error(url, 500, body))
    fake = _FakeCurl(content=b"<rss>via-impersonation</rss>")
    monkeypatch.setattr(ingest, "_curl_cffi_requests", fake)
    assert fetch_rss(url, policy="impersonated-feed") == b"<rss>via-impersonation</rss>"
    assert len(fake.calls) == 1


def test_impersonated_lane_explicit_error_without_curl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "http://example.com/feed"
    _stub_urlopen(monkeypatch, _http_error(url, 403, b""))
    monkeypatch.setattr(ingest, "_curl_cffi_requests", None)
    with pytest.raises(RuntimeError, match="impersonated-feed"):
        fetch_rss(url, policy="impersonated-feed")


def test_impersonated_lane_plain_404_is_explicit_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "http://example.com/feed"
    _stub_urlopen(monkeypatch, _http_error(url, 404, b"not found"))
    fake = _FakeCurl()
    monkeypatch.setattr(ingest, "_curl_cffi_requests", fake)
    with pytest.raises(RuntimeError, match="impersonated-feed"):
        fetch_rss(url, policy="impersonated-feed")
    assert fake.calls == []


def test_impersonated_lane_transport_error_is_explicit_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_urlopen(monkeypatch, urllib.error.URLError("timed out"))
    fake = _FakeCurl()
    monkeypatch.setattr(ingest, "_curl_cffi_requests", fake)
    with pytest.raises(RuntimeError, match="impersonated-feed"):
        fetch_rss("http://example.com/feed", policy="impersonated-feed")
    assert fake.calls == []


def test_impersonated_lane_failed_retry_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    url = "http://example.com/feed"
    _stub_urlopen(monkeypatch, _http_error(url, 403, b""))
    fake = _FakeCurl(status_code=403, content=b"still blocked")
    monkeypatch.setattr(ingest, "_curl_cffi_requests", fake)
    with pytest.raises(RuntimeError, match="impersonated-feed"):
        fetch_rss(url, policy="impersonated-feed")


def test_impersonated_lane_resolves_registered_url_without_policy_arg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = get_source("Social Media Today")["rss_url"]
    _stub_urlopen(monkeypatch, _http_error(url, 403, b""))
    fake = _FakeCurl(content=b"<rss>via-impersonation</rss>")
    monkeypatch.setattr(ingest, "_curl_cffi_requests", fake)
    assert fetch_rss(url) == b"<rss>via-impersonation</rss>"
    assert len(fake.calls) == 1


# --- fetch_task threading ------------------------------------------------------


def test_fetch_task_threads_source_policy_without_changing_fetch_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple] = []

    def fake_fetch(url: str, timeout: int = 30) -> bytes:
        seen.append((url, timeout))
        return b"<rss/>"

    monkeypatch.setattr(flows, "fetch_rss", fake_fetch)
    out = fetch_task("http://example.com/feed", source_name="MarTech")
    assert out == b"<rss/>"
    assert seen == [("http://example.com/feed", 30)]  # single-arg compatible
    # Unknown sources never raise: default lane applies.
    assert fetch_task("http://example.com/feed", source_name="No Such Source") == b"<rss/>"


def test_leaf_flow_threads_source_name_into_fetch_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_fetch_task(url: str, source_name: str | None = None) -> bytes:
        captured["url"] = url
        captured["source_name"] = source_name
        return (FIXTURES / "martech_sample.xml").read_bytes()

    monkeypatch.setattr(flows, "fetch_task", fake_fetch_task)
    monkeypatch.setattr(flows, "enrich_document_or_keep", lambda doc, *a, **k: (doc, "rss", None))
    monkeypatch.setattr(flows, "upsert_documents", lambda docs: (len(docs), 0))
    result = ingest_source_flow(source_name="MarTech")
    assert result == {"inserted": 3, "skipped": 0}
    assert captured["source_name"] == "MarTech"
    assert captured["url"] == get_source("MarTech")["rss_url"]


def test_impersonated_source_flow_still_succeeds_with_stubbed_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Policy resolution must not break impersonated sources under the standard
    (url, timeout) fetch stub used across the suite."""
    fixture = (FIXTURES / "smt_sample.xml").read_bytes()
    monkeypatch.setattr(flows, "fetch_rss", lambda url, timeout=30: fixture)
    monkeypatch.setattr(flows, "enrich_document_or_keep", lambda doc, *a, **k: (doc, "rss", None))
    monkeypatch.setattr(flows, "upsert_documents", lambda docs: (len(docs), 0))
    result = ingest_source_flow(source_name="Social Media Today")
    assert result["inserted"] == 3
    assert "error" not in result


def test_batch_keeps_shape_across_both_lanes(monkeypatch: pytest.MonkeyPatch) -> None:
    from brain.flows import ingest_sources_flow

    by_url = {get_source(n)["rss_url"]: n for n in ("MarTech", "InfoMoney")}
    fixtures = {
        "MarTech": (FIXTURES / "martech_sample.xml").read_bytes(),
        "InfoMoney": (FIXTURES / "infomoney_sample.xml").read_bytes(),
    }
    monkeypatch.setattr(flows, "fetch_rss", lambda url, timeout=30: fixtures[by_url[url]])
    monkeypatch.setattr(flows, "enrich_document_or_keep", lambda doc, *a, **k: (doc, "rss", None))

    def fake_upsert(docs: list[NormalizedDocument]) -> tuple[int, int]:
        return (len(docs), 0)

    monkeypatch.setattr(flows, "upsert_documents", fake_upsert)
    results = ingest_sources_flow(source_names=["MarTech", "InfoMoney"])
    assert results["MarTech"] == {"inserted": 3, "skipped": 0}
    assert results["InfoMoney"] == {"inserted": 3, "skipped": 0}
