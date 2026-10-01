"""Non-interactive core logic for Lazy-ESP32 Web version.

Reuses the same tools as lazy_esp.py (arduino-cli, esptool, mklittlefs)
but exposes pure functions returning dicts instead of input()/print().
"""
from __future__ import annotations

import asyncio
import glob
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path

FQBN_MAP = {
    "esp32": "esp32:esp32:esp32",
    "esp32-s2": "esp32:esp32:esp32s2",
    "esp32-s3": "esp32:esp32:esp32s3",
    "esp32-c3": "esp32:esp32:esp32c3",
    "esp32-c6": "esp32:esp32:esp32c6",
}

CHIP_TO_FQBN = {
    "ESP32-S3": "esp32:esp32:esp32s3",
    "ESP32-S2": "esp32:esp32:esp32s2",
    "ESP32-C3": "esp32:esp32:esp32c3",
    "ESP32-C6": "esp32:esp32:esp32c6",
    "ESP32": "esp32:esp32:esp32",
}

PINOUTS = {
    "ESP32": "Standard 38-pin DevKit. Safe: GPIO 4,5,12-19,21-23,25-27,32,33. Input-only: 34-36,39. Avoid: 6-11 (SPI flash), 0 (boot), 1/3 (UART0). I2C default SDA=21 SCL=22. VSPI MOSI=23 MISO=19 CLK=18 CS=5.",
    "ESP32-S3": "Native USB (19/20). Safe analog/touch: GPIO 4-10. General IO: 11-14,15-18,21,38-48 (except strapping). Avoid: 0 (BOOT), 3,19,20 (USB), 35/36/37 (octal flash - DO NOT USE), 45/46 (strapping). RGB LED=38. UART TX=43 RX=44.",
    "ESP32-S2": "Native USB (19/20). Safe: GPIO 1-18,21,26,33-42. Avoid strapping: 0,45,46. TX=43 RX=44.",
    "ESP32-C3": "RISC-V, Native USB (18/19). Safe: GPIO 0-7,10,18-21. Strapping/BOOT: 2,8,9. TX=21 RX=20.",
    "ESP32-C6": "RISC-V + 802.15.4. Safe: GPIO 0-7,11. BOOT: 9. USB: 12/13. TX=16 RX=17.",
}

PINOUT_ASCII = {
    "ESP32": """38-pin Standard
3V3 |1    38| GND | EN |2    37| GPIO23 (VSPI MOSI)
VP(36)|3  36| GPIO22 (I2C SCL) | VN(39)|4 35| TX0(1)
34|5   34| RX0(3) | 35|6   33| GPIO21 (I2C SDA)
32|7   32| GND | 33|8   31| GPIO19 (MISO)
25|9   30| GPIO18 (CLK) | 26|10 29| GPIO5 (CS)
27|11  28| GPIO17(TX2) | 14|12 27| GPIO16(RX2)
12|13  26| GPIO4 | GND|14  25| GPIO0(BOOT)
13|15  24| GPIO2 | SD2(9)|16 23| GPIO15
SD3(10)|17 22| SD1(8) | CMD(11)|18 21| SD0(7)
5V|19   20| CLK(6)""",
    "ESP32-S3": "See CLI pinout: safe Analog 4-10, General 11-18,21,38-48. Avoid 0,3,19,20,35,36,37,45,46. USB D-/D+ = 19/20. TX=43 RX=44. RGB=38.",
    "ESP32-S2": "Safe 1-18,21,26,33-42. TX=43 RX=44. USB 19/20.",
    "ESP32-C3": "Safe 0-7,10,18-21. BOOT pins 2,8,9. TX=21 RX=20. USB 18/19.",
    "ESP32-C6": "Safe 0-7,11. BOOT=9. TX=16 RX=17. USB 12/13.",
}


def list_serial_ports() -> list[dict]:
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    out = []
    for p in list_ports.comports():
        out.append({"device": p.device, "description": p.description or "", "hwid": p.hwid or ""})
    return out


def run_capture(cmd: str, timeout: int = 30) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "Command timed out"
    except Exception as e:
        return 1, str(e)


