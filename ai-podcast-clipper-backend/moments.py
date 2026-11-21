"""
Viral-moment selection with Gemini: pick the clips that deliver the most, whole.

Pipeline
    1. Words -> sentence lines "[start-end] text" (much smaller and easier for
       Gemini to reason over than per-word JSON).
    2. Long videos are split into overlapping windows; Gemini proposes and scores
       candidates in each window (in parallel), using `prompts/viral_moments.md`
       as the system prompt and a strict JSON response schema. Each candidate names
       its arc (question and answer, claim and reason...) and quotes its payoff line.
    3. Code - not the model - snaps clips to whole sentences, never cuts a proposal
       short (one longer than CLIP_MAX_SECONDS + grace is marked `tooLong` instead),
       checks that the quoted payoff is inside the clip (extending to it when it fits).
    4. One judge call (`prompts/viral_moments_judge.md`) re-reads every shortlisted
       clip's FINAL text with a few lines around it, re-scores all of them side by
       side, says whether each holds a complete thought and may correct a boundary;
       code validates every correction. Gemini calls per run: windows + 1.
    5. Ranking: weighted score (SCORE_PROFILE, "value" by default), complete clips
       above incomplete ones, overlaps removed, the top `max_clips` returned (clip_0 = best).
    6. YouTube jobs with a "Most replayed" graph (replay.py, fetched on the laptop):
       each replay hotspot is offered to Gemini, which returns the cleanest clip
       around it (scored on the transcript alone); hotspots it skips are cut around
       in code and scored by the judge. The final score blends Gemini's rubric with
       the audience score (REPLAY weight, 0.4 by default). Clips that run into
       SponsorBlock segments (sponsor reads, intros, previews...) are dropped.
    7. Gemini calls go down the GEMINI_MODELS chain quickly (one retry when a
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
import threading
from concurrent.futures import ThreadPoolExecutor

import replay as replay_signals

PROMPTS_DIR = pathlib.Path(__file__).resolve().parent / "prompts"


def _env_float(name: str, default: float | None) -> float | None:
    raw = (os.environ.get(name) or "").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


# Settings come from ai-podcast-clipper-backend/.env (forwarded to the GPU container).
MIN_CLIP_S = _env_float("CLIP_MIN_SECONDS", 30.0)
# The longest clip. The conversation decides the length within 30-120 s: a clip is as
# long as its thought needs, never padded to a target.
MAX_CLIP_S = _env_float("CLIP_MAX_SECONDS", 120.0)
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
# Extra attempts on the same model when it answers 500/502/503 ("busy"). A stall or a
# used-up quota (429) moves to the next model without one (see classify_error).
GEMINI_BUSY_RETRIES = max(0, int(_env_float("GEMINI_BUSY_RETRIES", 1)))
GEMINI_BUSY_WAIT_S = _env_float("GEMINI_BUSY_WAIT_SECONDS", 3.0)
def thinking_level(env: dict | None = None) -> str | None:
    """GEMINI_THINKING_LEVEL: minimal | low | medium | high; unset = low; empty (or
    "default", which is how an empty value reaches the container) = the model's default."""
    env = os.environ if env is None else env
    level = (env.get("GEMINI_THINKING_LEVEL", "low") or "").strip().lower()
    return None if level in ("", "default") else level


# Gemini 3 thinking level for ranking (see thinking_level).
GEMINI_THINKING_LEVEL = thinking_level()
# Empty = the model's default temperature (recommended for Gemini 3 models).
GEMINI_TEMPERATURE = _env_float("GEMINI_TEMPERATURE", None)
# Per-request timeout, and the total time ranking may take before the transcript fallback.
# The timeout is how long a request may go without a byte: answers stream within 38-55 s
# (measured 2026-11-21), so 75 s only ever cuts a stalled request, and a stall still
# leaves the next model most of the 240 s budget (it was 120 s: two stalls used it all).
GEMINI_TIMEOUT_S = _env_float("GEMINI_TIMEOUT_SECONDS", 75.0)
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

# Weighted score (0-100) computed in code for transparent, stable ranking. Six rubric
# parts (the app shows them); SCORE_PROFILE picks the weights:
#   value   (default) what the viewer gets by the end: payoff and value lead;
#   classic the weights before 2026-11-15, led by the hook.
SCORE_PROFILES = {
    "value": {"hook": 0.15, "standalone": 0.15, "payoff": 0.25, "emotion": 0.10, "value": 0.25,
              "shareability": 0.10},
    "classic": {"hook": 0.25, "standalone": 0.20, "payoff": 0.20, "emotion": 0.15, "value": 0.10,
                "shareability": 0.10},
}
SCORE_KEYS = ("hook", "standalone", "payoff", "emotion", "value", "shareability")


def score_profile(env: dict | None = None) -> str:
    """SCORE_PROFILE: value | classic; unset or unknown = value."""
    env = os.environ if env is None else env
    name = (env.get("SCORE_PROFILE") or "").strip().lower()
    return name if name in SCORE_PROFILES else "value"


