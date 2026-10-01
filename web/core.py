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
    "ESP32": """ESP32 Pinout Cheatsheet (Standard 38-Pin)
[SAFE TO USE] [INPUT ONLY] [POWER] [COMMUNICATION]

       3V3  | 1       38 |  GND
       EN   | 2       37 |  GPIO 23 (VSPI MOSI)
GPIO 36 (VP)| 3       36 |  GPIO 22 (I2C SCL)
GPIO 39 (VN)| 4       35 |  TX0 (GPIO 1)
GPIO 34     | 5       34 |  RX0 (GPIO 3)
GPIO 35     | 6       33 |  GPIO 21 (I2C SDA)
GPIO 32     | 7       32 |  GND
GPIO 33     | 8       31 |  GPIO 19 (VSPI MISO)
GPIO 25     | 9       30 |  GPIO 18 (VSPI CLK)
GPIO 26     | 10      29 |  GPIO 5  (VSPI CS)
GPIO 27     | 11      28 |  GPIO 17 (TX2)
GPIO 14     | 12      27 |  GPIO 16 (RX2)
GPIO 12     | 13      26 |  GPIO 4
       GND  | 14      25 |  GPIO 0  (Boot mode)
GPIO 13     | 15      24 |  GPIO 2
SD2 (GP 9)  | 16      23 |  GPIO 15
SD3 (GP 10) | 17      22 |  SD1 (GP 8)
CMD (GP 11) | 18      21 |  SD0 (GP 7)
       5V   | 19      20 |  CLK (GP 6)

I2C default: SDA=21 SCL=22. Avoid GPIO 6-11 (flash), 0 (boot), 1/3 (UART0).""",
    "ESP32-S3": """ESP32-S3 Pinout (Beginner-Friendly DevKitC)
[SAFE] [STRAPPING/CAUTION] [POWER] [BEST USED FOR]

                              3V3 |  1           44  | GND
                              3V3 |  2           43  | GPIO 43 (Serial TX - GPS, Displays)
                               EN |  3           42  | GPIO 44 (Serial RX - GPS, Sensors)
  (Analog In, Touch Buttons) GPIO 4 |  4           41  | GPIO 1  (Analog In, Touch Buttons)
  (Analog In, Touch Buttons) GPIO 5 |  5           40  | GPIO 2  (Analog In, Touch Buttons)
  (Analog In, Touch Buttons) GPIO 6 |  6           39  | GPIO 0  (BOOT BUTTON - Avoid using!)
  (Analog In, Touch Buttons) GPIO 7 |  7           38  | GPIO 42 (General IO)
        (General IO)        GPIO 15 |  8           37  | GPIO 41 (General IO)
        (General IO)        GPIO 16 |  9           36  | GPIO 40 (General IO)
 (Analog In - Wi-Fi off only) GPIO 17 | 10           35  | GPIO 39 (General IO)
 (Analog In - Wi-Fi off only) GPIO 18 | 11           34  | GPIO 38 (Built-in RGB NeoPixel LED)
  (Analog In, Touch Buttons) GPIO 8 | 12           33  | GPIO 37 (INTERNAL MEMORY - DO NOT USE)
  (Analog In, Touch Buttons) GPIO 3 | 13           32  | GPIO 36 (INTERNAL MEMORY - DO NOT USE)
 (INPUT ONLY - No outputs!) GPIO 46 | 14           31  | GPIO 35 (INTERNAL MEMORY - DO NOT USE)
  (Analog In, Touch Buttons) GPIO 9 | 15           30  | GPIO 0  (BOOT BUTTON - Avoid using!)
  (Analog In, Touch Buttons) GPIO 10 | 16           29  | GPIO 45 (STRAPPING - Avoid using!)
 (Analog In - Wi-Fi off only) GPIO 11 | 17           28  | GPIO 48 (General IO)
 (Analog In - Wi-Fi off only) GPIO 12 | 18           27  | GPIO 47 (General IO)
 (Analog In - Wi-Fi off only) GPIO 13 | 19           26  | GPIO 21 (General IO)
 (Analog In - Wi-Fi off only) GPIO 14 | 20           25  | GPIO 20 (USB Data+ - DO NOT USE)
                               5V | 21           24  | GPIO 19 (USB Data- - DO NOT USE)
                              GND | 22           23  | GND

[HARDWARE EXAMPLES]
Sensors: ANY Analog In pin (GPIO 4-10). Buttons/LEDs: ANY General IO or Analog In.
I2C OLED/LCD: ANY safe GPIO (default SDA=8, SCL=9).
SPI SD/RFID: ANY safe GPIO (default MOSI=11, MISO=13, SCK=12).
Hardware Serial GPS/GSM: GPIO 43 (TX) and 44 (RX).
DO NOT USE: 0, 3, 19, 20, 35, 36, 37, 45, 46.""",
    "ESP32-S2": """ESP32-S2 Pinout (Typical DevKit)
[SAFE] [STRAPPING/INPUT] [POWER] [COMMUNICATION]

       3V3  | 1       42 |  GND
       3V3  | 2       41 |  TX (43)
       EN   | 3       40 |  RX (44)
GPIO 1      | 4       39 |  GPIO 42
GPIO 2      | 5       38 |  GPIO 41
GPIO 3      | 6       37 |  GPIO 40
GPIO 4      | 7       36 |  GPIO 39
GPIO 5      | 8       35 |  GPIO 38
GPIO 6      | 9       34 |  GPIO 37
GPIO 7      | 10      33 |  GPIO 36
GPIO 8      | 11      32 |  GPIO 35
GPIO 9      | 12      31 |  GPIO 34
GPIO 10     | 13      30 |  GPIO 33
GPIO 11     | 14      29 |  GPIO 26
GPIO 12     | 15      28 |  GPIO 21
GPIO 13     | 16      27 |  USB D+ (20)
GPIO 14     | 17      26 |  USB D- (19)
GPIO 15     | 18      25 |  GPIO 18
GPIO 16     | 19      24 |  GPIO 17
       3V3  | 20      23 |  5V
       GND  | 21      22 |  GND""",
    "ESP32-C3": """ESP32-C3 Pinout (Typical DevKit)
[SAFE] [STRAPPING/INPUT] [POWER] [COMMUNICATION]

       3V3  | 1       30 |  GND
       EN   | 2       29 |  GPIO 0
GPIO 4      | 3       28 |  GPIO 1
GPIO 5      | 4       27 |  GPIO 2 (BOOT)
GPIO 6      | 5       26 |  GPIO 3
GPIO 7      | 6       25 |  GPIO 8 (STRAP)
GPIO 9 (BOOT)| 7      24 |  GPIO 10
TX (GPIO 21)| 8      23 |  GPIO 18 (USB D-)
RX (GPIO 20)| 9      22 |  GPIO 19 (USB D+)
       GND  | 10      21 |  5V""",
    "ESP32-C6": """ESP32-C6 Pinout (Typical DevKit)
[SAFE] [STRAPPING/INPUT] [POWER] [COMMUNICATION]

       3V3  | 1       30 |  GND
       EN   | 2       29 |  GPIO 0
GPIO 4      | 3       28 |  GPIO 1
GPIO 5      | 4       27 |  GPIO 2
GPIO 6      | 5       26 |  GPIO 3
GPIO 7      | 6       25 |  GPIO 8
GPIO 9 (BOOT)| 7      24 |  GPIO 10
TX (GPIO 16)| 8      23 |  GPIO 11
RX (GPIO 17)| 9      22 |  GPIO 12 (USB D-)
       GND  | 10      21 |  GPIO 13 (USB D+)
       5V   | 11      20 |  GND""",
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
    result: dict = {"ok": True, "chip": chip, "features": features, "mac": mac,
                    "flash": flash, "raw": output[-3000:]}
    # Factory-state check: peek at bootloader region (same logic as CLI board_info)
    boot_off = "0x0" if any(x in chip for x in ("S3", "C3", "C6")) else "0x1000"
    with tempfile.TemporaryDirectory() as td:
        peek = os.path.join(td, "boot_peek.bin")
        c2, _ = run_capture(f"esptool {port_arg} --baud {baud} read-flash {boot_off} 0x20 {peek}", timeout=30)
        if c2 == 0 and os.path.exists(peek):
            try:
                with open(peek, "rb") as f:
                    data = f.read()
                if all(b == 0xFF for b in data):
                    result["factory_state"] = "blank"
                    result["factory_note"] = f"BRAND NEW / BLANK — no bootloader at {boot_off}."
                elif data and data[0] == 0xE9:
                    result["factory_state"] = "used"
                    result["factory_note"] = f"USED — bootloader magic found at {boot_off}."
                else:
                    result["factory_state"] = "unknown"
                    result["factory_note"] = f"Memory at {boot_off} is neither blank nor standard bootloader."
            except Exception:
                result["factory_state"] = "unknown"
        # Firmware descriptor: search app region for ESP_APP_DESC magic
        app_peek = os.path.join(td, "app_peek.bin")
        c3, _ = run_capture(f"esptool {port_arg} --baud {baud} read-flash 0x10000 0x1000 {app_peek}", timeout=30)
        if c3 == 0 and os.path.exists(app_peek):
            try:
                with open(app_peek, "rb") as f:
                    app = f.read()
                idx = app.find(b'\x32\x54\xcd\xab')
                if idx != -1:
                    def _s(off: int, ln: int) -> str:
                        return app[off:off + ln].split(b'\x00')[0].decode('utf-8', errors='ignore')
                    result["firmware"] = {
                        "project": _s(idx + 48, 32),
                        "compiled_time": _s(idx + 80, 16),
                        "compiled_date": _s(idx + 96, 16),
                        "idf_version": _s(idx + 112, 32),
                    }
            except Exception:
                pass
    return result


