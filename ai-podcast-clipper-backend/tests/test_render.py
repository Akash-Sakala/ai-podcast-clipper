"""Offline tests for the fast render path: same speaker decisions as the old code,
cost estimate and settings.

The equivalence tests need torch / torchvision / opencv (the GPU image has them; on
the laptop they are skipped). They use randomly initialised networks: what is
checked is that the batched code computes the same thing as the original
one-at-a-time code, which does not depend on the trained weights.

    python -m pytest tests -q
"""

import math
import pathlib
import shutil
import subprocess
import sys

import numpy as np
import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import costs  # noqa: E402
import render  # noqa: E402
import settings  # noqa: E402
from render import fast_asd  # noqa: E402


# --------------------------------------------------------------------------- #
# Settings and cost estimate
# --------------------------------------------------------------------------- #

def test_gpu_spec_accepts_a_fallback_list():
    assert settings.gpu_spec("A10") == "A10"
    assert settings.gpu_spec(" L40S , A10 ") == ["L40S", "A10"]
    assert settings.gpu_spec("") == "A10"


def test_gpu_label_maps_nvidia_names():
    assert costs.gpu_label("NVIDIA A10") == "A10"
    assert costs.gpu_label("NVIDIA L4") == "L4"
    assert costs.gpu_label("NVIDIA L40S") == "L40S"
    assert costs.gpu_label("NVIDIA A100-SXM4-40GB") == "A100-40GB"
    assert costs.gpu_label("NVIDIA A100 80GB PCIe") == "A100-80GB"
    assert costs.gpu_label("NVIDIA H100 80GB HBM3") == "H100"
    assert costs.gpu_label("Tesla T4") == "T4"
    assert costs.gpu_label(None) is None


def test_cost_estimate_counts_each_container_once():
    a10 = costs.GPU_PRICE_PER_S["A10"]
    usages = [
        {"container": "t1", "gpu": "NVIDIA A10", "enterSeconds": 20, "seconds": 30, "cpu": 0.125,
         "memoryGiB": 0.125, "scaledown": 10},
        {"container": "r1", "gpu": "NVIDIA A10", "enterSeconds": 8, "seconds": 60, "cpu": 4, "memoryGiB": 4,
         "scaledown": 5},
        {"container": "r1", "gpu": "NVIDIA A10", "enterSeconds": 0, "seconds": 50, "cpu": 4, "memoryGiB": 4,
         "scaledown": 5},
    ]
    out = costs.estimate(usages, orchestrator_seconds=100)
    t1 = (costs.BOOT_SECONDS + 20 + 30 + 10)
    r1 = (costs.BOOT_SECONDS + 8 + 110 + 5)
    expected = (t1 * (a10 + 0.125 * costs.CPU_CORE_PRICE_PER_S + 0.125 * costs.MEMORY_GIB_PRICE_PER_S)
                + r1 * (a10 + 4 * costs.CPU_CORE_PRICE_PER_S + 4 * costs.MEMORY_GIB_PRICE_PER_S)
                + 100 * (costs.CPU_CORE_PRICE_PER_S + costs.MEMORY_GIB_PRICE_PER_S))
    assert out["containers"] == 2 and out["gpus"] == {"A10": 2}
    assert math.isclose(out["usd"], round(expected, 4))
    assert out["gpuSeconds"] == round(t1 + r1)


# --------------------------------------------------------------------------- #
# Speaker choice, crops and frames (no torch needed)
# --------------------------------------------------------------------------- #

def old_choose(tracks, scores, n_frames):
    """The selection part of the old main.create_vertical_video, verbatim."""
    faces = [[] for _ in range(n_frames)]
    for tidx, track in enumerate(tracks):
        score_array = scores[tidx]
        for fidx, frame in enumerate(track["track"]["frame"].tolist()):
            slice_start = max(fidx - 30, 0)
            slice_end = min(fidx + 30, len(score_array))
            score_slice = score_array[slice_start:slice_end]
            avg_score = float(np.mean(score_slice) if len(score_slice) > 0 else 0)
            faces[frame].append({"track": tidx, "score": avg_score, "s": track["proc_track"]["s"][fidx],
                                 "x": track["proc_track"]["x"][fidx], "y": track["proc_track"]["y"][fidx]})
    out = []
    for current in faces:
        best = max(current, key=lambda face: face["score"]) if current else None
        if best and best["score"] < 0:
            best = None
        out.append(best)
    return out


