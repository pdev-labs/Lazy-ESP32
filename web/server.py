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


# ---------- background jobs with live logs ----------
# Long tasks (compile/flash/erase can take minutes). The browser POSTs,
# gets a job_id immediately, then polls GET /api/jobs/{id} or streams
# WS /ws/job/{id} for live progress instead of staring at a frozen page.
jobs: dict[str, dict] = {}


def _new_job(label: str) -> str:
    jid = uuid.uuid4().hex[:8]
    jobs[jid] = {"label": label, "status": "queued", "logs": "", "result": {},
                 "code": None, "done": False}
    # keep memory bounded
    if len(jobs) > 50:
        oldest = next(iter(jobs))
        jobs.pop(oldest, None)
    return jid


def _job_log(jid: str, text: str) -> None:
    if jid in jobs:
        jobs[jid]["logs"] = (jobs[jid]["logs"] + text)[-60000:]


async def _run_job_logged(jid: str, cmd: str, timeout: int = 600) -> bool:
    """Stream a shell command into the job log. Returns True on exit 0."""
    _job_log(jid, f"[Running]: {cmd}\n")
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
        assert proc.stdout is not None
        try:
            async with asyncio.timeout(timeout):
                while True:
                    line = await proc.stdout.readline()
                    if not line:
                        break
                    _job_log(jid, line.decode(errors="replace"))
        except TimeoutError:
            proc.kill()
            _job_log(jid, f"\n[TIMEOUT after {timeout}s]\n")
            jobs[jid]["status"] = "error"
            jobs[jid]["done"] = True
            return False
        await proc.wait()
        _job_log(jid, f"\n[exit code: {proc.returncode}]\n")
        jobs[jid]["code"] = proc.returncode
        return proc.returncode == 0
    except Exception as e:
        _job_log(jid, f"[ERROR]: {e}\n")
        return False


def _finish_job(jid: str, ok: bool, result: dict | None = None) -> None:
    if jid in jobs:
        jobs[jid]["status"] = "done" if ok else "error"
        jobs[jid]["done"] = True
        if result:
            jobs[jid]["result"] = result


@app.get("/api/jobs/{jid}")
def job_status(jid: str):
    j = jobs.get(jid)
    if not j:
        return JSONResponse({"error": "unknown job"}, status_code=404)
    out = {"job_id": jid, "label": j["label"], "status": j["status"],
           "done": j["done"], "logs": j["logs"], "code": j["code"]}
    out.update(j["result"])
    return out


@app.websocket("/ws/job/{jid}")
async def ws_job(ws: WebSocket, jid: str):
    await ws.accept()
    sent = 0
    try:
        while True:
            j = jobs.get(jid)
            if not j:
                await ws.send_text("[ERROR] unknown job\n")
                break
            logs = j["logs"]
            if len(logs) > sent:
                await ws.send_text(logs[sent:])
                sent = len(logs)
            if j["done"]:
                break
            await asyncio.sleep(0.4)
    except WebSocketDisconnect:
        pass
    finally:
        try:
            await ws.close()
        except Exception:
            pass


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


# ---------- compile / flash (job-based: instant response + live logs) ----------
async def _auto_install_libs_logged(jid: str, target: Path) -> list[str]:
    try:
        src = target.read_text(errors="ignore")
    except Exception:
        return []
    libs = core.scan_includes(src)
    if libs:
        _job_log(jid, f"[Auto-install] detected libraries: {', '.join(libs)}\n")
        for lib in libs:
            await _run_job_logged(jid, f'arduino-cli lib install "{lib}"', timeout=180)
    else:
        _job_log(jid, "[Auto-install] no third-party libraries detected.\n")
    return libs


