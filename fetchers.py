from __future__ import annotations

import hashlib
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import feedparser
import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateutil_parser

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
TIMEOUT = 18
_DATE_RE = re.compile(
    r"(January|February|March|April|May|June|July|August"
    r"|September|October|November|December)\s+\d{1,2},?\s+\d{4}",
    re.IGNORECASE,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _uid(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()


def _parse_date(raw: str) -> str:
    if not raw:
        return ""
    try:
        return dateutil_parser.parse(raw).isoformat()
    except Exception:
        return ""


def _within_days(date_str: str, days: int) -> bool:
    if not date_str:
        return True
    try:
        pub = dateutil_parser.parse(date_str)
        if pub.tzinfo is None:
            pub = pub.replace(tzinfo=timezone.utc)
        return pub >= datetime.now(timezone.utc) - timedelta(days=days)
    except Exception:
        return True


def _clean_html(html: str) -> str:
    return BeautifulSoup(html, "html.parser").get_text(separator=" ").strip()


def _meta(soup: BeautifulSoup, *attrs: dict) -> str:
    for attr in attrs:
        el = soup.find("meta", attr)
        if el and el.get("content"):
            return el["content"].strip()
    return ""


def _rss_summary(entry) -> str:
    """Extract the best available summary from an RSS entry."""
    # Prefer content:encoded (full article text for NY Fed, Alpha Architect etc.)
    if hasattr(entry, "content") and entry.content:
        raw = entry.content[0].get("value", "")
        if raw:
            text = _clean_html(raw)
            if len(text) > 100:
                return text[:800]

    # Fall back to summary/description
    for attr in ("summary", "description"):
        raw = getattr(entry, attr, None)
        if raw:
            text = _clean_html(raw)
            if len(text) > 30:
                return text[:800]
    return ""


# ── generic RSS ───────────────────────────────────────────────────────────────

def _fetch_rss(url: str, source: str, days: int, paywall: bool = False) -> list[dict]:
    try:
        feed = feedparser.parse(url, request_headers=HEADERS)
        articles = []
        for e in feed.entries:
            pub = ""
            for attr in ("published", "updated", "created"):
                if hasattr(e, attr):
                    pub = _parse_date(getattr(e, attr))
                    break
            if pub and not _within_days(pub, days):
                continue

            link = e.get("link", "")
            if not link:
                continue

            articles.append({
                "id": _uid(link),
                "source": source,
                "title": e.get("title", "").strip(),
                "url": link,
                "summary": _rss_summary(e),
                "published_date": pub,
                "is_paywall": paywall,
            })
        return articles
    except Exception as exc:
        logger.warning("RSS fetch failed [%s]: %s", source, exc)
        return []


# ── Bloomberg ─────────────────────────────────────────────────────────────────

def fetch_bloomberg(days: int) -> list[dict]:
    """Bloomberg RSS — summaries publicly readable; full articles require subscription."""
    urls = [
        "https://feeds.bloomberg.com/markets/news.rss",
        "https://feeds.bloomberg.com/economics/news.rss",
    ]
    out: list[dict] = []
    for url in urls:
        out.extend(_fetch_rss(url, "Bloomberg", days, paywall=True))
    return out


# ── Financial Times ───────────────────────────────────────────────────────────

def fetch_ft(days: int) -> list[dict]:
    """FT RSS — summaries publicly readable; full articles require FT subscription."""
    urls = [
        ("https://www.ft.com/markets?format=rss",       "FT Markets"),
        ("https://www.ft.com/economics?format=rss",     "FT Economics"),
        ("https://www.ft.com/global-economy?format=rss","FT Global Economy"),
    ]
    out: list[dict] = []
    for url, label in urls:
        out.extend(_fetch_rss(url, label, days, paywall=True))
    return out


# ── The Economist ─────────────────────────────────────────────────────────────

def fetch_economist(days: int) -> list[dict]:
    """Economist RSS — summaries publicly readable; full articles require subscription."""
    urls = [
        "https://www.economist.com/finance-and-economics/rss.xml",
        "https://www.economist.com/briefing/rss.xml",
        "https://www.economist.com/leaders/rss.xml",
    ]
    out: list[dict] = []
    for url in urls:
        out.extend(_fetch_rss(url, "The Economist", days, paywall=True))
    return out


# ── AQR via sitemap + per-page meta-tags ──────────────────────────────────────

def _fetch_aqr_page(url: str) -> dict | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        soup = BeautifulSoup(r.text, "html.parser")

        title = _meta(soup, {"property": "og:title"}, {"name": "title"})
        if not title:
            t = soup.find("title")
            title = t.get_text(strip=True) if t else ""
        title = title.replace(" | AQR", "").replace(" | AQR Capital Management", "").strip()

        if len(title) < 20 or title.lower() in ("video", "podcast", "webinar", "events"):
            return None

        summary = _meta(soup, {"name": "description"}, {"property": "og:description"})

        # Try to extract article body paragraphs for richer summary
        body = soup.find("div", class_=re.compile(r"article", re.I)) or soup.find("main")
        if body:
            paras = [p.get_text(strip=True) for p in body.find_all("p") if len(p.get_text(strip=True)) > 60]
            if paras:
                summary = " ".join(paras[:4])[:800]

        pub = ""
        date_el = soup.find("p", class_="article-page__header__article-date")
        if date_el:
            pub = _parse_date(date_el.get_text(strip=True))
        if not pub:
            m = _DATE_RE.search(r.text)
            if m:
                pub = _parse_date(m.group(0))

        return {
            "id": _uid(url),
            "source": "AQR",
            "title": title,
            "url": url,
            "summary": summary[:800],
            "published_date": pub,
            "is_paywall": False,
        }
    except Exception as exc:
        logger.debug("AQR page fetch failed [%s]: %s", url, exc)
        return None


def fetch_aqr(days: int) -> list[dict]:
    try:
        r = requests.get("https://www.aqr.com/sitemap.xml", headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "xml")
        all_urls = [
            u.find("loc").text for u in soup.find_all("url")
            if u.find("loc") and "/insights/" in u.find("loc").text.lower()
        ]
        candidate_urls = all_urls[-60:]
    except Exception as exc:
        logger.warning("AQR sitemap failed: %s", exc)
        return []

    articles: list[dict] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_fetch_aqr_page, url): url for url in candidate_urls}
        for future in as_completed(futures):
            result = future.result()
            if result and _within_days(result["published_date"], days):
                articles.append(result)

    logger.info("AQR: %d articles (from last 60 sitemap URLs)", len(articles))
    return articles


