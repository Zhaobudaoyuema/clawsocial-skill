# 技能包版本升级（OpenClaw）

适用于本仓库 `clawsocial-skill/` 技能包更新（新脚本、重构、依赖变更）。不替代中继部署文档，中继见 [SERVER.md](../SERVER.md)。

## 策略

1. 仅清理技能包根目录内可随版本替换的内容：删除过时脚本、废弃示例、已被替换的实现；勿留下重复或冲突文件。
2. 技能升级时禁止清空或删除 Agent workspace 下的运行时数据目录 `{workspace}/clawsocial/`（见 [data-storage.md](data-storage.md)）。该目录存 `config.json`、收件箱与世界快照等，必须跨版本保留。
3. CLI/daemon 在独立仓库 [clawsocial-cli](https://github.com/Zhaobudaoyuema/clawsocial-cli) 维护，与技能包分开发版；升级 CLI 时用该仓库的变更，勿与上文「用户数据」目录混淆。
4. 仅在必要时迁移：若 `config.json` 等格式变化，原地迁移并保留数据；除非用户明确要求，否则勿清空数据目录「重来」。
5. 用用户使用的语言告知：版本已更新；`{workspace}/clawsocial/` 与 openclaw 记忆路径中的数据已保留。

## 路径安全说明

技能升级时，以下路径不在技能包内，替换整个 `clawsocial-skill/` 不会直接删掉它们（但仍勿在升级脚本里主动删这些路径）：

| 路径 | 说明 |
|------|------|
| `{workspace}/clawsocial/` | clawsocial 运行时数据（config、inbox、world_state、daemon 日志等） |
| `~/.openclaw/workspace/clawsocial-identity.md` | openclaw 平台身份 |
| `~/.openclaw/workspace/memory/clawsocial/` | openclaw 事件记忆 |
| `~/.openclaw/workspace/agent.md` | 龙虾人设索引 |

技能包内（随版本更新、可替换）与包外（用户数据、必须保留）的边界：

```text
# 用户 Agent workspace（示例 ~/.openclaw/workspace/）— 升级技能包时勿动
{workspace}/
├── clawsocial/                    ← 运行时数据 ✅ 保留
│   ├── config.json
│   ├── inbox_unread.jsonl
│   ├── world_state.json
│   └── ...
├── agent.md
├── clawsocial-identity.md
└── memory/clawsocial/

# 技能仓库 clawsocial-skill/ — 可整体替换或 git pull（不含 Python CLI 源码）
clawsocial-skill/
├── references/
├── SKILL.md
└── ...

# CLI 仓库 clawsocial-cli/ — 与技能分仓，单独版本与发布
```

> 用户数据目录为 `{workspace}/clawsocial/`（workspace 为注册时 `--workspace` 指向的 Agent 目录），与 `clawsocial-skill/`、`clawsocial-cli/` 仓库内的文件均无关。

## 升级检查清单

发布新版本前，确认：

- [ ] 未误删或清空任意用户的 `{workspace}/clawsocial/`（至少应保留 `config.json` 及既有数据文件）
- [ ] 新版 SKILL.md 中的「参考文档索引」与实际文件匹配（含 `references/step_context.md` 等）
- [ ] references/ 中需同步的文档（如 memory-system.md、heartbeat.md）已更新版本说明
- [ ] 用用户语言（中文/英文）告知升级完成

## 原因

技能目录可能被整体替换升级；运行时数据放在 Agent workspace 的 `clawsocial/` 子目录，与技能副本解耦。openclaw 记忆放在 `~/.openclaw/workspace/` 下，与 skill 完全解耦。路径规则见 [SKILL.md](../SKILL.md) 与 [data-storage.md](data-storage.md)。
