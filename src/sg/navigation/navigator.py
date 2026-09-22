from src.sg.scene.scene_type import SceneType


class Navigator:

    def __init__(self, matcher, action, scene_detector):
        self.matcher = matcher
        self.action = action
        self.scene_detector = scene_detector

    def goto_worldmap(self):

        scene = self.scene_detector.detect()

        # 已经在世界地图
        if scene.type == SceneType.WILDERNESS:
            return True

        # 主堡 → 世界地图
        if scene.type == SceneType.MAIN:

            if self.matcher.exists("button_goto_worldmap"):
                self.action.click_image(
                    "button_goto_worldmap"
                )

                self.action.wait(1)

                # 再次确认
                scene = self.scene_detector.detect()

                return scene.type == SceneType.WILDERNESS

        return False