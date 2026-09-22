class SceneElement:

    def __init__(
        self,
        name: str,
        resource_id: str,
    ):
        self.name = name
        self.resource_id = resource_id

    def __repr__(self):
        return f"SceneElement({self.name})"


# ============================================================
# Scene
# ============================================================

# 判断是否进入游戏主界面，没有被活动页面遮挡
AREA_MAIN = SceneElement(
    name="area_main",
    resource_id="area_main",
)

# 主城 → 荒野
BUTTON_GOTO_WORLDMAP = SceneElement(
    name="跳转世界标签",
    resource_id="button_goto_worldmap",
)

# 荒野
WILDERNESS = SceneElement(
    name="跳转主堡标签",
    resource_id="wilderness",
)


# ============================================================
# Popup
# ============================================================

BUTTON_CLOSE_1 = SceneElement(
    name="button_close_1",
    resource_id="button_close_1",
)

BUTTON_CLOSE_2 = SceneElement(
    name="button_close_2",
    resource_id="button_close_2",
)

BUTTON_CLOSE_3 = SceneElement(
    name="button_close_3",
    resource_id="button_close_3",
)


# ============================================================
# Giant Beast
# ============================================================

# 荒野 → 搜索资源
BUTTON_SEARCH_RESOURCES = SceneElement(
    name="荒野 → 搜索资源button_search_resources",
    resource_id="button_search_resources",
)

# 搜索结果中的巨兽
GIANT_BEAST = SceneElement(
    name="搜索结果中的巨兽giantbeast_1",
    resource_id="giantbeast_1",
)

# 巨兽页面 → 集结
BUTTON_RALLY_GIANT_BEAST = SceneElement(
    name="巨兽页面 → 集结button_rallygiantbeast",
    resource_id="button_rallygiantbeast",
)

# 集结队伍
TEAM_HUNTING = SceneElement(
    name="team_hunting",
    resource_id="team_hunting",
)

# 发起集结 / 出征
BUTTON_HUNT_EXPEDITION = SceneElement(
    name="button_huntexpedition",
    resource_id="button_huntexpedition",
)
# 发起集结確定
BUTTON_STARTRALLY = SceneElement(
    name="button_startrally",
    resource_id="button_startrally",
)

# 搜索资源
BUTTON_RESOURCESEARCH = SceneElement(
    name="button_resourcesearch",
    resource_id="button_resourcesearch",
)


