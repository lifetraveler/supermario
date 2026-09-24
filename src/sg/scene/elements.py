class SceneElement:

    def __init__(
        self,
        name: str,
        resource_id: str,
        desc: str,
    ):
        self.name = name
        self.resource_id = resource_id
        self.desc = desc

    def __repr__(self):
        return f"SceneElement({self.name})"


# ============================================================
# Scene
# ============================================================

# 判断是否进入游戏主界面，没有被活动页面遮挡
AREA_MAIN = SceneElement(
    name="area_main",
    resource_id="global_area_main",
    desc="判断是否进入游戏主界面，没有被活动页面遮挡",
)

# 主城 → 荒野
BUTTON_GOTO_WORLDMAP = SceneElement(
    name="跳转世界标签",
    resource_id="city_button_goto_worldmap",
    desc="主城 → 荒野，跳转世界标签",
)

# 荒野
WILDERNESS = SceneElement(
    name="跳转主堡标签",
    resource_id="wilderness",
    desc="荒野，跳转主堡标签",
)


# ============================================================
# Popup
# ============================================================

BUTTON_CLOSE_1 = SceneElement(
    name="button_close_1",
    resource_id="activity_button_close_1",
    desc="活动弹窗关闭按钮1",
)

BUTTON_CLOSE_2 = SceneElement(
    name="button_close_2",
    resource_id="activity_button_close_2",
    desc="活动弹窗关闭按钮2",
)

BUTTON_CLOSE_3 = SceneElement(
    name="button_close_3",
    resource_id="activity_button_close_3",
    desc="活动弹窗关闭按钮3",
)


# ============================================================
# Giant Beast
# ============================================================

# 荒野 → 搜索资源
BUTTON_SEARCH_RESOURCES = SceneElement(
    name="荒野 → 搜索资源button_search_resources",
    resource_id="button_search_resources",
    desc="荒野 → 搜索资源按钮",
)

# 搜索结果中的巨兽
GIANT_BEAST = SceneElement(
    name="搜索结果中的巨兽giantbeast_1",
    resource_id="giantbeast_1",
    desc="搜索结果中的巨兽",
)

# 巨兽页面 → 集结
BUTTON_RALLY_GIANT_BEAST = SceneElement(
    name="巨兽页面 → 集结button_rallygiantbeast",
    resource_id="wolrd_resource_monster_button_rallygiantbeast",
    desc="巨兽页面 → 集结按钮",
)

# 发起集结確定
BUTTON_STARTRALLY = SceneElement(
    name="button_startrally",
    resource_id="world_resource_monster_button_startrally",
    desc="发起集结确定按钮",
)

# 集结队伍
TEAM_HUNTING = SceneElement(
    name="team_hunting",
    resource_id="team_prepare_hunting_less_sinew",
    desc="集结队伍，狩猎队伍",
)

# 发起集结 / 出征
BUTTON_HUNT_EXPEDITION = SceneElement(
    name="button_huntexpedition",
    resource_id="team_prepare_button_huntexpedition",
    desc="发起集结 / 出征按钮",
)


# 搜索资源
BUTTON_RESOURCESEARCH = SceneElement(
    name="button_resourcesearch",
    resource_id="world_search_button_resource_search",
    desc="搜索资源按钮",
)


# ============================================================
# Resource Bar Anchors（资源栏右端锚点）
# ============================================================

# 资源栏右端：石头
WORLD_ICON_RESOURCE_STONE = SceneElement(
    name="world_icon_resource_stone",
    resource_id="wolrd_icon_resource_stone",
    desc="资源栏右端：石头",
)

# 资源栏右端：铁矿
WORLD_ICON_RESOURCE_IRON = SceneElement(
    name="world_icon_resource_iron",
    resource_id="wolrd_icon_resource_iron",
    desc="资源栏右端：铁矿",
)

# 集结路上时间
TEAM_TIME_ON_THE_WAY = SceneElement(
    name="集结路上时间",
    resource_id="team_time_on_the_way",
    desc="集结路上时间",
)

# 查询体力入口
OVERALL_STAMINA = SceneElement(
    name="查询体力入口",
    resource_id="overall_stamina",
    desc="查询体力入口",
)

# 当前体力box
OVERALL_STAMINA_CURRENT = SceneElement(
    name="当前体力box",
    resource_id="overall_stamina_current",
    desc="当前体力box",
)


