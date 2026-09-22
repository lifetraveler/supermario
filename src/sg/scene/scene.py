from .scene_type import SceneType
from .scene_state import SceneState


class Scene:

    def __init__(
        self,
        scene_type: SceneType = SceneType.UNKNOWN,
        state: SceneState = SceneState.UNKNOWN,
        confidence: float = 0.0,
        features=None,
    ):
        self.type = scene_type
        self.state = state
        self.confidence = confidence
        self.features = features or {}

    @property
    def is_main(self):
        return self.type == SceneType.MAIN

    @property
    def is_wilderness(self):
        return self.type == SceneType.WILDERNESS

    @property
    def is_unknown(self):
        return self.type == SceneType.UNKNOWN

    def has_feature(self, name: str) -> bool:
        return self.features.get(name, False)

    def __repr__(self):
        return (
            f"Scene("
            f"type={self.type.value}, "
            f"state={self.state.value}, "
            f"confidence={self.confidence:.2f}, "
            f"features={self.features}"
            f")"
        )