def test_choose_faces_matches_old_selection():
    rng = np.random.default_rng(1)
    n_frames, tracks, scores = 300, [], []
    for start, length in [(0, 200), (50, 250), (120, 60)]:
        frames = np.arange(start, start + length)
        tracks.append({"track": {"frame": frames},
                       "proc_track": {"s": rng.uniform(20, 60, length), "x": rng.uniform(100, 1800, length),
                                      "y": rng.uniform(100, 900, length)}})
        scores.append(np.round(rng.normal(0, 1.5, length), 1))
    assert render.choose_faces(tracks, scores, n_frames) == old_choose(tracks, scores, n_frames)


class _Cut:
    def __init__(self, frame):
        self.frame_num = frame


def _scenes(*cuts, n):
    bounds = [0, *cuts, n]
    return [(_Cut(a), _Cut(b)) for a, b in zip(bounds, bounds[1:])]


def _plan(spec, n, present=None):
    """spec: list of (label, length), label = track id or None. present: {track: frames it is tracked}
    (default: every track is tracked in every frame). Returns candidates and the raw choice."""
    tracks = sorted({label for label, _ in spec if label is not None} | set(present or {}))
    present = present or {t: range(n) for t in tracks}
    labels = [label for label, length in spec for _ in range(length)]
    assert len(labels) == n
    candidates = [[{"track": t, "score": (1.0 if labels[f] == t else 0.2), "x": 100.0 * (t + 1) + f * 0.1,
                    "y": 50.0, "s": 20.0} for t in tracks if f in present[t]] for f in range(n)]
    chosen = []
    for f, label in enumerate(labels):
        chosen.append(next(c for c in candidates[f] if c["track"] == label) if label is not None else None)
    return candidates, chosen


def _labels(chosen):
    return [c["track"] if c else None for c in chosen]


def test_steady_faces_removes_a_short_flip_between_speakers():
    candidates, chosen = _plan([(0, 30), (1, 5), (0, 30)], 65)
    out, stats = render.steady_faces(candidates, chosen, render.scene_ranges(_scenes(n=65), 65), 13)
    assert _labels(out) == [0] * 65 and stats == {"shortStretches": 1, "framesChanged": 5}
    assert out[32]["x"] == candidates[32][0]["x"]             # follows speaker 0's tracked position
    assert render.count_switches(chosen) == 2 and render.count_switches(out) == 0


def test_steady_faces_holds_the_crop_over_a_one_frame_flash():
    present = {0: [f for f in range(81) if f != 40]}             # the face is not tracked on frame 40
    candidates, chosen = _plan([(0, 40), (None, 1), (0, 40)], 81, present)
    out, _ = render.steady_faces(candidates, chosen, render.scene_ranges(_scenes(n=81), 81), 13)
    assert out[40] is not None and out[40]["x"] == out[39]["x"] and out[40].get("held")
    assert render.count_switches(out) == 0


def test_steady_faces_never_crosses_a_source_cut():
    # A 0.2 s shot at the end of the clip is a cut in the video itself: it stays.
    candidates, chosen = _plan([(0, 60), (1, 5)], 65)
    out, stats = render.steady_faces(candidates, chosen, render.scene_ranges(_scenes(60, n=65), 65), 13)
    assert _labels(out) == _labels(chosen) and stats["shortStretches"] == 0
    # A whole-frame flash on the first frame after a cut joins the new shot, not the old one.
    present = {0: range(0, 60), 1: range(61, 100)}
    candidates, chosen = _plan([(0, 60), (None, 1), (1, 39)], 100, present)
    out, _ = render.steady_faces(candidates, chosen, render.scene_ranges(_scenes(60, n=100), 100), 13)
    assert _labels(out) == [0] * 60 + [1] * 40 and out[60]["x"] == out[61]["x"]


def test_steady_faces_keeps_real_switches_and_whole_frame_stretches():
    candidates, chosen = _plan([(0, 30), (None, 20), (1, 30)], 80)
    out, stats = render.steady_faces(candidates, chosen, render.scene_ranges(_scenes(n=80), 80), 13)
    assert _labels(out) == _labels(chosen) and stats["shortStretches"] == 0


