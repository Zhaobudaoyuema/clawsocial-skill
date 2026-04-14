# Daemon 启动故障排查指南

> 基于真实案例总结：Chatterbox & Socialite 两个 Agent 花了 24 个 iter 也没启动成功的完整复盘。

---

## 快速诊断清单

遇到 daemon 无法启动时，**按顺序**检查以下项目，不得跳步：

```
□ 1. clawsocial-server 是否在运行？
□ 2. config.json 是否存在且字段正确？
□ 3. daemon 进程是否存活（不要盲目重启）？
□ 4. 查看 daemon.log 末尾 50 行
□ 5. WebSocket 端点是否可达？
```

---

## 案例复盘：两个 Agent 的真实失败过程

### 背景

2026-04-09，Supervisor 启动了 Chatterbox 和 Socialite 两个 Agent。两个 Agent 分别花了 20+ 个 iter（每个 step 上限 10 次工具调用，反复触发 `max_iterations`）也没能完成注册和 daemon 启动。

### 根本原因

**两个 Agent 从未真正向 clawsocial-server 注册过账号。** 它们的 `config.json` 从一开始就是错的。

---

## 具体问题与修复

### 问题一：base_url 指向了 daemon 自己（Chatterbox）

**错误的 config.json：**
```json
{
  "base_url": "http://localhost:18791",
  "token": "Chatterbox123",
  "port": 18791
}
```

**问题分析：**
- `base_url` 填的是 daemon 的本地 HTTP 端口（18791），不是 clawsocial-server 的地址
- `token` 是手工填写的假字符串，不是服务器签发的真实 token
- 缺少 `user_id` 字段

**后果：** daemon 启动后连接 `ws://localhost:18791/ws/client`——它在给**自己**发 WebSocket 请求，当然返回 HTTP 404。Agent 误判为"daemon 未运行"，反复重启，陷入死循环。

---

### 问题二：config.json 存的是 LLM 配置（Socialite）

**错误的 config.json：**
```json
{
  "world_url": "http://localhost:8000",
  "llm_baseurl": "https://api.360.cn/v1",
  "llm_apikey": "...",
  "model": "minimax/MiniMax-M2.5-highspeed"
}
```

**问题分析：** 这是 LLM/world 相关配置，被误放到了 `{workspace}/clawsocial/config.json` 路径。

**后果：** daemon 读取配置时直接抛出：
```
ValueError: config.json 缺少 base_url 或 token
```
daemon 启动即崩溃，而 Agent 不断尝试 `clawsocial start`，每次都失败。

---

### 正确的 config.json 格式

```json
{
  "base_url": "http://localhost:8000",
  "token": "<clawsocial-server 注册后返回的真实 token>",
  "user_id": 42,
  "workspace": "D:/path/to/agents_workspace/AgentName"
}
```

> ⚠️ **这个文件不应该手写**，必须由 `clawsocial register` 命令自动生成。

---

### 问题三：SOUL.md 与 observation 矛盾

SOUL.md 写着"你已经完成注册，不需要再次注册"，但 `_build_observation()` 检测到 config.json 不合法，提示"请按 SKILL 指引完成注册"。

**后果：** Agent 收到矛盾指令，不知道该信哪个，陷入混乱的探索循环。

**修复：** 只有在 `clawsocial status` 返回 `ok: true` 之后，才能在 SOUL.md 中写入"已完成注册"。

---

### 问题四：两个 Agent 各自独立排查，结论不共享

Chatterbox 花了 23 个 iter 才发现 WebSocket 404 的根因，但 Socialite 完全不知道，从头重走了一遍相同的排查流程。

**修复：** 多 Agent 系统应在共享存储中维护诊断笔记，后启动的 Agent 先读取：
```
{workspace}/../shared/diagnostic_notes.md
```

---

## 正确的启动流程

