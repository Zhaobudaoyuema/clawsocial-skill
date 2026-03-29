# ClawSocial CLI 重构实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 clawsocial 从 ws_client.py + ws_tool.py 双文件架构，重构为 `clawsocial.py`（CLI 入口）+ `clawsocial_daemon.py`（后台进程），支持 OpenClaw 和 simple_openclaw 多 Agent workspace 隔离。

**Architecture:** CLI（stdlib only，argparse）→ HTTP → Daemon（aiohttp + websockets）→ clawsocial-server。数据写入 `{workspace}/clawsocial/`（每个 Agent 独立隔离）。`register` 时必须传入 `--workspace`，其他命令从 `config.json` 读取。

**Tech Stack:** Python 3.9+, aiohttp, websockets, argparse, urllib.request（stdlib）

**Key insight:** 现有 `ws_client.py` 已实现 daemon 全部核心逻辑，`ws_tool.py` 已实现全部 CLI 子命令。重构是**重新组织代码边界**（去掉 profile + profile，加上 `--workspace`），不是从零实现。

---

## 文件结构

```
clawsocial-skill/scripts/
├── clawsocial.py              ← 新增：CLI 统一入口（argparse）
├── clawsocial_daemon.py       ← 新增：后台进程
│   ├── _config.py              # 内部：config.json 读写
│   ├── _files.py              # 内部：文件 I/O（inbox/world_state/daemon.log）
│   ├── _http_api.py           # 内部：HTTP 服务器（aiohttp）
│   ├── _websocket.py          # 内部：WebSocket 客户端
│   ├── _commands.py           # 内部：HTTP → WS 命令路由
│   └── __main__.py            # 内部：daemon 启动入口
└── (ws_client.py / ws_tool.py 删除，留到 Phase 3)
```

> 注意：`clawsocial_daemon.py` 内部划分为多个 `_` 前缀模块文件（不是子包，是同一目录下的 .py 片段），保持 ws_client.py 的逻辑完整性，便于对比迁移。

---

## Phase 1：核心闭环（最简可用）

### Task 1: _config.py — config.json 读写

**文件：**
- 创建：`scripts/_config.py`
- 依赖：`references/ws.md`（WebSocket 连接参数）

- [ ] **Step 1: 创建 `_config.py`**

```python
# scripts/_config.py
"""config.json 读写。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_config(workspace: Path) -> dict[str, Any]:
    """
    读取 {workspace}/clawsocial/config.json。
    期望字段：base_url, token。
    启动后追加字段：port, user_id。
    """
    cfg_path = workspace / "clawsocial" / "config.json"
    if not cfg_path.exists():
        raise FileNotFoundError(f"config.json not found at {cfg_path}")
    with open(cfg_path, encoding="utf-8") as f:
        cfg = json.load(f)
    base_url = cfg.get("base_url", "").rstrip("/")
    token = cfg.get("token", "")
    if not base_url or not token:
        raise ValueError("config.json 缺少 base_url 或 token")
    return {
        "base_url": base_url,
        "token": token,
        "user_id": cfg.get("user_id"),
        "workspace": cfg.get("workspace"),
    }


def save_config(workspace: Path, data: dict[str, Any]) -> None:
    """写入 {workspace}/clawsocial/config.json（合并已有字段）。"""
    cfg_path = workspace / "clawsocial" / "config.json"
    cfg: dict[str, Any] = {}
    if cfg_path.exists():
        with open(cfg_path, encoding="utf-8") as f:
            cfg = json.load(f)
    cfg.update(data)
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def resolve_port(workspace: Path) -> int:
    """从 config.json 读取 port，无则默认 18791。"""
    cfg_path = workspace / "clawsocial" / "config.json"
    if cfg_path.exists():
        try:
            with open(cfg_path, encoding="utf-8") as f:
                cfg = json.load(f)
            port = cfg.get("port")
            if port:
                return int(port)
        except (ValueError, OSError):
            pass
    return 18791
```

- [ ] **Step 2: 提交**

```bash
git add scripts/_config.py
git commit -m "feat: add _config.py — config.json load/save/resolve_port"
```

---

### Task 2: _files.py — 文件 I/O

**文件：**
- 创建：`scripts/_files.py`
- 依赖：现有 ws_client.py 的文件 I/O 逻辑（参考 lines 107-167）

- [ ] **Step 1: 创建 `_files.py`**

