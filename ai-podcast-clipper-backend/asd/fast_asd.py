"""
In-process LR-ASD active speaker detection for one clip.

Same algorithm and parameters as Columbia_test.py (kept unchanged as the reference):
PySceneDetect shots, S3FD faces at 0.25 scale with confidence > 0.9, IoU tracking
(min 10 frames, up to 10 missed detections), median-smoothed face boxes, 40 % crop
padding, and LR-ASD scores averaged over 1-6 s windows.

What is different is only how the work is done:
- models are loaded once per container, not once per clip in a new process;
- frames stay in memory (no JPG dump, no XVID face videos, no debug video);
- S3FD runs on batches of frames, reuses its prior boxes and only runs NMS on the
  boxes that can pass the 0.9 threshold (greedy NMS keeps exactly the same ones,
  see detect_faces_batch);
- LR-ASD scores all equal-length windows of a track in one batch.
"""

from __future__ import annotations

import math
import os
import pathlib
import pickle
import sys

import numpy as np

ASD_DIR = pathlib.Path(__file__).resolve().parent
if str(ASD_DIR) not in sys.path:
    sys.path.insert(0, str(ASD_DIR))   # LR-ASD's own imports: model.*, loss

FPS = 25
FACEDET_SCALE = 0.25
CONF_TH = 0.9
MIN_TRACK = 10
NUM_FAILED_DET = 10
MIN_FACE_SIZE = 1
CROP_SCALE = 0.40
IOU_THRES = 0.5
# Columbia_test.py writes {1,1,1,2,2,2,3,3,4,5,6}: a Python set, so the windows are 1..6 s.
DURATION_SET = (1, 2, 3, 4, 5, 6)
# S3FD input: RGB minus (123, 117, 104), what S3FD.detect_faces ends up feeding the network.
S3FD_MEAN_RGB = np.array([123.0, 117.0, 104.0], dtype=np.float32)
S3FD_WEIGHTS = ASD_DIR / "model" / "faceDetector" / "s3fd" / "sfd_face.pth"
ASD_WEIGHTS = ASD_DIR / "weight" / "finetuning_TalkSet.model"


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #

class Models:
    """S3FD + LR-ASD, loaded once. `device` is "cuda" in the container ("cpu" in tests)."""

    def __init__(self, device: str = "cuda", s3fd_weights=S3FD_WEIGHTS, asd_weights=ASD_WEIGHTS,
                 load_weights: bool = True):
        import torch
        from loss import lossAV
        from model.Model import ASD_Model
        from model.faceDetector.s3fd.nets import S3FDNet

        self.device = device
        self.s3fd = S3FDNet(device=device).to(device)
        self.asd = ASD_Model().to(device)
        self.loss_av = lossAV().to(device)
        if load_weights:
            self.s3fd.load_state_dict(torch.load(str(s3fd_weights), map_location=device))
            load_asd_parameters(self.asd, self.loss_av, torch.load(str(asd_weights), map_location=device))
        self.s3fd.eval()
        self.asd.eval()
        self.loss_av.eval()


def load_asd_parameters(asd_model, loss_av, state: dict) -> None:
    """ASD.loadParameters, for a model that is not hard-wired to .cuda()."""
    targets = {**{f"model.{k}": v for k, v in asd_model.state_dict().items()},
               **{f"lossAV.{k}": v for k, v in loss_av.state_dict().items()}}
    for name, param in state.items():
        key = name if name in targets else name.replace("module.", "")
        if key in targets and targets[key].size() == param.size():
            targets[key].copy_(param)


# --------------------------------------------------------------------------- #
# Scene and face detection
# --------------------------------------------------------------------------- #

def scene_detect(video_path: str) -> list:
    """Columbia_test.scene_detect: list of (start, end) FrameTimecodes for each shot."""
    from scenedetect.detectors import ContentDetector
    from scenedetect.scene_manager import SceneManager
    from scenedetect.stats_manager import StatsManager
    from scenedetect.video_manager import VideoManager

    video_manager = VideoManager([video_path])
    scene_manager = SceneManager(StatsManager())
    scene_manager.add_detector(ContentDetector())
    base_timecode = video_manager.get_base_timecode()
    video_manager.set_downscale_factor()
    video_manager.start()
    scene_manager.detect_scenes(frame_source=video_manager)
    scenes = scene_manager.get_scene_list(base_timecode)
    if scenes == []:
        scenes = [(video_manager.get_base_timecode(), video_manager.get_current_timecode())]
    video_manager.release()
    return scenes


