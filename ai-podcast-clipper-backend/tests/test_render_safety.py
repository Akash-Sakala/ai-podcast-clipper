"""Offline tests for the render containers: FFmpeg process hygiene, subtitles, start-up
checks, clips written to the volume as they finish, and proofs that the render speed-ups
give exactly the same output (scene list, chosen faces, piped frames, S3FD boxes).

    python -m pytest tests -q
"""

import hashlib
import json
import pathlib
import shutil
import subprocess
import sys
import types
import warnings

import numpy as np
import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import download_weights  # noqa: E402
import parakeet_transcriber  # noqa: E402
import render  # noqa: E402
from render import fast_asd  # noqa: E402

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                                  reason="needs ffmpeg and ffprobe on PATH")


# --------------------------------------------------------------------------- #
# 8. FFmpeg processes: exit codes and clean-up
# --------------------------------------------------------------------------- #

@needs_ffmpeg
def test_a_broken_decode_raises_instead_of_giving_fewer_frames(tmp_path):
    broken = tmp_path / "segment.mkv"
    broken.write_bytes(b"\x1aE\xdf\xa3 not really a video" * 100)
    with pytest.raises(render.DecodeError, match="decoding segment.mkv failed"):
        list(render.iter_frames(broken, 64, 36))
    with pytest.raises(render.RenderError, match="ffmpeg failed"):
        render.frame_thumbs(broken)
    with pytest.raises(render.RenderError):
        render.video_size(broken)


@needs_ffmpeg
def test_a_reader_that_stops_early_ends_the_decoder_quietly(tmp_path):
    clip = tmp_path / "c.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=25",
                    "-t", "4", "-pix_fmt", "yuv420p", str(clip)], check=True)
    frames = render.iter_frames(clip, 160, 90)
    first = next(frames)
    frames.close()                                    # no error, no stray process
    assert first.shape == (90, 160, 3)
    assert len(list(render.iter_frames(clip, 160, 90))) == 100


def test_encode_pass_kills_and_reaps_ffmpeg_on_any_error(tmp_path, monkeypatch):
    events = []

    class FakeStdin:
        def write(self, data):
            events.append("write")

        def close(self):
            events.append("stdin closed")

    class FakeProc:
        stdin = FakeStdin()

        def kill(self):
            events.append("kill")

        def wait(self):
            events.append("wait")
            return -9

    def frames():
        try:
            yield np.zeros((36, 64, 3), np.uint8)
            yield np.zeros((36, 64, 3), np.uint8)
        finally:
            events.append("decoder closed")

    def boom(frame, face):
        if events.count("write") == 1:
            raise ValueError("bug in framing")
        return np.zeros((render.TARGET_H, render.TARGET_W, 3), np.uint8)

    monkeypatch.setattr(render.subprocess, "Popen", lambda *a, **k: FakeProc())
    monkeypatch.setattr(render, "iter_clean_frames", lambda *a, **k: frames())
    monkeypatch.setattr(render, "vertical_frame", boom)
    with pytest.raises(ValueError):
        render.encode_pass(tmp_path / "s.mkv", 64, 36, [], tmp_path / "subs.ass", 3.0, tmp_path / "o.mp4", "x264",
                           lambda i: None)
    assert events == ["write", "kill", "stdin closed", "wait", "decoder closed"]


def test_a_decode_error_is_not_retried_as_an_encoder_problem(tmp_path, monkeypatch):
    calls = []

    def encode(*args, **kwargs):
        calls.append(args[7])
        raise render.DecodeError("decoding segment.mkv failed")

    monkeypatch.setattr(render, "encode_pass", encode)
    monkeypatch.setattr(render, "cut_segment", lambda *a, **k: None)
    monkeypatch.setattr(render, "video_size", lambda p: (64, 36))
    monkeypatch.setattr(render, "read_audio_16k", lambda p: np.zeros(16000, np.int16))
    monkeypatch.setattr(render, "frame_thumbs", lambda p: np.zeros((10, 36, 64), np.uint8))
    monkeypatch.setattr(render.fast_asd, "scene_detect", lambda p: [])
    monkeypatch.setattr(render, "detect_pass", lambda *a, **k: [[] for _ in range(10)])
    monkeypatch.setattr(render, "crop_pass", lambda *a, **k: [])
    monkeypatch.setattr(render, "write_subtitles", lambda *a, **k: 0)
    with pytest.raises(render.DecodeError):
        render.render_clip(None, tmp_path / "in.mp4", 0.0, 0.4, [], tmp_path / "o.mp4", tmp_path / "w", "nvenc")
    assert calls == ["nvenc"]