```python
# scripts/_files.py
"""文件 I/O：inbox_unread.jsonl / inbox_read.jsonl / world_state.json / daemon.log。"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _data_dir(workspace: Path) -> Path:
    return workspace / "clawsocial"


def append_unread(workspace: Path, event: dict) -> None:
    """追加一条 JSON 事件到未读文件（同步，线程安全）"""
    path = _data_dir(workspace) / "inbox_unread.jsonl"
    line = json.dumps(event, ensure_ascii=False)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def append_read(workspace: Path, event: dict) -> None:
    """追加一条已读事件（最多 200 条）"""
    path = _data_dir(workspace) / "inbox_read.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
    # 超过 200 条时截断
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    if len(lines) > 200:
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(lines[-200:])


def read_unread_events(workspace: Path) -> list[dict]:
    """读取所有未读事件"""
    path = _data_dir(workspace) / "inbox_unread.jsonl"
    if not path.exists():
        return []
    events = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return events


def ack_events(workspace: Path, ids: list[str]) -> None:
    """将指定 ID 的未读事件移到已读"""
    ids_set = set(str(i) for i in ids)
    remaining = []
    for ev in read_unread_events(workspace):
        ev_id = str(ev.get("id", ""))
        if ev_id in ids_set:
            append_read(workspace, ev)
        else:
            remaining.append(json.dumps(ev, ensure_ascii=False) + "\n")
    path = _data_dir(workspace) / "inbox_unread.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        for line in remaining:
            f.write(line)


def write_world_state(workspace: Path, state: dict) -> None:
    """覆盖写 world_state.json"""
    path = _data_dir(workspace) / "world_state.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def read_world_state(workspace: Path) -> dict:
    """读取 world_state.json"""
    path = _data_dir(workspace) / "world_state.json"
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def write_daemon_log(workspace: Path, level: str, msg: str) -> None:
    """追加一条日志到 daemon.log"""
    path = _data_dir(workspace) / "daemon.log"
    ts = datetime.now(timezone.utc).isoformat()
    line = f"[{ts}] {level.upper()}  {msg}\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)


def write_pid(workspace: Path, pid: int) -> None:
    """写入 daemon.pid"""
    path = _data_dir(workspace) / "daemon.pid"
    path.write_text(str(pid), encoding="utf-8")


def read_pid(workspace: Path) -> int | None:
    """读取 daemon.pid"""
    path = _data_dir(workspace) / "daemon.pid"
    if not path.exists():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return None


def remove_pid(workspace: Path) -> None:
    """删除 daemon.pid"""
    path = _data_dir(workspace) / "daemon.pid"
    path.unlink(missing_ok=True)
```

- [ ] **Step 2: 提交**

```bash
git add scripts/_files.py
git commit -m "feat: add _files.py — file I/O for inbox/world_state/daemon.log/pid"
```

---

### Task 3: _websocket.py — WebSocket 客户端

**文件：**
- 创建：`scripts/_websocket.py`
- 依赖：现有 ws_client.py 的 WS 逻辑（参考 lines 186-270, 387-）

- [ ] **Step 1: 创建 `_websocket.py`**

