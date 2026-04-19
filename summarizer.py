from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
FETCH_TIMEOUT = 8


def _fetch_article_text(url: str) -> str:
    """Fetch and extract readable text from an article URL."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=FETCH_TIMEOUT)
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside", "noscript", "figure"]):
            tag.decompose()
        container = soup.find("article") or soup.find("main") or soup.find(id=re.compile(r"content|article|body", re.I))
        if not container:
            container = soup.find("body")
        if not container:
            return ""
        paras = [p.get_text(" ", strip=True) for p in container.find_all("p")]
        text = " ".join(p for p in paras if len(p) > 40)
        return text[:6000]
    except Exception as exc:
        logger.debug("Article fetch failed [%s]: %s", url[:60], exc)
        return ""


def _score_sentence(sent: str) -> float:
    """Score a sentence by keyword relevance to rates/QIS trading."""
    from scorer import KEYWORDS
    s = sent.lower()
    return sum(w for kw, w in KEYWORDS.items() if kw in s)


def _extract_bullets(text: str, n: int = 3) -> list[str]:
    """Return top-n sentences from text ranked by keyword relevance."""
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    sentences = [s.strip() for s in sentences if 35 < len(s.strip()) < 280]
    if not sentences:
        return []
    if len(sentences) <= n:
        return sentences

    scored = []
    for i, sent in enumerate(sentences):
        kw_score = _score_sentence(sent)
        # Mild position boost — opening sentences carry context
        pos_boost = max(0.0, 1.5 - i * 0.15)
        scored.append((kw_score + pos_boost, i, sent))

    top_idx = sorted(idx for _, idx, _ in sorted(scored, reverse=True)[:n])
    return [sentences[i] for i in top_idx]


def ai_summary(title: str, content: str, url: str = "", is_paywall: bool = False) -> str:
    """
    Free extractive summary.
    For non-paywall articles: fetches the article page and picks the 3 most
    relevant sentences by keyword score.
    For paywall articles: falls back to the RSS summary text.
    Returns a newline-joined bullet string, or '' if nothing useful found.
    """
    full_text = ""
    if url and not is_paywall:
        full_text = _fetch_article_text(url)

    text = full_text if len(full_text) > 200 else (content or "")
    bullets = _extract_bullets(text, n=3)
    if not bullets:
        return ""
    return "\n".join(f"• {b}" for b in bullets)


def enrich_articles(articles: list[dict], max_workers: int = 6) -> None:
    """Fill ai_summary in-place for articles that lack one, using a thread pool."""
    targets = [a for a in articles if not a.get("ai_summary")]
    if not targets:
        return

    def _run(a: dict) -> None:
        a["ai_summary"] = ai_summary(
            a.get("title", ""),
            a.get("summary", ""),
            url=a.get("url", ""),
            is_paywall=bool(a.get("is_paywall")),
        )

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_run, a): a for a in targets}
        for fut in as_completed(futures):
            try:
                fut.result()
            except Exception as exc:
                logger.warning("Summary failed: %s", exc)
