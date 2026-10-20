from src.sg.scene.scene import Scene
from src.sg.scene.scene_type import SceneType
from src.sg.scene.scene_state import SceneState

from src.sg.scene.elements import (
    AREA_MAIN,
    BUTTON_GOTO_WORLDMAP,
    WILDERNESS,
    ISLAND_AREA_SYMBOL
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
        
        island_area_symbol = self._find(ISLAND_AREA_SYMBOL)

        if island_area_symbol is not None:
            features[ISLAND_AREA_SYMBOL.name] = island_area_symbol

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


        if island_area_symbol is not None:
            return Scene(
                scene_type=SceneType.ISLAND_AREA_SYMBOL,
                state=SceneState.NORMAL,
                confidence=island_area_symbol.confidence,
                features=features,
            )

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