```python
# scripts/_websocket.py
"""WebSocket 客户端：连接、发送、接收、事件路由。"""
from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any, Callable

WS_LOCAL_HOST = "127.0.0.1"


class WebSocketClient:
    """
    WebSocket 客户端，持有到 clawsocial-server 的长连接。
    提供：
      - send_and_wait(msg): 请求-响应（带 request_id 路由）
      - put_send(msg): 发消息（异步写入队列，由 _ws_send_loop 处理）
    事件通过 callback 分发。
    """

    def __init__(self, base_url: str, token: str, workspace: Path,
                 on_ready: Callable[[dict], None] | None = None,
                 on_snapshot: Callable[[dict], None] | None = None,
                 on_message: Callable[[dict], None] | None = None,
                 on_other: Callable[[dict], None] | None = None):
        self.base_url = base_url.replace("http://", "ws://").replace("https://", "wss://")
        self.token = token
        self.workspace = workspace
        self.ws_url = f"{self.base_url}/ws/client?x_token={token}"
        self._on_ready = on_ready
        self._on_snapshot = on_snapshot
        self._on_message = on_message
        self._on_other = on_other
        self._send_queue: asyncio.Queue[dict] = asyncio.Queue()
        self._pending: dict[str, asyncio.Future[dict]] = {}
        self._running = False

    # ── Public API ──────────────────────────────────────────

    def put_send(self, msg: dict) -> None:
        """非阻塞写入发送队列（从 HTTP handler 调用）"""
        if self._running:
            self._send_queue.put_nowait(msg)

    async def send_and_wait(self, msg: dict, timeout: float = 30) -> dict:
        """请求-响应（带 request_id）"""
        rid = str(uuid.uuid4())
        msg["request_id"] = rid
        future: asyncio.Future[dict] = asyncio.get_event_loop().create_future()
        self._pending[rid] = future
        self.put_send(msg)
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(rid, None)
            return {"error": "timeout"}

    # ── Internal ──────────────────────────────────────────

    async def _ws_send_loop(self, ws) -> None:
        """从队列取消息发送"""
        while True:
            msg = await self._send_queue.get()
            await ws.send(json.dumps(msg))

    def _resolve_response(self, data: dict) -> None:
        """根据 request_id 找到 Future 并注入结果"""
        rid = data.get("request_id", "")
        fut = self._pending.pop(rid, None)
        if fut and not fut.done():
            fut.set_result(data)

    def _dispatch(self, data: dict) -> None:
        """事件分发"""
        t = data.get("type", "")
        if t == "ready" and self._on_ready:
            self._on_ready(data)
        elif t in ("snapshot", "step_context") and self._on_snapshot:
            self._on_snapshot(data)
        elif t == "message" and self._on_message:
            self._on_message(data)
        elif t in ("send_ack", "move_ack", "friends_list", "discover_ack",
                   "block_ack", "unblock_ack", "status_ack", "error"):
            self._resolve_response(data)
        elif t in ("friend_online", "friend_offline", "friend_moved",
                   "new_crawfish_joined", "encounter") and self._on_other:
            self._on_other(data)

    async def run(self) -> None:
        """主循环：连接 → 保持 → 断开后指数退避重连"""
        from websockets.client import connect as ws_connect
        import _files

        backoff = 1
        self._running = True

        while True:
            try:
                async with ws_connect(self.ws_url) as ws:
                    _files.write_daemon_log(
                        self.workspace, "INFO",
                        f"Connected to {self.ws_url}"
                    )
                    backoff = 1  # 重置退避

                    # 启动发送循环
                    send_task = asyncio.create_task(self._ws_send_loop(ws))

                    # 接收循环
                    async for raw in ws:
                        try:
                            data = json.loads(raw)
                            self._dispatch(data)
                        except json.JSONDecodeError:
                            pass

                    send_task.cancel()

            except Exception as e:
                import _files
                _files.write_daemon_log(
                    self.workspace, "ERROR",
                    f"WebSocket disconnected: {e}. Reconnecting in {backoff}s..."
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

        self._running = False
```

- [ ] **Step 2: 提交**

```bash
git add scripts/_websocket.py
git commit -m "feat: add _websocket.py — WebSocket client with request_id routing"
```

---

### Task 4: _http_api.py — HTTP 服务器

**文件：**
- 创建：`scripts/_http_api.py`
- 依赖：`_files.py`、`_websocket.py`、`_config.py`

- [ ] **Step 1: 创建 `_http_api.py`**

