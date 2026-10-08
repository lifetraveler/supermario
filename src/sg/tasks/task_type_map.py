# -*- coding: utf-8 -*-
"""
task_type_map —— type 短 id → 任务类 的代码侧映射。

设计意图（Skill A 第七节：注册机制的 JSON 化）：
  - task_config.json 里只写稳定短 id（type），不写 import 路径——
    字符串路径写错要到运行时才爆，映射表写错在 import 时就爆。
  - 新增一种任务：写任务类 + 在 TASK_TYPE_MAP 加一行 +
    在 configs/task_config.json 的 task_types 里加定义段。
  - 领域层/加载层绝不 import 本表以外的任何业务任务类。

映射 key 必须与 task_config.json 的 task_types[].type 完全一致。
"""

from src.sg.tasks.daily.ClaimMailRewardTask import ClaimMailRewardTask
from src.sg.tasks.daily.ClaimStaminaTask import ClaimStaminaTask
from src.sg.tasks.daily.LeaderRewardTask import LeaderRewardTask
from src.sg.tasks.daily.PetTreasureHuntTask import PetTreasureHuntTask
from src.sg.tasks.daily.PunishClaimTask import PunishClaimTask
from src.sg.tasks.island.GatherIslandWaterTask import GatherIslandWaterTask
from src.sg.tasks.league.LeagueRewardBoxTask import LeagueRewardBoxTask
from src.sg.tasks.league.LeagueTechDonateTask import LeagueTechDonateTask
from src.sg.tasks.world.CesareFightTask import CesareFightTask
from src.sg.tasks.world.GatherResourceTask import GatherResourceTask
from src.sg.tasks.world.HuntBeastTask import HuntBeastTask
from src.sg.tasks.world.HuntEliteRebelsTask import HuntEliteRebelsTask
from src.sg.tasks.world.HuntMonsterTask import HuntMonsterTask
from src.sg.tasks.world.HuntScareWolfTask import HuntScareWolfTask
from src.sg.tasks.world.WatchTowerEventTask import WatchTowerEventTask

TASK_TYPE_MAP = {
    "Cesare Fight":          CesareFightTask,
    "Claim Mail Reward":     ClaimMailRewardTask,
    "Claim Stamina":         ClaimStaminaTask,
    "Gather Island Water":   GatherIslandWaterTask,
    "Gather Resource Troop": GatherResourceTask,
    "Hunt Beast Troop":      HuntBeastTask,
    "Hunt Elite Rebels Troop": HuntEliteRebelsTask,
    "Hunt Monster Troop":    HuntMonsterTask,
    "Hunt Scare Wolf Troop": HuntScareWolfTask,
    "Leader Reward":         LeaderRewardTask,
    "League Tech Donate":    LeagueTechDonateTask,
    "League Reward Box":     LeagueRewardBoxTask,
    "Pet Treasure Hunt":     PetTreasureHuntTask,
    "Punish Claim":          PunishClaimTask,
    "Watch Tower Event":     WatchTowerEventTask,
}
