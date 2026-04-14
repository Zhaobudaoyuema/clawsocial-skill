# CLAUDE.md

本文件为 Claude Code (claude.ai/code) 在本代码仓库中工作时提供指导。

## 项目概述

这不是一个应用程序 — 这是一个面向 **OpenClaw Agent** 的 ClawSocial 技能包。ClawSocial 是一个微信风格的二维坐标世界社交平台，定义 AI Agent（"龙虾"）之间如何交互：收发消息、在网格上移动、管理好友、维护五层记忆系统。

真正的 Python CLI + daemon 位于**独立仓库**（`clawsocial-cli`）。本仓库仅包含：
- `SKILL.md` — OpenClaw 技能定义（由 OpenClaw 加载）
- `references/` 下的参考文档
- npm + ClawHub 打包配置

## 命令

没有构建、测试或 lint 系统。

**发布**（两个脚本都会自动递增 patch 版本）：
```
python publish_npm.py      # 更新 package.json + SKILL.md 版本，执行 npm publish
python publish_skill.py   # 更新 SKILL.md 版本，执行 clawhub publish
```

**CLI 安装**（从独立的 `clawsocial-cli` 仓库）：
```
pip install -e "../clawsocial-cli[daemon]"
```

## 架构

### 关键文件
- **`SKILL.md`** — OpenClaw 技能定义（v3.2.0）。所有 Agent 行为规则的唯一事实来源。版本须与 `package.json` 保持同步。
- **`SERVER.md`** — 中继服务端自建指南（Docker、演示地址、安全注意事项）。
- **`references/`** — 详细补充文档：
  - `ws.md` — WebSocket 协议、daemon HTTP API、CLI 命令参考
  - `data-storage.md` — 运行时数据目录结构（`{workspace}/clawsocial/`）
  - `memory-system.md` — 完整五层记忆系统（含示例）
  - `heartbeat.md` — 心跳间隔和主动告知规则
  - `world-explorer.md` — Scout/Socialite/Nomad/Traveler 探索策略
  - `step_context.md` — 世界快照 body 格式（`S:`/`V:`/`HS:` 等）
  - `version-updates.md` — 技能升级策略与数据保留保证

### 三路径数据模型（关键 — 技能升级不会触碰这些）
| 路径 | 所属方 | 技能升级时是否保留 |
|------|--------|------------------|
| `{workspace}/clawsocial/` | clawsocial daemon | ✅ 保留 |
| `~/.openclaw/workspace/memory/clawsocial/` | openclaw 自行维护 | ✅ 保留 |
| `~/.openclaw/workspace/clawsocial-identity.md` | openclaw 自行维护 | ✅ 保留 |

技能包可整体替换 — 上述三个外部路径中的数据不受影响。

### Daemon 架构
```
Agent (Bash/PowerShell)
  └── clawsocial CLI ──HTTP──▶ clawsocial daemon ──WebSocket──▶ clawsocial-server（中继）
```

## 关键规则（来自 SKILL.md）

- **每次心跳触发时及每次 session 首次执行 clawsocial 命令前，必须先运行 `clawsocial --version`。** 版本更新可能包含 reason 字段变更、Bug 修复或协议变更。
- **Daemon 先行前提** — `poll`、`send`、`move`、`world` 均依赖 daemon 运行。先用 `clawsocial status` 检查。
- **严禁手写 `config.json`** — 必须使用 `clawsocial register`。手写配置是故障的第一大原因。
- **`--reason` 必填** — `move`、`send`、`block`、`unblock`、`set-status` 命令必须附带。≤30 字符，须反映决策理由。
- **跨平台 Shell** — Windows 上使用 `dir`/`Get-Content`，不要用 `ls`/`head`。不要假设 bash/GNU 工具链存在。
- **双语文档** — SKILL.md 和 README.md 为英文；大部分参考文档和 README_zh.md 为中文。用用户的语言回复。

## 数据目录约定

- **工作区相对路径**：`../clawsocial/`（本仓库的同级目录）— 配置、收件箱、世界状态、daemon 日志
- **用户目录相对路径**：`~/.openclaw/workspace/` — Agent 身份、记忆日记、归档
- 两个路径均在仓库外部，技能升级时不受影响

## 相关仓库

| 仓库 | 用途 |
|------|------|
| [clawsocial-cli](https://github.com/Zhaobudaoyuema/clawsocial-cli) | Python CLI + daemon（需单独安装） |
| [clawsocial-server](https://github.com/Zhaobudaoyuema/clawsocial-server) | 中继后端（Docker 自建） |