# ── risk.net (homepage scrape) ────────────────────────────────────────────────

_RISKNET_SECTIONS = [
    "/markets/", "/derivatives/", "/rates/", "/structured-products/",
    "/credit/", "/commodities/", "/risk-management/", "/asset-management/",
]


def fetch_risk_net(_: int) -> list[dict]:
    try:
        resp = requests.get("https://www.risk.net", headers=HEADERS, timeout=TIMEOUT)
        soup = BeautifulSoup(resp.text, "html.parser")
        seen: set[str] = set()
        articles: list[dict] = []

        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            if not any(s in href for s in _RISKNET_SECTIONS):
                continue
            url = href if href.startswith("http") else f"https://www.risk.net{href}"
            if url in seen:
                continue
            seen.add(url)
            title = a_tag.get_text(strip=True)
            if len(title) < 20:
                continue
            articles.append({
                "id": _uid(url),
                "source": "risk.net",
                "title": title,
                "url": url,
                "summary": "",
                "published_date": "",
                "is_paywall": True,
            })

        logger.info("risk.net: %d articles", len(articles))
        return articles
    except Exception as exc:
        logger.warning("risk.net fetch failed: %s", exc)
        return []


# ── BIS working papers ────────────────────────────────────────────────────────

def fetch_bis(days: int) -> list[dict]:
    return _fetch_rss("https://www.bis.org/doclist/wppubls.rss", "BIS", days)


