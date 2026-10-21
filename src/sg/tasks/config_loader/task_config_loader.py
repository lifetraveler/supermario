# -*- coding: utf-8 -*-
"""
TaskConfigLoader —— 双配置文件的解析与校验。

  configs/task_type.json   定义段（按 package 分组的 task_types，
                           [{"package":…, "task_types":[…]}, …]，跟软件绑定）
  configs/task_config.json 实例段（version + machines 多设备/应用/分身链）

职责（Skill A 分层：本模块属"配置注入"层，不认识任何业务名词）：
  1. 读两份 JSON、按 version 分派解析器
  2. 解析全部 enabled 的 machine → app → user 链（parse_user_chains，
     多 user 轮转调度用）；parse_document 取第一条，即单 user 老入口
  3. 按每条链 app 的 package 从 task_type.json 取该软件的定义段，
     解析出 queue 全局参数 + 实例条目列表，字段覆盖链：
         task 覆盖字段 → task_types[].defaults → params[].default
  4. 结构非法（type/id 重复、实例引用未定义类型、缺失段落）直接抛
     TaskConfigError——配置错误要在启动时爆，不能留到调度中段。
"""
import json
from pathlib import Path

DEFAULT_CONFIG_PATH = Path("configs") / "task_config.json"
DEFAULT_TYPE_PATH = Path("configs") / "task_type.json"

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



def load_type_document(path=DEFAULT_TYPE_PATH):
    """
    读定义配置（task_type.json）：顶层数组
        [{"package": "com.got.china", "task_types": [...]}, …]
    返回 {package: task_types list}；重复 package 启动即抛。
    """
    path = Path(path)
    if not path.exists():
        raise TaskConfigError(f"任务类型定义文件不存在: {path}")
    try:
        groups = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise TaskConfigError(f"JSON 解析失败 {path}: {e}") from e
    if not isinstance(groups, list) or not groups:
        raise TaskConfigError("task_type.json 顶层数组缺失或为空")
    by_pkg = {}
    for g in groups:
        if not isinstance(g, dict):
            raise TaskConfigError(f"task_type.json 分组必须是对象: {g!r}")
        pkg = g.get("package")
        if not pkg:
            raise TaskConfigError(f"task_type.json 分组缺 package: {g!r}")
        if pkg in by_pkg:
            raise TaskConfigError(f"task_type.json package 重复: {pkg!r}")
        by_pkg[pkg] = g.get("task_types") or []
    return by_pkg


def pick_package_types(by_pkg, package):
    """取指定 package 的定义段列表；缺组直接抛（该软件没有定义任何任务）。"""
    raw = by_pkg.get(package)
    if raw is None:
        raise TaskConfigError(f"task_type.json 缺少 package 分组: {package!r}")
    if not isinstance(raw, list) or not raw:
        raise TaskConfigError(f"task_type.json 中 {package!r} 的 task_types 缺失或为空")
    return raw


def parse_user_chains(doc):
    """
    解析出全部 enabled 的 machine → app → user 链（按配置顺序）。

    返回 [(machine, app, user, trail), …]，trail 形如
    "machine1/com.got.china/user0"。没有任何 enabled 链时抛
    TaskConfigError（与单链语义一致）。
    多 user 轮转调度（5.5）由 UnifiedQueue 消费；单链场景
    parse_document 仍只取第一条。
    """
    machines = doc.get("machines")
    if not isinstance(machines, list) or not machines:
        raise TaskConfigError("machines 缺失或为空")
    chains = []
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
                chains.append((m, a, u, trail))
    if not chains:
        raise TaskConfigError("没有任何 enabled 的 machine/app/user 链")
    return chains


def pick_active_user(doc):
    """取第一条 enabled 的链（parse_user_chains 的单链便捷入口）。"""
    return parse_user_chains(doc)[0]