def test_steady_faces_gives_a_short_stretch_to_the_stronger_speaker():
    candidates, chosen = _plan([(0, 30), (2, 6), (1, 30)], 66)
    for f in range(30, 36):                                       # speaker 1 scores higher there
        next(c for c in candidates[f] if c["track"] == 1)["score"] = 0.6
    out, _ = render.steady_faces(candidates, chosen, render.scene_ranges(_scenes(n=66), 66), 13)
    assert _labels(out) == [0] * 30 + [1] * 36


def test_steady_faces_off_and_setting(monkeypatch):
    candidates, chosen = _plan([(0, 30), (1, 5), (0, 30)], 65)
    out, _ = render.steady_faces(candidates, chosen, render.scene_ranges(_scenes(n=65), 65), 0)
    assert out == chosen
    monkeypatch.setenv("SPEAKER_MIN_HOLD_SECONDS", "0")
    assert render.min_hold_frames() == 0
    monkeypatch.setenv("SPEAKER_MIN_HOLD_SECONDS", "0.5")
    assert render.min_hold_frames() == 13                      # never shorter than 0.5 s at 25 fps
    monkeypatch.setenv("SPEAKER_MIN_HOLD_SECONDS", "1")
    assert render.min_hold_frames() == 25
    monkeypatch.delenv("SPEAKER_MIN_HOLD_SECONDS")
    assert render.min_hold_frames() == 13


def _thumbs(spec, seed=0):
    """spec: list of (shot id, length). Each shot is a fixed random picture plus a little noise."""
    rng = np.random.default_rng(seed)
    pictures, frames = {}, []
    for shot, length in spec:
        base = pictures.setdefault(shot, rng.integers(0, 255, (36, 64)).astype(np.int16))
        for _ in range(length):
            frames.append(np.clip(base + rng.integers(-2, 3, base.shape), 0, 255).astype(np.uint8))
    return np.stack(frames)


def test_flash_frames_finds_short_runs_between_cuts():
    thumbs = _thumbs([("A", 100), ("B", 1), ("A", 60), ("C", 2), ("D", 40), ("E", 4), ("D", 30)])
    flashes = render.flash_frames(thumbs, max_len=3)
    assert flashes == {100: 99, 161: 160, 162: 160}             # 1 frame A-B-A, 2 frames C-D; 4 frames stay


def test_flash_frames_at_the_edges_and_off():
    thumbs = _thumbs([("X", 2), ("A", 50), ("Y", 1)])
    assert render.flash_frames(thumbs, 3) == {0: 2, 1: 2, 52: 51}
    assert render.flash_frames(thumbs, 0) == {}
    assert render.flash_frames(_thumbs([("A", 80)]), 3) == {}   # one long shot: nothing


def test_iter_clean_frames_replaces_without_changing_the_count(monkeypatch):
    frames = [np.full((2, 2, 3), i, np.uint8) for i in range(10)]
    monkeypatch.setattr(render, "iter_frames", lambda path, w, h: iter(frames))
    out = [int(f[0, 0, 0]) for f in render.iter_clean_frames(None, 2, 2, {0: 2, 1: 2, 5: 4, 6: 4, 9: 8})]
    assert out == [2, 2, 2, 3, 4, 4, 4, 7, 8, 8]
    assert [int(f[0, 0, 0]) for f in render.iter_clean_frames(None, 2, 2, {})] == list(range(10))


def test_drop_flash_cuts_merges_a_flash_inside_a_shot_and_keeps_a_real_change():
    # A, flash, A: the cut PySceneDetect put on the flash disappears.
    thumbs = _thumbs([("A", 100), ("B", 1), ("A", 60)])
    flashes = render.flash_frames(thumbs)
    scenes = render.drop_flash_cuts(_scenes(100, n=161), flashes, thumbs)
    assert render.scene_ranges(scenes, 161) == [(0, 161)]
    # A, flash, C: the camera change stays, at the first frame after the flash.
    thumbs = _thumbs([("A", 100), ("B", 1), ("C", 60)])
    flashes = render.flash_frames(thumbs)
    for cut in (100, 101):                                      # wherever PySceneDetect put it
        scenes = render.drop_flash_cuts(_scenes(cut, n=161), flashes, thumbs)
        assert render.scene_ranges(scenes, 161) == [(0, 101), (101, 161)]