def s3fd_input(frame_bgr: np.ndarray) -> np.ndarray:
    """The 0.25-scale S3FD input for one frame, as S3FD.detect_faces computes it
    (BGR->RGB, cv2 bilinear resize, mean subtraction), in CHW float32."""
    import cv2

    small = cv2.resize(frame_bgr, dsize=(0, 0), fx=FACEDET_SCALE, fy=FACEDET_SCALE,
                       interpolation=cv2.INTER_LINEAR)
    rgb = small[:, :, ::-1].astype(np.float32) - S3FD_MEAN_RGB
    return np.ascontiguousarray(rgb.transpose(2, 0, 1))


def detect_faces_batch(net, batch_chw: np.ndarray, full_w: int, full_h: int, conf_th: float = CONF_TH,
                       device: str = "cuda") -> list[np.ndarray]:
    """S3FD.detect_faces for a batch of frames: one (n, 5) array [x1, y1, x2, y2, score] per frame.

    The original runs greedy NMS (IoU 0.3) over every box scoring > 0.05, then keeps
    the survivors scoring > conf_th. Greedy NMS visits boxes from the highest score
    down, and a box can only be removed by a higher-scoring box, so running it on
    just the boxes above conf_th keeps exactly the same survivors, far faster.
    The final NMS at 0.1 is the original numpy one.
    """
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


def frame_detections(frame_index: int, bboxes: np.ndarray) -> list[dict]:
    return [{"frame": frame_index, "bbox": (bbox[:-1]).tolist(), "conf": bbox[-1]} for bbox in bboxes]


# --------------------------------------------------------------------------- #
# Tracking (verbatim from Columbia_test.py)
# --------------------------------------------------------------------------- #

def bb_intersection_over_union(box_a, box_b):
    x_a, y_a = max(box_a[0], box_b[0]), max(box_a[1], box_b[1])
    x_b, y_b = min(box_a[2], box_b[2]), min(box_a[3], box_b[3])
    inter = max(0, x_b - x_a) * max(0, y_b - y_a)
    area_a = (box_a[2] - box_a[0]) * (box_a[3] - box_a[1])
    area_b = (box_b[2] - box_b[0]) * (box_b[3] - box_b[1])
    return inter / float(area_a + area_b - inter)


def track_shot(scene_faces: list[list[dict]]) -> list[dict]:
    from scipy.interpolate import interp1d

    tracks = []
    while True:
        track = []
        for frame_faces in scene_faces:
            for face in frame_faces:
                if track == []:
                    track.append(face)
                    frame_faces.remove(face)
                elif face["frame"] - track[-1]["frame"] <= NUM_FAILED_DET:
                    iou = bb_intersection_over_union(face["bbox"], track[-1]["bbox"])
                    if iou > IOU_THRES:
                        track.append(face)
                        frame_faces.remove(face)
                        continue
                else:
                    break
        if track == []:
            break
        elif len(track) > MIN_TRACK:
            frame_num = np.array([f["frame"] for f in track])
            bboxes = np.array([np.array(f["bbox"]) for f in track])
            frame_i = np.arange(frame_num[0], frame_num[-1] + 1)
            bboxes_i = []
            for ij in range(0, 4):
                interp_fn = interp1d(frame_num, bboxes[:, ij])
                bboxes_i.append(interp_fn(frame_i))
            bboxes_i = np.stack(bboxes_i, axis=1)
            if max(np.mean(bboxes_i[:, 2] - bboxes_i[:, 0]), np.mean(bboxes_i[:, 3] - bboxes_i[:, 1])) > MIN_FACE_SIZE:
                tracks.append({"frame": frame_i, "bbox": bboxes_i})
    return tracks


def find_tracks(scenes: list, faces: list[list[dict]]) -> list[dict]:
    all_tracks = []
    for shot in scenes:
        if shot[1].frame_num - shot[0].frame_num >= MIN_TRACK:
            all_tracks.extend(track_shot(faces[shot[0].frame_num:shot[1].frame_num]))
    return all_tracks


def smooth_track(track: dict) -> dict:
    """crop_video's median-smoothed centre and size: the 'proc_track' saved in tracks.pckl."""
    from scipy import signal

    dets = {"x": [], "y": [], "s": []}
    for det in track["bbox"]:
        dets["s"].append(max((det[3] - det[1]), (det[2] - det[0])) / 2)
        dets["y"].append((det[1] + det[3]) / 2)
        dets["x"].append((det[0] + det[2]) / 2)
    dets["s"] = signal.medfilt(dets["s"], kernel_size=13)
    dets["x"] = signal.medfilt(dets["x"], kernel_size=13)
    dets["y"] = signal.medfilt(dets["y"], kernel_size=13)
    return dets


# --------------------------------------------------------------------------- #
# Face crops and LR-ASD scores
# --------------------------------------------------------------------------- #

