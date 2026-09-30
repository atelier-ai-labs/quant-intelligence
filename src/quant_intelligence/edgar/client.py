"""Rate-limited, caching SEC EDGAR client.

Only the free public endpoints are used. SEC fair-access rules require a descriptive
User-Agent with contact details and at most 10 requests/second; we refuse to run without
a User-Agent and default to a conservative 5 requests/second.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession_nodash}/{document}"
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
ACCESSION_RE = re.compile(r"^\d{10}-\d{2}-\d{6}$")

# transport(url, headers, timeout) -> (status_code, body). Injected in tests so no network is used.
Transport = Callable[[str, dict[str, str], float], tuple[int, bytes]]


class SecConfigurationError(RuntimeError):
    """Raised when the client is not configured to meet SEC fair-access requirements."""


class SecFetchError(RuntimeError):
    """Raised when EDGAR cannot be fetched after retries (callers must fail closed)."""


def urllib_transport(url: str, headers: dict[str, str], timeout: float) -> tuple[int, bytes]:
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed https hosts
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read() if exc.fp else b""


class RateLimiter:
    """Minimum-interval limiter; thread-safe and injectable for tests."""

    def __init__(self, max_per_second: float, clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        if not 0 < max_per_second <= 10: raise SecConfigurationError("SEC rate limit must be in (0, 10] requests/second")
        self.interval = 1.0 / max_per_second
        self.clock, self.sleep = clock, sleep
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = self.clock()
            if now < self._next:
                self.sleep(self._next - now)
                now = self._next
            self._next = now + self.interval


def parse_acceptance(value: str) -> datetime:
    """Parse EDGAR acceptanceDateTime (submissions JSON is UTC, e.g. 2025-10-31T10:01:26.000Z)."""
    if not value: raise ValueError("missing acceptanceDateTime")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None: raise ValueError(f"acceptanceDateTime is not timezone-aware: {value!r}")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class FilingRef:
    ticker: str
    cik: int
    form: str
    accession_no: str
    accepted_at: datetime
    filing_date: date
    primary_document: str

    @property
    def raw_document(self) -> str:
        # Form 4 primaryDocument points at the XSL-rendered HTML (xslF345X06/form4.xml); the raw XML is the basename.
        return self.primary_document.rsplit("/", 1)[-1]

    @property
    def url(self) -> str:
        return ARCHIVE_URL.format(cik=self.cik, accession_nodash=self.accession_no.replace("-", ""), document=self.raw_document)

    @property
    def storage_key(self) -> str:
        return f"{self.accepted_at.strftime('%Y%m%dT%H%M%SZ')}_{self.accession_no}"

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["accepted_at"] = self.accepted_at.isoformat(); data["filing_date"] = self.filing_date.isoformat()
        return data


class SecClient:
    def __init__(self, user_agent: str | None, cache_dir: str | Path = "data/edgar", *, transport: Transport | None = None,
                 max_per_second: float = 5.0, max_retries: int = 4, backoff_seconds: float = 1.0, timeout: float = 30.0,
                 sleep: Callable[[float], None] | None = None, clock: Callable[[], float] | None = None):
        user_agent = (user_agent or "").strip()
        if not user_agent: raise SecConfigurationError("SEC_USER_AGENT is required (e.g. 'Your Org research you@example.com'); refusing to call EDGAR")
        if "@" not in user_agent: raise SecConfigurationError("SEC_USER_AGENT must include a contact email address")
        self.user_agent = user_agent
        self.cache_dir = Path(cache_dir)
        self.transport = transport or urllib_transport
        sleep = sleep or (lambda seconds: time.sleep(seconds))
        self.limiter = RateLimiter(max_per_second, clock=clock or time.monotonic, sleep=sleep)
        self.max_retries, self.backoff_seconds, self.timeout, self.sleep = max_retries, backoff_seconds, timeout, sleep
        self.request_count = 0

    @classmethod
    def from_env(cls, **kwargs: Any) -> "SecClient":
        return cls(os.environ.get("SEC_USER_AGENT"), os.environ.get("QI_EDGAR_CACHE_DIR", "data/edgar"), **kwargs)

    def get(self, url: str) -> bytes:
        headers = {"User-Agent": self.user_agent, "Accept-Encoding": "identity"}
        last_error = "no attempt"
        for attempt in range(self.max_retries + 1):
            self.limiter.wait()
            self.request_count += 1
            try:
                status, body = self.transport(url, headers, self.timeout)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                status, body, last_error = None, b"", f"{type(exc).__name__}: {exc}"
            if status == 200: return body
            if status is not None:
                last_error = f"HTTP {status}"
                if status not in RETRYABLE_STATUS: break
            if attempt < self.max_retries:
                delay = self.backoff_seconds * (2 ** attempt)
                log.warning("EDGAR fetch failed (%s) for %s; retrying in %.1fs", last_error, url, delay)
                self.sleep(delay)
        raise SecFetchError(f"failed to fetch {url}: {last_error}")

    def cached_get(self, url: str, path: Path, *, refresh: bool = False) -> bytes:
        if path.is_file() and not refresh: return path.read_bytes()
        body = self.get(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp"); tmp.write_bytes(body); tmp.replace(path)
        return body

    def ticker_to_cik(self, ticker: str, *, refresh: bool = False) -> int:
        data = json.loads(self.cached_get(TICKERS_URL, self.cache_dir / "company_tickers.json", refresh=refresh))
        wanted = ticker.upper().strip()
        for row in data.values():
            if str(row.get("ticker", "")).upper() == wanted: return int(row["cik_str"])
        raise SecFetchError(f"ticker {ticker!r} not found in SEC company_tickers.json")

    def submissions(self, cik: int, *, refresh: bool = True) -> dict[str, Any]:
        return json.loads(self.cached_get(SUBMISSIONS_URL.format(cik=cik), self.cache_dir / "submissions" / f"CIK{cik:010d}.json", refresh=refresh))

    def list_filings(self, ticker: str, forms: tuple[str, ...] = ("10-K", "4"), *, refresh: bool = True,
                     min_10k: int = 2, min_10q: int = 0, max_extra_pages: int = 6) -> list[FilingRef]:
        """Filings newest first. Large filers overflow `filings.recent`; older pages load until history quotas are met."""
        cik = self.ticker_to_cik(ticker)
        data = self.submissions(cik, refresh=refresh)
        filings = filings_from_submissions(ticker, cik, data, forms)

        def _need_more() -> bool:
            if "10-K" in forms and sum(f.form == "10-K" for f in filings) < min_10k:
                return True
            if "10-Q" in forms and min_10q > 0 and sum(f.form == "10-Q" for f in filings) < min_10q:
                return True
            return False

        for page in data.get("filings", {}).get("files", [])[:max_extra_pages]:
            if not _need_more():
                break
            name = str(page.get("name", ""))
            if not re.fullmatch(r"CIK\d{10}-submissions-\d{3}\.json", name):
                continue
            older = json.loads(self.cached_get(f"https://data.sec.gov/submissions/{name}", self.cache_dir / "submissions" / name, refresh=refresh))
            filings += filings_from_submissions(ticker, cik, {"filings": {"recent": older}}, forms)
        filings.sort(key=lambda f: f.accepted_at, reverse=True)
        return filings

    def fetch_document(self, filing: FilingRef) -> bytes:
        path = self.cache_dir / filing.ticker.upper() / filing.form.replace("/", "_") / filing.storage_key / filing.raw_document
        body = self.cached_get(filing.url, path)
        meta = path.parent / "filing.json"
        if not meta.is_file(): meta.write_text(json.dumps(filing.to_json(), indent=2), encoding="utf-8")
        return body


def filings_from_submissions(ticker: str, cik: int, submissions: dict[str, Any], forms: tuple[str, ...]) -> list[FilingRef]:
    """Build FilingRefs from the `filings.recent` arrays, newest first. Malformed rows are skipped (never guessed)."""
    recent = submissions.get("filings", {}).get("recent", {})
    out: list[FilingRef] = []
    for i, form in enumerate(recent.get("form", [])):
        if form not in forms: continue
        try:
            accession = recent["accessionNumber"][i]
            if not ACCESSION_RE.match(accession): raise ValueError(f"bad accession {accession!r}")
            out.append(FilingRef(ticker.upper(), cik, form, accession, parse_acceptance(recent["acceptanceDateTime"][i]),
                                 date.fromisoformat(recent["filingDate"][i]), recent["primaryDocument"][i]))
        except (KeyError, IndexError, ValueError) as exc:
            log.warning("skipping malformed submissions row %d for %s: %s", i, ticker, exc)
    out.sort(key=lambda f: f.accepted_at, reverse=True)
    return out