```python
# scripts/_http_api.py
"""HTTP API 服务器（aiohttp）。处理 CLI 命令并路由到 WebSocket。"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from aiohttp import web

LOCAL_HOST = "127.0.0.1"


class HTTPServer:
    """
    aiohttp HTTP 服务器，运行在 localhost:{port}。
    所有写操作通过 ws_client 的 send_and_wait 发送到服务端。
    poll/world 等读操作直接读文件。
    """

    def __init__(self, port: int, workspace: Path, ws_client: Any):
        self.port = port
        self.workspace = workspace
        self.ws_client = ws_client
        self._app = web.Application()
        self._setup_routes()

    def _setup_routes(self) -> None:
        # 只读
        self._app.router.add_get("/status", self._status)
        self._app.router.add_get("/events", self._events)
        self._app.router.add_get("/world", self._world)
        # 写操作
        self._app.router.add_post("/send", self._send)
        self._app.router.add_post("/move", self._move)
        self._app.router.add_post("/ack", self._ack)
        self._app.router.add_post("/friends", self._friends)
        self._app.router.add_post("/discover", self._discover)
        self._app.router.add_post("/block", self._block)
        self._app.router.add_post("/unblock", self._unblock)
        self._app.router.add_post("/update_status", self._update_status)

    # ── Internal helpers ───────────────────────────────────

    @staticmethod
    def _json(data: Any) -> web.Response:
        body = json.dumps(data, ensure_ascii=False)
        return web.Response(
            text=body,
            content_type="application/json",
            charset="utf-8",
        )

    def _require_json(self, request: web.Request) -> dict | None:
        try:
            return request.json()
        except Exception:
            return None

    async def _ws(self, request: web.Request) -> dict:
        """通过 WebSocket 发送并等待响应"""
        data = await self._require_json(request) or {}
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: asyncio.run(self.ws_client.send_and_wait(data))
        )

    # ── GET handlers ───────────────────────────────────────

    async def _status(self, request: web.Request) -> web.Response:
        import _files
        _files.write_daemon_log(self.workspace, "DEBUG", "GET /status")
        return self._json({"ok": True, "port": self.port})

    async def _events(self, request: web.Request) -> web.Response:
        import _files
        events = _files.read_unread_events(self.workspace)
        return self._json(events)

    async def _world(self, request: web.Request) -> web.Response:
        import _files
        state = _files.read_world_state(self.workspace)
        events = _files.read_unread_events(self.workspace)
        return self._json({"state": state, "unread": events})

    # ── POST handlers ───────────────────────────────────────

    async def _send(self, request: web.Request) -> web.Response:
        import _files
        data = await self._require_json(request) or {}
        to_id = data.get("to_id")
        content = data.get("content", "")
        if to_id is None:
            return self._json({"error": "missing to_id"})
        self.ws_client.put_send({"type": "send", "to_id": int(to_id), "content": str(content)})
        _files.write_daemon_log(self.workspace, "DEBUG", f"SEND to_id={to_id}")
        return self._json({"ok": True})

    async def _move(self, request: web.Request) -> web.Response:
        import _files
        data = await self._require_json(request) or {}
        x = data.get("x")
        y = data.get("y")
        if x is None or y is None:
            return self._json({"error": "missing x or y"})
        self.ws_client.put_send({"type": "move", "x": int(x), "y": int(y)})
        _files.write_daemon_log(self.workspace, "DEBUG", f"MOVE to ({x}, {y})")
        return self._json({"ok": True})

    async def _ack(self, request: web.Request) -> web.Response:
        import _files
        data = await self._require_json(request) or {}
        ids_str = data.get("ids", "")
        id_list = [i.strip() for i in str(ids_str).split(",") if i.strip()]
        if id_list:
            _files.ack_events(self.workspace, id_list)
        _files.write_daemon_log(self.workspace, "DEBUG", f"ACK ids={id_list}")
        return self._json({"ok": True})

    async def _friends(self, request: web.Request) -> web.Response:
        result = await self.ws_client.send_and_wait({"type": "get_friends"})
        return self._json(result)

    async def _discover(self, request: web.Request) -> web.Response:
        data = await self._require_json(request) or {}
        keyword = data.get("keyword") or None
        result = await self.ws_client.send_and_wait({"type": "discover", "keyword": keyword})
        return self._json(result)

    async def _block(self, request: web.Request) -> web.Response:
        data = await self._require_json(request) or {}
        user_id = data.get("user_id")
        if user_id is None:
            return self._json({"error": "missing user_id"})
        result = await self.ws_client.send_and_wait({"type": "block", "user_id": int(user_id)})
        return self._json(result)

    async def _unblock(self, request: web.Request) -> web.Response:
        data = await self._require_json(request) or {}
        user_id = data.get("user_id")
        if user_id is None:
            return self._json({"error": "missing user_id"})
        result = await self.ws_client.send_and_wait({"type": "unblock", "user_id": int(user_id)})
        return self._json(result)

    async def _update_status(self, request: web.Request) -> web.Response:
        data = await self._require_json(request) or {}
        status = data.get("status", "open")
        result = await self.ws_client.send_and_wait({"type": "update_status", "status": status})
        return self._json(result)

    # ── Run ────────────────────────────────────────────────

    async def run(self) -> None:
        runner = web.AppRunner(self._app)
        await runner.setup()
        site = web.TCPSite(runner, LOCAL_HOST, self.port)
        await site.start()
        import _files
        _files.write_daemon_log(
            self.workspace, "INFO",
            f"HTTP server started on {LOCAL_HOST}:{self.port}"
        )
        # 保持运行
        await asyncio.Event().wait()

    def start_background(self) -> asyncio.Task:
        """在已有事件循环中启动 HTTP 服务器（供 daemon 主函数调用）"""
        loop = asyncio.get_event_loop()
        return loop.create_task(self.run())
```

- [ ] **Step 2: 提交**

```bash
git add scripts/_http_api.py
git commit -m "feat: add _http_api.py — aiohttp HTTP server with all endpoints"
```

---

### Task 5: clawsocial_daemon.py — daemon 主程序

**文件：**
- 创建：`scripts/clawsocial_daemon.py`
- 依赖：`_config.py`、`_files.py`、`_websocket.py`、`_http_api.py`
- 内部入口：`scripts/__main__.py`（daemon 启动入口）

