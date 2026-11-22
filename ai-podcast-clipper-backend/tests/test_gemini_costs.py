"""Gemini token counts per call and their estimated cost (offline)."""

import datetime as dt
import json
import pathlib
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import billing  # noqa: E402
import costs  # noqa: E402
import gemini_costs  # noqa: E402
import moments  # noqa: E402
import rank_moments  # noqa: E402


def quiet(*_):
    pass


# --------------------------------------------------------------------------- #
# Prices


def test_prices_follow_the_dated_periods():
    dec = gemini_costs.price_for("gemini-3.8-flash", dt.date(2026, 12, 31))
    jan = gemini_costs.price_for("gemini-3.8-flash", "2027-01-01")
    assert (dec["input"], dec["output"], dec["cached"]) == (0.75, 3.75, 0.075)
    assert (jan["input"], jan["output"], jan["cached"]) == (1.50, 7.50, 0.15)
    assert gemini_costs.price_for("gemini-4-argon", "2026-11-30")["input"] == 2.00
    december = dt.datetime(2026, 12, 1, 3, tzinfo=dt.timezone.utc)
    assert gemini_costs.price_for("gemini-4-argon", december)["input"] == 4.00
    assert gemini_costs.price_for("gemini-3.5-flash-lite", "2030-01-01")["output"] == 2.50


def test_model_names_resolve_to_a_price():
    c = gemini_costs.canonical
    assert c("models/gemini-3.6-flash") == "gemini-3.6-flash"
    assert c("gemini-3.6-flash-001") == "gemini-3.6-flash"
    assert c("gemini-3.5-flash") == "gemini-3.6-flash"            # routed by Google, billed as 3.6
    assert c("gemini-3.7-flash") == "gemini-3.8-flash"
    assert c("gemini-3.5-flash-lite") == "gemini-3.5-flash-lite"   # not mistaken for 3.5 Flash
    assert c("gemini-3.5-flash-lite-preview-09") == "gemini-3.5-flash-lite"
    assert c("gemini-flash-latest") is None and c(None) is None


def test_call_cost_bills_thinking_as_output_and_cached_input_at_the_cache_price():
    prices = gemini_costs.price_for("gemini-3.8-flash", "2026-11-22")
    call = {"input": 20_000, "cached": 4_000, "output": 1_200, "thinking": 800}
    expected = (16_000 * 0.75 + 4_000 * 0.075 + 2_000 * 3.75) / 1e6
    assert gemini_costs.call_cost(call, prices) == pytest.approx(expected)


def test_long_prompts_use_the_long_prices():
    prices = gemini_costs.price_for("gemini-2.5-pro", "2026-11-22")
    short = gemini_costs.call_cost({"input": 200_000, "output": 0}, prices)
    long = gemini_costs.call_cost({"input": 200_001, "output": 0}, prices)
    assert short == pytest.approx(200_000 * 1.25 / 1e6) and long == pytest.approx(200_001 * 2.50 / 1e6)


def test_estimate_adds_calls_by_model_and_flags_unknown_models():
    calls = [
        {"model": "gemini-3.8-flash", "input": 15_000, "output": 1_000, "thinking": 500, "ok": True},
        {"model": "gemini-3.8-flash", "input": 14_000, "output": 1_100, "thinking": 400, "ok": True},
        {"model": "gemini-3.8-flash", "served": "gemini-3.6-flash", "input": 100, "output": 10, "ok": True},
        {"model": "gemini-9-mystery", "input": 1_000, "output": 10, "ok": False},
    ]
    cost = gemini_costs.estimate(calls, "2026-11-22")
    assert cost["calls"] == 4 and cost["tokens"] == {"input": 30_100, "cached": 0, "output": 2_120, "thinking": 900}
    assert set(cost["models"]) == {"gemini-3.8-flash", "gemini-3.6-flash", "gemini-9-mystery"}
    assert cost["models"]["gemini-3.8-flash"]["calls"] == 2
    assert cost["unpriced"] == ["gemini-9-mystery"] and cost["prices"] == gemini_costs.PRICES_CHECKED
    expected = ((29_000 * 0.75 + 3_000 * 3.75) + (100 * 0.75 + 10 * 3.75)) / 1e6
    assert cost["usd"] == pytest.approx(expected, abs=1e-6)
    assert gemini_costs.estimate([]) is None and gemini_costs.estimate(None) is None
    line = gemini_costs.describe(cost, 93.0)
    assert "4 call(s)" in line and "30,100 tokens in" in line and "₹" in line and "gemini-9-mystery" in line
    assert gemini_costs.describe(None) == "Gemini: no token counts reported"


# --------------------------------------------------------------------------- #
# Token counts from the stream


class _Meta:
    def __init__(self, prompt, out, thoughts=None, cached=None):
        self.prompt_token_count, self.candidates_token_count = prompt, out
        self.thoughts_token_count, self.cached_content_token_count = thoughts, cached


class _Chunk:
    def __init__(self, text, meta=None, version=None):
        self.text, self.usage_metadata, self.model_version = text, meta, version


def _client(chunks):
    class _Models:
        def generate_content_stream(self, model, contents, config):
            yield from chunks

    class _Client:
        models = _Models()
    return _Client()