async def _compile_worker(jid: str, target: str, eff_fqbn: str, build_path: Path, sid: str):
    jobs[jid]["status"] = "running"
    clean = "--clean" if "PartitionScheme=custom" in eff_fqbn else ""
    if clean:
        _job_log(jid, "[Cache] custom partitions detected → using --clean (poisoning prevention).\n")
    ok = await _run_job_logged(
        jid, f'arduino-cli compile {clean} --build-path "{build_path}" -b {eff_fqbn} "{target}"',
        timeout=600)
    bins = list(build_path.glob("*.bin"))
    result = {"fqbn": eff_fqbn, "session": sid}
    if ok and bins:
        best = max(bins, key=lambda p: p.stat().st_size)
        result["bin_url"] = f"/api/download/{sid}/{best.name}"
        result["bin_name"] = best.name
        _job_log(jid, f"\n[SUCCESS] Binary: {best.name} ({best.stat().st_size // 1024} KB)\n")
    elif ok:
        _job_log(jid, "\n[WARN] Compile finished but no .bin found in build dir.\n")
    _finish_job(jid, ok, result)


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
    if partitions is not None and partitions.filename:
        _save_upload(partitions, work / "upload" / "partitions.csv")
    target = core.prepare_sketch_dir(str(sketch_path), str(work / "sketch"))
    uploaded_csv = work / "upload" / "partitions.csv"
    if uploaded_csv.exists() and not (Path(target).parent / "partitions.csv").exists():
        shutil.copy2(uploaded_csv, Path(target).parent / "partitions.csv")
    eff_fqbn = _fqbn_with_partitions(fqbn, Path(target).parent / "partitions.csv")
    build_path = work / "build"
    build_path.mkdir(exist_ok=True)
    jid = _new_job("compile")
    _job_log(jid, f"[Compile] {sketch.filename} with FQBN {eff_fqbn}\n")

    async def _chain():
        await _auto_install_libs_logged(jid, Path(target))
        await _compile_worker(jid, target, eff_fqbn, build_path, sid)

    asyncio.create_task(_chain())
    return {"job_id": jid, "session": sid, "fqbn": eff_fqbn}


async def _flash_littlefs_logged(jid: str, data_dir: Path, csv_path: Path,
                                 port: str, baud: str, work: Path) -> bool:
    """Pack data_dir with mklittlefs and flash at the spiffs offset from csv. Returns success."""
    try:
        csv_text = csv_path.read_text(errors="ignore")
    except Exception as e:
        _job_log(jid, f"[LittleFS] cannot read partitions.csv: {e}\n[LittleFS] skipping data upload.\n")
        return False
    info = core.parse_spiffs_from_csv(csv_text)
    if not info or not info.get("offset"):
        _job_log(jid, "[LittleFS] no spiffs/littlefs partition in CSV — skipping data upload.\n")
        return False
    files = [p for p in data_dir.rglob("*") if p.is_file()]
    if not files:
        _job_log(jid, "[LittleFS] data folder empty — skipping.\n")
        return False
    _job_log(jid, f"[LittleFS] {len(files)} file(s), partition {info['name']} "
                   f"at {info['offset']} size {info['size_human']}.\n")
    fs_bin = work / "build" / "littlefs.bin"
    fs_bin.parent.mkdir(parents=True, exist_ok=True)
    ok, logs = core.build_littlefs_image(str(data_dir), info["size"], str(fs_bin))
    _job_log(jid, logs + "\n")
    if not ok:
        return False
    port_arg = f"--port {port}" if port else ""
    baud_arg = f"--baud {baud}" if baud else ""
    return await _run_job_logged(
        jid, f'esptool {port_arg} {baud_arg} write-flash {info["offset"]} "{fs_bin}"', timeout=300)


async def _flash_usb_worker(jid: str, kind: str, fpath: Path, port: str, baud: str,
                            fqbn: str, addr: str, work: Path, sid: str,
                            data_dir: Path | None, csv_for_fs: Path | None):
    jobs[jid]["status"] = "running"
    port_arg = f"--port {port}" if port else ""
    baud_arg = f"--baud {baud}" if baud else ""
    if kind == "bin":
        ok = await _run_job_logged(
            jid, f'esptool {port_arg} {baud_arg} write-flash -z {addr} "{fpath}"', timeout=300)
        _finish_job(jid, ok, {"mode": "bin"})
        return
    # .ino path
    target = core.prepare_sketch_dir(str(fpath), str(work / "sketch"))
    uploaded_csv = work / "upload" / "partitions.csv"
    if uploaded_csv.exists() and not (Path(target).parent / "partitions.csv").exists():
        shutil.copy2(uploaded_csv, Path(target).parent / "partitions.csv")
    eff_fqbn = _fqbn_with_partitions(fqbn, Path(target).parent / "partitions.csv")
    await _auto_install_libs_logged(jid, Path(target))
    build_path = work / "build"
    build_path.mkdir(exist_ok=True)
    clean = "--clean" if "PartitionScheme=custom" in eff_fqbn else ""
    port_flag = f"-p {port}" if port else ""
    ok = await _run_job_logged(
        jid, f'arduino-cli compile {clean} --build-path "{build_path}" --upload '
             f'-b {eff_fqbn} {port_flag} "{target}"', timeout=600)
    if not ok:
        _finish_job(jid, False, {"mode": "ino", "fqbn": eff_fqbn})
        return
    _job_log(jid, "[Flash] app flash successful!\n")
    # LittleFS data upload (CLI parity: flash_littlefs_data)
    csv_path = Path(target).parent / "partitions.csv"
    data_src = data_dir if data_dir and any(data_dir.rglob("*")) else None
    if data_src is None:
        # also check sketch-adjacent data/ (prepare_sketch_dir copies it)
        adj = Path(target).parent / "data"
        if adj.is_dir() and any(adj.rglob("*")):
            data_src, csv_path = adj, csv_path
    if data_src is not None and csv_path.exists():
        _job_log(jid, "[LittleFS] data folder detected — packing & flashing...\n")
        fs_ok = await _flash_littlefs_logged(jid, data_src, csv_path, port, baud, work)
        _job_log(jid, "[LittleFS] upload complete!\n" if fs_ok else "[LittleFS] upload failed/skipped.\n")
    elif data_src is not None:
        _job_log(jid, "[LittleFS] data files uploaded but no partitions.csv with spiffs — skipping FS flash.\n")
    _finish_job(jid, True, {"mode": "ino", "fqbn": eff_fqbn})


