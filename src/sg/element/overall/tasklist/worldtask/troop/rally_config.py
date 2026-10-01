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
        # 并发>1 时跳过选队：省体力队伍（如 TEAM_HUNTING）只有一支，
        # 多路集结共用同一步会互相覆盖配置；并发=1 才按配置选队。
        max_active = getattr(self.task, "max_active", 1) or 1
        if max_active > 1:
            self.task.log_info(
                f"RallyConfig: 并发={max_active} > 1，跳过选择队伍"
            )
            return True
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