# ============================================================
# 补充：根据 COCO categories 更新
# 已排除上方已定义过的 resource_id
# ============================================================

# id: 1
WORLD_FEATURE_MARCH_WORLD = SceneElement(
    name="world_feature_march_world",
    resource_id="world_feature_march_world",
    desc="世界地图行军功能",
)

# id: 4
MAINCASTLE = SceneElement(
    name="maincastle",
    resource_id="maincastle",
    desc="主堡",
)

# id: 6
WORLD_ICON_RESOURCE_MONSTER = SceneElement(
    name="wolrd_icon_resource_monster",
    resource_id="wolrd_icon_resource_monster",
    desc="世界资源图标：怪物",
)

# id: 7
WILDERNESS_TARGET = SceneElement(
    name="wildernesstarget",
    resource_id="wildernesstarget",
    desc="荒野目标",
)

# id: 8
WORLD_ICON_RESOURCE_BEAST = SceneElement(
    name="wolrd_icon_resource_beast",
    resource_id="wolrd_icon_resource_beast",
    desc="世界资源图标：野兽",
)

# id: 9
MARCH_TEAM_NUM = SceneElement(
    name="march_team_num",
    resource_id="march_team_num",
    desc="行军队伍数量",
)

# id: 10
TEAM_PREPARE_TIP_TIME_HUNTERWAY = SceneElement(
    name="team_prepare_tip_time_hunterway",
    resource_id="team_prepare_tip_time_hunterway",
    desc="队伍准备提示：狩猎路程时间",
)

# id: 12
TEAM_PREPARE_BEARPIT2 = SceneElement(
    name="team_prepare_bearpit2",
    resource_id="team_prepare_bearpit2",
    desc="队伍准备：熊陷阱2",
)

# id: 13
TEAM_PREPARE_BEARPIT3 = SceneElement(
    name="team_prepare_bearpit3",
    resource_id="team_prepare_bearpit3",
    desc="队伍准备：熊陷阱3",
)

# id: 16
TEAM_PREPARE_BEARPIT1 = SceneElement(
    name="team_prepare_bearpit1",
    resource_id="team_prepare_bearpit1",
    desc="队伍准备：熊陷阱1",
)

# id: 17
WORLD_RESOURCE_BUTTON_ADD = SceneElement(
    name="wolrd_resource_button_add",
    resource_id="wolrd_resource_button_add",
    desc="世界资源按钮：添加",
)

# id: 18
GOBAL_TAG_MARCH = SceneElement(
    name="gobal_tag_march",
    resource_id="gobal_tag_march",
    desc="全局标签：行军",
)

# id: 22
CITY_BUTTON_ANIMAL_BUFFER = SceneElement(
    name="city_button_animal_buffer",
    resource_id="city_button_animal_buffer",
    desc="城市按钮：动物增益",
)

# id: 23
GLOBAL_TASK_CITY_BUTTON_COMPLETE = SceneElement(
    name="global_task_city_button_complete",
    resource_id="global_task_city_button_complete",
    desc="全局任务城市按钮：完成",
)

# id: 24
CITY_EVENT_ENVELOPE = SceneElement(
    name="city_event_envelope",
    resource_id="city_event_envelope",
    desc="城市事件：信封",
)

# id: 25
CITY_EVENT_FIGHT = SceneElement(
    name="city_event_fight",
    resource_id="city_event_fight",
    desc="城市事件：战斗",
)

# id: 26
GLOBAL_TAG_LEAGUE = SceneElement(
    name="global_tag_league",
    resource_id="global_tag_league",
    desc="全局标签：联盟",
)

# id: 27
GOBAL_TAG_SHOP = SceneElement(
    name="gobal_tag_shop",
    resource_id="gobal_tag_shop",
    desc="全局标签：商店",
)

# id: 28
GOBAL_TAG_BAG = SceneElement(
    name="gobal_tag_bag",
    resource_id="gobal_tag_bag",
    desc="全局标签：背包",
)

# id: 29
GOBAL_TAG_HERO = SceneElement(
    name="gobal_tag_hero",
    resource_id="gobal_tag_hero",
    desc="全局标签：英雄",
)

# id: 30
FEATURE_MARCH = SceneElement(
    name="feature_march",
    resource_id="feature_march",
    desc="功能：行军",
)

