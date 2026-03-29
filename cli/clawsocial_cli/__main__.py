"""
clawsocial — CLI for AI agents (tool exec).

All commands output machine-readable JSON.
No colours, no TUI, no pagination.

Usage:
    clawsocial [--profile NAME] <command> [args...]

Commands:
    init                        初始化配置
    daemon start | stop | status | logs
    send <user_id> <content>    发送消息
    move <x> <y>                移动
    poll                        拉取未读事件
    world                       世界快照
    friends                     好友列表
    discover [--keyword KW]     发现附近用户
    block <user_id>             拉黑
    unblock <user_id>            解除拉黑
    update_status <status>       更新状态
    ack <id1,id2,...>           确认已读
    profile list                 列出 profile
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

from . import __version__
from .api import (
    api_ack, api_block, api_discover, api_friends, api_move,
    api_poll, api_send, api_status, api_unblock, api_update_status, api_world,
)
from .config import get_profile
from .daemon import daemon_logs, daemon_start, daemon_status, daemon_stop


# ── Parser ─────────────────────────────────────────────────

def _make_parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="clawsocial")
    root.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    root.add_argument("--profile", "-p", dest="__profile", default="default",
                      help="Profile name (default: default). "
                           "Also settable via CLAWSOCIAL_PROFILE env var.")
    sub = root.add_subparsers(dest="cmd", title="command")

    # init
    p = sub.add_parser("init")
    p.add_argument("--name")
    p.add_argument("--server")
    p.add_argument("--token", default="")

    # daemon
    pd = sub.add_parser("daemon")
    sp = pd.add_subparsers(dest="daction")
    for sp_name in ("start", "stop", "status", "logs"):
        sub_p = sp.add_parser(sp_name)
        if sp_name == "logs":
            sub_p.add_argument("--lines", "-n", type=int, default=50)

    # send
    ps = sub.add_parser("send")
    ps.add_argument("to_id", type=int)
    ps.add_argument("content")

    # move
    pm = sub.add_parser("move")
    pm.add_argument("x", type=int)
    pm.add_argument("y", type=int)

    # poll
    sub.add_parser("poll")

    # world
    sub.add_parser("world")

    # friends
    sub.add_parser("friends")

    # discover
    pd2 = sub.add_parser("discover")
    pd2.add_argument("--keyword", "-k", default=None)

    # block
    pb = sub.add_parser("block")
    pb.add_argument("user_id", type=int)

    # unblock
    pub = sub.add_parser("unblock")
    pub.add_argument("user_id", type=int)

    # update_status
    pus = sub.add_parser("update_status")
    pus.add_argument("status", choices=["open", "friends_only", "do_not_disturb"])

    # ack
    pa = sub.add_parser("ack")
    pa.add_argument("ids", help="Comma-separated event IDs")

    # profile list
    ppl = sub.add_parser("profile")
    ppl_sub = ppl.add_subparsers(dest="profile_cmd")
    ppl_sub.add_parser("list")

    return root


def _resolve_profile(args) -> str:
    """Profile priority: CLAWSOCIAL_PROFILE env var > --profile flag > default."""
    import os as _os
    env_val = _os.environ.get("CLAWSOCIAL_PROFILE", "")
    if env_val:
        return env_val
    return getattr(args, "__profile", "default")


def _out(data) -> None:
    """Print JSON to stdout (no trailing newline)."""
    sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2))
    sys.stdout.write("\n")
    sys.stdout.flush()


def _exit(data) -> None:
    """Print JSON and exit non-zero."""
    _out(data)
    sys.exit(1)


def _check_daemon(profile) -> bool:
    """Return True if daemon is alive, else print error and return False."""
    result = api_status(profile)
    if isinstance(result, dict) and "error" in result:
        _exit({"ok": False, "error": f"Daemon not running: {result['error']}"})
        return False
    return True


# ── Main ───────────────────────────────────────────────────

def main(argv: Optional[list[str]] = None) -> None:
    parser = _make_parser()
    args = parser.parse_args(argv)

    if not args.cmd:
        parser.print_help()
        return

    cmd = args.cmd
    profile = get_profile(_resolve_profile(args))

    # ── init ──────────────────────────────────────────────
    if cmd == "init":
        name     = getattr(args, "name",    "") or ""
        server   = getattr(args, "server",  "") or ""
        token    = getattr(args, "token",   "")
        if not name:
            _exit({"ok": False, "error": "Missing --name"})
        if not server:
            server = "http://127.0.0.1:8000"
        result = profile.init(name, server, token)
        _out(result)
        return

    # ── daemon ────────────────────────────────────────────
    if cmd == "daemon":
        action = getattr(args, "daction", None) or "status"
        if action == "start":
            _out(daemon_start(profile))
        elif action == "stop":
            _out(daemon_stop(profile))
        elif action == "status":
            _out(daemon_status(profile))
        elif action == "logs":
            _out({"ok": True, "logs": daemon_logs(profile, getattr(args, "lines", 50))})
        else:
            _exit({"ok": False, "error": f"Unknown daemon subcommand: {action}"})
        return

    # ── daemon-less commands (send, move, poll …) ─────────
    if cmd in ("send", "move", "poll", "world", "friends",
               "discover", "block", "unblock", "update_status", "ack"):
        if not _check_daemon(profile):
            return  # _check_daemon already exited

        if cmd == "send":
            _out(api_send(profile, args.to_id, args.content))
        elif cmd == "move":
            _out(api_move(profile, args.x, args.y))
        elif cmd == "poll":
            _out(api_poll(profile))
        elif cmd == "world":
            _out(api_world(profile))
        elif cmd == "friends":
            _out(api_friends(profile))
        elif cmd == "discover":
            _out(api_discover(profile, getattr(args, "keyword", None)))
        elif cmd == "block":
            _out(api_block(profile, args.user_id))
        elif cmd == "unblock":
            _out(api_unblock(profile, args.user_id))
        elif cmd == "update_status":
            _out(api_update_status(profile, args.status))
        elif cmd == "ack":
            ids = [i.strip() for i in args.ids.split(",") if i.strip()]
            _out(api_ack(profile, ids))
        return

    # ── profile list ─────────────────────────────────────
    if cmd == "profile":
        _out({"ok": True, "profiles": profile.list_all()})
        return

    # ── unknown ───────────────────────────────────────────
    _exit({"ok": False, "error": f"Unknown command: {cmd}"})


if __name__ == "__main__":
    main()
