#!/usr/bin/env python3
"""
Linux sender for CPU COOLER V4.0 style USB HID display/cooler.

Reverse-engineered packet from the Windows installer:
  VID:PID = AA88:8666
  Active HID output report/payload is 11 bytes on the tested device.
  The vendor helper allocates a 24-byte pipe buffer, but only bytes 0..10
  carry data.
  Byte 0  = 0
  Byte 1  = CPU temperature, C
  Byte 2  = GPU temperature, C
  Byte 3  = CPU usage, %
  Byte 4  = memory usage, %
  Byte 5  = motherboard/board temperature, C
  Byte 6  = GPU usage, %
  Byte 7  = fan RPM high byte
  Byte 8  = fan RPM low byte
  Byte 9  = CPU power high byte
  Byte 10 = CPU power low byte
"""

from __future__ import annotations

import argparse
import os
import subprocess
import time
from pathlib import Path

VID = 0xAA88
PID = 0x8666
REPORT_LEN = 11


def clamp_u8(value: float | int | None) -> int:
    if value is None:
        return 0
    return max(0, min(255, int(round(value))))


def clamp_u16(value: float | int | None) -> int:
    if value is None:
        return 0
    return max(0, min(65535, int(round(value))))


def read_text(path: Path) -> str | None:
    try:
        return path.read_text(errors="replace").strip()
    except OSError:
        return None


def read_int(path: Path) -> int | None:
    text = read_text(path)
    if text is None:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def hidraw_matches(hidraw: Path, vid: int, pid: int) -> bool:
    uevent = read_text(hidraw / "device" / "uevent") or ""
    for line in uevent.splitlines():
        if not line.startswith("HID_ID="):
            continue
        parts = line.split("=", 1)[1].split(":")
        if len(parts) == 3:
            try:
                return int(parts[1], 16) == vid and int(parts[2], 16) == pid
            except ValueError:
                return False

    # Fallback for kernels/devices exposing USB PRODUCT instead of HID_ID here.
    node = hidraw / "device"
    for _ in range(8):
        uevent = read_text(node / "uevent") or ""
        product = f"PRODUCT={vid:x}/{pid:x}/"
        if product.lower() in uevent.lower():
            return True
        parent = node.parent
        if parent == node:
            break
        node = parent
    return False


def find_hidraw(vid: int = VID, pid: int = PID) -> Path:
    for hidraw in sorted(Path("/sys/class/hidraw").glob("hidraw*")):
        if hidraw_matches(hidraw, vid, pid):
            return Path("/dev") / hidraw.name
    raise FileNotFoundError(f"hidraw device for VID_{vid:04X}&PID_{pid:04X} not found")


def all_hwmon_dirs() -> list[Path]:
    return sorted(Path("/sys/class/hwmon").glob("hwmon*"))


def temp_milli_to_c(value: int | None) -> float | None:
    if value is None:
        return None
    return value / 1000.0 if value > 1000 else float(value)


def read_labeled_temps(wanted_labels: tuple[str, ...]) -> list[float]:
    found: list[float] = []
    wanted = tuple(label.lower() for label in wanted_labels)
    for hwmon in all_hwmon_dirs():
        for temp_input in hwmon.glob("temp*_input"):
            base = temp_input.name.replace("_input", "")
            label = (read_text(hwmon / f"{base}_label") or read_text(hwmon / "name") or "").lower()
            if any(w in label for w in wanted):
                value = temp_milli_to_c(read_int(temp_input))
                if value is not None and 0 <= value <= 130:
                    found.append(value)
    return found


def cpu_temp_c() -> int:
    temps = read_labeled_temps(("package", "tdie", "tctl", "cpu", "coretemp", "k10temp"))
    if not temps:
        for zone in Path("/sys/class/thermal").glob("thermal_zone*"):
            zone_type = (read_text(zone / "type") or "").lower()
            if any(key in zone_type for key in ("x86_pkg_temp", "cpu", "soc", "k10temp")):
                value = temp_milli_to_c(read_int(zone / "temp"))
                if value is not None and 0 <= value <= 130:
                    temps.append(value)
    return clamp_u8(max(temps) if temps else 0)


def board_temp_c() -> int:
    temps = read_labeled_temps(("systin", "system", "motherboard", "mainboard", "board"))
    return clamp_u8(max(temps) if temps else 0)


def fan_rpm() -> int:
    values: list[int] = []
    for hwmon in all_hwmon_dirs():
        for fan_input in hwmon.glob("fan*_input"):
            value = read_int(fan_input)
            if value is not None and 0 <= value <= 65535:
                values.append(value)
    return clamp_u16(max(values) if values else 0)


def mem_percent() -> int:
    info: dict[str, int] = {}
    text = read_text(Path("/proc/meminfo")) or ""
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            try:
                info[parts[0].rstrip(":")] = int(parts[1])
            except ValueError:
                pass
    total = info.get("MemTotal")
    available = info.get("MemAvailable")
    if not total or available is None:
        return 0
    return clamp_u8((total - available) * 100 / total)