async def stream_command(cmd: str):
    """Async generator yielding log lines from a shell command."""
    proc = await asyncio.create_subprocess_shell(
        cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    assert proc.stdout is not None
    while True:
        line = await proc.stdout.readline()
        if not line:
            break
        yield line.decode(errors="replace")
    await proc.wait()
    yield f"\n[exit code: {proc.returncode}]\n"


def detect_chip(port: str = "") -> dict:
    port_arg = f"--port {port}" if port else ""
    code, output = run_capture(f"esptool {port_arg} flash-id", timeout=20)
    chip = "Unknown"
    fqbn = "esp32:esp32:esp32"
    if code != 0 and not output.strip():
        return {"chip": chip, "fqbn": fqbn, "ok": False, "raw": "Could not communicate. Is board connected?"}
    for key, mapped in CHIP_TO_FQBN.items():
        if key in output:
            # Check longer names first (S3/S2/C3/C6 before plain ESP32)
            if key == "ESP32" and any(x in output for x in ("ESP32-S3", "ESP32-S2", "ESP32-C3", "ESP32-C6")):
                continue
            chip = key if key != "ESP32" else "ESP32"
            fqbn = mapped
            break
    if "ESP32-S3" in output:
        chip, fqbn = "ESP32-S3", CHIP_TO_FQBN["ESP32-S3"]
    elif "ESP32-S2" in output:
        chip, fqbn = "ESP32-S2", CHIP_TO_FQBN["ESP32-S2"]
    elif "ESP32-C3" in output:
        chip, fqbn = "ESP32-C3", CHIP_TO_FQBN["ESP32-C3"]
    elif "ESP32-C6" in output:
        chip, fqbn = "ESP32-C6", CHIP_TO_FQBN["ESP32-C6"]
    return {"chip": chip, "fqbn": fqbn, "ok": chip != "Unknown", "raw": output[-2000:]}


def detect_flash_size(port: str = "") -> dict:
    port_arg = f"--port {port}" if port else ""
    code, output = run_capture(f"esptool {port_arg} flash-id", timeout=20)
    mb = 4
    label = "Unknown (default 4MB)"
    for cand in ("16MB", "8MB", "4MB", "2MB"):
        if cand in output:
            label = cand
            mb = int(cand.replace("MB", ""))
            break
    if "Detected flash size:" in output:
        for line in output.splitlines():
            if "Detected flash size:" in line:
                label = line.split("Detected flash size:")[1].strip()
                break
    return {"flash_mb": mb, "flash_label": label, "ok": code == 0, "raw": output[-2000:]}


def board_diagnostics(port: str = "", baud: str = "115200") -> dict:
    port_arg = f"--port {port}" if port else ""
    code, output = run_capture(f"esptool {port_arg} --baud {baud} flash-id", timeout=25)
    if code != 0 and not output:
        return {"ok": False, "error": "Failed to connect to board"}
    chip, features, mac, flash = "Unknown", "Unknown", "Unknown", "Unknown"
    for line in output.splitlines():
        if line.startswith("Detecting chip type..."):
            chip = line.split("...", 1)[1].strip() if "..." in line else line
        elif line.startswith("Features:"):
            features = line.split("Features:", 1)[1].strip()
        elif line.startswith("MAC:"):
            mac = line.split("MAC:", 1)[1].strip()
        elif line.startswith("Detected flash size:"):
            flash = line.split("Detected flash size:", 1)[1].strip()
    return {"ok": True, "chip": chip, "features": features, "mac": mac,
            "flash": flash, "raw": output[-3000:]}


def scan_includes(source: str) -> list[str]:
    includes = re.findall(r'#include\s*[<"](.*?)\.h[>"]', source)
    ignore = {"WiFi", "Wire", "SPI", "FS", "LittleFS", "SPIFFS", "EEPROM", "Arduino",
              "ESPmDNS", "WebServer", "HTTPClient", "Update", "Preferences",
              "BluetoothSerial", "BLEDevice"}
    return sorted({lib for lib in includes if lib not in ignore})


def prepare_sketch_dir(file_path: str, workdir: str) -> str:
    """Copy sketch into Arduino-compatible folder. Returns compile target path."""
    file_name = os.path.basename(file_path)
    base_name = os.path.splitext(file_name)[0]
    folder_name = os.path.basename(os.path.dirname(os.path.abspath(file_path)))
    if folder_name == base_name:
        return file_path
    project_dir = os.path.join(workdir, base_name)
    os.makedirs(project_dir, exist_ok=True)
    shutil.copy2(file_path, os.path.join(project_dir, file_name))
    orig_folder = os.path.dirname(os.path.abspath(file_path))
    for extra in ("partitions.csv",):
        src = os.path.join(orig_folder, extra)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(project_dir, extra))
    data_dir = os.path.join(orig_folder, "data")
    if os.path.isdir(data_dir):
        shutil.copytree(data_dir, os.path.join(project_dir, "data"), dirs_exist_ok=True)
    return os.path.join(project_dir, file_name)