# ── Federal Reserve FEDS papers ───────────────────────────────────────────────

def fetch_fed(days: int) -> list[dict]:
    return _fetch_rss("https://www.federalreserve.gov/feeds/feds.xml", "Federal Reserve", days)


# ── NY Fed Liberty Street Economics ──────────────────────────────────────────

def fetch_ny_fed(days: int) -> list[dict]:
    return _fetch_rss(
        "https://libertystreeteconomics.newyorkfed.org/feed/",
        "NY Fed",
        days,
    )


# ── Alpha Architect ───────────────────────────────────────────────────────────

def fetch_alpha_architect(days: int) -> list[dict]:
    return _fetch_rss("https://alphaarchitect.com/feed/", "Alpha Architect", days)


# ── SSRN keyword search ───────────────────────────────────────────────────────

def fetch_ssrn(days: int) -> list[dict]:
    query = "rates+carry+momentum+risk+premia+volatility+fixed+income+QIS+alternative+risk+premia"
    url = (
        "https://papers.ssrn.com/sol3/results.cfm?"
        "RequestTimeout=50000&form_name=journalBrowse&Network=no"
        f"&SortOrder=sub_date&txtMaxShowItemsPerPage=25&absdef_cntr=1"
        f"&Submit=Search&Keywords={query}"
    )
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        soup = BeautifulSoup(resp.text, "html.parser")
        articles = []

        for item in soup.select("div.result-item, div[id^='p']"):
            a_tag = item.find("a", href=lambda h: h and "/abstract/" in h)
            if not a_tag:
                continue
            href = a_tag["href"]
            article_url = href if href.startswith("http") else f"https://papers.ssrn.com{href}"
            title = a_tag.get_text(strip=True)
            if not title:
                continue

            abstract_el = item.find(class_=lambda x: x and "abstract" in x.lower())
            summary = abstract_el.get_text(strip=True)[:800] if abstract_el else ""

            date_el = item.find(string=lambda s: s and any(
                m in str(s) for m in ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                                       "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
            ))
            pub = _parse_date(
                date_el.get_text() if hasattr(date_el, "get_text") else str(date_el) if date_el else ""
            )

            if pub and not _within_days(pub, days):
                continue

            articles.append({
                "id": _uid(article_url),
                "source": "SSRN",
                "title": title,
                "url": article_url,
                "summary": summary,
                "published_date": pub,
                "is_paywall": False,
            })

        logger.info("SSRN: %d articles", len(articles))
        return articles
    except Exception as exc:
        logger.warning("SSRN fetch failed: %s", exc)
        return []


# ── Citadel Securities (via Google News RSS — site blocks direct scraping) ───

def fetch_citadel(days: int) -> list[dict]:
    """
    Citadel Securities blocks scrapers with Cloudflare.
    Google News indexes their published articles and exposes them via RSS,
    letting us surface only articles whose source is Citadel Securities itself.
    """
    url = (
        "https://news.google.com/rss/search"
        "?q=%22citadel+securities%22&hl=en-US&gl=US&ceid=US:en"
    )
    try:
        feed = feedparser.parse(url, request_headers=HEADERS)
        articles = []
        for e in feed.entries:
            # Only keep entries published by Citadel Securities themselves
            source_title = (e.get("source") or {}).get("title", "")
            if "citadel" not in source_title.lower():
                continue

            pub = ""
            for attr in ("published", "updated"):
                if hasattr(e, attr):
                    pub = _parse_date(getattr(e, attr))
                    break
            if pub and not _within_days(pub, days):
                continue

            link = e.get("link", "")
            title = e.get("title", "").strip()
            # Strip " - Citadel Securities" suffix Google News appends
            title = re.sub(r"\s*[-–]\s*Citadel Securities\s*$", "", title).strip()

            summary = _clean_html(e.get("summary", ""))
            # Google News summaries are just the title again; drop them
            if summary.lower().strip() == title.lower().strip():
                summary = ""

            if not title or not link:
                continue

            articles.append({
                "id": _uid(link),
                "source": "Citadel Securities",
                "title": title,
                "url": link,
                "summary": summary[:800],
                "published_date": pub,
                "is_paywall": False,
            })

        logger.info("Citadel Securities: %d articles", len(articles))
        return articles
    except Exception as exc:
        logger.warning("Citadel Securities fetch failed: %s", exc)
        return []


