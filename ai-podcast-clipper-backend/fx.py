"""
US dollar to Indian rupee rate, for showing costs in rupees (Settings > Currency).

The runner saves the day's rate with every run (stats ledger "usdInr"), so a run's
rupee cost never changes afterwards. Sources, all free and without an API key:

    1. Frankfurter      https://api.frankfurter.dev  (central-bank reference rates, daily)
    2. ExchangeRate-API https://open.er-api.com      (daily; only if Frankfurter fails)
    3. the last rate saved in <data>/stats/fx.json

Standard library only. Never raises.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import urllib.request
from collections.abc import Callable

FRANKFURTER_URL = "https://api.frankfurter.dev/v2/rates?base=usd&quotes=inr"
FRANKFURTER_V1_URL = "https://api.frankfurter.dev/v1/latest?from=USD&to=INR"
OPEN_ER_URL = "https://open.er-api.com/v6/latest/USD"
CACHE = pathlib.Path("stats") / "fx.json"
MAX_AGE_HOURS = 6


def _get_json(url: str, timeout_s: float = 10.0):
    request = urllib.request.Request(url, headers={"User-Agent": "ai-podcast-clipper (personal use)"})
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        return json.loads(response.read().decode("utf-8"))


def _plausible(rate) -> float | None:
    try:
        value = float(rate)
    except (TypeError, ValueError):
        return None
    return value if 40.0 < value < 250.0 else None


def from_frankfurter(get=_get_json) -> dict | None:
    data = get(FRANKFURTER_URL)
    if isinstance(data, list) and data:
        rate = _plausible(data[0].get("rate"))
        if rate:
            return {"rate": rate, "date": data[0].get("date"), "source": "frankfurter"}
    data = get(FRANKFURTER_V1_URL)
    rate = _plausible((data.get("rates") or {}).get("INR")) if isinstance(data, dict) else None
    return {"rate": rate, "date": data.get("date"), "source": "frankfurter"} if rate else None


def from_open_er(get=_get_json) -> dict | None:
    data = get(OPEN_ER_URL)
    if not isinstance(data, dict) or data.get("result") != "success":
        return None
    rate = _plausible((data.get("rates") or {}).get("INR"))
    if not rate:
        return None
    day = dt.datetime.fromtimestamp(int(data.get("time_last_update_unix") or 0), dt.timezone.utc).date()
    return {"rate": rate, "date": day.isoformat(), "source": "exchangerate-api"}


def usd_inr(data_dir: pathlib.Path, *, now: dt.datetime | None = None,
            sources: tuple[Callable, ...] = (from_frankfurter, from_open_er), log=print) -> dict | None:
    """{"rate", "date", "source", "fetchedAt"} for 1 USD in INR, or None if no rate was ever found.
    A rate fetched less than MAX_AGE_HOURS ago is reused without a network call."""
    now = now or dt.datetime.now(dt.timezone.utc)
    cache = pathlib.Path(data_dir) / CACHE
    cached = None
    try:
        cached = json.loads(cache.read_text(encoding="utf-8"))
        fetched = dt.datetime.fromisoformat(cached["fetchedAt"].replace("Z", "+00:00"))
        if now - fetched < dt.timedelta(hours=MAX_AGE_HOURS) and _plausible(cached.get("rate")):
            return cached
    except Exception:  # noqa: BLE001 - no cache yet, or a broken one
        pass
    for source in sources:
        try:
            found = source()
        except Exception as exc:  # noqa: BLE001
            log(f"Exchange rate: {source.__name__} failed ({type(exc).__name__}: {str(exc)[:120]})")
            continue
        if found:
            found["fetchedAt"] = now.isoformat().replace("+00:00", "Z")
            try:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(json.dumps(found), encoding="utf-8")
            except OSError:
                pass
            return found
    if cached and _plausible(cached.get("rate")):
        return {**cached, "stale": True}
    return None
