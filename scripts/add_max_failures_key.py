# -*- coding: utf-8 -*-
"""一次性迁移：task_type.json 所有定义段 defaults 补 max_failures 键。

最大失败次数功能：定义段 defaults 增加第 7 个调度键。
  - 缺省 0 = 回退队列级 max_failures（queue 段），队列级也是 0 = 不限。
  - >0 = 该任务类型自己的失败上限，优先于队列级。

老配置文件（缺失键）由本脚本统一补齐，备份 *.pre-maxfail.bak。
"""
import json
import shutil
from pathlib import Path

TYPE_FILE = Path("configs/task_type.json")
BAK_SUFFIX = ".pre-maxfail.bak"


def main():
    if not TYPE_FILE.exists():
        print(f"skip: {TYPE_FILE} 不存在")
        return
    doc = json.loads(TYPE_FILE.read_text(encoding="utf-8"))
    bak = TYPE_FILE.with_suffix(TYPE_FILE.suffix + BAK_SUFFIX)
    shutil.copy2(TYPE_FILE, bak)

    n_types = 0
    for group in doc if isinstance(doc, list) else []:
        for t in group.get("task_types") or []:
            defaults = t.get("defaults")
            if not isinstance(defaults, dict):
                continue
            defaults.setdefault("max_failures", 0)
            n_types += 1

    TYPE_FILE.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"migrated {n_types} task_types -> {TYPE_FILE} (backup: {bak})")


if __name__ == "__main__":
    main()
