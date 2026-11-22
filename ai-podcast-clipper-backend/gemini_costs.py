"""
Gemini API cost of a run, from the token counts each call reports.

Every Gemini answer carries `usage_metadata`; moments.py keeps it per call in the
ranking report (report["usage"]: model, served, input, cached, output, thinking, ok).
estimate() prices those calls with the official Standard paid-tier prices below, as
they stood on the day of the run:

    input   = (input - cached) tokens x input price  +  cached tokens x caching price
    output  = (output + thinking) tokens x output price   (thinking is billed as output)

Prices: https://ai.google.dev/gemini-api/docs/pricing (Standard tier, text, USD per
1M tokens), checked 2026-11-22. Update PRICES (and PRICES_CHECKED) when Google changes
them; each run stores the date it was priced with, so old runs keep their cost.
It is an estimate: AI Studio's Billing page has the real balance.

No third-party imports (runner.py and rank_moments.py use it on the laptop; it is not
bundled into the Modal image, so a price change needs no redeploy).
"""

from __future__ import annotations

import datetime as dt

PRICES_CHECKED = "2026-11-22"
PRICES_SOURCE = "https://ai.google.dev/gemini-api/docs/pricing"

# model -> periods, oldest first. A period applies from its "from" date (UTC, inclusive).
# "long": prices for prompts over LONG_PROMPT_TOKENS (only the Pro models have them).
PRICES: dict[str, list[dict]] = {
    "gemini-3.8-flash": [
        {"from": None, "input": 0.75, "output": 3.75, "cached": 0.075},
        {"from": "2027-01-01", "input": 1.50, "output": 7.50, "cached": 0.15},
    ],
    "gemini-3.6-flash": [
        {"from": None, "input": 0.75, "output": 3.75, "cached": 0.075},
        {"from": "2027-01-01", "input": 1.50, "output": 7.50, "cached": 0.15},
    ],
    "gemini-3.5-flash-lite": [{"from": None, "input": 0.30, "output": 2.50, "cached": 0.03}],
    "gemini-3.1-flash-lite": [{"from": None, "input": 0.25, "output": 1.50, "cached": 0.025}],
    "gemini-3-flash-preview": [{"from": None, "input": 0.50, "output": 3.00, "cached": 0.05}],
    "gemini-2.5-flash": [{"from": None, "input": 0.30, "output": 2.50, "cached": 0.03}],
    "gemini-2.5-flash-lite": [{"from": None, "input": 0.10, "output": 0.40, "cached": 0.01}],
    "gemini-2.5-pro": [{"from": None, "input": 1.25, "output": 10.00, "cached": 0.125,
                        "long": {"input": 2.50, "output": 15.00, "cached": 0.25}}],
    "gemini-3.1-pro-preview": [{"from": None, "input": 2.00, "output": 12.00, "cached": 0.20,
                                "long": {"input": 4.00, "output": 18.00, "cached": 0.40}}],
    "gemini-4-argon": [
        {"from": None, "input": 2.00, "output": 10.00, "cached": 0.10},
        {"from": "2026-12-01", "input": 4.00, "output": 20.00, "cached": 0.20},
    ],
}
# Retired model IDs Google now routes to another model (billed as that model).
ALIASES = {"gemini-3.5-flash": "gemini-3.6-flash", "gemini-3.7-flash": "gemini-3.8-flash"}
LONG_PROMPT_TOKENS = 200_000
TOKEN_KEYS = ("input", "cached", "output", "thinking")


def canonical(model: str | None) -> str | None:
    """A model ID as PRICES knows it: "models/" and version suffixes such as "-001" or a
    preview date are dropped by matching the longest known ID at the start; aliases resolve."""
    if not model:
        return None
    name = str(model).strip().lower().removeprefix("models/")
    name = ALIASES.get(name, name)
    if name in PRICES:
        return name
    for known in sorted(PRICES, key=len, reverse=True):
        if name.startswith(known + "-"):
            return known
    for old, new in ALIASES.items():
        if name.startswith(old + "-"):
            return new
    return None