def suggest_fs_mb(total_bytes: int) -> float:
    """Same formula as CLI partition manager: +20% overhead, round up to 0.5 MB, min 0.5."""
    calc_mb = (total_bytes / (1024 * 1024)) * 1.2
    suggested = max(0.5, round(calc_mb * 2) / 2.0)
    return suggested


def parse_spiffs_from_csv(csv_text: str) -> dict | None:
    """Find spiffs/littlefs/ffat partition offset+size from partitions.csv text."""
    for line in csv_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 5 and (parts[0] in ("spiffs", "littlefs", "ffat")
                                or parts[2] in ("spiffs", "littlefs", "ffat")):
            try:
                size_int = int(parts[4].rstrip("Kk"), 0) * 1024 if parts[4].upper().endswith("K") \
                    else int(parts[4], 0)
            except ValueError:
                try:
                    size_int = int(parts[4], 16)
                except ValueError:
                    continue
            return {"name": parts[0], "offset": parts[3], "size": size_int,
                    "size_human": f"{size_int / 1024:.0f}K"}
    return None


def build_littlefs_image(data_dir: str, size_bytes: int, out_bin: str) -> tuple[bool, str]:
    """Pack data_dir into littlefs.bin via mklittlefs. Returns (ok, logs)."""
    tool = find_mklittlefs()
    if not tool:
        return False, "mklittlefs not found (install ESP32 core via Core Manager first)"
    if not os.path.isdir(data_dir):
        return False, f"data directory not found: {data_dir}"
    code, out = run_capture(f'"{tool}" -c "{data_dir}" -s {size_bytes} "{out_bin}"', timeout=120)
    if code != 0 or not os.path.exists(out_bin):
        return False, f"mklittlefs failed:\n{out}"
    return True, f"Packed {data_dir} ({size_bytes} bytes) -> {out_bin}\n{out[-1000:]}"


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