# id: 31
CITY_BUTTON_SMS = SceneElement(
    name="city_button_sms",
    resource_id="city_button_sms",
    desc="城市按钮：消息",
)

# id: 32
CITY_BUTTON_BUFFER = SceneElement(
    name="city_button_buffer",
    resource_id="city_button_buffer",
    desc="城市按钮：增益",
)

# id: 33
GLOBAL_EVENT_TASK_NEED_HANDLE = SceneElement(
    name="global_event_task_need_handle",
    resource_id="global_event_task_need_handle",
    desc="全局事件任务：需要处理",
)

# id: 37
TEAM_PREPARE_ALLIANCEMINE = SceneElement(
    name="team_prepare_alliancemine",
    resource_id="team_prepare_alliancemine",
    desc="队伍准备：联盟矿",
)

# id: 38
WORLD_RESOURCE_BUTTON_NEGTIVE = SceneElement(
    name="wolrd_resource_button_negtive",
    resource_id="wolrd_resource_button_negtive",
    desc="世界资源按钮：减少",
)

# id: 39
ISLAND_AREA_SYMBOL = SceneElement(
    name="island_area_symbol",
    resource_id="island_area_symbol",
    desc="岛屿区域标志",
)

# id: 40
LEAGUE_TECH_ENTRY = SceneElement(
    name="league_tech_entry",
    resource_id="league_tech_entry",
    desc="联盟科技入口",
)

# id: 41
GLOBAL_BUTTON_QUICKHELP = SceneElement(
    name="global_button_quickhelp",
    resource_id="global_button_quickhelp",
    desc="全局按钮：快速帮助",
)

# id: 43
CITY_BUILDING_BUTTON_CLICK_HERE = SceneElement(
    name="city_building_button_click_here",
    resource_id="city_building_button_click_here",
    desc="城市建筑按钮：点击这里",
)

# id: 44
CITY_BUILDING_BUTTON_TRAIN = SceneElement(
    name="city_building_button_train",
    resource_id="city_building_button_train",
    desc="城市建筑按钮：训练",
)

# id: 45
ISLAND_BUTTON_GATHER_WATER = SceneElement(
    name="island_button_gather_water",
    resource_id="island_button_gather_water",
    desc="岛屿按钮：采集水",
)

# id: 46
SOLDIER_BUTTON_TRAINNOW = SceneElement(
    name="soldier_button_trainnow",
    resource_id="soldier_button_trainnow",
    desc="士兵按钮：立即训练",
)

# id: 47
SOLDIER_TAG_ARCHER = SceneElement(
    name="soldier_tag_archer",
    resource_id="soldier_tag_archer",
    desc="士兵标签：弓兵",
)

# id: 48
SOLDIER_TAG_INFANTRY = SceneElement(
    name="soldier_tag_infantry",
    resource_id="soldier_tag_infantry",
    desc="士兵标签：步兵",
)

# id: 49
SOLDIER_TAG_CAVALRY = SceneElement(
    name="soldier_tag_cavalry",
    resource_id="soldier_tag_cavalry",
    desc="士兵标签：骑兵",
)

# id: 50
GLOBAL_BUTTON_TASKLIST = SceneElement(
    name="global_button_tasklist",
    resource_id="global_button_tasklist",
    desc="全局按钮：任务列表",
)

# id: 51
LEAGUE_SHOP_ENTRY = SceneElement(
    name="league_shop_entry",
    resource_id="league_shop_entry",
    desc="联盟商店入口",
)

# id: 52
LEAGUE_HELP_ENTRY = SceneElement(
    name="league_help_entry",
    resource_id="league_help_entry",
    desc="联盟帮助入口",
)

# id: 53
LEAGUE_TECH_UPDATE = SceneElement(
    name="league_tech_update",
    resource_id="league_tech_update",
    desc="联盟科技升级",
)

# id: 54
LEAGUE_TECH_UPDATE_DONATE = SceneElement(
    name="league_tech_update_donate",
    resource_id="league_tech_update_donate",
    desc="联盟科技升级：捐赠",
)

# id: 55
PET_SYMBOL_TREASURE_HUNT = SceneElement(
    name="pet_symbol_treasure_hunt",
    resource_id="pet_symbol_treasure_hunt",
    desc="宠物标志：寻宝",
)

