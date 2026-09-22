class PopupHandler:

    CLOSE_BUTTONS = [
        "button_close_1",
        "button_close_2",
        "button_close_3",
    ]

    def __init__(self, matcher, action):
        self.matcher = matcher
        self.action = action

    def close_all(self, max_rounds=10):

        for _ in range(max_rounds):

            closed = False

            for button in self.CLOSE_BUTTONS:

                if self.matcher.exists(button):

                    self.action.click_image(button)

                    self.action.wait(0.5)

                    closed = True

                    # 一个循环只关闭一个
                    # 然后重新扫描
                    break

            if not closed:
                break

        return True