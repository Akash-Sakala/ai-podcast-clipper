"""
Re-run Gemini moment ranking on a finished job, on this laptop, in seconds.

Uses the job's transcript.json, so no GPU and no re-transcription. Good for
tuning prompts/viral_moments.md or seeing why a job produced few clips.

    pip install -r requirements-local.txt
    $env:GEMINI_API_KEY = "<your key>"        # or GEMINI_API_KEY=... in .env
    python rank_moments.py --list-models      # model IDs your key can use (for GEMINI_MODELS)
    python rank_moments.py "..\\data\\jobs\\<job id>" --max-clips 5

Writes <job dir>/moments.preview.json; job.json and the clips are not touched.

Compare the saved picks (moments.json) with what the current rules pick, before any GPU
time is spent. Each job costs its windows + 1 Gemini calls (2 for a video up to 45 min):

    python rank_moments.py --compare "..\\data\\jobs\\<id>" "..\\data\\jobs\\<id2>" --dry-run   # calls only
    python rank_moments.py --compare "..\\data\\jobs\\<id>"            # ranks, writes <job>\\eval\\
    python rank_moments.py --compare "..\\data\\jobs\\<id>" --reuse    # report again, no calls

The HTML report goes to <data dir>\\eval\\clip-comparison.html (or --out). Each ranking
prints its Gemini tokens and estimated cost (gemini_costs.py), and the report shows them.
"""

import argparse
import html
import json
import os
import pathlib
import sys

import settings  # loads .env before moments reads its settings

# isort: split
import fx
import gemini_costs
import moments
import replay


def list_models(client) -> int:
    """Print Gemini models this key can call with generateContent, chain models first."""
    rows = []
    for model in client.models.list():
        actions = model.supported_actions or []
        if actions and "generateContent" not in actions:
            continue
        name = (model.name or "").removeprefix("models/")
        if "gemini" not in name:
            continue
        rows.append((name, model.display_name or "", model.input_token_limit or 0,
                     getattr(model, "thinking", None)))
    if not rows:
        print("No Gemini models returned for this key.")
        return 1
    chain = moments.model_chain(os.environ.get("GEMINI_MODEL"))
    available = {r[0] for r in rows}
    print("Current chain (GEMINI_MODEL + GEMINI_MODELS):")
    for m in chain:
        print(f"  {'OK     ' if m in available else 'MISSING'}  {m}")
    print("\nAll Gemini models for this key:")
    for name, display, limit, thinking in sorted(rows):
        print(f"  {name:<40} {display:<32} context {limit:>9,}" + ("  thinking" if thinking else ""))
    missing = [m for m in chain if m not in available]
    if missing:
        print(f"\nRemove or replace in GEMINI_MODELS (.env): {', '.join(missing)}")
    return 0


def job_inputs(job_dir: pathlib.Path) -> tuple[list[dict], dict, dict]:
    """The transcript words, job.json and the Gemini context the real run would use
    (pipeline.gemini_context: title, channel, description, Most replayed and SponsorBlock
    as the current Settings allow)."""
    words = json.loads((job_dir / "transcript.json").read_text(encoding="utf-8"))
    job = json.loads((job_dir / "job.json").read_text(encoding="utf-8")) if (job_dir / "job.json").exists() else {}
    meta = job.get("meta") or {}
    context = {"title": meta.get("title") or job.get("title"), "channel": meta.get("channel"),
               "description": meta.get("description")}
    settings.apply_user_settings(job_dir.parent.parent)
    signals = replay.ranking_context(job.get("replay"), settings.REPLAY_WEIGHT,
                                     most_replayed=settings.MOST_REPLAYED, sponsorblock=settings.SPONSORBLOCK)
    if signals:   # saved by the runner (or runner.py --backfill-replay)
        context["replay"] = signals
    return words, job, context


def plan(words: list[dict]) -> dict:
    """Video length, windows and the Gemini calls a comparison makes (windows + 1 judge)."""
    lines = moments.build_lines(words)
    windows = len(moments.split_windows(lines))
    return {"seconds": round(lines[-1]["end"], 1) if lines else 0.0, "windows": windows,
            "calls": windows + 1 if windows else 0}


def usd_inr(data_dir: pathlib.Path) -> float | None:
    """The saved USD->INR rate (data/stats/fx.json), without a network call."""
    try:
        return (fx.usd_inr(data_dir, sources=(), log=lambda *_: None) or {}).get("rate")
    except Exception:  # noqa: BLE001
        return None


