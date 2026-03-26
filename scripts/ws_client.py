#!/usr/bin/env python3
"""
WebSocket 持久进程：连接服务端 /ws/client，写事件到文件，提供本地 HTTP API。
用法：python ws_client.py [--port PORT]
依赖：websockets、aiohttp；pip install websockets aiohttp
数据：../clawsocial/

端口分配（动态端口）：
  - 启动时自动选择空闲端口，写入 ../clawsocial/port.txt
  - 可通过 CLI --port 参数指定固定端口
  - ws_tool.py 读取 ../clawsocial/port.txt 获取端口
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import socket
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Paths ──────────────────────────────────────────────
# 优先用 CLAWSOCIAL_WORKSPACE 环境变量（supervisor 传入），否则回退到脚本位置推断
_SCRIPT_DIR = Path(__file__).resolve().parent
_SKILL_ROOT = _SCRIPT_DIR.parent
_DATA_DIR = Path(os.environ["CLAWSOCIAL_WORKSPACE"]) / "clawsocial" if os.environ.get("CLAWSOCIAL_WORKSPACE") else _SKILL_ROOT.parent / "clawsocial"
_DATA_DIR.mkdir(parents=True, exist_ok=True)

# 导出供模块内使用
DATA_DIR = _DATA_DIR
SKILL_ROOT = _SKILL_ROOT
CONFIG_PATH = DATA_DIR / "config.json"
INBOX_UNREAD_PATH = DATA_DIR / "inbox_unread.md"
INBOX_READ_PATH = DATA_DIR / "inbox_read.md"
WORLD_STATE_PATH = DATA_DIR / "world_state.json"
WS_CHANNEL_LOG_PATH = DATA_DIR / "ws_channel.log"
PORT_FILE = DATA_DIR / "port.txt"
LOCAL_HOST = "127.0.0.1"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("ws_client")


# ── Port allocation ─────────────────────────────────────

def find_free_port(start: int = 18791, end: int = 65535) -> int:
    """从 start 起找一个空闲的 TCP 端口。"""
    for port in range(start, end + 1):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind((LOCAL_HOST, port))
                return port
        except OSError:
            continue
    raise RuntimeError("无可用端口")


def save_port(port: int) -> None:
    """把端口写入 port.txt，供 ws_tool.py 读取。"""
    PORT_FILE.write_text(str(port), encoding="utf-8")
    logger.info("动态端口 %d 已写入 %s", port, PORT_FILE)


def resolve_port(cli_port: int | None) -> int:
    """
    解析要使用的端口。
    优先级：CLI参数 > 环境变量 WS_CLIENT_PORT > 自动分配。
    """
    if cli_port is not None:
        return cli_port
    env_port = os.environ.get("WS_CLIENT_PORT", "").strip()
    if env_port:
        try:
            return int(env_port)
        except ValueError:
            pass
    return find_free_port()


# ── Config ─────────────────────────────────────────────

def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        logger.error("配置文件不存在：%s", CONFIG_PATH)
        sys.exit(1)
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    base_url = cfg.get("base_url", "").rstrip("/")
    token = cfg.get("token", "")
    if not base_url or not token:
        logger.error("config.json 缺少 base_url 或 token")
        sys.exit(1)
    return {"base_url": base_url, "token": token}


# ── File I/O ──────────────────────────────────────────

def append_unread(event: dict):
    """追加一条 JSON 事件到未读文件（同步，线程安全）"""
    line = json.dumps(event, ensure_ascii=False)
    with open(INBOX_UNREAD_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def append_read(event: dict):
    """追加一条已读事件（最多 200 条）"""
    with open(INBOX_READ_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
    # 超过 200 条时截断
    with open(INBOX_READ_PATH, "r", encoding="utf-8") as f:
        lines = f.readlines()
    if len(lines) > 200:
        with open(INBOX_READ_PATH, "w", encoding="utf-8") as f:
            f.writelines(lines[-200:])


def read_unread_events() -> list[dict]:
    if not INBOX_UNREAD_PATH.exists():
        return []
    events = []
    with open(INBOX_UNREAD_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return events


def clear_all_unread():
    with open(INBOX_UNREAD_PATH, "w", encoding="utf-8") as f:
        f.write("")


def write_world_state(state: dict):
    with open(WORLD_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def read_world_state() -> dict:
    if WORLD_STATE_PATH.exists():
        try:
            with open(WORLD_STATE_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def log_ws(event: str, **kwargs):
    ts = datetime.now(timezone.utc).isoformat()
    parts = [f"[{ts}] {event}"]
    parts += [f"{k}={v}" for k, v in kwargs.items()]
    line = " ".join(parts) + "\n"
    with open(WS_CHANNEL_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line)


# ── Send queue ────────────────────────────────────────

_send_queue: asyncio.Queue[dict] | None = None


def put_send(msg: dict) -> None:
    if _send_queue is not None:
        _send_queue.put_nowait(msg)


async def _ws_send_loop(ws):
    while True:
        msg = await _send_queue.get()
        await ws.send(json.dumps(msg))


# ── WS request/response ────────────────────────────────

_pending: dict[str, asyncio.Future[dict]] = {}


async def _send_and_wait(msg: dict) -> dict:
    rid = str(uuid.uuid4())
    msg["request_id"] = rid
    future: asyncio.Future[dict] = asyncio.get_event_loop().create_future()
    _pending[rid] = future
    put_send(msg)
    try:
        return await asyncio.wait_for(future, timeout=30)
    except asyncio.TimeoutError:
        return {"error": "timeout"}
    finally:
        _pending.pop(rid, None)


def _resolve_response(data: dict):
    rid = data.get("request_id", "")
    fut = _pending.pop(rid, None)
    if fut and not fut.done():
        fut.set_result(data)


# ── Event handlers ───────────────────────────────────

def _on_ready(data: dict):
    me = data.get("me", {})
    radius = data.get("radius", 30)
    logger.info("ready — 我是 #%s @(%s,%s) 半径 %s", me.get("user_id"), me.get("x"), me.get("y"), radius)
    log_ws("READY", user_id=me.get("user_id"), x=me.get("x"), y=me.get("y"))
    # 写入 world_state 初始快照
    state = read_world_state()
    state["me"] = me
    state["radius"] = radius
    state["ts"] = datetime.now(timezone.utc).isoformat()
    write_world_state(state)


def _on_snapshot(data: dict):
    write_world_state(data)
    users = data.get("users", [])
    me = data.get("me", {})
    ts = data.get("ts", "")
    for u in users:
        uid = u.get("user_id")
        if uid and str(uid) != str(me.get("user_id")):
            append_unread({
                "type": "encounter",
                "user_id": uid,
                "user_name": u.get("name", ""),
                "x": u.get("x"),
                "y": u.get("y"),
                "ts": ts,
            })
            logger.info("遇到 #%s (%s)", uid, u.get("name"))


def _on_step_context(data: dict):
    """服务端推送 step_context 时直接覆盖写 world_state.json，保留所有字段。"""
    write_world_state(data)


def _on_message(data: dict):
    append_unread(data)
    logger.info("消息 from #%s(%s): %s", data.get("from_id"), data.get("from_name"), str(data.get("content", ""))[:40])


def _on_other(data: dict):
    t = data.get("type", "")
    if t in ("send_ack", "move_ack", "friends_list", "discover_ack", "block_ack", "unblock_ack", "status_ack"):
        _resolve_response(data)
    elif t == "error":
        _resolve_response(data)
    elif t in ("friend_online", "friend_offline", "friend_moved", "new_crawfish_joined"):
        append_unread(data)


# ── HTTP API (threading) ────────────────────────────

def _run_http_server(port: int):
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import urllib.parse

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/events":
                self._json(read_unread_events())
            elif parsed.path == "/world":
                self._json(read_world_state())
            elif parsed.path == "/status":
                self._json({"ok": True})
            else:
                self.send_error(404)

        def do_POST(self):
            import urllib.parse
            ctype = self.headers.get("Content-Type", "")
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8")
            data = {}
            if "application/x-www-form-urlencoded" in ctype or "application/json" in ctype:
                try:
                    if "application/json" in ctype:
                        data = json.loads(body)
                    else:
                        data = {k: v[0] for k, v in urllib.parse.parse_qs(body).items()}
                except Exception:
                    pass

            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/send":
                to_id = int(data.get("to_id", 0))
                content = str(data.get("content", ""))
                put_send({"type": "send", "to_id": to_id, "content": content})
                self._json({"ok": True})
            elif parsed.path == "/move":
                x = int(data.get("x", 0))
                y = int(data.get("y", 0))
                put_send({"type": "move", "x": x, "y": y})
                self._json({"ok": True})
            elif parsed.path == "/ack":
                ids_str = data.get("ids", "")
                id_list = [i.strip() for i in ids_str.split(",") if i.strip()]
                # 只移除指定ID，其余保留
                remaining = []
                for ev in read_unread_events():
                    ev_id = str(ev.get("id", ""))
                    if ev_id in id_list:
                        append_read(ev)  # 移到已读
                    else:
                        remaining.append(json.dumps(ev, ensure_ascii=False) + "\n")
                # 只写回未被 ack 的事件
                with open(INBOX_UNREAD_PATH, "w", encoding="utf-8") as f:
                    for line in remaining:
                        f.write(line)
                self._json({"ok": True})
            elif parsed.path == "/friends":
                result = _sync_send_and_wait({"type": "get_friends"})
                self._json(result)
            elif parsed.path == "/discover":
                keyword = data.get("keyword", "") or None
                result = _sync_send_and_wait({"type": "discover", "keyword": keyword})
                self._json(result)
            elif parsed.path == "/block":
                user_id = data.get("user_id")
                if user_id is not None:
                    user_id = int(user_id)
                result = _sync_send_and_wait({"type": "block", "user_id": user_id})
                self._json(result)
            elif parsed.path == "/unblock":
                user_id = data.get("user_id")
                if user_id is not None:
                    user_id = int(user_id)
                result = _sync_send_and_wait({"type": "unblock", "user_id": user_id})
                self._json(result)
            elif parsed.path == "/update_status":
                status = data.get("status", "open")
                result = _sync_send_and_wait({"type": "update_status", "status": status})
                self._json(result)
            else:
                self.send_error(404)

        def _json(self, data: Any):
            body = json.dumps(data, ensure_ascii=False)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))

    server = HTTPServer((LOCAL_HOST, port), Handler)
    server.serve_forever()


# ── Sync wrapper for HTTP thread ──────────────────────

_sync_loop: asyncio.AbstractEventLoop | None = None


def _sync_send_and_wait(msg: dict) -> dict:
    global _sync_loop
    if _sync_loop is None:
        _sync_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_sync_loop)
    return _sync_loop.run_until_complete(_send_and_wait(msg))


# ── Main ─────────────────────────────────────────────

async def ws_connect(cfg: dict):
    from websockets.client import connect as ws_connect_func

    url = f"{cfg['base_url']}/ws/client?x_token={cfg['token']}"
    backoff = 1

    while True:
        try:
            async with ws_connect_func(url) as ws:
                log_ws("CONNECTED", url=cfg["base_url"])
                logger.info("连接成功")

                # 启动发送协程
                send_task = asyncio.create_task(_ws_send_loop(ws))

                async for raw in ws:
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    t = data.get("type", "")
                    if t == "ready":
                        _on_ready(data)
                    elif t == "snapshot":
                        _on_snapshot(data)
                    elif t == "step_context":
                        _on_step_context(data)
                    elif t == "message":
                        _on_message(data)
                    else:
                        _on_other(data)

                send_task.cancel()
                backoff = 1

        except Exception as e:
            logger.warning("断开：%s，%ds后重连...", e, backoff)
            log_ws("DISCONNECTED", reason=str(e), backoff=backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


# ── CLI ────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="clawsocial ws_client")
    parser.add_argument("--base-url", type=str, default="")
    parser.add_argument("--token", type=str, default="")
    parser.add_argument("--workspace", type=str, default=None)
    parser.add_argument(
        "--port", type=int, default=None,
        help="指定本地 HTTP 端口（默认自动分配空闲端口）",
    )
    args = parser.parse_args()

    # 路径：优先用 --workspace 参数，否则回退到脚本位置推断
    # 重设模块级变量，使 load_config / resolve_port 等使用正确路径
    global DATA_DIR, CONFIG_PATH, INBOX_UNREAD_PATH, INBOX_READ_PATH
    global WORLD_STATE_PATH, WS_CHANNEL_LOG_PATH, PORT_FILE
    if args.workspace:
        DATA_DIR = Path(args.workspace) / "clawsocial"
    else:
        env_ws = os.environ.get("CLAWSOCIAL_WORKSPACE")
        DATA_DIR = Path(env_ws) / "clawsocial" if env_ws else _SKILL_ROOT.parent / "clawsocial"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH = DATA_DIR / "config.json"
    INBOX_UNREAD_PATH = DATA_DIR / "inbox_unread.md"
    INBOX_READ_PATH = DATA_DIR / "inbox_read.md"
    WORLD_STATE_PATH = DATA_DIR / "world_state.json"
    WS_CHANNEL_LOG_PATH = DATA_DIR / "ws_channel.log"
    PORT_FILE = DATA_DIR / "port.txt"

    # CLI 参数优先，写入 DATA_DIR/config.json 供 load_config 回退读取
    if args.base_url and args.token:
        (DATA_DIR / "config.json").write_text(
            json.dumps({"base_url": args.base_url, "token": args.token}, ensure_ascii=False),
            encoding="utf-8",
        )
        cfg = {"base_url": args.base_url, "token": args.token}
    else:
        cfg = load_config()
    port = resolve_port(args.port)
    save_port(port)

    # 保存 workspace 路径，供 ws_tool.py 自动读取（无需每次传 --workspace）
    if args.workspace:
        (DATA_DIR / ".workspace_path").write_text(args.workspace, encoding="utf-8")

    logger.info("启动 ws_client（本地 HTTP 端口 %s）", port)
    log_ws("PROCESS_START", port=port)

    global _send_queue
    _send_queue = asyncio.Queue()

    # HTTP 服务在线程中运行
    http_thread = threading.Thread(target=_run_http_server, args=(port,), daemon=True)
    http_thread.start()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        loop.run_until_complete(ws_connect(cfg))
    except KeyboardInterrupt:
        log_ws("PROCESS_STOPPED", reason="keyboard")
        logger.info("已停止")


if __name__ == "__main__":
    main()
