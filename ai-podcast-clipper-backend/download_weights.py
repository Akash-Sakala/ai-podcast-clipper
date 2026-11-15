"""
Model weights the render containers need, and a check that they are here before deploying.

`modal deploy main.py` copies the asd/ folder into the image. The S3FD face detector
weights (asd/model/faceDetector/s3fd/sfd_face.pth, 86 MB) are git-ignored, so a fresh
clone does not have them; without them every clip would fail. main.py calls
check_before_deploy() when the Modal CLI deploys or runs it, and stops with a message.

Fetch (or verify) the weights once:

    pip install gdown
    python download_weights.py            # downloads sfd_face.pth if missing, checks both files
    python download_weights.py --check    # only checks
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import sys

ASD_DIR = pathlib.Path(__file__).resolve().parent / "asd"
S3FD_GDRIVE_ID = "1KafnHz7ccT-3IyddBsL5yi2xGtxAKypt"

# path inside asd/ -> SHA-256 (sfd_face.pth as downloaded from the id above on 2026-11-14;
# finetuning_TalkSet.model as tracked in git)
WEIGHTS = {
    "model/faceDetector/s3fd/sfd_face.pth": "d54a87c2b7543b64729c9a25eafd188da15fd3f6e02f0ecec76ae1b30d86c491",
    "weight/finetuning_TalkSet.model": "6b4ef53694e874e96cf630198dc479c78aebb3993bbf166aee3d926dfe7d9342",
}
DEPLOY_COMMANDS = ("deploy", "run", "serve", "shell")


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def problems(asd_dir: pathlib.Path = ASD_DIR) -> list[str]:
    """What is wrong with the weight files (empty list: all present and intact)."""
    out = []
    for relative, expected in WEIGHTS.items():
        path = asd_dir / relative
        if not path.is_file():
            out.append(f"{path} is missing")
        elif sha256(path) != expected:
            out.append(f"{path} is not the expected file (SHA-256 differs)")
    return out


def modal_cli_command(argv: list[str] | None = None) -> str | None:
    """'deploy', 'run', ... when this process is the Modal CLI, else None (a plain import,
    the runner, the tests). Checks are skipped when CLIPPER_SKIP_WEIGHT_CHECK=1."""
    argv = sys.argv if argv is None else argv
    if not argv:
        return None
    program = pathlib.PurePath(argv[0].replace("\\", "/"))
    is_modal = program.stem.lower() == "modal" or (program.name == "__main__.py" and program.parent.name == "modal")
    if not is_modal:
        return None
    for arg in argv[1:]:
        if not arg.startswith("-"):
            return arg
    return None


def check_before_deploy(argv: list[str] | None = None, asd_dir: pathlib.Path = ASD_DIR) -> None:
    """Stop `modal deploy` / `modal run` with a clear message when the weights are missing."""
    if os.environ.get("CLIPPER_SKIP_WEIGHT_CHECK") == "1":
        return
    if modal_cli_command(argv) not in DEPLOY_COMMANDS:
        return
    found = problems(asd_dir)
    if found:
        raise SystemExit("Model weights missing or damaged, so every clip would fail to render:\n  - "
                         + "\n  - ".join(found)
                         + "\nFix it with:  pip install gdown; python download_weights.py")


def download_s3fd(asd_dir: pathlib.Path = ASD_DIR) -> pathlib.Path:
    """Download sfd_face.pth (to a .part file, renamed once its checksum matches)."""
    import gdown

    relative = "model/faceDetector/s3fd/sfd_face.pth"
    target = asd_dir / relative
    part = target.with_name(target.name + ".part")
    target.parent.mkdir(parents=True, exist_ok=True)
    gdown.download(id=S3FD_GDRIVE_ID, output=str(part), quiet=False)
    got = sha256(part)
    if got != WEIGHTS[relative]:
        part.unlink(missing_ok=True)
        raise SystemExit(f"Downloaded sfd_face.pth has SHA-256 {got}, expected {WEIGHTS[relative]}")
    os.replace(part, target)
    return target


def main(argv: list[str]) -> int:
    relative = "model/faceDetector/s3fd/sfd_face.pth"
    s3fd = ASD_DIR / relative
    if "--check" not in argv and not (s3fd.is_file() and sha256(s3fd) == WEIGHTS[relative]):
        print("Downloading the S3FD face detector weights ...")
        download_s3fd()
    found = problems()
    for line in found:
        print(line)
    if not found:
        print("All model weights are present and intact.")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
