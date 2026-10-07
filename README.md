# Phase 0 — building the RDG corpus

This is the feasibility test for the thesis. It answers one question: **can I actually
get Bank Indonesia's monetary policy statements out of bi.go.id, back far enough, in
good enough shape, to build a tone index?**

Do this before you write any economics. If the answer is no, you need to know now.

---

## What you end up with

Two files in `out/`:

**`documents.csv`** — one row per press release.

| code | lang | date | title | direction | bps | rate_level | n_sections | flags |
|---|---|---|---|---|---|---|---|---|
| 2810726 | id | 2026-05-20 | BI-Rate Naik 50 bps menjadi 5,25%… | hike | 50 | 5.25 | 13 | |

**`corpus.csv`** — one row per *section* within a release. This is the one you'll
actually run the tone index on.

| code | lang | date | section_order | topic | heading | text |
|---|---|---|---|---|---|---|
| 2810726 | id | 2026-05-20 | 6 | inflation | Inflasi IHK terjaga rendah… | Inflasi inti tetap terkendali… |
| 2810726 | id | 2026-05-20 | 5 | exchange_rate | Bank Indonesia terus memperkuat… | Intervensi dilakukan di pasar spot… |

The `topic` column is why this is worth doing properly. It lets you build a separate
inflation-tone series and rupiah-tone series instead of one flat score — so you can show
BI turning hawkish on inflation while turning dovish on growth, which a single index
would average away.

This covers 2012–2026 — that's the confirmed start of BI's enumerable press-release
archive, not a placeholder. For 2005–2011, see **"Filling the pre-2012 gap"** below:
a separate, lower-granularity supplementary dataset, not more rows in these two files.

---

## Setup

```bash
pip install -r requirements.txt
```

Open `bi_rdg.py` and put your real email in `USER_AGENT` near the top. You are hitting a
central bank's website a few thousand times; being identifiable is both good manners and
your protection if anyone asks what the traffic was.

Do not lower `DEFAULT_DELAY` below 1.5 seconds.

---

## The four steps

### Step 1 — `selftest` (offline, 2 seconds)

```bash
python bi_rdg.py selftest
```

Parses a bundled fixture that mimics BI's page structure. No network. This only proves
the parsing logic runs and your dependencies are installed. Expect 13 PASS lines.

If anything fails here it's an environment problem, not a BI problem.

### Step 2 — `pilot` (~5 minutes) — **this is the go/no-go test**

```bash
python bi_rdg.py pilot
```

Probes four eras of BI's website — by default 2005, 2013, 2020, 2026, though 2005 is
now known to fail (see "Filling the pre-2012 gap" below — the confirmed start is 2012,
so `--years 2012 2017 2020 2026` is a better spread today) — finds a few monetary
policy releases in each, downloads both language versions, parses them, and prints an
audit.

Why four eras: BI's site has been rebuilt several times in 21 years. A scraper written
against the 2026 layout may silently return empty rows for 2008. Testing the extremes
first is how you find that out in an afternoon instead of in month four.

### Step 3 — check it by hand (~30 minutes) — **do not skip this**

Open `out/audit_report.txt`. For each release listed, open the URL in your browser next
to the report and check four things:

1. **Date** — does it match the date on the page?
2. **Rate** — does `direction`, `bps` and `rate_level` match the headline? A May 2026
   release headed "Naik 50 bps menjadi 5,25%" must read `hike / 50 / 5.25`.
3. **Headings** — do the section headings look like the bold lead-in sentences on the
   page, or are they fragments and boilerplate?
4. **Count** — recent BI releases run about 10–13 sections. Two or three means the
   parser found the wrong container.

This half hour is the whole point of the pilot. A scraper that returns rows is not a
scraper that returns *correct* rows, and silent wrongness is the failure mode that
destroys a thesis six months later.

**Reading the result:**

| What you see | What it means | What to do |
|---|---|---|
| All four eras parse cleanly | Best case | Go to step 4 |
| 2026 and 2020 fine, 2005 empty or garbled | Template changed; early archive differs | Send me a saved 2005 page, I'll add a case |
| No releases found in any year | The `sp_` URL scheme is wrong | Open the listing in a browser, copy one real URL, send it to me |
| Dates right but rates wrong | Headline wording differs by era | Easy fix to the regexes — send me the titles |
| Early years genuinely unavailable | Sample starts later than 2005 | **Tell your advisor before building anything.** A thin pre-2020 baseline weakens the whole comparison |

### Step 4 — full run (~2–4 hours, unattended)

```bash
python bi_rdg.py discover --start-year 2012 --end-year 2026
python bi_rdg.py fetch
python bi_rdg.py parse
python bi_rdg.py audit --n 40
```

