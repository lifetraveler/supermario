from enum import Enum


class SceneState(str, Enum):

    UNKNOWN = "unknown"
    NORMAL = "normal"

    LOADING = "loading"

    PREPARE = "prepare"

    FIGHTING = "fighting"

    WIN = "win"

    LOSE = "lose"

    BUILDING = "building"

    UPGRADING = "upgrading"

    SEARCHING = "searching"

    MARCHING = "marching"

    GATHERING = "gathering"

    REWARD = "reward"

    ERROR = "error"
