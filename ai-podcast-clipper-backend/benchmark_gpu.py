"""
Benchmark clip rendering on several GPUs, and check the new render path against the old one.

Uses a finished job (its input.mp4, moments.json and transcript.json) and renders its
first clips on each GPU type, all clips of a GPU at once, as a real job does. Prints
the time and the estimated cost per clip so you can pick CLIPPER_GPU.

    cd ai-podcast-clipper-backend
    modal run benchmark_gpu.py --job-dir "..\\data\\jobs\\<job id>"
    modal run benchmark_gpu.py --job-dir "..\\data\\jobs\\<job id>" --gpus "L4,A10,L40S" --clips 3
    modal run benchmark_gpu.py --job-dir "..\\data\\jobs\\<job id>" --parity
    modal run benchmark_gpu.py --job-dir "..\\data\\jobs\\<job id>" --parity --gpus none   # parity only
    modal run benchmark_gpu.py --job-dir "..\\data\\jobs\\<job id>" --preview 2 --gpus none  # re-render clip_2

--parity renders the best clip on CLIPPER_GPU the old way (Columbia_test.py in its own
process, as before this change) and the new way, each twice, and compares the crop
decision of every frame (which face is followed, and where). Old vs old and new vs new
show how much each path varies on its own; old vs new is the change. The report and a
side-by-side still of each difference go to <job dir>/parity/.

This runs a separate, temporary app ("ai-podcast-clipper-benchmark") with the same
image and render code; the deployed app is not touched.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import time

import modal

import costs
import main
import settings

app = modal.App("ai-podcast-clipper-benchmark", image=main.image.add_local_python_source("main"))


@app.cls(gpu=main.GPU, timeout=1200, scaledown_window=main.RENDERER_SCALEDOWN_S, cpu=float(settings.RENDER_CPU),
         memory=main.RENDER_MEMORY_MB, secrets=[main.settings_secret], volumes={str(main.JOBS_PATH): main.jobs_volume})
class BenchRenderer:
    """main.Renderer, with the GPU chosen per run (with_options)."""

    @modal.enter()
    def load(self):
        self.worker = main.RenderWorker()

    @modal.method()
    def render(self, job_id: str, index: int, moment: dict, words: list, keep_video: bool = False) -> dict:
        result = self.worker.render(job_id, index, moment, words)
        if not keep_video:
            result.pop("data", None)      # the benchmark only needs the timings
        return result


def old_path(source: pathlib.Path, start: float, end: float, base: pathlib.Path) -> tuple[list, list, float]:
    """The pre-change steps for one clip: output-seek cut, Columbia_test.py in a new process.
    Returns its per-frame crop decisions, its frame JPGs and how long it took."""
    import pickle

    import render

    t = time.time()
    shutil.rmtree(base, ignore_errors=True)
    base.mkdir(parents=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(source), "-ss", str(start), "-t", str(end - start),
                    str(base / "clip.mp4")], check=True)
    subprocess.run(["python", "Columbia_test.py", "--videoName", "clip", "--videoFolder", str(base),
                    "--pretrainModel", "weight/finetuning_TalkSet.model"], cwd="/asd", check=True)
    with open(base / "clip" / "pywork" / "tracks.pckl", "rb") as f:
        tracks = pickle.load(f)
    with open(base / "clip" / "pywork" / "scores.pckl", "rb") as f:
        scores = pickle.load(f)
    jpgs = sorted((base / "clip" / "pyframes").glob("*.jpg"))
    return render.chosen_summary(render.choose_faces(tracks, scores, len(jpgs))), jpgs, time.time() - t


def side_by_side(frame, old: tuple, new: tuple, label: str) -> bytes:
    """Both crops of the same picture, old left and new right, at half size."""
    import cv2
    import numpy as np

    import render

    def crop(decision):
        return render.vertical_frame(frame, {"x": decision[1]} if decision[0] == "crop" else None)

    pair = np.hstack([crop(old), np.full((render.TARGET_H, 24, 3), 255, np.uint8), crop(new)])
    pair = cv2.resize(pair, (pair.shape[1] // 2, pair.shape[0] // 2), interpolation=cv2.INTER_AREA)
    for x, text in ((20, "OLD"), (pair.shape[1] // 2 + 26, "NEW")):
        cv2.putText(pair, text, (x, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (0, 0, 0), 8, cv2.LINE_AA)
        cv2.putText(pair, text, (x, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (36, 165, 245), 3, cv2.LINE_AA)
    cv2.putText(pair, label, (20, pair.shape[0] - 30), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 0), 6, cv2.LINE_AA)
    cv2.putText(pair, label, (20, pair.shape[0] - 30), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2,
                cv2.LINE_AA)
    return cv2.imencode(".jpg", pair, [cv2.IMWRITE_JPEG_QUALITY, 88])[1].tobytes()


@app.function(gpu=main.GPU, timeout=2400, cpu=float(settings.RENDER_CPU), memory=main.RENDER_MEMORY_MB,
              volumes={str(main.JOBS_PATH): main.jobs_volume})
def parity_run(job_id: str, moment: dict, words: list, max_stills: int = 12) -> dict:
    """Old (Columbia_test.py subprocess) vs new (render.py) crop decisions for one clip.

    Each path runs twice, so the report shows how much each one varies between runs on
    its own (old vs old, new vs new) next to how much they differ (old vs new). It also
    lists every stretch where old and new crop differently, with a side-by-side still."""
    import cv2

    import render

    main.jobs_volume.reload()
    source = main.JOBS_PATH / job_id / "input.mp4"
    start, end = float(moment["start"]), float(moment["end"])
    _, source_height = render.video_size(source)
    root = pathlib.Path("/tmp/parity")
    shutil.rmtree(root, ignore_errors=True)

    old_a, old_jpgs, old_seconds = old_path(source, start, end, root / "old_a")
    old_b, _, _ = old_path(source, start, end, root / "old_b")

    t = time.time()
    models = render.fast_asd.Models("cuda")
    encoder = render.probe_encoder("nvenc")
    new_a = render.chosen_summary(render.render_clip(models, source, start, end, words, root / "new_a.mp4",
                                                     root / "new_a", encoder)["rawChosen"])
    new_seconds = time.time() - t
    new_b = render.chosen_summary(render.render_clip(models, source, start, end, words, root / "new_b.mp4",
                                                     root / "new_b", encoder)["rawChosen"])

    # Line old and new up by picture (the old cut can start a frame earlier).
    segment = root / "new_a" / "segment.mkv"
    width, height = render.video_size(segment)
    wanted = set(render.offset_samples(len(new_a)))
    samples = {i: f.copy() for i, f in enumerate(render.iter_frames(segment, width, height)) if i in wanted}
    offset = render.frame_offset(lambda i: cv2.imread(str(old_jpgs[i])), len(old_jpgs), samples)

    segments = render.differing_segments(old_a, new_a, source_height, offset)
    biggest = sorted(segments, key=lambda s: -s["frames"])[:max_stills]
    still_at = {s["midFrame"]: s for s in biggest}
    stills = {}
    for i, frame in enumerate(render.iter_frames(segment, width, height)):
        if i in still_at:
            s = still_at[i]
            at = start + i / render.FPS
            name = f"{len(stills) + 1:02d}_{int(at // 60)}m{at % 60:04.1f}s_{s['kind'].split(',')[0].replace(' ', '-')}.jpg"
            label = f"{int(at // 60)}:{at % 60:04.1f} in the video - {s['kind']} ({s['frames']} frames)"
            stills[name] = side_by_side(frame, s["old"], s["new"], label)
            s["still"] = name
    for s in segments:
        s["videoTime"] = round(start + s["startFrame"] / render.FPS, 2)
        s["seconds"] = round(s["frames"] / render.FPS, 2)

    return {
        "report": {
            "clip": {"start": start, "end": end},
            "oldVsNew": render.compare_decisions(old_a, new_a, source_height, offset),
            "oldVsOld": render.compare_decisions(old_a, old_b, source_height, 0),
            "newVsNew": render.compare_decisions(new_a, new_b, source_height, 0),
            "speakerFrames": {"old": sum(1 for a in old_a if a[0] == "crop"),
                              "new": sum(1 for b in new_a if b[0] == "crop")},
            "oldSeconds": round(old_seconds, 1), "newSeconds": round(new_seconds, 1),
            "differences": segments,
        },
        "stills": stills,
    }


def upload_job(job_dir: pathlib.Path, bench_id: str) -> tuple[list, list]:
    for name in ("input.mp4", "moments.json", "transcript.json"):
        if not (job_dir / name).exists():
            raise SystemExit(f"{job_dir / name} is missing: use a finished job folder")
    size_mb = (job_dir / "input.mp4").stat().st_size / 1024 / 1024
    print(f"Uploading input.mp4 ({size_mb:.0f} MB) ...")
    with main.jobs_volume.batch_upload(force=True) as batch:
        batch.put_file(str(job_dir / "input.mp4"), f"/{bench_id}/input.mp4")
    moments = json.loads((job_dir / "moments.json").read_text(encoding="utf-8"))
    words = json.loads((job_dir / "transcript.json").read_text(encoding="utf-8"))
    return moments, words


def render_preview(job_path: pathlib.Path, bench_id: str, moments: list, words: list, which: str) -> None:
    """Re-render some of a job's clips (same moments) with the current code, into <job dir>/preview/."""
    indices = [int(i) for i in which.replace(" ", "").split(",") if i.isdigit() and int(i) < len(moments)]
    if not indices:
        print(f"--preview: no clip numbers below {len(moments)} in {which!r}")
        return
    print(f"Preview: rendering clip(s) {', '.join(map(str, indices))} with the current code on {main.GPU} ...")
    folder = job_path / "preview"
    folder.mkdir(exist_ok=True)
    renderer = BenchRenderer()
    args = [(bench_id, i, moments[i], main.clip_words(words, float(moments[i]["start"]), float(moments[i]["end"])),
             True) for i in indices]
    for result in renderer.render.starmap(args):
        if result.get("error"):
            print(f"  clip_{result['index']}: failed - {result['error']}")
            continue
        (folder / result["name"]).write_bytes(result["data"])
        s = result.get("switches") or {}
        print(f"  {result['name']}: crop changes {s.get('before')} -> {s.get('after')} "
              f"({s.get('shortStretches', 0)} short stretch(es) smoothed, {s.get('framesChanged', 0)} frames; "
              f"{s.get('flashFrames', 0)} flash frame(s) replaced; {s.get('sourceCuts', 0)} cuts in the source itself)")
    print(f"Preview clips: {folder}\n")


