from __future__ import annotations

import re

# Weights reflect relevance to Rates QIS Trading work.
KEYWORDS: dict[str, int] = {
    # QIS / Systematic / Factor
    "qis": 9,
    "carry trade": 9,
    "carry": 7,
    "momentum": 7,
    "alternative risk premia": 9,
    "risk premia": 8,
    "risk premium": 8,
    "arp": 7,
    "smart beta": 6,
    "factor investing": 7,
    "factor": 5,
    "systematic trading": 8,
    "systematic": 5,
    "quantitative": 4,
    "quant": 5,
    "time-series momentum": 9,
    "trend following": 7,
    "cta": 6,
    "vol targeting": 9,
    "volatility targeting": 9,
    "low volatility": 7,
    "cross-sectional": 6,
    "value factor": 7,
    "mean reversion": 6,
    "cross-asset": 6,
    "multi-asset": 5,
    "backtesting": 6,
    "backtest": 6,
    "signal": 5,
    "alpha": 5,
    "sharpe ratio": 6,
    "information ratio": 6,
    # Rates / Fixed Income
    "interest rate swap": 8,
    "rate volatility": 9,
    "swaption": 9,
    "yield curve": 7,
    "term premium": 8,
    "curve steepener": 9,
    "curve flattener": 9,
    "interest rate": 7,
    "fixed income": 6,
    "duration": 5,
    "convexity": 6,
    "real rates": 8,
    "breakeven inflation": 8,
    "sofr": 6,
    "libor": 5,
    "ois": 5,
    "treasury": 4,
    "gilts": 5,
    "bund": 5,
    "rates": 4,
    "bond market": 5,
    "steepener": 7,
    "flattener": 7,
    "tips": 5,
    # Macro / Central Bank
    "monetary policy": 6,
    "quantitative easing": 5,
    "quantitative tightening": 5,
    "rate hike": 6,
    "rate cut": 6,
    "central bank": 5,
    "federal reserve": 5,
    "inflation": 5,
    "cpi": 6,
    "pce": 6,
    "fomc": 6,
    "macro": 4,
    "ecb": 4,
    "bank of england": 4,
    "boj": 4,
    "real economy": 4,
    "qe": 4,
    "qt": 4,
    # Risk / Portfolio
    "portfolio construction": 7,
    "portfolio optimization": 7,
    "asset management": 5,
    "risk management": 6,
    "drawdown": 5,
    "tail risk": 6,
    "value at risk": 6,
    "hedging": 5,
    "correlation": 4,
    "volatility": 5,
    "diversification": 4,
    "rebalancing": 5,
    "allocation": 4,
}

_COMPILED = [(re.compile(r"\b" + re.escape(kw) + r"\b", re.IGNORECASE), w) for kw, w in KEYWORDS.items()]

# Source bonus: research-first outlets get a small bump
_SOURCE_BONUS: dict[str, int] = {
    "AQR": 6,
    "SSRN": 4,
    "Two Sigma": 5,
    "Alpha Architect": 5,
    "Citadel Securities": 5,
    "NY Fed": 3,
    "BIS": 3,
    "risk.net": 2,
}


def score_article(article: dict) -> float:
    title = article.get("title", "")
    summary = article.get("summary", "")
    # Weight title 2× over summary
    text = f"{title} {title} {summary}"
    score = 0.0
    for pattern, weight in _COMPILED:
        hits = len(pattern.findall(text))
        if hits:
            score += weight * min(hits, 3)
    # Source bonus
    for src_key, bonus in _SOURCE_BONUS.items():
        if src_key in article.get("source", ""):
            score += bonus
            break
    return round(score, 2)


def score_and_rank(articles: list[dict]) -> list[dict]:
    for a in articles:
        a["score"] = score_article(a)
    return sorted(articles, key=lambda x: x["score"], reverse=True)
