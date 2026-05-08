#!/usr/bin/env python3
"""
Heroku XMR Ultra Low Stable Supervisor v8.

هدف هذه النسخة:
- حل أخطاء unrecognized option الناتجة عن تمرير خيارات XMRig بشكل خاطئ.
- تقليل الاستهلاك جدًا عبر micro-throttle باستخدام SIGSTOP/SIGCONT.
- منع تعدد العمال: worker.2 / worker.3 يبقون في وضع انتظار ولا يشغلون XMRig.
- إبقاء Python worker حيًا حتى لو Heroku قتل XMRig بالكود -9/137.

ملاحظة مهمة: لا يمكن لأي كود ضمان أن Heroku لن يقتل عملية ممنوعة أو عملية يتجاوز استهلاكها حدود dyno.
هذه النسخة تقلل الاستهلاك قدر الإمكان وتعيد المحاولة بأمان.
"""
from __future__ import annotations

import os
import re
import shlex
import signal
import subprocess
import sys
import tarfile
import threading
import time
import urllib.request
from pathlib import Path
from typing import Dict, Optional

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.env"
WALLET_FILE = BASE_DIR / "wallet.txt"
TMP_DIR = Path(os.environ.get("TMPDIR", "/tmp")) / "xmrig_v8_low"
XMRIG_VERSION = "6.26.0"
XMRIG_URL = f"https://github.com/xmrig/xmrig/releases/download/v{XMRIG_VERSION}/xmrig-{XMRIG_VERSION}-linux-static-x64.tar.gz"
XMRIG_BIN = TMP_DIR / "xmrig" / "xmrig"

PLACEHOLDERS = {
    "PUT_YOUR_XMR_RECEIVE_ADDRESS_HERE",
    "YOUR_XMR_WALLET",
    "ضع_عنوان_XMR_هنا",
    "عنوان_XMR_الخاص_بك",
    "",
}

stop_requested = False
current_process: Optional[subprocess.Popen] = None


def log(msg: str) -> None:
    print(msg, flush=True)


