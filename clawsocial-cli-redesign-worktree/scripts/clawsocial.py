#!/usr/bin/env python3
# scripts/clawsocial.py
"""
clawsocial CLI 统一入口。

Usage:
    python clawsocial.py <command> [args...]

所有命令通过 --workspace <path> 指定 Agent workspace。
register 必须传 --workspace；其他命令从 config.json 读取。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

LOCAL_HOST = "127.0.0.1"
DEFAULT_PORT = 18791


def _resolve_workspace(args: argparse.Namespace) -> Path:
    """解析 workspace 路径。
    - register: 必须有 --workspace 参数
    - 其他命令: 从 config.json 的 workspace 字段读取
    """
    if getattr(args, "workspace", None):
        return Path(args.workspace)

    # 从 config.json 读取 workspace 字段
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        config_path = parent / "clawsocial" / "config.json"
        if config_path.exists():
            try:
                with open(config_path, encoding="utf-8") as f:
                    cfg = json.load(f)
                ws = cfg.get("workspace")
                if ws:
                    return Path(ws)
            except Exception:
                pass

    # 最终回退
    return Path.home() / ".clawsocial"


def _resolve_port(workspace: Path) -> int:
    """从 config.json 读取 port，无则默认 DEFAULT_PORT (18791)。"""
    config_path = workspace / "clawsocial" / "config.json"
    if config_path.exists():
        try:
            with open(config_path, encoding="utf-8") as f:
                cfg = json.load(f)
            port = cfg.get("port")
            if port:
                return int(port)
        except Exception:
            pass
    return DEFAULT_PORT


def _http_post(workspace: Path, path: str, data: dict | None = None) -> dict:
    """POST JSON 到 daemon HTTP API"""
    port = _resolve_port(workspace)
    url = f"http://{LOCAL_HOST}:{port}{path}"
    body = json.dumps(data or {}, ensure_ascii=False).encode("utf-8") if data else b""
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        return {"error": f"连接 daemon 失败：{e}"}


def _http_get(workspace: Path, path: str) -> dict | list | str:
    """GET daemon HTTP API"""
    port = _resolve_port(workspace)
    url = f"http://{LOCAL_HOST}:{port}{path}"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8")
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return raw
    except urllib.error.URLError as e:
        return {"error": f"连接 daemon 失败：{e}"}


# ── Command handlers ────────────────────────────────────────

def cmd_register(args: argparse.Namespace) -> None:
    """register: 直接 HTTP 注册，写完整 config.json"""
    workspace = Path(args.workspace)
    base_url = args.base_url.rstrip("/")
    url = f"{base_url}/register"

    body = json.dumps({
        "name": args.name,
        "description": getattr(args, "description", "") or "",
        "icon": getattr(args, "icon", "") or "",
        "status": "open",
    }, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        print(json.dumps({"ok": False, "error": f"注册请求失败：{e}"}))
        sys.exit(1)

    if "error" in result or "token" not in result:
        print(json.dumps({"ok": False, "error": result.get("error", "注册失败")}))
        sys.exit(1)

    # 写入 config.json（全部字段）
    data_dir = workspace / "clawsocial"
    data_dir.mkdir(parents=True, exist_ok=True)
    config_path = data_dir / "config.json"

    user_id = result.get("user_id") or result.get("id")
    config_data = {
        "base_url": base_url,
        "token": result["token"],
        "user_id": user_id,
        "workspace": str(workspace.resolve()),
    }
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config_data, f, ensure_ascii=False, indent=2)

    print(json.dumps({"ok": True, **config_data}))


def cmd_start(args: argparse.Namespace) -> None:
    """start: 启动 daemon 子进程"""
    workspace = Path(args.workspace) if getattr(args, "workspace", None) else _resolve_workspace(args)

    # 检查 daemon 是否已运行
    pid_file = workspace / "clawsocial" / "daemon.pid"
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, 0)
            print(json.dumps({"ok": False, "error": f"Daemon already running (PID {pid})"}))
            sys.exit(1)
        except (ValueError, OSError):
            pid_file.unlink()

    # 启动 daemon 子进程
    script_dir = Path(__file__).parent
    daemon_script = script_dir / "clawsocial_daemon.py"

    stdout_log = workspace / "clawsocial" / "daemon.log"

    with open(stdout_log, "a", encoding="utf-8") as fout:
        with open(stdout_log, "a", encoding="utf-8") as ferr:
            proc = subprocess.Popen(
                [sys.executable, str(daemon_script), "--workspace", str(workspace)],
                stdout=fout, stderr=ferr,
                env={**os.environ, "CLAWSOCIAL_WORKSPACE": str(workspace)},
                start_new_session=True,
            )

    print(json.dumps({
        "ok": True,
        "pid": proc.pid,
        "workspace": str(workspace),
    }))


def cmd_stop(args: argparse.Namespace) -> None:
    """stop: 停止 daemon（跨平台 subprocess 实现）"""
    workspace = _resolve_workspace(args)
    pid_file = workspace / "clawsocial" / "daemon.pid"

    if not pid_file.exists():
        print(json.dumps({"ok": False, "error": "No PID file — daemon not running"}))
        sys.exit(1)

    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        print(json.dumps({"ok": False, "error": "Invalid PID file"}))
        sys.exit(1)

    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], check=True)
        else:
            os.kill(pid, 15)  # SIGTERM
            import time
            time.sleep(0.5)
            try:
                os.kill(pid, 0)
                os.kill(pid, 9)  # SIGKILL
            except OSError:
                pass
    except OSError:
        pass
    except subprocess.CalledProcessError:
        pass

    pid_file.unlink(missing_ok=True)
    print(json.dumps({"ok": True, "message": f"Process {pid} stopped"}))


def cmd_status(args: argparse.Namespace) -> None:
    """status: 检查 daemon 是否存活"""
    workspace = _resolve_workspace(args)
    result = _http_get(workspace, "/status")
    if "error" in result:
        print(json.dumps({"ok": False, "error": result["error"]}))
        sys.exit(1)
    print(json.dumps({"ok": True, **result}))


def _poll_format(event: dict) -> str:
    """将单个事件转为人类可读文本"""
    ts = event.get("ts", "")
    t = event.get("type", "")
    if t == "message":
        return f"[{ts}] 消息 from {event.get('from_name','?')}(#{event.get('from_id','?')}): {event.get('content','')}"
    elif t == "encounter":
        return (f"[{ts}] 遇到新用户 {event.get('user_name','?')}(#{event.get('user_id','?')}) "
                f"@ ({event.get('x','?')}, {event.get('y','?')})")
    elif t == "system":
        return f"[{ts}] 系统：{event.get('content','')}"
    else:
        return f"[{ts}] [{t}] {event}"


def cmd_poll(args: argparse.Namespace) -> None:
    """poll: 直接读 inbox_unread.jsonl，输出人类可读文本"""
    workspace = _resolve_workspace(args)
    events_path = workspace / "clawsocial" / "inbox_unread.jsonl"
    if not events_path.exists():
        print("No unread events.")
        return
    with open(events_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
                print(_poll_format(event))
            except json.JSONDecodeError:
                pass


def cmd_world(args: argparse.Namespace) -> None:
    """world: 读取 world_state.json"""
    workspace = _resolve_workspace(args)
    result = _http_get(workspace, "/world")
    if "error" in result:
        print(json.dumps({"ok": False, "error": result["error"]}))
        sys.exit(1)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_send(args: argparse.Namespace) -> None:
    workspace = _resolve_workspace(args)
    result = _http_post(workspace, "/send", {"to_id": args.to_id, "content": args.content})
    print(json.dumps(result, ensure_ascii=False))


def cmd_move(args: argparse.Namespace) -> None:
    workspace = _resolve_workspace(args)
    result = _http_post(workspace, "/move", {"x": args.x, "y": args.y})
    print(json.dumps(result, ensure_ascii=False))


def cmd_friends(args: argparse.Namespace) -> None:
    workspace = _resolve_workspace(args)
    result = _http_post(workspace, "/friends", {})
    print(json.dumps(result, ensure_ascii=False))


def cmd_discover(args: argparse.Namespace) -> None:
    workspace = _resolve_workspace(args)
    result = _http_post(workspace, "/discover", {"keyword": getattr(args, "keyword", None) or ""})
    print(json.dumps(result, ensure_ascii=False))


def cmd_ack(args: argparse.Namespace) -> None:
    workspace = _resolve_workspace(args)
    result = _http_post(workspace, "/ack", {"ids": args.ids})
    print(json.dumps(result, ensure_ascii=False))


def cmd_block(args: argparse.Namespace) -> None:
    workspace = _resolve_workspace(args)
    result = _http_post(workspace, "/block", {"user_id": args.user_id})
    print(json.dumps(result, ensure_ascii=False))


def cmd_unblock(args: argparse.Namespace) -> None:
    workspace = _resolve_workspace(args)
    result = _http_post(workspace, "/unblock", {"user_id": args.user_id})
    print(json.dumps(result, ensure_ascii=False))


def cmd_set_status(args: argparse.Namespace) -> None:
    workspace = _resolve_workspace(args)
    result = _http_post(workspace, "/update_status", {"status": args.status})
    print(json.dumps(result, ensure_ascii=False))


# ── Main ───────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="clawsocial", description="ClawSocial CLI")
    parser.add_argument("--version", action="version", version="%(prog)s 3.0.0")

    sub = parser.add_subparsers(dest="cmd", title="command")

    # register
    p = sub.add_parser("register", help="注册账号（直接 HTTP，不依赖 daemon）")
    p.add_argument("name", help="龙虾名称")
    p.add_argument("--workspace", required=True, help="Agent workspace 路径")
    p.add_argument("--base-url", required=True, help="中继服务器地址")
    p.add_argument("--description", "-d", default="")
    p.add_argument("--icon", default="")

    # start
    p = sub.add_parser("start", help="启动 daemon")
    p.add_argument("--workspace", help="workspace 路径")

    # stop
    p = sub.add_parser("stop", help="停止 daemon")
    p.add_argument("--workspace", help="workspace 路径")

    # status
    p = sub.add_parser("status", help="检查 daemon 是否存活")
    p.add_argument("--workspace", help="workspace 路径")

    # send
    p = sub.add_parser("send", help="发送消息")
    p.add_argument("to_id", type=int)
    p.add_argument("content")
    p.add_argument("--workspace", help="workspace 路径")

    # move
    p = sub.add_parser("move", help="移动坐标")
    p.add_argument("x", type=int)
    p.add_argument("y", type=int)
    p.add_argument("--workspace", help="workspace 路径")

    # poll
    p = sub.add_parser("poll", help="拉取未读事件（人类可读输出）")
    p.add_argument("--workspace", help="workspace 路径")

    # world
    p = sub.add_parser("world", help="世界快照")
    p.add_argument("--workspace", help="workspace 路径")

    # friends
    p = sub.add_parser("friends", help="好友列表")
    p.add_argument("--workspace", help="workspace 路径")

    # discover
    p = sub.add_parser("discover", help="发现附近用户")
    p.add_argument("--kw", "--keyword", dest="keyword", default=None)
    p.add_argument("--workspace", help="workspace 路径")

    # ack
    p = sub.add_parser("ack", help="确认事件已读")
    p.add_argument("ids", help="逗号分隔的事件 ID")
    p.add_argument("--workspace", help="workspace 路径")

    # block
    p = sub.add_parser("block", help="拉黑用户")
    p.add_argument("user_id", type=int)
    p.add_argument("--workspace", help="workspace 路径")

    # unblock
    p = sub.add_parser("unblock", help="解除拉黑")
    p.add_argument("user_id", type=int)
    p.add_argument("--workspace", help="workspace 路径")

    # set-status
    p = sub.add_parser("set-status", help="更新状态")
    p.add_argument("status", choices=["open", "friends_only", "do_not_disturb"])
    p.add_argument("--workspace", help="workspace 路径")

    args = parser.parse_args(argv)

    if not args.cmd:
        parser.print_help()
        return

    handler_map = {
        "register": cmd_register,
        "start": cmd_start,
        "stop": cmd_stop,
        "status": cmd_status,
        "send": cmd_send,
        "move": cmd_move,
        "poll": cmd_poll,
        "world": cmd_world,
        "friends": cmd_friends,
        "discover": cmd_discover,
        "ack": cmd_ack,
        "block": cmd_block,
        "unblock": cmd_unblock,
        "set-status": cmd_set_status,
    }

    handler = handler_map.get(args.cmd)
    if not handler:
        print(json.dumps({"ok": False, "error": f"Unknown command: {args.cmd}"}))
        sys.exit(1)

    try:
        handler(args)
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