@app.post("/api/flash-usb")
async def flash_usb(
    file: UploadFile = File(...),
    port: str = Form(""),
    baud: str = Form("460800"),
    fqbn: str = Form("esp32:esp32:esp32"),
    addr: str = Form("0x10000"),
    partitions: UploadFile | None = File(default=None),
    data: list[UploadFile] | None = File(default=None),
):
    sid = uuid.uuid4().hex[:8]
    work = BUILD_ROOT / sid
    work.mkdir(parents=True, exist_ok=True)
    fname = file.filename or "firmware.bin"
    fpath = _save_upload(file, work / "upload" / fname)
    ext = Path(fname).suffix.lower()
    if ext not in (".bin", ".ino"):
        return JSONResponse({"ok": False, "error": f"Unsupported file type {ext}. Use .ino or .bin"}, status_code=400)
    if ext == ".bin" and not core.esptool_available():
        return {"ok": False, "error": "esptool not found on server"}
    if ext == ".ino" and not core.arduino_cli_available():
        return JSONResponse({"ok": False, "error": "arduino-cli not found"}, status_code=400)
    if partitions is not None and partitions.filename:
        _save_upload(partitions, work / "upload" / "partitions.csv")
    data_dir: Path | None = None
    if data:
        data_dir = work / "upload" / "data"
        for up in data:
            if up.filename:
                _save_upload(up, data_dir / Path(up.filename).name)
    jid = _new_job("flash-usb")
    _job_log(jid, f"[Flash] {fname} → port={port or 'auto'} baud={baud}\n")
    csv_for_fs = work / "upload" / "partitions.csv"
    asyncio.create_task(_flash_usb_worker(
        jid, "bin" if ext == ".bin" else "ino", fpath, port, baud, fqbn, addr,
        work, sid, data_dir, csv_for_fs if csv_for_fs.exists() else None))
    return {"job_id": jid, "session": sid}


@app.get("/api/download/{sid}/{name}")
def download_bin(sid: str, name: str):
    base = (BUILD_ROOT / sid / "build").resolve()
    target = (base / name).resolve()
    if BUILD_ROOT.resolve() not in target.parents and target.parent != BUILD_ROOT.resolve():
        return JSONResponse({"error": "invalid path"}, status_code=400)
    if not target.exists():
        return JSONResponse({"error": "file not found"}, status_code=404)
    return FileResponse(target, filename=name, media_type="application/octet-stream")


# ---------- erase / backup (job-based) ----------
async def _erase_worker(jid: str, port: str, baud: str, mode: str, fqbn: str):
    jobs[jid]["status"] = "running"
    port_arg = f"--port {port}" if port else ""
    baud_arg = f"--baud {baud}" if baud else ""
    if mode not in ("normal", "factory"):
        _job_log(jid, f"[ERROR] unknown mode {mode}\n")
        _finish_job(jid, False)
        return
    _job_log(jid, "[WARNING] erasing entire flash...\n")
    ok = await _run_job_logged(jid, f"esptool {port_arg} {baud_arg} erase-flash", timeout=180)
    if not ok:
        _finish_job(jid, False)
        return
    if mode == "factory":
        _job_log(jid, "[Factory] flashing clean bootloader via dummy sketch...\n")
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td) / "dummy_bootloader"
            proj.mkdir()
            (proj / "dummy_bootloader.ino").write_text("void setup() {}\nvoid loop() {}")
            port_flag = f"-p {port}" if port else ""
            ok2 = await _run_job_logged(
                jid, f'arduino-cli compile --upload -b {fqbn} {port_flag} "{proj / "dummy_bootloader.ino"}"',
                timeout=300)
            _finish_job(jid, ok2)
            return
    _job_log(jid, "[SUCCESS] erase complete. Flash is empty.\n")
    _finish_job(jid, True)