def parse_env_file(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        try:
            parts = shlex.split(value)
            value = parts[0] if parts else ""
        except Exception:
            value = value.strip('"').strip("'")
        if key:
            values[key] = value
    return values


def merged_config() -> Dict[str, str]:
    # Heroku Config Vars are loaded first, config.env overrides them to avoid old dashboard vars like XMR_THREADS=8.
    cfg = dict(os.environ)
    cfg.update(parse_env_file(CONFIG_FILE))

    wallet = cfg.get("XMR_WALLET", "").strip()
    if wallet in PLACEHOLDERS and WALLET_FILE.exists():
        lines = WALLET_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()
        for line in lines:
            line = line.strip()
            if line and not line.startswith("#") and line not in PLACEHOLDERS:
                cfg["XMR_WALLET"] = line
                break
    return cfg


def as_float(value: str, default: float, min_v: float, max_v: float) -> float:
    try:
        num = float(str(value).strip())
    except Exception:
        return default
    return max(min_v, min(max_v, num))


def as_int(value: str, default: int, min_v: int, max_v: int) -> int:
    try:
        num = int(str(value).strip())
    except Exception:
        return default
    return max(min_v, min(max_v, num))


def as_bool(value: str, default: bool = True) -> bool:
    if value is None:
        return default
    v = str(value).strip().lower()
    if v in {"1", "true", "yes", "on", "y"}:
        return True
    if v in {"0", "false", "no", "off", "n"}:
        return False
    return default


def validate_wallet(wallet: str) -> bool:
    if not wallet or wallet in PLACEHOLDERS:
        return False
    # Monero address/base58, permissive to support subaddresses/integrated addresses.
    return bool(re.fullmatch(r"[123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz]{80,120}", wallet))


def current_dyno_number() -> Optional[int]:
    dyno = os.environ.get("DYNO", "")
    m = re.fullmatch(r"worker\.(\d+)", dyno)
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


def idle_duplicate_worker() -> None:
    dyno = os.environ.get("DYNO", "unknown")
    log(f"🟡 {dyno}: هذا Worker زائد. لن يشغل التعدين لتجنب استهلاك الموارد.")
    log("💡 من Heroku Resources اجعل worker = 1 فقط.")
    while True:
        time.sleep(3600)


def safe_extract_tar(tar_path: Path, dest: Path) -> None:
    dest.resolve().mkdir(parents=True, exist_ok=True)
    with tarfile.open(tar_path, "r:gz") as tar:
        for member in tar.getmembers():
            target = (dest / member.name).resolve()
            if not str(target).startswith(str(dest.resolve())):
                raise RuntimeError(f"Unsafe tar path: {member.name}")
        tar.extractall(dest)


def ensure_xmrig() -> Path:
    if XMRIG_BIN.exists():
        return XMRIG_BIN
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    archive = TMP_DIR / "xmrig.tar.gz"
    extract_dir = TMP_DIR / "extract"
    if extract_dir.exists():
        subprocess.run(["rm", "-rf", str(extract_dir)], check=False)
    extract_dir.mkdir(parents=True, exist_ok=True)

    log(f"⬇️ Downloading XMRig {XMRIG_VERSION}...")
    urllib.request.urlretrieve(XMRIG_URL, archive)
    safe_extract_tar(archive, extract_dir)

    candidates = list(extract_dir.rglob("xmrig"))
    if not candidates:
        raise RuntimeError("XMRig binary not found after extraction")
    target_dir = TMP_DIR / "xmrig"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "xmrig"
    target.write_bytes(candidates[0].read_bytes())
    target.chmod(0o755)
    log(f"✅ XMRig ready: {target}")
    return target


def stream_output(proc: subprocess.Popen) -> None:
    assert proc.stdout is not None
    for line in iter(proc.stdout.readline, ""):
        if not line:
            break
        print(line.rstrip("\n"), flush=True)


def build_command(cfg: Dict[str, str]) -> list[str]:
    wallet = cfg["XMR_WALLET"].strip()
    pool = cfg.get("XMR_POOL", "pool.supportxmr.com:3333").strip() or "pool.supportxmr.com:3333"
    worker = cfg.get("WORKER_NAME", "afdaa1").strip() or "afdaa1"
    donate = as_int(cfg.get("DONATE_LEVEL", "1"), 1, 1, 5)

    # هذه النسخة لا تسمح بأكثر من thread واحد حتى لو كانت Config Vars قديمة.
    threads = 1
    mode = "light"

    xmrig = ensure_xmrig()
    # مهم: استخدم صيغة option=value حتى لا يظهر خطأ unrecognized option: 1.
    return [
        str(xmrig),
        "-o", pool,
        "-u", wallet,
        "-p", worker,
        "--coin=monero",
        f"--donate-level={donate}",
        f"--threads={threads}",
        f"--randomx-mode={mode}",
        "--no-color",
        "--print-time=60",
    ]


def start_xmrig(cfg: Dict[str, str]) -> subprocess.Popen:
    wallet = cfg["XMR_WALLET"].strip()
    pool = cfg.get("XMR_POOL", "pool.supportxmr.com:3333").strip() or "pool.supportxmr.com:3333"
    worker = cfg.get("WORKER_NAME", "afdaa1").strip() or "afdaa1"
    cmd = build_command(cfg)

    log("✅ تم العثور على عنوان XMR من الملفات/الإعدادات.")
    log(f"🌐 Pool: {pool}")
    log(f"🧩 Worker: {worker}")
    log("🛡️ الوضع شديد الانخفاض: threads=1, RandomX=light, micro-throttle مفعّل")
    log("🚀 بدء تشغيل التعدين...")

    def preexec() -> None:
        try:
            os.setsid()
        except Exception:
            pass
        try:
            os.nice(19)
        except Exception:
            pass

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True,
        bufsize=1,
        preexec_fn=preexec,
    )
    threading.Thread(target=stream_output, args=(proc,), daemon=True).start()
    return proc


def stop_child(proc: Optional[subprocess.Popen]) -> None:
    if not proc or proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except Exception:
        try:
            proc.terminate()
        except Exception:
            pass
    try:
        proc.wait(timeout=8)
    except Exception:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            pass


def handle_signal(signum, frame) -> None:  # type: ignore[no-untyped-def]
    global stop_requested, current_process
    stop_requested = True
    log(f"🛑 استلام إشارة إيقاف من Heroku: {signum}")
    stop_child(current_process)
    sys.exit(0)


