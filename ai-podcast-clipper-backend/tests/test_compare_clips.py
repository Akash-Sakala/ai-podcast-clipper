"""Offline tests for comparing the clips of a re-rank with the previous ones (clipsets.py):
the re-rank leaves both sets, the owner's choice is applied file by file, and nothing is
lost when a step fails.

    python -m pytest tests -q
"""

import json
import os
import pathlib
import subprocess
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from test_robustness import (  # noqa: E402, F401 - quiet is an autouse fixture
    FakeVolume,
    gpu_result,
    install_fake_modal,
    make_job,
    quiet,
    read,
    remote_files,
    rerank_job_dir,
)

import clipsets  # noqa: E402
import jobstore  # noqa: E402
import pipeline  # noqa: E402
import run_history  # noqa: E402


def moment(start, score, title):
    return {"start": float(start), "end": float(start) + 40, "score": score, "title": title}


def details_for(moments_list, names):
    return [{"name": n, "rank": i + 1, "score": m["score"], "title": m["title"], "start": m["start"],
             "end": m["end"]} for i, (n, m) in enumerate(zip(names, moments_list))]


def pending_job(tmp_path, previous_scores=(90, 70, 50), new_scores=(95, 60)):
    """A job right after a re-rank: clips/ is the previous set, clips_new/ the new one."""
    prev_m = [moment(100 * i, s, f"Old {i}") for i, s in enumerate(previous_scores)]
    new_m = [moment(100 * i + 7, s, f"New {i}") for i, s in enumerate(new_scores)]
    prev_names = [f"clip_{i}.mp4" for i in range(len(prev_m))]
    new_names = [f"clip_{i}.mp4" for i in range(len(new_m))]
    job_dir = make_job(tmp_path, status="done", clips=prev_names, clipDetails=details_for(prev_m, prev_names),
                       comparison={"createdAt": "2026-11-15T10:00:00Z",
                                   "new": {"clips": new_names, "details": details_for(new_m, new_names)},
                                   "previous": {"at": "2026-11-14T10:00:00Z"}})
    for folder, names, tag in (("clips", prev_names, "old"), ("clips_new", new_names, "new")):
        (job_dir / folder).mkdir()
        for n in names:
            (job_dir / folder / n).write_bytes(f"{tag} {n}".encode())
            (job_dir / folder / n.replace(".mp4", ".jpg")).write_bytes(f"{tag} poster {n}".encode())
    (job_dir / "moments.json").write_text(json.dumps(prev_m))
    (job_dir / "moments.json.new").write_text(json.dumps(new_m))
    return job_dir


def snapshot(job_dir):
    return {str(p.relative_to(job_dir)): p.read_bytes() for p in sorted(job_dir.rglob("*")) if p.is_file()}


# --------------------------------------------------------------------------- #
# The re-rank run
# --------------------------------------------------------------------------- #

def rerank_world(tmp_path, monkeypatch, job_dir, clips=("clip_0.mp4", "clip_1.mp4"), fail=False):
    install_fake_modal(monkeypatch, FakeVolume(remote_files(clips)))
    monkeypatch.setattr(pipeline, "fetch_replay", lambda *a, **k: None)

    def modal_step(*a, **k):
        if fail:
            raise RuntimeError("render died")
        return gpu_result(clips)

    monkeypatch.setattr(pipeline, "process_on_modal", modal_step)
    return job_dir


def test_a_rerank_ends_with_a_pending_comparison_and_the_previous_set_untouched(tmp_path, monkeypatch):
    job_dir = rerank_world(tmp_path, monkeypatch, rerank_job_dir(tmp_path))
    data = read(job_dir)
    data["runs"] = [{"mode": "full", "status": "done", "finishedAt": "2026-11-14T09:00:00Z"}]
    (job_dir / "job.json").write_text(json.dumps(data))
    before = {p.name: p.read_bytes() for p in (job_dir / "clips").iterdir()}
    assert pipeline.run_rerank(job_dir, 5, keep_remote=False) == 0
    data = read(job_dir)
    assert {p.name: p.read_bytes() for p in (job_dir / "clips").iterdir()} == before
    assert data["clips"] == ["clip_0.mp4", "clip_1.mp4", "clip_2.mp4"] and data["status"] == "done"
    comparison = data["comparison"]
    assert comparison["new"]["clips"] == ["clip_0.mp4", "clip_1.mp4"]
    assert [d["name"] for d in comparison["new"]["details"]] == ["clip_0.mp4", "clip_1.mp4"]
    assert comparison["previous"]["at"] == "2026-11-14T09:00:00Z" and comparison["createdAt"]
    assert (job_dir / "clips_new" / "clip_1.mp4").read_bytes() == b"new clip_1.mp4"
    assert (job_dir / "moments.json.new").read_bytes() == b"[1]" and (job_dir / "moments.json").read_text() == '["old"]'
    assert (job_dir / "transcript.json").read_text() == "[2]"            # the transcript is committed as before
    [line] = run_history.read_ledger(tmp_path)
    assert line["mode"] == "rerank" and line["clips"] == 2                # recorded as before


