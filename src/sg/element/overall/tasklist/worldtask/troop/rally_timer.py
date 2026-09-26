import re


class RallyTimer:
    """
    集结时间预估。
    负责：
      - 解析 OCR 时间文本
      - 读取行军时间
      - 计算总等待时间
    状态：last_march_seconds / last_rally_seconds
    """

    def __init__(self, task,**key):
        self.task = task

        # 集结上限 1分30秒（满集结会立刻行军）
        self.rally_max_wait = 90
        # 集结上限 1分30秒（满集结会立刻行军）
        self.rally_default_wait = 90
        

        # 行军时间缓冲
        self.march_time_buffer = 10

        # 无法识别行军时间时的兜底
        self.default_march_seconds = 300

        # 运行时状态
        self.last_march_seconds = 0.0
        self.last_rally_seconds = 0.0

    # --------------------------------------------------------
    # 时间文本解析
    # --------------------------------------------------------

    def parse_time_text(self, text):
        """
        把 OCR 文本解析成秒。
        支持: '90', '1:30', '1:30:00', '1小时30分', '1小时30分20秒', '90秒'
        """
        if text is None:
            return None

        text = str(text).strip()
        if not text:
            return None

        if text.isdigit():
            return int(text)

        if ":" in text or "：" in text:
            normalized = text.replace("：", ":")
            parts = normalized.split(":")
            try:
                nums = [int(p) for p in parts]
            except ValueError:
                return None
            if len(nums) == 2:
                return nums[0] * 60 + nums[1]
            if len(nums) == 3:
                return nums[0] * 3600 + nums[1] * 60 + nums[2]
            return None

        total = 0
        hour = re.search(r"(\d+)\s*[时小]", text)
        minute = re.search(r"(\d+)\s*分", text)
        second = re.search(r"(\d+)\s*秒", text)
        if hour:
            total += int(hour.group(1)) * 3600
        if minute:
            total += int(minute.group(1)) * 60
        if second:
            total += int(second.group(1))
        return total if total > 0 else None

    # --------------------------------------------------------
    # OCR 读取行军时间
    # --------------------------------------------------------

    def read_march_time(self, confirm_box):
        """
        从确认出征按钮上读取行军时间，返回秒数；未识别返回 None。
        同时会写入 self.last_march_seconds。
        """
        try:
            results = self.task.ocr(
                box=confirm_box
            )
            match=r"\d{1,2}\s*[:：]\s*\d{1,2}\s*[:：]\s*\d{1,2}"
        except Exception as e:
            self.task.log_info(f"OCR 行军时间异常: {e}")
            results = None

        if not results:
            self.last_march_seconds = float(self.default_march_seconds)
            self.task.log_info(
                f"未识别到行军时间，使用默认值: "
                f"{self.default_march_seconds}s"
            )
            return None

        text = results[0].name
        self.task.log_info(f"OCR 行军时间原文: {text}")
        seconds = self.parse_time_text(text)
        if seconds is not None and seconds > 0:
            self.last_march_seconds = float(seconds*2)
            self.task.log_info(f"识别到行军时间: {seconds*2}s")
        else:
            self.last_march_seconds = float(self.default_march_seconds)
            self.task.log_info(
                f"解析失败，使用默认值: {self.default_march_seconds}s"
            )
        return seconds*2

    # --------------------------------------------------------
    # 计算总等待
    # --------------------------------------------------------

    def total_wait(self,extra_config) -> float:
        rally = 0.0
        if self.last_rally_seconds > 0:
            rally = min(
                float(self.last_rally_seconds),
                float(self.rally_max_wait),
            )
        if(rally==0.0):
            rally_wait = getattr(extra_config, 'rally_config_wait', self.rally_default_wait)
            rally = max(float(self.rally_default_wait), float(rally_wait))
        march = max(0.0, float(self.last_march_seconds))
        total = rally + (march) + float(self.march_time_buffer)

        self.task.log_info(
            f"等待计算: 集结={rally}s, 行军={march}s, "
            f"缓冲={self.march_time_buffer}s, 共={total}s"
        )
        return total