def padded_window(image: np.ndarray, pad: int, rows: tuple[int, int], cols: tuple[int, int],
                  value: int = 110) -> np.ndarray:
    """numpy.pad(image, pad, constant 110)[r0:r1, c0:c1] without padding the whole frame."""
    height, width = image.shape[:2]
    r0, r1, _ = slice(*rows).indices(height + 2 * pad)
    c0, c1, _ = slice(*cols).indices(width + 2 * pad)
    out = np.full((max(r1 - r0, 0), max(c1 - c0, 0), image.shape[2]), value, dtype=image.dtype)
    src_r0, src_r1 = max(r0 - pad, 0), min(r1 - pad, height)
    src_c0, src_c1 = max(c0 - pad, 0), min(c1 - pad, width)
    if src_r1 > src_r0 and src_c1 > src_c0:
        out[src_r0 + pad - r0:src_r1 + pad - r0, src_c0 + pad - c0:src_c1 + pad - c0] = \
            image[src_r0:src_r1, src_c0:src_c1]
    return out


def face_crop(frame: np.ndarray, dets: dict, index: int) -> np.ndarray:
    """crop_video's 224x224 face, then evaluate_network's grey 112x112 centre."""
    import cv2

    cs = CROP_SCALE
    bs = dets["s"][index]
    bsi = int(bs * (1 + 2 * cs))
    my = dets["y"][index] + bsi
    mx = dets["x"][index] + bsi
    face = padded_window(frame, bsi, (int(my - bs), int(my + bs * (1 + 2 * cs))),
                         (int(mx - bs * (1 + cs)), int(mx + bs * (1 + cs))))
    face = cv2.resize(face, (224, 224))
    grey = cv2.cvtColor(face, cv2.COLOR_BGR2GRAY)
    return grey[int(112 - (112 / 2)):int(112 + (112 / 2)), int(112 - (112 / 2)):int(112 + (112 / 2))]


def track_audio(audio_16k: np.ndarray, track: dict) -> np.ndarray:
    """The track's slice of the 16 kHz mono audio (crop_video's ffmpeg -ss/-to)."""
    start = int(round(track["frame"][0] / FPS * 16000))
    end = int(round((track["frame"][-1] + 1) / FPS * 16000))
    return audio_16k[start:end]


def score_track(models: Models, audio: np.ndarray, faces: np.ndarray) -> np.ndarray:
    """evaluate_network for one track, with each window length's chunks batched."""
    import python_speech_features
    import torch

    audio_feature = python_speech_features.mfcc(audio, 16000, numcep=13, winlen=0.025, winstep=0.010)
    video_feature = np.asarray(faces)
    length = min((audio_feature.shape[0] - audio_feature.shape[0] % 4) / 100, video_feature.shape[0])
    audio_feature = audio_feature[:int(round(length * 100)), :]
    video_feature = video_feature[:int(round(length * 25)), :, :]
    all_score = []
    for duration in DURATION_SET:
        batch_size = int(math.ceil(length / duration))
        chunks = [(audio_feature[i * duration * 100:(i + 1) * duration * 100, :],
                   video_feature[i * duration * 25:(i + 1) * duration * 25, :, :]) for i in range(batch_size)]
        scores = []
        with torch.no_grad():
            i = 0
            while i < len(chunks):
                # Run consecutive chunks of the same shape as one batch.
                j = i
                while j < len(chunks) and chunks[j][0].shape == chunks[i][0].shape \
                        and chunks[j][1].shape == chunks[i][1].shape:
                    j += 1
                input_a = torch.FloatTensor(np.stack([c[0] for c in chunks[i:j]])).to(models.device)
                input_v = torch.FloatTensor(np.stack([c[1] for c in chunks[i:j]])).to(models.device)
                embed_a = models.asd.forward_audio_frontend(input_a)
                embed_v = models.asd.forward_visual_frontend(input_v)
                out = models.asd.forward_audio_visual_backend(embed_a, embed_v)
                scores.extend(models.loss_av.forward(out, labels=None))
                i = j
        all_score.append(scores)
    return np.round((np.mean(np.array(all_score), axis=0)), 1).astype(float)


def save_debug(work_dir: pathlib.Path, tracks: list, scores: list) -> None:
    """Optional: the tracks.pckl / scores.pckl pair Columbia_test.py writes (for parity checks)."""
    work_dir.mkdir(parents=True, exist_ok=True)
    with open(work_dir / "tracks.pckl", "wb") as f:
        pickle.dump(tracks, f)
    with open(work_dir / "scores.pckl", "wb") as f:
        pickle.dump(scores, f)


os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")
