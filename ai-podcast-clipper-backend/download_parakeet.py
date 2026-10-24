"""
Image build step (see main.py): bake Parakeet TDT 0.6B v2 (fp32 ONNX, ~2.5 GB)
into the GPU image so containers start without downloading anything.

Hugging Face rate-limits anonymous downloads from shared IPs (HTTP 429), so this
retries with backoff and uses HF_TOKEN when the Modal secret provides one.

    python download_parakeet.py /opt/parakeet-tdt-0.6b-v2
"""

import os
import sys
import time

from huggingface_hub import snapshot_download

REPO = "istupakov/parakeet-tdt-0.6b-v2-onnx"
FILES = ["config.json", "vocab.txt", "nemo128.onnx", "decoder_joint-model.onnx",
         "encoder-model.onnx", "encoder-model.onnx.data"]


def main(target: str) -> None:
    for attempt in range(1, 7):
        try:
            snapshot_download(REPO, local_dir=target, allow_patterns=FILES,
                              token=os.environ.get("HF_TOKEN") or None)
            print(f"Parakeet v2 downloaded to {target}")
            return
        except Exception as exc:  # noqa: BLE001
            if attempt == 6:
                raise
            wait = 30 * attempt
            print(f"Download attempt {attempt} failed ({exc!r}); retrying in {wait}s")
            time.sleep(wait)


if __name__ == "__main__":
    main(sys.argv[1])
