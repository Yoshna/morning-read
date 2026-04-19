from __future__ import annotations

import json as _json
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

_PAYWALL_CLASSES = re.compile(r"paywall|subscribe-wall|subscription-wall|paid-content|premium-content", re.I)


def _is_paywalled(soup: BeautifulSoup) -> bool:
    """Detect paywall from JSON-LD isAccessibleForFree or HTML markers."""
    # JSON-LD check (Bloomberg, WSJ embed this)
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = _json.loads(script.string or "")
            items = data if isinstance(data, list) else [data]
            for item in items:
                if isinstance(item, dict) and item.get("isAccessibleForFree") is False:
                    return True
        except Exception:
            pass
    # HTML element check
    if soup.find(id=_PAYWALL_CLASSES) or soup.find(class_=_PAYWALL_CLASSES):
        return True
    return False


def _fetch_article(url: str) -> tuple[bool, str]:
    """
    Fetch article page. Returns (is_paywalled, text).
    is_paywalled is True only when we can positively confirm a paywall.
    """
    try:
        r = requests.get(url, headers=HEADERS, timeout=FETCH_TIMEOUT)
        soup = BeautifulSoup(r.text, "html.parser")
        paywalled = _is_paywalled(soup)
        if paywalled:
            return True, ""
        for tag in soup(["script", "style", "nav", "header", "footer", "aside", "noscript", "figure"]):
            tag.decompose()
        container = (soup.find("article") or soup.find("main")
                     or soup.find(id=re.compile(r"content|article|body", re.I))
                     or soup.find("body"))
        if not container:
            return False, ""
        paras = [p.get_text(" ", strip=True) for p in container.find_all("p")]
        text = " ".join(p for p in paras if len(p) > 40)
        return False, text[:6000]
    except Exception as exc:
        logger.debug("Article fetch failed [%s]: %s", url[:60], exc)
        return False, ""


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


def enrich_articles(articles: list[dict], max_workers: int = 6) -> None:
    """
    Fill ai_summary in-place (and update is_paywall if detected) for articles
    that lack a summary, using a thread pool for parallel page fetches.
    """
    targets = [a for a in articles if not a.get("ai_summary")]
    if not targets:
        return

    def _run(a: dict) -> None:
        already_paywall = bool(a.get("is_paywall"))
        full_text = ""

        if a.get("url") and not already_paywall:
            detected_paywall, full_text = _fetch_article(a["url"])
            if detected_paywall:
                a["is_paywall"] = True  # mark individual article as paywalled

        text = full_text if len(full_text) > 200 else (a.get("summary") or "")
        bullets = _extract_bullets(text, n=3)
        a["ai_summary"] = "\n".join(f"• {b}" for b in bullets) if bullets else ""

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_run, a): a for a in targets}
        for fut in as_completed(futures):
            try:
                fut.result()
            except Exception as exc:
                logger.warning("Enrichment failed: %s", exc)