(`--start-year 2012`, not 2005 — see "Filling the pre-2012 gap" below. Scanning
2005–2011 here would just spend hours confirming, year by year, what `probe` already
settled in minutes: nothing resolves before 2012.)

`discover` scans sequence numbers year by year and keeps the ones whose titles look like
policy decisions. `fetch` downloads both languages. Everything is cached in `raw/`, so
re-running is free and you can stop and restart safely.

Audit again on a fresh sample of 40. Check the per-year counts printed at the top: you
should see roughly 12 releases a year before 2016 and up to 24 after (BI moved to more
frequent meetings), so a year showing 2 is a year that needs investigating.

---

## Step 5 — fix the topic labels (~1 hour, high value)

```bash
python bi_rdg.py topics
```

BI recycles the same ~20 heading templates across the entire archive. This command
collapses every section into that short list and shows the topic the keyword rules
guessed for each.

The keyword tagger gets most of them right and some of them wrong — it has no way to know
that "Peran kredit perbankan dalam mendukung **pertumbuhan ekonomi**" is about credit
rather than growth. Since there are only about twenty templates, hand-labelling is an
hour's work and gives you something far more defensible than a heuristic:

1. open `out/topic_review.csv`
2. fix the `topic` column
3. save it as `topic_overrides.csv` next to `bi_rdg.py`
4. re-run `python bi_rdg.py parse`

Your labels now override the guesses. Keep that file — it goes in your thesis appendix as
the topic coding scheme, and it's the kind of documented, reproducible choice that makes
a measurement chapter credible.

---

## Filling the pre-2012 gap

`probe` (above) settles a question that matters more than it first seems: the
enumerable `sp_` archive — the one everything above is built on — only goes
back to **2012**. Verified two ways:

- `python bi_rdg.py probe --start-year 2005 --end-year 2026` resolves cleanly
  for every year 2012–2026 and finds nothing before that.
- BI's older PDF press-release archive (`/id/archive/arsip-siaran-pers/`,
  a separate, non-enumerable system with a date-range search form instead of
  sequential URLs) contains exactly **3 documents total**, all dated
  29–31 December 2004, and nothing else — checked directly against the live
  search, including a wide net spanning 2004–2012. None of the 3 are RDG
  decisions. This isn't a scraper limitation; it's what BI's site actually
  serves today.

So 2005–2011 (7 years) has no retrievable RDG press releases via either
system. **Tell your advisor** — this caps how far back your primary-source
sample can go, independent of anything the scraper does.

What does exist for that gap: BI's annual **"Laporan Perekonomian Indonesia"**
reports (one per year, at `.../publikasi/laporan/Pages/lpi_{year}.aspx` —
except 2009, which sits at `lpi_09.aspx`, not `lpi_2009.aspx`) each carry a
dedicated monetary-policy chapter. Two scripts pull these into a usable form:

```bash
python extract_annual_reports.py   # downloads + extracts the right chapter per year
python parse_annual_reports.py     # cleans it and splits it into topic-tagged chunks
```

This produces:

- `pre2012_annual/{year}.txt` — raw chapter text, as extracted from the PDF.
- `pre2012_annual/{year}_clean.txt` — running headers, stray page numbers, and
  PDF ligature artifacts (`inﬂ asi` → `inflasi`) stripped; line-wraps reflowed
  into real prose.
- `out/pre2012_annual_reports.csv` — one row per year: chapter title, source
  URL, word count.
- `out/pre2012_annual_corpus.csv` — the cleaned text split into ~5-sentence
  chunks and topic-tagged with the same keyword scorer (`tag_topic`) the main
  corpus uses.

**This is not documents.csv/corpus.csv with a different date range — treat it
as a separate, lower-granularity dataset:**

- The chapter to extract is **not** at a consistent chapter number across
  years — it moved from "Bab 5" (2005) to "Bab 6" (2006–2007) to a bundled
  "Bab V: Respons Kebijakan Bank Indonesia" (2008) to "Bab III" (2009, framed
  around the global crisis) to "Bab IV" (2010, framed around capital inflows)
  to "Bab VII" (2011, split out as its own "Monetary and Macroprudential
  Policy" chapter). Each year in `extract_annual_reports.py`'s `CHAPTERS` list
  was verified by hand against that year's own table of contents — don't
  assume a fixed chapter number if you extend this to other years.
- BI's own download links are inconsistently labelled: at least two zips'
  *internal* filenames didn't match what they actually contained (a link
  titled "Bagian 5" once served "Bagian 1"'s content; a 2005-page download
  slug said `lpi2007.zip` but held the 2005 report). Verify content, not
  filenames, before trusting a new source file.
