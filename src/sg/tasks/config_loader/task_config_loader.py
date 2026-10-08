# -*- coding: utf-8 -*-
"""
TaskConfigLoader —— configs/task_config.json 的解析与校验。

职责（Skill A 分层：本模块属"配置注入"层，不认识任何业务名词）：
  1. 读 JSON、按 version 分派解析器
  2. 取第一个 enabled 的 machine → app → user（多设备/分身派发后续实现，
     本期只消费第一条 enabled 链，schema 已预留）
  3. 解析出 queue 全局参数 + 实例条目列表，字段覆盖链：
         task 覆盖字段 → task_types[].defaults → params[].default
  4. 结构非法（type 重复、实例引用未定义类型、缺失段落）直接抛
     TaskConfigError——配置错误要在启动时爆，不能留到调度中段。
"""
import json
from pathlib import Path

DEFAULT_CONFIG_PATH = Path("configs") / "task_config.json"

# 定义段 defaults 的合法键（缺一不可，防止半截定义悄悄溜进调度）
DEFAULT_KEYS = (
    "count", "max_active", "requires_march_queue",
    "next_trigger_delay", "one_shot", "cron",
)
# 实例条目允许覆盖的定义字段
OVERRIDABLE_KEYS = DEFAULT_KEYS

PARAM_WIDGETS = {"switch", "number", "text", "drop_down", "multi_drop_down"}


class TaskConfigError(Exception):
    """task_config.json 结构或语义非法。"""


def load_document(path=DEFAULT_CONFIG_PATH):
    """读文件 + 顶层结构校验，返回 dict。"""
    path = Path(path)
    if not path.exists():
        raise TaskConfigError(f"配置文件不存在: {path}")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise TaskConfigError(f"JSON 解析失败 {path}: {e}") from e
    if not isinstance(doc, dict):
        raise TaskConfigError("顶层必须是对象")
    if doc.get("version") != 1:
        raise TaskConfigError(f"不支持的 version: {doc.get('version')!r}")
    return doc


def pick_active_user(doc):
    """
    取第一个 enabled 的 machine → app → user。
    多设备/多应用/多分身派发是后续功能；本期返回值即"当前生效链"，
    并带一条 trail 供日志标注来源。
    """
    machines = doc.get("machines")
    if not isinstance(machines, list) or not machines:
        raise TaskConfigError("machines 缺失或为空")
    for m in machines:
        if not m.get("enabled"):
            continue
        for a in m.get("apps") or []:
            if not a.get("enabled"):
                continue
            for u in a.get("users") or []:
                if not u.get("enabled"):
                    continue
                trail = f"{m.get('name')}/{a.get('package')}/user{u.get('user_id')}"
                return m, a, u, trail
    raise TaskConfigError("没有任何 enabled 的 machine/app/user 链")


def parse_document(doc):
    """
    解析为运行时结构：
      {
        "trail":  "machine1/com.got.china/user0",
        "queue":  {"tick_interval":…, "continue_after_failure":…},
        "task_types": {type: 规范化定义, …},
        "tasks":  [规范化实例条目, …],
      }
    """
    trail_m, _trail_a, trail_u, trail = pick_active_user(doc)  # noqa: F841
    user = trail_u

    # ---------------- 定义段 ----------------
    raw_types = doc.get("task_types")
    if not isinstance(raw_types, list) or not raw_types:
        raise TaskConfigError("task_types 缺失或为空")
    task_types = {}
    for t in raw_types:
        if not isinstance(t, dict):
            raise TaskConfigError("task_types 条目必须是对象")
        ttype = t.get("type")
        if not ttype:
            raise TaskConfigError(f"task_types 缺 type: {t!r}")
        if ttype in task_types:
            raise TaskConfigError(f"task type 重复: {ttype!r}")
        defaults = t.get("defaults")
        if not isinstance(defaults, dict):
            raise TaskConfigError(f"{ttype}.defaults 缺失")
        missing = [k for k in DEFAULT_KEYS if k not in defaults]
        if missing:
            raise TaskConfigError(f"{ttype}.defaults 缺字段: {missing}")
        if not isinstance(defaults["cron"], str):
            raise TaskConfigError(f"{ttype}.defaults.cron 必须是字符串")

        params = {}
        for p in t.get("params") or []:
            pname, pattr = p.get("name"), p.get("attr")
            if not pname or not pattr:
                raise TaskConfigError(f"{ttype}.params 缺 name/attr: {p!r}")
            if pattr in params:
                raise TaskConfigError(f"{ttype}.params attr 重复: {pattr!r}")
            widget = p.get("widget")
            if widget is not None:
                if not isinstance(widget, dict) or widget.get("type") not in PARAM_WIDGETS:
                    raise TaskConfigError(f"{ttype}.{pname}.widget 非法: {widget!r}")
            params[pattr] = {
                "name": pname,
                "default": p.get("default"),
                "widget": widget,
                "desc": p.get("desc", ""),
            }
        task_types[ttype] = {
            "name_prefix": t.get("name_prefix") or ttype,
            "description": t.get("description") or ttype,
            "defaults": dict(defaults),
            "params": params,
        }

    # ---------------- 实例段 ----------------
    tasks_out = []
    for it in user.get("tasks") or []:
        if not isinstance(it, dict):
            raise TaskConfigError("tasks 条目必须是对象")
        ttype = it.get("type")
        if ttype not in task_types:
            raise TaskConfigError(f"tasks 引用了未定义的 type: {ttype!r}")
        meta = task_types[ttype]
        entry = {
            "type": ttype,
            "enabled": bool(it.get("enabled", False)),
            "name_prefix": meta["name_prefix"],
        }
        for k in OVERRIDABLE_KEYS:
            v = it.get(k)
            entry[k] = meta["defaults"][k] if v is None else v
        params_def = meta["params"]
        params_in = it.get("params") or {}
        unknown = [k for k in params_in if k not in params_def]
        if unknown:
            raise TaskConfigError(f"{ttype}.params 未知字段: {unknown}")
        entry["params"] = {
            attr: params_in.get(attr, pd["default"])
            for attr, pd in params_def.items()
        }
        tasks_out.append(entry)

    queue_in = user.get("queue") or {}
    queue = {
        "tick_interval": float(queue_in.get("tick_interval", 1.0)),
        "continue_after_failure": bool(queue_in.get("continue_after_failure", True)),
    }
    return {
        "trail": trail,
        "queue": queue,
        "task_types": task_types,
        "tasks": tasks_out,
    }


def load(path=DEFAULT_CONFIG_PATH):
    """一步到位：文件 → 运行时结构。"""
    return parse_document(load_document(path))