# ── Two Sigma ─────────────────────────────────────────────────────────────────

def fetch_twosigma(days: int) -> list[dict]:
    try:
        resp = requests.get("https://www.twosigma.com/articles/", headers=HEADERS, timeout=TIMEOUT)
        soup = BeautifulSoup(resp.text, "html.parser")
        articles = []

        cards = soup.select("article, div[class*='article'], div[class*='post'], div[class*='card']")
        if not cards:
            cards = [t for t in soup.find_all(["article", "li", "div"]) if t.find(["h2", "h3"]) and t.find("a")]

        for card in cards[:30]:
            heading = card.find(["h2", "h3", "h4"])
            if not heading:
                continue
            title = heading.get_text(strip=True)
            a_tag = card.find("a", href=True)
            if not a_tag:
                continue
            href = a_tag["href"]
            url = href if href.startswith("http") else f"https://www.twosigma.com{href}"

            summary_el = card.find("p")
            summary = summary_el.get_text(strip=True)[:800] if summary_el else ""

            date_el = card.find("time")
            pub = _parse_date(date_el.get("datetime", date_el.get_text()) if date_el else "")

            if pub and not _within_days(pub, days):
                continue

            articles.append({
                "id": _uid(url),
                "source": "Two Sigma",
                "title": title,
                "url": url,
                "summary": summary,
                "published_date": pub,
                "is_paywall": False,
            })

        logger.info("Two Sigma: %d articles", len(articles))
        return articles
    except Exception as exc:
        logger.warning("Two Sigma fetch failed: %s", exc)
        return []


# ── orchestrator ──────────────────────────────────────────────────────────────

def fetch_wsj(days: int) -> list[dict]:
    """WSJ RSS feeds — summaries publicly readable; full articles require subscription."""
    urls = [
        "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
        "https://feeds.a.dj.com/rss/WSJcomUSBusiness.xml",
        "https://feeds.a.dj.com/rss/RSSWorldNews.xml",
    ]
    out: list[dict] = []
    for url in urls:
        out.extend(_fetch_rss(url, "WSJ", days, paywall=True))
    return out


def fetch_all(days: int) -> list[dict]:
    tasks = {
        "Bloomberg":           fetch_bloomberg,
        "FT":                  fetch_ft,
        "Economist":           fetch_economist,
        "WSJ":                 fetch_wsj,
        "Citadel Securities":  fetch_citadel,
        "risk.net":            fetch_risk_net,
        "BIS":                 fetch_bis,
        "Federal Reserve":     fetch_fed,
        "NY Fed":              fetch_ny_fed,
        "Alpha Architect":     fetch_alpha_architect,
        "AQR":                 fetch_aqr,
        "SSRN":                fetch_ssrn,
        "Two Sigma":           fetch_twosigma,
    }
    all_articles: list[dict] = []

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(fn, days): name for name, fn in tasks.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                results = future.result(timeout=70)
                logger.info("%-18s → %d articles", name, len(results))
                all_articles.extend(results)
            except Exception as exc:
                logger.warning("%-18s → FAILED: %s", name, exc)

    seen: set[str] = set()
    unique: list[dict] = []
    for a in all_articles:
        if a["url"] not in seen and a.get("title"):
            seen.add(a["url"])
            unique.append(a)

    logger.info("Total unique articles: %d", len(unique))
    return unique