def test_padded_window_equals_numpy_pad_then_slice():
    rng = np.random.default_rng(2)
    image = rng.integers(0, 255, (90, 160, 3), dtype=np.uint8)
    for _ in range(300):
        pad = int(rng.integers(0, 40))
        r0, c0 = int(rng.integers(-5, 170)), int(rng.integers(-5, 240))
        rows, cols = (r0, r0 + int(rng.integers(0, 80))), (c0, c0 + int(rng.integers(0, 80)))
        padded = np.pad(image, ((pad, pad), (pad, pad), (0, 0)), "constant", constant_values=(110, 110))
        expected = padded[rows[0]:rows[1], cols[0]:cols[1]]
        got = fast_asd.padded_window(image, pad, rows, cols)
        assert got.shape == expected.shape and np.array_equal(got, expected)


def smooth_image(width=1920, height=1080, seed=3):
    cv2 = pytest.importorskip("cv2")
    rng = np.random.default_rng(seed)
    small = rng.integers(0, 255, (height // 40, width // 40, 3), dtype=np.uint8)
    return cv2.resize(small, (width, height), interpolation=cv2.INTER_CUBIC)


def test_crop_frame_matches_old_resize_then_cut():
    cv2 = pytest.importorskip("cv2")
    img = smooth_image()
    for face_x in (30.0, 700.5, 960.0, 1500.2, 1910.0):
        scale = 1920 / img.shape[0]
        resized = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        center_x = int(face_x * scale)
        top_x = max(min(center_x - 540, resized.shape[1] - 1080), 0)
        old = resized[0:1920, top_x:top_x + 1080].astype(int)
        new = render.crop_frame(img, face_x).astype(int)
        assert new.shape == (1920, 1080, 3)
        assert np.abs(new - old).mean() < 1.0 and np.abs(new - old).max() <= 12


def test_fit_frame_looks_like_the_old_blurred_fill():
    cv2 = pytest.importorskip("cv2")
    img = smooth_image()
    h, w = img.shape[:2]
    scale = 1080 / w
    resized = cv2.resize(img, (1080, int(h * scale)), interpolation=cv2.INTER_AREA)
    scale_bg = max(1080 / w, 1920 / h)
    bg = cv2.resize(img, (int(w * scale_bg), int(h * scale_bg)))
    bg = cv2.GaussianBlur(bg, (121, 121), 0)
    cx, cy = (bg.shape[1] - 1080) // 2, (bg.shape[0] - 1920) // 2
    old = bg[cy:cy + 1920, cx:cx + 1080].copy()
    top = (1920 - resized.shape[0]) // 2
    old[top:top + resized.shape[0], :] = resized
    new = render.fit_frame(img)
    assert new.shape == (1920, 1080, 3)
    middle = slice(top, top + resized.shape[0])
    assert np.array_equal(new[middle], old[middle])                       # the video itself: identical
    diff = np.abs(new.astype(int) - old.astype(int))
    assert diff.mean() < 3.0                                              # the blurred fill: near-identical


def test_subtitles_and_fade(tmp_path):
    pytest.importorskip("pysubs2")
    words = [{"start": 9.5 + 0.4 * i, "end": 9.8 + 0.4 * i, "word": f"w{i}"} for i in range(12)]
    path = tmp_path / "s.ass"
    assert render.write_subtitles(words, 10.0, 13.0, path) == 2      # 7 words in range -> 5 + 2
    text = path.read_text()
    assert "Anton" in text and "w1 w2 w3 w4 w5" in text and "PlayResY: 1920" in text
    assert render.audio_fade(40.0) == "afade=t=in:st=0:d=0.12,afade=t=out:st=39.65:d=0.35"


def test_chosen_summary():
    assert render.chosen_summary([None, {"x": 12.345}]) == [("fit", None), ("crop", 12.3)]


def test_frame_offset_finds_a_one_frame_lead():
    rng = np.random.default_rng(5)
    clip = [rng.integers(0, 255, (18, 32, 3), dtype=np.uint8) for _ in range(120)]
    old = [clip[0]] + clip                              # the old cut starts one frame earlier
    new_frames = {i: clip[i] for i in render.offset_samples(len(clip))}
    assert render.frame_offset(lambda i: old[i], len(old), new_frames) == 1
    assert render.frame_offset(lambda i: clip[i], len(clip), new_frames) == 0


def test_compare_decisions_with_an_offset():
    new = [("crop", 500.0 + 3 * i) if i % 40 else ("fit", None) for i in range(200)]
    old = [("crop", 499.0)] + [("crop", x + 0.9) if mode == "crop" else (mode, x) for mode, x in new]
    out = render.compare_decisions(old, new, source_height=1080, offset=1)
    assert out["frameOffset"] == 1 and out["framesCompared"] == 200 and out["sameDecision"] == 1.0
    assert out["meanShiftPx"] == pytest.approx(0.9 * 1920 / 1080, abs=0.01)
    assert out["framesShiftedOver10Px"] == 0
    assert render.compare_decisions(old, new, 1080, offset=0)["sameDecision"] < 1.0


def test_differing_segments_groups_frames_by_kind():
    new = [("crop", 500.0)] * 100
    old = [("crop", 999.0)] + [("crop", 500.0)] * 100            # one-frame lead, then:
    for i in range(20, 30):                                       # another person on new frames 20-29
        old[i + 1] = ("crop", 1400.0)
    for i in range(60, 64):                                       # whole frame on 60-63, a 2-frame gap, 66
        old[i + 1] = ("fit", None)
    old[66 + 1] = ("fit", None)
    old[80 + 1] = ("crop", 503.0)                                 # 5 output px: the same person
    segs = render.differing_segments(old, new, source_height=1080, offset=1)
    assert [(s["kind"], s["startFrame"], s["endFrame"], s["frames"]) for s in segs] == [
        ("different person", 20, 29, 10),
        ("old shows the whole frame, new follows a speaker", 60, 66, 7)]
    assert segs[0]["old"] == ("crop", 1400.0) and segs[0]["new"] == ("crop", 500.0)
    assert render.differing_segments(new, new, 1080) == []


# --------------------------------------------------------------------------- #
# Batched S3FD and LR-ASD == the original one-at-a-time code (torch)
# --------------------------------------------------------------------------- #

def _random_s3fd():
    torch = pytest.importorskip("torch")
    pytest.importorskip("torchvision")
    pytest.importorskip("cv2")
    from model.faceDetector.s3fd import S3FD
    from model.faceDetector.s3fd.nets import S3FDNet

    torch.manual_seed(0)
    net = S3FDNet(device="cpu").eval()
    original = S3FD.__new__(S3FD)          # skip the weight download, same network
    original.device, original.net = "cpu", net
    return torch, net, original


def test_batched_s3fd_keeps_exactly_the_original_detections():
    torch, net, original = _random_s3fd()
    cv2 = pytest.importorskip("cv2")
    frames = [smooth_image(512, 288, seed=s) for s in (4, 5, 6)]
    batch = np.stack([fast_asd.s3fd_input(f) for f in frames])
    with torch.no_grad():
        _, conf, _ = net.forward_raw(torch.from_numpy(batch))
    scores = torch.softmax(conf, -1)[:, :, 1].flatten().sort(descending=True).values
    for rank in (40, 300):                  # thresholds that leave a few / many candidate boxes
        conf_th = float(scores[rank])
        got = fast_asd.detect_faces_batch(net, batch, 512, 288, conf_th, "cpu")
        for frame, mine in zip(frames, got):
            expected = original.detect_faces(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB), conf_th=conf_th,
                                             scales=[fast_asd.FACEDET_SCALE])
            assert mine.shape == expected.shape
            order_m, order_e = np.lexsort(mine.T[::-1]), np.lexsort(expected.T[::-1])
            assert np.allclose(mine[order_m], expected[order_e], atol=1e-4)


def test_batched_asd_scores_match_one_window_at_a_time():
    torch = pytest.importorskip("torch")
    pytest.importorskip("python_speech_features")
    import python_speech_features

    torch.manual_seed(0)
    models = fast_asd.Models("cpu", load_weights=False)
    rng = np.random.default_rng(7)
    audio = (rng.standard_normal(int(4.3 * 16000)) * 3000).astype(np.int16)
    faces = rng.integers(0, 255, (108, 112, 112), dtype=np.uint8)
    got = fast_asd.score_track(models, audio, faces)

    # Columbia_test.evaluate_network, one window at a time.
    audio_feature = python_speech_features.mfcc(audio, 16000, numcep=13, winlen=0.025, winstep=0.010)
    length = min((audio_feature.shape[0] - audio_feature.shape[0] % 4) / 100, faces.shape[0])
    audio_feature = audio_feature[:int(round(length * 100)), :]
    video_feature = faces[:int(round(length * 25)), :, :]
    all_score = []
    for duration in {1, 1, 1, 2, 2, 2, 3, 3, 4, 5, 6}:
        scores = []
        with torch.no_grad():
            for i in range(int(math.ceil(length / duration))):
                a = torch.FloatTensor(audio_feature[i * duration * 100:(i + 1) * duration * 100, :]).unsqueeze(0)
                v = torch.FloatTensor(video_feature[i * duration * 25:(i + 1) * duration * 25, :, :]).unsqueeze(0)
                out = models.asd.forward_audio_visual_backend(models.asd.forward_audio_frontend(a),
                                                              models.asd.forward_visual_frontend(v))
                scores.extend(models.loss_av.forward(out, labels=None))
        all_score.append(scores)
    expected = np.round(np.mean(np.array(all_score), axis=0), 1).astype(float)
    assert got.shape == expected.shape
    assert np.abs(got - expected).max() <= 0.1 + 1e-9 and np.mean(got == expected) > 0.95


# --------------------------------------------------------------------------- #
# A whole clip on the CPU (random weights, libx264)
# --------------------------------------------------------------------------- #

def test_render_clip_end_to_end_on_cpu(tmp_path):
    torch = pytest.importorskip("torch")
    pytest.importorskip("torchvision")
    pytest.importorskip("cv2")
    pytest.importorskip("scenedetect")
    pytest.importorskip("pysubs2")
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        pytest.skip("needs ffmpeg and ffprobe on PATH")

    source = tmp_path / "source.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc2=size=640x360:rate=30", "-f", "lavfi", "-i", "sine=frequency=300:sample_rate=48000",
                    "-t", "6", "-ac", "2", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                    "-shortest", str(source)], check=True)

    torch.manual_seed(0)
    models = fast_asd.Models("cpu", load_weights=False)
    first = next(render.iter_frames(source, 640, 360))
    with torch.no_grad():
        _, conf, _ = models.s3fd.forward_raw(torch.from_numpy(fast_asd.s3fd_input(first)[None]))
    conf_th = float(torch.softmax(conf, -1)[0, :, 1].sort(descending=True).values[15])

    seen = []
    words = [{"start": 1.0 + 0.3 * i, "end": 1.25 + 0.3 * i, "word": f"word{i}"} for i in range(10)]
    out = tmp_path / "clip_0.mp4"
    result = render.render_clip(models, source, 1.0, 4.0, words, out, tmp_path / "work", encoder="x264",
                                progress=seen.append, conf_th=conf_th, debug_dir=tmp_path / "debug")
    assert out.exists() and result["frames"] == 75 and result["encoder"] == "x264"
    assert seen and seen[-1] == pytest.approx(1.0) and seen == sorted(seen)
    assert (tmp_path / "debug" / "tracks.pckl").exists()
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height,sample_rate,"
                            "channels", "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout
    assert "video,1080,1920" in probe.replace(" ", "")
    assert "audio,48000,2" in probe.replace(" ", "")            # original audio kept (was 16 kHz mono)


