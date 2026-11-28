"""Live Modal rates, stage costs, the rupee rate, the Modal bill and the app's settings (offline)."""

import datetime as dt
import json
import pathlib
import sys
import time

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import billing  # noqa: E402
import costs  # noqa: E402
import fx  # noqa: E402
import jobstore  # noqa: E402
import run_history  # noqa: E402
import settings  # noqa: E402


def quiet(*_):
    pass


# --------------------------------------------------------------------------- #
# Modal's live rates
# --------------------------------------------------------------------------- #

def test_parse_modal_rates_per_hour_and_per_second():
    per_hour = {"cpu": "0.0473", "cpu_sandbox": "0.1419", "memory": "0.008", "gpu_nvidia_a10g": "1.10",
                "gpu_nvidia_a100_80gb": "2.50", "gpu_nvidia_a100_40gb": "2.10", "gpu_nvidia_l40s": "1.95",
                "volumes": "0.09", "network_egress": "0.04"}
    rates = costs.parse_modal_rates(per_hour)
    assert rates["source"] == "modal"
    assert abs(rates["gpu"]["A10"] - 1.10 / 3600) < 1e-9
    assert abs(rates["gpu"]["A100-80GB"] - 2.50 / 3600) < 1e-9 and abs(rates["gpu"]["A100-40GB"] - 2.10 / 3600) < 1e-9
    assert abs(rates["cpu"] - 0.0473 / 3600) < 1e-9 and abs(rates["memory"] - 0.008 / 3600) < 1e-9

    per_second = {"CPU": "0.0000131", "Memory": "0.00000222", "Nvidia A10G": "0.000306"}
    assert costs.parse_modal_rates(per_second)["gpu"]["A10"] == 0.000306


def test_parse_modal_rates_gives_up_on_unknown_names():
    assert costs.parse_modal_rates({"something": "1.0"}) is None
    assert costs.parse_modal_rates({"cpu": "0.0473", "memory": "0.008"}) is None      # no A10
    assert costs.parse_modal_rates({"cpu": "47", "memory": "0.008", "a10": "1.1"}) is None   # implausible CPU


def test_estimate_splits_cost_by_stage_and_uses_given_rates():
    usages = [
        {"container": "t", "gpu": "NVIDIA A10", "cpu": 0.125, "memoryGiB": 0.125, "seconds": 30, "enterSeconds": 10,
         "scaledown": 20, "stage": "transcribe"},
        {"container": "r1", "gpu": "NVIDIA A10", "cpu": 4, "memoryGiB": 8, "seconds": 60, "scaledown": 20,
         "stage": "render"},
        {"container": "r2", "gpu": "NVIDIA A10", "cpu": 4, "memoryGiB": 8, "seconds": 55, "scaledown": 20},
    ]
    built_in = costs.estimate(usages, orchestrator_seconds=200)
    assert built_in["rates"] == "built-in" and set(built_in["stages"]) == set(costs.STAGES)
    assert abs(sum(built_in["stages"].values()) - built_in["usd"]) < 1e-3
    assert built_in["stages"]["render"] > built_in["stages"]["transcribe"] > 0
    assert built_in["stages"]["rank"] > 0                                   # the job's own CPU container
    doubled = costs.builtin_rates()
    doubled["gpu"] = {k: v * 2 for k, v in doubled["gpu"].items()}
    doubled["source"] = "modal"
    live = costs.estimate(usages, orchestrator_seconds=200, rates=doubled)
    assert live["rates"] == "modal" and live["stages"]["render"] > built_in["stages"]["render"] * 1.5


def test_live_rates_are_cached_and_fall_back(tmp_path, monkeypatch):
    calls = []

    def fetch():
        calls.append(1)
        return {"cpu": "0.0473", "memory": "0.008", "gpu_a10g": "1.10"}

    first = billing.live_rates(tmp_path, fetch=fetch)
    second = billing.live_rates(tmp_path, fetch=fetch)
    assert first["source"] == "modal" and second == first and len(calls) == 1
    assert billing.live_rates(tmp_path / "other", fetch=lambda: None)["source"] == "built-in"
    assert billing.live_rates(tmp_path / "x", fetch=lambda: {"weird": "1"})["source"] == "built-in"


