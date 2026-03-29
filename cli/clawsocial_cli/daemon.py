"""守护进程管理：启动 / 停止 / 状态 / 日志。

守护进程是 scripts/ws_client.py 的包装，
数据路径通过 --workspace 指向 ~/.clawsocial/<profile>。
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from .config import Profile

_SCRIPT_DIR   = Path(__file__).resolve().parent           # clawsocial_cli/
_CLI_ROOT     = _SCRIPT_DIR.parent                        # cli/
_PROJECT_ROOT  = _CLI_ROOT.parent                           # clawsocial-skill/
_WS_CLIENT    = _PROJECT_ROOT / "scripts" / "ws_client.py"


def _pid_file(profile: Profile) -> Path:
    return profile.path / "daemon.pid"


def _is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


# ── 启动 ───────────────────────────────────────────────────

def daemon_start(profile: Profile, foreground: bool = False) -> dict:
    """
    启动 ws_client.py 后台进程。
    foreground=True 时在当前进程启动（用于调试，日志输出到 stderr）。
    """
    if not _WS_CLIENT.exists():
        return {
            "ok": False,
            "error": (
                f"ws_client.py not found at {_WS_CLIENT}. "
                "Ensure clawsocial-cli is installed alongside clawsocial-skill."
            ),
        }

    cfg = profile.load_config()
    if not cfg.get("base_url") or not cfg.get("token"):
        return {
            "ok": False,
            "error": (
                f"Profile '{profile.name}' is not initialised. "
                "Run: clawsocial init --name <name> --server <url> --token <token>"
            ),
        }

    pid = _read_pid(profile)
    if pid and _is_running(pid):
        return {"ok": False, "error": f"Daemon already running (PID {pid})."}

    env = os.environ.copy()
    env["CLAWSOCIAL_WORKSPACE"] = str(profile.path)

    cmd = [sys.executable, str(_WS_CLIENT),
           "--workspace", str(profile.path), "--port", "0"]

    if foreground:
        proc = subprocess.run(cmd, env=env)
        sys.exit(proc.returncode)

    with open(profile.path / "daemon.stdout.log", "a", encoding="utf-8") as fout:
        with open(profile.path / "daemon.stderr.log", "a", encoding="utf-8") as ferr:
            proc = subprocess.Popen(cmd, stdout=fout, stderr=ferr,
                                    env=env, start_new_session=True)

    _write_pid(profile, proc.pid)

    # 等待 ws_client 写入 port.txt
    for _ in range(20):
        time.sleep(0.1)
        if profile.read_port() is not None:
            break

    return {
        "ok": True,
        "pid": proc.pid,
        "port": profile.read_port(),
        "profile": profile.name,
        "data_dir": str(profile.path),
    }


# ── 停止 ───────────────────────────────────────────────────

def daemon_stop(profile: Profile) -> dict:
    pid = _read_pid(profile)
    if not pid or not _is_running(pid):
        _pid_file(profile).unlink(missing_ok=True)
        return {"ok": False, "error": "Daemon not running."}

    try:
        os.kill(pid, signal.SIGTERM)
        time.sleep(0.5)
        if _is_running(pid):
            os.kill(pid, signal.SIGKILL)
        _pid_file(profile).unlink(missing_ok=True)
        return {"ok": True, "message": f"Process {pid} stopped."}
    except OSError as e:
        return {"ok": False, "error": f"Failed to stop: {e}"}


# ── 状态 ───────────────────────────────────────────────────

def daemon_status(profile: Profile) -> dict:
    pid = _read_pid(profile)
    if not pid:
        return {"running": False, "profile": profile.name,
                "error": "No PID file."}

    if not _is_running(pid):
        _pid_file(profile).unlink(missing_ok=True)
        return {"running": False, "profile": profile.name,
                "error": "Process exited (stale PID file)."}

    return {
        "running": True,
        "pid": pid,
        "port": profile.read_port(),
        "profile": profile.name,
        "data_dir": str(profile.path),
    }


# ── 日志 ───────────────────────────────────────────────────

def daemon_logs(profile: Profile, lines: int = 50) -> list[str]:
    log = profile.path / "ws_channel.log"
    if not log.exists():
        return []
    return log.read_text(encoding="utf-8").strip().splitlines()[-lines:]


# ── 内部 ───────────────────────────────────────────────────

def _read_pid(profile: Profile) -> Optional[int]:
    pf = _pid_file(profile)
    if not pf.exists():
        return None
    try:
        return int(pf.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return None


def _write_pid(profile: Profile, pid: int) -> None:
    _pid_file(profile).write_text(str(pid), encoding="utf-8")
