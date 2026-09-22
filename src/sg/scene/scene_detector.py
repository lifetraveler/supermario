from src.sg.scene.scene import Scene
from src.sg.scene.scene_type import SceneType
from src.sg.scene.scene_state import SceneState

from src.sg.scene.elements import (
    AREA_MAIN,
    BUTTON_GOTO_WORLDMAP,
    WILDERNESS,
)


class SceneDetector:

    def __init__(self, task):
        self.task = task

    def _find(self, element, threshold=0.8):
        return self.task.find_one(
            feature_name=element.resource_id,
            threshold=threshold,
        )

    def detect(self) -> Scene:

        features = {}

        area_main = self._find(AREA_MAIN)

        if area_main is not None:
            features[AREA_MAIN.name] = area_main

        goto_wilderness = self._find(
            BUTTON_GOTO_WORLDMAP
        )

        if goto_wilderness is not None:
            features[BUTTON_GOTO_WORLDMAP.name] = (
                goto_wilderness
            )

        wilderness = self._find(WILDERNESS)

        if wilderness is not None:
            features[WILDERNESS.name] = wilderness

        # --------------------------------------------------
        # Wilderness
        # --------------------------------------------------

        if wilderness is not None:
            return Scene(
                scene_type=SceneType.WORLD_MAP,
                state=SceneState.NORMAL,
                confidence=wilderness.confidence,
                features=features,
            )

        # --------------------------------------------------
        # Main
        # --------------------------------------------------

        if area_main is not None:
            return Scene(
                scene_type=SceneType.CITY,
                state=SceneState.NORMAL,
                confidence=area_main.confidence,
                features=features,
            )

        # --------------------------------------------------
        # Unknown
        # --------------------------------------------------

        return Scene(
            scene_type=SceneType.UNKNOWN,
            state=SceneState.UNKNOWN,
            confidence=0.0,
            features=features,
        )