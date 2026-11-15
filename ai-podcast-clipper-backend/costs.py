"""
Estimated Modal cost of a job, shown in the app and on the stats page.

Modal bills each container per second, from start until it scales down: the GPU,
plus the CPU cores and memory it reserves. Each call reports how long it ran, how
long its container took to load models (first call only), which GPU it got and its
stage (transcribe, render); estimate() adds a boot allowance and the idle
time before scale-down, and counts the job's own CPU container as "rank".

Prices: the built-in table below (modal.com/pricing, November 2026, USD per second),
or Modal's live rates from `modal billing rates --json` (runner.py reads them once a
day, see parse_modal_rates). It is an estimate: `modal billing summary` has the bill.
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
STAGES = ("transcribe", "rank", "render")
# Modal's default request per container; main.py requests exactly this for its CPU functions
# and the Transcriber (one source for the decorators and the estimate).
MODAL_DEFAULT_CPU = 0.125
MODAL_DEFAULT_MEMORY_MB = 128


def builtin_rates() -> dict:
    return {"gpu": dict(GPU_PRICE_PER_S), "cpu": CPU_CORE_PRICE_PER_S, "memory": MEMORY_GIB_PRICE_PER_S,
            "source": "built-in"}


def _per_second(value: float, builtin_per_s: float) -> float | None:
    """Modal's rate keys don't say their unit: accept per second or per hour, whichever
    lands within 3x of the built-in price."""
    for divisor in (1.0, 3600.0):
        candidate = value / divisor
        if builtin_per_s / 3 <= candidate <= builtin_per_s * 3:
            return candidate
    return None


def parse_modal_rates(raw: dict) -> dict | None:
    """Map `modal billing rates --json` ({name: "price"}) onto our labels. Sandbox rates are
    ignored. Returns None (use the built-in table) unless CPU, memory and the A10 are found."""
    rates = builtin_rates()
    found = set()
    for key, value in (raw or {}).items():
        name = str(key).lower().replace("-", "").replace("_", "").replace(" ", "")
        if "sandbox" in name or "token" in name or "egress" in name or "volume" in name:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        target = None
        if "cpu" in name:
            target, builtin = "cpu", CPU_CORE_PRICE_PER_S
        elif "memory" in name or "mem" in name:
            target, builtin = "memory", MEMORY_GIB_PRICE_PER_S
        else:
            for label in sorted(GPU_PRICE_PER_S, key=len, reverse=True):
                needle = label.lower().replace("-", "")
                if needle == "a10":
                    hit = "a10" in name and "a100" not in name
                elif needle.startswith("a100"):
                    hit = "a100" in name and needle[4:6] in name
                else:
                    hit = needle in name
                if hit:
                    target, builtin = ("gpu", label), GPU_PRICE_PER_S[label]
                    break
        if target is None:
            continue
        per_s = _per_second(number, builtin)
        if per_s is None:
            continue
        if isinstance(target, tuple):
            rates["gpu"][target[1]] = per_s
            found.add(target[1])
        else:
            rates[target] = per_s
            found.add(target)
    if not {"cpu", "memory", "A10"} <= found:
        return None
    rates["source"] = "modal"
    return rates


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


def estimate(usages: list[dict], orchestrator_seconds: float = 0.0, rates: dict | None = None,
             orchestrator: dict | None = None) -> dict:
    """usages: one dict per container call {container, gpu, enterSeconds, seconds, cpu, memoryGiB,
    scaledown, cpuOnly, stage}; cpu and memoryGiB are what Modal bills (the larger of the
    request and the use, as the container measured them). Calls served by the same container
    share its boot and scale-down. orchestrator: {cpu, memoryGiB} of the CPU functions' own
    container (default: Modal's default request). Returns {usd, gpuSeconds, containers, gpus,
    stages: {stage: usd}, rates: source}."""
    rates = rates or builtin_rates()
    gpu_prices, cpu_price, memory_price = rates["gpu"], rates["cpu"], rates["memory"]
    containers: dict[str, dict] = {}
    for usage in usages:
        key = str(usage.get("container") or id(usage))
        entry = containers.setdefault(key, {**usage, "seconds": 0.0, "enterSeconds": 0.0})
        entry["seconds"] += float(usage.get("seconds") or 0)
        entry["enterSeconds"] += float(usage.get("enterSeconds") or 0)

    usd, gpu_seconds, gpus = 0.0, 0.0, {}
    stages = {stage: 0.0 for stage in STAGES}
    for entry in containers.values():
        seconds = container_seconds(entry, float(entry.get("scaledown") or 0))
        price = 0.0
        if not entry.get("cpuOnly"):  # a CPU-only container bills no GPU
            label = gpu_label(entry.get("gpu")) or "A10"
            price = gpu_prices.get(label, gpu_prices["A10"])
            gpu_seconds += seconds
            gpus[label] = gpus.get(label, 0) + 1
        cost = seconds * (price + float(entry.get("cpu") or MODAL_DEFAULT_CPU) * cpu_price
                          + float(entry.get("memoryGiB") or MODAL_DEFAULT_MEMORY_MB / 1024) * memory_price)
        usd += cost
        stage = entry.get("stage") or "render"
        stages[stage if stage in stages else "render"] += cost
    orchestrator = orchestrator or {}
    own = orchestrator_seconds * (float(orchestrator.get("cpu") or MODAL_DEFAULT_CPU) * cpu_price
                                  + float(orchestrator.get("memoryGiB") or MODAL_DEFAULT_MEMORY_MB / 1024)
                                  * memory_price)
    usd += own
    stages["rank"] += own                     # the job's own CPU container, where Gemini ranks
    return {"usd": round(usd, 4), "gpuSeconds": round(gpu_seconds), "containers": sum(gpus.values()),
            "gpus": gpus, "stages": {k: round(v, 5) for k, v in stages.items()},
            "rates": rates.get("source", "built-in")}


def combine_orchestrators(parts: list[tuple[float, dict | None]]) -> tuple[float, dict]:
    """One (seconds, {cpu, memoryGiB}) for several CPU-function containers (phase 1 and
    phase 2): the seconds add up and cpu and memory are seconds-weighted averages, so
    estimate() prices the combination exactly as the sum of the parts (it is linear)."""
    total = sum(max(0.0, float(seconds or 0)) for seconds, _ in parts)
    if total <= 0:
        return 0.0, {"cpu": MODAL_DEFAULT_CPU, "memoryGiB": MODAL_DEFAULT_MEMORY_MB / 1024}
    cpu = sum(max(0.0, float(s or 0)) * float((o or {}).get("cpu") or MODAL_DEFAULT_CPU) for s, o in parts)
    memory = sum(max(0.0, float(s or 0)) * float((o or {}).get("memoryGiB") or MODAL_DEFAULT_MEMORY_MB / 1024)
                 for s, o in parts)
    return round(total, 2), {"cpu": cpu / total, "memoryGiB": memory / total}