- Extracted PDF text carries **no reliable paragraph-break signal** — one
  sample chapter had 2 genuine paragraph breaks across 2052 lines, i.e.
  effectively every line break is a mid-sentence column wrap. The ~5-sentence
  chunk boundaries in `pre2012_annual_corpus.csv` are therefore algorithmic,
  not the author's real structure the way corpus.csv's bold-led sections are.
  Topic labels here are a real but looser signal — good for narrative
  cross-checking and describing the year's overall stance, not a substitute
  for per-meeting tone granularity.

## Then the bilingual check

You now have the same release in both languages, keyed by the same `code`. Score both and
correlate them. They are official translations of one decision, so any systematic
divergence in measured stance is either translation drift or audience-targeting — and
either reading is interesting. Almost no other researcher can run this test, which is
exactly why it belongs in the thesis.

---

## Notes and honesty

- **The URL scheme is inferred, not documented.** It's verified for 2026
  (`sp_2810726` = prefix 28, release 107, year 2026, matching reference No.28/107/DKom)
  and the script probes each year to confirm rather than assuming. Older years may not
  follow it. The pilot is what tells you.
- **The HTML selectors are best guesses.** I could read BI's rendered pages but not their
  raw HTML, so `_content_root()` tries a list of likely containers and then falls back to
  whichever `<div>` holds the most bold-led paragraphs. If the pilot shows bad section
  counts, save one page (`Ctrl+S`, "Webpage, HTML only") and send it to me — with the
  real markup I can make the selectors exact in a few minutes.
- **The sample starts in 2012, confirmed — not a guess anymore.** The go/no-go table in
  Step 2 flagged "early years genuinely unavailable" as a possible pilot outcome; that's
  what happened. `probe` resolves cleanly for every year 2012–2026 and finds nothing
  before it, and BI's old PDF press-release archive turned out to hold only 3 unrelated
  documents from December 2004. See "Filling the pre-2012 gap" above for what covers
  2005–2011 instead, and tell your advisor about the boundary either way.
- **The pilot's audit is what catches silent wrongness, not just crashes.** Every
  substantive parsing bug found so far — a false-positive 404 detector that discarded
  real pages, a cache key collision that let one URL template's cached content masquerade
  as another's, a day/month date swap from matching the wrong number on the page, a
  direction misclassification from a title typo falling through to an unrelated keyword
  in the body, a content-root selector grabbing the site's nav menu instead of the
  article, and releases authored with `<div>`-wrapped paragraphs instead of `<p>` — was
  found by actually reading the audit output against the real page, not by the code
  crashing. If something about a parsed row looks even slightly off, open the URL and
  check it by hand before trusting the row.
- **Caching is deliberate.** Raw HTML in `raw/` is your archive. Never re-download to
  re-parse; iterate on `parse` against the cache. Also back `raw/` up — if BI reorganises
  its site mid-thesis, that folder is your only copy. Cache paths are keyed on
  `(lang, path_template, code)` — `raw/<lang>/<news-release|siaran-pers>/sp_<code>.html`
  — not just `(lang, code)`, because the same numeric code can independently resolve
  under more than one path template and a code-only key let one silently overwrite the
  other's cached content.
- **The corpus is a research asset in itself.** Even if the wedge result comes back null,
  a cleaned, topic-segmented, bilingual corpus of 20 years of BI communication is a
  contribution nobody has made. That's chapter one, and it's why this direction had the
  highest floor of the six.

## Finding missing meetings (added 2026-09-29)

`discover` alone left ~40 meetings out. Two separate causes, two tools:

- **`scan_gaps.py`** — from 2021 on BI issues more than 260 releases a year, so
  `discover --max-seq 260` cut off every Oct–Dec RDG release (Oct 2022 is seq 285).
  `python scan_gaps.py 2021 2026 --start 261 --end 450` extends the scan and appends
  finds to `out/urls.csv`.
- **`search_listing.py`** — before ~2021 many RDG releases sit at irregular slugs no
  code pattern produces (`sp_140112` zero-padded, `SP_12062012`, `sp_142612-1`,
  `sp_176315b`, `sp 78705`…). This queries bi.go.id's own public news-release search
  form (keyword + date range) for a given month:
  `python search_listing.py "" 01/01/2012 31/01/2012 [--lang en]`.
  The SharePoint REST API behind the site returns 401; this form is the public route.
- English versions sometimes use a different slug from the Indonesian one; those are
  saved under the Indonesian code and listed in `out/en_slug_aliases.csv`.

`parse` now cross-checks every decision against the rate-level series and against
its other-language version (`reconcile_decisions()`); the `*_vs_levels`,
`level_jump` and `lang_mismatch:*` flags are what to review. A missing meeting shows
up as a level/bps mismatch on the meeting after it — that is how the 2005 gaps were found.
