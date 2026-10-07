#!/usr/bin/env python3
"""
bi_rdg.py — build a corpus of Bank Indonesia Board of Governors (RDG) press releases.

Thesis: measuring BI's de facto independence from the wedge between its
communicated stance and its estimated reaction function.

WHAT THIS DOES
--------------
BI publishes each press release at a URL of the form

    https://www.bi.go.id/{lang}/publikasi/ruang-media/news-release/Pages/sp_{code}.aspx

where {lang} is "id" or "en" and {code} decomposes as PREFIX + SEQ(3 digits) + YY(2 digits).
Verified example: sp_2810726  ->  prefix 28, release #107, year 2026
                                  = "BI-Rate Naik 50 bps menjadi 5,25%", 20 May 2026,
                                    reference No.28/107/DKom
The SAME code serves both languages, so bilingual pairing is free.

Body text is organised as paragraphs whose opening sentence is bold. Those bold
lead-ins are the section headings (global conditions, inflation, rupiah, and so on),
which is what lets us build topic-segmented tone rather than one flat score.

COMMANDS
--------
    selftest    Parse a bundled fixture. Runs offline. Do this first.
    pilot       Probe a few years, grab a handful of RDG releases, parse, audit.
                This is the go/no-go test for the whole thesis. ~5 minutes.
    discover    Scan sequence numbers across years, save RDG URLs to urls.csv.
    fetch       Download raw HTML for everything in urls.csv (cached).
    parse       Turn raw HTML into documents.csv + corpus.csv.
    audit       Print a human-readable report so you can eyeball the parse.

TYPICAL RUN
-----------
    python bi_rdg.py selftest
    python bi_rdg.py pilot
    # inspect audit_report.txt by hand, then:
    python bi_rdg.py discover --start-year 2005 --end-year 2026
    python bi_rdg.py fetch
    python bi_rdg.py parse
    python bi_rdg.py audit --n 30

Requires: requests, beautifulsoup4, lxml, pandas
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("Missing dependencies. Run:  pip install requests beautifulsoup4 lxml pandas")
    raise

# --------------------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------------------

BASE = "https://www.bi.go.id"

# BI has moved its press releases between paths over the years. Verified examples:
#   sp_2810726  /id/publikasi/ruang-media/news-release/  20 May 2026, No.28/107/DKom
#   sp_285026   /id/publikasi/ruang-media/news-release/  27 Feb 2026, No.28/50/DKom
#   sp_263324   /id/publikasi/ruang-media/news-release/  2024, release #33
#   SP_212519   /id/ruang-media/siaran-pers/             2019, release #25
#   sp_192417   /id/ruang-media/siaran-pers/             2017, release #24
# Older material (2004 and thereabouts) sits in an archive as PDFs with descriptive
# filenames, which are NOT enumerable — see the `probe` command's notes.
PATH_TEMPLATES = [
    ("news-release", BASE + "/{lang}/publikasi/ruang-media/news-release/Pages/sp_{code}.aspx"),
    ("siaran-pers",  BASE + "/{lang}/ruang-media/siaran-pers/Pages/sp_{code}.aspx"),
]
PATH_BY_NAME = dict(PATH_TEMPLATES)
URL_TEMPLATE = PATH_TEMPLATES[0][1]   # default, used only as a fallback

# Identify yourself. BI is a public institution; scraping politely and identifiably
# is both good manners and good protection. Put your real email here.
USER_AGENT = (
    "Mozilla/5.0 (compatible; academic-research/1.0; "
    "master's thesis on BI communication; contact: pagesibin@korea.ac.kr)"
)

DEFAULT_DELAY = 1.5      # seconds between requests. Do not lower this.
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3

ROOT = Path(__file__).resolve().parent
RAW_DIR = ROOT / "raw"
OUT_DIR = ROOT / "out"

# Verified year -> prefix anchors. The rule appears to be prefix = year - 1998, which
# holds for every case checked (2017->19, 2019->21, 2024->26, 2025->27, 2026->28), but
# probing confirms it per year rather than trusting it.
KNOWN_ANCHORS = {2017: 19, 2019: 21, 2024: 26, 2025: 27, 2026: 28}

# Sequence numbers to try when probing a year. Deliberately mid-range: release #1 of a
# year often does not resolve, so probing at seq 1 gives a false negative — which is
# exactly the bug that made the first pilot report "no releases found" everywhere.
# Consecutive empty sequence numbers before a year is considered finished.
MISS_TOLERANCE = 150

PROBE_SEQS = [50, 25, 100, 75, 10, 150, 200, 5]

# --------------------------------------------------------------------------------------
# Patterns
# --------------------------------------------------------------------------------------

# Which releases are monetary policy decisions. Deliberately broad: it is far cheaper
# to over-collect and filter by hand than to silently miss meetings.
RDG_TITLE_RE = re.compile(
    r"(bi[-\s]?rate"
    r"|bi[-\s]?7[-\s]?day"
    r"|7[-\s]?day\s+reverse\s+repo"
    r"|suku\s+bunga\s+acuan"
    r"|hasil\s+rapat\s+dewan\s+gubernur"
    r"|rapat\s+dewan\s+gubernur"
    r"|\brdg\b"
    r"|board\s+of\s+governors)",
    re.IGNORECASE,
)

# Direction of the decision. Checked against the decision sentence only, and hike/cut
# are tested before hold because "tetap" ("remains") turns up in unrelated clauses.
#
# Several stems below were widened from an exact word to a \w* stem, or had a new
# synonym added, after auditing real 2005-2011 titles that parsed with NO direction
# at all under the original patterns:
#   "meningkat\w*"  — "meningkat" (rose, intransitive) is a separate word from
#                      "naik", not an inflection of it; only "meningkatkan"
#                      (transitive) was covered before. sp_bi_rate_090805-1,
#                      sp_it_60905_bi_rate-1.
#   "turunkan"      — "Turunkan" (imperative "Lower!") is the prefix-less form,
#                      distinct from "menurunkan"/"turun". sp_181416.
#   "eas\w+"        — "Eases"/"Easing" (monetary easing = a cut) wasn't covered by
#                      any cut synonym at all. sp_05052009.
#   "hold\w*"       — was exact "hold"; "Holds" (3rd person singular) didn't match
#                      due to the \b boundary right after "hold". sp_125410.
#   "keep\w*"       — same issue as "hold": was exact "keep", missed "Keeps".
#                      sp_113209, sp_112609.
#   "stay\w*"       — new synonym: "Stays at X%" wasn't covered. sp_122310.
#   "leav\w*"       — new synonym: "leaves BI Rate at X% unchanged" wasn't covered.
#                      sp_112909.
DIRECTION_RULES = [
    ("hike", re.compile(r"\b(menaikkan|dinaikkan|naik|meningkat\w*"
                        r"|increas\w+|rais\w+|hik\w+|rise[sn]?|rising|rose)\b", re.IGNORECASE)),
    ("cut",  re.compile(r"\b(menurunkan|turunkan|diturunkan|turun|memangkas"
                        r"|lower\w*|cut\w*|reduc\w+|decreas\w+|eas\w+|fall\w*|fell|trim\w*)\b", re.IGNORECASE)),
    ("hold", re.compile(r"\b(mempertahankan|dipertahankan|menahan|tetap|tidak\s+berubah"
                        r"|held|hold\w*|maintain\w+|retain\w*|kept|keep\w*|unchanged"
                        r"|steady|stay\w*|leav\w*)\b", re.IGNORECASE)),
]

# Mentions of the policy rate itself. extract_decision() picks the direction word
# CLOSEST to one of these rather than the first rule that matches anywhere — a
# fixed hike/cut/hold order let unrelated verbs win: sp_091607's title "...Optimisme
# Pertumbuhan Ekonomi Meningkat, BI Rate Tetap 9%" read as a hike on "Meningkat"
# (growth rose), and sp_188416 en's "Macroeconomic Stability Maintained, BI Cuts
# 7-Day (Reverse) Repo Rate..." read as a hold on "Maintained".
POLICY_RATE_RE = re.compile(
    r"BI[\s-]?Rate|Repo\s+Rate|RR\s+Rate|policy\s+rate|suku\s+bunga\s+(?:kebijakan|acuan)",
    re.IGNORECASE,
)

# The date BI switched its policy rate from the BI Rate to the BI 7-Day (Reverse)
# Repo Rate (announced sp_182916, effective sp_186716). Rate levels on either side
# of it are different instruments, so the level series is never differenced
# across it — see reconcile_decisions().
POLICY_RATE_SWITCH = "2016-08-19"

# "Basis Poin" (spelled out): confirmed missed on the same 2005 documents as the
# DIRECTION_RULES fix above — their titles read "25 Basis Poin", not "25 bps".
BPS_RE = re.compile(r"(\d{1,3})\s*(?:bps|basis\s+poin)", re.IGNORECASE)
LEVEL_AFTER_RE = re.compile(r"(?:menjadi|to|at|di\s+level|pada\s+level)\s*([\d.,]+)\s*%",
                            re.IGNORECASE)
LEVEL_ANY_RE = re.compile(r"([\d]{1,2}[.,]\d{1,2})\s*%")

REF_RE = re.compile(r"No\.?\s*(\d{1,3})\s*/\s*(\d{1,4})\s*/\s*([A-Za-z]+)")

ID_MONTHS = {
    "januari": 1, "februari": 2, "maret": 3, "april": 4, "mei": 5, "juni": 6,
    "juli": 7, "agustus": 8, "september": 9, "oktober": 10, "november": 11,
    "desember": 12,
}
EN_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}
ALL_MONTHS = {**ID_MONTHS, **EN_MONTHS}

DATE_TEXT_RE = re.compile(
    r"(\d{1,2})\s+(" + "|".join(ALL_MONTHS.keys()) + r")\s+(\d{4})", re.IGNORECASE
)
DATE_NUM_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
PUBLISH_STAMP_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\s+\d{1,2}:\d{2}\s*[AP]M\b",
                              re.IGNORECASE)

# The closing-signature dateline every release ends with, e.g. "Jakarta, 12 Juli 2012"
# or "Jakarta, 12 July 2012". Specific and unambiguous, so it's tried before any
# generic or numeric date match — see extract_date().
JAKARTA_DATELINE_RE = re.compile(
    r"\bJakarta\s*,\s*(\d{1,2})\s+(" + "|".join(ALL_MONTHS.keys()) + r")\s+(\d{4})",
    re.IGNORECASE,
)

# Topic tagging, applied to the bold section heading. Bilingual on purpose: the same
# rule set has to work on the Indonesian and English versions of the same release,
# and agreement between the two is itself a validation check.
TOPIC_KEYWORDS = [
    ("decision",           ["rapat dewan gubernur", "memutuskan", "board of governors",
                            "decided", "bi-rate", "bi rate", "7-day reverse repo"]),
    ("global",             ["global", "dunia", "internasional", "world economy",
                            "geopolit", "timur tengah", "middle east"]),
    ("growth",             ["pertumbuhan ekonomi", "economic growth", "momentum",
                            "konsumsi", "investasi", "consumption", "investment"]),
    ("bop",                ["neraca pembayaran", "balance of payments", "npi", "bop",
                            "transaksi berjalan", "current account", "ekspor", "export"]),
    ("exchange_rate",      ["nilai tukar", "rupiah", "exchange rate", "kurs",
                            "stabilisasi", "stabilisation", "stabilization"]),
    ("inflation",          ["inflasi", "inflation", "ihk", "cpi", "harga konsumen",
                            "sasaran inflasi", "target corridor"]),
    ("monetary_policy",    ["kebijakan moneter", "monetary policy", "suku bunga",
                            "policy rate", "operasi moneter", "monetary operations",
                            "penguatan kebijakan moneter", "monetary policy response",
                            "respons kebijakan moneter"]),
    ("money_supply",       ["uang beredar", "money supply", "likuiditas", "liquidity",
                            "uang primer", "base money", "m1", "m2"]),
    ("macroprudential",    ["makroprudensial", "macroprudential", "klm", "insentif likuiditas",
                            "liquidity incentive", "countercyclical"]),
    ("credit",             ["kredit", "pembiayaan", "lending", "loan", "intermediasi",
                            "intermediation", "penyaluran", "kredit perbankan",
                            "penyaluran kredit", "bank lending", "peran kredit",
                            "role of bank lending"]),
    ("banking_resilience", ["ketahanan perbankan", "banking resilience", "permodalan",
                            "capital adequacy", "car", "npl", "risiko kredit"]),
    ("digital_payments",   ["digital", "qris", "elektronik", "electronic",
                            "keuangan digital", "ekonomi dan keuangan digital",
                            "digital economic", "transaksi digital"]),
    ("payment_system",     ["sistem pembayaran", "payment system", "uang kartal",
                            "currency in circulation", "bi-fast"]),
]

# --------------------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------------------


def log(msg: str) -> None:
    print(msg, flush=True)


def sp_code(prefix: int, seq: int, year: int) -> str:
    """Build the numeric part of an sp_ URL: prefix + sequence + 2-digit year.

    The sequence is NOT zero-padded — verified against real releases:
        seq  33 -> sp_263324   (2024)
        seq  50 -> sp_285026   (2026, No.28/50/DKom)
        seq 107 -> sp_2810726  (2026, No.28/107/DKom)
    Zero-padding it to three digits produces sp_2800126-style URLs, all of which 404.
    """
    return f"{prefix}{seq}{year % 100:02d}"


def release_url(prefix: int, seq: int, year: int, lang: str = "id",
                path: str = "news-release") -> str:
    tmpl = PATH_BY_NAME.get(path, URL_TEMPLATE)
    return tmpl.format(lang=lang, code=sp_code(prefix, seq, year))


def to_float(num: str) -> float | None:
    """'5,25' -> 5.25 ; '5.25' -> 5.25 ; '1.234,5' -> 1234.5"""
    if not num:
        return None
    s = num.strip()
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


# Some releases carry a "_dkom" (Departemen Komunikasi) or similar suffix after the
# numeric code, e.g. sp_154413_dkom.aspx / SP_154413_DKom.aspx — confirmed on real
# November 2013 releases found via the site's own date-range search, invisible to
# plain sequence guessing since get_html() only ever built the no-suffix URL. The
# optional non-capturing group below accepts either form without changing what
# group(2) captures (still just the digits).
URL_PARTS_RE = re.compile(r"/(id|en)/.*sp_(\d+)(?:_\w+)?\.aspx", re.IGNORECASE)

# 2005-2011 releases (found via the site's own date-range search, not the sp_ scheme —
# see "Filling the pre-2012 gap" work) use wildly inconsistent filenames: "sp 77105.aspx"
# (space, not underscore), "SP8422006.aspx" (no separator), "bi rate 090805-2.aspx" or
# "Rilis08122006.aspx" or "peng RDG 2006.aspx" (no "sp" at all — free-text names). No
# single numeric-code pattern covers this, so this fallback captures the whole filename
# slug instead. Tried only when URL_PARTS_RE doesn't match, so it changes nothing about
# how already-cached 2012+ URLs resolve.
URL_PARTS_FALLBACK_RE = re.compile(r"/(id|en)/.*/Pages/([^/]+)\.aspx", re.IGNORECASE)


def _url_lang_code(url: str) -> tuple[str, str] | None:
    """Return (lang, code) for a release URL, or None if neither pattern matches."""
    m = URL_PARTS_RE.search(url)
    if m:
        return m.group(1).lower(), m.group(2)
    m = URL_PARTS_FALLBACK_RE.search(url)
    if m:
        return m.group(1).lower(), re.sub(r"[^\w-]+", "_", m.group(2)).strip("_")
    return None


def cache_path(url: str) -> Path:
    """Cache location for a fetched URL.

    Keyed on (lang, path_template, code) — NOT just (lang, code). The numeric sp_
    code is not unique across path templates: the same code can independently
    resolve under both news-release and siaran-pers for a given prefix/seq/year.
    Keying on code alone caused a real collision (found 2026-09-08): fetching a
    code under news-release cached it, and a later probe of the SAME code under
    siaran-pers silently read back that cached content instead of hitting the
    network, making siaran-pers look reachable for 2017 when it was not.
    """
    parts = _url_lang_code(url)
    if parts:
        lang, code = parts
    else:
        lang, code = "xx", re.sub(r"\W+", "_", url)[-24:]
    path_name = next((name for name, _ in PATH_TEMPLATES if f"/{name}/" in url), "other")
    d = RAW_DIR / lang / path_name
    d.mkdir(parents=True, exist_ok=True)
    return d / f"sp_{code}.html"


TITLE_TAG_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)

# BI serves TWO different "not found" pages, both with HTTP 200, so status codes are
# useless:
#   modern path -> "404 Halaman Tidak Ditemukan", canonical /id/e404.aspx
#   SharePoint  -> <title>404</title>, ~133KB of chrome, no body
#
# NOTE: SharePoint's "You may be trying to access this site from a secured browser
# on the server" text was originally in this marker list too, on the assumption it
# was 404-page-only. It is not — it's boilerplate present on every SharePoint page,
# real content included, so matching on it flagged every single page as not-found.
# Confirmed on 2026-09-07 against a real, live release (sp_195017, 2017 "Cadangan
# Devisa" press release) which has real title/body text but still contains that
# string. The title check below ("404" exactly) already catches the SharePoint
# flavour on its own, so dropping the body marker loses no real detection.
SOFT_404_MARKERS = (
    "halaman tidak ditemukan",
    "e404.aspx",
)


def _is_soft_404(html: str) -> bool:
    low = html.lower()
    if any(m in low for m in SOFT_404_MARKERS):
        return True
    m = TITLE_TAG_RE.search(html)
    if m:
        title = re.sub(r"\s+", " ", m.group(1)).strip().lower()
        if title in ("404", "error", "") or title.startswith("404 "):
            return True
        if "not found" in title or "tidak ditemukan" in title:
            return True
    return False


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept-Language": "id,en;q=0.8",
    })
    return s


def get_html(url: str, session: requests.Session, delay: float = DEFAULT_DELAY,
             force: bool = False, quiet: bool = False) -> str | None:
    """Fetch with on-disk cache. Returns None on 404 (i.e. the release does not exist)."""
    path = cache_path(url)
    if path.exists() and not force:
        cached = path.read_text(encoding="utf-8", errors="replace")
        # Re-check on read: an earlier run may have cached an error page before the
        # detector knew about that flavour. Move it aside rather than trusting it.
        if _is_soft_404(cached):
            junk = RAW_DIR / "_not_found" / path.parent.relative_to(RAW_DIR)
            junk.mkdir(parents=True, exist_ok=True)
            path.replace(junk / path.name)
            path.with_suffix(".url").unlink(missing_ok=True)
            return None
        return cached

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            time.sleep(delay * (1 + 0.25 * random.random()))
            r = session.get(url, timeout=REQUEST_TIMEOUT)
            if r.status_code == 404:
                return None
            if r.status_code >= 500:
                raise requests.HTTPError(f"server {r.status_code}")
            r.raise_for_status()
            r.encoding = r.encoding or "utf-8"
            html = r.text
            # BI serves a soft-404 page rather than a 404 status for missing releases.
            if _is_soft_404(html):
                return None
            path.write_text(html, encoding="utf-8")
            # Remember which URL this file came from — the path varies by era, so it
            # cannot be reconstructed from the filename alone at parse time.
            path.with_suffix(".url").write_text(url, encoding="utf-8")
            return html
        except Exception as exc:  # noqa: BLE001
            if attempt == MAX_RETRIES:
                if not quiet:
                    log(f"    ! failed {url}: {exc}")
                return None
            time.sleep(delay * 2 * attempt)
    return None


# --------------------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------------------


@dataclass
class Section:
    order: int
    heading: str
    body: str
    topic: str


@dataclass
class Document:
    url: str = ""
    lang: str = ""
    code: str = ""
    date: str = ""
    title: str = ""
    ref_no: str = ""
    is_rdg: bool = False
    direction: str = ""
    bps: float | None = None
    rate_level: float | None = None
    n_sections: int = 0
    n_words: int = 0
    sections: list[Section] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)


def _content_root(soup: BeautifulSoup):
    """Find the element holding the release body.

    BI runs SharePoint, so the body lives in a content div whose class or id varies by
    template era. Try the known candidates, then fall back to whichever <div> holds the
    most bold-led paragraphs — which is a decent structural proxy for 'the release'.

    Each known selector is scored across ALL its matches, not just the first: a page
    can contain many elements sharing a class like ms-rtestate-field, and the real
    content one is not guaranteed to be first in document order. Confirmed on
    sp_235021: the page has 15 ms-rtestate-field divs, and the one actually holding
    the release body (5 paragraphs, starts "No. 23/50/DKom...") is the 4th, not the
    1st. Picking the 1st via select_one() (1 paragraph, below the >=3 threshold)
    fell through every known selector to the generic fallback below, which then
    picked the site's nav megamenu instead — it has more bold-led <p>s than the
    real body. That misrouted a Quarterly Monetary Policy Report (not an RDG
    decision) into is_rdg=True with fabricated direction/bps/level, because the nav
    menu's "Indikator Moneter BI-Rate" link matched RDG_TITLE_RE.

    A candidate qualifies on paragraph count OR raw text length: a >=3-paragraph
    requirement alone excludes genuinely short releases. Confirmed on
    sp_peng_RDG_2006 (a brief "tentative RDG schedule" notice): its real content
    div has only 2 <p> tags (1037 chars, opens "Dalam pasal 43 UU No. 23 Tahun
    1999..."), fell through every known selector on paragraph count alone, and hit
    the same nav-menu fallback as the sp_235021 case above.
    """
    def _qualifies(n) -> bool:
        return len(n.find_all("p")) >= 3 or len(n.get_text(strip=True)) >= 200

    for sel in [
        "div.detail-content", "div.content-detail", "div#content", "div.isi-berita",
        "div.ms-rtestate-field", "div[class*='detail']", "article",
    ]:
        candidates = [n for n in soup.select(sel) if _qualifies(n)]
        if candidates:
            return max(candidates, key=lambda n: (len(n.find_all("p")), len(n.get_text(strip=True))))

    best, best_score = None, 0
    for div in soup.find_all("div"):
        ps = div.find_all("p", recursive=True)
        if len(ps) < 3:
            continue
        score = sum(1 for p in ps if p.find(["strong", "b"]))
        if score > best_score:
            best, best_score = div, score
    return best or soup.body or soup


def _leading_bold(p) -> str:
    """Return the bold text at the start of a paragraph, or '' if it doesn't start bold."""
    for child in p.children:
        if getattr(child, "name", None) in ("strong", "b"):
            return child.get_text(" ", strip=True)
        if getattr(child, "name", None) == "span":
            style = (child.get("style") or "").lower()
            if "bold" in style or child.find(["strong", "b"]):
                return child.get_text(" ", strip=True)
            if child.get_text(strip=True):
                return ""
        if isinstance(child, str) and child.strip():
            return ""   # paragraph starts with plain text
    return ""


def extract_title(soup: BeautifulSoup, url: str = "") -> str:
    # Some pages carry a hidden/SEO <h1> that just echoes the page's own filename
    # rather than a real headline. A "no whitespace" check catches most cases (e.g.
    # "sp_154413_dkom") but not filenames that happen to contain spaces themselves —
    # confirmed on peng_RDG_2006.aspx (url slug "peng RDG 2006"), whose <h1> is
    # literally "peng RDG 2006", which passes a whitespace check but is still just
    # the filename. Comparing directly against the URL's own filename catches both.
    m = re.search(r"/Pages/([^/]+)\.aspx", url, re.IGNORECASE)
    url_slug = m.group(1).strip().lower() if m else None

    for sel in ["h1", "h2.title", "div.detail-title", "meta[property='og:title']"]:
        node = soup.select_one(sel)
        if node:
            txt = node.get("content") if node.name == "meta" else node.get_text(" ", strip=True)
            txt = (txt or "").strip()
            if txt and len(txt) > 10 and " " in txt and txt.lower() != url_slug:
                return re.sub(r"\s+", " ", txt).strip()
    if soup.title and soup.title.string:
        return re.sub(r"\s*[-|]\s*Bank Indonesia\s*$", "", soup.title.string).strip()
    return ""


def extract_date(soup: BeautifulSoup, text: str, code_year: int | None = None) -> str:
    """Return ISO date. Tries metadata, then the 'Jakarta, 20 Mei 2026' dateline
    (searched over the FULL page text, not just `text`), then d/m/Y.

    The dateline check must scan the whole page rather than a truncated prefix:
    it's a closing-signature line, so on longer releases it sits well past the
    first 4000 characters (confirmed on sp_142412: dateline at char ~8450 of
    ~9600). Skipping it let a SharePoint page-metadata timestamp — an unrelated
    "last modified" field near the page top, e.g. "7/12/2012 9:04 AM" — win the
    match instead, which the ambiguous d/m-vs-m/d guess below then read as
    2012-12-07 for a release that was actually on 2012-07-12. The dateline is
    specific and appears exactly once per release, so searching the full text
    for it carries no extra false-positive risk the way a generic date search
    would.
    """
    for sel in ["meta[property='article:published_time']", "time[datetime]"]:
        node = soup.select_one(sel)
        if node:
            raw = node.get("content") or node.get("datetime") or ""
            m = re.match(r"(\d{4})-(\d{2})-(\d{2})", raw)
            if m:
                return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    page = soup.get_text(" ", strip=True)
    dateline = ""
    m = JAKARTA_DATELINE_RE.search(page)
    if m:
        d, mon, y = int(m.group(1)), ALL_MONTHS[m.group(2).lower()], int(m.group(3))
        dateline = f"{y:04d}-{mon:02d}-{d:02d}"

    # SharePoint's publish stamp, "Communication Department 7/21/2016 10:00 AM" — M/D
    # order, unambiguous because of the clock time after it. Agrees with the dateline
    # on 98% of cached pages (2095/2133). It outranks a body date: sp_185616 en has no
    # dateline, and its body's first date ("come to effect on 19 August 2016") is a
    # future effective date, not the release date (21 July 2016).
    stamp = ""
    m = PUBLISH_STAMP_RE.search(page)
    if m:
        stamp = f"{int(m.group(3)):04d}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"

    # The dateline is hand-typed and occasionally carries the wrong year (sp_245122
    # "15 Februari 2021" on a No.24/.. 2022 release; sp_283526 "2025" on a 2026 one).
    # When the code says which year it is, and the stamp agrees but the dateline
    # doesn't, trust the stamp.
    if dateline and code_year and stamp and int(dateline[:4]) != code_year \
            and int(stamp[:4]) == code_year:
        return stamp
    if dateline:
        return dateline
    if stamp:
        return stamp

    m = DATE_TEXT_RE.search(text)
    if m:
        d, mon, y = int(m.group(1)), ALL_MONTHS[m.group(2).lower()], int(m.group(3))
        return f"{y:04d}-{mon:02d}-{d:02d}"

    m = DATE_NUM_RE.search(text)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        # BI's metadata line renders as M/D/YYYY; body dates are D/M/YYYY.
        mon, day = (a, b) if a <= 12 and b > 12 else (b, a) if b <= 12 else (a, b)
        return f"{y:04d}-{mon:02d}-{day:02d}"
    return ""


TOPIC_OVERRIDES: dict[str, str] = {}   # heading prefix -> topic, loaded from CSV


def heading_key(heading: str, words: int = 6) -> str:
    """First few words of a heading, normalised. BI reuses ~20 heading templates across
    the whole archive, so this key collapses hundreds of releases into a short list you
    can hand-map once — far more reliable than any keyword rule."""
    h = re.sub(r"[^\w\s]", " ", heading.lower())
    return " ".join(h.split()[:words])


def tag_topic(heading: str, order: int = 0) -> str:
    """Assign a topic to a section heading.

    Three stages, in order of reliability:
      1. a hand-made override keyed on the heading's opening words (see `topics` command);
      2. position 1 is always the decision paragraph;
      3. keyword scoring, weighted by how early in the heading the keyword appears —
         BI states a paragraph's subject in its opening clause, so an early match is
         much more informative than a passing mention later on.
    """
    h = heading.lower()
    key = heading_key(heading)
    if key in TOPIC_OVERRIDES:
        return TOPIC_OVERRIDES[key]
    if order == 1 and RDG_TITLE_RE.search(h):
        return "decision"

    n = max(len(h), 1)
    best, best_score = "other", 0.0
    for topic, kws in TOPIC_KEYWORDS:
        score = 0.0
        for k in kws:
            idx = h.find(k)
            if idx >= 0:
                score += len(k) * (1.0 + 2.0 * (1.0 - idx / n))
        if score > best_score:
            best, best_score = topic, score
    return best


def load_topic_overrides() -> None:
    path = ROOT / "topic_overrides.csv"
    if not path.exists():
        return
    for row in _read_csv(path):
        key, topic = row.get("heading_key", "").strip(), row.get("topic", "").strip()
        if key and topic:
            TOPIC_OVERRIDES[key] = topic
    if TOPIC_OVERRIDES:
        log(f"Loaded {len(TOPIC_OVERRIDES)} topic overrides from topic_overrides.csv")


# (code, lang) -> [{action, field, new_value}, ...] from hand review; lang == ""
# applies to both language versions of that code. A list, not a single dict — a
# document commonly needs more than one fix_field row (e.g. both direction and
# bps), and a dict keyed on (code, lang) would let the second row silently
# overwrite the first. Loaded once per `parse` run — see load_review_notes() and
# cmd_parse()'s call site.
REVIEW_NOTES: dict[tuple[str, str], list[dict]] = {}

REVIEW_FIELDS = {"date", "title", "direction", "bps", "rate_level", "is_rdg"}


def load_review_notes() -> None:
    """review_notes.csv is where hand-review findings go (see README) — one row per
    correction, action is 'exclude' (drop the document entirely, e.g. it's a
    schedule notice, not an RDG decision) or 'fix_field' (override one field: date,
    title, direction, bps, or rate_level). Applied in cmd_parse() so re-running
    `parse` after a raw HTML refresh never loses a hand-reviewed correction — same
    durability guarantee topic_overrides.csv already gives the topic labels.
    """
    path = ROOT / "review_notes.csv"
    if not path.exists():
        return
    n = 0
    for row in _read_csv(path):
        code = row.get("code", "").strip()
        lang = row.get("lang", "").strip()
        action = row.get("action", "").strip().lower()
        if not code:
            continue
        # Warn rather than skip silently: a row with its columns shifted by one
        # (action="action", field="fix_field") or lang="eng" instead of "en" used to
        # vanish without a trace, so the hand fix it carried never reached the output.
        if action not in ("exclude", "fix_field"):
            log(f"  ! review_notes.csv: skipping {code!r} — unknown action {action!r} "
                f"(expected 'exclude' or 'fix_field')")
            continue
        if lang not in ("", "id", "en"):
            log(f"  ! review_notes.csv: skipping {code!r} — unknown lang {lang!r} "
                f"(expected 'id', 'en', or blank for both)")
            continue
        field = row.get("field", "").strip()
        if action == "fix_field" and field not in REVIEW_FIELDS:
            log(f"  ! review_notes.csv: skipping {code!r} — unknown field {field!r} "
                f"(expected one of {sorted(REVIEW_FIELDS)})")
            continue
        REVIEW_NOTES.setdefault((code, lang), []).append({
            "action": action, "field": field,
            "new_value": row.get("new_value", "").strip(),
        })
        n += 1
    if n:
        log(f"Loaded {n} review notes from review_notes.csv")


def _review_notes_for(code: str, lang: str) -> list[dict]:
    return REVIEW_NOTES.get((code, lang), []) + REVIEW_NOTES.get((code, ""), [])


def _apply_review_note(doc: "Document", note: dict) -> None:
    field, new_value = note["field"], note["new_value"]
    if field in ("bps", "rate_level"):
        setattr(doc, field, to_float(new_value) if new_value else None)
    elif field == "is_rdg":
        doc.is_rdg = new_value.strip().lower() in ("true", "1", "yes")
    else:
        setattr(doc, field, new_value)


def reconcile_decisions(parsed: list[tuple["Document", set[str]]]) -> None:
    """Cross-check each release's text-parsed decision against two independent
    signals, fill what the text left blank, and flag disagreements for hand review:

      1. the rate-level series — this meeting's level minus the previous meeting's
         level (either language, same instrument) implies hike/cut/hold and the size;
      2. the other language version of the same code — both are official versions
         of ONE decision, so direction, bps, level and date must agree.

    Only fills blanks, never overwrites a parsed value (a mismatch is flagged, since
    either side could be the wrong one), and never touches a field set by hand in
    review_notes.csv (`hand` holds those field names).
    """
    def _flag(doc, f):
        if f not in doc.flags:
            doc.flags.append(f)

    for d, hand in parsed:
        if d.direction == "hold" and d.bps is None and "bps" not in hand:
            d.bps = 0.0   # e.g. a hand-set direction=hold, which bypasses extract_decision

    # One series across BOTH languages: the policy rate doesn't depend on language,
    # and the en archive has gaps (no en release for Feb 2012, for instance), so a
    # per-language series compared en Mar 2012 against en Jan 2012 and flagged a
    # correct hold as a mismatch. Each release is compared with the latest meeting
    # on a strictly earlier date — the id release where both exist.
    series = sorted(
        ((d, hand) for d, hand in parsed
         if d.is_rdg and d.date and d.rate_level is not None and d.direction != "initial"),
        key=lambda x: (x[0].date, x[0].lang != "id"),
    )
    by_date: dict[str, "Document"] = {}
    for d, _ in series:
        by_date.setdefault(d.date, d)
    dates = sorted(by_date)
    prev_of = {dt: by_date[dates[i - 1]] for i, dt in enumerate(dates) if i}
    for d, hand in series:
        prev = prev_of.get(d.date)
        if prev is None or (prev.date < POLICY_RATE_SWITCH) != (d.date < POLICY_RATE_SWITCH):
            continue
        delta = round((d.rate_level - prev.rate_level) * 100)
        implied = "hike" if delta > 0 else "cut" if delta < 0 else "hold"
        if abs(delta) > 200:
            # No BI meeting has moved more than 100 bps; a bigger jump means one
            # of the two levels was misparsed (or one release isn't a decision).
            _flag(d, "level_jump")
        elif not d.direction and "direction" not in hand:
            d.direction = implied
            _flag(d, "direction_from_levels")
            if "no_decision_parsed" in d.flags:
                d.flags.remove("no_decision_parsed")
        elif d.direction != implied:
            _flag(d, "direction_vs_levels")
        if abs(delta) <= 200 and d.direction == implied != "hold":
            if d.bps is None and "bps" not in hand:
                d.bps = float(abs(delta))
                _flag(d, "bps_from_levels")
            elif d.bps != abs(delta):
                _flag(d, "bps_vs_levels")

    by_code: dict[str, dict[str, "Document"]] = {}
    for d, _ in parsed:
        by_code.setdefault(d.code, {})[d.lang] = d
    hand_of = {(d.code, d.lang): hand for d, hand in parsed}
    for code, pair in by_code.items():
        if len(pair) != 2:
            continue
        a, b = pair["id"], pair["en"]
        for x, y in ((a, b), (b, a)):
            if x.bps is None and y.bps is not None and x.direction == y.direction \
                    and "bps" not in hand_of[(code, x.lang)]:
                x.bps = y.bps
                _flag(x, f"bps_from_{y.lang}")
        diff = [f for f in ("date", "direction", "bps", "rate_level")
                if getattr(a, f) != getattr(b, f)]
        if diff:
            for x in (a, b):
                _flag(x, "lang_mismatch:" + "+".join(diff))


def extract_decision(title: str, heading: str,
                      first_section: str) -> tuple[str, float | None, float | None]:
    """Direction, size in bps, and resulting level.

    Tries progressively wider text: title (most formulaic) -> heading alone (the
    bold decision sentence) -> heading+body (last resort). Searching heading+body
    directly, before trying the heading alone, previously let unrelated commentary
    decide the outcome: sp_141112 en's title has a site-side typo ("Mantained") so
    the title check fell through, and its body separately mentions "...going to
    raise interest rate of monetary operation instrument..." (a different
    instrument, not the policy decision) — with heading+body searched as one blob,
    the hike rule matched "raise" there before "hold" in the heading ever got a
    chance, since rules are tried in a fixed hike/cut/hold order rather than by
    where the match falls in the text. The heading is unambiguous on its own
    ("...decided to hold the BI rate steady at 5.75%"), so it's tried first now.
    """
    direction = ""
    for source in (title, heading, first_section):
        if source:
            direction = _nearest_direction(source)
        if direction:
            break
    if not direction:
        return "", None, None

    # bps/level are searched across ALL sources independently, not just the one that
    # matched direction. Confirmed necessary on sp_8706 (2006-02-07): direction only
    # matches in `heading` (title has no hold/hike/cut keyword at all), but the rate
    # figure ("BI Rate 12,75%") is only in `title` — heading's own sentence doesn't
    # mention a level. Confining the numeric search to the direction's source missed
    # a level that a wider source plainly had.
    bps = None
    level = None
    for source in (title, heading, first_section):
        if not source:
            continue
        if bps is None:
            bps_m = BPS_RE.search(source)
            if bps_m:
                bps = float(bps_m.group(1))
        if level is None:
            lvl_m = LEVEL_AFTER_RE.search(source) or LEVEL_ANY_RE.search(source)
            if lvl_m:
                level = to_float(lvl_m.group(1))
        if bps is not None and level is not None:
            break
    # A hold is 0 bps by definition. Any "N bps" in a hold release belongs to another
    # instrument — sp_186716's "lowering the Lending Facility 100 bps" and sp_133011's
    # corridor "Widened to 150 Bps" both previously came out as hold/100 and hold/150.
    if direction == "hold":
        bps = 0.0
    return direction, bps, level


def _nearest_direction(source: str) -> str:
    """The direction word closest to a policy-rate mention in `source`, or, if the
    source never names the policy rate, the earliest direction word in it."""
    hits = [(m.start(), m.end(), name)
            for name, rx in DIRECTION_RULES for m in rx.finditer(source)]
    if not hits:
        return ""
    anchors = [(m.start(), m.end()) for m in POLICY_RATE_RE.finditer(source)]
    if not anchors:
        return min(hits)[2]

    def gap(hit):
        s, e, _ = hit
        return min(max(a_s - e, s - a_e, 0) for a_s, a_e in anchors), s
    return min(hits, key=gap)[2]


def _paragraph_blocks(root):
    """Content blocks in document order: <p> tags, plus leaf <div>s that hold text
    directly.

    Some releases are authored with div-wrapped paragraphs instead of <p> — the
    same release can differ by language (confirmed on sp_281326: the English page
    uses <p>, the Indonesian page uses <div>, for identical content) or use <div>
    in both (sp_223020). A <p>-only selector silently produces zero sections on
    these despite the body text parsing fine, since full_text comes from
    root.get_text() regardless of tag type. A <div> only counts as a block here if
    it has no nested <p>/<div> — that excludes structural wrapper divs (e.g.
    ms-rtestate-field) while keeping the leaf divs that are standing in for <p>.
    """
    blocks = []
    for el in root.find_all(["p", "div"]):
        if el.name == "div" and el.find(["p", "div"]):
            continue
        blocks.append(el)
    return blocks


def _fallback_paragraph_sections(root) -> list["Section"]:
    """Section split for releases with no bold lead-in at all.

    Confirmed on 2005-2011 "Pernyataan Gubernur Bank Indonesia" (Governor's
    Statement) era releases, e.g. sp_091207 (2007-03-06): real <p> paragraph
    boundaries exist and the content is genuine, but there is no <strong>/<b>
    lead-in anywhere in the document — the topic-segmented, bold-headed format
    is a later stylistic convention, not present this early. Only called when
    the normal bold-lead-in pass finds zero sections, so it never overrides
    real structure; each paragraph's first sentence stands in for the heading
    (mirroring the bold lead-in's role), same as the main format's semantics.
    """
    sections: list[Section] = []
    for p in _paragraph_blocks(root):
        ptext = re.sub(r"\s+", " ", p.get_text(" ", strip=True)).strip()
        if len(ptext) < 30:  # boilerplate lines (datelines, signatures) are short
            continue
        m = re.search(r"(?<=[.!?])\s+", ptext)
        if m:
            heading, body = ptext[:m.start()].strip(), ptext[m.end():].strip()
        else:
            heading, body = ptext, ""
        order = len(sections) + 1
        sections.append(Section(order=order, heading=heading, body=body,
                                 topic=tag_topic(heading, order)))
    return sections


def _code_year(code: str) -> int | None:
    """Release year from a numeric sp_ code's last two digits (sp_185616 -> 2016,
    sp_77105 -> 2005). None for slug codes like 'bi_rate_090805-1'."""
    return 2000 + int(code[-2:]) if code.isdigit() and len(code) >= 4 else None


def parse_document(html: str, url: str = "") -> Document:
    soup = BeautifulSoup(html, "lxml")

    for junk in soup.find_all(["script", "style", "nav", "footer", "header"]):
        junk.decompose()

    doc = Document(url=url)
    parts = _url_lang_code(url)
    if parts:
        doc.lang, doc.code = parts

    doc.title = extract_title(soup, url)
    root = _content_root(soup)
    full_text = root.get_text(" ", strip=True)

    doc.date = extract_date(soup, soup.get_text(" ", strip=True)[:4000], _code_year(doc.code))
    ref = REF_RE.search(soup.get_text(" ", strip=True))
    if ref:
        doc.ref_no = f"{ref.group(1)}/{ref.group(2)}/{ref.group(3)}"

    doc.is_rdg = bool(RDG_TITLE_RE.search(doc.title or "")) or bool(
        RDG_TITLE_RE.search(full_text[:600])
    )

    # Sections: a bold lead-in opens a section; the rest of that paragraph and any
    # following non-bold paragraphs are its body.
    sections: list[Section] = []
    current: Section | None = None
    for p in _paragraph_blocks(root):
        # get_text(" ", strip=True) only joins BETWEEN text nodes; a literal line
        # break inside a single source text node (seen in the wild, e.g. a <sup>
        # ordinal split across lines: "20\n<sup>th</sup>-21\n<sup>st</sup>") survives
        # as-is and would otherwise fragment heading_key() grouping in `topics`.
        ptext = re.sub(r"\s+", " ", p.get_text(" ", strip=True)).strip()
        if not ptext:
            continue
        heading = re.sub(r"\s+", " ", _leading_bold(p)).strip()
        if heading and len(heading) > 15:
            # Strip the heading off the front of the paragraph to get the body. Slice by
            # prefix match where possible; whitespace normalisation can make the lengths
            # disagree, so fall back to a single replace rather than cutting blindly.
            if ptext.startswith(heading):
                body = ptext[len(heading):]
            else:
                body = ptext.replace(heading, "", 1)
            body = body.strip(" .:-–—")
            order = len(sections) + 1
            current = Section(order=order, heading=heading,
                              body=body, topic=tag_topic(heading, order))
            sections.append(current)
        elif current is not None:
            current.body = (current.body + " " + ptext).strip()
        # paragraphs before the first heading (dateline, boilerplate) are dropped

    total_words = len(full_text.split())
    captured_words = sum(len(s.heading.split()) + len(s.body.split()) for s in sections)
    if not sections or (total_words > 0 and captured_words / total_words < 0.5):
        # A late, isolated bold paragraph (e.g. a bolded signature name at the very
        # end) makes the loop above "succeed" with one section while silently
        # dropping everything before it as pre-heading boilerplate — confirmed on
        # sp_8106 (2006-01-09): the only bold lead-in was "Rizal A. Djaafara" (the
        # signing official's name), so the two real content paragraphs before it
        # were dropped entirely (164 of 164 words *should* have been 164, but only
        # ~4 were captured). Checking `not sections` alone missed this: sections
        # was non-empty (1), just capturing almost none of the real content.
        sections = _fallback_paragraph_sections(root)

    # A bolded reference line ("No. 13 / 01/ PSHM / Humas") with no body is not a
    # section. Confirmed on sp_130111 (2011-01-05): it became section 1, so the
    # decision paragraph was section 2 and extract_decision() never saw it.
    sections = [s for s in sections
                if not (REF_RE.fullmatch(s.heading.split("/ Humas")[0].strip(" /"))
                        or (REF_RE.match(s.heading) and len(s.heading) < 40 and not s.body))]
    for i, s in enumerate(sections, 1):
        if s.order != i:
            s.order = i
            s.topic = tag_topic(s.heading, i)

    doc.sections = sections
    doc.n_sections = len(sections)
    doc.n_words = total_words

    heading0 = sections[0].heading if sections else ""
    first_sec = heading0 + " " + sections[0].body if sections else ""
    doc.direction, doc.bps, doc.rate_level = extract_decision(doc.title, heading0, first_sec)

    if not doc.date:
        doc.flags.append("no_date")
    if not doc.title:
        doc.flags.append("no_title")
    if doc.n_sections < 4:
        doc.flags.append("few_sections")
    if doc.n_words < 250:
        doc.flags.append("short_body")
    if doc.is_rdg and not doc.direction:
        doc.flags.append("no_decision_parsed")
    if doc.is_rdg and doc.rate_level is None:
        doc.flags.append("no_rate_level")
    return doc


# --------------------------------------------------------------------------------------
# Prefix probing and discovery
# --------------------------------------------------------------------------------------


YEAR_MAP: dict[int, tuple[str, int]] = {}   # year -> (path name, prefix)


def load_year_map() -> None:
    for row in _read_csv(OUT_DIR / "year_map.csv"):
        try:
            YEAR_MAP[int(row["year"])] = (row["path"], int(row["prefix"]))
        except (KeyError, ValueError):
            continue


def save_year_map() -> None:
    rows = [{"year": y, "path": p, "prefix": pre}
            for y, (p, pre) in sorted(YEAR_MAP.items())]
    _write_csv(OUT_DIR / "year_map.csv", rows)


def probe_year(year: int, session: requests.Session, delay: float,
               verbose: bool = True) -> tuple[str, int] | None:
    """Work out which path template and prefix a given year uses.

    Searches a grid of (path, prefix, sequence). BI changed its URL layout more than
    once, so the path is discovered rather than assumed; the prefix follows year - 1998
    in every verified case, but nearby values are tried in case that breaks somewhere.
    A hit counts only if the retrieved page's own date falls in the target year.
    """
    if year in YEAR_MAP:
        return YEAR_MAP[year]

    guess = KNOWN_ANCHORS.get(year, year - 1998)
    prefixes = [guess, guess - 1, guess + 1, guess - 2, guess + 2]
    path_order = [n for n, _ in PATH_TEMPLATES]
    # Newer years are far more likely to sit on the newer path, and vice versa.
    if year <= 2020:
        path_order.reverse()

    for path in path_order:
        for prefix in prefixes:
            if not 1 <= prefix <= 60:
                continue
            for seq in PROBE_SEQS:
                url = release_url(prefix, seq, year, "id", path)
                html = get_html(url, session, delay, quiet=True)
                if not html:
                    continue
                doc = parse_document(html, url)
                if doc.date.startswith(str(year)):
                    if verbose:
                        log(f"  {year}: path={path} prefix={prefix} "
                            f"(seq {seq} -> {doc.date})")
                    YEAR_MAP[year] = (path, prefix)
                    save_year_map()
                    return path, prefix
                if verbose:
                    log(f"  {year}: {url.rsplit('/', 1)[-1]} resolved but dated "
                        f"{doc.date or '?'} — not this year, continuing")
    if verbose:
        log(f"  {year}: nothing found (tried paths {path_order}, prefixes {prefixes})")
    return None


def discover(start_year: int, end_year: int, max_seq: int, delay: float,
             rdg_only: bool = True) -> list[dict]:
    session = make_session()
    rows: list[dict] = []
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    load_year_map()
    for year in range(start_year, end_year + 1):
        log(f"\n=== {year} ===")
        found = probe_year(year, session, delay)
        if found is None:
            continue
        path, prefix = found
        misses = 0
        for seq in range(1, max_seq + 1):
            url = release_url(prefix, seq, year, "id", path)
            html = get_html(url, session, delay, quiet=True)
            if not html:
                misses += 1
                # Tolerance must be generous: BI's sp_ numbering is SPARSE in the older
                # years, so long runs of unused sequence numbers are normal, not the end
                # of the year. A tolerance of 25 silently truncated 2013 at seq 9 (1 RDG
                # release instead of 12) and 2017 at seq ~25 (0 instead of 12). Rescanning
                # is cheap because already-fetched pages come from the cache.
                if misses >= MISS_TOLERANCE:
                    log(f"  {year}: {MISS_TOLERANCE} consecutive misses after seq {seq}, stopping")
                    break
                continue
            misses = 0
            doc = parse_document(html, url)
            keep = doc.is_rdg or not rdg_only
            if keep:
                rows.append({
                    "year": year, "path": path, "prefix": prefix, "seq": seq,
                    "code": sp_code(prefix, seq, year),
                    "url_id": url,
                    "url_en": release_url(prefix, seq, year, "en", path),
                    "date": doc.date, "title": doc.title,
                    "is_rdg": doc.is_rdg, "ref_no": doc.ref_no,
                })
                log(f"  [{len(rows):3d}] {doc.date}  {doc.title[:78]}")

    path = OUT_DIR / "urls.csv"
    _write_csv(path, rows)
    log(f"\nSaved {len(rows)} releases -> {path}")
    return rows


def _write_csv(path: Path, rows: list[dict]) -> None:
    import csv
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def _read_csv(path: Path) -> list[dict]:
    import csv
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


# --------------------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------------------


def cmd_fetch(args) -> None:
    rows = _read_csv(OUT_DIR / "urls.csv")
    if not rows:
        log("No out/urls.csv — run `discover` (or `pilot`) first.")
        return
    session = make_session()
    langs = ["id", "en"] if args.both else [args.lang]
    total = len(rows) * len(langs)
    got = 0
    for i, row in enumerate(rows, 1):
        for lang in langs:
            url = row["url_en"] if lang == "en" else row["url_id"]
            html = get_html(url, session, args.delay, force=args.force, quiet=True)
            got += bool(html)
        if i % 10 == 0 or i == len(rows):
            log(f"  {i}/{len(rows)} releases, {got}/{total} pages cached")
    log(f"\nDone. Raw HTML in {RAW_DIR}")


def cmd_parse(args) -> None:
    load_topic_overrides()
    load_review_notes()
    all_files = [p for p in RAW_DIR.rglob("sp_*.html") if "_not_found" not in p.parts]
    if not all_files:
        log(f"No cached HTML in {RAW_DIR} — run `fetch` first.")
        return

    # The legacy pre-nesting cache layout (raw/<lang>/sp_<code>.html, written before
    # cache_path() started keying on path template too — see its docstring) can still
    # be on disk alongside the current nested layout (raw/<lang>/<path>/sp_<code>.html)
    # for the same (lang, code): a page fetched once before that change and again
    # after it leaves both files behind. Parsing both silently double-counts that
    # release's sections — confirmed on sp_185016/id, where every section appeared
    # twice in corpus.csv (264 duplicate rows across 15 code/lang pairs total).
    # Only the flat legacy file is dropped here; two NESTED files for the same code
    # are kept as-is, since different path templates can be genuinely distinct
    # releases sharing a recycled code (see cache_path()'s docstring).
    by_lang_code: dict[tuple[str, str], list[Path]] = {}
    for path in all_files:
        lang = path.parent.name if path.parent.name in ("id", "en") else path.parent.parent.name
        code = path.stem.replace("sp_", "")
        by_lang_code.setdefault((lang, code), []).append(path)

    files: list[Path] = []
    skipped_legacy = 0
    for (lang, code), paths in by_lang_code.items():
        nested = [p for p in paths if p.parent.name != lang]
        if nested and len(paths) > len(nested):
            skipped_legacy += len(paths) - len(nested)
            paths = nested
        files.extend(paths)
    files.sort()

    if skipped_legacy:
        log(f"Skipping {skipped_legacy} legacy pre-nesting cache file(s) "
            f"superseded by a current nested copy")

    excluded = 0
    parsed: list[tuple[Document, set[str]]] = []
    docs_rows, corpus_rows = [], []
    for path in files:
        # cache_path() nests one level deeper than it used to (raw/<lang>/<path
        # template>/sp_x.html, not raw/<lang>/sp_x.html — see its docstring for
        # why). Cached files fetched before that change are still on disk in the
        # old, flatter layout, so accept either rather than silently mislabelling
        # old files (they'd otherwise read lang="raw" from the fixed offset).
        lang = path.parent.name if path.parent.name in ("id", "en") else path.parent.parent.name
        code = path.stem.replace("sp_", "")
        sidecar = path.with_suffix(".url")
        url = (sidecar.read_text(encoding="utf-8").strip() if sidecar.exists()
               else URL_TEMPLATE.format(lang=lang, code=code))
        doc = parse_document(path.read_text(encoding="utf-8", errors="replace"), url)
        notes = _review_notes_for(code, lang)
        if any(n["action"] == "exclude" for n in notes):
            excluded += 1
            continue
        doc.code, doc.lang = code, lang
        for note in notes:
            _apply_review_note(doc, note)
        # After the notes, so a hand-confirmed is_rdg=True keeps a decision whose
        # title the RDG filter misses (sp_78705, the 30 Aug 2005 +75 bps package).
        if args.rdg_only and not doc.is_rdg:
            continue
        parsed.append((doc, {n["field"] for n in notes if n["action"] == "fix_field"}))

    reconcile_decisions(parsed)

    for doc, _ in parsed:
        code, lang, url = doc.code, doc.lang, doc.url
        docs_rows.append({
            "code": code, "lang": lang, "date": doc.date, "title": doc.title,
            "ref_no": doc.ref_no, "is_rdg": doc.is_rdg, "direction": doc.direction,
            "bps": doc.bps, "rate_level": doc.rate_level,
            "n_sections": doc.n_sections, "n_words": doc.n_words,
            "flags": "|".join(doc.flags), "url": url,
        })
        for s in doc.sections:
            corpus_rows.append({
                "code": code, "lang": lang, "date": doc.date,
                "direction": doc.direction, "rate_level": doc.rate_level,
                "section_order": s.order, "topic": s.topic,
                "heading_key": heading_key(s.heading),
                "heading": s.heading, "text": s.body,
                "n_words": len(s.body.split()),
            })

    docs_rows.sort(key=lambda r: (r["date"], r["lang"]))
    corpus_rows.sort(key=lambda r: (r["date"], r["lang"], r["section_order"]))
    _write_csv(OUT_DIR / "documents.csv", docs_rows)
    _write_csv(OUT_DIR / "corpus.csv", corpus_rows)

    flagged = sum(1 for r in docs_rows if r["flags"])
    log(f"\nParsed {len(docs_rows)} documents -> out/documents.csv")
    log(f"       {len(corpus_rows)} sections  -> out/corpus.csv")
    log(f"       {flagged} documents carry at least one flag "
        f"({flagged / max(len(docs_rows), 1):.0%}) — check these in `audit`")
    if excluded:
        log(f"       {excluded} documents excluded per review_notes.csv")


def cmd_audit(args) -> None:
    rows = _read_csv(OUT_DIR / "documents.csv")
    if not rows:
        log("No out/documents.csv — run `parse` first.")
        return
    corpus = _read_csv(OUT_DIR / "corpus.csv")
    by_code: dict[str, list[dict]] = {}
    for c in corpus:
        by_code.setdefault((c["code"], c["lang"]), []).append(c)

    flagged = [r for r in rows if r["flags"]]
    clean = [r for r in rows if not r["flags"]]
    random.seed(args.seed)
    sample = (flagged[:args.n // 2] +
              random.sample(clean, min(len(clean), args.n - len(flagged[:args.n // 2]))))

    lines: list[str] = []
    lines.append("=" * 78)
    lines.append("BI RDG CORPUS — PARSE AUDIT")
    lines.append("=" * 78)
    lines.append(f"documents parsed : {len(rows)}")
    lines.append(f"with flags       : {len(flagged)} ({len(flagged)/max(len(rows),1):.0%})")
    dates = sorted(r["date"] for r in rows if r["date"])
    if dates:
        lines.append(f"date range       : {dates[0]} .. {dates[-1]}")
    years = {}
    for r in rows:
        if r["date"]:
            years[r["date"][:4]] = years.get(r["date"][:4], 0) + 1
    if years:
        lines.append("per year         : " +
                     "  ".join(f"{y}:{n}" for y, n in sorted(years.items())))
    lines.append("")
    lines.append("CHECK EACH OF THESE BY OPENING THE URL AND COMPARING:")
    lines.append("  1. is the date right?")
    lines.append("  2. is the rate level right, and does the direction match?")
    lines.append("  3. do the section headings look like real headings (not sentence fragments)?")
    lines.append("  4. is the number of sections plausible (BI releases run ~10-13)?")
    lines.append("")

    for r in sample:
        secs = by_code.get((r["code"], r["lang"]), [])
        lines.append("-" * 78)
        lines.append(f"{r['date']}  [{r['lang']}]  sp_{r['code']}"
                     + (f"   FLAGS: {r['flags']}" if r["flags"] else ""))
        lines.append(f"  title    : {r['title'][:100]}")
        lines.append(f"  decision : {r['direction'] or '(none)':>5}  "
                     f"bps={r['bps'] or '-'}  level={r['rate_level'] or '-'}%  "
                     f"ref={r['ref_no'] or '-'}")
        lines.append(f"  sections : {r['n_sections']}   words: {r['n_words']}")
        lines.append(f"  url      : {r['url']}")
        for s in secs[:14]:
            lines.append(f"     {s['section_order']:>2}. [{s['topic']:<18}] "
                         f"{s['heading'][:88]}")
    lines.append("-" * 78)

    report = "\n".join(lines)
    print(report)
    (OUT_DIR / "audit_report.txt").write_text(report, encoding="utf-8")
    log(f"\nWritten to {OUT_DIR / 'audit_report.txt'}")


def cmd_probe(args) -> None:
    """Map each year to a working URL path + prefix. Answers: how far back can I go?

    This is the question that actually sizes your sample, so run it before anything else.
    Costs a few dozen requests per year and caches the answer in out/year_map.csv.
    """
    session = make_session()
    load_year_map()
    results: list[tuple[int, str]] = []
    n_years = args.end_year - args.start_year + 1
    log(f"Probing {n_years} years. A year that resolves is quick; a year that does not")
    log("takes up to ~2 minutes of silent requests before it gives up. Let it run.\n")
    for i, year in enumerate(range(args.start_year, args.end_year + 1), 1):
        log(f"[{i}/{n_years}] {year} ...")
        mapping = probe_year(year, session, args.delay, verbose=args.verbose)
        results.append((year, f"{mapping[0]}  prefix {mapping[1]}" if mapping else "—"))
        log(f"          -> {results[-1][1]}")

    ok = [y for y, r in results if r != "—"]
    log("\n" + "=" * 66)
    log("YEAR MAP")
    log("=" * 66)
    for year, r in results:
        log(f"  {year}   {r}")
    log("")
    if ok:
        log(f"Retrievable years: {min(ok)}–{max(ok)}  ({len(ok)} of {len(results)})")
        gaps = [y for y, r in results if r == "—" and min(ok) < y < max(ok)]
        if gaps:
            log(f"Gaps inside that range: {gaps} — these need a different path template.")
        if min(ok) > 2005:
            log("")
            log(f"NOTE FOR THE THESIS: the enumerable archive starts at {min(ok)}, not 2005.")
            log("Anything earlier is in BI's PDF archive under /id/archive/arsip-siaran-pers/")
            log("with descriptive filenames rather than sequential URLs, so it can't be")
            log("enumerated the same way. That is a real constraint on your sample and")
            log("your advisor should hear about it before you design around 2005.")
    else:
        log("No year resolved. The URL scheme has changed — send me a working release URL.")
    log(f"\nSaved -> {OUT_DIR / 'year_map.csv'}")


def cmd_topics(args) -> None:
    """Frequency table of distinct heading templates, so you can hand-map topics once.

    Writes out/topic_review.csv. Edit the `topic` column, save it as topic_overrides.csv
    in this folder, and re-run `parse` — your labels then beat the keyword guesses.
    """
    corpus = _read_csv(OUT_DIR / "corpus.csv")
    if not corpus:
        log("No out/corpus.csv — run `parse` first.")
        return
    groups: dict[tuple[str, str], dict] = {}
    for row in corpus:
        key = (row["lang"], row.get("heading_key", heading_key(row["heading"])))
        g = groups.setdefault(key, {"lang": key[0], "heading_key": key[1], "n": 0,
                                    "topic": row["topic"], "example": row["heading"][:110],
                                    "orders": set()})
        g["n"] += 1
        g["orders"].add(int(row["section_order"]))

    rows = sorted(groups.values(), key=lambda g: -g["n"])
    for g in rows:
        g["typical_order"] = round(sum(g["orders"]) / len(g["orders"]), 1)
        del g["orders"]

    _write_csv(OUT_DIR / "topic_review.csv", rows)

    log(f"{len(rows)} distinct heading templates across {len(corpus)} sections\n")
    log(f"{'n':>4}  {'lang':<5} {'auto-topic':<19} heading template")
    log("-" * 96)
    for g in rows[:args.n]:
        log(f"{g['n']:>4}  {g['lang']:<5} {g['topic']:<19} {g['example'][:64]}")
    log("-" * 96)
    log(f"\nFull table -> {OUT_DIR / 'topic_review.csv'}")
    log("If the auto-topics look wrong, fix the `topic` column, save the file as")
    log("topic_overrides.csv next to bi_rdg.py, and re-run `parse`.")
    if len(rows) < 60:
        log(f"\nOnly {len(rows)} templates — hand-mapping these is an hour's work and")
        log("gives you topic labels far more reliable than any keyword rule.")


def cmd_pilot(args) -> None:
    """The go/no-go test. Grab a few RDG releases from each of four eras and parse them."""
    session = make_session()
    years = args.years or [2005, 2013, 2020, 2026]
    rows: list[dict] = []

    load_year_map()
    log("PILOT — probing four eras of BI's website to see whether the archive is usable.\n")
    for year in years:
        log(f"=== {year} ===")
        mapping = probe_year(year, session, args.delay)
        if mapping is None:
            log(f"  {year}: SKIPPED — no working URL scheme found\n")
            continue
        path, prefix = mapping
        found = 0
        # Start mid-year: sequence 1 frequently does not resolve, and BI publishes
        # a few hundred releases a year, so RDG meetings are scattered throughout.
        for seq in range(1, args.scan + 1):
            url = release_url(prefix, seq, year, "id", path)
            html = get_html(url, session, args.delay, quiet=True)
            if not html:
                continue
            doc = parse_document(html, url)
            if not doc.is_rdg:
                continue
            rows.append({
                "year": year, "path": path, "prefix": prefix, "seq": seq,
                "code": sp_code(prefix, seq, year),
                "url_id": url, "url_en": release_url(prefix, seq, year, "en", path),
                "date": doc.date, "title": doc.title,
                "is_rdg": True, "ref_no": doc.ref_no,
            })
            found += 1
            log(f"  found: {doc.date}  {doc.title[:70]}")
            if found >= args.per_year:
                break
        if found == 0:
            log(f"  {year}: scanned {args.scan} sequence numbers on '{path}', "
                f"no RDG release found (other release types may still exist)")
        log("")

    if not rows:
        log("PILOT FAILED: no RDG releases found at all.")
        log("This does NOT necessarily kill the thesis — it means the sp_ URL scheme")
        log("is wrong for these years. Next step: open the listing page in a browser,")
        log("copy one release URL, and adjust URL_TEMPLATE / probe_prefix accordingly.")
        return

    _write_csv(OUT_DIR / "urls.csv", rows)
    log(f"Saved {len(rows)} pilot URLs -> out/urls.csv")

    log("\nFetching English versions for the bilingual check...")
    for r in rows:
        get_html(r["url_en"], session, args.delay, quiet=True)

    log("\nParsing...")
    cmd_parse(argparse.Namespace(rdg_only=True))
    log("\nAuditing...")
    cmd_audit(argparse.Namespace(n=len(rows) * 2, seed=0))

    log("\n" + "=" * 78)
    log("GO / NO-GO")
    log("=" * 78)
    log("GO if: dates are right, rate levels match the headline, and section headings")
    log("       look like headings in every era you tested.")
    log("FIX  if: one era parses badly -> the template changed that year; add a case.")
    log("STOP if: the early years have no retrievable releases at all -> your sample")
    log("       starts later than 2005, and the pre-2020 baseline gets thin. Tell your")
    log("       advisor before you build anything else.")


def cmd_selftest(args) -> None:
    """Parse a bundled fixture that mimics BI's structure. Runs offline."""
    fixture = ROOT / "tests" / "fixture_release.html"
    if not fixture.exists():
        log(f"Fixture missing: {fixture}")
        sys.exit(1)
    doc = parse_document(
        fixture.read_text(encoding="utf-8"),
        "https://www.bi.go.id/id/publikasi/ruang-media/news-release/Pages/sp_2810726.aspx",
    )
    checks = [
        ("language detected",     doc.lang == "id",                        doc.lang),
        ("code detected",         doc.code == "2810726",                   doc.code),
        ("date parsed",           doc.date == "2026-05-20",                doc.date),
        ("title parsed",          "BI-Rate" in doc.title,                  doc.title[:50]),
        ("reference number",      doc.ref_no == "28/107/DKom",             doc.ref_no),
        ("identified as RDG",     doc.is_rdg is True,                      doc.is_rdg),
        ("direction = hike",      doc.direction == "hike",                 doc.direction),
        ("size = 50 bps",         doc.bps == 50.0,                         doc.bps),
        ("level = 5.25",          doc.rate_level == 5.25,                  doc.rate_level),
        ("sections found",        doc.n_sections >= 5,                     doc.n_sections),
        ("inflation tagged",      any(s.topic == "inflation" for s in doc.sections), ""),
        ("fx tagged",             any(s.topic == "exchange_rate" for s in doc.sections), ""),
        ("body text captured",    all(len(s.body) > 20 for s in doc.sections), ""),
    ]
    log("SELFTEST — parser logic, no network required\n")
    ok = True
    for name, passed, got in checks:
        log(f"  [{'PASS' if passed else 'FAIL'}] {name:<22} {got}")
        ok &= bool(passed)
    log("")
    for s in doc.sections:
        log(f"   {s.order:>2}. [{s.topic:<18}] {s.heading[:70]}")
    log("")
    if ok:
        log("All checks passed. The parser works. Now run:  python bi_rdg.py pilot")
    else:
        log("Some checks failed — tell me which ones and I'll fix the parser.")
        sys.exit(1)


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Build a corpus of Bank Indonesia RDG press releases.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Start with:  python bi_rdg.py selftest",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("selftest", help="parse the bundled fixture (offline)")
    p.set_defaults(func=cmd_selftest)

    p = sub.add_parser("pilot", help="go/no-go feasibility test on ~4 eras")
    p.add_argument("--years", type=int, nargs="*", default=None)
    p.add_argument("--per-year", type=int, default=3, help="RDG releases per year")
    p.add_argument("--scan", type=int, default=40, help="sequence numbers to scan")
    p.add_argument("--delay", type=float, default=DEFAULT_DELAY)
    p.set_defaults(func=cmd_pilot)

    p = sub.add_parser("probe", help="map years -> URL path + prefix (run this first)")
    p.add_argument("--start-year", type=int, default=2005)
    p.add_argument("--end-year", type=int, default=2026)
    p.add_argument("--delay", type=float, default=DEFAULT_DELAY)
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("discover", help="scan all years, save RDG URLs")
    p.add_argument("--start-year", type=int, default=2005)
    p.add_argument("--end-year", type=int, default=2026)
    p.add_argument("--max-seq", type=int, default=260)
    p.add_argument("--delay", type=float, default=DEFAULT_DELAY)
    p.add_argument("--all", dest="rdg_only", action="store_false",
                   help="keep every release, not just RDG")
    p.set_defaults(func=lambda a: discover(a.start_year, a.end_year, a.max_seq,
                                           a.delay, a.rdg_only))

    p = sub.add_parser("fetch", help="download raw HTML for urls.csv")
    p.add_argument("--lang", default="id", choices=["id", "en"])
    p.add_argument("--both", action="store_true", default=True,
                   help="fetch both languages (default)")
    p.add_argument("--delay", type=float, default=DEFAULT_DELAY)
    p.add_argument("--force", action="store_true", help="re-download even if cached")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("parse", help="raw HTML -> documents.csv + corpus.csv")
    p.add_argument("--all", dest="rdg_only", action="store_false")
    p.set_defaults(func=cmd_parse, rdg_only=True)

    p = sub.add_parser("audit", help="human-readable parse report")
    p.add_argument("--n", type=int, default=20)
    p.add_argument("--seed", type=int, default=0)
    p.set_defaults(func=cmd_audit)

    p = sub.add_parser("topics", help="heading templates + topic labels to review by hand")
    p.add_argument("--n", type=int, default=40, help="rows to print")
    p.set_defaults(func=cmd_topics)

    args = ap.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    args.func(args)


if __name__ == "__main__":
    main()
