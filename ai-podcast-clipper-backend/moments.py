"""
Viral-moment selection with Gemini: pick the BEST clips, not the first ones.

Pipeline
    1. Words -> sentence lines "[start-end] text" (much smaller and easier for
       Gemini to reason over than per-word JSON).
    2. Long videos are split into overlapping windows; Gemini proposes and scores
       candidates in each window (in parallel), using `prompts/viral_moments.md`
       as the system prompt and a strict JSON response schema.
    3. With more than one window, a second call re-scores all candidates
       side by side (`prompts/viral_moments_rerank.md`) so scores are comparable.
    4. Code - not the model - snaps clips to sentence boundaries, enforces the
       30-60 s length, computes a weighted score, removes overlaps and returns
       the top `max_clips` sorted by score (clip_0 = best).
    5. Gemini calls go down the GEMINI_MODELS chain quickly (one retry when a
       model is busy), stream their answers with a low thinking level, and stop
       after RANKING_TIME_LIMIT_SECONDS. If Gemini still delivers fewer than MIN_CLIPS
       moments, the rest are picked from the transcript (source="fallback"), so a
       job never ends with zero clips; the reason is recorded in `report`.

Only `google-genai`/`pydantic` are needed, imported lazily, so the pure helpers
are unit-testable without network access.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
from concurrent.futures import ThreadPoolExecutor

PROMPTS_DIR = pathlib.Path(__file__).resolve().parent / "prompts"


def _env_float(name: str, default: float | None) -> float | None:
    raw = (os.environ.get(name) or "").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


# Settings come from ai-podcast-clipper-backend/.env (forwarded to the GPU container).
MIN_CLIP_S = _env_float("CLIP_MIN_SECONDS", 30.0)
MAX_CLIP_S = _env_float("CLIP_MAX_SECONDS", 60.0)
# A clip may run this much past CLIP_MAX_SECONDS, only to finish the sentence it is in.
CLIP_GRACE_S = _env_float("CLIP_GRACE_SECONDS", 5.0)
# Breathing room around the speech: a little silence before the first word and after
# the last one (never more than half the pause to the neighbouring speech).
LEAD_IN_S = 0.3
TAIL_S = 0.6
# A pause this long between lines starts a new thought even without punctuation.
NEW_THOUGHT_PAUSE_S = 1.5
# Never return fewer clips than this (missing slots are filled from the transcript).
MIN_CLIPS = int(_env_float("MIN_CLIPS", 3))
# Models tried in order; a busy model gets one quick retry, then the next one is used.
# Check which IDs your key can use with `python rank_moments.py --list-models`.
DEFAULT_GEMINI_MODELS = "gemini-3.8-flash,gemini-3.6-flash,gemini-3.5-flash-lite"
GEMINI_MODELS = [m.strip() for m in (os.environ.get("GEMINI_MODELS") or DEFAULT_GEMINI_MODELS).split(",")
                 if m.strip()]
# Extra attempts on the same model when it answers 429/503/504 ("busy").
GEMINI_BUSY_RETRIES = max(0, int(_env_float("GEMINI_BUSY_RETRIES", 1)))
GEMINI_BUSY_WAIT_S = _env_float("GEMINI_BUSY_WAIT_SECONDS", 3.0)
# Gemini 3 thinking level for ranking: minimal | low | medium | high, empty = model default.
GEMINI_THINKING_LEVEL = (os.environ.get("GEMINI_THINKING_LEVEL", "low") or "").strip().lower() or None
# Empty = the model's default temperature (recommended for Gemini 3 models).
GEMINI_TEMPERATURE = _env_float("GEMINI_TEMPERATURE", None)
# Per-request timeout, and the total time ranking may take before the transcript fallback.
GEMINI_TIMEOUT_S = _env_float("GEMINI_TIMEOUT_SECONDS", 120.0)
RANKING_TIME_LIMIT_S = _env_float("RANKING_TIME_LIMIT_SECONDS", 240.0)


def make_client(api_key: str):
    from google import genai
    from google.genai import types

    return genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=int(GEMINI_TIMEOUT_S * 1000)))


def model_chain(primary: str | None) -> list[str]:
    chain = [primary] if primary else []
    return [m for i, m in enumerate(chain + GEMINI_MODELS) if m and m not in (chain + GEMINI_MODELS)[:i]]
# Clips a little shorter than MIN_CLIP_S are kept rather than thrown away.
MIN_CLIP_TOLERANCE_S = 25.0

SINGLE_WINDOW_MAX_S = 45 * 60   # one Gemini call up to 45 minutes of video
WINDOW_S = 30 * 60
WINDOW_OVERLAP_S = 2 * 60

# Weighted virality score (0-100) computed in code for transparent, stable ranking.
SCORE_WEIGHTS = {
    "hook": 0.25,
    "standalone": 0.20,
    "payoff": 0.20,
    "emotion": 0.15,
    "value": 0.10,
    "shareability": 0.10,
}
SCORE_KEYS = tuple(SCORE_WEIGHTS)

_SENTENCE_END = re.compile(r"[.!?…]['\")\]]*$")


# --------------------------------------------------------------------------- #
# Transcript -> lines
# --------------------------------------------------------------------------- #

def build_lines(words: list[dict], max_gap_s: float = 0.8, max_words: int = 30,
                max_line_s: float = 15.0) -> list[dict]:
    """Group timed words into sentence-like lines."""
    lines, current = [], []

    def flush():
        if current:
            lines.append({
                "start": round(current[0]["start"], 2),
                "end": round(current[-1]["end"], 2),
                "text": " ".join(w["word"] for w in current),
                # Lines also break on pauses and length, so a line can end mid-sentence.
                "ends_sentence": bool(_SENTENCE_END.search(current[-1]["word"])),
            })
            current.clear()

    for w in words:
        if w.get("start") is None or w.get("end") is None or not str(w.get("word", "")).strip():
            continue
        word = {"start": float(w["start"]), "end": float(w["end"]), "word": str(w["word"]).strip()}
        if current and (word["start"] - current[-1]["end"] > max_gap_s
                        or len(current) >= max_words
                        or word["end"] - current[0]["start"] > max_line_s):
            flush()
        current.append(word)
        if _SENTENCE_END.search(word["word"]):
            flush()
    flush()
    for i, line in enumerate(lines):
        line["starts_sentence"] = (i == 0 or lines[i - 1]["ends_sentence"]
                                   or line["start"] - lines[i - 1]["end"] >= NEW_THOUGHT_PAUSE_S)
    return lines


def _ends_sentence(line: dict) -> bool:
    return line.get("ends_sentence", bool(_SENTENCE_END.search(line["text"])))


def _starts_sentence(lines: list[dict], i: int) -> bool:
    if "starts_sentence" in lines[i]:
        return lines[i]["starts_sentence"]
    return i == 0 or _ends_sentence(lines[i - 1])


def format_lines(lines: list[dict]) -> str:
    # 0.1 s precision is plenty (clips snap to line boundaries) and ~15% fewer prompt tokens.
    return "\n".join(f"[{l['start']:.1f}-{l['end']:.1f}] {l['text']}" for l in lines)


def split_windows(lines: list[dict], window_s: float = WINDOW_S, overlap_s: float = WINDOW_OVERLAP_S,
                  single_max_s: float = SINGLE_WINDOW_MAX_S) -> list[list[dict]]:
    if not lines:
        return []
    total = lines[-1]["end"] - lines[0]["start"]
    if total <= single_max_s:
        return [lines]
    windows, start = [], lines[0]["start"]
    while start < lines[-1]["end"]:
        end = start + window_s
        chunk = [l for l in lines if l["start"] >= start and l["start"] < end]
        if chunk:
            windows.append(chunk)
        start = end - overlap_s
    return windows


def format_context(context: dict | None) -> str:
    if not context:
        return ""
    parts = []
    for key, label in (("title", "Title"), ("channel", "Channel"), ("description", "Description")):
        value = (context.get(key) or "").strip()
        if value:
            parts.append(f"{label}: {value[:1500]}")
    return "Video context\n" + "\n".join(parts) + "\n\n" if parts else ""


# --------------------------------------------------------------------------- #
# Validation, scoring, ranking (pure)
# --------------------------------------------------------------------------- #

def weighted_score(scores: dict) -> float:
    total = 0.0
    for key, weight in SCORE_WEIGHTS.items():
        try:
            value = float(scores.get(key, 0))
        except (TypeError, ValueError):
            value = 0.0
        total += weight * min(max(value, 0.0), 10.0)
    return round(total * 10, 1)


def snap_to_lines(start: float, end: float, lines: list[dict], min_s: float = MIN_CLIP_S,
                  max_s: float = MAX_CLIP_S, grace_s: float = CLIP_GRACE_S) -> tuple[float, float] | None:
    """Turn a proposed range into a clean cut: begin where a sentence begins, end
    where a sentence ends, stay within min_s..max_s (up to grace_s longer, only to
    finish the sentence in progress at max_s), then add a little breathing room."""
    if not lines or end <= start:
        return None
    n = len(lines)
    first = min(range(n), key=lambda i: abs(lines[i]["start"] - start))
    last = min(range(n), key=lambda i: abs(lines[i]["end"] - end))
    if last < first:
        return None
    hard_max = max_s + grace_s

    def length(i: int, j: int) -> float:
        return lines[j]["end"] - lines[i]["start"]

    # 1. Begin on a sentence start: step back up to 3 lines (keeps the set-up),
    #    otherwise forward to the next sentence start.
    if not _starts_sentence(lines, first):
        back = next((i for i in range(first - 1, max(first - 4, -1), -1) if _starts_sentence(lines, i)), None)
        if back is not None and length(back, last) <= hard_max:
            first = back
        else:
            first = next((i for i in range(first + 1, min(first + 4, last + 1)) if _starts_sentence(lines, i)),
                         first)

    # 2. End on a sentence end, as close as possible to the proposed end. Past max_s
    #    only when the final sentence was already under way at the max_s mark.
    def acceptable(j: int) -> bool:
        if not _ends_sentence(lines[j]):
            return False
        d = length(first, j)
        return d <= max_s or (d <= hard_max and lines[j]["start"] - lines[first]["start"] < max_s)

    reach = [j for j in range(first, n) if length(first, j) <= hard_max]
    ends = [j for j in reach if acceptable(j)]
    pool = [j for j in ends if length(first, j) >= min_s] or ends
    if pool:
        last = min(pool, key=lambda j: (abs(lines[j]["end"] - end), length(first, j)))
    else:
        # No sentence end in reach (an unpunctuated transcript): fit whole lines.
        last = max(first, min(last, reach[-1] if reach else first))
        while last > first and length(first, last) > max_s:
            last -= 1
        while (length(first, last) < min_s and last + 1 < n
               and length(first, last + 1) <= max_s):
            last += 1

    s, e = lines[first]["start"], lines[last]["end"]
    if e - s > hard_max:  # a single very long line
        e = s + max_s
    if e - s < min(MIN_CLIP_TOLERANCE_S, min_s):
        return None

    # 3. Breathing room: never cut on the first or last sound.
    prev_end = lines[first - 1]["end"] if first > 0 else 0.0
    s = max(0.0, s - min(LEAD_IN_S, (s - prev_end) / 2))
    if last + 1 < n:
        e += max(0.0, min(TAIL_S, (lines[last + 1]["start"] - e) / 2))
    else:
        e += TAIL_S
    return round(s, 3), round(e, 3)


def normalize_candidates(raw: list[dict], lines: list[dict], min_s: float = MIN_CLIP_S,
                         max_s: float = MAX_CLIP_S) -> list[dict]:
    out = []
    for c in raw or []:
        try:
            start, end = float(c["start"]), float(c["end"])
        except (KeyError, TypeError, ValueError):
            continue
        snapped = snap_to_lines(start, end, lines, min_s, max_s)
        if not snapped:
            continue
        source = c.get("scores") if isinstance(c.get("scores"), dict) else c
        scores = {k: _int_score(source.get(k)) for k in SCORE_KEYS}
        out.append({
            "start": snapped[0],
            "end": snapped[1],
            "title": str(c.get("title") or "").strip()[:120],
            "hook": str(c.get("hook") or "").strip()[:300],
            "reason": str(c.get("reason") or "").strip()[:500],
            "scores": scores,
            "score": weighted_score(scores),
            "source": c.get("source", "gemini"),
        })
    return out


def _int_score(value) -> int:
    try:
        return int(min(max(round(float(value)), 0), 10))
    except (TypeError, ValueError):
        return 0


def overlaps(a: dict, b: dict, tolerance_s: float = 1.0) -> bool:
    return a["start"] < b["end"] - tolerance_s and b["start"] < a["end"] - tolerance_s


def overlap_ratio(a: dict, b: dict) -> float:
    inter = min(a["end"], b["end"]) - max(a["start"], b["start"])
    shorter = min(a["end"] - a["start"], b["end"] - b["start"])
    return max(inter, 0.0) / shorter if shorter > 0 else 0.0


def select_top(candidates: list[dict], max_clips: int) -> list[dict]:
    """Greedy best-first selection without overlaps; ties broken by hook, not by time."""
    ranked = sorted(candidates, key=lambda c: (c["score"], c["scores"].get("hook", 0),
                                               c["scores"].get("payoff", 0)), reverse=True)
    chosen: list[dict] = []
    for cand in ranked:
        if all(not overlaps(cand, other) for other in chosen):
            chosen.append(cand)
        if len(chosen) >= max_clips:
            break
    return [dict(c, rank=i + 1, duration=round(c["end"] - c["start"], 2)) for i, c in enumerate(chosen)]


def text_between(lines: list[dict], start: float, end: float) -> str:
    return " ".join(l["text"] for l in lines if l["start"] >= start - 0.01 and l["end"] <= end + 0.01)


# --------------------------------------------------------------------------- #
# Transcript-only fallback (used only when Gemini cannot deliver MIN_CLIPS)
# --------------------------------------------------------------------------- #

_PROMO = re.compile(r"\b(sponsor\w*|subscribe|promo code|discount code|link in (the )?description|"
                    r"free trial|sign up|check out|brought to you|smash that|like and)\b", re.I)
_HOOKY = re.compile(r"\b(why|how|what|never|always|secret|mistake|truth|biggest|worst|best|"
                    r"actually|nobody|everyone|crazy|wrong|surprising|lesson|advice)\b", re.I)


def heuristic_candidates(lines: list[dict], min_s: float = MIN_CLIP_S, max_s: float = MAX_CLIP_S) -> list[dict]:
    """Score every sentence-aligned window with simple, explainable signals:
    a question or strong opener first, a finished sentence last, dense speech,
    no promo talk, not in the first/last 45 s. Scores are capped at 45/100 so
    any Gemini pick always ranks above a fallback pick."""
    if not lines:
        return []
    total_end = lines[-1]["end"]
    out = []
    for i, first in enumerate(lines):
        if not _starts_sentence(lines, i):
            continue
        j = i
        while j + 1 < len(lines) and lines[j + 1]["end"] - first["start"] <= max_s:
            j += 1
        start, end = first["start"], lines[j]["end"]
        if end - start < min(MIN_CLIP_TOLERANCE_S, min_s):
            continue
        text = " ".join(l["text"] for l in lines[i:j + 1])
        words = len(text.split())
        signal = 0.0
        signal += 3.0 if first["text"].rstrip().endswith("?") else 0.0
        signal += 1.5 if _HOOKY.search(first["text"]) else 0.0
        signal += 1.0 if _SENTENCE_END.search(lines[j]["text"]) else 0.0
        density = words / max(end - start, 1.0)
        signal += min(density, 3.5) / 3.5 * 2.0                            # dense speech, up to +2
        signal += 0.5 * min(text.count("?"), 3)
        signal += 1.0 - min(abs((end - start) - 45.0) / 30.0, 1.0)          # close to 45 s, up to +1
        signal += 0.6 * len(_HOOKY.findall(text)) / max(words / 25.0, 1.0)  # strong words per sentence
        signal += 0.2 * min(len(first["text"].split()), 12) / 12 if first["text"].rstrip().endswith("?") else 0.0
        signal -= 4.0 if _PROMO.search(text) else 0.0
        signal -= 3.0 if start < 45 or end > total_end - 45 else 0.0
        snapped = snap_to_lines(start, end, lines, min_s, max_s)
        if not snapped:
            continue
        out.append({"start": snapped[0], "end": snapped[1],
                    "title": first["text"][:80], "hook": first["text"][:300],
                    "reason": "Picked from the transcript because Gemini returned too few moments.",
                    "scores": {}, "signal": signal, "source": "fallback"})
    # Scale relative to this video's best window (0-45) so scores separate instead of tying.
    top = max((c["signal"] for c in out), default=0.0)
    for c in out:
        c["score"] = round(45.0 * max(c.pop("signal"), 0.0) / top, 1) if top > 0 else 0.0
    return out


# --------------------------------------------------------------------------- #
# Gemini
# --------------------------------------------------------------------------- #

def _load_prompt(name: str, **values) -> str:
    text = (PROMPTS_DIR / name).read_text(encoding="utf-8")
    for key, value in values.items():
        text = text.replace("{" + key + "}", str(value))
    return text


def _schemas():
    from pydantic import BaseModel

    class Candidate(BaseModel):
        start: float
        end: float
        title: str
        hook: str
        hook_score: int
        standalone: int
        payoff: int
        emotion: int
        value: int
        shareability: int
        reason: str

    class Rescore(BaseModel):
        id: int
        hook: int
        standalone: int
        payoff: int
        emotion: int
        value: int
        shareability: int
        reason: str

    return Candidate, Rescore


class RankingTimeout(Exception):
    """The overall ranking time limit (RANKING_TIME_LIMIT_SECONDS) ran out."""


class ResponseFormatError(Exception):
    """Gemini answered, but not with a parseable JSON list."""


def classify_error(exc: Exception) -> str:
    """busy (try once more, then next model) | thinking | format | fatal (next model)."""
    if isinstance(exc, RankingTimeout):
        return "timeout"
    if isinstance(exc, ResponseFormatError) or type(exc).__name__ in ("JSONDecodeError", "ValidationError"):
        return "format"
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    text = str(exc)
    if code in (408, 429, 500, 502, 503, 504) or any(
            s in text for s in ("RESOURCE_EXHAUSTED", "UNAVAILABLE", "DEADLINE_EXCEEDED", "high demand")) \
            or "timeout" in type(exc).__name__.lower() or "timed out" in text.lower():
        return "busy"
    if code == 400 and "thinking" in text.lower():
        return "thinking"
    if code == 400 and any(s in text.lower() for s in ("schema", "response_mime_type", "json")):
        return "format"
    return "fatal"


def _call_gemini(client, model: str, system: str, user: str, schema, timeout_s: float | None = None,
                 thinking: str | None = None, deadline: float | None = None) -> list[dict]:
    """One streamed request. `schema=None` asks for plain JSON. Streaming keeps long answers
    from hitting the server-side deadline; `deadline` aborts it when ranking runs out of time."""
    import time

    from google.genai import types

    config = {"system_instruction": system, "response_mime_type": "application/json",
              # No tools are used; disabling AFC also silences the SDK's AFC notice.
              "automatic_function_calling": types.AutomaticFunctionCallingConfig(disable=True)}
    if timeout_s:
        config["http_options"] = types.HttpOptions(timeout=int(max(timeout_s, 5) * 1000))
    if schema is not None:
        config["response_schema"] = schema
    if GEMINI_TEMPERATURE is not None:      # Gemini 3 models are tuned for their default (1.0)
        config["temperature"] = GEMINI_TEMPERATURE
    if thinking:
        config["thinking_config"] = types.ThinkingConfig(thinking_level=types.ThinkingLevel[thinking.upper()])
    parts = []
    for chunk in client.models.generate_content_stream(model=model, contents=user,
                                                       config=types.GenerateContentConfig(**config)):
        if chunk.text:
            parts.append(chunk.text)
        if deadline is not None and time.monotonic() > deadline:
            raise RankingTimeout(f"ranking time limit reached while {model} was answering")
    raw = "".join(parts)
    items = parse_json_list(raw)
    if not items and raw.strip() and raw.strip() not in ("[]", "{}"):
        raise ResponseFormatError(f"{model} returned text that is not a JSON list: {raw[:120]!r}")
    return items


def _generate_json(client, model: str, system: str, user: str, schema, report: dict | None = None,
                   log=print, sleep=None) -> list[dict]:
    """Try each model in `model_chain(model)`:
    busy (429/503/504/timeout) -> GEMINI_BUSY_RETRIES quick retries, then the next model;
    bad/empty JSON             -> one retry as plain JSON on the same model;
    thinking not supported     -> retry without the thinking setting;
    anything else              -> the next model.
    The whole call respects report["_deadline"] (RANKING_TIME_LIMIT_SECONDS)."""
    import time

    sleep = sleep or time.sleep
    report = report if report is not None else {}
    deadline = report.get("_deadline")
    last_exc: Exception | None = None

    for m in model_chain(model):
        use_schema, plain_tried, busy_left, thinking = True, False, GEMINI_BUSY_RETRIES, GEMINI_THINKING_LEVEL
        while True:
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining < 5:
                report["timedOut"] = True
                raise RankingTimeout("ranking time limit reached")
            timeout_s = GEMINI_TIMEOUT_S if remaining is None else min(GEMINI_TIMEOUT_S, remaining)
            mode = "schema" if use_schema else "plain JSON"
            log(f"Calling Gemini {m} ({mode}, thinking {thinking or 'default'}, timeout {timeout_s:.0f}s)")
            started = time.monotonic()
            try:
                items = _call_gemini(client, m, system, user, schema if use_schema else None,
                                     timeout_s, thinking, deadline)
            except Exception as exc:  # noqa: BLE001
                took = time.monotonic() - started
                kind = classify_error(exc)
                if kind == "timeout":
                    report["timedOut"] = True
                    raise
                last_exc = exc
                message = f"{m} ({mode}, {took:.0f}s, {kind}): {type(exc).__name__}: {str(exc)[:240]}"
                log(f"Gemini call failed - {message}")
                report.setdefault("errors", []).append(message)
                if kind == "busy" and busy_left > 0:
                    busy_left -= 1
                    sleep(GEMINI_BUSY_WAIT_S)
                    continue
                if kind == "thinking" and thinking:
                    thinking = None
                    continue
                if kind == "format" and not plain_tried:
                    use_schema, plain_tried = False, True
                    continue
                break                                        # next model
            took = round(time.monotonic() - started, 1)
            log(f"Gemini {m} answered in {took}s with {len(items)} item(s)")
            report.setdefault("calls", []).append({"model": m, "mode": mode, "items": len(items), "seconds": took})
            if items:
                report["modelUsed"] = m
                return items
            if not plain_tried:                              # empty answer: one plain-JSON retry
                use_schema, plain_tried = False, True
                continue
            break
    if last_exc is not None:
        raise last_exc
    return []


def parse_json_list(raw: str) -> list[dict]:
    cleaned = raw.strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", cleaned, re.S)
        if not match:
            return []
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return []
    if isinstance(data, dict):  # {"candidates": [...]} in plain-JSON mode
        data = next((v for v in data.values() if isinstance(v, list)), [])
    return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []


def propose_candidates(client, model: str, lines: list[dict], n_candidates: int, context: dict | None,
                       min_s: float, max_s: float, generate=_generate_json, report: dict | None = None,
                       log=print) -> list[dict]:
    Candidate, _ = _schemas()
    system = _load_prompt("viral_moments.md", n_candidates=n_candidates, min_s=int(min_s), max_s=int(max_s),
                          grace_s=int(CLIP_GRACE_S))
    user = format_context(context) + "Transcript\n" + format_lines(lines)
    raw = generate(client, model, system, user, list[Candidate], report=report, log=log)
    for c in raw:  # schema uses hook_score to avoid clashing with the hook text
        c["scores"] = {"hook": c.get("hook_score", c.get("hook") if isinstance(c.get("hook"), (int, float)) else 0),
                       **{k: c.get(k) for k in SCORE_KEYS if k != "hook"}}
    valid = normalize_candidates(raw, lines, min_s, max_s)
    if report is not None:
        report["returned"] = report.get("returned", 0) + len(raw)
        report["valid"] = report.get("valid", 0) + len(valid)
    return valid


def rerank_candidates(client, model: str, candidates: list[dict], lines: list[dict],
                      generate=_generate_json, report: dict | None = None, log=print) -> list[dict]:
    _, Rescore = _schemas()
    system = _load_prompt("viral_moments_rerank.md")
    listing = "\n\n".join(
        f"id {i}: [{c['start']:.2f}-{c['end']:.2f}] {text_between(lines, c['start'], c['end'])}"
        for i, c in enumerate(candidates))
    rescored = {r.get("id"): r for r in generate(client, model, system, "Candidates\n\n" + listing,
                                                 list[Rescore], report=report, log=log)}
    out = []
    for i, c in enumerate(candidates):
        r = rescored.get(i)
        if r:
            scores = {k: _int_score(r.get(k)) for k in SCORE_KEYS}
            c = dict(c, scores=scores, score=weighted_score(scores), reason=r.get("reason") or c["reason"])
        out.append(c)
    return out


def select_moments(words: list[dict], max_clips: int, client, model: str, context: dict | None = None,
                   min_s: float | None = None, max_s: float | None = None, generate=_generate_json,
                   log=print, report: dict | None = None, min_clips: int | None = None) -> list[dict]:
    """Return up to `max_clips` ranked, non-overlapping moments (best first).

    Never returns fewer than `min_clips` (MIN_CLIPS, default 3) when the video is
    long enough: missing slots are filled from the transcript and marked
    source="fallback". `report` (a dict) receives counts and any Gemini errors.
    """
    min_s = MIN_CLIP_S if min_s is None else min_s
    max_s = MAX_CLIP_S if max_s is None else max_s
    min_clips = min(MIN_CLIPS if min_clips is None else min_clips, max_clips)
    report = report if report is not None else {}
    import time

    report.update({"model": model, "models": model_chain(model), "errors": [], "returned": 0, "valid": 0,
                   "fallback": 0, "timedOut": False,
                   "_deadline": time.monotonic() + RANKING_TIME_LIMIT_S if RANKING_TIME_LIMIT_S else None})
    ranking_started = time.monotonic()

    lines = build_lines(words)
    if not lines:
        report["reason"] = "empty transcript"
        report.pop("_deadline", None)
        return []
    windows = split_windows(lines)
    # 8 candidates for a 5-clip job: enough to choose from, ~20% less output than 10.
    per_window = max(8, max_clips + 3) if len(windows) == 1 else max(5, max_clips)
    log(f"Moment selection: {len(lines)} lines, {len(windows)} window(s), "
        f"{per_window} candidates per window, model {model}")

    def run(window):
        try:
            return propose_candidates(client, model, window, per_window, context, min_s, max_s,
                                      generate, report, log)
        except Exception as exc:  # noqa: BLE001 - one failed window must not lose the others
            log(f"Window {window[0]['start']:.0f}-{window[-1]['end']:.0f}s failed: {exc!r}")
            return []

    with ThreadPoolExecutor(max_workers=min(4, len(windows))) as pool:
        candidates = [dict(c, source="gemini") for batch in pool.map(run, windows) for c in batch]

    # Windows overlap: drop near-duplicates, keeping the higher score.
    unique: list[dict] = []
    for cand in sorted(candidates, key=lambda c: c["score"], reverse=True):
        if all(overlap_ratio(cand, u) < 0.5 for u in unique):
            unique.append(cand)

    if len(windows) > 1 and len(unique) > max_clips:
        shortlist = sorted(unique, key=lambda c: c["score"], reverse=True)[: max(3 * max_clips, 12)]
        try:
            unique = rerank_candidates(client, model, shortlist, lines, generate, report, log)
        except Exception as exc:  # noqa: BLE001
            log(f"Re-ranking failed, using per-window scores: {exc!r}")

    chosen = select_top(unique, max_clips)
    if len(chosen) < min_clips:
        log(f"Gemini delivered {len(chosen)} usable moment(s); filling to {min_clips} from the transcript")
        filled = list(chosen)
        ranked = sorted(heuristic_candidates(lines, min_s, max_s), key=lambda c: c["score"], reverse=True)
        # First pass keeps fallback picks >= 2 min apart for variety, second pass fills any gap.
        for gap in (120.0, 0.0):
            for cand in ranked:
                if len(filled) >= min_clips:
                    break
                if all(not overlaps(cand, o) and (o.get("source") != "fallback" or
                       min(abs(cand["start"] - o["end"]), abs(o["start"] - cand["end"])) >= gap)
                       for o in filled):
                    filled.append(cand)
        # Gemini picks always rank above transcript fallbacks.
        filled.sort(key=lambda c: (c.get("source") != "fallback", c["score"]), reverse=True)
        chosen = [dict(c, rank=i + 1, duration=round(c["end"] - c["start"], 2)) for i, c in enumerate(filled)]
        report["fallback"] = sum(1 for c in chosen if c.get("source") == "fallback")

    report["chosen"] = len(chosen)
    report["seconds"] = round(time.monotonic() - ranking_started, 1)
    report.pop("_deadline", None)
    for m in chosen:
        log(f"  #{m['rank']} score {m['score']:5.1f}  {m['start']:8.2f}-{m['end']:8.2f}  "
            f"[{m.get('source', 'gemini')}] {m['title']}")
    return chosen
