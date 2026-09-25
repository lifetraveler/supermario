# session-bootstrap 技能规格

日期：2026-09-26
状态：已获用户批准的设计，待实现

## 目的

进入任何项目开始开发前，由本技能驱动一个统一的"项目进入"流程：询问新/老项目、展示全部个人技能清单、由用户决定本次加载哪些，并把硬过滤配置写入项目，使新 session 只携带选中的技能。多余的技能元数据不再进入新 session 的系统提示。

## 用户决策记录

1. 机制：两者结合——配置硬过滤为主 + 会话内软加载为不重启的替代路径。
2. 记忆：按项目记忆选择（项目画像），存于技能目录 `profiles/` 下。
3. 范围：仅个人技能。排除 arkcli-* 托管技能与本技能自身，它们不进入 includeSkills。
4. 新老项目：两者都全量询问；唯一差别是老项目用画像预填上次选择。

## 已验证的环境事实

- Pi（omp）技能在启动时全量发现；系统提示只注入 name + description 元数据；内容按需 `read skill://<name>` 加载。
- 过滤配置键（`omp config list` 确认存在）：`skills.includeSkills`（glob allowlist，空=全部包含）、`skills.ignoredSkills`（glob 排除）、`skills.enabled`。
- 项目级配置 `<repo>/.omp/config.yml` 优先于全局；数组整体替换不追加。
- 配置改动在 session 启动时读取 → 必须新开 session 才生效。
- 技能发现布局：`<skills-root>/<skill-name>/SKILL.md`，一层目录，不可嵌套。
- 个人技能源：`~/.omp/agent/skills/`（Pi 全局）与 `~/.claude/skills/`（Claude 全局，Claude Code 会话）。
- arkcli 托管技能判定：`~/.claude/skills/.arkcli-managed-skills.json` 的 `skills` 键列出的名字。
- Claude Code 无 includeSkills 等价机制 → 该环境只走软加载路径。
- `/skill:<name>` 斜杠命令可手动触发技能（`skills.enableSkillCommands` 默认 true）。

## 技能定义

- 名称：`session-bootstrap`
- 位置：`~/.omp/agent/skills/session-bootstrap/SKILL.md`（主）；同步副本到 `~/.claude/skills/session-bootstrap/SKILL.md`（供 Claude Code）。
- frontmatter：`name: session-bootstrap`；`description` 以"Use when..."开头，第三人称，只写触发条件（进入新项目/开始开发前/切换项目加载哪些 skill），不总结工作流。
- 目标字数：<500 词正文（writing-skills 对频繁加载技能的要求），流程图一个（何时走哪条路径的决策）。

## 工作流（技能正文规定的行为）

1. 判定 harness：若系统提示含 `skill://` 协议与 `includeSkills` 文档语境（Pi），双路径可用；否则（Claude Code）仅软加载路径。
2. 询问用户：进入的是新项目还是老项目？（ask 工具，两选项）
3. 扫描个人技能：
   - 列出 `~/.omp/agent/skills/*/SKILL.md` 与 `~/.claude/skills/*/SKILL.md`（按当前 harness 的源）。
   - 排除：`.arkcli-managed-skills.json` 中列出的名字、`session-bootstrap` 自身、无 SKILL.md 的目录。
   - 每个技能取 frontmatter 的 name + description 首句，构成一行式清单。
4. 展示清单，用 ask（multi: true）让用户勾选要加载的技能。
   - 老项目：先读 `profiles/<项目名>.yml`，把已存技能在清单中标注"（上次选择）"，仍全量展示。
   - 全不选：提示最少选 1 个，或明确选择"跳过硬过滤"。
5. 保存画像：`~/.omp/agent/skills/session-bootstrap/profiles/<项目名>.yml`，内容 `project_path` + `skills` 列表。项目名取 cwd 目录名；与已有画像的 project_path 冲突时追加短哈希后缀。
6. 硬过滤（仅 Pi 且用户未跳过）：写入 `<cwd>/.omp/config.yml` 的 `skills.includeSkills: [选中的名字...]`。文件已存在则只合并该键（保留其他键）；不存在则创建（含 `.omp/` 目录）。
7. 软加载（当前 session 立即生效）：对本 session 中选中的每个技能执行 `read skill://<name>`，其余技能不读。
8. 收尾提示：告知用户新开 session 后硬过滤生效；`/skill:session-bootstrap` 可随时重新进入本流程调整。

## 错误与边界

- `includeSkills` 写入后空数组等价于"包含全部" → 禁止写空数组；全不选走"跳过"分支。
- 数组整体替换 → 写入前必须读出项目配置文件现有内容，只改 `skills` 子树的 `includeSkills` 键。
- 画像文件损坏/缺失 → 视为无画像，走全新询问。
- 无任何个人技能 → 直接告知并结束，不写配置。
- Windows 路径：全部用正斜杠或 Path API，不硬编码反斜杠。

## 验收标准

1. 技能触发：用户说"进入新项目/老项目开始开发"时，Pi 自动匹配本技能。
2. 全量询问：新老项目都展示完整个人技能清单。
3. 画像复用：同项目第二次进入时，清单标注上次选择。
4. 硬过滤生效：写入后新开 session，`omp config list` 在该项目 cwd 下显示 includeSkills 为所选列表；未选技能不出现在系统提示技能清单。
5. 软加载生效：当前 session 内只有选中技能的正文被读取。
6. 不触碰 arkcli-* 托管技能与本技能自身。