def save_parity(folder: pathlib.Path, out: dict) -> None:
    """Write the parity report and stills to <job dir>/parity/ and print a summary."""
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    report = out["report"]
    (folder / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    for name, data in out["stills"].items():
        (folder / name).write_bytes(data)

    def line(label, c):
        return (f"  {label:<12}{c['sameDecision'] * 100:6.1f}% same decision, {c['framesShiftedOver10Px']:4d} frames "
                f"on another person, mean shift {c['meanShiftPx']:.1f} px")

    print("\nCrop decisions, frame by frame:")
    print(line("old vs old", report["oldVsOld"]))
    print(line("new vs new", report["newVsNew"]))
    print(line("old vs new", report["oldVsNew"]))
    diffs = report["differences"]
    print(f"\n{len(diffs)} stretch(es) where old and new differ, "
          f"{sum(d['seconds'] for d in diffs):.1f} s in total:")
    for d in sorted(diffs, key=lambda d: d["videoTime"]):
        at = d["videoTime"]
        print(f"  {int(at // 60)}:{at % 60:04.1f}  {d['frames']:4d} frames  {d['kind']}"
              + (f"  -> {d['still']}" if d.get("still") else ""))
    print(f"\nTime for this clip: old {report['oldSeconds']} s, new {report['newSeconds']} s"
          f"\nReport and side-by-side stills (old left, new right): {folder}\n")


@app.local_entrypoint()
def benchmark(job_dir: str, gpus: str = "L4,A10,L40S", clips: int = 2, parity: bool = False, preview: str = ""):
    job_path = pathlib.Path(job_dir).resolve()
    bench_id = f"bench-{int(time.time())}"
    all_moments, words = upload_job(job_path, bench_id)
    moments = all_moments[:max(1, clips)]
    per_clip_words = [main.clip_words(words, float(m["start"]), float(m["end"])) for m in moments]
    rows = []
    try:
        if preview:
            render_preview(job_path, bench_id, all_moments, words, preview)

        if parity:
            print(f"Parity check on {main.GPU}: best clip, old and new paths twice each (about 5 min) ...")
            out = parity_run.remote(bench_id, moments[0], per_clip_words[0])
            save_parity(job_path / "parity", out)

        for gpu in [g.strip() for g in gpus.split(",") if g.strip() and g.strip().lower() != "none"]:
            if gpu.upper() in settings.NO_NVENC_GPUS:
                print(f"{gpu}: no NVENC video encoder, clips would be encoded on the CPU - skipped")
                continue
            print(f"Rendering {len(moments)} clip(s) on {gpu} ...")
            renderer = BenchRenderer.with_options(gpu=gpu)()
            started = time.time()
            results = list(renderer.render.map([bench_id] * len(moments), range(len(moments)), moments,
                                               per_clip_words))
            wall = time.time() - started
            failed = [r["error"] for r in results if r.get("error")]
            usages = [r["usage"] for r in results if r.get("usage")]
            cost = costs.estimate(usages)
            render_s = [sum((r.get("timings") or {}).values()) for r in results if not r.get("error")]
            enter_s = [u["enterSeconds"] for u in usages if u.get("enterSeconds")]
            rows.append((gpu, wall, sum(render_s) / max(len(render_s), 1), sum(enter_s) / max(len(enter_s), 1),
                         cost["usd"] / max(len(moments), 1), results[0].get("encoder", "?"), failed))
    finally:
        try:
            main.jobs_volume.remove_file(f"/{bench_id}", recursive=True)
        except Exception as exc:  # noqa: BLE001
            print(f"Could not remove /{bench_id} from the volume: {exc}")

    if rows:
        print()
        print(f"{'GPU':<8}{'all clips':>11}{'per clip':>10}{'start':>8}{'$/clip':>10}{'$/5 clips':>11}  encoder")
        for gpu, wall, per_clip, enter, usd, encoder, failed in rows:
            print(f"{gpu:<8}{wall:>10.0f}s{per_clip:>9.0f}s{enter:>7.0f}s{usd:>10.4f}{usd * 5:>11.3f}  {encoder}"
                  + (f"  ({len(failed)} failed: {failed[0][:80]})" if failed else ""))
        print("\n'all clips' is the wall time with every clip rendering at once (what a job waits for);"
              "\n'start' is model loading per container; costs are estimates (costs.py).")