@app.post("/api/erase")
async def erase(body: dict):
    port = body.get("port", "")
    baud = body.get("baud", "460800")
    mode = body.get("mode", "normal")
    fqbn = body.get("fqbn", "esp32:esp32:esp32")
    jid = _new_job(f"erase-{mode}")
    asyncio.create_task(_erase_worker(jid, port, baud, mode, fqbn))
    return {"job_id": jid}


async def _backup_worker(jid: str, port: str, baud: str, sid: str, out: Path, size_hex: str):
    jobs[jid]["status"] = "running"
    port_arg = f"--port {port}" if port else ""
    ok = await _run_job_logged(
        jid, f'esptool {port_arg} --baud {baud} read-flash 0x0 {size_hex} "{out}"', timeout=300)
    result = {"download_url": f"/api/download-backup/{sid}"} if (ok and out.exists()) else {}
    if ok and out.exists():
        _job_log(jid, f"\n[SUCCESS] Backup: {out.name} ({out.stat().st_size // 1024} KB)\n")
    _finish_job(jid, ok and out.exists(), result)


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
    jid = _new_job("backup")
    _job_log(jid, f"[Backup] detected flash {flash['flash_label']}, reading {size_hex}...\n")
    asyncio.create_task(_backup_worker(jid, port, baud, sid, out, size_hex))
    return {"job_id": jid, "flash": flash["flash_label"]}


@app.get("/api/download-backup/{sid}")
def download_backup(sid: str):
    work = BUILD_ROOT / sid
    bins = list(work.glob("backup_*.bin"))
    if not bins:
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(bins[0], filename=bins[0].name, media_type="application/octet-stream")


# ---------- core manager (job-based for upgrade/install) ----------
@app.get("/api/core/list")
async def core_list():
    res = await _run_logged("arduino-cli core list", timeout=60)
    return {"ok": res["ok"], "logs": res["logs"]}


@app.get("/api/core/search")
async def core_search(q: str = "esp32"):
    res = await _run_logged(f"arduino-cli core search {q}", timeout=60)
    return {"ok": res["ok"], "logs": res["logs"]}


async def _core_upgrade_worker(jid: str):
    jobs[jid]["status"] = "running"
    ok = await _run_job_logged(jid, "arduino-cli core update-index", timeout=180)
    if not ok:
        _finish_job(jid, False)
        return
    ok2 = await _run_job_logged(jid, "arduino-cli core upgrade", timeout=900)
    _finish_job(jid, ok2)


@app.post("/api/core/upgrade")
async def core_upgrade():
    jid = _new_job("core-upgrade")
    asyncio.create_task(_core_upgrade_worker(jid))
    return {"job_id": jid}


async def _core_install_worker(jid: str, version: str):
    jobs[jid]["status"] = "running"
    ok = await _run_job_logged(jid, f"arduino-cli core install esp32:esp32@{version}", timeout=900)
    _finish_job(jid, ok)


@app.post("/api/core/install")
async def core_install(body: dict):
    version = (body.get("version") or "").strip()
    if not version:
        return {"ok": False, "error": "version required, e.g. 2.0.11"}
    jid = _new_job("core-install")
    asyncio.create_task(_core_install_worker(jid, version))
    return {"job_id": jid}


# ----------NEW: library scan, partition suggestion, LittleFS ----------
@app.post("/api/libs/scan")
async def libs_scan(sketch: UploadFile = File(...)):
    raw = await sketch.read()
    try:
        src = raw.decode("utf-8")
    except UnicodeDecodeError:
        src = raw.decode("utf-8", errors="replace")
    libs = core.scan_includes(src)
    return {"file": sketch.filename, "libs": libs,
            "note": "Already-bundled ESP32 core headers are excluded." if libs else "No third-party libraries detected."}


