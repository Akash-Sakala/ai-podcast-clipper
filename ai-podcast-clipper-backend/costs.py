"""
Estimated Modal cost of a job, shown in the app next to its processing time.

Modal bills each container per second, from start until it scales down: the GPU,
plus the CPU cores and memory it reserves. Each GPU call reports how long it ran,
how long its container took to load models (first call only) and which GPU it got;
estimate() adds a boot allowance and the idle time before scale-down.

Prices: modal.com/pricing, October 2026 (USD per second). It is an estimate:
the Modal dashboard has the real bill.
"""

from __future__ import annotations

GPU_PRICE_PER_S = {
    "B300": 0.001972,
    "B200": 0.001736,
    "H200": 0.001261,
    "H100": 0.001097,
    "RTX-PRO-6000": 0.000842,
    "A100-80GB": 0.000694,
    "A100-40GB": 0.000583,
    "L40S": 0.000542,
    "A10": 0.000306,
    "L4": 0.000222,
    "T4": 0.000164,
}
CPU_CORE_PRICE_PER_S = 0.0000131
MEMORY_GIB_PRICE_PER_S = 0.00000222
BOOT_SECONDS = 5.0            # container start before our code runs (rough)


def gpu_label(name: str | None) -> str | None:
    """nvidia-smi name -> Modal GPU type ("NVIDIA A10" -> "A10")."""
    if not name:
        return None
    n = name.upper().replace("NVIDIA", "").replace("TESLA", "").strip()
    checks = [("B300", "B300"), ("B200", "B200"), ("H200", "H200"), ("H100", "H100"),
              ("RTX PRO 6000", "RTX-PRO-6000"), ("L40S", "L40S"), ("L4", "L4"), ("T4", "T4")]
    for needle, label in checks:
        if needle in n:
            return label
    if "A100" in n:
        return "A100-80GB" if "80" in n else "A100-40GB"
    if "A10" in n:
        return "A10"
    return n or None


def container_seconds(usage: dict, scaledown_s: float) -> float:
    return BOOT_SECONDS + float(usage.get("enterSeconds") or 0) + float(usage.get("seconds") or 0) + scaledown_s


def estimate(usages: list[dict], orchestrator_seconds: float = 0.0) -> dict:
    """usages: one dict per GPU call {container, gpu, enterSeconds, seconds, cpu, memoryGiB, scaledown}.
    Calls served by the same container share its boot and scale-down."""
    containers: dict[str, dict] = {}
    for usage in usages:
        key = str(usage.get("container") or id(usage))
        entry = containers.setdefault(key, {**usage, "seconds": 0.0, "enterSeconds": 0.0})
        entry["seconds"] += float(usage.get("seconds") or 0)
        entry["enterSeconds"] += float(usage.get("enterSeconds") or 0)

    usd, gpu_seconds, gpus = 0.0, 0.0, {}
    for entry in containers.values():
        seconds = container_seconds(entry, float(entry.get("scaledown") or 0))
        label = gpu_label(entry.get("gpu")) or "A10"
        price = GPU_PRICE_PER_S.get(label, GPU_PRICE_PER_S["A10"])
        usd += seconds * (price + float(entry.get("cpu") or 0.125) * CPU_CORE_PRICE_PER_S
                          + float(entry.get("memoryGiB") or 0.125) * MEMORY_GIB_PRICE_PER_S)
        gpu_seconds += seconds
        gpus[label] = gpus.get(label, 0) + 1
    usd += orchestrator_seconds * (1.0 * CPU_CORE_PRICE_PER_S + 1.0 * MEMORY_GIB_PRICE_PER_S)
    return {"usd": round(usd, 4), "gpuSeconds": round(gpu_seconds), "containers": len(containers),
            "gpus": gpus}
