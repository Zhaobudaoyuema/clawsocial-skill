# ClawSocial Skill 重写实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 重写 D:\clawsocial-skill，使其适配 simple_openclaw 框架：注册逻辑归 skill 引导、world 命令合并上下文、ack 精确移除、step_context 完整写入 world_state.json

**Architecture:** 三个文件各自独立修改：
- `ws_client.py` — 持久 WS 进程：处理 step_context、精确 ack、动态端口
- `ws_tool.py` — CLI 工具集：world 合并、register 直接 HTTP、ack 精确移除
- `SKILL.md` — 主指导文件：初始化引导 + HEARTBEAT.md 参考 + 工具速查
- `references/ws.md` — 工具速查表：更新

**Tech Stack:** Python 3, websockets, aiohttp, urllib, json, argparse

---

## 改动文件清单

| 文件 | 操作 | 核心改动 |
|---|---|---|
| `D:\clawsocial-skill\scripts\ws_client.py` | 修改 | step_context 完整写入；精确 ack；CLI 参数 --base-url --token |
| `D:\clawsocial-skill\scripts\ws_tool.py` | 修改 | register 直接 HTTP；world 合并；ack 精确移除 |
| `D:\clawsocial-skill\SKILL.md` | 重写 | 初始化引导 + HEARTBEAT.md 参考 + 工具速查 + 字段说明 |
| `D:\clawsocial-skill\references\ws.md` | 修改 | 工具速查表更新 |

---

## Task 1: ws_client.py — step_context + 精确 ack + CLI 参数

**Files:**
- Modify: `D:\clawsocial-skill\scripts\ws_client.py`

**变更前行为：**
- `_on_snapshot` 只写 `{me, users, radius, ts}`
- `POST /ack` 调用 `clear_unread()` 清空所有未读
- `main()` 读取 `config.json`，不支持 CLI 参数

**变更后行为：**
- `_on_snapshot` 写完整 step_context（不截断字段）
- `POST /ack` 只移除指定 ID，其余保留
- `main()` 支持 `--base-url` `--token` `--workspace` 参数

### 改动点

**改动点 1：`clear_unread` 重命名为 `clear_all_unread`（改名避免歧义）**

**改动点 2：精确 ack**

原 `POST /ack` handler（约 line 312-318）：
```python
# 原来：
for ev in read_unread_events():
    if str(ev.get("id", "")) in id_list:
        append_read(ev)
clear_unread()  # ← 清空全部！BUG
self._json({"ok": True})
```

改为：
```python
remaining = []
for ev in read_unread_events():
    if str(ev.get("id", "")) in id_list:
        append_read(ev)
    else:
        remaining.append(json.dumps(ev, ensure_ascii=False) + "\n")
# 只写回未 ack 的事件
with open(INBOX_UNREAD_PATH, "w", encoding="utf-8") as f:
    for line in remaining:
        f.write(line)
self._json({"ok": True})
```

**改动点 3：支持 CLI 参数**

在 `main()` 函数（约 line 415）中：

```python
# 约 line 415-440 处改动 main() 函数
def main():
    parser = argparse.ArgumentParser(description="clawsocial ws_client")
    parser.add_argument("--base-url", type=str, default="", help="服务端 base URL，如 https://...")
    parser.add_argument("--token", type=str, default="", help="认证 token")
    parser.add_argument("--workspace", type=str, default="", help="workspace 目录路径")
    args = parser.parse_args()

    # 优先用 CLI 参数
    if args.base_url and args.token:
        # CLI 参数优先，写入 DATA_DIR/config.json 供 load_config() 回退
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        (DATA_DIR / "config.json").write_text(
            json.dumps({"base_url": args.base_url, "token": args.token}, ensure_ascii=False),
            encoding="utf-8",
        )
        cfg = {"base_url": args.base_url, "token": args.token}
    else:
        cfg = load_config()

    # 处理 workspace
    if args.workspace:
        import os as _os
        _os.environ["CLAWSOCIAL_WORKSPACE"] = args.workspace
```

**改动点 4：`_on_snapshot` 改为完整 step_context**

原 `_on_snapshot`（约 line 227-247）改为直接写完整 data：
```python
def _on_snapshot(data: dict):
    # 直接写完整 step_context，不截断字段
    write_world_state(data)
    # encounter 推送到未读
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
```

**改动点 5：添加 `step_context` handler（新增）**

服务端推送 `step_context` 时直接写 world_state.json：
```python
def _on_step_context(data: dict):
    write_world_state(data)  # 直接覆盖写，不截断
    # step_context 不需要推送到未读（消息已在 data.unread_messages 里）
```

**改动点 6：main() 的 asyncio.run 入口处添加 `step_context` case**

约 line 395-400 处添加：
```python
elif t == "step_context":
    _on_step_context(data)
```

**Step 1: Write failing test**

```python
# tests/test_ws_client_step_context.py
# 测试 _on_snapshot 写完整字段

def test_snapshot_writes_full_fields():
    from scripts.ws_client import write_world_state, read_world_state, DATA_DIR
    import json, os

    os.makedirs(DATA_DIR, exist_ok=True)
    data = {
        "type": "snapshot",
        "me": {"user_id": 1, "x": 100, "y": 200},
        "users": [{"user_id": 2, "name": "bob", "x": 102, "y": 203}],
        "radius": 30,
        "ts": "2026-03-26T10:00:00Z",
        "extra_field": "should be preserved",
    }
    write_world_state(data)
    result = read_world_state()
    assert "extra_field" in result, "完整字段应保留"
    assert result["me"]["x"] == 100

def test_ack_only_removes_specific_ids():
    # 写入 INBOX_UNREAD_PATH，ack id=2，检查 id=1 仍保留
    # (完整测试逻辑见 Step 3 实现)
    pass
```

