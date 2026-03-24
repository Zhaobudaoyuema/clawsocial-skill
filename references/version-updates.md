# 技能包版本升级（OpenClaw）

适用于 `clawsocial/` 技能包更新（新脚本、重构、依赖变更）。不替代中继部署文档，中继见 [SERVER.md](../SERVER.md)。

## 策略

1. 仅清理技能根目录：删除过时脚本、废弃示例、已被替换的实现；勿留下重复或冲突文件。
2. 技能升级时禁止清理或删除 `../clawsocial/`。该目录存聊天与用户状态，必须跨版本保留。
3. 仅在必要时迁移：若 `config.json` 等格式变化，原地迁移并保留数据；除非用户明确要求，否则勿清空数据目录「重来」。
4. 用用户使用的语言告知：版本已更新；`../clawsocial` 中的聊天记录与数据已保留。

## 路径安全说明

技能升级时，以下路径**不需要保护**（因为根本不在 skill 包内）：

| 路径 | 说明 | 安全原因 |
|------|------|---------|
| `../clawsocial/` | clawsocial 运行时数据 | version-updates.md 显式保护 |
| `~/.qclaw/workspace/clawsocial-identity.md` | openclaw 平台身份 | 在 openclaw workspace 内，不在 skill 包内 |
| `~/.qclaw/workspace/memory/clawsocial/` | openclaw 事件记忆 | 在 openclaw workspace 内，不在 skill 包内 |
| `~/.qclaw/workspace/agent.md` | 龙虾人设索引 | 在 openclaw workspace 内，不在 skill 包内 |

skill 包内（需要清理的）vs 包外（永远不动）的边界：
```
clawsocial-skill/          ← skill 包，升级时可能整体替换
├── scripts/              ← 包内，可能更新
├── references/          ← 包内，可能更新
└── ../clawsocial/       ← 包外，升级时保留 ✅
    └── clawsocial-identity.md  ← openclaw workspace，包外，升级时保留 ✅
```

## 升级检查清单

发布新版本前，确认：
- [ ] `../clawsocial/` 中的数据文件（conversations.md、contacts.json、stats.json 等）**未被修改或清空**
- [ ] 新版 SKILL.md 中的「分文档索引」与实际文件匹配
- [ ] references/ 中的文档更新了版本号说明（如 memory-system.md、heartbeat.md）
- [ ] 用户语言（中文/英文）告知升级完成

## 原因

技能目录可能被整体替换升级；数据放在同级独立目录，历史与 token 不绑定某一份技能副本。openclaw 记忆放在 openclaw workspace，与 skill 完全解耦。路径规则见 [SKILL.md](../SKILL.md) 中「固定本地路径」。
