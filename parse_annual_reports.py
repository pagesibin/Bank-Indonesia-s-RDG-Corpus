#!/usr/bin/env python3
"""
parse_annual_reports.py — turn the raw pre2012_annual/{year}.txt chapter
extracts (see extract_annual_reports.py) into two usable forms:

  1. pre2012_annual/{year}_clean.txt — running headers/footers and stray page
     numbers stripped, hard line-wraps reflowed into flowing prose.
  2. out/pre2012_annual_corpus.csv — the cleaned text split into ~5-sentence
     chunks and topic-tagged with the SAME keyword scorer bi_rdg.py uses for
     the main corpus (tag_topic), for tooling compatibility.

IMPORTANT CAVEAT, not a hedge: unlike the main corpus.csv, whose section
breaks are the release's own bold-led paragraphs (the author's real structure),
these annual-report PDFs carry no recoverable paragraph markers in extracted
text — checked directly: one sample chapter had 2 real paragraph breaks across
2052 lines, i.e. essentially every line break is a mid-sentence column wrap,
not intentional. So the chunk boundaries here are algorithmic (every ~5
sentences), and the topic label per chunk is a looser signal than in the main
corpus — treat this as narrative supplementary material, not comparable
per-meeting granularity.
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import bi_rdg  # noqa: E402  (reuse tag_topic / TOPIC_KEYWORDS)

SUPPLEMENT_DIR = ROOT / "pre2012_annual"
OUT_DIR = ROOT / "out"

# A running header/footer line: short, and carries a BAB/Bagian marker glued to
# a page number or separated by | or • — as opposed to the chapter's real
# title block, which is multi-line and has none of these separators.
NOISE_LINE_RE = re.compile(
    r"^\s*\d+\s*$"                                    # bare page number
    r"|.*\bBAB\s+[IVXLCDM]+\b.*[|•].*"                # "... BAB III | ..." / "... • BAB VII..."
    r"|.*[|•].*\bBAB\s+[IVXLCDM]+\b.*"                # reversed order
    r"|^\s*\d+\s+BAB\s+[IVXLCDM]+.*"                  # "82 BAB III | ..."
    r"|^Laporan Perekonomian Indonesia \d{4}.*",       # 2011-style running header
    re.IGNORECASE,
)

SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")
CHUNK_SIZE = 5  # sentences per synthetic chunk


# PDF ligature glyphs some of these reports use, which pypdf sometimes extracts
# with a stray trailing space baked in (e.g. "inﬂ asi" for "inflasi").
LIGATURE_MAP = {"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl"}


def _fix_ligatures(s: str) -> str:
    for lig, expansion in LIGATURE_MAP.items():
        s = s.replace(lig + " ", expansion).replace(lig, expansion)
    return s


def clean_text(raw: str) -> str:
    raw = _fix_ligatures(raw)
    lines = raw.split("\n")
    kept = []
    prev = None
    for line in lines:
        s = line.strip()
        if not s:
            continue
        if NOISE_LINE_RE.match(s):
            continue
        if s == prev:  # collapse the chapter title appearing twice in a row
            continue
        kept.append(s)
        prev = s
    # Reflow hard line-wraps into flowing prose: join with a space unless the
    # previous line already ended a sentence (then keep a paragraph-ish break).
    out = []
    for s in kept:
        if out and not re.search(r"[.!?:]$", out[-1]):
            out[-1] = out[-1] + " " + s
        else:
            out.append(s)
    return "\n\n".join(out)


def chunk_and_tag(clean: str, year: int, source_url: str) -> list[dict]:
    flat = re.sub(r"\s+", " ", clean).strip()
    sentences = SENTENCE_SPLIT_RE.split(flat)
    rows = []
    for i in range(0, len(sentences), CHUNK_SIZE):
        group = sentences[i:i + CHUNK_SIZE]
        text = " ".join(group).strip()
        if not text:
            continue
        topic = bi_rdg.tag_topic(text)
        rows.append({
            "year": year, "chunk_order": len(rows) + 1, "topic": topic,
            "n_words": len(text.split()), "text": text,
            "source_report_url": source_url,
        })
    return rows


def main() -> None:
    meta_path = OUT_DIR / "pre2012_annual_reports.csv"
    meta = list(csv.DictReader(meta_path.open(encoding="utf-8-sig")))

    all_rows = []
    for row in meta:
        year = int(row["year"])
        raw_path = ROOT / row["text_file"]
        raw = raw_path.read_text(encoding="utf-8")
        cleaned = clean_text(raw)

        clean_path = SUPPLEMENT_DIR / f"{year}_clean.txt"
        clean_path.write_text(cleaned, encoding="utf-8")

        chunks = chunk_and_tag(cleaned, year, row["source_report_url"])
        all_rows.extend(chunks)

        topic_counts = {}
        for c in chunks:
            topic_counts[c["topic"]] = topic_counts.get(c["topic"], 0) + 1
        print(f"  {year}: {len(cleaned.split()):>6} words cleaned, "
              f"{len(chunks):>3} chunks -> {clean_path.relative_to(ROOT)}  "
              f"topics: {topic_counts}")

    corpus_path = OUT_DIR / "pre2012_annual_corpus.csv"
    with corpus_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()))
        w.writeheader()
        w.writerows(all_rows)
    print(f"\nWrote {len(all_rows)} topic-tagged chunks -> {corpus_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