def test_job_cost_prices_usages_with_live_rates(tmp_path, monkeypatch):
    monkeypatch.setattr(billing, "live_rates", lambda data_dir: costs.builtin_rates())
    result = {"gpu": "A10", "cost": {"usd": 9.99},
              "usages": [{"container": "r", "gpu": "NVIDIA A10", "seconds": 60, "scaledown": 20, "stage": "render"}],
              "orchestratorSeconds": 100}
    cost = billing.job_cost(result, tmp_path)
    assert cost["usd"] < 1 and cost["stages"]["render"] > 0 and cost["rates"] == "built-in"
    assert billing.job_cost({"cost": {"usd": 0.05}}, tmp_path)["usd"] == 0.05      # an older deployment


# --------------------------------------------------------------------------- #
# Modal bill
# --------------------------------------------------------------------------- #

def test_refresh_modal_bill_saves_numbers(tmp_path):
    raw = {"metered_cost": "0.30", "billed_cost": "0.00",
           "adjustments": {"credits": "-0.27", "network_egress_allowance": "-0.01", "free_storage": "-0.02"},
           "metered_cost_breakdown": {"deployed_apps": "0.27", "volumes": "0.02", "network_egress": "0.01"}}
    bill = billing.refresh_modal_bill(tmp_path, fetch=lambda: raw)
    saved = json.loads((tmp_path / "stats" / "modal-bill.json").read_text())
    assert saved == bill and bill["meteredUsd"] == 0.3 and bill["billedUsd"] == 0.0
    assert bill["breakdown"]["deployed_apps"] == 0.27 and bill["adjustments"]["credits"] == -0.27
    assert billing.refresh_modal_bill(tmp_path, fetch=lambda: None) is None


# --------------------------------------------------------------------------- #
# USD -> INR
# --------------------------------------------------------------------------- #

def test_usd_inr_uses_first_source_and_caches(tmp_path):
    calls = []

    def frankfurter():
        calls.append("f")
        return {"rate": 94.04, "date": "2026-11-14", "source": "frankfurter"}

    now = dt.datetime(2026, 11, 14, 10, tzinfo=dt.timezone.utc)
    first = fx.usd_inr(tmp_path, now=now, sources=(frankfurter,), log=quiet)
    again = fx.usd_inr(tmp_path, now=now + dt.timedelta(hours=1), sources=(frankfurter,), log=quiet)
    assert first["rate"] == 94.04 and again["rate"] == 94.04 and calls == ["f"]
    fx.usd_inr(tmp_path, now=now + dt.timedelta(hours=7), sources=(frankfurter,), log=quiet)
    assert calls == ["f", "f"]                                           # older than 6 hours: fetched again


def test_usd_inr_falls_back_then_uses_the_stale_rate(tmp_path):
    def down():
        raise OSError("offline")

    def backup():
        return {"rate": 93.9, "date": "2026-11-14", "source": "exchangerate-api"}

    now = dt.datetime(2026, 11, 14, tzinfo=dt.timezone.utc)
    assert fx.usd_inr(tmp_path, now=now, sources=(down, backup), log=quiet)["source"] == "exchangerate-api"
    stale = fx.usd_inr(tmp_path, now=now + dt.timedelta(days=2), sources=(down,), log=quiet)
    assert stale["rate"] == 93.9 and stale["stale"] is True
    assert fx.usd_inr(tmp_path / "none", now=now, sources=(down,), log=quiet) is None


