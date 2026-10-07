"""Query bi.go.id's public news-release search form (keyword + date range) and print
the matching releases' URL, date, DKom number and title.

This is the same form a visitor uses on
https://www.bi.go.id/{lang}/publikasi/ruang-media/news-release/default.aspx — an
ASP.NET postback, so each query is: GET the page for its __VIEWSTATE, then POST the
filter fields back. The SharePoint REST API behind it returns 401, so this form is
the sanctioned way to look releases up by date instead of guessing sp_ codes.

    python search_listing.py "BI Rate" 01/01/2012 31/01/2012 [--lang id] [--fmt ...]
"""
import argparse
import re
import time

from bs4 import BeautifulSoup

import bi_rdg as B

WP = "ctl00$ctl54$g_895e8ef2_eaad_4a83_9db7_1632dd8595c0$ctl00$"


def _form_state(soup: BeautifulSoup) -> dict:
    form = soup.find("form")
    data = {}
    for el in form.find_all("input"):
        name = el.get("name")
        if name and el.get("type") not in ("submit", "image", "button", "checkbox"):
            data[name] = el.get("value", "")
    return data


def _items(soup: BeautifulSoup) -> list[dict]:
    out, seen = [], set()
    for a in soup.select("a[href*='Pages/']"):
        href = a["href"]
        if "/news-release/Pages/" not in href and not href.startswith("Pages/"):
            continue
        if href in seen or not a.get_text(strip=True):
            continue
        seen.add(href)
        box = a.find_parent(["div", "li"])
        txt = box.get_text(" | ", strip=True) if box else ""
        date = re.search(r"\d{1,2} \w+ \d{4}", txt)
        ref = B.REF_RE.search(txt)
        out.append({"href": href, "title": a.get_text(" ", strip=True),
                    "date": date.group(0) if date else "",
                    "ref": "/".join(ref.groups()) if ref else ""})
    return out


def search(session, keyword: str, start: str, end: str, lang: str = "id",
           delay: float = B.DEFAULT_DELAY, max_pages: int = 10) -> list[dict]:
    url = f"https://www.bi.go.id/{lang}/publikasi/ruang-media/news-release/default.aspx"
    time.sleep(delay)
    soup = BeautifulSoup(session.get(url, timeout=B.REQUEST_TIMEOUT).text, "lxml")
    # The web-part id differs per language site (id: g_895e8ef2..., en: g_4b5c0a9e...),
    # so read it off the page rather than hardcoding it.
    box = soup.find("input", {"name": re.compile(r"\$TextBoxSearch$")})
    global WP
    WP = box["name"][: -len("TextBoxSearch")]
    data = _form_state(soup)
    data.update({
        WP + "TextBoxSearch": keyword,
        WP + "textboxDatePickerAwal": start,
        WP + "textboxDatePickerAkhir": end,
        WP + "ButtonFilter": "Cari",
    })
    results = []
    for page in range(max_pages):
        time.sleep(delay)
        soup = BeautifulSoup(session.post(url, data=data, timeout=B.REQUEST_TIMEOUT).text, "lxml")
        items = [i for i in _items(soup) if i["href"] not in {r["href"] for r in results}]
        if not items:
            break
        results += items
        # next page: the pager's "next" image button (ctl02)
        nxt = soup.find("input", {"name": WP + "DataPagerSiaranPersList$ctl02$ctl00"})
        if not nxt or nxt.has_attr("disabled"):
            break
        data = _form_state(soup)
        data[WP + "DataPagerSiaranPersList$ctl02$ctl00.x"] = "5"
        data[WP + "DataPagerSiaranPersList$ctl02$ctl00.y"] = "5"
    return results


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("keyword")
    ap.add_argument("start")
    ap.add_argument("end")
    ap.add_argument("--lang", default="id")
    ap.add_argument("--max-pages", type=int, default=10)
    a = ap.parse_args()
    for r in search(B.make_session(), a.keyword, a.start, a.end, a.lang, max_pages=a.max_pages):
        print(f"{r['date']:>20}  {r['ref']:>14}  {r['href'].rsplit('/', 1)[-1]:<28} {r['title'][:80]}")
