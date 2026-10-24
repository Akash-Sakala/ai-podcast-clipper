"""
Re-run Gemini moment ranking on a finished job, on this laptop, in seconds.

Uses the job's transcript.json, so no GPU and no re-transcription. Good for
tuning prompts/viral_moments.md or seeing why a job produced few clips.

    pip install -r requirements-local.txt
    $env:GEMINI_API_KEY = "<your key>"        # or GEMINI_API_KEY=... in .env
    python rank_moments.py --list-models      # model IDs your key can use (for GEMINI_MODELS)
    python rank_moments.py "..\\data\\jobs\\<job id>" --max-clips 5

Writes <job dir>/moments.preview.json; job.json and the clips are not touched.
"""

import argparse
import json
import os
import pathlib
import sys

import settings  # noqa: F401 - loads .env before moments reads its settings
import moments


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("job_dir", nargs="?", help="Job folder containing transcript.json")
    parser.add_argument("--max-clips", type=int, default=5)
    parser.add_argument("--model", default=os.environ.get("GEMINI_MODEL") or moments.GEMINI_MODELS[0],
                        help="Primary model; the rest of GEMINI_MODELS follows as fallbacks")
    parser.add_argument("--list-models", action="store_true", help="List model IDs your key can use")
    args = parser.parse_args()

    if not os.environ.get("GEMINI_API_KEY"):
        print("Set GEMINI_API_KEY (environment or .env) first.")
        return 1
    client = moments.make_client(os.environ["GEMINI_API_KEY"])
    if args.list_models:
        return list_models(client)
    if not args.job_dir:
        parser.error("job_dir is required unless --list-models is used")

    job_dir = pathlib.Path(args.job_dir).resolve()
    words = json.loads((job_dir / "transcript.json").read_text(encoding="utf-8"))
    job = json.loads((job_dir / "job.json").read_text(encoding="utf-8")) if (job_dir / "job.json").exists() else {}
    meta = job.get("meta") or {}
    context = {"title": meta.get("title") or job.get("title"), "channel": meta.get("channel"),
               "description": meta.get("description")}

    report: dict = {}
    chosen = moments.select_moments(words, args.max_clips, client, args.model, context, report=report)
    (job_dir / "moments.preview.json").write_text(json.dumps(chosen, indent=2), encoding="utf-8")
    print("\nReport:", json.dumps(report, indent=2))
    print(f"\n{len(chosen)} moment(s) written to {job_dir / 'moments.preview.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