def load_old(job_dir: pathlib.Path) -> list[dict]:
    path = job_dir / "moments.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    return data if isinstance(data, list) else []


def compare_jobs(job_dirs: list[pathlib.Path], out: pathlib.Path | None, reuse: bool = False,
                 dry_run: bool = False, max_clips: int | None = None, client=None, model: str | None = None,
                 generate=None, log=print) -> int:
    """--compare: old picks (moments.json) against the current rules' picks, one HTML report."""
    entries = []
    for job_dir in job_dirs:
        words, job, context = job_inputs(job_dir)
        info = plan(words)
        old = load_old(job_dir)
        eval_dir = job_dir / "eval"
        if dry_run:
            log(f"{job_dir.name}: {info['seconds'] / 60:.1f} min of video, {info['windows']} window(s), "
                f"{info['calls']} Gemini call(s) ({info['windows']} proposal + 1 judge); none made now")
            continue
        if reuse:
            try:
                new = json.loads((eval_dir / "moments.new.json").read_text(encoding="utf-8"))
                report = json.loads((eval_dir / "ranking.new.json").read_text(encoding="utf-8"))
            except OSError:
                log(f"{job_dir.name}: no saved eval/moments.new.json, run --compare without --reuse first")
                return 1
        else:
            report = {}
            clips = max_clips or len(old) or settings.MAX_CLIPS
            kwargs = {"generate": generate} if generate else {}
            new = moments.select_moments(words, clips, client, model, context, report=report, log=log, **kwargs)
            eval_dir.mkdir(exist_ok=True)
            (eval_dir / "moments.new.json").write_text(json.dumps(new, indent=2), encoding="utf-8")
            (eval_dir / "ranking.new.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            log(gemini_costs.describe(gemini_costs.estimate(report.get("usage")), usd_inr(job_dir.parent.parent)))
        entries.append({"id": job_dir.name, "title": context.get("title") or job_dir.name, "plan": info,
                        "lines": moments.build_lines(words), "old": old, "new": new, "report": report})
    if dry_run:
        log(f"Total: {sum(plan(job_inputs(d)[0])['calls'] for d in job_dirs)} Gemini call(s)")
        return 0
    run_costs = [gemini_costs.estimate(e["report"].get("usage")) for e in entries]
    if len([c for c in run_costs if c]) > 1:
        rate = usd_inr(job_dirs[0].parent.parent)
        total = sum(c["usd"] for c in run_costs if c)
        log(f"Gemini for all {len(entries)} videos: about ${total:.4f}" + (f" (₹{total * rate:.2f})" if rate else ""))
    out = out or job_dirs[0].parent.parent / "eval" / "clip-comparison.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_report(entries, usd_inr(job_dirs[0].parent.parent)), encoding="utf-8")
    log(f"Report: {out}")
    return 0


# --------------------------------------------------------------------------- #
# HTML report
# --------------------------------------------------------------------------- #

def _esc(value) -> str:
    return html.escape("" if value is None else str(value))


def _clock(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def profile_ranks(picks: list[dict]) -> dict[str, dict[int, tuple[float, int]]]:
    """Per profile: pick index -> (rubric score, rank that profile would give in this set)."""
    out = {}
    for profile in moments.SCORE_PROFILES:
        scored = [(i, moments.weighted_score(p["scores"], profile)) for i, p in enumerate(picks) if p.get("scores")]
        order = sorted(scored, key=lambda x: x[1], reverse=True)
        out[profile] = {i: (score, rank + 1) for rank, (i, score) in enumerate(order)}
    return out


def looks_unfinished(pick: dict, lines: list[dict]) -> bool:
    """Code check for old picks (they carry no payoff quote): the clip ends on a question,
    mid-sentence, or the next line continues the same sentence."""
    span = moments.line_span(lines, pick["start"], pick["end"])
    if not span:
        return False
    last = span[1]
    text = lines[last]["text"].rstrip()
    continues = last + 1 < len(lines) and not moments._starts_sentence(lines, last + 1)
    return text.endswith("?") or not moments._ends_sentence(lines[last]) or continues


def _transcript(pick: dict, lines: list[dict], payoff: tuple[int, int] | None) -> str:
    span = moments.line_span(lines, pick["start"], pick["end"])
    if not span:
        return ""
    first, last = span
    rows = []
    for k in range(first, min(len(lines), last + 3)):
        classes = []
        if k == first:
            classes.append("first")
        if payoff and payoff[0] <= k <= payoff[1]:
            classes.append("payoff")
        if k > last:
            classes.append("after")
        tag = " <span class=tag>after the cut</span>" if k == last + 1 else ""
        rows.append(f'<p class="{" ".join(classes)}"><time>{_clock(lines[k]["start"])}</time> '
                    f"{_esc(lines[k]['text'])}{tag}</p>")
    return "<div class=text>" + "".join(rows) + "</div>"


def _card(pick: dict, index: int, ranks: dict, lines: list[dict], old: list[dict] | None) -> str:
    span = moments.line_span(lines, pick["start"], pick["end"])
    payoff = None
    if span and pick.get("payoff"):
        payoff = moments.find_payoff(pick["payoff"], lines, span[0], span[1] + 3)
    chips = []
    for profile in ("value", "classic"):
        got = ranks[profile].get(index)
        if got:
            chips.append(f"<span class=chip>{profile} {got[0]:.0f} (rank {got[1]})</span>")
    if pick.get("arc"):
        chips.append(f"<span class=chip>{_esc(pick['arc'].replace('_', ' '))}</span>")
    if old is None:   # an old pick: no verdict, only the ending check
        if looks_unfinished(pick, lines):
            chips.append("<span class='chip bad'>ends on a question or mid-sentence</span>")
    else:
        if moments.is_incomplete(pick):
            chips.append(f"<span class='chip bad'>incomplete: {_esc(pick.get('issue') or 'payoff outside')}</span>")
        elif pick.get("complete") is True:
            chips.append("<span class='chip good'>complete</span>")
        if pick.get("issue") and pick["issue"] != "none" and not moments.is_incomplete(pick):
            chips.append(f"<span class=chip>{_esc(pick['issue'])}</span>")
        if pick.get("adjusted"):
            chips.append(f"<span class=chip>{_esc(pick['adjusted'])}</span>")
        same = [o.get("rank", i + 1) for i, o in enumerate(old) if moments.overlaps(pick, o)]
        if same:
            chips.append(f"<span class=chip>overlaps old #{', #'.join(str(r) for r in same)}</span>")
        else:
            chips.append("<span class='chip new'>new moment</span>")
    if pick.get("source") and pick["source"] != "gemini":
        chips.append(f"<span class=chip>{_esc(pick['source'])}</span>")
    duration = pick["end"] - pick["start"]
    score = pick.get("score")
    return (f"<article class=card><header><span class=rank>#{_esc(pick.get('rank', index + 1))}</span>"
            f"<h4>{_esc(pick.get('title'))}</h4><span class=score>{_esc(score)}</span></header>"
            f"<p class=meta>{_clock(pick['start'])} to {_clock(pick['end'])}, {duration:.0f} s</p>"
            f"<p class=chips>{''.join(chips)}</p>"
            f"<p class=reason>{_esc(pick.get('reason'))}</p>"
            + _transcript(pick, lines, payoff) + "</article>")


def _endings(old: list[dict], lines: list[dict]) -> str:
    rows = []
    for i, pick in enumerate(old):
        span = moments.line_span(lines, pick["start"], pick["end"])
        if not span:
            continue
        first, last = span
        tail = "".join(f"<p><time>{_clock(lines[k]['start'])}</time> {_esc(lines[k]['text'])}</p>"
                       for k in range(max(first, last - 1), last + 1))
        after = "".join(f"<p class=after><time>{_clock(lines[k]['start'])}</time> {_esc(lines[k]['text'])}</p>"
                        for k in range(last + 1, min(len(lines), last + 3)))
        flag = " <span class='chip bad'>looks unfinished</span>" if looks_unfinished(pick, lines) else ""
        rows.append(f"<li><strong>Old #{_esc(pick.get('rank', i + 1))}</strong>{flag}"
                    f"<div class=text>{tail}{after}</div></li>")
    return "<ol class=endings>" + "".join(rows) + "</ol>" if rows else "<p>No old picks.</p>"


def _lengths(picks: list[dict]) -> str:
    if not picks:
        return "none"
    d = [p["end"] - p["start"] for p in picks]
    return f"{', '.join(f'{x:.0f}' for x in d)} s (average {sum(d) / len(d):.0f} s)"


def _gemini_rows(report: dict, rate: float | None) -> str:
    """Report rows: the run's Gemini tokens and estimated cost (none for older eval files)."""
    cost = gemini_costs.estimate(report.get("usage"))
    if not cost:
        return "<dt>Gemini tokens</dt><dd>not recorded</dd>"
    t = cost["tokens"]
    cached = f", {t['cached']:,} cached" if t["cached"] else ""
    money = f"${cost['usd']:.4f}" + (f" (about ₹{cost['usd'] * rate:.2f})" if rate else "")
    return (f"<dt>Gemini tokens</dt><dd>{t['input']:,} in{cached}, {t['output']:,} out, "
            f"{t['thinking']:,} thinking</dd>"
            f"<dt>Gemini cost</dt><dd>{_esc(money)}, prices of {_esc(cost['prices'])}</dd>")


def render_report(entries: list[dict], rate: float | None = None) -> str:
    sections = []
    for e in entries:
        lines, old, new, report = e["lines"], e["old"], e["new"], e["report"]
        judge = report.get("judge") or {}
        unfinished = sum(1 for p in old if looks_unfinished(p, lines))
        incomplete = sum(1 for p in new if moments.is_incomplete(p))
        calls = report.get("calls")
        summary = (
            "<dl class=summary>"
            f"<dt>Video</dt><dd>{e['plan']['seconds'] / 60:.1f} min, {e['plan']['windows']} window(s)</dd>"
            f"<dt>Gemini calls</dt><dd>{_esc(calls if isinstance(calls, int) else 'not recorded')}"
            f" (planned {e['plan']['calls']})</dd>"
            f"{_gemini_rows(report, rate)}"
            f"<dt>Old clip lengths</dt><dd>{_lengths(old)}</dd>"
            f"<dt>New clip lengths</dt><dd>{_lengths(new)}</dd>"
            f"<dt>Old picks that look unfinished</dt><dd>{unfinished} of {len(old)}</dd>"
            f"<dt>New picks marked incomplete</dt><dd>{incomplete} of {len(new)}</dd>"
            f"<dt>Judge</dt><dd>{'ran' if judge.get('ran') else 'did not run'}: "
            f"{_esc(judge.get('complete', 0))} complete, {_esc(judge.get('incomplete', 0))} incomplete, "
            f"{_esc(judge.get('adjusted', 0))} adjusted, {_esc(judge.get('droppedTooLong', 0))} too long dropped</dd>"
            f"<dt>Score profile</dt><dd>{_esc(report.get('profile', 'value'))}</dd>"
            "</dl>")
        old_ranks, new_ranks = profile_ranks(old), profile_ranks(new)
        old_cards = "".join(_card(p, i, old_ranks, lines, None) for i, p in enumerate(old)) or "<p>None.</p>"
        new_cards = "".join(_card(p, i, new_ranks, lines, old) for i, p in enumerate(new)) or "<p>None.</p>"
        sections.append(
            f"<section><h2>{_esc(e['title'])}</h2><p class=id>{_esc(e['id'])}</p>{summary}"
            f"<details><summary>How the old picks end</summary>{_endings(old, lines)}</details>"
            f"<div class=cols><div><h3>Old picks</h3>{old_cards}</div>"
            f"<div><h3>New picks</h3>{new_cards}</div></div></section>")
    return HTML_TEMPLATE.replace("{body}", "".join(sections) or "<p>No jobs.</p>")


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Clip comparison</title>
<style>
:root { color-scheme: light dark; --bg: #f7f7f8; --card: #fff; --fg: #18181b; --muted: #71717a;
  --line: #e4e4e7; --accent: #F5A524; --good: #15803d; --bad: #b91c1c; --mark: #fef3c7; --pay: #dcfce7; }
@media (prefers-color-scheme: dark) { :root { --bg: #0f0f11; --card: #18181b; --fg: #f4f4f5;
  --muted: #a1a1aa; --line: #2e2e33; --good: #4ade80; --bad: #f87171; --mark: #3a2e0a; --pay: #10331d; } }
* { box-sizing: border-box; }
body { margin: 0; padding: 24px; background: var(--bg); color: var(--fg);
  font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
h1 { font-size: 22px; margin: 0 0 4px; } h2 { font-size: 19px; margin: 0; } h3 { font-size: 16px; }
h4 { font-size: 15px; margin: 0; flex: 1; }
section { border-top: 3px solid var(--accent); padding-top: 16px; margin-top: 28px; }
.id, .meta, time { color: var(--muted); font-size: 13px; }
.summary { display: grid; grid-template-columns: max-content 1fr; gap: 2px 16px; margin: 12px 0; }
.summary dt { color: var(--muted); }
.summary dd { margin: 0; }
.cols { display: grid; grid-template-columns: repeat(auto-fit, minmax(360px, 1fr)); gap: 20px; }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 14px;
  margin-bottom: 14px; }
.card header { display: flex; gap: 10px; align-items: baseline; }
.rank { font-weight: 700; color: var(--accent); } .score { font-weight: 600; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; margin: 6px 0; }
.chip { border: 1px solid var(--line); border-radius: 999px; padding: 0 8px; font-size: 12px; }
.chip.good { color: var(--good); border-color: var(--good); }
.chip.bad { color: var(--bad); border-color: var(--bad); }
.chip.new { color: var(--accent); border-color: var(--accent); }
.reason { margin: 6px 0; font-style: italic; }
.text p { margin: 2px 0; padding: 1px 6px; border-radius: 4px; }
.text p.first { background: var(--mark); }
.text p.payoff { background: var(--pay); font-weight: 600; }
.text p.after { color: var(--muted); font-style: italic; }
.tag { font-size: 11px; border: 1px dashed var(--muted); border-radius: 4px; padding: 0 4px; margin-left: 4px; }
.endings li { margin-bottom: 8px; }
.legend span { padding: 0 6px; border-radius: 4px; margin-right: 8px; }
</style></head><body>
<h1>Clip comparison: saved picks against the new rules</h1>
<p class=legend><span style="background:var(--mark)">first sentence</span>
<span style="background:var(--pay)">payoff sentence</span>
<span style="color:var(--muted);font-style:italic">the next lines after the cut</span></p>
<p class=meta>Scores per profile are the rubric scores (without the audience blend); the rank in brackets is
the order that profile would give within the same set.</p>
{body}
</body></html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("job_dir", nargs="?", help="Job folder containing transcript.json")
    parser.add_argument("--max-clips", type=int, default=None,
                        help="Clips to pick (preview: 5; --compare: as many as the saved moments.json)")
    parser.add_argument("--model", default=os.environ.get("GEMINI_MODEL") or moments.GEMINI_MODELS[0],
                        help="Primary model; the rest of GEMINI_MODELS follows as fallbacks")
    parser.add_argument("--list-models", action="store_true", help="List model IDs your key can use")
    parser.add_argument("--compare", nargs="+", metavar="JOB_DIR",
                        help="Compare saved picks with the current rules for these job folders")
    parser.add_argument("--reuse", action="store_true", help="--compare: rebuild the report from eval/, no calls")
    parser.add_argument("--dry-run", action="store_true", help="--compare: print the Gemini calls, make none")
    parser.add_argument("--out", help="--compare: HTML report path (default <data dir>/eval/clip-comparison.html)")
    args = parser.parse_args()

    if args.compare:
        job_dirs = [pathlib.Path(d).resolve() for d in args.compare]
        missing = [str(d) for d in job_dirs if not (d / "transcript.json").exists()]
        if missing:
            print("No transcript.json in: " + ", ".join(missing))
            return 1
        client = None
        if not (args.reuse or args.dry_run):
            if not os.environ.get("GEMINI_API_KEY"):
                print("Set GEMINI_API_KEY (environment or .env) first.")
                return 1
            client = moments.make_client(os.environ["GEMINI_API_KEY"])
        return compare_jobs(job_dirs, pathlib.Path(args.out).resolve() if args.out else None, reuse=args.reuse,
                            dry_run=args.dry_run, max_clips=args.max_clips, client=client, model=args.model)

    if not os.environ.get("GEMINI_API_KEY"):
        print("Set GEMINI_API_KEY (environment or .env) first.")
        return 1
    client = moments.make_client(os.environ["GEMINI_API_KEY"])
    if args.list_models:
        return list_models(client)
    if not args.job_dir:
        parser.error("job_dir is required unless --list-models or --compare is used")

    job_dir = pathlib.Path(args.job_dir).resolve()
    words, _, context = job_inputs(job_dir)
    report: dict = {}
    chosen = moments.select_moments(words, args.max_clips or 5, client, args.model, context, report=report)
    (job_dir / "moments.preview.json").write_text(json.dumps(chosen, indent=2), encoding="utf-8")
    print("\nReport:", json.dumps(report, indent=2))
    print("\n" + gemini_costs.describe(gemini_costs.estimate(report.get("usage")), usd_inr(job_dir.parent.parent)))
    print(f"\n{len(chosen)} moment(s) written to {job_dir / 'moments.preview.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
