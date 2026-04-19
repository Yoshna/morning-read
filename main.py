from __future__ import annotations

import logging
import os
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path

import uvicorn
from dateutil import parser as dateutil_parser
from fastapi import FastAPI, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

from database import cache_age_hours, get_articles, save_articles
from fetchers import fetch_all
from scorer import score_and_rank
from summarizer import ai_summary

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="Morning Read")

STATIC = Path(__file__).parent / "static"
CACHE_TTL_HOURS = 6


def _filter_by_days(articles: list[dict], days: int) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out = []
    for a in articles:
        if not a.get("published_date"):
            out.append(a)
            continue
        try:
            pub = dateutil_parser.parse(a["published_date"])
            if pub.tzinfo is None:
                pub = pub.replace(tzinfo=timezone.utc)
            if pub >= cutoff:
                out.append(a)
        except Exception:
            out.append(a)
    return out


AI_SUMMARY_TOP_N = 20  # summarise only the top-ranked articles to keep API cost low


def _enrich_with_ai(ranked: list[dict]) -> None:
    """Fills ai_summary in-place for top articles that lack one."""
    for a in ranked[:AI_SUMMARY_TOP_N]:
        if a.get("ai_summary"):
            continue
        content = a.get("summary", "")
        result = ai_summary(a.get("title", ""), content)
        a["ai_summary"] = result


@app.get("/")
async def root():
    return FileResponse(str(STATIC / "index.html"))


@app.get("/api/articles")
async def get_ranked_articles(
    days: int = Query(default=3, ge=1, le=90),
    top_n: int = Query(default=50, ge=1, le=200),
    force_refresh: bool = Query(default=False),
):
    age = cache_age_hours()
    needs_refresh = force_refresh or age > CACHE_TTL_HOURS

    if needs_refresh:
        logger.info("Fetching fresh articles (cache age=%.1fh, force=%s)", age, force_refresh)
        raw = await run_in_threadpool(fetch_all, days)
        ranked = score_and_rank(raw)
        _enrich_with_ai(ranked)
        await run_in_threadpool(save_articles, ranked)
    else:
        logger.info("Using cache (age=%.1fh)", age)
        ranked = get_articles(days)
        if not ranked:
            raw = await run_in_threadpool(fetch_all, days)
            ranked = score_and_rank(raw)
            _enrich_with_ai(ranked)
            await run_in_threadpool(save_articles, ranked)

    filtered = _filter_by_days(ranked, days)

    return {
        "articles": filtered[:top_n],
        "total_fetched": len(ranked),
        "total_filtered": len(filtered),
        "cache_age_hours": round(age if not needs_refresh else 0.0, 2),
        "last_updated": datetime.utcnow().isoformat() + "Z",
    }


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    try:
        local_ip = socket.gethostbyname(socket.gethostname())
    except Exception:
        local_ip = "127.0.0.1"

    print("\n" + "=" * 55)
    print("  Morning Read — Article Fetcher")
    print("=" * 55)
    print(f"  Local:   http://localhost:{port}")
    print(f"  Phone:   http://{local_ip}:{port}   (same WiFi)")
    print("=" * 55 + "\n")

    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