def test_render_clip_removes_a_flash_frame_on_cpu(tmp_path):
    pytest.importorskip("torch")
    pytest.importorskip("torchvision")
    pytest.importorskip("cv2")
    pytest.importorskip("scenedetect")
    pytest.importorskip("pysubs2")
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        pytest.skip("needs ffmpeg and ffprobe on PATH")

    source = tmp_path / "flash.mp4"                         # frame 40 is a red flash frame
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25",
                    "-f", "lavfi", "-i", "sine=frequency=300:sample_rate=48000", "-t", "4",
                    "-vf", "drawbox=x=0:y=0:w=iw:h=ih:color=red:t=fill:enable='eq(n,40)'",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12", "-c:a", "aac", "-shortest",
                    str(source)], check=True)
    models = render.fast_asd.Models("cpu", load_weights=False)
    out = tmp_path / "clip.mp4"
    result = render.render_clip(models, source, 0.0, 3.0, [], out, tmp_path / "work", encoder="x264")
    assert result["switches"]["flashFrames"] == 1

    frames = list(render.iter_frames(out, 1080, 1920))
    redness = [float(f[:, :, 2].mean()) - float(f[:, :, 1].mean()) for f in frames]
    assert max(redness[38:43]) < 60                          # no red frame left
    assert np.abs(frames[40].astype(int) - frames[39].astype(int)).mean() < 3   # frame 39 shown again