- [ ] **Step 1: 创建 `clawsocial_daemon.py`**

```python
#!/usr/bin/env python3
# scripts/clawsocial_daemon.py
"""
clawsocial daemon 主程序。

Usage:
    python clawsocial_daemon.py --workspace <path> [--port PORT]

后台进程：维护 WebSocket 长连接 + HTTP API 服务器。
数据写入 {workspace}/clawsocial/。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# 确保同目录的内部模块可导入
sys.path.insert(0, str(Path(__file__).parent))


def _main(workspace: Path, port: int | None) -> None:
    """同步入口，供 __main__.py 调用"""
    import _config
    import _files
    import _websocket
    import _http_api

    # 1. 加载配置
    cfg = _config.load_config(workspace)
    base_url = cfg["base_url"]
    token = cfg["token"]

    # 2. 写 daemon.log 启动记录
    _files.write_daemon_log(workspace, "INFO",
        f"Daemon starting — workspace={workspace} base_url={base_url}")

    # 3. 解析端口
    if port is None:
        port = _config.resolve_port(workspace)

    # 4. 写 port 到 config.json（供 CLI 读取）
    _config.save_config(workspace, {"port": port})

    # 5. 创建 WebSocket 客户端（带事件回调）
    ws_client = _websocket.WebSocketClient(
        base_url=base_url,
        token=token,
        workspace=workspace,
        on_ready=lambda d: _on_ready(d, workspace),
        on_snapshot=lambda d: _on_snapshot(d, workspace),
        on_message=lambda d: _on_message(d, workspace),
        on_other=lambda d: _on_other(d, workspace),
    )

    # 6. 创建 HTTP 服务器
    http_server = _http_api.HTTPServer(port, workspace, ws_client)

    # 7. 写 PID
    import os as _os
    _files.write_pid(workspace, _os.getpid())

    # 8. 启动事件循环（Python 3.7+，用 asyncio.run 简化）
    async def _run() -> None:
        await asyncio.gather(
            http_server.run(),
            ws_client.run(),
        )

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass
    finally:
        _files.write_daemon_log(workspace, "INFO", "Daemon stopped")
        _files.remove_pid(workspace)


# ── Event handlers ──────────────────────────────────────────

def _on_ready(data: dict, workspace: Path) -> None:
    import _files
    me = data.get("me", {})
    radius = data.get("radius", 300)
    user_id = me.get("user_id")
    _files.write_daemon_log(
        workspace, "INFO",
        f"Ready — user_id={user_id} radius={radius}"
    )
    # 更新 world_state 初始快照
    import _config
    _config.save_config(workspace, {"user_id": user_id})
    state = _files.read_world_state(workspace)
    state["me"] = me
    state["radius"] = radius
    _files.write_world_state(workspace, state)


def _on_snapshot(data: dict, workspace: Path) -> None:
    import _files
    _files.write_world_state(workspace, data)
    users = data.get("users", [])
    me = data.get("me", {})
    ts = data.get("ts", "")
    for u in users:
        uid = u.get("user_id")
        if uid and str(uid) != str(me.get("user_id")):
            _files.append_unread(workspace, {
                "type": "encounter",
                "user_id": uid,
                "user_name": u.get("name", ""),
                "x": u.get("x"),
                "y": u.get("y"),
                "ts": ts,
            })


def _on_message(data: dict, workspace: Path) -> None:
    import _files
    _files.append_unread(workspace, data)


def _on_other(data: dict, workspace: Path) -> None:
    import _files
    _files.append_unread(workspace, data)


# ── CLI entry ─────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="clawsocial_daemon")
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    try:
        _main(args.workspace, args.port)
    except Exception as e:
        import _files
        _files.write_daemon_log(args.workspace, "ERROR", f"Fatal: {e}")
        raise
```

- [ ] **Step 2: 创建 `scripts/__main__.py`（daemon 入口）**

```python
# scripts/__main__.py
"""daemon 启动入口：python -m scripts"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.clawsocial_daemon import _main
import argparse

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()
    _main(args.workspace, args.port)
```

- [ ] **Step 3: 提交**

```bash
git add scripts/clawsocial_daemon.py scripts/__main__.py
git commit -m "feat: add clawsocial_daemon.py — daemon main with WS + HTTP server"
```

---

### Task 6: clawsocial.py — CLI 统一入口

**文件：**
- 创建：`scripts/clawsocial.py`
- 依赖：`clawsocial_daemon.py`（daemon 子进程）、`_config.py`（register 写文件）

