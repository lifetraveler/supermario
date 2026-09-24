class RallyConfig:
    """
    集结配置：选择队伍、英雄调整、兵力调整。
    纯配置对象，不涉及流程编排。
    """

    def __init__(self, task):
        self.task = task

        # 要选择的队伍元素（SceneElement）
        self.team_element = None

        # 英雄调整步骤（list[SceneElement]），按顺序执行
        self.hero_elements = []

        # 兵力调整（预留）
        self.troop_adjust = None

    # --------------------------------------------------------
    # 步骤
    # --------------------------------------------------------

    def select_team(self) -> bool:
        if self.team_element is None:
            self.task.log_info("RallyConfig: 未配置队伍，跳过")
            return True
        return self.task._wait_and_click(
            self.team_element,
            name=f"选择集结队伍: {self.team_element.name}",
        )

    def adjust_heroes(self) -> bool:
        if not self.hero_elements:
            return True
        for element in self.hero_elements:
            if not self.task._wait_and_click(
                element,
                name=f"调整英雄: {element.name}",
            ):
                return False
        return True

    def adjust_troops(self) -> bool:
        # TODO: 兵力调整
        return True

    # --------------------------------------------------------
    # 组合
    # --------------------------------------------------------

    def apply(self) -> bool:
        if not self.select_team():
            return False
        if not self.adjust_heroes():
            return False
        if not self.adjust_troops():
            return False
        return True