def test_a_rerank_of_a_job_without_clips_swaps_in_directly(tmp_path, monkeypatch):
    job_dir = rerank_world(tmp_path, monkeypatch, rerank_job_dir(tmp_path, old=()))
    assert pipeline.run_rerank(job_dir, 5, keep_remote=False) == 0
    data = read(job_dir)
    assert not data.get("comparison") and data["clips"] == ["clip_0.mp4", "clip_1.mp4"]
    assert (job_dir / "clips" / "clip_0.mp4").read_bytes() == b"new clip_0.mp4"
    assert not (job_dir / "clips_new").exists() and (job_dir / "moments.json").read_text() == "[1]"


def test_a_rerank_without_new_clips_keeps_the_previous_set_and_opens_no_comparison(tmp_path, monkeypatch):
    job_dir = rerank_world(tmp_path, monkeypatch, rerank_job_dir(tmp_path), clips=())
    assert pipeline.run_rerank(job_dir, 5, keep_remote=False) == 0
    data = read(job_dir)
    assert not data.get("comparison") and "kept the previous clips" in data["message"]
    assert data["clips"] == ["clip_0.mp4", "clip_1.mp4", "clip_2.mp4"] and not (job_dir / "clips_new").exists()


def test_a_second_rerank_discards_the_undecided_new_set(tmp_path, monkeypatch):
    job_dir = pending_job(tmp_path)
    (job_dir / "transcript.json").write_text("[]")
    seen = {}

    def modal_step(*a, **k):
        seen["new"] = (job_dir / "clips_new").exists() or (job_dir / "moments.json.new").exists()
        seen["comparison"] = read(job_dir).get("comparison")
        raise RuntimeError("render died")

    install_fake_modal(monkeypatch, FakeVolume())
    monkeypatch.setattr(pipeline, "fetch_replay", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "process_on_modal", modal_step)
    assert pipeline.run_rerank(job_dir, 5, keep_remote=False) == 1
    assert seen == {"new": False, "comparison": None}
    data = read(job_dir)
    assert not data.get("comparison") and data["clips"] == ["clip_0.mp4", "clip_1.mp4", "clip_2.mp4"]
    assert (job_dir / "clips" / "clip_0.mp4").read_bytes() == b"old clip_0.mp4"


