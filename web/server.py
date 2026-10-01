"""Lazy-ESP32 Web server — FastAPI backend + vanilla SPA.

Run with:  python3 lazy_web.py   (auto-opens browser, no terminal interaction needed)
Or:        python3 web/server.py
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, Query, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import core

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"
BUILD_ROOT = BASE_DIR / "builds"
BUILD_ROOT.mkdir(exist_ok=True)

app = FastAPI(title="Lazy-ESP32 Web", version="1.0.0")


# ---------- helpers ----------
def _save_upload(upload: UploadFile, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "wb") as f:
        shutil.copyfileobj(upload.file, f)
    return dest


async def _run_logged(cmd: str, timeout: int = 600) -> dict:
    """Run shell cmd, capture combined output."""
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
        assert proc.stdout is not None
        chunks: list[str] = []
        try:
            async with asyncio.timeout(timeout):
                while True:
                    line = await proc.stdout.readline()
                    if not line:
                        break
                    chunks.append(line.decode(errors="replace"))
        except TimeoutError:
            proc.kill()
            chunks.append("\n[TIMEOUT after %ss]\n" % timeout)
        await proc.wait()
        return {"ok": proc.returncode == 0, "code": proc.returncode, "logs": "".join(chunks)[-20000:]}
    except Exception as e:
        return {"ok": False, "code": 1, "logs": f"Failed to run: {e}"}


def _fqbn_with_partitions(fqbn: str, partitions_csv: Path | None) -> str:
    if partitions_csv and partitions_csv.exists():
        if "PartitionScheme" not in fqbn:
            fqbn += ":PartitionScheme=custom"
        for line in partitions_csv.read_text(errors="ignore").splitlines():
            if "FlashSize:" in line:
                fs = line.split("FlashSize:")[1].strip()
                if f"FlashSize={fs}" not in fqbn:
                    fqbn += f",FlashSize={fs}"
                break
    return fqbn


# ---------- basic ----------
@app.get("/api/health")
def health():
    return {"ok": True, "arduino_cli": core.arduino_cli_available(),
            "esptool": core.esptool_available(),
            "mklittlefs": core.find_mklittlefs() is not None}


@app.get("/api/ports")
def ports():
    return {"ports": core.list_serial_ports()}


@app.post("/api/detect")
def detect(body: dict):
    return core.detect_chip(body.get("port", ""))


@app.get("/api/flash-size")
def flash_size(port: str = ""):
    return core.detect_flash_size(port)


@app.get("/api/pinout")
def pinout(chip: str = "ESP32"):
    key = (chip or "ESP32").upper().replace("_", "-")
    # normalize e.g. "esp32-s3" -> "ESP32-S3"
    match = None
    for k in core.PINOUTS:
        if k in key:
            # prefer longest match (S3/C3/C6 over plain)
            if match is None or len(k) > len(match):
                match = k
    match = match or "ESP32"
    return {"chip": match, "summary": core.PINOUTS[match],
            "ascii": core.PINOUT_ASCII.get(match, "")}


@app.get("/api/board-info")
def board_info(port: str = "", baud: str = "115200"):
    return core.board_diagnostics(port, baud)


# ---------- partitions ----------
@app.post("/api/partitions/calc")
def partitions_calc(body: dict):
    try:
        p = core.parse_partition_request(
            float(body.get("flash_mb", 4)), bool(body.get("ota", False)), float(body.get("fs_mb", 1.5)))
        return {"ok": True, **p}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/partitions/generate")
def partitions_generate(body: dict):
    try:
        csv = core.generate_partitions_csv(
            float(body.get("flash_mb", 4)), bool(body.get("ota", False)), float(body.get("fs_mb", 1.5)))
        return {"ok": True, "csv": csv}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------- compile / flash ----------
@app.post("/api/compile")
async def compile_sketch(
    sketch: UploadFile = File(...),
    fqbn: str = Form("esp32:esp32:esp32"),
    partitions: UploadFile | None = File(default=None),
):
    if not core.arduino_cli_available():
        return JSONResponse({"ok": False, "error": "arduino-cli not found on server. Run install.sh"}, status_code=400)
    sid = uuid.uuid4().hex[:8]
    work = BUILD_ROOT / sid
    work.mkdir(parents=True, exist_ok=True)
    sketch_path = _save_upload(sketch, work / "upload" / (sketch.filename or "sketch.ino"))
    part_path: Path | None = None
    if partitions is not None and partitions.filename:
        part_path = _save_upload(partitions, work / "upload" / "partitions.csv")
        # place next to sketch for FQBN detection
        shutil.copy2(part_path, work / "upload" / "partitions.csv")
    # Arduino quirk fix
    target = core.prepare_sketch_dir(str(sketch_path), str(work / "sketch"))
    if part_path:
        # ensure partitions.csv sits beside the sketch target too
        dst = Path(target).parent / "partitions.csv"
        if not dst.exists():
            shutil.copy2(part_path, dst)
    eff_fqbn = _fqbn_with_partitions(fqbn, Path(target).parent / "partitions.csv")
    build_path = work / "build"
    build_path.mkdir(exist_ok=True)
    clean = "--clean" if "PartitionScheme=custom" in eff_fqbn else ""
    # auto-install libs (best effort, non-fatal)
    try:
        src = Path(target).read_text(errors="ignore")
        for lib in core.scan_includes(src):
            await _run_logged(f'arduino-cli lib install "{lib}"', timeout=120)
    except Exception:
        pass
    cmd = f'arduino-cli compile {clean} --build-path "{build_path}" -b {eff_fqbn} "{target}"'
    res = await _run_logged(cmd, timeout=600)
    # locate .bin
    bins = list(build_path.glob("*.bin"))
    bin_url = None
    if res["ok"] and bins:
        # keep first merged bin for download
        bin_url = f"/api/download/{sid}/{bins[0].name}"
    return {"ok": res["ok"], "logs": res["logs"], "fqbn": eff_fqbn,
            "bin_url": bin_url, "session": sid}


@app.post("/api/flash-usb")
async def flash_usb(
    file: UploadFile = File(...),
    port: str = Form(""),
    baud: str = Form("460800"),
    fqbn: str = Form("esp32:esp32:esp32"),
    addr: str = Form("0x10000"),
    partitions: UploadFile | None = File(default=None),
):
    sid = uuid.uuid4().hex[:8]
    work = BUILD_ROOT / sid
    work.mkdir(parents=True, exist_ok=True)
    fname = file.filename or "firmware.bin"
    fpath = _save_upload(file, work / "upload" / fname)
    ext = Path(fname).suffix.lower()
    port_arg = f"--port {port}" if port else ""
    baud_arg = f"--baud {baud}" if baud else ""

    if ext == ".bin":
        if not core.esptool_available():
            return {"ok": False, "error": "esptool not found on server"}
        res = await _run_logged(f"esptool {port_arg} {baud_arg} write-flash -z {addr} \"{fpath}\"", timeout=300)
        return {"ok": res["ok"], "logs": res["logs"], "mode": "bin"}
    elif ext == ".ino":
        if not core.arduino_cli_available():
            return JSONResponse({"ok": False, "error": "arduino-cli not found"}, status_code=400)
        part_path = None
        if partitions is not None and partitions.filename:
            part_path = _save_upload(partitions, work / "upload" / "partitions.csv")
        target = core.prepare_sketch_dir(str(fpath), str(work / "sketch"))
        if part_path:
            dst = Path(target).parent / "partitions.csv"
            if not dst.exists():
                shutil.copy2(part_path, dst)
        eff_fqbn = _fqbn_with_partitions(fqbn, Path(target).parent / "partitions.csv")
        try:
            src = Path(target).read_text(errors="ignore")
            for lib in core.scan_includes(src):
                await _run_logged(f'arduino-cli lib install "{lib}"', timeout=120)
        except Exception:
            pass
        build_path = work / "build"
        build_path.mkdir(exist_ok=True)
        clean = "--clean" if "PartitionScheme=custom" in eff_fqbn else ""
        port_flag = f"-p {port}" if port else ""
        cmd = f'arduino-cli compile {clean} --build-path "{build_path}" --upload -b {eff_fqbn} {port_flag} "{target}"'
        res = await _run_logged(cmd, timeout=600)
        return {"ok": res["ok"], "logs": res["logs"], "mode": "ino", "fqbn": eff_fqbn}
    else:
        return JSONResponse({"ok": False, "error": f"Unsupported file type {ext}. Use .ino or .bin"}, status_code=400)


@app.get("/api/download/{sid}/{name}")
def download_bin(sid: str, name: str):
    base = (BUILD_ROOT / sid / "build").resolve()
    target = (base / name).resolve()
    if BUILD_ROOT.resolve() not in target.parents and target.parent != BUILD_ROOT.resolve():
        return JSONResponse({"error": "invalid path"}, status_code=400)
    if not target.exists():
        return JSONResponse({"error": "file not found"}, status_code=404)
    return FileResponse(target, filename=name, media_type="application/octet-stream")


# ---------- erase / backup ----------
@app.post("/api/erase")
async def erase(body: dict):
    port, baud, mode = body.get("port", ""), body.get("baud", "460800"), body.get("mode", "normal")
    fqbn = body.get("fqbn", "esp32:esp32:esp32")
    port_arg = f"--port {port}" if port else ""
    baud_arg = f"--baud {baud}" if baud else ""
    r1 = await _run_logged(f"esptool {port_arg} {baud_arg} erase-flash", timeout=180)
    logs = r1["logs"]
    if not r1["ok"]:
        return {"ok": False, "logs": logs}
    if mode == "factory":
        # flash clean bootloader via dummy sketch
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td) / "dummy_bootloader"
            proj.mkdir()
            (proj / "dummy_bootloader.ino").write_text("void setup() {}\nvoid loop() {}")
            port_flag = f"-p {port}" if port else ""
            r2 = await _run_logged(
                f'arduino-cli compile --upload -b {fqbn} {port_flag} "{proj / "dummy_bootloader.ino"}"',
                timeout=300)
            logs += "\n" + r2["logs"]
            return {"ok": r2["ok"], "logs": logs}
    return {"ok": True, "logs": logs}


@app.post("/api/backup")
async def backup(body: dict):
    port, baud = body.get("port", ""), body.get("baud", "460800")
    flash = core.detect_flash_size(port)
    size_map = {16: "0x1000000", 8: "0x800000", 4: "0x400000", 2: "0x200000"}
    size_hex = size_map.get(flash["flash_mb"], "0x400000")
    sid = uuid.uuid4().hex[:8]
    work = BUILD_ROOT / sid
    work.mkdir(parents=True, exist_ok=True)
    out = work / f"backup_{sid}.bin"
    port_arg = f"--port {port}" if port else ""
    res = await _run_logged(f"esptool {port_arg} --baud {baud} read-flash 0x0 {size_hex} \"{out}\"", timeout=300)
    if not res["ok"] or not out.exists():
        return {"ok": False, "logs": res["logs"]}
    return {"ok": True, "logs": res["logs"], "download_url": f"/api/download-backup/{sid}"}


@app.get("/api/download-backup/{sid}")
def download_backup(sid: str):
    work = BUILD_ROOT / sid
    bins = list(work.glob("backup_*.bin"))
    if not bins:
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(bins[0], filename=bins[0].name, media_type="application/octet-stream")


# ---------- core manager ----------
@app.get("/api/core/list")
async def core_list():
    res = await _run_logged("arduino-cli core list", timeout=60)
    return {"ok": res["ok"], "logs": res["logs"]}


@app.post("/api/core/upgrade")
async def core_upgrade():
    r1 = await _run_logged("arduino-cli core update-index", timeout=180)
    if not r1["ok"]:
        return {"ok": False, "logs": r1["logs"]}
    r2 = await _run_logged("arduino-cli core upgrade", timeout=600)
    return {"ok": r2["ok"], "logs": r1["logs"] + "\n" + r2["logs"]}


@app.post("/api/core/install")
async def core_install(body: dict):
    version = (body.get("version") or "").strip()
    if not version:
        return {"ok": False, "error": "version required, e.g. 2.0.11"}
    res = await _run_logged(f"arduino-cli core install esp32:esp32@{version}", timeout=600)
    return {"ok": res["ok"], "logs": res["logs"]}


# ---------- pack-web ----------
@app.post("/api/pack-web")
async def pack_web(files: list[UploadFile] = File(...)):
    contents: dict[str, str] = {}
    for up in files:
        raw = await up.read()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("utf-8", errors="replace")
        contents[up.filename or "index.html"] = text
    if not contents:
        return {"ok": False, "error": "no files uploaded"}
    header = core.convert_assets_to_header(contents)
    return {"ok": True, "header": header, "files": sorted(contents.keys())}


@app.post("/api/pack-web/download")
async def pack_web_download(files: list[UploadFile] = File(...)):
    res = await pack_web(files)  # type: ignore[arg-type]
    if not res["ok"]:
        return res
    with tempfile.NamedTemporaryFile("w", suffix=".h", delete=False) as tf:
        tf.write(res["header"])
        tmp = tf.name
    return FileResponse(tmp, filename="web_assets.h", media_type="text/plain")


# ---------- OTA ----------
@app.post("/api/ota/scan")
def ota_scan(body: dict | None = None):
    timeout = float((body or {}).get("timeout", 3.0))
    return {"devices": core.discover_ota_devices(timeout)}


@app.post("/api/ota/flash")
async def ota_flash(file: UploadFile = File(...), ip: str = Form(...), password: str = Form("")):
    espota = Path("espota.py")
    if not espota.exists():
        # also check web/ and cwd
        alt = BASE_DIR.parent / "espota.py"
        if alt.exists():
            espota = alt
        else:
            return {"ok": False, "error": "espota.py not found. Run install.sh first"}
    sid = uuid.uuid4().hex[:8]
    work = BUILD_ROOT / sid
    work.mkdir(parents=True, exist_ok=True)
    fpath = _save_upload(file, work / (file.filename or "firmware.bin"))
    pass_arg = f"-a {password}" if password else ""
    res = await _run_logged(f'python3 "{espota}" -i {ip} -f "{fpath}" {pass_arg}', timeout=300)
    return {"ok": res["ok"], "logs": res["logs"]}


# ---------- serial monitor (WebSocket) ----------
@app.websocket("/ws/monitor")
async def ws_monitor(ws: WebSocket, port: str = "", baud: int = 115200):
    await ws.accept()
    try:
        import serial
    except ImportError:
        await ws.send_text("[ERROR] pyserial not installed on server\n")
        await ws.close()
        return
    try:
        ser = serial.Serial(port, int(baud), timeout=0.1)
    except Exception as e:
        await ws.send_text(f"[ERROR] cannot open {port}: {e}\n")
        await ws.close()
        return

    async def reader():
        try:
            while True:
                n = ser.in_waiting
                if n:
                    data = ser.read(n).decode(errors="replace")
                    await ws.send_text(data)
                await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    task = asyncio.create_task(reader())
    try:
        while True:
            msg = await ws.receive_text()
            try:
                ser.write(msg.encode())
            except Exception:
                pass
    except WebSocketDisconnect:
        pass
    finally:
        task.cancel()
        try:
            ser.close()
        except Exception:
            pass


# serve SPA
@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