# id: 56
WORLD_EVENT_FIRED = SceneElement(
    name="world_event_fired",
    resource_id="world_event_fired",
    desc="世界事件：火焰",
)

# id: 57
WORLD_EVENT_BEAST_2 = SceneElement(
    name="world_event_beast_2",
    resource_id="world_event_beast_2",
    desc="世界事件：野兽2",
)

# id: 58
WORLD_EVENT_BEAST_1 = SceneElement(
    name="world_event_beast_1",
    resource_id="world_event_beast_1",
    desc="世界事件：野兽1",
)

# id: 59
WORLD_EVENT_BEAST_3 = SceneElement(
    name="world_event_beast_3",
    resource_id="world_event_beast_3",
    desc="世界事件：野兽3",
)

# id: 60
WORLD_EVENT_CONQ_1 = SceneElement(
    name="world_event_conq_1",
    resource_id="world_event_conq_1",
    desc="世界事件：征服1",
)

# id: 61
WORLD_EVENT_CONQ_2 = SceneElement(
    name="world_event_conq_2",
    resource_id="world_event_conq_2",
    desc="世界事件：征服2",
)

# id: 62
WORLD_EVENT_ZHANGPENG_3 = SceneElement(
    name="world_event_zhangpeng_3",
    resource_id="world_event_zhangpeng_3",
    desc="世界事件：帐篷3",
)

# id: 63
WORLD_EVENT_ZHANGPENG_1 = SceneElement(
    name="world_event_zhangpeng_1",
    resource_id="world_event_zhangpeng_1",
    desc="世界事件：帐篷1",
)

# id: 64
WORLD_EVENT_BUTTON_TAKEOVER = SceneElement(
    name="world_event_button_takeover",
    resource_id="world_event_button_takeover",
    desc="世界事件按钮：接管",
)

# id: 65
WORLD_ICON_RESOURCE_SCAREWOLF = SceneElement(
    name="wolrd_icon_resource_scarewolf",
    resource_id="wolrd_icon_resource_scarewolf",
    desc="世界资源图标：稻草狼",
)

# id: 66
WORLD_ICON_RESOURCE_BREAD = SceneElement(
    name="wolrd_icon_resource_bread",
    resource_id="wolrd_icon_resource_bread",
    desc="世界资源图标：面包",
)

# id: 67
WORLD_ICON_RESOURCE_WOOD = SceneElement(
    name="wolrd_icon_resource_wood",
    resource_id="wolrd_icon_resource_wood",
    desc="世界资源图标：木材",
)

# id: 70
WORLD_EVENT_ICON_CITYGATHERWATER = SceneElement(
    name="world_event_icon_citygatherwater",
    resource_id="world_event_icon_citygatherwater",
    desc="世界事件图标：城市采集水",
)

# id: 74
WORLD_EVENT_CONQ_3 = SceneElement(
    name="world_event_conq_3",
    resource_id="world_event_conq_3",
    desc="世界事件：征服3",
)

# id: 75
ITEM_SCARE_WOLF_CLAW = SceneElement(
    name="item_scare_wolf_claw",
    resource_id="item_scare_wolf_claw",
    desc="物品：稻草狼爪",
)

# id: 76
ITEM_SCARE_WOLF_CLAW_BUTTON_USE = SceneElement(
    name="item_scare_wolf_claw_button_use",
    resource_id="item_scare_wolf_claw_button_use",
    desc="物品：稻草狼爪按钮：使用",
)

# id: 77
GOBAL_TAG_BAG_EQUIPMENT = SceneElement(
    name="gobal_tag_bag_equipment",
    resource_id="gobal_tag_bag_equipment",
    desc="全局标签：背包-装备",
)

# id: 78
GOBAL_TAG_BAG_OTHER = SceneElement(
    name="gobal_tag_bag_other",
    resource_id="gobal_tag_bag_other",
    desc="全局标签：背包-其他",
)

# id: 79
WORLD_RESOURCE_SCARE_WOLF = SceneElement(
    name="world_resource_scare_wolf",
    resource_id="world_resource_scare_wolf",
    desc="世界资源：稻草狼",
)

# id: 80
WORLD_RESOURCE_SCARE_WOLF_1 = SceneElement(
    name="world_resource_scare_wolf_1",
    resource_id="world_resource_scare_wolf_1",
    desc="世界资源：稻草狼1",
)