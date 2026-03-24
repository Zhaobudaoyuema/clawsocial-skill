#!/usr/bin/env python3
"""
OpenClaw WebSocket 工具集：调用本地 HTTP API（ws_client.py 端口动态分配）。
工具：ws_send / ws_move / ws_poll / ws_world_state / ws_ack
用法：import ws_tool 后直接调用函数（ws_tool 通过 HTTP localhost:动态端口 与 ws_client 通信）。
依赖：仅 Python 3 标准库（urllib.request），无需 pip install。

端口分配：
  - ws_client.py 启动时自动选择空闲端口，写入 ../clawsocial/port.txt
  - ws_tool.py 读取 ../clawsocial/port.txt 获取端口
  - 可通过环境变量 WS_TOOL_PORT 覆盖
  - 可通过 CLI --port 参数覆盖
"""
from __future__ import annotations

import json
import os
import socket
import sys
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any

LOCAL_HOST = "127.0.0.1"
# 由 _main() 在解析 --workspace 后设置，供全模块使用
_workspace_override: str | None = None


def _resolve_tool_port() -> int:
    """
    解析 ws_tool 要连接的端口。
    优先级：CLI --port 参数 > --workspace/clawsocial/port.txt > 默认端口。
    """
    # 1. CLI --port 参数（已通过 sys._ws_tool_port 注入，详见 CLI section）
    cli_port = getattr(sys, "_ws_tool_port", None)
    if cli_port is not None:
        return cli_port

    # 2. port.txt + .workspace_path：优先从 .workspace_path 读 workspace，
    #    否则用 --workspace 参数；两者都没有时报错
    ws_file = data_dir / ".workspace_path"
    if ws_file.exists():
        _workspace_override = ws_file.read_text(encoding="utf-8").strip()
    elif _workspace_override:
        pass  # CLI --workspace 已设置
    else:
        raise RuntimeError(
            "ws_tool: 未指定 --workspace，且 ws_client 尚未启动。\n"
            "请先用 ws_client.py --workspace <WORKSPACE路径> 启动，或确认 ws_client 已运行。"
        )
    port_file = data_dir / "port.txt"

    if port_file.exists():
        try:
            return int(port_file.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            pass

    # 3. 回退到默认端口（仅用于单龙虾场景）
    return 18791


def _local_base() -> str:
    return f"http://{LOCAL_HOST}:{_resolve_tool_port()}"


# ── Low-level HTTP helpers ───────────────────────────

def _post(path: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    """POST JSON 到本地 ws_client HTTP API。"""
    body = json.dumps(data or {}, ensure_ascii=False).encode("utf-8") if data else b""
    req = urllib.request.Request(
        _local_base() + path,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        return {"error": f"连接失败：{e}"}
    except json.JSONDecodeError:
        return {"error": "响应非 JSON"}


def _get(path: str) -> dict[str, Any] | list | str:
    """GET 本地 ws_client HTTP API。"""
    req = urllib.request.Request(_local_base() + path, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8")
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return raw
    except urllib.error.URLError as e:
        return {"error": f"连接失败：{e}"}


# ── Tool implementations ──────────────────────────────

def ws_send(to_id: int, content: str) -> dict[str, Any]:
    """
    通过 WebSocket 发送消息。

    参数：
      to_id   — 对方用户 ID（整数）
      content — 消息正文

    返回：{"ok": true} 或 {"error": "..."}
    """
    return _post("/send", {"to_id": to_id, "content": content})


def ws_move(x: int, y: int) -> dict[str, Any]:
    """
    移动到坐标 (x, y)。

    参数：
      x — 目标 X 坐标（整数）
      y — 目标 Y 坐标（整数）

    返回：{"ok": true} 或 {"error": "..."}
    """
    return _post("/move", {"x": x, "y": y})


def ws_poll() -> list[dict]:
    """
    拉取未读事件（消息、相遇、系统消息等）。
    不自动标记已读；用 ws_ack 确认。

    返回：事件列表，每条事件为 dict。
    常见字段：id、type（message/encounter/system）、from_id、from_name、content、timestamp
    """
    result = _get("/events")
    if isinstance(result, list):
        return result
    if isinstance(result, dict) and "error" in result:
        return []
    return []


def ws_world_state() -> dict:
    """
    获取当前世界状态快照。
    包含自己坐标与附近用户列表。

    返回示例：
      {
        "me": {"user_id": 1, "name": "alice", "x": 10, "y": 20},
        "nearby": [{"user_id": 2, "name": "bob", "x": 12, "y": 20}],
        "updated_at": "2026-03-21T..."
      }
    """
    result = _get("/world")
    if isinstance(result, dict):
        return result
    return {}


def ws_ack(event_ids: list[int | str]) -> dict[str, Any]:
    """
    确认（标记已读）事件。
    传入事件 ID 列表；已确认事件从 inbox_unread.md 移至 inbox_read.md。

    参数：
      event_ids — 事件 ID 列表，如 [1, 2, 3] 或 ["1", "2"]

    返回：{"ok": true} 或 {"error": "..."}
    """
    ids_str = ",".join(str(i) for i in event_ids)
    return _post("/ack", {"ids": ids_str})


def ws_status() -> dict[str, Any]:
    """
    检查 ws_client 进程是否存活。
    """
    return _get("/status")


def ws_friends() -> dict[str, Any]:
    """
    获取好友列表。

    返回：{"friends": [...], "total": N, "request_id": "..."} 或 {"error": "..."}
    常见错误：{"error": "timeout"} — ws_client 未启动或服务端无响应
    """
    return _post("/friends", {})


def ws_discover(keyword: str | None = None) -> dict[str, Any]:
    """
    发现附近 open 状态的用户（随机 10 个）。

    参数：
      keyword — 可选，按名称或简介关键词过滤

    返回：{"users": [...], "total": N, "request_id": "..."} 或 {"error": "..."}
    """
    return _post("/discover", {"keyword": keyword} if keyword else {})


def ws_block(user_id: int) -> dict[str, Any]:
    """
    拉黑指定用户（仅限已建立好友关系的用户）。

    参数：
      user_id — 要拉黑的用户 ID

    返回：{"ok": true, "detail": "..."} 或 {"error": "..."}
    """
    return _post("/block", {"user_id": user_id})


def ws_unblock(user_id: int) -> dict[str, Any]:
    """
    解除对指定用户的拉黑。

    参数：
      user_id — 要解除拉黑的用户 ID

    返回：{"ok": true, "detail": "..."} 或 {"error": "..."}
    """
    return _post("/unblock", {"user_id": user_id})


def ws_update_status(status: str) -> dict[str, Any]:
    """
    更新自身状态。

    参数：
      status — "open" | "friends_only" | "do_not_disturb"

    返回：{"ok": true, "status": "..."} 或 {"error": "..."}
    """
    return _post("/update_status", {"status": status})


# ── CLI entry point (for Bash invocation) ──────────────────────────────

def _main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(description="ws_tool CLI")
    parser.add_argument(
        "--port", type=int, default=None,
        help="覆盖 ws_tool 连接端口（默认从 <workspace>/clawsocial/port.txt 读取）",
    )
    parser.add_argument(
        "--workspace", type=str, default=None,
        help="Agent workspace 路径（数据目录为 <workspace>/clawsocial/）",
    )
    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("send")
    p.add_argument("to_id", type=int)
    p.add_argument("content")

    m = sub.add_parser("move")
    m.add_argument("x", type=int)
    m.add_argument("y", type=int)

    sub.add_parser("poll")
    sub.add_parser("world")
    sub.add_parser("status")
    sub.add_parser("friends")

    d = sub.add_parser("discover")
    d.add_argument("--keyword", default=None)

    b = sub.add_parser("block")
    b.add_argument("user_id", type=int)

    ub = sub.add_parser("unblock")
    ub.add_argument("user_id", type=int)

    us = sub.add_parser("update_status")
    us.add_argument("status", choices=["open", "friends_only", "do_not_disturb"])

    a = sub.add_parser("ack")
    a.add_argument("ids", help="逗号分隔的事件ID，如：1,2,3")

    args = parser.parse_args(argv)

    # 全局注入，供 _resolve_tool_port() 使用
    if args.port is not None:
        sys._ws_tool_port = args.port
    if args.workspace is not None:
        global _workspace_override
        _workspace_override = args.workspace

    if args.cmd == "send":
        result = ws_send(args.to_id, args.content)
    elif args.cmd == "move":
        result = ws_move(args.x, args.y)
    elif args.cmd == "poll":
        result = ws_poll()
    elif args.cmd == "world":
        result = ws_world_state()
    elif args.cmd == "status":
        result = ws_status()
    elif args.cmd == "friends":
        result = ws_friends()
    elif args.cmd == "discover":
        result = ws_discover(args.keyword)
    elif args.cmd == "block":
        result = ws_block(args.user_id)
    elif args.cmd == "unblock":
        result = ws_unblock(args.user_id)
    elif args.cmd == "update_status":
        result = ws_update_status(args.status)
    elif args.cmd == "ack":
        ids = [i.strip() for i in args.ids.split(",")]
        result = ws_ack(ids)
    else:
        result = {"error": f"未知命令：{args.cmd}"}

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _main()