def _day(when) -> dt.date:
    if when is None:
        return dt.datetime.now(dt.timezone.utc).date()
    if isinstance(when, dt.datetime):
        return (when.astimezone(dt.timezone.utc) if when.tzinfo else when).date()
    if isinstance(when, dt.date):
        return when
    return dt.date.fromisoformat(str(when)[:10])


def price_for(model: str | None, when=None) -> dict | None:
    """USD per 1M tokens for `model` on the day `when` (default today, UTC); None if unknown."""
    periods = PRICES.get(canonical(model) or "")
    if not periods:
        return None
    day, chosen = _day(when), None
    for period in periods:
        if period["from"] is None or day >= dt.date.fromisoformat(period["from"]):
            chosen = period
    return chosen


def tokens(call: dict) -> dict:
    out = {}
    for key in TOKEN_KEYS:
        try:
            out[key] = max(0, int(call.get(key) or 0))
        except (TypeError, ValueError):
            out[key] = 0
    out["cached"] = min(out["cached"], out["input"])
    return out


def call_cost(call: dict, prices: dict) -> float:
    """USD of one call's tokens at `prices` (a PRICES period)."""
    t = tokens(call)
    rates = prices.get("long") if prices.get("long") and t["input"] > LONG_PROMPT_TOKENS else prices
    return ((t["input"] - t["cached"]) * rates["input"] + t["cached"] * rates["cached"]
            + (t["output"] + t["thinking"]) * rates["output"]) / 1_000_000


def estimate(calls: list[dict] | None, when=None) -> dict | None:
    """The cost of a run's Gemini calls (report["usage"]); None when no call reported tokens.

    Returns {"usd", "calls", "tokens": {input, cached, output, thinking},
    "models": {model: {"calls", "usd", input, cached, output, thinking}},
    "unpriced": [models without a price], "prices": PRICES_CHECKED}.
    A call is priced as the model that answered ("served") when Google reports it."""
    calls = [c for c in calls or [] if isinstance(c, dict)]
    if not calls:
        return None
    total = {key: 0 for key in TOKEN_KEYS}
    models: dict[str, dict] = {}
    usd, unpriced = 0.0, set()
    for call in calls:
        t = tokens(call)
        name = canonical(call.get("served")) or canonical(call.get("model")) or str(call.get("model") or "unknown")
        prices = price_for(name, when)
        cost = call_cost(call, prices) if prices else 0.0
        if prices is None and any(t.values()):
            unpriced.add(name)
        entry = models.setdefault(name, {"calls": 0, "usd": 0.0, **{key: 0 for key in TOKEN_KEYS}})
        entry["calls"] += 1
        entry["usd"] += cost
        for key in TOKEN_KEYS:
            entry[key] += t[key]
            total[key] += t[key]
        usd += cost
    for entry in models.values():
        entry["usd"] = round(entry["usd"], 6)
    return {"usd": round(usd, 6), "calls": len(calls), "tokens": total, "models": models,
            "unpriced": sorted(unpriced), "prices": PRICES_CHECKED}


def describe(cost: dict | None, usd_inr: float | None = None) -> str:
    """One line for logs and rank_moments.py: calls, tokens and the estimated cost."""
    if not cost:
        return "Gemini: no token counts reported"
    t = cost["tokens"]
    money = f"${cost['usd']:.4f}" + (f" (about ₹{cost['usd'] * usd_inr:.2f})" if usd_inr else "")
    extra = f", {t['cached']:,} cached" if t["cached"] else ""
    line = (f"Gemini: {cost['calls']} call(s), {t['input']:,} tokens in{extra}, "
            f"{t['output']:,} out + {t['thinking']:,} thinking, about {money}")
    if cost.get("unpriced"):
        line += f" (no price for {', '.join(cost['unpriced'])})"
    return line