SCORE_PROFILE = score_profile()
SCORE_WEIGHTS = SCORE_PROFILES[SCORE_PROFILE]

# The blind transcript fallback keeps short clips: it cannot tell where a thought ends.
HEURISTIC_MAX_S = 60.0
# What a proposal may say about its shape, and what the judge may say is wrong with a cut.
ARCS = ("question_answer", "claim_reason", "story_point", "lesson", "other")
ISSUES = ("none", "starts_mid_thought", "ends_before_payoff", "trails_off", "needs_context", "too_long")
# Judge call: transcript lines shown before and after each clip, and how many proposals
# longer than the cap it may try to rescue with a later start.
JUDGE_BEFORE_LINES = 3
JUDGE_AFTER_LINES = 5
JUDGE_TOO_LONG_MAX = 4

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


def build_hotspots(replay_context: dict | None) -> list[dict]:
    """Replay peaks plus the SponsorBlock highlight (when no peak is near it), ids H1, H2..."""
    if not replay_context:
        return []
    spots = [dict(p, kind="replay") for p in replay_context.get("peaks") or []]
    highlight = replay_context.get("highlight")
    if highlight is not None and all(abs(float(highlight) - p["center"]) >= replay_signals.PEAK_MERGE_S
                                     for p in spots):
        t = float(highlight)
        spots.append({"start": t, "end": t, "center": t, "value": None, "lift": None,
                      "rank": len(spots) + 1, "kind": "highlight"})
    skips = replay_context.get("skips") or []
    spots = [s for s in spots if not replay_signals.touches_sponsor(s["center"] - 1, s["center"] + 1, skips)]
    return [dict(s, id=f"H{i + 1}") for i, s in enumerate(spots)]


def format_replay(hotspots: list[dict], skips: list[dict]) -> str:
    """Hotspots and ranges to avoid, for the user message (no replay intensities:
    Gemini scores the content alone, the audience score is added in code)."""
    text = ""
    if hotspots:
        rows = []
        for h in hotspots:
            if h["end"] > h["start"]:
                rows.append(f"{h['id']}: centre {h['center']:.1f} (viewers replayed {h['start']:.1f}-{h['end']:.1f})")
            else:
                rows.append(f"{h['id']}: centre {h['center']:.1f} (marked as the highlight by viewers)")
        text += "Replay hotspots\n" + "\n".join(rows) + "\n\n"
    if skips:
        rows = [f"{s['start']:.1f}-{s['end']:.1f} ({s['category']})" for s in skips]
        text += "Leave out (sponsor reads, intros, outros, previews)\n" + "\n".join(rows) + "\n\n"
    return text


# --------------------------------------------------------------------------- #
# Validation, scoring, ranking (pure)
# --------------------------------------------------------------------------- #

def weighted_score(scores: dict, profile: str | None = None) -> float:
    weights = SCORE_PROFILES.get(profile or SCORE_PROFILE, SCORE_PROFILES["value"])
    total = 0.0
    for key, weight in weights.items():
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


def _ends_thought(lines: list[dict], k: int) -> bool:
    """Line k ends a sentence, or the next line starts a new thought (a long pause in an
    unpunctuated transcript), or it is the last line."""
    return _ends_sentence(lines[k]) or k + 1 >= len(lines) or _starts_sentence(lines, k + 1)


def _sentence_end_from(lines: list[dict], j: int, limit_s: float = 30.0) -> int:
    """The line that finishes the sentence line j is in (j itself when it ends one)."""
    k = j
    while not _ends_thought(lines, k) and lines[k + 1]["end"] - lines[j]["end"] <= limit_s:
        k += 1
    return k if _ends_thought(lines, k) else j


def _pad(lines: list[dict], first: int, last: int) -> tuple[float, float]:
    """Breathing room: a little silence before the first word and after the last one."""
    s, e = lines[first]["start"], lines[last]["end"]
    prev_end = lines[first - 1]["end"] if first > 0 else 0.0
    s = max(0.0, s - min(LEAD_IN_S, (s - prev_end) / 2))
    if last + 1 < len(lines):
        e += max(0.0, min(TAIL_S, (lines[last + 1]["start"] - e) / 2))
    else:
        e += TAIL_S
    return round(s, 3), round(e, 3)


def line_span(lines: list[dict], start: float, end: float) -> tuple[int, int] | None:
    """Indices of the first and last whole line inside a (padded) clip."""
    inside = [i for i, l in enumerate(lines) if l["start"] >= start - 0.01 and l["end"] <= end + 0.01]
    return (inside[0], inside[-1]) if inside else None


