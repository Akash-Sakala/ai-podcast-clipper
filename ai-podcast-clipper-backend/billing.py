"""Laptop-side billing: the `modal` CLI, live rates, this month's bill, the cost of a run
(Modal containers, plus the Gemini calls priced by gemini_costs.py)."""

import datetime as dt
import json
import os
import pathlib
import subprocess
import sys
import time

import costs
import gemini_costs
from jobstore import BACKEND_DIR, log
from media import no_window

RATES_CACHE = pathlib.Path("stats") / "rates.json"

BILL_CACHE = pathlib.Path("stats") / "modal-bill.json"


def modal_cli_json(*args: str, timeout_s: float = 60.0):
    """Run `python -m modal <args> --json` and parse its output (None on any failure)."""
    try:
        done = subprocess.run([sys.executable, "-m", "modal", *args, "--json"], capture_output=True, text=True,
                              timeout=timeout_s, cwd=str(BACKEND_DIR), env={**os.environ, "PYTHONUTF8": "1"},
                              **no_window())
    except (OSError, subprocess.TimeoutExpired) as exc:
        log(f"modal {' '.join(args)} failed: {exc}")
        return None
    if done.returncode != 0:
        log(f"modal {' '.join(args)} failed: {(done.stderr or done.stdout).strip()[-300:]}")
        return None
    text = done.stdout.strip()
    try:
        return json.loads(text[text.index("{"):]) if "{" in text else None
    except ValueError:
        return None


def live_rates(data_dir: pathlib.Path, max_age_hours: float = 24.0, fetch=None) -> dict:
    """Modal's current prices (`modal billing rates`), read at most once a day and saved in
    <data>/stats/rates.json. Falls back to the built-in table in costs.py."""
    cache = data_dir / RATES_CACHE
    try:
        saved = json.loads(cache.read_text(encoding="utf-8"))
        age = time.time() - float(saved.get("fetchedAt") or 0)
        if age < max_age_hours * 3600 and saved.get("rates"):
            return saved["rates"]
    except (OSError, ValueError):
        saved = None
    raw = (fetch or (lambda: modal_cli_json("billing", "rates")))()
    parsed = costs.parse_modal_rates(raw) if isinstance(raw, dict) else None
    if parsed is None:
        if raw is not None:
            log("Modal's rate names were not recognised; using the built-in prices from costs.py")
        return (saved or {}).get("rates") or costs.builtin_rates()
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"fetchedAt": time.time(), "rates": parsed, "raw": raw}), encoding="utf-8")
    except OSError:
        pass
    return parsed


def refresh_modal_bill(data_dir: pathlib.Path, fetch=None) -> dict | None:
    """This month's bill from `modal billing summary`, saved in <data>/stats/modal-bill.json
    for the stats page. Amounts are USD strings in Modal's output; stored as numbers."""
    raw = (fetch or (lambda: modal_cli_json("billing", "summary")))()
    if not isinstance(raw, dict) or "metered_cost" not in raw:
        return None

    def number(value) -> float:
        try:
            return round(float(value), 4)
        except (TypeError, ValueError):
            return 0.0

    now = dt.datetime.now(dt.timezone.utc)
    bill = {"fetchedAt": now.isoformat().replace("+00:00", "Z"), "month": now.strftime("%Y-%m"),
            "meteredUsd": number(raw.get("metered_cost")), "billedUsd": number(raw.get("billed_cost")),
            "breakdown": {k: number(v) for k, v in (raw.get("metered_cost_breakdown") or {}).items()},
            "adjustments": {k: number(v) for k, v in (raw.get("adjustments") or {}).items()}}
    try:
        (data_dir / BILL_CACHE).parent.mkdir(parents=True, exist_ok=True)
        (data_dir / BILL_CACHE).write_text(json.dumps(bill, indent=2), encoding="utf-8")
    except OSError:
        pass
    return bill


def gemini_cost(result: dict, when=None) -> dict | None:
    """The run's Gemini calls (ranking report "usage") priced with gemini_costs.PRICES
    on the day of the run; None for a run (or an older deployment) without token counts."""
    try:
        return gemini_costs.estimate((result.get("ranking") or {}).get("usage"), when)
    except Exception as exc:  # noqa: BLE001 - the cost is best effort
        log(f"Could not price the Gemini calls: {exc}")
        return None


def job_cost(result: dict, data_dir: pathlib.Path | None = None, when=None) -> dict | None:
    """The estimated cost of this run, as stored in job.json and the stats ledger: the
    Modal containers' running time priced with Modal's live rates, split by stage
    ("usd", "stages"), and the Gemini calls' tokens ("gemini", see gemini_costs.estimate).
    "usd" stays Modal's alone, so it can be checked against Modal's bill; the run's total
    is usd + gemini.usd."""
    cost = result.get("cost")
    if result.get("usages") and data_dir is not None:
        try:
            cost = costs.estimate(result["usages"], float(result.get("orchestratorSeconds") or 0),
                                  live_rates(data_dir), orchestrator=result.get("orchestrator"))
        except Exception as exc:  # noqa: BLE001 - keep the container's own estimate
            log(f"Could not price the run with Modal's live rates: {exc}")
    if not cost or cost.get("usd") is None:
        return None
    out = {"usd": cost["usd"], "gpuSeconds": cost.get("gpuSeconds"), "gpu": result.get("gpu"),
           "containers": cost.get("containers"), "stages": cost.get("stages"),
           "rates": cost.get("rates", "built-in")}
    gemini = gemini_cost(result, when)
    if gemini:
        out["gemini"] = gemini
        log(gemini_costs.describe(gemini))
    return out