def proc_cpu_times() -> tuple[int, int]:
    line = (read_text(Path("/proc/stat")) or "").splitlines()[0]
    parts = [int(x) for x in line.split()[1:]]
    idle = parts[3] + (parts[4] if len(parts) > 4 else 0)
    total = sum(parts)
    return idle, total


def cpu_percent(prev: tuple[int, int] | None) -> tuple[int, tuple[int, int]]:
    current = proc_cpu_times()
    if prev is None:
        return 0, current
    idle_delta = current[0] - prev[0]
    total_delta = current[1] - prev[1]
    if total_delta <= 0:
        return 0, current
    return clamp_u8((1 - idle_delta / total_delta) * 100), current


def nvidia_metrics() -> tuple[int, int]:
    try:
        output = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=temperature.gpu,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            timeout=1.5,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return 0, 0
    first = output.strip().splitlines()[0].split(",")
    if len(first) < 2:
        return 0, 0
    try:
        return clamp_u8(float(first[0])), clamp_u8(float(first[1]))
    except ValueError:
        return 0, 0


def gpu_metrics() -> tuple[int, int]:
    temp, util = nvidia_metrics()
    if temp or util:
        return temp, util

    temps: list[float] = []
    utils: list[int] = []
    for card in Path("/sys/class/drm").glob("card[0-9]*"):
        busy = read_int(card / "device" / "gpu_busy_percent")
        if busy is not None:
            utils.append(busy)
        for temp_input in (card / "device" / "hwmon").glob("hwmon*/temp*_input"):
            value = temp_milli_to_c(read_int(temp_input))
            if value is not None and 0 <= value <= 130:
                temps.append(value)
    return clamp_u8(max(temps) if temps else 0), clamp_u8(max(utils) if utils else 0)


class RaplPower:
    def __init__(self) -> None:
        self.path = self._find_energy_path()
        self.prev: tuple[int, float] | None = None

    @staticmethod
    def _find_energy_path() -> Path | None:
        for path in Path("/sys/class/powercap").glob("intel-rapl:*/energy_uj"):
            return path
        return None

    def watts(self) -> int:
        if self.path is None:
            return 0
        energy = read_int(self.path)
        now = time.monotonic()
        if energy is None:
            return 0
        if self.prev is None:
            self.prev = (energy, now)
            return 0
        prev_energy, prev_time = self.prev
        self.prev = (energy, now)
        elapsed = now - prev_time
        if elapsed <= 0:
            return 0
        delta = energy - prev_energy
        if delta < 0:
            return 0
        return clamp_u16((delta / 1_000_000.0) / elapsed)


def build_report(cpu_t: int, gpu_t: int, cpu_u: int, mem_u: int, board_t: int, gpu_u: int, rpm: int, cpu_w: int) -> bytes:
    rpm = clamp_u16(rpm)
    cpu_w = clamp_u16(cpu_w)
    report = bytearray(REPORT_LEN)
    report[0] = 0
    report[1] = clamp_u8(cpu_t)
    report[2] = clamp_u8(gpu_t)
    report[3] = clamp_u8(cpu_u)
    report[4] = clamp_u8(mem_u)
    report[5] = clamp_u8(board_t)
    report[6] = clamp_u8(gpu_u)
    report[7] = (rpm >> 8) & 0xFF
    report[8] = rpm & 0xFF
    report[9] = (cpu_w >> 8) & 0xFF
    report[10] = cpu_w & 0xFF
    return bytes(report)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", help="hidraw path, for example /dev/hidraw3")
    parser.add_argument("--interval", type=float, default=1.0, help="seconds between updates")
    parser.add_argument("--once", action="store_true", help="send one packet and exit")
    parser.add_argument("--dry-run", action="store_true", help="print packets instead of writing HID")
    args = parser.parse_args()

    dev = Path(args.device) if args.device else find_hidraw()
    fd = None if args.dry_run else os.open(dev, os.O_WRONLY)
    cpu_prev: tuple[int, int] | None = None
    rapl = RaplPower()

    try:
        while True:
            cpu_u, cpu_prev = cpu_percent(cpu_prev)
            gpu_t, gpu_u = gpu_metrics()
            report = build_report(
                cpu_t=cpu_temp_c(),
                gpu_t=gpu_t,
                cpu_u=cpu_u,
                mem_u=mem_percent(),
                board_t=board_temp_c(),
                gpu_u=gpu_u,
                rpm=fan_rpm(),
                cpu_w=rapl.watts(),
            )
            if args.dry_run:
                print(" ".join(f"{b:02x}" for b in report))
            else:
                os.write(fd, report)
            if args.once:
                break
            time.sleep(args.interval)
    finally:
        if fd is not None:
            os.close(fd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