def cut_proposal(start: float, end: float, lines: list[dict], min_s: float = MIN_CLIP_S,
                 max_s: float = MAX_CLIP_S, grace_s: float = CLIP_GRACE_S) -> dict | None:
    """A Gemini proposal as a clean cut, never shortened in code: begin where a sentence
    begins, end where the proposal's own last sentence ends (not on an earlier one),
    extend a clip shorter than min_s to the next sentence end. A cut longer than
    max_s + grace_s is returned with tooLong=True: only the judge may shorten it."""
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

    if not _starts_sentence(lines, first):
        back = next((i for i in range(first - 1, max(first - 4, -1), -1) if _starts_sentence(lines, i)), None)
        fwd = next((i for i in range(first + 1, min(first + 4, last + 1)) if _starts_sentence(lines, i)), None)
        if back is not None and (length(back, last) <= hard_max or fwd is None):
            first = back
        elif fwd is not None:
            first = fwd
    last = _sentence_end_from(lines, last)
    if length(first, last) < min_s:
        longer = [k for k in range(last + 1, n) if length(first, k) >= min_s and length(first, k) <= hard_max]
        target = next((k for k in longer if _ends_thought(lines, k)), longer[0] if longer else None)
        if target is not None:
            last = target
    d = length(first, last)
    if d < min(MIN_CLIP_TOLERANCE_S, min_s):
        return None
    s, e = _pad(lines, first, last)
    return {"start": s, "end": e, "first": first, "last": last, "tooLong": d > hard_max}


_TOKEN = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)*")
_STOPWORDS = frozenset("a an and are as at be but by for i if in is it of on or so that the this to was "
                       "we you".split())
# Share of the quote's words a transcript passage must contain to count as the payoff.
PAYOFF_MATCH_MIN = 0.6


def _tokens(text: str) -> set[str]:
    words = set(_TOKEN.findall((text or "").lower().replace("\u2019", "'")))
    content = words - _STOPWORDS
    return content if len(content) >= 3 else words


def find_payoff(quote: str, lines: list[dict], lo: int = 0, hi: int | None = None) -> tuple[int, int] | None:
    """The lines (one to three in a row, between lo and hi) that hold the quoted payoff.
    Tolerant: case and punctuation are ignored and most, not all, words must be there,
    since Gemini's quote and the speech model's transcript can differ slightly."""
    q = _tokens(quote)
    if not q:
        return None
    need = 1.0 if len(q) <= 3 else PAYOFF_MATCH_MIN
    hi = len(lines) if hi is None else min(hi, len(lines))
    best, best_score, best_len = None, 0.0, 0
    tokens = [_tokens(l["text"]) for l in lines[:hi]]
    for span in (1, 2, 3):
        for i in range(lo, hi - span + 1):
            words = set().union(*tokens[i:i + span])
            score = len(q & words) / len(q)
            if score > best_score + (0.05 if best is not None and span > best_len else 0.0):
                best, best_score, best_len = (i, i + span - 1), score, span
    return best if best is not None and best_score >= need else None


def check_payoff(cand: dict, lines: list[dict], max_s: float = MAX_CLIP_S, grace_s: float = CLIP_GRACE_S,
                 extend: bool = True) -> None:
    """Find the candidate's quoted payoff: payoffAt (seconds) and payoffInside (True,
    False, or None when there is no quote or it cannot be found). A payoff just past
    the end is taken in when the clip still fits (adjusted = "extended to payoff")."""
    cand["payoffAt"], cand["payoffInside"] = None, None
    span = line_span(lines, cand["start"], cand["end"])
    quote = cand.get("payoff") or ""
    if not span or not quote.strip():
        return
    first, last = span
    limit = lines[first]["start"] + max_s + grace_s + 60.0
    hi = next((k for k in range(last + 1, len(lines)) if lines[k]["start"] > limit), len(lines))
    match = find_payoff(quote, lines, first, hi)
    if match is None:
        return
    i, j = match
    cand["payoffAt"] = round(lines[i]["start"], 2)
    if j <= last:
        cand["payoffInside"] = True
        return
    end_idx = _sentence_end_from(lines, j)
    if extend and lines[end_idx]["end"] - lines[first]["start"] <= max_s + grace_s:
        cand["end"] = _pad(lines, first, end_idx)[1]
        cand["adjusted"] = "extended to payoff"
        cand["payoffInside"] = True
    else:
        cand["payoffInside"] = False


def is_incomplete(cand: dict) -> bool:
    """The judge said the thought is not complete, or (without a verdict) the quoted
    payoff lies outside the clip."""
    verdict = cand.get("complete")
    return verdict is False or (verdict is None and cand.get("payoffInside") is False)


