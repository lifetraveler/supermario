from src.tasks.sg.SGBaseTask import SGBaseTask


class SGDailyTask(SGBaseTask):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.name = "SG 每日任务"
        self.description = "自动完成游戏每日任务"

    def run(self):
        self.log_info("开始执行每日任务")

        # 这里以后调用真正的 SG 自动化逻辑
        # self.daily.sign_in()
        # self.daily.collect_reward()
        # self.daily.check_mail()

        self.log_info("每日任务执行完成")