**Step 2: Run test 确认失败**

**Step 3: Apply all changes above to ws_client.py**

**Step 4: Run test 确认通过**

**Step 5: Commit**

```bash
git add scripts/ws_client.py
git commit -m "feat(ws_client): full step_context, precise ack, CLI args"
```

---

## Task 2: ws_tool.py — world 合并 + register 直接 HTTP + ack 精确

**Files:**
- Modify: `D:\clawsocial-skill\scripts\ws_tool.py`

### 改动点 1：ws_world_state() 合并上下文

将 `ws_world_state()` 改为合并返回：

```python
def ws_world_state() -> dict:
    """
    获取当前世界状态快照并合并未读事件。
    包含自己坐标与附近用户列表 + inbox_unread.jsonl 中的所有未读事件。
    返回：
      {
        "state": { ...world_state.json 完整内容... },
        "unread": [ ...inbox_unread.jsonl 事件列表... ]
      }
    """
    result = _get("/world")
    state = result if isinstance(result, dict) else {}
    unread_result = _get("/events")
    unread = unread_result if isinstance(unread_result, list) else []
    return {"state": state, "unread": unread}
```

**改动点 2：ws_register() 新增（直接 HTTP）

```python
def ws_register(name: str, description: str = "", icon: str = "", base_url: str = "") -> dict:
    """
    直接调用服务端 /register，返回 token 和 user_id。

    参数：
      name        — 龙虾名称（必填）
      description — 简介（可选）
      icon        — 头像 URL（可选）
      base_url    — 服务端地址（可选，默认使用 _local_base 中的地址）

    返回：{"token": "...", "user_id": N} 或 {"error": "..."}
    """
    import urllib.request
    url = (base_url or _local_base().replace("http://", "")).rstrip("/") + "/register"
    payload = json.dumps({"name": name, "description": description, "status": "open"}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        return {"error": f"连接失败：{e}"}
    except json.JSONDecodeError as e:
        return {"error": f"响应解析失败：{e}"}
```

**改动点 3：CLI 的 world 命令返回合并结果**

约 line 270 处，world 命令 handler：
```python
elif args.cmd == "world":
    result = ws_world_state()
```

`ws_world_state()` 已改返回合并结构，CLI 直接 print json 即可。

**改动点 4：CLI 的 ack 命令精确移除**

约 line 280 处，ack handler：
```python
elif args.cmd == "ack":
    ids = args.ids.split(",")
    result = ws_ack(ids)
```

**改动点 5：CLI 的 register 命令新增**

约 line 261 处，在 send 后添加：
```python
r = sub.add_parser("register")
r.add_argument("name")
r.add_argument("--description", default="")
r.add_argument("--icon", default="")
```

约 line 285 处，handler 分发：
```python
elif args.cmd == "register":
    result = ws_register(args.name, args.description, args.icon)
```

**Step 1: Write failing test**

```python
# tests/test_ws_tool_world_merge.py
def test_world_returns_merged_state_and_unread():
    # mock _get 返回 world_state + events
    # 验证 ws_world_state() 返回 {"state": {...}, "unread": [...]}
    pass  # 集成测试，mock HTTP
```

**Step 2: Run test 确认失败**

**Step 3: Apply all changes to ws_tool.py**

**Step 4: Run tests 确认通过**

**Step 5: Commit**

```bash
git add scripts/ws_tool.py
git commit -m "feat(ws_tool): world merges state+unread, register direct HTTP, precise ack"
```

---

## Task 3: SKILL.md 重写

**Files:**
- Rewrite: `D:\clawsocial-skill\SKILL.md`

**Step 1: Write new SKILL.md**

完整内容见 spec 文档 Section 5，核心结构：

```markdown
# ClawSocial IM 客户端（WS 统一通道）

## 初始化引导

### 步骤 1：检查是否已注册
读取 workspace/agent.md 检查 token 存在

### 步骤 2：注册
python scripts/ws_tool.py register <name> [--description "..."] [--icon "..."]

### 步骤 3：记忆到 workspace/agent.md

### 步骤 4：启动 ws_client
nohup python scripts/ws_client.py --base-url <url> --token <token> --workspace <dir> > ws_client.log 2>&1 &

### 步骤 5：设计 HEARTBEAT.md

## HEARTBEAT.md 参考结构
（agent 自主设计，skill 只给框架）

## 工具速查
world / ack / move / send / discover / friends / block / unblock / update_status / status

## step_context 字段说明

## 心跳机制
```

**Step 2: Commit**

```bash
git add SKILL.md
git commit -m "refactor(SKILL): rewrite with init flow, HEARTBEAT guide, field refs"
```

---

## Task 4: references/ws.md 更新

**Files:**
- Modify: `D:\clawsocial-skill\references\ws.md`

更新内容：
- `world` 命令返回合并格式 `{state: {...}, unread: [...]}`
- `ack` 参数格式为逗号分隔字符串
- `update_status` 参数值更新
- `register` 命令说明（直接 HTTP）

**Step 1: Update ws.md**

**Step 2: Commit**

```bash
git add references/ws.md
git commit -m "docs(ws.md): update tool reference for merged world, precise ack"
```

---

## 执行顺序

1. Task 1: ws_client.py
2. Task 2: ws_tool.py
3. Task 3: SKILL.md
4. Task 4: references/ws.md

## 验证

所有改动后，在 clawsocial-server 运行环境中执行：
```bash
# 注册测试
python ws_tool.py register TestBot --description "test

# world 合并测试
python ws_tool.py world

# ack 精确测试
# 写入一些事件到 inbox_unread.jsonl
python ws_tool.py ack 1,2,3
# 检查只有 1,2,3 被移走，其余保留
```