def throttle_process(proc: subprocess.Popen, work_s: float, sleep_s: float) -> None:
    """Stop/resume XMRig frequently to reduce average CPU and avoid Heroku SIGKILL."""
    if proc.poll() is not None:
        return
    try:
        # ابدأ التهدئة سريعًا جدًا قبل أن يرتفع الاستهلاك لعدة ثوانٍ.
        time.sleep(work_s)
        if proc.poll() is not None:
            return
        os.killpg(os.getpgid(proc.pid), signal.SIGSTOP)
        log(f"⏸️ تهدئة الموارد {sleep_s:.2f}s...")
        time.sleep(sleep_s)
        if proc.poll() is None:
            os.killpg(os.getpgid(proc.pid), signal.SIGCONT)
            log(f"▶️ استئناف قصير {work_s:.2f}s...")
    except ProcessLookupError:
        return
    except Exception as exc:
        log(f"⚠️ تعذر تنفيذ التهدئة: {exc}")
        time.sleep(max(2.0, sleep_s))


def main() -> None:
    global current_process
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    cfg = merged_config()

    # حماية من تشغيل أكثر من worker.1
    if as_bool(cfg.get("ONLY_WORKER_1", "true"), True):
        dyno_num = current_dyno_number()
        if dyno_num is not None and dyno_num != 1:
            idle_duplicate_worker()

    wallet = cfg.get("XMR_WALLET", "").strip()
    if not validate_wallet(wallet):
        log("❌ عنوان XMR غير مضبوط أو غير صحيح.")
        log('افتح config.env وضع عنوانك في: XMR_WALLET="..."')
        log("أو ضع العنوان فقط داخل wallet.txt")
        while True:
            time.sleep(3600)
    cfg["XMR_WALLET"] = wallet

    throttle_enabled = as_bool(cfg.get("THROTTLE_ENABLED", "true"), True)
    work_s = as_float(cfg.get("THROTTLE_WORK_SECONDS", "0.20"), 0.20, 0.05, 2.0)
    sleep_s = as_float(cfg.get("THROTTLE_SLEEP_SECONDS", "2.00"), 2.00, 0.5, 60.0)
    backoff_work_s = as_float(cfg.get("BACKOFF_WORK_SECONDS", "0.10"), 0.10, 0.05, 1.0)
    backoff_sleep_s = as_float(cfg.get("BACKOFF_SLEEP_SECONDS", "5.00"), 5.00, 1.0, 120.0)
    max_kills = as_int(cfg.get("MAX_KILLS_BEFORE_LONG_SLEEP", "3"), 3, 1, 20)
    long_sleep = as_int(cfg.get("LONG_SLEEP_SECONDS", "600"), 600, 60, 3600)

    kill_count = 0
    restart_count = 0

    while not stop_requested:
        try:
            current_process = start_xmrig(cfg)
        except Exception as exc:
            log(f"❌ فشل تجهيز/تشغيل XMRig: {exc}")
            log("🔁 إعادة المحاولة بعد 60 ثانية...")
            time.sleep(60)
            continue

        while current_process.poll() is None and not stop_requested:
            if throttle_enabled:
                throttle_process(current_process, work_s, sleep_s)
            else:
                time.sleep(5)

        if stop_requested:
            break

        code = current_process.poll() if current_process else None
        log(f"⚠️ XMRig خرج بالكود: {code}")
        restart_count += 1

        if code in {-9, 137}:
            kill_count += 1
            work_s = min(work_s, backoff_work_s)
            sleep_s = max(sleep_s, backoff_sleep_s)
            log(f"🧯 تخفيف تلقائي: عمل {work_s:.2f}s / توقف {sleep_s:.2f}s")
            if kill_count >= max_kills:
                log(f"🛌 تكرر القتل {kill_count} مرات. انتظار طويل {long_sleep}s لتجنب crash loop...")
                time.sleep(long_sleep)
                kill_count = 0
                continue
        else:
            kill_count = 0

        delay = min(300, 20 + restart_count * 10)
        log(f"🔁 إعادة المحاولة بعد {delay} ثانية...")
        time.sleep(delay)


if __name__ == "__main__":
    main()
