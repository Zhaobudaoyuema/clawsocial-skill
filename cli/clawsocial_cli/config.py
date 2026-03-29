"""配置管理：读写 ~/.clawsocial/<profile>/config.json。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _root() -> Path:
    root = os.environ.get("CLAWSOCIAL_HOME", "")
    return Path(root).expanduser() if root else Path.home() / ".clawsocial"


@dataclass
class Profile:
    name: str = "default"
    root: Path = field(default_factory=_root)

    @property
    def path(self) -> Path:
        p = self.root / self.name
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def config_path(self) -> Path:
        return self.path / "config.json"

    @property
    def port_file(self) -> Path:
        return self.path / "port.txt"

    def load_config(self) -> dict[str, Any]:
        if not self.config_path.exists():
            return {}
        try:
            return json.loads(self.config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def save_config(self, cfg: dict[str, Any]) -> None:
        self.config_path.write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def read_port(self) -> int | None:
        if not self.port_file.exists():
            return None
        try:
            return int(self.port_file.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            return None

    @property
    def base_url(self) -> str:
        return self.load_config().get("base_url", "")

    @property
    def token(self) -> str:
        return self.load_config().get("token", "")

    @property
    def has_config(self) -> bool:
        cfg = self.load_config()
        return bool(cfg.get("base_url") and cfg.get("token"))

    def init(self, name: str, base_url: str, token: str = "") -> dict[str, Any]:
        """初始化配置文件。token 为空则尝试通过 /register 注册。"""
        if token:
            cfg = {"name": name, "base_url": base_url, "token": token}
            self.save_config(cfg)
            return {"ok": True, "token": token}

        import urllib.error, urllib.request
        url = base_url.rstrip("/") + "/register"
        payload = json.dumps({
            "name": name, "description": "", "icon": "", "status": "open",
        }, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                if "token" in result:
                    self.save_config({"name": name, "base_url": base_url, "token": result["token"]})
                return result
        except urllib.error.URLError as e:
            return {"error": f"注册失败：{e}"}

    def list_all(self) -> list[str]:
        if not self.root.exists():
            return []
        return sorted(p.name for p in self.root.iterdir() if p.is_dir() and not p.name.startswith("."))


# ── 全局 ────────────────────────────────────────────────────

_current: Profile | None = None


def get_profile(name: str = "default") -> Profile:
    global _current
    if _current is None or _current.name != name:
        _current = Profile(name=name)
    return _current
