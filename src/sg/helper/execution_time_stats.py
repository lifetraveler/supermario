# -*- coding: utf-8 -*-
"""
ExecutionTimeStats —— task_excute_time.json 的读写（5.4 耗时统计）。

文件形态（configs/task_excute_time.json）：
    {
      "version": 1,
      "types": {
        "<task_types[].id>": {"avg": 123.4, "samples": 20, "max_keep": 10}
      }
    }

设计要点：
  - key 是 task_config.json 定义段的 id（唯一标记），不是 type。
  - 本模块无业务知识，只做「样本合并 + 滚动均值 + 原子写」。
  - 单次耗时样本在 TaskQueue._do_interaction 按工厂 type_id 聚合，
    run() 收尾由 UnifiedQueue 把队列内存样本合并进该文件。
"""
import json
import os
from collections import deque
from pathlib import Path

DEFAULT_STATS_PATH = Path("configs") / "task_excute_time.json"

# 合并进文件时每个 id 最多保留的样本数（滚动均值窗口）
MAX_KEEP = 10

# 一次 run 内每个工厂聚合窗口（TaskQueue._SAMPLE_WINDOW，镜像值防循环 import）
QUEUE_SAMPLE_WINDOW = 20


class ExecutionTimeStats:
    """task_excute_time.json 的读写器：合并 + 滚动均值 + 原子写。"""

    def __init__(self, path=DEFAULT_STATS_PATH, max_keep=MAX_KEEP):
        self.path = Path(path)
        self.max_keep = int(max_keep)
        self._data = {"version": 1, "types": {}}
        self._load()

    # ------------------------------------------------------------------
    # 读
    # ------------------------------------------------------------------
    def _load(self):
        """读磁盘，缺文件 / 结构坏 / 类型不合法一律按空表处理（统计不阻断调度）。"""
        try:
            text = self.path.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError):
            return
        try:
            doc = json.loads(text)
        except json.JSONDecodeError:
            return
        if not isinstance(doc, dict):
            return
        types = doc.get("types")
        if not isinstance(types, dict):
            return
        clean = {}
        for tid, item in types.items():
            if not isinstance(item, dict):
                continue
            avg = item.get("avg")
            samples = item.get("samples")
            if isinstance(avg, bool) or not isinstance(avg, (int, float)):
                continue
            if isinstance(samples, bool) or not isinstance(samples, int):
                continue
            clean[tid] = {"avg": float(avg), "samples": int(samples)}
        self._data = {"version": 1, "types": clean}

    # ------------------------------------------------------------------
    # 合并 + 写
    # ------------------------------------------------------------------
    def merge(self, samples_by_id):
        """
        合并一次 run 的内存样本：{id: deque[float] | list[float]}。
        返回 {id: (新均值, 总样本数)}，供调用方打日志。
        """
        out = {}
        types = self._data.setdefault("types", {})
        for tid, samples in samples_by_id.items():
            fresh = [float(s) for s in (samples or []) if isinstance(s, (int, float))]
            if not fresh:
                continue
            prev = types.get(tid)
            prev_list = []
            if prev is not None and prev.get("samples", 0) > 1:
                # 文件只存均值：旧均值视作 (samples-1) 个等值样本参与合并，
                # 保证滚动窗口语义（新样本权重更高，旧数据逐渐被稀释）。
                prev_list = [prev["avg"]] * (int(prev["samples"]) - 1)
            merged = (prev_list + fresh)[-self.max_keep:]
            avg = sum(merged) / len(merged)
            types[tid] = {
                "avg": avg,
                "samples": len(merged),
                "max_keep": self.max_keep,
            }
            out[tid] = (avg, len(merged))
        return out

    def save(self):
        """原子写：.tmp + os.replace，写前落 .bak。"""
        types = self._data.get("types") or {}
        if not types:
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(self._data, ensure_ascii=False, indent=2) + "\n"
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(text, encoding="utf-8")
        try:
            if self.path.exists():
                os.replace(self.path, self.path.with_suffix(".json.bak"))
            os.replace(tmp, self.path)
        except OSError:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            return False
        return True

    # ------------------------------------------------------------------
    # 读接口（webui / 预估耗时用）
    # ------------------------------------------------------------------
    def get(self, task_id):
        """取某任务 id 的 {"avg": 秒, "samples": n}，未知 id 返回 None。"""
        return self._data["types"].get(task_id)