def test_call_gemini_returns_the_token_counts_of_the_last_chunk():
    chunks = [_Chunk('[{"start": 1.0, ', _Meta(10, 1)), _Chunk('"end": 40.5}]', _Meta(15_000, 700, 300, 2_000),
                                                                  "gemini-3.8-flash")]
    out = moments._call_gemini(_client(chunks), "gemini-3.8-flash", "s", "u", None)
    assert out == [{"start": 1.0, "end": 40.5}]
    assert out.usage == {"input": 15_000, "cached": 2_000, "output": 700, "thinking": 300,
                         "served": "gemini-3.8-flash"}


def test_a_billed_answer_that_is_not_json_keeps_its_counts():
    with pytest.raises(moments.ResponseFormatError) as raised:
        moments._call_gemini(_client([_Chunk("Sorry.", _Meta(900, 5))]), "m", "s", "u", None)
    assert raised.value.usage == {"input": 900, "cached": 0, "output": 5, "thinking": 0}


def test_no_metadata_means_no_usage():
    out = moments._call_gemini(_client([_Chunk("[]")]), "m", "s", "u", None)
    assert out == [] and out.usage is None
    assert moments.token_usage(None) is None and moments.token_usage(_Meta(0, 0)) is None
    assert moments.token_usage({"prompt_token_count": 5})["input"] == 5


def test_generate_json_records_every_answered_call(monkeypatch):
    monkeypatch.setattr(moments, "GEMINI_MODELS", ["primary", "backup"])
    monkeypatch.setattr(moments, "GEMINI_BUSY_RETRIES", 0)
    attempts = []

    def fake(client, m, system, user, schema, timeout_s=None, thinking=None, deadline=None):
        attempts.append(m)
        if m == "primary":
            error = moments.ResponseFormatError("not json")
            error.usage = {"input": 100, "cached": 0, "output": 3, "thinking": 0}
            raise error
        answer = moments.Answer([{"start": 1, "end": 40}])
        answer.usage = {"input": 200, "cached": 0, "output": 50, "thinking": 20}
        return answer
    monkeypatch.setattr(moments, "_call_gemini", fake)
    report = {}
    moments._generate_json(None, "primary", "s", "u", object(), report=report, log=quiet, sleep=lambda *_: None)
    # primary: the schema answer and the plain-JSON retry both failed to parse (billed), then backup.
    assert attempts == ["primary", "primary", "backup"]
    assert [(u["model"], u["ok"]) for u in report["usage"]] == [("primary", False), ("primary", False),
                                                                ("backup", True)]


def test_plain_lists_from_older_fakes_record_nothing(monkeypatch):
    monkeypatch.setattr(moments, "GEMINI_MODELS", ["primary"])
    monkeypatch.setattr(moments, "_call_gemini", lambda *a, **k: [{"start": 1, "end": 40}])
    report = {}
    moments._generate_json(None, "primary", "s", "u", object(), report=report, log=quiet)
    assert "usage" not in report


# --------------------------------------------------------------------------- #
# The run's cost (runner) and the comparison report


def test_job_cost_adds_gemini_next_to_modal(tmp_path, monkeypatch):
    monkeypatch.setattr(billing, "live_rates", lambda data_dir: costs.builtin_rates())
    usage = [{"model": "gemini-3.8-flash", "input": 20_000, "cached": 0, "output": 1_000, "thinking": 1_000,
              "ok": True}]
    result = {"usages": [{"container": "r", "gpu": "NVIDIA A10", "seconds": 60, "stage": "render"}],
              "orchestratorSeconds": 10, "ranking": {"usage": usage}}
    cost = billing.job_cost(result, tmp_path, when="2026-11-22")
    modal_only = billing.job_cost({**result, "ranking": {}}, tmp_path)
    assert cost["usd"] == modal_only["usd"]                     # usd stays Modal's (checked against its bill)
    assert cost["gemini"]["usd"] == pytest.approx((20_000 * 0.75 + 2_000 * 3.75) / 1e6)
    assert "gemini" not in modal_only
    assert billing.job_cost(result, tmp_path, when="2027-01-02")["gemini"]["usd"] == pytest.approx(
        2 * cost["gemini"]["usd"])


def test_comparison_report_shows_tokens_and_cost(tmp_path):
    report = {"usage": [{"model": "gemini-3.6-flash", "input": 12_345, "cached": 0, "output": 600,
                         "thinking": 400, "ok": True}]}
    rows = rank_moments._gemini_rows(report, 93.0)
    assert "12,345 in" in rows and "Gemini cost" in rows and "₹" in rows
    assert "not recorded" in rank_moments._gemini_rows({}, None)
    (tmp_path / "stats").mkdir()
    (tmp_path / "stats" / "fx.json").write_text(json.dumps({"rate": 92.9, "date": "2026-11-21",
                                                            "fetchedAt": "2020-01-01T00:00:00Z"}))
    assert rank_moments.usd_inr(tmp_path) == 92.9               # the saved rate, no network call
    assert rank_moments.usd_inr(tmp_path / "none") is None