def parse_partition_request(flash_mb: float, ota: bool, fs_mb: float) -> dict:
    total_kb = int(flash_mb * 1024)
    available_kb = total_kb - 64
    fs_kb = int(fs_mb * 1024)
    remaining_kb = available_kb - fs_kb
    if remaining_kb <= 0:
        raise ValueError("File system allocation exceeds flash size")
    app_kb = remaining_kb // 2 if ota else remaining_kb
    return {"total_kb": total_kb, "available_kb": available_kb, "fs_kb": fs_kb,
            "app_kb": app_kb, "ota": ota, "flash_mb": flash_mb}


def generate_partitions_csv(flash_mb: float, ota: bool, fs_mb: float) -> str:
    p = parse_partition_request(flash_mb, ota, fs_mb)
    app_kb, fs_kb = p["app_kb"], p["fs_kb"]
    lines = ["# Name, Type, SubType, Offset, Size, Flags",
             f"# FlashSize: {flash_mb:g}M"]
    lines += ["nvs,      data, nvs,     0x9000,  20K,",
              "otadata,  data, ota,     0xe000,  8K,"]
    if ota:
        lines.append(f"app0,     app,  ota_0,   0x10000, {app_kb}K,")
        lines.append(f"app1,     app,  ota_1,   ,        {app_kb}K,")
    else:
        lines.append(f"app0,     app,  factory, 0x10000, {app_kb}K,")
    lines.append(f"spiffs,   data, spiffs,  ,        {fs_kb}K,")
    return "\n".join(lines) + "\n"


def convert_assets_to_header(files: dict[str, str]) -> str:
    """files: filename -> text content. Returns web_assets.h content."""
    out = ["#pragma once", "", "// Auto-generated by Lazy-ESP32 Web UI", ""]
    for fname, content in files.items():
        var = re.sub(r'[^0-9a-zA-Z_]', '_', os.path.basename(fname))
        out.append(f"const char* {var} = R\"=====(\n{content}\n)=====\";\n")
    return "\n".join(out)


def find_mklittlefs() -> str | None:
    base = os.path.expanduser("~/.arduino15/packages/esp32/tools/mklittlefs")
    if not os.path.exists(base):
        return None
    for root, _, filenames in os.walk(base):
        if "mklittlefs" in filenames:
            return os.path.join(root, "mklittlefs")
    return None


def discover_ota_devices(timeout: float = 3.0) -> list[dict]:
    try:
        from zeroconf import ServiceBrowser, Zeroconf
    except ImportError:
        return []
    devices: list[dict] = []

    class Listener:
        def add_service(self, zc, typ, name):
            info = zc.get_service_info(typ, name)
            if info and info.addresses:
                try:
                    addr = socket.inet_ntoa(info.addresses[0])
                except Exception:
                    return
                devices.append({"name": name.replace("._arduino._tcp.local.", ""), "ip": addr})

    zc = Zeroconf()
    try:
        ServiceBrowser(zc, "_arduino._tcp.local.", Listener())
        time.sleep(timeout)
    finally:
        zc.close()
    return devices


def arduino_cli_available() -> bool:
    return shutil.which("arduino-cli") is not None


def esptool_available() -> bool:
    return shutil.which("esptool") is not None
