# =============================================================================
# registrations/ —— 所有业务任务的注册集中地
# =============================================================================
# 新增一种业务时，只需在本目录下放一个 .py 文件，
# 里面调用 GenericQueueTask.register_task_type(...)。
# 不需要修改本 __init__.py，也不需要修改任何其他文件。
#
# 机制：
#   用 pkgutil 扫描本目录下所有非下划线开头的模块并 import，
#   模块顶层代码会在首次 import 时执行一次，完成注册。
# =============================================================================

import importlib
import pkgutil

for _finder, _name, _ispkg in pkgutil.iter_modules(__path__):
    if _name.startswith("_") or _ispkg:
        continue
    importlib.import_module(f"{__name__}.{_name}")