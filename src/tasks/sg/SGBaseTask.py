from src.tasks.MyBaseTask import MyBaseTask
from src.sg.scene.scene_detector import SceneDetector

class SGBaseTask(MyBaseTask):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.name = "SG Base Task"
        self.description = "Base task for SG game automation."
        self.scene_detector = SceneDetector(self)
 
    @property
    def runtime_locale(self) -> str | None:
        """统一获取运行时 UI 语言。"""
        executor = getattr(self, "executor", None)
        locale_obj = getattr(executor, "locale", None)
        if locale_obj is None:
            return None
        if hasattr(locale_obj, "name"):
            try:
                name_attr = getattr(locale_obj, "name")
                value = name_attr() if callable(name_attr) else name_attr
                if value:
                    return str(value)
            except Exception:
                pass
        return str(locale_obj)        