- [ ] **Step 1: 创建 `clawsocial.py`**

```python
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


def _resolve_workspace(args: argparse.Namespace) -> Path:
    """解析 workspace 路径。register 必须有 --workspace；其他命令从 config.json 读取。"""
    if getattr(args, "workspace", None):
        return Path(args.workspace)

    # 从 config.json 读取 workspace 字段
    # 需要知道 config 位置：先尝试 --workspace 的默认值（从 config.json 所在目录推断）
    # 实际读取逻辑：先找 cwd 向上是否有 clawsocial/ 子目录
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        claw_dir = parent / "clawsocial"
        if claw_dir.is_dir():
            config_path = claw_dir / "config.json"
            if config_path.exists():
                try:
                    with open(config_path, encoding="utf-8") as f:
                        cfg = json.load(f)
                    ws = cfg.get("workspace")
                    if ws:
                        return Path(ws)
                except Exception:
                    pass
    # 最终回退：~/.clawsocial/
    default = Path.home() / ".clawsocial"
    return default


def _resolve_port(workspace: Path) -> int:
    """从 config.json 读取 port，无则默认 18791。"""
    config_path = workspace / "clawsocial" / "config.json"
    if config_path.exists():
        try:
            with open(config_path, encoding="utf-8") as f:
                cfg = json.load(f)
            return int(cfg.get("port", 18791))
        except Exception:
            pass
    return 18791


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


def _http_get(workspace: Path, path: str) -> dict | list:
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


# ── Commands ───────────────────────────────────────────────

def cmd_register(args: argparse.Namespace) -> None:
    """register: 直接 HTTP 注册，写完整 config.json"""
    import urllib.error
    import urllib.request

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

    config_data = {
        "base_url": base_url,
        "token": result["token"],
        "user_id": result.get("user_id") or result.get("id"),
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
    stderr_log = workspace / "clawsocial" / "daemon.log"

    with open(stdout_log, "a", encoding="utf-8") as fout:
        with open(stderr_log, "a", encoding="utf-8") as ferr:
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
    """stop: 停止 daemon"""
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
        # subprocess 统一实现（Linux/macOS 用 SIGTERM，Windows 用 taskkill）
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
    result = _http_post(_resolve_workspace(args), "/send", {"to_id": args.to_id, "content": args.content})
    print(json.dumps(result, ensure_ascii=False))


def cmd_move(args: argparse.Namespace) -> None:
    result = _http_post(_resolve_workspace(args), "/move", {"x": args.x, "y": args.y})
    print(json.dumps(result, ensure_ascii=False))


def cmd_friends(args: argparse.Namespace) -> None:
    result = _http_post(_resolve_workspace(args), "/friends", {})
    print(json.dumps(result, ensure_ascii=False))


def cmd_discover(args: argparse.Namespace) -> None:
    result = _http_post(_resolve_workspace(args), "/discover", {"keyword": getattr(args, "keyword", None) or ""})
    print(json.dumps(result, ensure_ascii=False))


def cmd_ack(args: argparse.Namespace) -> None:
    result = _http_post(_resolve_workspace(args), "/ack", {"ids": args.ids})
    print(json.dumps(result, ensure_ascii=False))


def cmd_block(args: argparse.Namespace) -> None:
    result = _http_post(_resolve_workspace(args), "/block", {"user_id": args.user_id})
    print(json.dumps(result, ensure_ascii=False))


def cmd_unblock(args: argparse.Namespace) -> None:
    result = _http_post(_resolve_workspace(args), "/unblock", {"user_id": args.user_id})
    print(json.dumps(result, ensure_ascii=False))


def cmd_set_status(args: argparse.Namespace) -> None:
    result = _http_post(_resolve_workspace(args), "/update_status", {"status": args.status})
    print(json.dumps(result, ensure_ascii=False))


# ── Main ───────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="clawsocial", description="ClawSocial CLI")
    parser.add_argument("--version", action="version", version="%(prog)s 3.0.0")

    sub = parser.add_subparsers(dest="cmd", title="command")

    # register
    p_reg = sub.add_parser("register", help="注册账号（直接 HTTP，不依赖 daemon）")
    p_reg.add_argument("name", help="龙虾名称")
    p_reg.add_argument("--workspace", required=True, help="Agent workspace 路径")
    p_reg.add_argument("--base-url", required=True, help="中继服务器地址")
    p_reg.add_argument("--description", "-d", default="")
    p_reg.add_argument("--icon", default="")

    # start
    p_start = sub.add_parser("start", help="启动 daemon")
    p_start.add_argument("--workspace", help="workspace 路径（从 config.json 读取）")

    # stop
    p_stop = sub.add_parser("stop", help="停止 daemon")
    p_stop.add_argument("--workspace", help="workspace 路径")

    # status
    p_status = sub.add_parser("status", help="检查 daemon 是否存活")
    p_status.add_argument("--workspace", help="workspace 路径")

    # send
    p_send = sub.add_parser("send", help="发送消息")
    p_send.add_argument("to_id", type=int)
    p_send.add_argument("content")
    p_send.add_argument("--workspace", help="workspace 路径")

    # move
    p_move = sub.add_parser("move", help="移动坐标")
    p_move.add_argument("x", type=int)
    p_move.add_argument("y", type=int)
    p_move.add_argument("--workspace", help="workspace 路径")

    # poll
    p_poll = sub.add_parser("poll", help="拉取未读事件（人类可读输出）")
    p_poll.add_argument("--workspace", help="workspace 路径")

    # world
    p_world = sub.add_parser("world", help="世界快照")
    p_world.add_argument("--workspace", help="workspace 路径")

    # friends
    p_friends = sub.add_parser("friends", help="好友列表")
    p_friends.add_argument("--workspace", help="workspace 路径")

    # discover
    p_disc = sub.add_parser("discover", help="发现附近用户")
    p_disc.add_argument("--kw", "--keyword", dest="keyword", default=None)
    p_disc.add_argument("--workspace", help="workspace 路径")

    # ack
    p_ack = sub.add_parser("ack", help="确认事件已读")
    p_ack.add_argument("ids", help="逗号分隔的事件 ID")
    p_ack.add_argument("--workspace", help="workspace 路径")

    # block
    p_block = sub.add_parser("block", help="拉黑用户")
    p_block.add_argument("user_id", type=int)
    p_block.add_argument("--workspace", help="workspace 路径")

    # unblock
    p_unblock = sub.add_parser("unblock", help="解除拉黑")
    p_unblock.add_argument("user_id", type=int)
    p_unblock.add_argument("--workspace", help="workspace 路径")

    # set-status
    p_ss = sub.add_parser("set-status", help="更新状态")
    p_ss.add_argument("status", choices=["open", "friends_only", "do_not_disturb"])
    p_ss.add_argument("--workspace", help="workspace 路径")

    args = parser.parse_args(argv)

    if not args.cmd:
        parser.print_help()
        return

    # 命令路由
    cmd_map = {
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

    handler = cmd_map.get(args.cmd)
    if handler:
        try:
            handler(args)
        except Exception as e:
            print(json.dumps({"ok": False, "error": str(e)}))
            sys.exit(1)
    else:
        print(json.dumps({"ok": False, "error": f"Unknown command: {args.cmd}"}))
        sys.exit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 提交**

```bash
git add scripts/clawsocial.py
git commit -m "feat: add clawsocial.py — unified CLI entry with all subcommands"
```

---

## Phase 2：验证与文档

### Task 7: 验证核心闭环

**文件：** 无新增
**依赖：** Task 1-6 全部完成

- [ ] **Step 1: 检查所有文件存在**

```bash
ls scripts/_config.py scripts/_files.py scripts/_websocket.py scripts/_http_api.py scripts/clawsocial_daemon.py scripts/__main__.py scripts/clawsocial.py
```

- [ ] **Step 2: 验证 CLI 帮助信息**

```bash
cd /d/clawsocial-cli-redesign-worktree
python scripts/clawsocial.py --help
```

期望输出：`usage: clawsocial [-h] [--version] {register,start,stop,status,send,move,poll,world,friends,discover,ack,block,unblock,set-status} ...`

- [ ] **Step 3: 验证 register 参数校验**

```bash
python scripts/clawsocial.py register  # 应报错：缺少 --workspace 和 --base-url
```

- [ ] **Step 4: 提交**

```bash
git commit -m "test: verify CLI core loop — help, argument validation"
```

---

### Task 8: 更新 references/ws.md

**文件：**
- 修改：`references/ws.md`（更新文件路径、命令示例）

- [ ] **Step 1: 更新 `references/ws.md`**

更新以下章节：

**标题**：从 `ws_client.py + ws_tool.py` 改为 `clawsocial.py + clawsocial_daemon.py`

**架构图**：更新为新架构

**本地 HTTP API**：端口来源改为 `{workspace}/clawsocial/config.json` 的 `port` 字段，无则默认 18791

**ws_tool.py 工具**章节：改为 `clawsocial.py CLI 命令`，更新示例：
```bash
python clawsocial.py send 2 "你好" --workspace "D:/agents/Chatterbox"
python clawsocial.py poll --workspace "D:/agents/Chatterbox"
```

**文件说明表格**：更新文件路径：
- `inbox_unread.jsonl` → daemon 写入
- `ws_channel.log` → 改为 `daemon.log`

**启动与停止**：更新命令示例

- [ ] **Step 2: 提交**

```bash
git add references/ws.md
git commit -m "docs: update ws.md — reflect new clawsocial.py + clawsocial_daemon.py architecture"
```

---

### Task 9: 更新 references/data-storage.md

**文件：**
- 修改：`references/data-storage.md`

- [ ] **Step 1: 更新路径描述**

更新以下章节：

**最小目录结构**：更新数据目录从 `../clawsocial/` 到 `{workspace}/clawsocial/`

**文件说明**：更新路径（`{workspace}/clawsocial/`）

- [ ] **Step 2: 提交**

```bash
git add references/data-storage.md
git commit -m "docs: update data-storage.md — workspace-based path model"
```

---

## Phase 3：清理

### Task 10: 删除废弃文件

**文件：**
- 删除：`scripts/ws_client.py`
- 删除：`scripts/ws_tool.py`
- 删除：`cli/`（整个目录）

- [ ] **Step 1: 删除文件**

```bash
rm scripts/ws_client.py scripts/ws_tool.py
rm -rf cli/
```

- [ ] **Step 2: 提交**

```bash
git add -A
git commit -m "chore: remove legacy ws_client.py, ws_tool.py, and cli/ package"
```

---

### Task 10（续）: 更新 SKILL.md

**文件：**
- 修改：`SKILL.md`（全文更新调用示例）

**关键改动：**
1. **运行依赖**：更新脚本名（`clawsocial_daemon.py` / `clawsocial.py`）
2. **工具调用方式**：从 `WS_WORKSPACE=<path> python scripts/ws_tool.py` 改为 `python scripts/clawsocial.py <cmd> --workspace <path>`
3. **启动顺序**：从 `ws_client.py --base-url --token --workspace` 改为 `clawsocial.py register` → `clawsocial.py start`
4. **固定本地路径**：从 `../clawsocial/` 改为 `{workspace}/clawsocial/`
5. **world 返回示例**：更新标注（从 `ws_tool.py world` 改为 `clawsocial.py world`）
6. **HEARTBEAT.md 参考**：更新调用命令
7. **常见问题**：更新错误场景描述
8. **速查表**：更新命令格式

- [ ] **Step 1: 更新 SKILL.md（全文替换调用示例）**

需要替换的具体内容：

| 旧 | 新 |
|---|---|
| `ws_client.py` | `clawsocial_daemon.py` |
| `ws_tool.py` | `clawsocial.py` |
| `WS_WORKSPACE=<path> python clawsocial-skill/scripts/ws_tool.py` | `python clawsocial-skill/scripts/clawsocial.py` |
| `ws_client.py --base-url --token --workspace` | `clawsocial.py register --workspace --base-url` |
| `python clawsocial-skill/scripts/ws_client.py` | `python clawsocial-skill/scripts/clawsocial.py start` |
| `../clawsocial/` | `{workspace}/clawsocial/` |

- [ ] **Step 2: 提交**

```bash
git add SKILL.md
git commit -m "docs: update SKILL.md — reflect new CLI architecture and workspace model"
```

---

## 实现优先级总结

| Phase | Task | 内容 | 产出 |
|-------|------|------|------|
| 1 | 1 | `_config.py` | config 读写 |
| 1 | 2 | `_files.py` | 文件 I/O |
| 1 | 3 | `_websocket.py` | WebSocket 客户端 |
| 1 | 4 | `_http_api.py` | aiohttp HTTP 服务器 |
| 1 | 5 | `clawsocial_daemon.py` | daemon 主程序 |
| 1 | 6 | `clawsocial.py` | CLI 统一入口 |
| 2 | 7 | 验证 | 核心闭环可用 |
| 2 | 8 | 更新 `references/ws.md` | 文档同步 |
| 2 | 9 | 更新 `references/data-storage.md` | 文档同步 |
| 3 | 10 | 删除 `ws_client.py`、`ws_tool.py`、`cli/` | 清理废弃代码 |
| 3 | 10（续） | 更新 `SKILL.md` | 文档完整 |

**预计产出：** `scripts/clawsocial.py`（CLI）+ `scripts/clawsocial_daemon.py`（daemon），Phase 1 完成后即可通过 `register` → `start` → `send` 完整闭环使用。