@app.post("/api/partitions/suggest")
async def partitions_suggest(files: list[UploadFile] = File(...)):
    total = 0
    names = []
    for up in files:
        raw = await up.read()
        total += len(raw)
        names.append(up.filename or "file")
    return {"files": names, "total_bytes": total, "total_kb": round(total / 1024, 1),
            "suggested_mb": core.suggest_fs_mb(total)}


@app.post("/api/littlefs/build")
async def littlefs_build(
    files: list[UploadFile] = File(...),
    partitions_csv: str = Form(""),
    fs_kb: int = Form(0),
):
    """Pack uploaded data files into littlefs.bin. Needs either partitions.csv text or fs_kb."""
    sid = uuid.uuid4().hex[:8]
    work = BUILD_ROOT / sid
    data_dir = work / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    total = 0
    for up in files:
        if not up.filename:
            continue
        dest = data_dir / Path(up.filename).name
        raw = await up.read()
        dest.write_bytes(raw)
        total += len(raw)
    size_bytes = 0
    offset = ""
    if partitions_csv.strip():
        info = core.parse_spiffs_from_csv(partitions_csv)
        if info:
            size_bytes, offset = info["size"], info["offset"]
    if not size_bytes and fs_kb:
        size_bytes = int(fs_kb) * 1024
    if not size_bytes:
        return JSONResponse({"ok": False, "error": "provide partitions.csv text or fs_kb"}, status_code=400)
    if total > size_bytes:
        return {"ok": False, "error": f"data ({total // 1024}K) exceeds FS partition ({size_bytes // 1024}K)"}
    out = work / "littlefs.bin"
    ok, logs = core.build_littlefs_image(str(data_dir), size_bytes, str(out))
    if not ok:
        return {"ok": False, "logs": logs}
    return {"ok": True, "logs": logs, "offset": offset or "see partitions.csv",
            "size": size_bytes, "total_bytes": total,
            "download_url": f"/api/download-littlefs/{sid}"}


@app.get("/api/download-littlefs/{sid}")
def download_littlefs(sid: str):
    f = BUILD_ROOT / sid / "littlefs.bin"
    if not f.exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(f, filename="littlefs.bin", media_type="application/octet-stream")


async def _littlefs_flash_worker(jid: str, data_dir: Path, csv_text: str,
                                 port: str, baud: str, work: Path):
    jobs[jid]["status"] = "running"
    info = core.parse_spiffs_from_csv(csv_text)
    if not info or not info.get("offset"):
        _job_log(jid, "[LittleFS] no spiffs/littlefs partition found in CSV.\n")
        _finish_job(jid, False)
        return
    fs_bin = work / "littlefs.bin"
    ok, logs = core.build_littlefs_image(str(data_dir), info["size"], str(fs_bin))
    _job_log(jid, logs + "\n")
    if not ok:
        _finish_job(jid, False)
        return
    port_arg = f"--port {port}" if port else ""
    baud_arg = f"--baud {baud}" if baud else ""
    ok2 = await _run_job_logged(
        jid, f'esptool {port_arg} {baud_arg} write-flash {info["offset"]} "{fs_bin}"', timeout=300)
    if ok2:
        _job_log(jid, "[SUCCESS] LittleFS data upload complete!\n")
    _finish_job(jid, ok2)


@app.post("/api/littlefs/flash")
async def littlefs_flash(
    files: list[UploadFile] = File(...),
    partitions_csv: str = Form(...),
    port: str = Form(""),
    baud: str = Form("460800"),
):
    sid = uuid.uuid4().hex[:8]
    work = BUILD_ROOT / sid
    data_dir = work / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for up in files:
        if up.filename:
            (data_dir / Path(up.filename).name).write_bytes(await up.read())
            n += 1
    if not n:
        return {"ok": False, "error": "no data files uploaded"}
    jid = _new_job("littlefs-flash")
    _job_log(jid, f"[LittleFS] {n} file(s), flashing to {port or 'auto'}...\n")
    asyncio.create_task(_littlefs_flash_worker(jid, data_dir, partitions_csv, port, baud, work))
    return {"job_id": jid}


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


# ---------- OTA (job-based) ----------
def _find_espota() -> Path | None:
    for cand in (Path("espota.py"), BASE_DIR.parent / "espota.py", BASE_DIR / "espota.py"):
        if cand.exists():
            return cand
    found = shutil.which("espota.py")
    return Path(found) if found else None