def parse_document(doc, type_groups=None, chain=None):
    """
    解析为运行时结构（doc 是实例段 version+machines；定义段经 type_groups
    传入；chain 指定要解析的 machine→app→user 链，缺省取第一条 enabled 链）：
      {
        "trail":  "machine1/com.got.china/user0",
        "app":    {"package":…, "user_id":…, "after_done_user_id":…},
        "queue":  {"tick_interval":…, "continue_after_failure":…},
        "task_types": {type: 规范化定义, …},
        "tasks":  [规范化实例条目, …],
      }

    定义段字段：
      id                任务唯一标记，实例条目通过它引用定义段，
                        也是 task_excute_time.json 的统计 key。
      estimated_seconds 任务预估处理时间（秒），预留字段，默认 0 = 未知。

    type_groups 是 load_type_document() 的返回值（{package: task_types list}）；
    缺省时自动读 configs/task_type.json。定义段按链 app 的 package
    取该软件的分组。

    实例条目引用方式：优先 id；旧文件写 type 仍兼容（反查定义段补 id），
    两者同时给出且不一致时启动即抛。

    chain 是 parse_user_chains() 返回值的一项（machine, app, user, trail）。
    多 user 轮转（5.5）用它逐链解析；单链老调用（chain=None）行为不变。
    """
    if chain is None:
        chain = pick_active_user(doc)
    trail_m, trail_a, trail_u, trail = chain
    user = trail_u
    app = trail_a

    # ---------------- 定义段：按链 app 的 package 作用域 ----------------
    # 缺省读模块级 DEFAULT_TYPE_PATH（运行时可被测试/脚本重定向）
    if type_groups is None:
        type_groups = load_type_document(DEFAULT_TYPE_PATH)
    raw_types = pick_package_types(
        type_groups, app.get("package") if isinstance(app, dict) else None)

    # ---------------- 生效链应用信息（分身启动 / 收尾切用户） ----------------
    def _int_field(owner, key, default=0):
        v = owner.get(key)
        if v is None:
            return default
        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
            raise TaskConfigError(f"{key} 必须是非负整数: {v!r}")
        return v

    app_info = {
        "package": app.get("package") if isinstance(app, dict) else None,
        "user_id": _int_field(user, "user_id", 0),
        "after_done_user_id": _int_field(user, "after_done_user_id", 0),
    }
    # ---------------- 定义段规范化 ----------------
    task_types = {}
    id_index = {}
    for t in raw_types:
        if not isinstance(t, dict):
            raise TaskConfigError("task_types 条目必须是对象")
        ttype = t.get("type")
        if not ttype:
            raise TaskConfigError(f"task_types 缺 type: {t!r}")
        if ttype in task_types:
            raise TaskConfigError(f"task type 重复: {ttype!r}")
        tid = t.get("id")
        if not tid or not isinstance(tid, str):
            raise TaskConfigError(f"{ttype}.id 必须是非空字符串: {tid!r}")
        if tid in id_index:
            raise TaskConfigError(f"task id 重复: {tid!r}")
        estimated = t.get("estimated_seconds", 0)
        if isinstance(estimated, bool) or not isinstance(estimated, (int, float)) or estimated < 0:
            raise TaskConfigError(
                f"{ttype}.estimated_seconds 必须是非负数: {estimated!r}"
            )
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
            "id": tid,
            "name_prefix": t.get("name_prefix") or ttype,
            "description": t.get("description") or ttype,
            "estimated_seconds": float(estimated),
            "defaults": dict(defaults),
            "params": params,
        }
        id_index[tid] = ttype

    # ---------------- 实例段 ----------------
    tasks_out = []
    for it in user.get("tasks") or []:
        if not isinstance(it, dict):
            raise TaskConfigError("tasks 条目必须是对象")
        tid = it.get("id")
        ttype = it.get("type")
        if tid:
            if tid not in id_index:
                raise TaskConfigError(f"tasks 引用了未定义的 id: {tid!r}")
            resolved = id_index[tid]
            if ttype and ttype != resolved:
                raise TaskConfigError(
                    f"tasks 条目 id={tid!r} 与 type={ttype!r} 不一致"
                )
            ttype = resolved
        meta = task_types[ttype]
        entry = {
            "id": meta["id"],
            "type": ttype,
            "enabled": bool(it.get("enabled", False)),
            "name_prefix": meta["name_prefix"],
            "estimated_seconds": meta["estimated_seconds"],
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
        "app": app_info,
        "queue": queue,
        "task_types": task_types,
        "tasks": tasks_out,
    }


def parse_user_configs(doc, type_groups=None):
    """
    解析全部 enabled 链为运行时结构列表（多 user 轮转，5.5）。

    每项 = parse_document(doc, type_groups, chain=chain)，并附加链标记：
      machine  machines[].name
      package  apps[].package
      user_id  users[].user_id
    返回 [cfg, …]，按配置顺序；单链场景返回 1 项，与 parse_document 等价。
    每条链的 app 引用的 package 必须在定义文件里有分组，否则启动即抛。
    """
    chains = parse_user_chains(doc)
    if type_groups is None:
        type_groups = load_type_document(DEFAULT_TYPE_PATH)
    out = []
    for chain in chains:
        cfg = parse_document(doc, type_groups, chain=chain)
        cfg["machine"] = chain[0].get("name")
        cfg["package"] = cfg["app"]["package"]
        cfg["user_id"] = cfg["app"]["user_id"]
        out.append(cfg)
    return out


def load(path=DEFAULT_CONFIG_PATH, type_path=None):
    """一步到位：实例文件 + 定义文件 → 运行时结构。

    type_path 缺省运行时取 DEFAULT_TYPE_PATH（模块级变量，
    便于测试重定向）。
    """
    if type_path is None:
        type_path = DEFAULT_TYPE_PATH
    return parse_document(load_document(path), load_type_document(type_path))