def normalize_candidates(raw: list[dict], lines: list[dict], min_s: float = MIN_CLIP_S,
                         max_s: float = MAX_CLIP_S, grace_s: float = CLIP_GRACE_S) -> list[dict]:
    out = []
    for c in raw or []:
        try:
            start, end = float(c["start"]), float(c["end"])
        except (KeyError, TypeError, ValueError):
            continue
        cut = cut_proposal(start, end, lines, min_s, max_s, grace_s)
        if not cut:
            continue
        source = c.get("scores") if isinstance(c.get("scores"), dict) else c
        scores = {k: _int_score(source.get(k)) for k in SCORE_KEYS}
        arc = str(c.get("arc") or "").strip().lower()
        payoff = c.get("payoff")
        cand = {
            "start": cut["start"],
            "end": cut["end"],
            "title": str(c.get("title") or "").strip()[:120],
            "hook": str(c.get("hook") or "").strip()[:300],
            "reason": str(c.get("reason") or "").strip()[:500],
            "scores": scores,
            "score": weighted_score(scores),
            "source": c.get("source", "gemini"),
            "hotspot": str(c.get("hotspot") or "").strip()[:8],
            "arc": (arc if arc in ARCS else "other") if arc else None,
            "payoff": payoff.strip()[:400] if isinstance(payoff, str) and payoff.strip() else None,
            "complete": None,
            "issue": None,
            "adjusted": None,
            "tooLong": cut["tooLong"],
        }
        if not cand["tooLong"]:
            check_payoff(cand, lines, max_s, grace_s)
        else:
            cand["payoffAt"], cand["payoffInside"] = None, None
        out.append(cand)
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


# Gemini-scored picks first, then clips cut around a hotspot that Gemini could not
# score, then transcript fallbacks.
TIERS = {"fallback": 0, "replay": 1}


def tier(candidate: dict) -> int:
    return TIERS.get(candidate.get("source"), 2)


def select_top(candidates: list[dict], max_clips: int) -> list[dict]:
    """Greedy best-first selection without overlaps. Within a tier, a complete clip always
    ranks above an incomplete one; ties break on payoff, then value (not on the hook)."""
    ranked = sorted(candidates, key=lambda c: (tier(c), not is_incomplete(c), c["score"],
                                               c["scores"].get("payoff", 0), c["scores"].get("value", 0)),
                    reverse=True)
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
        arc: str
        payoff: str
        hook_score: int
        standalone: int
        payoff_score: int
        emotion: int
        value: int
        shareability: int
        reason: str
        hotspot: str

    class Verdict(BaseModel):
        id: int
        hook: int
        standalone: int
        payoff: int
        emotion: int
        value: int
        shareability: int
        complete: bool
        issue: str
        start: float | None = None
        end: float | None = None
        reason: str

    return Candidate, Verdict


class RankingTimeout(Exception):
    """The overall ranking time limit (RANKING_TIME_LIMIT_SECONDS) ran out."""


class ResponseFormatError(Exception):
    """Gemini answered, but not with a parseable JSON list."""


def classify_error(exc: Exception) -> str:
    """What a failed request means for the next step:
    busy  - a moment of high demand (500/502/503): one quick retry, then the next model;
    stall - no reply within GEMINI_TIMEOUT_SECONDS, or the server's own deadline (408/504):
            the next model at once (a second wait on the same model could use the whole budget);
    quota - 429, the key's quota for this model is used up: the next model at once (it has
            its own quota; a few seconds won't refill this one);
    thinking | format | fatal (the next model)."""
    if isinstance(exc, RankingTimeout):
        return "timeout"
    if isinstance(exc, ResponseFormatError) or type(exc).__name__ in ("JSONDecodeError", "ValidationError"):
        return "format"
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    text = str(exc)
    if code in (408, 504) or "DEADLINE_EXCEEDED" in text \
            or "timeout" in type(exc).__name__.lower() or "timed out" in text.lower():
        return "stall"
    if code == 429 or "RESOURCE_EXHAUSTED" in text:
        return "quota"
    if code in (500, 502, 503) or any(s in text for s in ("UNAVAILABLE", "high demand")):
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


_CALLS_LOCK = threading.Lock()


def count_call(report: dict) -> None:
    """report["calls"]: every request sent to Gemini, retries and fallback models included
    (windows run in parallel threads, hence the lock)."""
    with _CALLS_LOCK:
        calls = report.get("calls")
        report["calls"] = (calls if isinstance(calls, int) else 0) + 1