# --------------------------------------------------------------------------- #
# 12. Subtitles: no deprecated int alignment, same .ass file
# --------------------------------------------------------------------------- #

def test_subtitle_alignment_enum_writes_the_same_file(tmp_path, monkeypatch):
    pysubs2 = pytest.importorskip("pysubs2")
    words = [{"start": 1.0 + 0.4 * i, "end": 1.3 + 0.4 * i, "word": f"word{i}"} for i in range(12)]
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        render.write_subtitles(words, 1.0, 6.0, tmp_path / "new.ass")
    assert not [w for w in caught if "lignment" in str(w.message)]
    monkeypatch.setattr(pysubs2, "Alignment", types.SimpleNamespace(BOTTOM_CENTER=2))   # the old plain int
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        render.write_subtitles(words, 1.0, 6.0, tmp_path / "old.ass")
    assert (tmp_path / "new.ass").read_bytes() == (tmp_path / "old.ass").read_bytes()
    assert "Default,Anton,140" in (tmp_path / "new.ass").read_text()


# --------------------------------------------------------------------------- #
# 7. Weights checked before deploying
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("argv, command", [
    (["/venv/bin/modal", "deploy", "main.py"], "deploy"),
    ([r"C:\venv\Scripts\modal.exe", "run", "benchmark_gpu.py"], "run"),
    (["/py/lib/site-packages/modal/__main__.py", "--env", "deploy"], "deploy"),
    (["-c"], None), (["/venv/bin/pytest", "tests"], None), ([], None)])
def test_modal_cli_command(argv, command):
    assert download_weights.modal_cli_command(argv) == command


def test_deploy_stops_when_weights_are_missing_or_damaged(tmp_path, monkeypatch):
    monkeypatch.delenv("CLIPPER_SKIP_WEIGHT_CHECK", raising=False)
    deploy = ["modal", "deploy", "main.py"]
    with pytest.raises(SystemExit, match="sfd_face.pth is missing"):
        download_weights.check_before_deploy(deploy, tmp_path)
    download_weights.check_before_deploy(["python", "-c"], tmp_path)        # a plain import: no check
    s3fd = tmp_path / "model/faceDetector/s3fd/sfd_face.pth"
    s3fd.parent.mkdir(parents=True)
    s3fd.write_bytes(b"truncated")
    (tmp_path / "weight").mkdir()
    shutil.copy(BACKEND / "asd/weight/finetuning_TalkSet.model", tmp_path / "weight")
    with pytest.raises(SystemExit, match="not the expected file"):
        download_weights.check_before_deploy(deploy, tmp_path)
    monkeypatch.setattr(download_weights, "WEIGHTS", {**download_weights.WEIGHTS,
                        "model/faceDetector/s3fd/sfd_face.pth": hashlib.sha256(b"truncated").hexdigest()})
    download_weights.check_before_deploy(deploy, tmp_path)                  # all present: passes


