from __future__ import annotations

import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

# --------------------------------------------------------------------------- log

_COLOR = os.environ.get("NO_COLOR") is None and sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


def info(msg: str) -> None:
    print(f"{_c('36', '::')} {msg}")


def ok(msg: str) -> None:
    print(f"{_c('32', 'OK')} {msg}")


def warn(msg: str) -> None:
    print(f"{_c('33', '!!')} {msg}")


def err(msg: str) -> None:
    print(f"{_c('31', 'XX')} {msg}", file=sys.stderr)


def die(msg: str, code: int = 1):
    err(msg)
    raise SystemExit(code)


class Fail(Exception):
    """User facing error."""


# --------------------------------------------------------------------------- misc

def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} GB"


def slug(text: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(text)).strip("_")
    return text or "unnamed"


def version_tuple(v: str) -> tuple:
    """'v2.192.1' / '2.192.1' / '1.41.6.8' -> tuple of ints (padded)."""
    nums = [int(x) for x in re.findall(r"\d+", str(v))]
    nums += [0] * (4 - len(nums))
    return tuple(nums[:4])


def parse_timecode(value: str, tickrate: float) -> int:
    """'12:30' | '750s' | '48000' (ticks) -> tick number."""
    value = str(value).strip()
    if ":" in value:
        parts = [float(p) for p in value.split(":")]
        seconds = 0.0
        for p in parts:
            seconds = seconds * 60 + p
        return int(round(seconds * tickrate))
    if value.endswith("s"):
        return int(round(float(value[:-1]) * tickrate))
    return int(float(value))


def run(cmd: list, cwd: str | None = None, check: bool = True, quiet: bool = False) -> int:
    if not quiet:
        info("run: " + " ".join(f'"{c}"' if " " in str(c) else str(c) for c in cmd))
    proc = subprocess.run([str(c) for c in cmd], cwd=cwd)
    if check and proc.returncode != 0:
        raise Fail(f"command failed (exit {proc.returncode}): {cmd[0]}")
    return proc.returncode


def run_capture(cmd: list, cwd: str | None = None, quiet: bool = True) -> str:
    """Run a tool and return its stdout (used for listing VPK contents)."""
    if not quiet:
        info("run: " + " ".join(str(c) for c in cmd))
    proc = subprocess.run([str(c) for c in cmd], cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0 and not proc.stdout:
        raise Fail(f"command failed (exit {proc.returncode}): {proc.stderr.strip()[:400]}")
    return proc.stdout


def popen(cmd: list, cwd: str | None = None) -> subprocess.Popen:
    info("spawn: " + " ".join(f'"{c}"' if " " in str(c) else str(c) for c in cmd))
    return subprocess.Popen([str(c) for c in cmd], cwd=cwd)


# --------------------------------------------------------------------------- net

UA = {"User-Agent": "cs2toUE/1.0 (+https://github.com/advancedfx/advancedfx)"}


def http_json(url: str):
    import json

    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def download(url: str, dest: Path, expected_size: int | None = None) -> Path:
    """Streaming download with progress. Never uses the system TEMP dir."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers=UA)
    started = time.time()
    with urllib.request.urlopen(req, timeout=120) as r, open(part, "wb") as f:
        total = int(r.headers.get("Content-Length") or (expected_size or 0))
        done = 0
        while True:
            chunk = r.read(256 * 1024)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if total:
                pct = 100.0 * done / total
                sys.stdout.write(f"\r    {human(done)} / {human(total)}  {pct:5.1f}%")
            else:
                sys.stdout.write(f"\r    {human(done)}")
            sys.stdout.flush()
    sys.stdout.write("\n")
    if dest.exists():
        dest.unlink()
    part.rename(dest)
    ok(f"downloaded {dest.name} in {time.time() - started:.1f}s")
    return dest
