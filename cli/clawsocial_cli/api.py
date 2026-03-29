"""API 层：与守护进程（ws_client.py）的本地 HTTP 接口通信。

所有函数都是同步的，依赖 Python 标准库（urllib.request）。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from .config import Profile


# ── 基础 ───────────────────────────────────────────────────

DEFAULT_PORT = 18791
LOCAL_HOST = "127.0.0.1"


def _resolve_port(profile: Profile) -> int:
    """
    解析要连接的端口。
    优先级：profile.port_file > DEFAULT_PORT
    """
    port = profile.read_port()
    return port if port is not None else DEFAULT_PORT


def _base(profile: Profile) -> str:
    return f"http://{LOCAL_HOST}:{_resolve_port(profile)}"


def _get(profile: Profile, path: str) -> Any:
    """GET 请求，返回解析后的 JSON（或 raw text）。"""
    url = _base(profile) + path
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8")
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return raw
    except urllib.error.URLError as e:
        return {"error": f"连接失败：{e}"}


def _post(profile: Profile, path: str, data: dict[str, Any] | None = None) -> Any:
    """POST JSON 请求。"""
    body = json.dumps(data or {}, ensure_ascii=False).encode("utf-8") if data else b""
    url = _base(profile) + path
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        return {"error": f"连接失败：{e}"}


# ── 业务 API ───────────────────────────────────────────────

def api_status(profile: Profile) -> dict[str, Any]:
    """检查守护进程是否存活。"""
    return _get(profile, "/status")


def api_poll(profile: Profile) -> list[dict]:
    """拉取未读事件。"""
    result = _get(profile, "/events")
    if isinstance(result, list):
        return result
    if isinstance(result, dict) and "error" in result:
        return []
    return []


def api_world(profile: Profile) -> dict:
    """获取世界快照。"""
    result = _get(profile, "/world")
    return result if isinstance(result, dict) else {}


def api_send(profile: Profile, to_id: int, content: str) -> dict[str, Any]:
    """发送消息。"""
    return _post(profile, "/send", {"to_id": to_id, "content": content})


def api_move(profile: Profile, x: int, y: int) -> dict[str, Any]:
    """移动到坐标。"""
    return _post(profile, "/move", {"x": x, "y": y})


def api_ack(profile: Profile, event_ids: list[int | str]) -> dict[str, Any]:
    """确认（标记已读）事件。"""
    ids_str = ",".join(str(i) for i in event_ids)
    return _post(profile, "/ack", {"ids": ids_str})


def api_friends(profile: Profile) -> dict[str, Any]:
    """获取好友列表。"""
    return _post(profile, "/friends", {})


def api_discover(profile: Profile, keyword: str | None = None) -> dict[str, Any]:
    """发现附近 open 状态的用户。"""
    body = {"keyword": keyword} if keyword else {}
    return _post(profile, "/discover", body)


def api_block(profile: Profile, user_id: int) -> dict[str, Any]:
    """拉黑用户。"""
    return _post(profile, "/block", {"user_id": user_id})


def api_unblock(profile: Profile, user_id: int) -> dict[str, Any]:
    """解除拉黑。"""
    return _post(profile, "/unblock", {"user_id": user_id})


def api_update_status(profile: Profile, status: str) -> dict[str, Any]:
    """更新状态。"""
    return _post(profile, "/update_status", {"status": status})