def test_frankfurter_and_open_er_parsing():
    v2 = fx.from_frankfurter(get=lambda url: [{"date": "2026-11-14", "base": "USD", "quote": "INR", "rate": 94.04}])
    assert v2 == {"rate": 94.04, "date": "2026-11-14", "source": "frankfurter"}
    answers = {fx.FRANKFURTER_URL: [], fx.FRANKFURTER_V1_URL: {"date": "2026-11-13", "rates": {"INR": 94.25}}}
    assert fx.from_frankfurter(get=answers.get)["rate"] == 94.25
    er = fx.from_open_er(get=lambda url: {"result": "success", "time_last_update_unix": 1794614551,
                                          "rates": {"INR": 94.1}})
    assert er["rate"] == 94.1 and er["source"] == "exchangerate-api"
    assert fx.from_open_er(get=lambda url: {"result": "error"}) is None


# --------------------------------------------------------------------------- #
# The app's settings
# --------------------------------------------------------------------------- #

def test_user_settings_override_and_clamp(tmp_path, monkeypatch):
    for name in ("MOST_REPLAYED", "SPONSORBLOCK", "REPLAY_WEIGHT", "MAX_CLIPS", "END_CARD"):
        monkeypatch.setattr(settings, name, getattr(settings, name))
    (tmp_path / "settings.json").write_text(json.dumps(
        {"maxClips": 12, "mostReplayed": False, "sponsorBlock": False, "replayWeight": 0.9, "currency": "inr",
         "endCard": True}))
    applied = settings.apply_user_settings(tmp_path)
    assert applied == {"maxClips": 8, "mostReplayed": False, "sponsorBlock": False, "replayWeight": 0.6,
                       "endCard": True}
    (tmp_path / "settings.json").write_text(json.dumps({"maxClips": "7", "mostReplayed": "no"}))
    assert settings.apply_user_settings(tmp_path)["maxClips"] == 8             # bad values keep the last ones


def test_record_run_stores_failed_stage_rate_and_prompt(tmp_path, monkeypatch):
    monkeypatch.setattr(fx, "usd_inr", lambda data_dir, log=print: {"rate": 94.04, "date": "2026-11-14"})
    job_dir = tmp_path / "jobs" / "20261114-100000-aaaaaa"
    job_dir.mkdir(parents=True)
    (job_dir / "job.json").write_text(json.dumps({"id": job_dir.name, "source": "youtube", "title": "t", "clips": []}))
    job = jobstore.Job(job_dir)
    [run] = run_history.record_run(job, mode="full", status="failed", started=time.time() - 30, timings={"total": 30.0},
                              failed_stage="rendering")
    assert run["failedStage"] == "rendering" and run["usdInr"]["rate"] == 94.04
    assert run["promptVersion"] == run_history.prompt_version() and "clipSources" not in run


def test_clip_sources_counts_hotspots_and_sponsor_minutes(tmp_path):
    job_dir = tmp_path / "jobs" / "20261114-100000-aaaaaa"
    job_dir.mkdir(parents=True)
    replay_record = {"available": True, "peaks": [{"center": 1}, {"center": 2}],
                     "sponsor": {"segments": [{"start": 0, "end": 28.4, "category": "preview"},
                                              {"start": 600, "end": 660, "category": "sponsor"},
                                              {"start": 700, "end": 730, "category": "sponsor"}], "highlight": None}}
    (job_dir / "job.json").write_text(json.dumps({"id": job_dir.name, "source": "youtube", "replay": replay_record}))
    details = [{"peak": {"id": "H1"}, "audience": 90.0}, {"peak": None, "audience": 50.0}, {"peak": None}]
    sources = run_history.clip_sources(jobstore.Job(job_dir), details, {"replay": {"droppedForSponsor": 3}})
    assert sources == {"graph": True, "hotspots": 2, "clips": 3, "clipsOnHotspot": 1, "audienceAvg": 70.0,
                       "sponsorBlock": True, "skippedSeconds": {"preview": 28.4, "sponsor": 90.0},
                       "droppedForSponsor": 3}