def _generate_json(client, model: str, system: str, user: str, schema, report: dict | None = None,
                   log=print, sleep=None) -> list[dict]:
    """Try each model in `model_chain(model)`:
    busy (500/502/503)         -> GEMINI_BUSY_RETRIES quick retries, then the next model;
    stalled or out of quota    -> the next model at once (see classify_error);
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
            count_call(report)
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
                if kind in ("stall", "quota"):
                    break                                    # next model, no second wait
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
            report.setdefault("callLog", []).append({"model": m, "mode": mode, "items": len(items), "seconds": took})
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
                       log=print, hotspots: list[dict] | None = None, skips: list[dict] | None = None,
                       grace_s: float = CLIP_GRACE_S) -> list[dict]:
    Candidate, _ = _schemas()
    system = _load_prompt("viral_moments.md", n_candidates=n_candidates, min_s=int(min_s), max_s=int(max_s),
                          grace_s=int(grace_s))
    user = format_context(context) + format_replay(hotspots or [], skips or []) + "Transcript\n" + format_lines(lines)
    raw = generate(client, model, system, user, list[Candidate], report=report, log=log)
    for c in raw:  # the schema uses hook_score / payoff_score: hook and payoff are quotes
        payoff = c.get("payoff")
        c["scores"] = {"hook": c.get("hook_score", c.get("hook") if isinstance(c.get("hook"), (int, float)) else 0),
                       "payoff": c.get("payoff_score", payoff if isinstance(payoff, (int, float)) else 0),
                       **{k: c.get(k) for k in SCORE_KEYS if k not in ("hook", "payoff")}}
        if not isinstance(payoff, str):
            c["payoff"] = None
    valid = normalize_candidates(raw, lines, min_s, max_s, grace_s)
    if report is not None:
        report["returned"] = report.get("returned", 0) + len(raw)
        report["valid"] = report.get("valid", 0) + len(valid)
    return valid


def judge_listing(candidates: list[dict], lines: list[dict], hotspots: dict[str, dict]) -> tuple[str, dict]:
    """The judge's user message body: per candidate its id, range, a few lines before,
    the clip's lines and a few after, every line with its times. Also returns, per id,
    the (first, last, lo, hi) line indices, so corrections can be checked."""
    blocks, spans = [], {}
    for i, c in enumerate(candidates):
        span = line_span(lines, c["start"], c["end"])
        if not span:
            continue
        first, last = span
        lo, hi = max(0, first - JUDGE_BEFORE_LINES), min(len(lines), last + 1 + JUDGE_AFTER_LINES)
        spans[i] = (first, last, lo, hi)
        s, e = lines[first]["start"], lines[last]["end"]
        notes = []
        spot = hotspots.get(c.get("hotspot") or "")
        if spot and c["start"] <= spot["center"] <= c["end"]:
            notes.append(f"replay hotspot: keep {spot['center']:.1f} inside")
        if c.get("tooLong"):
            notes.append("too long: start later if it still stands alone")
        if c.get("payoff"):
            notes.append(f'proposed payoff: "{c["payoff"]}"')
        header = f"id {i}: [{s:.1f}-{e:.1f}] {e - s:.0f} s" + (f" ({'; '.join(notes)})" if notes else "")
        parts = [header]
        if lo < first:
            parts.append("before:\n" + format_lines(lines[lo:first]))
        parts.append("clip:\n" + format_lines(lines[first:last + 1]))
        if last + 1 < hi:
            parts.append("after:\n" + format_lines(lines[last + 1:hi]))
        blocks.append("\n".join(parts))
    return "\n\n".join(blocks), spans


def _line_at(lines: list[dict], indices: range, value, key: str) -> int | None:
    """The listed line whose start (key="start") or end the judge copied, at 0.1 s precision."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    near = [k for k in indices if abs(round(lines[k][key], 1) - value) <= 0.051]
    return min(near, key=lambda k: abs(lines[k][key] - value)) if near else None


def judge_correction(cand: dict, verdict: dict, lines: list[dict], span: tuple, min_s: float, max_s: float,
                     grace_s: float, skips: list[dict], hotspots: dict[str, dict]) -> tuple[tuple | None, str]:
    """Check the judge's corrected boundaries. Returns ((start, end, first, last), "")
    for a valid change, (None, "") for no change and (None, why) for an invalid one."""
    first, last, lo, hi = span
    new_first, new_last = first, last
    if verdict.get("start") is not None:
        new_first = _line_at(lines, range(lo, hi), verdict["start"], "start")
        if new_first is None:
            return None, f"start {verdict['start']} is not a listed line start"
    if verdict.get("end") is not None:
        new_last = _line_at(lines, range(lo, hi), verdict["end"], "end")
        if new_last is None:
            return None, f"end {verdict['end']} is not a listed line end"
    if (new_first, new_last) == (first, last):
        return None, ""
    if new_last < new_first:
        return None, "end before start"
    if not _starts_sentence(lines, new_first) or not _ends_thought(lines, new_last):
        return None, "not on sentence boundaries"
    length = lines[new_last]["end"] - lines[new_first]["start"]
    if length < min_s or length > max_s + grace_s:
        return None, f"{length:.0f} s is outside {min_s:.0f}-{max_s + grace_s:.0f} s"
    s, e = _pad(lines, new_first, new_last)
    if replay_signals.touches_sponsor(s, e, skips):
        return None, "runs into a left-out range"
    spot = hotspots.get(cand.get("hotspot") or "")
    if spot and cand["start"] <= spot["center"] <= cand["end"] and not s <= spot["center"] <= e:
        return None, "loses the replay hotspot"
    return (s, e, new_first, new_last), ""


def _describe_change(old: tuple[int, int], new: tuple[int, int]) -> str:
    parts = []
    if new[0] != old[0]:
        parts.append("starts earlier" if new[0] < old[0] else "starts later")
    if new[1] != old[1]:
        parts.append("ends later" if new[1] > old[1] else "ends earlier")
    return "judge: " + ", ".join(parts)


