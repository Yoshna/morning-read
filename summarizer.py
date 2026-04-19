from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_CLIENT = None


def _client():
    global _CLIENT
    if _CLIENT is None:
        import anthropic
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            return None
        _CLIENT = anthropic.Anthropic(api_key=key)
    return _CLIENT


PROMPT = """You are a research assistant for a Rates QIS Trader who works in:
- Rates, Fixed Income, Yield Curve, Swaptions
- QIS: Carry, Momentum, Volatility, Value, Alternative Risk Premia
- Macro: Inflation, Central Banks, Monetary Policy
- Risk Management and Portfolio Construction

Summarize the article below in EXACTLY this format — no extra text:

• [Key point 1 — specific fact or finding]
• [Key point 2 — specific fact or finding]
• [Key point 3 — specific fact or finding]
📊 Rates/QIS angle: [One sentence on why this matters for rates or QIS trading]

Article title: {title}

Article content:
{content}"""


def ai_summary(title: str, content: str) -> str:
    """
    Generate a 3-bullet AI summary using Claude Haiku.
    Returns empty string if API key not set or call fails.
    """
    c = _client()
    if not c:
        return ""

    text = content.strip()
    if not text or len(text) < 80:
        return ""

    # Truncate to ~2000 chars to keep cost low
    truncated = text[:2000]

    try:
        msg = c.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{
                "role": "user",
                "content": PROMPT.format(title=title, content=truncated),
            }],
        )
        return msg.content[0].text.strip()
    except Exception as exc:
        logger.warning("AI summary failed for '%s': %s", title[:50], exc)
        return ""