def test_a_full_run_discards_the_undecided_new_set_too(tmp_path, monkeypatch):
    job_dir = pending_job(tmp_path)
    (job_dir / "input.mp4").write_bytes(b"video")
    install_fake_modal(monkeypatch, FakeVolume())
    monkeypatch.setattr(pipeline, "fetch_replay", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "process_on_modal", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert pipeline.run_job(job_dir, 5, keep_remote=False) == 1
    assert not read(job_dir).get("comparison") and not (job_dir / "clips_new").exists()


@pytest.mark.parametrize("outcome", [RuntimeError("render died"), KeyboardInterrupt()])
def test_a_failed_or_stopped_rerank_leaves_no_comparison_and_no_new_folder(tmp_path, monkeypatch, outcome):
    job_dir = rerank_job_dir(tmp_path)
    install_fake_modal(monkeypatch, FakeVolume(remote_files()))
    monkeypatch.setattr(pipeline, "fetch_replay", lambda *a, **k: None)

    def modal_step(job, max_clips, mode, timings, saver, **kwargs):
        saver.describe(gpu_result()["moments"])
        saver.fetch(["clip_0.mp4"])
        saver.futures["clip_0.mp4"].result()
        raise outcome

    monkeypatch.setattr(pipeline, "process_on_modal", modal_step)
    if isinstance(outcome, Exception):
        assert pipeline.run_rerank(job_dir, 5, keep_remote=False) == 1
    else:
        with pytest.raises(KeyboardInterrupt):
            pipeline.run_rerank(job_dir, 5, keep_remote=False)
    data = read(job_dir)
    assert not data.get("comparison") and not data.get("liveClips") and not data.get("liveClipsSet")
    assert not (job_dir / "clips_new").exists() and not (job_dir / "moments.json.new").exists()
    assert data["clips"] == ["clip_0.mp4", "clip_1.mp4", "clip_2.mp4"]


# --------------------------------------------------------------------------- #
# Applying the choice
# --------------------------------------------------------------------------- #

def keep(*items):
    return [{"set": s, "clip": f"clip_{i}.mp4"} for s, i in items]


def test_keep_all_new(tmp_path):
    job_dir = pending_job(tmp_path)
    assert pipeline.apply_comparison(job_dir, keep(("new", 0), ("new", 1))) == 0
    data = read(job_dir)
    assert data["clips"] == ["clip_0.mp4", "clip_1.mp4"] and data["comparison"] is None
    assert data["message"] == "Kept 2 clips" and data["status"] == "done" and data["comparedAt"]
    assert sorted(p.name for p in (job_dir / "clips").iterdir()) == ["clip_0.jpg", "clip_0.mp4", "clip_1.jpg",
                                                                     "clip_1.mp4"]
    assert (job_dir / "clips" / "clip_1.mp4").read_bytes() == b"new clip_1.mp4"
    assert [d["from"] for d in data["clipDetails"]] == ["new", "new"]
    for name in ("clips_new", "clips_final", "clips_previous", "moments.json.new", "moments.json.final"):
        assert not (job_dir / name).exists()
    assert not run_history.read_ledger(tmp_path)                         # no run recorded


def test_keep_all_previous(tmp_path):
    job_dir = pending_job(tmp_path)
    assert pipeline.apply_comparison(job_dir, keep(("previous", 0), ("previous", 1), ("previous", 2))) == 0
    data = read(job_dir)
    assert [d["title"] for d in data["clipDetails"]] == ["Old 0", "Old 1", "Old 2"]
    assert (job_dir / "clips" / "clip_2.mp4").read_bytes() == b"old clip_2.mp4"
    assert (job_dir / "clips" / "clip_2.jpg").read_bytes() == b"old poster clip_2.mp4"
    assert [m["title"] for m in json.loads((job_dir / "moments.json").read_text())] == ["Old 0", "Old 1", "Old 2"]
    assert not (job_dir / "clips_new").exists() and data["message"] == "Kept 3 clips"


def test_a_mix_is_ordered_by_score_and_renumbered(tmp_path):
    job_dir = pending_job(tmp_path, previous_scores=(90, 70, 60), new_scores=(95, 60))
    # kept: new 0 (95), new 1 (60), previous 0 (90), previous 2 (60): a tie goes to the new set
    assert pipeline.apply_comparison(job_dir, keep(("previous", 2), ("new", 1), ("previous", 0), ("new", 0))) == 0
    data = read(job_dir)
    expected = [("New 0", "new"), ("Old 0", "previous"), ("New 1", "new"), ("Old 2", "previous")]
    assert [(d["title"], d["from"]) for d in data["clipDetails"]] == expected
    assert [d["name"] for d in data["clipDetails"]] == data["clips"] == [f"clip_{i}.mp4" for i in range(4)]
    assert [d["rank"] for d in data["clipDetails"]] == [1, 2, 3, 4]
    sources = [b"new clip_0.mp4", b"old clip_0.mp4", b"new clip_1.mp4", b"old clip_2.mp4"]
    for i, content in enumerate(sources):
        assert (job_dir / "clips" / f"clip_{i}.mp4").read_bytes() == content
        assert (job_dir / "clips" / f"clip_{i}.jpg").read_bytes() == content.replace(b" clip", b" poster clip")
    saved = json.loads((job_dir / "moments.json").read_text())
    assert [(m["title"], m["rank"]) for m in saved] == [(t, i + 1) for i, (t, _) in enumerate(expected)]


@pytest.mark.parametrize("selection", [
    [],
    None,
    keep(("new", 5)),
    keep(("old", 0)),
    keep(("new", 0), ("new", 0)),
    [{"set": "new", "clip": "../job.json"}],
    ["clip_0.mp4"],
])
def test_an_invalid_selection_changes_nothing(tmp_path, selection):
    job_dir = pending_job(tmp_path)
    files = snapshot(job_dir)
    data = read(job_dir)
    assert pipeline.apply_comparison(job_dir, selection) == 1
    after = read(job_dir)
    assert after["comparison"] == data["comparison"] and after["clips"] == data["clips"]
    assert after["status"] == "done" and after["error"].startswith("Could not keep the chosen clips")
    assert after["message"] == "Choose the clips to keep"
    assert {k: v for k, v in snapshot(job_dir).items() if k != "job.json"} == {
        k: v for k, v in files.items() if k != "job.json"}


def test_without_a_pending_comparison_nothing_happens(tmp_path):
    job_dir = pending_job(tmp_path)
    data = read(job_dir)
    data["comparison"] = None
    data["message"] = "Kept 2 clips"
    (job_dir / "job.json").write_text(json.dumps(data))
    assert pipeline.apply_comparison(job_dir, keep(("new", 0))) == 1
    assert read(job_dir)["message"] == "Kept 2 clips" and (job_dir / "clips_new").exists()


def test_a_locked_clip_during_the_swap_restores_the_previous_set_and_keeps_the_comparison(tmp_path, monkeypatch):
    job_dir = pending_job(tmp_path)
    files = snapshot(job_dir)
    monkeypatch.setattr(jobstore.time, "sleep", lambda s: None)
    real_replace = os.replace

    def replace(src, dst):
        if pathlib.Path(src).name == "clip_1.mp4" and pathlib.Path(src).parent.name == "clips":
            raise PermissionError("in use by the web app")             # clip_1 is being played
        return real_replace(src, dst)

    monkeypatch.setattr(jobstore.os, "replace", replace)
    assert pipeline.apply_comparison(job_dir, keep(("new", 0), ("new", 1))) == 1
    data = read(job_dir)
    assert data["comparison"] and data["clips"] == ["clip_0.mp4", "clip_1.mp4", "clip_2.mp4"]
    assert "nothing was changed" in data["error"] and data["status"] == "done"
    assert {k: v for k, v in snapshot(job_dir).items() if k != "job.json"} == {
        k: v for k, v in files.items() if k != "job.json"}

    monkeypatch.setattr(jobstore.os, "replace", real_replace)        # closed: trying again works
    assert pipeline.apply_comparison(job_dir, keep(("new", 0), ("new", 1))) == 0
    assert (job_dir / "clips" / "clip_1.mp4").read_bytes() == b"new clip_1.mp4"


def test_applying_twice_changes_nothing_the_second_time(tmp_path):
    """The second apply finds no comparison, so the kept set stays as the first left it."""
    job_dir = pending_job(tmp_path)
    assert pipeline.apply_comparison(job_dir, keep(("previous", 1))) == 0
    first = snapshot(job_dir)
    assert pipeline.apply_comparison(job_dir, keep(("previous", 1))) == 1
    assert {k: v for k, v in snapshot(job_dir).items() if k != "job.json"} == {
        k: v for k, v in first.items() if k != "job.json"}
    assert read(job_dir)["clips"] == ["clip_0.mp4"] and read(job_dir)["message"] == "Kept 1 clip"


# --------------------------------------------------------------------------- #
# The CLI
# --------------------------------------------------------------------------- #

def test_keep_clips_from_a_file_on_the_command_line(tmp_path):
    job_dir = pending_job(tmp_path)
    choice = job_dir / "keep-clips.json"
    choice.write_text(json.dumps(keep(("previous", 0), ("new", 0))), encoding="utf-8")
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    done = subprocess.run([sys.executable, str(BACKEND / "runner.py"), "--job-dir", str(job_dir),
                           "--keep-clips", f"@{choice}"], cwd=str(BACKEND), env=env, capture_output=True,
                          text=True, timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr
    data = read(job_dir)
    assert [(d["title"], d["from"]) for d in data["clipDetails"]] == [("New 0", "new"), ("Old 0", "previous")]
    assert not choice.exists() and data["comparison"] is None


def test_read_selection_accepts_json_and_a_file_with_a_bom(tmp_path):
    assert clipsets.read_selection('[{"set": "new", "clip": "clip_0.mp4"}]') == keep(("new", 0))
    path = tmp_path / "choice.json"
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps(keep(("previous", 1))).encode())
    assert clipsets.read_selection(f"@{path}") == keep(("previous", 1))
    assert not path.exists()