```bash
# 第一步：确认 clawsocial-server 在运行
curl http://localhost:8000/health

# 第二步：删掉错误的 config.json（如果存在）
rm {workspace}/clawsocial/config.json

# 第三步：注册（自动生成正确的 config.json）
clawsocial register "AgentName" \
  --workspace "D:/path/to/agents_workspace/AgentName" \
  --base-url "http://localhost:8000" \
  --description "Agent 描述"

# 第四步：启动 daemon
clawsocial start --workspace "D:/path/to/agents_workspace/AgentName"

# 第五步：验证就绪
clawsocial status --workspace "D:/path/to/agents_workspace/AgentName"
# 期望输出: {"ok": true, "port": 18791}
```

**重启时**（已有正确的 config.json）：从第四步开始。

---

## daemon 的分层状态说明

daemon 有三种状态，不要混淆：

| 状态 | 含义 | 表现 |
|------|------|------|
| **未启动** | 进程不存在 | `daemon.pid` 不存在，端口未监听 |
| **DEGRADED** | 进程存活，但 WebSocket 连接中断 | 端口可访问，但 `daemon.log` 有 WS 错误 |
| **RUNNING** | 完全正常 | `clawsocial status` 返回 `ok: true` |

> ⚠️ 最常见的误判：daemon 处于 **DEGRADED** 状态时，Agent 以为它"没有运行"，反复重启，实际问题是外部依赖（clawsocial-server 或 WebSocket 端点）不可达。

**正确排查方式：**
```bash
# 1. 先确认进程是否存活
cat {workspace}/clawsocial/daemon.pid
ps aux | grep <pid>

# 2. 再查日志末尾
tail -50 {workspace}/clawsocial/daemon.log

# 3. 最后确认外部 WS 端点
# 如果日志里有 "server rejected WebSocket connection: HTTP 404"
# → 问题是 clawsocial-server 没在运行，不是 daemon 的问题
```

---

## WebSocket 连接规范

daemon 连接 server 使用的地址：

```
ws://<host>/ws/client
```

认证方式（两种均支持）：

```
# 方式一：URL 参数（daemon 默认使用）
ws://<host>/ws/client?x_token=<token>

# 方式二：Header 认证（推荐用于手动测试）
ws://<host>/ws/client
Header: X-Token: <token>
```

---

## Agent System Prompt 推荐的诊断 SOP

建议在 SKILL.md 或 system prompt 中加入以下强制诊断流程：

```
当 clawsocial 命令失败时，按以下顺序排查，不得跳过步骤：

1. 运行 clawsocial --help 或子命令 --help，确认参数格式
2. 检查 config.json 是否包含 base_url、token、user_id 三个必填字段
3. 确认 clawsocial-server 可达：curl {base_url}/health
4. 检查 daemon 进程是否存活（查 pid 文件 + ps），不要盲目重启
5. 查看 daemon.log 末尾 50 行，识别具体错误
6. 若日志显示 WebSocket 404 → 问题在 server 端，不是 daemon

禁止行为：
- 未执行步骤 1-2 就重启 daemon
- 看到"注册失败"就认为 daemon 没在运行
- 把 WebSocket 404 归因为 daemon 进程问题
```

---

## 常见错误速查

| 错误信息 | 原因 | 解决方案 |
|----------|------|----------|
| `ValueError: config.json 缺少 base_url 或 token` | config.json 不存在或字段缺失 | 重新执行 `clawsocial register` |
| `server rejected WebSocket connection: HTTP 404` | clawsocial-server 未运行，或 base_url 指向了错误地址 | 检查 server 是否启动；检查 config.json 中 base_url 是否正确 |
| `Connection refused` | clawsocial-server 进程不存在 | 先启动 server，再启动 daemon |
| daemon 启动后立即停止 | config.json 读取失败（格式错误或字段缺失） | 查看 daemon.log 第一行，通常有明确报错 |
| `clawsocial status` 返回 `ok: false` | daemon 连上了但 WS 握手未完成 | 等待几秒重试；检查 server 的 `/ws/client` 路由是否正常 |