def judge_candidates(client, model: str, candidates: list[dict], lines: list[dict], min_s: float, max_s: float,
                     grace_s: float = CLIP_GRACE_S, skips: list[dict] | None = None,
                     hotspots: list[dict] | None = None, context: dict | None = None, generate=_generate_json,
                     report: dict | None = None, log=print) -> tuple[list[dict], dict]:
    """ONE call: re-score every candidate side by side on its final text, ask whether it
    holds a complete thought and let the judge correct a boundary (checked in code; an
    invalid correction is ignored). Returns (candidates, counts)."""
    _, Verdict = _schemas()
    skips = skips or []
    by_id = {h["id"]: h for h in hotspots or []}
    listing, spans = judge_listing(candidates, lines, by_id)
    system = _load_prompt("viral_moments_judge.md", min_s=int(min_s), max_s=int(max_s), grace_s=int(grace_s),
                          hard_max_s=int(max_s + grace_s))
    user = "Candidates\n\n" + listing
    if skips:
        user += "\n\nLeave out (never extend a clip into these)\n" + "\n".join(
            f"{s['start']:.1f}-{s['end']:.1f} ({s.get('category', 'skip')})" for s in skips)
    if format_context(context):
        user += "\n\n" + format_context(context).strip()
    verdicts = {}
    for r in generate(client, model, system, user, list[Verdict], report=report, log=log):
        try:
            verdicts[int(r.get("id"))] = r
        except (TypeError, ValueError):
            continue
    counts = {"complete": 0, "incomplete": 0, "adjusted": 0, "ignoredCorrections": 0}
    out = []
    for i, c in enumerate(candidates):
        r = verdicts.get(i)
        if not r or i not in spans:
            out.append(c)
            continue
        c = dict(c)
        if any(isinstance(r.get(k), (int, float)) and not isinstance(r.get(k), bool) for k in SCORE_KEYS):
            c["scores"] = {k: _int_score(r.get(k)) for k in SCORE_KEYS}
            c["score"] = weighted_score(c["scores"])
        if isinstance(r.get("complete"), bool):
            c["complete"] = r["complete"]
            counts["complete" if r["complete"] else "incomplete"] += 1
        issue = str(r.get("issue") or "").strip().lower()
        if issue:
            c["issue"] = issue if issue in ISSUES else "none"
        if str(r.get("reason") or "").strip():
            c["reason"] = str(r["reason"]).strip()[:500]
        change, why = judge_correction(c, r, lines, spans[i], min_s, max_s, grace_s, skips, by_id)
        if change:
            first, last = spans[i][:2]
            c["start"], c["end"] = change[0], change[1]
            c["adjusted"] = _describe_change((first, last), (change[2], change[3]))
            c["tooLong"] = False
            check_payoff(c, lines, max_s, grace_s, extend=False)
            counts["adjusted"] += 1
        elif why:
            counts["ignoredCorrections"] += 1
            log(f"Judge correction for {c['start']:.1f}-{c['end']:.1f} ignored: {why}")
        out.append(c)
    return out, counts


def peak_candidate(hotspot: dict, lines: list[dict], min_s: float, max_s: float) -> dict | None:
    """A clip cut in code around a hotspot Gemini did not return: about 30 s of set-up
    before the hotspot's centre and 20 s after, snapped to whole sentences."""
    centre = float(hotspot["center"])
    for before, after in ((30.0, 20.0), (20.0, 30.0), (40.0, 15.0)):
        snapped = snap_to_lines(max(0.0, centre - before), centre + after, lines, min_s, max_s)
        if snapped and snapped[0] <= centre <= snapped[1]:
            text = text_between(lines, snapped[0], snapped[1])
            first = next((l["text"] for l in lines if l["start"] >= snapped[0] - 0.01), text)
            return {"start": snapped[0], "end": snapped[1], "title": first[:80], "hook": first[:300],
                    "reason": "Cut around a moment viewers replay most on YouTube.",
                    "scores": {}, "score": 0.0, "source": "replay", "hotspot": hotspot["id"]}
    return None


def apply_replay(candidates: list[dict], replay_context: dict | None, hotspots: list[dict]) -> None:
    """Attach the audience score and the hotspot each clip contains, and blend the
    final score: (1 - weight) x Gemini rubric + weight x audience. Unscored clips
    (transcript fallbacks, hotspot clips Gemini never scored) keep their tier."""
    replay_context = replay_context or {}
    heatmap = replay_context.get("heatmap") or []
    chapters = replay_context.get("chapters") or []
    skips = replay_context.get("skips") or []
    weight = float(replay_context.get("weight", replay_signals.DEFAULT_WEIGHT))
    for c in candidates:
        base = c["score"]
        if c.get("source") == "replay":
            base = HEURISTIC_CAP                 # no rubric: count it like a fallback pick
        c["gemini"] = c["score"] if c.get("source") == "gemini" else None
        c["audience"] = replay_signals.audience_score(c["start"], c["end"], heatmap, chapters, skips)
        spot = replay_signals.covering_peak(c["start"], c["end"], hotspots)
        c["peak"] = {"id": spot["id"], "rank": spot["rank"], "kind": spot.get("kind", "replay"),
                     "center": spot["center"], "value": spot.get("value")} if spot else None
        c["score"] = replay_signals.blend(base, c["audience"], weight)


