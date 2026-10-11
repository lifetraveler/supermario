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

        # 队伍选择区域（出征页顶部预设标签行），相对坐标 (x, y, to_x, to_y)。
        # 无独立特征标注，OCR 按相对区域识别；分辨率差异时在此调整。
        self.team_select_area = (0.03, 0.07, 0.82, 0.13)

        # 预留开关：OCR 命中队伍名后是否自动点击选队。
        # 本版本固定 False（一律沿用系统默认队伍）；实机验证 OCR 命中
        # 可靠后置 True 启用"有"分支。
        self.team_select_click_enabled = False

    # --------------------------------------------------------
    # 步骤
    # --------------------------------------------------------

    def select_team(self, team_group_name=None) -> bool:
        # 并发>1 时跳过选队：省体力队伍（如 TEAM_HUNTING）只有一支，
        # 多路集结共用同一步会互相覆盖配置；并发=1 才按配置选队。
        max_active = getattr(self.task, "max_active", 1) or 1
        if max_active > 1:
            self.task.log_info(
                f"RallyConfig: 并发={max_active} > 1，跳过选择队伍"
            )
            return True
        # 按队伍名 OCR 选队（Gather 派遣路径）
        if team_group_name:
            return self._select_team_by_name(team_group_name)
        # 旧路径：模板特征点击（巨兽集结等任务注入 team_element）
        if self.team_element is None:
            self.task.log_info("RallyConfig: 未配置队伍，跳过")
            return True
        return self.task._wait_and_click(
            self.team_element,
            name=f"选择集结队伍: {self.team_element.name}",
        )

    def _select_team_by_name(self, team_group_name) -> bool:
        """
        OCR 队伍选择区域，按 team_group_name 匹配预设队伍标签。

        命中且 team_select_click_enabled=True → 点击选中（预留的"有"分支）；
        未命中 / 开关关闭 → 不更改，沿用系统默认队伍（本版本路径）。
        无论哪种结果都返回 True：选不到队不算失败，按默认继续。
        """
        task = self.task
        x, y, to_x, to_y = self.team_select_area
        area = task.box_of_screen(x, y, to_x, to_y)
        try:
            results = task.ocr(box=area)
        except Exception as e:
            task.log_info(f"OCR 队伍选择区域异常: {e}，沿用系统默认队伍")
            return True

        if not results:
            task.log_info("队伍选择区域 OCR 无结果，沿用系统默认队伍")
            return True

        query = str(team_group_name).strip().lower()
        # 精确命中优先（OCR 可能把相邻标签拆成含查询词的碎片，
        # 如查 "cj" 误匹配 "xcj"），无精确命中再退回子串匹配。
        matched = sorted(
            (b for b in results
             if query and query in (b.name or "").lower()),
            key=lambda b: ((b.name or "").strip().lower() != query, b.x),
        )
        if not matched:
            texts = " ".join((b.name or "") for b in results)
            task.log_info(
                f"未匹配到队伍 [{team_group_name}]（区域文本: {texts}），"
                f"沿用系统默认队伍"
            )
            return True

        target = matched[0]
        if not self.team_select_click_enabled:
            task.log_info(
                f"匹配到队伍 [{team_group_name}]: {target.name}"
                f"（预留分支未启用，沿用系统默认队伍）"
            )
            return True

        task.log_info(f"匹配到队伍 [{team_group_name}]: {target.name}，点击选中")
        if not task.click(target):
            return False
        task._sleep(0.5)
        return True

    def adjust_heroes(self, team_hero=None) -> bool:
        # 本版本不调整英雄：team_hero 仅预留；hero_elements 旧路径保留。
        if team_hero:
            self.task.log_info(
                "RallyConfig: team_hero 本版本不生效，跳过英雄调整"
            )
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

    def apply(self, team_group_name=None, team_hero=None) -> bool:
        if not self.select_team(team_group_name):
            return False
        if not self.adjust_heroes(team_hero):
            return False
        if not self.adjust_troops():
            return False
        return True