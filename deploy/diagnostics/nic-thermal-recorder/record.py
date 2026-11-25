"""Read Linux thermal, NIC and PCIe counters into a bounded JSONL log."""

import argparse
import hashlib
import json
import logging
import os
import signal
import subprocess
import threading
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path


def read(path):
    try:
        return path.read_text().strip()
    except OSError:
        return None


def temperatures(sysfs):
    values = []
    for hwmon in sorted((sysfs / "class/hwmon").glob("hwmon*")):
        for sensor in sorted(hwmon.glob("temp*_input")):
            raw = read(sensor)
            if raw is None:
                continue
            values.append(
                {
                    "chip": read(hwmon / "name"),
                    "device": str((hwmon / "device").resolve()),
                    "sensor": sensor.name,
                    "label": read(sensor.with_name(sensor.name.replace("_input", "_label"))),
                    "temperature_c": int(raw) / 1000,
                }
            )
    return values


def nic_state(sysfs, name):
    net = sysfs / "class/net" / name
    device = net / "device"
    readings = []
    for hwmon in sorted((device / "hwmon").glob("hwmon*")):
        for sensor in sorted(hwmon.glob("temp*_input")):
            raw = read(sensor)
            if raw is not None:
                readings.append({"sensor": sensor.name, "temperature_c": int(raw) / 1000})
    return {
        "present": net.exists(),
        "device": str(device.resolve()),
        "driver": (device / "driver").resolve().name if (device / "driver").exists() else None,
        "temperature_c": readings or None,
        "temperature_status": "available" if readings else "no_exposed_sensor",
        "link": {k: read(net / k) for k in ("carrier", "operstate", "speed")},
        "counters": {
            p.name: int(v) for p in sorted((net / "statistics").glob("*")) if (v := read(p)) is not None
        },
        "pcie": {
            k: read(device / k)
            for k in (
                "current_link_speed",
                "current_link_width",
                "power/control",
                "power/runtime_status",
                "link/l1_aspm",
                "link/l1_1_aspm",
                "link/l1_2_aspm",
                "aer_dev_correctable",
                "aer_dev_nonfatal",
                "aer_dev_fatal",
            )
        },
    }


def gpus():
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,temperature.gpu,utilization.gpu,power.draw",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "unavailable", "error": str(exc)}
    return {
        "status": "available" if result.returncode == 0 else "error",
        "csv_columns": ["index", "temperature_c", "utilization_pct", "power_w"],
        "rows": result.stdout.strip().splitlines(),
        "error": result.stderr.strip() or None,
    }


def sample(sysfs, interfaces):
    return {
        "sampled_at": datetime.now(timezone.utc).isoformat(),  # noqa: UP017 Ubuntu 22.04 Python 3.10
        "monotonic_seconds": time.monotonic(),
        "boot_id": read(Path("/proc/sys/kernel/random/boot_id")),
        "kernel": os.uname().release,
        "temperatures": temperatures(sysfs),
        "nics": {name: nic_state(sysfs, name) for name in interfaces},
        "pcie_upstream": {
            k: read(sysfs / "bus/pci/devices/0000:08:05.0" / k)
            for k in ("aer_dev_correctable", "aer_dev_nonfatal", "aer_dev_fatal")
        },
        "gpus": gpus(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--interval", type=float, default=30)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.interval <= 0 or (not args.once and args.output is None):
        parser.error("Use a positive interval and --output for continuous recording")
    source_sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    logger = logging.getLogger("nic_thermal")
    logger.setLevel(logging.INFO)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(args.output, maxBytes=32 * 1024 * 1024, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    while not stop.is_set():
        item = sample(Path("/sys"), ["enp11s0", "enp12s0"])
        item["source_sha256"] = source_sha
        payload = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
        if args.output:
            logger.info(payload)
        else:
            print(payload)
        if args.once:
            break
        stop.wait(args.interval)


if __name__ == "__main__":
    main()
