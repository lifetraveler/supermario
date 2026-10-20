# -*- coding: utf-8 -*-
"""一次性迁移：把 configs/task_config.json 的 task_types 拆分到 task_type.json。

产物结构（用户指定）：
    [
      {"package": "com.got.china", "task_types": [{...}, ...]},
      ...
    ]

按 packages 在 task_config.json machines 里实际出现的顺序建组（保证组顺序稳定）；
每条 task_types 定义挪进其生效 app 的 package 组。迁移后：
  - task_config.json 只剩 {"version":1, "machines":[...]}
  - 原文件备份 *.pre-split.bak
"""
import json
import shutil
from pathlib import Path

CFG = Path("configs/task_config.json")
TYPE_FILE = Path("configs/task_type.json")
BAK_SUFFIX = ".pre-split.bak"


def main():
    doc = json.loads(CFG.read_text(encoding="utf-8"))
    task_types = doc.pop("task_types", [])
    machines = doc.get("machines") or []

    packages = []
    for m in machines:
        for a in m.get("apps") or []:
            pkg = a.get("package")
            if pkg and pkg not in packages:
                packages.append(pkg)

    grouped = [{"package": pkg, "task_types": list(task_types)} for pkg in packages]
    doc["version"] = 1
    out_cfg = {"version": 1, "machines": machines}

    for path, payload in ((TYPE_FILE, grouped), (CFG, out_cfg)):
        if path.exists():
            shutil.copy2(path, str(path) + BAK_SUFFIX)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        print(f"written {path}")

    print(f"packages: {packages}")
    print(f"task_types duplicated into {len(grouped)} package groups "
          f"({len(task_types)} definitions each)")


if __name__ == "__main__":
    main()
