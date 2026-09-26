---
name: coco-to-elements
version: 1.0
description: |
  根据 coco_annotations.json 的 categories 生成 src/sg/scene/elements.py
  里的 SceneElement 常量。当需要新增/同步 UI 特征资源时使用。
trigger_keywords:
  - coco_annotations
  - elements.py
  - SceneElement
  - 资源 id
  - 特征资源
  - 生成 elements
---

# Skill C：从 coco_annotations.json 生成 elements.py

## 一、输入 / 输出

| 项 | 内容 |
|---|---|
| 输入 | `coco_annotations.json`（COCO 格式） |
| 关注字段 | `categories[].name`（特征名） |
| 忽略字段 | `images` / `annotations[].bbox` / `id` / `supercategory`（除非要做 bbox 兜底，见第五节） |
| 输出 | `src/sg/scene/elements.py` 里的一组 `SceneElement` 常量 |

---

## 二、转换规则

### 2.1 三步走

```
categories[].name  →  resource_id  →  变量名
   "button_goto_worldmap"   同 name      BUTTON_GOTO_WORLDMAP
```

### 2.2 resource_id：**原样照抄 name**

- **不修正、不翻译、不去重**，哪怕名字里有拼写错误。
- 例：游戏里就是 `wolrd_icon_resource_stone`（不是 world），`resource_id` 就写 `wolrd_icon_resource_stone`。
- 原因：`resource_id` 是查找特征时的 key，必须与图片资源名严格一致。

### 2.3 变量名：name 转大写下划线

| name | 变量名 |
|---|---|
| `button_goto_worldmap` | `BUTTON_GOTO_WORLDMAP` |
| `wolrd_icon_resource_stone` | `WORLD_ICON_RESOURCE_STONE`（变量名可修正，resource_id 不可） |
| `item_scare_wolf_claw` | `ITEM_SCARE_WOLF_CLAW` |

**规则**：`name.upper()`，保留下划线，去掉非字母数字字符。

### 2.4 name 字段：写一句中文说明

- 用于日志可读性，不必逐字对应。
- 例：`BUTTON_GOTO_WORLDMAP` 的 name 可写 `"进入荒野按钮"`。

---

## 三、输出模板

```python
from src.sg.scene.scene_element import SceneElement

# ========================================================
# 按钮
# ========================================================
BUTTON_GOTO_WORLDMAP = SceneElement(
    resource_id="button_goto_worldmap",
    name="进入荒野按钮",
)

BUTTON_BACKPACK = SceneElement(
    resource_id="button_backpack",
    name="背包按钮",
)

# ========================================================
# 世界资源图标
# ========================================================
WORLD_ICON_RESOURCE_STONE = SceneElement(
    resource_id="wolrd_icon_resource_stone",   # 游戏原名
    name="世界-石头资源图标",
)

WORLD_RESOURCE_SCARE_WOLF = SceneElement(
    resource_id="world_resource_scare_wolf",
    name="世界资源-恐狼",
)

# ========================================================
# 道具
# ========================================================
ITEM_SCARE_WOLF_CLAW = SceneElement(
    resource_id="item_scare_wolf_claw",
    name="狼爪道具",
)
```

---

## 四、生成流程

### 4.1 手动 / AI 生成步骤

1. 读取 `coco_annotations.json`，取出 `categories` 数组。
2. 对每个 `category.name`：
   - `resource_id` = name 原样
   - `变量名` = name 转大写下划线（保留下划线，去除非字母数字）
   - `name` = 按语义写一句中文（AI 推断，不确定时先留空 / 打 TODO）
3. 按**功能分组**输出（按钮 / 图标 / 道具 / 标签 / 弹窗关闭 …），组间加注释头。
4. 检查是否与 `elements.py` 已有常量重名：
   - 变量名重名 → 合并 / 改名（极少见）
   - `resource_id` 重名 → 直接跳过（已存在）

### 4.2 增量同步（推荐）

新活动 / 新版本上线时：

1. 全量导出新 `coco_annotations.json`。
2. 对比现有 `elements.py` 的 `resource_id` 集合。
3. **只追加**新增项，不删旧项、不改旧项。
4. 保留原有分组注释，新项插入合适分组或新建分组。

### 4.3 一段可用的生成脚本骨架

```python
import json
import re

def to_var(name: str) -> str:
    s = re.sub(r"[^0-9a-zA-Z_]", "_", name)
    return s.upper()

def gen(coco_path: str) -> str:
    with open(coco_path, encoding="utf-8") as f:
        data = json.load(f)

    lines = ["from src.sg.scene.scene_element import SceneElement", ""]
    for cat in data.get("categories", []):
        rid = cat["name"]
        var = to_var(rid)
        lines.append(f'{var} = SceneElement(')
        lines.append(f'    resource_id="{rid}",')
        lines.append(f'    name="{rid}",   # TODO: 补中文说明')
        lines.append(')')
        lines.append("")
    return "\n".join(lines)
```

**注意**：脚本只负责机械转换，中文 `name` 与分组注释交给 AI 或人工补。

---

## 五、bbox 兜底场景（可选）

如果某个资源需要在"游戏强制居中"时按 bbox 直接点（见 Skill B 第六节），可以额外在 `elements.py` 里记相对坐标：

```python
WORLD_RESOURCE_SCARE_WOLF = SceneElement(
    resource_id="world_resource_scare_wolf",
    name="世界资源-恐狼",
    # 仅当上一步操作让游戏强制居中时使用
    fallback_bbox=(0.32, 0.45, 0.10, 0.08),   # (x, y, w, h)，相对坐标
)
```

从 COCO 的 `annotations[].bbox`（像素）换算成相对坐标：

```
x_rel = bbox[0] / image_width
y_rel = bbox[1] / image_height
w_rel = bbox[2] / image_width
h_rel = bbox[3] / image_height
```

**不要默认给所有元素加 `fallback_bbox`**，只有确认游戏居中的资源才加。

---

## 六、常见陷阱

| 陷阱 | 正确做法 |
|---|---|
| 把 `wolrd` 修正成 `world` | `resource_id` 原样照抄 name，变量名可以修正 |
| 变量名重名 | 检查已存在常量，合并 / 加后缀区分 |
| 删除旧资源 | **只追加**，旧资源可能仍在使用 |
| 中文 `name` 瞎编 | 不确定时先写 `# TODO`，别硬猜 |
| 给所有元素加 `fallback_bbox` | 只在游戏强制居中的资源上加 |
| 分组注释全丢了 | 按功能分组，插入新项时保留原注释 |

---

## 七、给 AI 的指令模板

```text
参考 Skill C：从 coco_annotations.json 生成 elements.py。

输入：
- coco_annotations.json 路径：<路径>
- 目标 elements.py：<路径>

要求：
1. 只处理 categories[].name，忽略 images / annotations。
2. resource_id 原样照抄 name，不修正拼写。
3. 变量名 = name.upper()，保留下划线，去除非字母数字。
4. 按功能分组输出，组间加注释头。
5. 与现有 elements.py 去重：resource_id 已存在则跳过。
6. 中文 name 字段不确定时先写 `# TODO`，不硬猜。
7. 除非我明确说明某资源需要 bbox 兜底，不要生成 fallback_bbox。
```

---

**一句话**：`categories[].name` 原样做 `resource_id`，转大写做变量名，分组追加，绝不改旧、绝不猜中文、绝不默认加 bbox。
