"""
领主（Player）领域对象。

职责：
  - 从主界面头像进入领主展示界面
  - OCR 体力区域（"有效/上限"斜杠分割）
  - 部队查看 → 编组界面，切换省体力编组 / 最强编组，OCR 部队实力
  - 采集结果写入 task 全局字段 + game_temp_data.json 持久化
  - get_stamina / get_team_power 三级取值：
    task 全局对象 → game_temp_data.json → 现场跑获取流程

不负责：弹窗清理、场景判定兜底、队列调度。
"""

import json
import os
import re

from src.sg.scene.elements import (
    GLOBAL_PLAYER_HEADPHOTO,
    GLOBAL_PAGE_PLAYER_STAMINA,
    GLOBAL_PALYER_PAGE_TEAM_ENTRY,
    GLOBAL_PALYER_PAGE_TEAM_ORG_ENTRY,
    GLOBAL_PLAYER_TEAM_POWER_AREA,
    TEAM_POWER_BIGGEST,
    TEAM_HUNTING,
)
from src.sg.scene.scene_type import SceneType


class Player:
    """
    领主领域对象。

    对外接口：
      ensure_player_data()  -> bool   三级取值总入口，补齐缺失数据
      get_stamina()         -> int|None  有效体力（三级取值）
      get_team_power(key)   -> int|None  部队实力，'sinew'/'biggest'
      collect_all()         -> bool   现场完整获取流程（进界面→体力→双编组→回主界面）

    task 全局字段（本对象写入，与 game_temp_data.json 同层级）：
      task.player = {
          "stamina": {"remain": ..., "max": ...},
          "team_power": {
              "team_power_sinew": ...,
              "team_power_biggest": ...,
          },
      }
    """

    # task.player 嵌套结构与 json 文件的键名（两处统一）
    KEY_STAMINA = "remain"
    KEY_STAMINA_MAX = "max"
    KEY_POWER_SINEW = "team_power_sinew"
    KEY_POWER_BIGGEST = "team_power_biggest"

    def __init__(self, task):
        self.task = task

        # ---- 参数 ----
        # 界面跳转 / OCR 的元素等待超时
        self.entry_timeout = 6.0
        # 界面跳转后的稳定等待
        self.settle_seconds = 1.0
        # 临时数据文件（工作目录）
        self.data_file = "game_temp_data.json"

    # ========================================================
    # 三级取值总入口
    # ========================================================

    def get_stamina(self):
        """
        取有效体力。优先级：task 全局 → json 文件 → 现场获取。
        取不到返回 None。
        """
        value = self._task_stamina("remain")
        if value is not None:
            return value

        value = self._read_file_stamina()["remain"]
        if value is not None:
            self.task.log_info(f"从 {self.data_file} 读取体力: {value}")
            self._write_task_fields(
                {"player": {"stamina": {"remain": value}}}
            )
            return value

        self.task.log_info("task 与文件均无体力数据，现场获取")
        if not self.collect_all():
            return None
        return self._task_stamina("remain")

    def get_team_power(self):
        """
        取整个部队实力节点 team_power。

        优先级：task 全局 → json 文件 → 现场获取。
        返回 dict（可能为部分字段），完全无数据返回 None。
        """
        power = self._task_team_power()
        if power:
            return power

        file_power = {
            k: v for k, v in self._read_file_team_power().items()
            if v is not None
        }
        if file_power:
            self.task.log_info(f"从 {self.data_file} 读取部队实力: {file_power}")
            self._write_task_fields({"player": {"team_power": file_power}})
            return self._task_team_power()

        self.task.log_info("task 与文件均无部队实力，现场获取")
        if not self.collect_all():
            return None
        return self._task_team_power()

    def get_team_power_key(self, key):
        """
        取单个部队实力。

        key: 'sinew'（省体力编组）/'biggest'（最强编组），
        复用 get_team_power 的三级取值。
        """
        file_key = (self.KEY_POWER_SINEW if key == "sinew"
                    else self.KEY_POWER_BIGGEST)
        power = self.get_team_power()
        return power.get(file_key) if power else None

    def ensure_player_data(self, need_stamina=True, need_sinew=True,
                           need_biggest=False):
        """
        按需补齐缺失数据。全部就绪返回 True。
        缺什么补什么：字段有值就跳过，不做重复采集。
        """
        need_checks = (
            (need_stamina, lambda: self._task_stamina("remain"),
             "stamina"),
            (need_sinew, lambda: self._task_team_power(self.KEY_POWER_SINEW),
             "sinew"),
            (need_biggest,
             lambda: self._task_team_power(self.KEY_POWER_BIGGEST),
             "biggest"),
        )

        missing = [name for flag, getter, name in need_checks
                   if flag and getter() is None]

        if not missing:
            return True

        self.task.log_info(f"领主数据缺失: {missing}，开始采集")

        # 现场采集体力 + 双编组（一次进界面全拿）
        if not self.collect_all():
            return False

        ok = True
        for flag, getter, name in need_checks:
            if flag and getter() is None:
                ok = False
        return ok

    # ========================================================
    # 现场完整获取流程
    # ========================================================

    def collect_all(self) -> bool:
        """
        完整获取流程：
          主界面 → 头像 → 领主界面（OCR 体力）
          → 部队查看 → 编组界面 → 省体力编组实力
          → 切最强编组 → 实力 → ESC 退回主界面
        结果写 task 全局字段并落盘 game_temp_data.json。
        """
        self.task.log_info("========== 采集领主数据 ==========")

        if not self._open_player_page():
            self._back_to_main()
            return False

        stamina_ok = self._read_stamina()

        team_ok = self._open_team_org_page()
        if team_ok:
            sinew_ok = self._read_team_power("sinew")
            biggest_ok = self._switch_and_read_biggest()
            team_ok = sinew_ok or biggest_ok
            self._back_to_main()
        else:
            self._back_to_main()

        self._save_to_file()

        self.task.log_info(
            f"========== 领主数据采集完成 "
            f"(体力={'OK' if stamina_ok else 'FAIL'}, "
            f"部队={'OK' if team_ok else 'FAIL'}) =========="
        )
        return stamina_ok or team_ok

    # ========================================================
    # 步骤：进入领主界面
    # ========================================================

    def _open_player_page(self) -> bool:
        """主界面 → 点头像 → 领主展示界面。"""
        if not self.task._wait_and_click(
            GLOBAL_PLAYER_HEADPHOTO, timeout=self.entry_timeout
        ):
            self.task.log_error("未找到领主头像入口")
            return False
        self.task._sleep(self.settle_seconds)
        return True

    # ========================================================
    # 步骤：OCR 体力
    # ========================================================

    def _read_stamina(self) -> bool:
        """
        领主界面 OCR 体力区域。
        文本格式 "有效/上限"，斜杠分割，前有效体力、后最大自动恢复上限。
        """
        box = self._safe_box(GLOBAL_PAGE_PLAYER_STAMINA)
        if not box:
            self.task.log_error("未找到体力区域 bbox")
            return False

        text = self._ocr_text(box)
        if text is None:
            return False

        self.task.log_info(f"OCR 体力原文: {text}")
        current, maximum = self._parse_stamina_text(text)
        if current is None:
            self.task.log_error(f"体力文本解析失败: {text}")
            return False

        self._set_task_stamina(current, maximum)
        self.task.log_info(
            f"体力: {current}"
            + (f"/{maximum}" if maximum is not None else "")
        )
        return True

    def _parse_stamina_text(self, text):
        """
        解析 "有效/上限" 体力文本。

        正则取斜杠两侧的数字（兼容千分位与小数）：
          '150/200'       -> (150, 200)
          '1,200/2,000'   -> (1200, 2000)
          '150'           -> (150, None)
        无数字返回 (None, None)。
        """
        if text is None:
            return (None, None)
        text = str(text).strip()

        num = r"(\d[\d,]*(?:\.\d+)?)"
        m = re.search(num + r"\s*/\s*" + num, text)
        if m:
            return (self._to_int(m.group(1)), self._to_int(m.group(2)))

        m = re.search(num, text)
        if m:
            return (self._to_int(m.group(1)), None)
        return (None, None)

    # ========================================================
    # 步骤：部队编组界面
    # ========================================================

    def _open_team_org_page(self) -> bool:
        """领主界面 → 部队查看入口 → 部队编组按钮 → 编组界面。"""
        if not self.task._wait_and_click(
            GLOBAL_PALYER_PAGE_TEAM_ENTRY, timeout=self.entry_timeout
        ):
            self.task.log_error("未找到部队查看入口")
            return False
        self.task._sleep(self.settle_seconds)

        if not self.task._wait_and_click(
            GLOBAL_PALYER_PAGE_TEAM_ORG_ENTRY, timeout=self.entry_timeout
        ):
            self.task.log_error("未找到部队编组按钮")
            return False
        self.task._sleep(self.settle_seconds)
        return True

    # ========================================================
    # 步骤：编组实力 OCR
    # ========================================================

    def _read_team_power(self, key: str) -> bool:
        """
        编组界面：先点省体力编组（TEAM_HUNTING），切换完成后
        OCR power 区域，写入 task.player.team_power.team_power_sinew。
        key 仅支持 'sinew'（最强编组走 _switch_and_read_biggest）。
        """
        if key != "sinew":
            return False

        if not self.task._wait_and_click(
            TEAM_HUNTING, timeout=self.entry_timeout
        ):
            self.task.log_error("未找到省体力编组（team_prepare_hunting_less_sinew）")
            return False
        self.task._sleep(self.settle_seconds)
        return self._ocr_power_to(self.KEY_POWER_SINEW)

    def _switch_and_read_biggest(self) -> bool:
        """切换到最强编组并 OCR 实力，写入 task.player.team_power。"""
        if not self.task._wait_and_click(
            TEAM_POWER_BIGGEST, timeout=self.entry_timeout
        ):
            self.task.log_error("未找到最强编组切换入口")
            return False
        self.task._sleep(self.settle_seconds)
        return self._ocr_power_to(self.KEY_POWER_BIGGEST)

    def _ocr_power_to(self, power_key: str) -> bool:
        """OCR power 区域，成功则写入 task.player.team_power 的指定键。"""
        box = self._safe_box(GLOBAL_PLAYER_TEAM_POWER_AREA)
        if not box:
            self.task.log_error("未找到编组实力区域 bbox")
            return False

        text = self._ocr_text(box)
        if text is None:
            return False

        self.task.log_info(f"OCR 编组实力原文: {text}")
        value = self._parse_power_text(text)
        if value is None:
            self.task.log_error(f"编组实力解析失败: {text}")
            return False

        self._set_task_team_power(power_key, value)
        self.task.log_info(f"{power_key}: {value}")
        return True

    # ========================================================
    # 步骤：退回主界面
    # ========================================================

    def _back_to_main(self) -> bool:
        """ESC 逐层退出到主界面（城市/世界地图）。"""
        for _ in range(6):
            scene = self.task.scene_detector.detect()
            if scene.type in (SceneType.CITY, SceneType.WORLD_MAP):
                self.task.log_info("已退回主界面")
                return True
            self.task.recovery._try_press_esc()
            self.task._sleep(1.0)
        self.task.log_error("无法退回主界面")
        return False

    def _read_file_stamina(self) -> dict:
        """
        从嵌套 json 取体力段。
        返回 {"remain": x, "max": y}，缺失侧为 None。
        """
        player = self._load_from_file().get("player")
        stamina = player.get("stamina") if isinstance(player, dict) else None
        stamina = stamina if isinstance(stamina, dict) else {}
        return {
            "remain": stamina.get("remain"),
            "max": stamina.get("max"),
        }

    def _read_file_team_power(self) -> dict:
        """
        从嵌套 json 取部队实力段。
        返回 {"team_power_sinew": x, "team_power_biggest": y}，缺失为 None。
        """
        player = self._load_from_file().get("player")
        power = player.get("team_power") if isinstance(player, dict) else None
        power = power if isinstance(power, dict) else {}
        return {
            self.KEY_POWER_SINEW: power.get(self.KEY_POWER_SINEW),
            self.KEY_POWER_BIGGEST: power.get(self.KEY_POWER_BIGGEST),
        }


    def _save_to_file(self):
        """
        把本次采集到的数据合并写入 game_temp_data.json。

        文件格式（嵌套）：
            {
              "player": {
                "stamina": {"remain": 10, "max": 200},
                "team_power": {
                  "team_power_sinew": 161573316,
                  "team_power_biggest": 176913223
                }
              }
            }
        """
        data = self._load_from_file()
        self._merge_nested(data, self._task_fields_to_nested())
        try:
            with open(self.data_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self.task.log_info(f"领主数据已保存到 {self.data_file}: {data}")
        except OSError as e:
            self.task.log_error(f"保存 {self.data_file} 失败: {e}")

    def _load_from_file(self) -> dict:
        """
        读取 game_temp_data.json；不存在/损坏返回空 dict。
        返回的是嵌套结构原样（player → stamina/team_power）。
        """
        try:
            with open(self.data_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write_task_fields(self, nested: dict):
        """把文件嵌套数据深层合并进 task.player。"""
        patch = nested.get("player")
        if not isinstance(patch, dict):
            return

        task_player = self._ensure_task_player()
        self._merge_nested(task_player, patch)

    def _merge_nested(self, base: dict, patch: dict):
        """
        把 patch 深层合并进 base（就地修改）。
        dict 递归合并，其它值直接覆盖。
        防止部分字段保存时把另一段（stamina/team_power）整段抹掉。
        """
        for key, value in patch.items():
            if (
                key in base
                and isinstance(base[key], dict)
                and isinstance(value, dict)
            ):
                self._merge_nested(base[key], value)
            else:
                base[key] = value

    def _task_fields_to_nested(self) -> dict:
        """task.player → 嵌套 json 片段（只含有值的叶子）。"""
        player = self._task_player()
        if not player:
            return {}

        out_player = {}

        stamina = player.get("stamina")
        if isinstance(stamina, dict):
            seg = {k: v for k, v in stamina.items() if v is not None}
            if seg:
                out_player["stamina"] = seg

        team_power = player.get("team_power")
        if isinstance(team_power, dict):
            seg = {k: v for k, v in team_power.items() if v is not None}
            if seg:
                out_player["team_power"] = seg

        if not out_player:
            return {}
        return {"player": out_player}

    # ========================================================
    # task.player 嵌套访问（读写统一的底层）
    # ========================================================

    def _task_player(self) -> dict:
        """取 task.player（缺失/类型异常视为空 dict，不回写）。"""
        player = getattr(self.task, "player", None)
        return player if isinstance(player, dict) else {}

    def _ensure_task_player(self) -> dict:
        """取 task.player，缺失则初始化为 {} 并挂回 task。"""
        player = getattr(self.task, "player", None)
        if not isinstance(player, dict):
            player = {}
            self.task.player = player
        return player

    def _task_stamina(self, key):
        """task.player.stamina[key]，缺失返回 None。"""
        stamina = self._task_player().get("stamina")
        if not isinstance(stamina, dict):
            return None
        return stamina.get(key)

    def _task_team_power(self, key=None):
        """task.player.team_power 节点；传 key 则取单值，缺失返回 None。"""
        power = self._task_player().get("team_power")
        if not isinstance(power, dict):
            return None if key else {}
        return power.get(key) if key else power

    def _set_task_stamina(self, remain, maximum):
        """写入 task.player.stamina（None 值跳过对应键）。"""
        stamina = self._ensure_task_player().setdefault("stamina", {})
        if remain is not None:
            stamina["remain"] = remain
        if maximum is not None:
            stamina["max"] = maximum

    def _set_task_team_power(self, key, value):
        """写入 task.player.team_power[key]。"""
        power = self._ensure_task_player().setdefault("team_power", {})
        power[key] = value

    # ========================================================
    # 内部方法
    # ========================================================

    def _ocr_text(self, box):
        """OCR box 区域，返回首个结果文本；异常/无结果返回 None。"""
        try:
            results = self.task.ocr(box=box)
        except Exception as e:
            self.task.log_info(f"OCR 异常: {e}")
            return None
        if not results:
            return None
        return results[0].name or ""

    def _parse_power_text(self, text):
        """
        解析实力文本为 int。
        兼容千分位、小数、万/亿量级（与 Cesare._parse_power_text 同口径）。
        """
        if text is None:
            return None
        text = str(text).strip()
        if not text:
            return None

        m = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*([万亿])?", text)
        if not m:
            return None

        value = float(m.group(1).replace(",", ""))
        unit = m.group(2)
        if unit == "万":
            value *= 10_000
        elif unit == "亿":
            value *= 100_000_000
        return int(value)

    def _to_int(self, text):
        """'1,200' -> 1200；'1.5' -> 1（实力/体力均为整数语义）。"""
        return int(float(str(text).replace(",", "")))

    def _safe_box(self, element):
        """
        bbox 定位。框架 get_box_by_name 找不到类别时抛 ValueError，
        这里转为 None，交由上层处理。
        """
        try:
            return self.task.get_box_by_name(element.resource_id)
        except ValueError:
            return None