async def _ota_bin_worker(jid: str, fpath: Path, ip: str, password: str):
    jobs[jid]["status"] = "running"
    espota = _find_espota()
    if not espota:
        _job_log(jid, "[ERROR] espota.py not found. Run install.sh first.\n")
        _finish_job(jid, False)
        return
    pass_arg = f"-a {password}" if password else ""
    ok = await _run_job_logged(jid, f'python3 "{espota}" -i {ip} -f "{fpath}" {pass_arg}', timeout=300)
    if ok:
        _job_log(jid, "[SUCCESS] OTA flash complete!\n")
    _finish_job(jid, ok)


@app.post("/api/ota/scan")
def ota_scan(body: dict | None = None):
    timeout = float((body or {}).get("timeout", 3.0))
    return {"devices": core.discover_ota_devices(timeout)}


@app.post("/api/ota/flash")
async def ota_flash(file: UploadFile = File(...), ip: str = Form(...), password: str = Form("")):
    sid = uuid.uuid4().hex[:8]
    work = BUILD_ROOT / sid
    work.mkdir(parents=True, exist_ok=True)
    fpath = _save_upload(file, work / (file.filename or "firmware.bin"))
    jid = _new_job("ota-flash")
    _job_log(jid, f"[OTA] pushing {file.filename} to {ip}...\n")
    asyncio.create_task(_ota_bin_worker(jid, fpath, ip, password))
    return {"job_id": jid}


async def _ota_ino_worker(jid: str, target: str, eff_fqbn: str, build_path: Path,
                          ip: str, password: str):
    """Compile .ino then push the resulting .bin over OTA (CLI parity for wireless sketches)."""
    jobs[jid]["status"] = "running"
    await _auto_install_libs_logged(jid, Path(target))
    clean = "--clean" if "PartitionScheme=custom" in eff_fqbn else ""
    ok = await _run_job_logged(
        jid, f'arduino-cli compile {clean} --build-path "{build_path}" -b {eff_fqbn} "{target}"',
        timeout=600)
    if not ok:
        _finish_job(jid, False)
        return
    bins = list(build_path.glob("*.bin"))
    if not bins:
        _job_log(jid, "[ERROR] compile ok but no .bin produced.\n")
        _finish_job(jid, False)
        return
    best = max(bins, key=lambda p: p.stat().st_size)
    _job_log(jid, f"[OTA] compiled {best.name}, pushing to {ip}...\n")
    espota = _find_espota()
    if not espota:
        _job_log(jid, "[ERROR] espota.py not found. Run install.sh first.\n")
        _finish_job(jid, False)
        return
    pass_arg = f"-a {password}" if password else ""
    ok2 = await _run_job_logged(jid, f'python3 "{espota}" -i {ip} -f "{best}" {pass_arg}', timeout=300)
    if ok2:
        _job_log(jid, "[SUCCESS] OTA flash complete!\n")
    _finish_job(jid, ok2)


@app.post("/api/ota/compile-flash")
async def ota_compile_flash(
    sketch: UploadFile = File(...),
    fqbn: str = Form("esp32:esp32:esp32"),
    ip: str = Form(...),
    password: str = Form(""),
    partitions: UploadFile | None = File(default=None),
):
    if not core.arduino_cli_available():
        return JSONResponse({"ok": False, "error": "arduino-cli not found"}, status_code=400)
    sid = uuid.uuid4().hex[:8]
    work = BUILD_ROOT / sid
    work.mkdir(parents=True, exist_ok=True)
    sketch_path = _save_upload(sketch, work / "upload" / (sketch.filename or "sketch.ino"))
    if partitions is not None and partitions.filename:
        _save_upload(partitions, work / "upload" / "partitions.csv")
    target = core.prepare_sketch_dir(str(sketch_path), str(work / "sketch"))
    uploaded_csv = work / "upload" / "partitions.csv"
    if uploaded_csv.exists() and not (Path(target).parent / "partitions.csv").exists():
        shutil.copy2(uploaded_csv, Path(target).parent / "partitions.csv")
    eff_fqbn = _fqbn_with_partitions(fqbn, Path(target).parent / "partitions.csv")
    build_path = work / "build"
    build_path.mkdir(exist_ok=True)
    jid = _new_job("ota-compile")
    _job_log(jid, f"[OTA] compile {sketch.filename} ({eff_fqbn}) → push to {ip}...\n")
    asyncio.create_task(_ota_ino_worker(jid, target, eff_fqbn, build_path, ip, password))
    return {"job_id": jid}


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