# Transcript fallback scores are scaled to 0-HEURISTIC_CAP (see heuristic_candidates).
HEURISTIC_CAP = 45.0


def _judge_skip_reason(client, generate, report: dict) -> str | None:
    import time

    if client is None and generate is _generate_json:
        return "no Gemini client"
    deadline = report.get("_deadline")
    if deadline is not None and deadline - time.monotonic() < 5:
        report["timedOut"] = True
        return "the ranking time limit is used up"
    return None


def select_moments(words: list[dict], max_clips: int, client, model: str, context: dict | None = None,
                   min_s: float | None = None, max_s: float | None = None, generate=_generate_json,
                   log=print, report: dict | None = None, min_clips: int | None = None) -> list[dict]:
    """Return up to `max_clips` ranked, non-overlapping moments (best first).

    Gemini calls: one per window (one window up to 45 min) plus one judge call.
    Never returns fewer than `min_clips` (MIN_CLIPS, default 3) when the video is
    long enough: missing slots are filled from the transcript and marked
    source="fallback". `report` (a dict) receives counts and any Gemini errors.
    context["replay"] (replay.ranking_context) adds the Most replayed hotspots, the
    audience score and the SponsorBlock ranges to leave out.
    """
    min_s = MIN_CLIP_S if min_s is None else min_s
    max_s = MAX_CLIP_S if max_s is None else max_s
    grace_s = CLIP_GRACE_S
    min_clips = min(MIN_CLIPS if min_clips is None else min_clips, max_clips)
    report = report if report is not None else {}
    import time

    judge_report = {"ran": False, "complete": 0, "incomplete": 0, "adjusted": 0, "droppedTooLong": 0}
    report.update({"model": model, "models": model_chain(model), "errors": [], "returned": 0, "valid": 0,
                   "fallback": 0, "timedOut": False, "calls": 0, "tooLong": 0, "profile": SCORE_PROFILE,
                   "judge": judge_report,
                   "_deadline": time.monotonic() + RANKING_TIME_LIMIT_S if RANKING_TIME_LIMIT_S else None})
    ranking_started = time.monotonic()

    replay_context = (context or {}).get("replay") or None
    hotspots = build_hotspots(replay_context)
    skips = (replay_context or {}).get("skips") or []
    replay_report = {"available": bool((replay_context or {}).get("heatmap")), "hotspots": len(hotspots),
                     "skips": len(skips), "droppedForSponsor": 0, "cutInCode": 0,
                     "weight": float((replay_context or {}).get("weight", replay_signals.DEFAULT_WEIGHT))}

    lines = build_lines(words)
    if not lines:
        report["reason"] = "empty transcript"
        report.pop("_deadline", None)
        return []
    windows = split_windows(lines)
    # 8 candidates for a 5-clip job: enough to choose from, ~20% less output than 10.
    per_window = max(8, max_clips + 3) if len(windows) == 1 else max(5, max_clips)
    log(f"Moment selection: {len(lines)} lines, {len(windows)} window(s), "
        f"{per_window} candidates per window, model {model}, score profile {SCORE_PROFILE}"
        + (f", {len(hotspots)} replay hotspot(s)" if hotspots else "")
        + (f", {len(skips)} SponsorBlock range(s) left out" if skips else ""))

    def window_spots(window):
        return [h for h in hotspots if window[0]["start"] <= h["center"] <= window[-1]["end"]]

    def run(window):
        spots = window_spots(window)
        try:
            return propose_candidates(client, model, window, max(per_window, len(spots) + 3), context, min_s, max_s,
                                      generate, report, log, hotspots=spots, skips=skips, grace_s=grace_s)
        except Exception as exc:  # noqa: BLE001 - one failed window must not lose the others
            log(f"Window {window[0]['start']:.0f}-{window[-1]['end']:.0f}s failed: {exc!r}")
            return []

    with ThreadPoolExecutor(max_workers=min(4, len(windows))) as pool:
        candidates = [dict(c, source="gemini") for batch in pool.map(run, windows) for c in batch]

    # SponsorBlock: sponsor reads, intros, outros and previews never become clips.
    kept = [c for c in candidates if not replay_signals.touches_sponsor(c["start"], c["end"], skips)]
    replay_report["droppedForSponsor"] += len(candidates) - len(kept)
    # Proposals longer than the cap are never cut down in code; only the judge can save them.
    too_long = sorted((c for c in kept if c.get("tooLong")), key=lambda c: c["score"], reverse=True)
    report["tooLong"] = len(too_long)
    for c in too_long:
        c["wasTooLong"] = True
    if too_long:
        log(f"{len(too_long)} proposal(s) longer than {max_s + grace_s:.0f} s: kept out unless the judge fixes them")

    # Windows overlap: drop near-duplicates, keeping the higher score.
    unique: list[dict] = []
    for cand in sorted((c for c in kept if not c.get("tooLong")), key=lambda c: c["score"], reverse=True):
        if all(overlap_ratio(cand, u) < 0.5 for u in unique):
            unique.append(cand)

    # Hotspots Gemini did not cover get a clip cut around them in code.
    built = []
    for spot in hotspots:
        if any(c["start"] <= spot["center"] <= c["end"] for c in unique + built):
            continue
        cand = peak_candidate(spot, lines, min_s, max_s)
        if cand and not replay_signals.touches_sponsor(cand["start"], cand["end"], skips):
            built.append(cand)
    replay_report["cutInCode"] = len(built)
    if built:
        log(f"{len(built)} replay hotspot(s) without a Gemini candidate: cut in code, scored by the judge")

    # One judge call over the shortlist: final-text scores, completeness, boundary fixes.
    shortlist = unique[: max(3 * max_clips, 12)] + built + too_long[:JUDGE_TOO_LONG_MAX]
    rest = unique[max(3 * max_clips, 12):]
    skip = _judge_skip_reason(client, generate, report) if shortlist else "no candidates"
    if skip:
        if shortlist:
            log(f"Judge call skipped ({skip}): using the proposal scores")
        pool = unique + built
    else:
        try:
            judged, counts = judge_candidates(client, model, shortlist, lines, min_s, max_s, grace_s, skips,
                                              hotspots, context, generate, report, log)
            judge_report.update(counts, ran=True)
            # A hotspot clip the judge has now scored competes like any Gemini pick.
            pool = [dict(c, source="gemini") if c.get("source") == "replay" and c.get("scores") else c
                    for c in judged] + rest
        except Exception as exc:  # noqa: BLE001
            log(f"Judge call failed, using the proposal scores and the payoff check: {exc!r}")
            judge_report["failed"] = True
            pool = unique + built
    rescued = sum(1 for c in pool if c.get("wasTooLong") and not c.get("tooLong"))
    judge_report["droppedTooLong"] = len(too_long) - rescued
    unique = [c for c in pool if not c.get("tooLong")]

    apply_replay(unique, replay_context, hotspots)
    chosen = select_top(unique, max_clips)
    if len(chosen) < min_clips:
        log(f"Gemini delivered {len(chosen)} usable moment(s); filling to {min_clips} from the transcript")
        filled = list(chosen)
        fallbacks = [c for c in heuristic_candidates(lines, min_s, min(max_s, HEURISTIC_MAX_S))
                     if not replay_signals.touches_sponsor(c["start"], c["end"], skips)]
        apply_replay(fallbacks, replay_context, hotspots)
        ranked = sorted(fallbacks, key=lambda c: c["score"], reverse=True)
        # First pass keeps fallback picks >= 2 min apart for variety, second pass fills any gap.
        for gap in (120.0, 0.0):
            for cand in ranked:
                if len(filled) >= min_clips:
                    break
                if all(not overlaps(cand, o) and (o.get("source") != "fallback" or
                       min(abs(cand["start"] - o["end"]), abs(o["start"] - cand["end"])) >= gap)
                       for o in filled):
                    filled.append(cand)
        # Gemini picks always rank above clips cut in code, and those above transcript fallbacks.
        filled.sort(key=lambda c: (tier(c), not is_incomplete(c), c["score"]), reverse=True)
        chosen = [dict(c, rank=i + 1, duration=round(c["end"] - c["start"], 2)) for i, c in enumerate(filled)]
        report["fallback"] = sum(1 for c in chosen if c.get("source") == "fallback")

    replay_report["chosenOnHotspot"] = sum(1 for c in chosen if c.get("peak"))
    if hotspots or skips:
        report["replay"] = replay_report
    report["chosen"] = len(chosen)
    report["seconds"] = round(time.monotonic() - ranking_started, 1)
    report.pop("_deadline", None)
    for m in chosen:
        m.pop("hotspot", None)
        m.pop("tooLong", None)
        m.pop("wasTooLong", None)
        for key in ("arc", "payoff", "complete", "issue", "adjusted", "payoffAt", "payoffInside"):
            m.setdefault(key, None)
        audience = f" audience {m['audience']:5.1f}" if m.get("audience") is not None else ""
        peak = f" [{m['peak']['id']}]" if m.get("peak") else ""
        whole = " incomplete" if is_incomplete(m) else ""
        log(f"  #{m['rank']} score {m['score']:5.1f}{audience}  {m['start']:8.2f}-{m['end']:8.2f}  "
            f"[{m.get('source', 'gemini')}]{peak}{whole} {m['title']}")
    return chosen
