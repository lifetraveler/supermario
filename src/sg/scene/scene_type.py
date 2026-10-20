from enum import Enum


class SceneType(str, Enum):
    UNKNOWN = "unknown"

    LOGIN = "login"

    CITY = "city"

    WORLD_MAP = "world_map"
    
    ISLAND_AREA_SYMBOL = "island_area_symbol"

    TOWER_DEFENSE = "tower_defense"

    MARCH = "march"

    HERO = "hero"

    TROOP = "troop"

    TECH = "tech"

    ALLIANCE = "alliance"

    QUEST = "quest"

    ACTIVITY = "activity"

    MAIL = "mail"

    SHOP = "shop"

    REWARD = "reward"