def test_importing_main_needs_no_weights_and_no_network():
    pytest.importorskip("modal")
    out = subprocess.run([sys.executable, "-c", "import main; print(main.app.name)"], cwd=BACKEND,
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0 and out.stdout.strip() == "ai-podcast-clipper", out.stderr[-800:]


# --------------------------------------------------------------------------- #
# 15. Image pins
# --------------------------------------------------------------------------- #

def test_every_image_requirement_is_pinned():
    lines = [line.split("#")[0].strip() for line in (BACKEND / "requirements.txt").read_text().splitlines()]
    pins = dict(line.split("==") for line in lines if line)
    assert all(pins.values()) and len(pins) == len([line for line in lines if line])
    assert "opencv-python-headless" in pins and "opencv-python" not in pins and "ffmpegcv" not in pins
    assert pins["torch"] == "2.8.0" and pins["onnxruntime-gpu"] == "1.24.4" and pins["scenedetect"] == "0.6.7.1"
    source = (BACKEND / "main.py").read_text()
    assert f'HF_HUB = "huggingface_hub=={pins["huggingface_hub"]}"' in source
    assert "runtime-ubuntu22.04" in source and "libgl1-mesa-glx" not in source and "/raw/main/" not in source


# --------------------------------------------------------------------------- #
# Start-up checks in the GPU containers
# --------------------------------------------------------------------------- #

class _Session:
    def __init__(self, providers):
        self.providers = providers

    def get_providers(self):
        return self.providers

    def run(self, *a):
        return None


def test_parakeet_must_really_run_on_cuda():
    on_gpu = types.SimpleNamespace(asr=types.SimpleNamespace(
        _encoder=_Session(["CUDAExecutionProvider", "CPUExecutionProvider"]),
        _preprocessor=types.SimpleNamespace(_preprocessor=_Session(["CPUExecutionProvider"]))))
    assert len(parakeet_transcriber.check_cuda_sessions(on_gpu)) == 2
    fell_back = types.SimpleNamespace(asr=types.SimpleNamespace(_encoder=_Session(["CPUExecutionProvider"])))
    with pytest.raises(RuntimeError, match="not running on CUDA"):
        parakeet_transcriber.check_cuda_sessions(fell_back)
    with pytest.raises(RuntimeError):
        parakeet_transcriber.check_cuda_sessions(types.SimpleNamespace())


def test_render_container_must_have_cuda_and_nvenc(monkeypatch):
    pytest.importorskip("modal")
    torch = pytest.importorskip("torch")
    import main

    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    with pytest.raises(RuntimeError, match="CUDA is not usable"):
        main.check_render_gpu("nvenc", "NVIDIA A10")
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    main.check_render_gpu("nvenc", "NVIDIA A10")
    with pytest.raises(RuntimeError, match="NVENC is not working"):
        main.check_render_gpu("x264", "NVIDIA A10")
    main.check_render_gpu("x264", "NVIDIA H100 80GB HBM3")       # no NVENC on this GPU type: CPU encode is expected


# --------------------------------------------------------------------------- #
# 14. Clips written to the volume as they finish, reported "ready"
# --------------------------------------------------------------------------- #

def test_render_in_parallel_reports_each_clip_once_it_is_on_the_volume(tmp_path, monkeypatch):
    pytest.importorskip("modal")
    import main

    puts, commits = [], []
    monkeypatch.setattr(main, "JOBS_PATH", tmp_path)
    monkeypatch.setattr(main, "jobs_volume", types.SimpleNamespace(commit=lambda: commits.append(1)))
    monkeypatch.setattr(main, "progress_dict", types.SimpleNamespace(
        put=lambda k, v: puts.append((k, v)), get=lambda k: 0.5, pop=lambda k: None))
    monkeypatch.setattr(main.time, "sleep", lambda s: None)
    usage = {"container": "c", "gpu": "NVIDIA A10", "seconds": 40.0, "enterSeconds": 9.0}
    finished = {
        0: {"index": 0, "name": "clip_0.mp4", "written": True, "timings": {"cut": 1.0}, "encoder": "nvenc",
            "usage": usage},
        1: {"index": 1, "name": "clip_1.mp4", "data": b"bytes of clip 1", "timings": {"cut": 2.0},
            "encoder": "nvenc", "usage": usage},       # the renderer could not write it itself
        2: {"index": 2, "name": "clip_2.mp4", "error": "clip_2: no faces", "usage": usage},
    }
    waits = {0: 1, 1: 3, 2: 2}

    class Call:
        def __init__(self, i):
            self.i = i

        def get(self, timeout=None):
            if waits[self.i]:
                waits[self.i] -= 1
                raise TimeoutError()
            return dict(finished[self.i])

    sources = []

    def spawn(job, i, moment, words, source, target_us):
        sources.append((i, source, target_us))
        return Call(i)

    renderer = types.SimpleNamespace(render=types.SimpleNamespace(spawn=spawn))
    footage = [{"index": 0, "targetUs": 61_200_000}, {"index": 2, "targetUs": 5_000}]   # clip 1: the whole video
    clips, errors, usages, timings = main.render_in_parallel("job1", [{}, {}, {}], [[], [], []], renderer,
                                                             footage=footage)
    assert sources == [(0, "footage/clip_0.mp4", 61_200_000), (1, "input.mp4", None),
                       (2, "footage/clip_2.mp4", 5_000)]
    assert clips == ["clip_0.mp4", "clip_1.mp4"] and errors == ["clip_2: no faces"] and len(usages) == 3
    assert (tmp_path / "job1" / "clips" / "clip_1.mp4").read_bytes() == b"bytes of clip 1" and commits == [1]
    ready = [v["ready"] for k, v in puts if k == "job1"]
    assert ready[0] == [] and ["clip_0.mp4"] in ready and ready[-1] == ["clip_0.mp4", "clip_1.mp4"]
    assert [t["clip"] for t in timings] == ["clip_0.mp4", "clip_1.mp4", "clip_2.mp4"]
    assert timings[2]["error"] is True and timings[0]["steps"] == {"cut": 1.0} and timings[0]["enterSeconds"] == 9.0
    json.dumps(timings)


def test_render_in_parallel_cancels_the_renders_still_running_when_it_is_cancelled(tmp_path, monkeypatch):
    """A spawned call does not end with the call that spawned it: when render_clips is
    cancelled (Modal raises InputCancellation in it), the clip renders still running must be
    cancelled too, and their ids must have been reported for the laptop ("calls")."""
    pytest.importorskip("modal")
    import main

    puts, cancelled = [], []
    monkeypatch.setattr(main, "JOBS_PATH", tmp_path)
    monkeypatch.setattr(main, "jobs_volume", types.SimpleNamespace(commit=lambda: None))
    monkeypatch.setattr(main, "progress_dict", types.SimpleNamespace(
        put=lambda k, v: puts.append((k, v)), get=lambda k: 0.2, pop=lambda k: None))

    class Cancelled(BaseException):       # stands in for modal.exception.InputCancellation
        pass

    def cancelled_while_waiting(seconds):
        raise Cancelled()

    monkeypatch.setattr(main.time, "sleep", cancelled_while_waiting)

    class Call:
        def __init__(self, i):
            self.i, self.object_id = i, f"fc-r{i}"

        def get(self, timeout=None):
            if self.i == 0:
                return {"index": 0, "name": "clip_0.mp4", "written": True, "usage": {"seconds": 1.0}}
            raise TimeoutError()

        def cancel(self, terminate_containers=False):
            cancelled.append((self.i, terminate_containers))

    renderer = types.SimpleNamespace(render=types.SimpleNamespace(spawn=lambda job, i, *a, **k: Call(i)))
    with pytest.raises(Cancelled):
        main.render_in_parallel("job1", [{}, {}, {}], [[], [], []], renderer)
    assert cancelled == [(1, True), (2, True)]                    # clip 0 had finished
    assert [v["calls"] for k, v in puts if k == "job1"] == [["fc-r0", "fc-r1", "fc-r2"]]


def test_a_clip_is_committed_under_its_name_only_when_complete(tmp_path, monkeypatch):
    pytest.importorskip("modal")
    import main

    seen = []
    monkeypatch.setattr(main, "JOBS_PATH", tmp_path)
    monkeypatch.setattr(main, "jobs_volume", types.SimpleNamespace(
        commit=lambda: seen.append(sorted(p.name for p in (tmp_path / "job1" / "clips").iterdir()))))
    clip = tmp_path / "work.mp4"
    clip.write_bytes(b"clip")
    main.save_clip_to_volume("job1", "clip_3.mp4", clip)
    assert seen == [["clip_3.mp4"]] and (tmp_path / "job1" / "clips" / "clip_3.mp4").read_bytes() == b"clip"


# --------------------------------------------------------------------------- #
# 16. Speed-ups that must not change the output
# --------------------------------------------------------------------------- #

def old_detect_faces_batch(net, batch_chw, full_w, full_h, conf_th, device):
    """detect_faces_batch before the post-processing was batched, verbatim."""
    import torch
    from torchvision.ops import nms

    from model.faceDetector.s3fd.box_utils import decode, nms_

    with torch.no_grad():
        x = torch.from_numpy(batch_chw).to(device)
        loc, conf, priors = net.forward_raw(x)
        scores = torch.softmax(conf, dim=-1)[:, :, 1]
        n, num_priors = scores.shape
        boxes = decode(loc.reshape(-1, 4), priors.to(loc.device).repeat(n, 1), [0.1, 0.2]).view(n, num_priors, 4)
        scale = torch.tensor([full_w, full_h, full_w, full_h], dtype=torch.float32)
        results = []
        for i in range(n):
            mask = scores[i] > conf_th
            kept = np.empty(shape=(0, 5))
            if bool(mask.any()):
                b, s = boxes[i][mask], scores[i][mask]
                keep = nms(b, s, 0.3)
                b = (b[keep].cpu() * scale).numpy()
                s = s[keep].cpu().numpy()
                kept = np.column_stack([b.astype(np.float64), s.astype(np.float64)])
            results.append(kept[nms_(kept, 0.1)])
    return results


def test_batched_s3fd_post_processing_gives_identical_boxes():
    torch = pytest.importorskip("torch")
    pytest.importorskip("torchvision")
    pytest.importorskip("cv2")
    from model.faceDetector.s3fd.nets import S3FDNet

    torch.manual_seed(0)
    net = S3FDNet(device="cpu").eval()
    rng = np.random.default_rng(3)
    frames = [rng.integers(0, 255, (288, 512, 3), dtype=np.uint8) for _ in range(5)]
    frames.append(np.zeros((288, 512, 3), np.uint8))                     # a frame with no candidates
    batch = np.stack([fast_asd.s3fd_input(f) for f in frames])
    with torch.no_grad():
        _, conf, _ = net.forward_raw(torch.from_numpy(batch))
    ranked = torch.softmax(conf, -1)[:, :, 1].flatten().sort(descending=True).values
    for conf_th in (float(ranked[30]), float(ranked[400]), 1.1):           # few, many, none
        new = fast_asd.detect_faces_batch(net, batch, 512, 288, conf_th, "cpu")
        old = old_detect_faces_batch(net, batch, 512, 288, conf_th, "cpu")
        assert len(new) == len(old)
        for a, b in zip(new, old):
            assert a.shape == b.shape and a.dtype == b.dtype and np.array_equal(a, b)


@needs_ffmpeg
def test_scene_detect_thread_and_zero_copy_frames_change_nothing(tmp_path, monkeypatch):
    torch = pytest.importorskip("torch")
    pytest.importorskip("torchvision")
    pytest.importorskip("cv2")
    pytest.importorskip("scenedetect")
    pytest.importorskip("pysubs2")

    source = tmp_path / "source.mp4"            # two shots: a scene cut at 2 s
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:d=2",
                    "-f", "lavfi", "-i", "mandelbrot=size=640x360:rate=25", "-f", "lavfi",
                    "-i", "sine=frequency=300:sample_rate=48000",
                    "-filter_complex", "[1:v]trim=duration=2,setpts=PTS-STARTPTS[b];[0:v][b]concat=n=2:v=1[v]",
                    "-map", "[v]", "-map", "2:a", "-t", "4", "-c:v", "libx264", "-preset", "ultrafast",
                    "-crf", "12", "-c:a", "aac", "-shortest", str(source)], check=True)
    torch.manual_seed(0)
    models = fast_asd.Models("cpu", load_weights=False)
    first = next(render.iter_frames(source, 640, 360))
    with torch.no_grad():
        _, conf, _ = models.s3fd.forward_raw(torch.from_numpy(fast_asd.s3fd_input(first)[None]))
    conf_th = float(torch.softmax(conf, -1)[0, :, 1].sort(descending=True).values[15])

    real_popen = subprocess.Popen
    real_vertical = render.vertical_frame
    real_drop = render.drop_flash_cuts
    real_score = fast_asd.score_track
    # Random weights score everyone below 0 (nobody speaks): shift the real scores up so the
    # crop follows faces and the 9:16 crop path is compared too.
    monkeypatch.setattr(render.fast_asd, "score_track", lambda *a: real_score(*a) + 5.0)

    def run(in_thread: bool, name: str) -> dict:
        piped, framed, scenes = hashlib.sha256(), hashlib.sha256(), []

        class Tap:
            def __init__(self, stdin):
                self.stdin = stdin

            def write(self, data):
                piped.update(data)
                return self.stdin.write(data)

            def close(self):
                self.stdin.close()

        def popen(cmd, **kwargs):
            proc = real_popen(cmd, **kwargs)
            if "-i" in cmd and cmd[cmd.index("-i") + 1] == "-":
                proc.stdin = Tap(proc.stdin)
            return proc

        def vertical(frame, face):
            out = real_vertical(frame, face)
            framed.update(out.tobytes())                     # what the old code wrote
            return out

        def drop(*args, **kwargs):
            out = real_drop(*args, **kwargs)
            scenes.append([(int(a.frame_num), int(b.frame_num)) for a, b in out])
            return out

        monkeypatch.setattr(render, "SCENE_DETECT_IN_THREAD", in_thread)
        monkeypatch.setattr(render.subprocess, "Popen", popen)
        monkeypatch.setattr(render, "vertical_frame", vertical)
        monkeypatch.setattr(render, "drop_flash_cuts", drop)
        result = render.render_clip(models, source, 0.0, 4.0, [], tmp_path / f"{name}.mp4", tmp_path / name,
                                    encoder="x264", conf_th=conf_th)
        return {"piped": piped.hexdigest(), "framed": framed.hexdigest(), "scenes": scenes,
                "raw": render.chosen_summary(result["rawChosen"]), "chosen": render.chosen_summary(result["chosen"]),
                "tracks": result["tracks"]}

    before, after = run(False, "before"), run(True, "after")
    assert after["tracks"] > 0 and len(after["scenes"][0]) == 2              # the test exercises tracks and a cut
    assert any(c[0] == "crop" for c in after["chosen"])
    assert after == before
    assert after["piped"] == after["framed"]                                  # same bytes as .tobytes()
