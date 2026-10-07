#!/usr/bin/env python3
"""
extract_annual_reports.py — pull the monetary-policy chapter out of BI's annual
"Laporan Perekonomian Indonesia" reports for 2005-2011, as supplementary
qualitative coverage for years the sp_-numbered RDG press release archive
doesn't reach (confirmed: enumerable RDG releases start 2012; the old PDF press
release archive has only 3 unrelated documents from Dec 2004; see README).

This is NOT part of the main per-meeting corpus (documents.csv/corpus.csv) and
is deliberately kept separate: these are single yearly narrative chapters, not
one-row-per-RDG-meeting records, so they don't share that schema. Use them for
qualitative cross-checking / narrative context for 2005-2011, not as a
like-for-like substitute for the per-meeting tone index.

Each year's monetary-policy chapter lives in a different place in the source
PDF, under a different chapter number/title, and some chapter "cover" pages are
image-only (no extractable text) — see CHAPTERS below for what was manually
verified for each year on 2026-09-08. 2009's report page turned out to sit at a
different URL slug (lpi_09.aspx, not lpi_2009.aspx) — found by the user, not
discoverable from the pattern the other years used.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

import pypdf

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "out"
SUPPLEMENT_DIR = ROOT / "pre2012_annual"

# (year, pdf_path, first_page, last_page_exclusive, chapter_label, source_url)
# Page ranges are 0-indexed PDF page numbers, verified by hand against each
# report's own table of contents and chapter-break pages.
CHAPTERS = [
    (2005, "/tmp/lpi-2005.pdf", 119, 142,
     "Bab 5: Perkembangan Moneter",
     "https://www.bi.go.id/id/publikasi/laporan/Pages/lpi_2005.aspx"),
    (2006, "/tmp/lpi_reports/extracted/2006/Bab6.pdf", 0, None,
     "Bab 6: Perkembangan Moneter",
     "https://www.bi.go.id/id/publikasi/laporan/Pages/lpi_2006.aspx"),
    (2007, "/tmp/lpi_reports/extracted/2007/BI LPI 2007.pdf", 102, 126,
     "Bab 6: Perkembangan Moneter",
     "https://www.bi.go.id/id/publikasi/laporan/Pages/lpi_2007.aspx"),
    (2008, "/tmp/lpi_reports/extracted/2008/07_bab_V.pdf", 0, None,
     "Bab V: Respons Kebijakan Bank Indonesia "
     "(Kebijakan Moneter | Perbankan | Sistem Pembayaran | Koordinasi)",
     "https://www.bi.go.id/id/publikasi/laporan/Pages/lpi_2008.aspx"),
    (2009, "/tmp/lpi_reports/2009_babIII.pdf", 1, None,
     "Bab III: Respons Kebijakan Moneter di Tengah Krisis Global",
     "https://www.bi.go.id/id/publikasi/laporan/Pages/lpi_09.aspx"),
    (2010, "/tmp/lpi_reports/extracted/2010/06_bab4.pdf", 0, None,
     "Bab IV: Bauran Kebijakan Bank Indonesia di Tengah "
     "Derasnya Aliran Masuk Modal Asing",
     "https://www.bi.go.id/id/publikasi/laporan/Pages/lpi_2010.aspx"),
    (2011, "/tmp/lpi_reports/extracted/2011_p3/Bagian 3.pdf", 4, 28,
     "Bab VII: Kebijakan Moneter dan Makroprudensial",
     "https://www.bi.go.id/id/publikasi/laporan/Pages/lpi_2011.aspx"),
]


def extract_chapter(pdf_path: str, first: int, last: int | None) -> str:
    reader = pypdf.PdfReader(pdf_path)
    end = last if last is not None else len(reader.pages)
    parts = []
    for i in range(first, min(end, len(reader.pages))):
        t = reader.pages[i].extract_text() or ""
        parts.append(t)
    text = "\n".join(parts)
    # Collapse the running header/footer noise ("Laporan Perekonomian Indonesia
    # 2011 * BAB VII110") and excess whitespace without touching real content.
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def main() -> None:
    SUPPLEMENT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    rows = []
    for year, pdf_path, first, last, label, url in CHAPTERS:
        if not Path(pdf_path).exists():
            print(f"  {year}: MISSING source file {pdf_path} — skipped")
            continue
        text = extract_chapter(pdf_path, first, last)
        out_path = SUPPLEMENT_DIR / f"{year}.txt"
        out_path.write_text(text, encoding="utf-8")
        n_words = len(text.split())
        rows.append({
            "year": year, "chapter_label": label, "source_report_url": url,
            "n_words": n_words, "text_file": str(out_path.relative_to(ROOT)),
        })
        print(f"  {year}: {n_words:>6} words -> {out_path.relative_to(ROOT)}")

    csv_path = OUT_DIR / "pre2012_annual_reports.csv"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"\nWrote {len(rows)} chapters -> {csv_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
