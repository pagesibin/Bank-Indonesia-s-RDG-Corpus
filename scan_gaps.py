"""Extend discover's scan past --max-seq 260 for years whose sp_ numbering runs higher.

BI issued more than 260 press releases a year from 2021 on, so `discover`'s default
cap cut off every Oct-Dec RDG release in 2022 and 2023 (confirmed: Oct 2022 is
sp_2428522 = seq 285, Dec 2022 sp_2435022 = seq 350, Oct 2023 sp_2528323 = seq 283).

Scans seq START..END for each year, stops after MISS_STOP consecutive misses, and
appends every RDG release found to out/urls.csv (deduped on code) so the normal
`fetch` -> `parse` pipeline picks them up. Uses bi_rdg's own cached, rate-limited
fetcher, so re-running is cheap.

    python scan_gaps.py 2021 2026 --start 261 --end 450
"""
import argparse

import bi_rdg as B

ap = argparse.ArgumentParser()
ap.add_argument("start_year", type=int)
ap.add_argument("end_year", type=int)
ap.add_argument("--start", type=int, default=261)
ap.add_argument("--end", type=int, default=450)
ap.add_argument("--miss-stop", type=int, default=60)
ap.add_argument("--delay", type=float, default=B.DEFAULT_DELAY)
args = ap.parse_args()

B.load_year_map()
session = B.make_session()
urls_path = B.OUT_DIR / "urls.csv"
rows = B._read_csv(urls_path)
have = {r["code"] for r in rows}
added = 0

for year in range(args.start_year, args.end_year + 1):
    path, prefix = B.YEAR_MAP[year]
    misses, hits = 0, 0
    for seq in range(args.start, args.end + 1):
        url = B.release_url(prefix, seq, year, "id", path)
        html = B.get_html(url, session, args.delay, quiet=True)
        if not html:
            misses += 1
            if misses >= args.miss_stop:
                B.log(f"  {year}: {args.miss_stop} consecutive misses after seq {seq}, stopping")
                break
            continue
        misses, hits = 0, hits + 1
        doc = B.parse_document(html, url)
        code = B.sp_code(prefix, seq, year)
        if doc.is_rdg and code not in have:
            rows.append({
                "year": year, "path": path, "prefix": prefix, "seq": seq, "code": code,
                "url_id": url, "url_en": B.release_url(prefix, seq, year, "en", path),
                "date": doc.date, "title": doc.title,
                "is_rdg": doc.is_rdg, "ref_no": doc.ref_no,
            })
            have.add(code)
            added += 1
            B.log(f"  + {doc.date}  sp_{code}  {doc.title[:80]}")
            B._write_csv(urls_path, rows)   # save as we go
    B.log(f"  {year}: {hits} pages found in seq {args.start}..")

B.log(f"\nAdded {added} RDG releases -> {urls_path}")
