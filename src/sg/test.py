#!/usr/bin/env python3
"""
多用户应用启动脚本
使用 adbutils 自动发现设备上所有用户，并在每个安装了目标包的用户下启动应用。

核心改进：
  - 用户运行状态直接从 `pm list users` 的输出解析，避免 dumpsys user + grep 在不同
    ROM 上格式不一致导致的误判。
  - 启动 Activity 使用 `cmd package resolve-activity --brief` 获取，输出干净。
  - 支持通过 FORCE_START 开关绕过状态检查，直接尝试启动。
"""

import re
import time
import sys

from adbutils import adb, AdbError


# ==================== 配置区 ====================
PACKAGE = "com.got.china"          # 目标包名
DEVICE_SERIAL = None               # 多设备时填写序列号，单设备留 None
START_DELAY = 2.0                  # 每个用户启动后的等待秒数
FORCE_START = False                # True 时跳过用户状态检查，直接尝试启动
# ================================================


def get_device():
    """获取目标设备对象"""
    if DEVICE_SERIAL:
        return adb.device(serial=DEVICE_SERIAL)
    return adb.device()


def get_all_users(device):
    """
    获取设备上所有用户及其运行状态。

    pm list users 输出格式示例：
        Users:
            UserInfo{0:Owner:13} running
            UserInfo{10:Work profile:30} running
            UserInfo{999:Clone user:13} stopped

    返回: [(uid, state), ...]，state 通常是 'running' 或 'stopped'
    """
    output = device.shell("pm list users")
    # 同时捕获 UserInfo{ 后面的数字 ID，以及 } 后面的状态词
    users = re.findall(r"UserInfo\{(\d+):[^}]*\}\s*(\w+)", output)
    return [(int(uid), state) for uid, state in users]


def ensure_user_running(device, uid, state):
    """
    根据 pm list users 返回的状态判断用户是否运行。
    若未运行，尝试用 am start-user 启动（不切换系统前台 UI）。
    """
    if state == "running":
        return True

    print(f"  用户 {uid} 未运行，尝试启动...")
    try:
        device.shell(f"am start-user {uid}")
    except AdbError as e:
        print(f"  am start-user 失败: {e}")
        return False

    time.sleep(1)

    # 重新查询状态
    for u, s in get_all_users(device):
        if u == uid and s == "running":
            print(f"  用户 {uid} 已启动")
            return True

    print(f"  用户 {uid} 无法启动，跳过")
    return False


def has_package(device, uid, package):
    """
    检查指定用户下是否安装了目标包。
    pm list packages --user <UID> 会列出该用户空间下的所有包。
    使用精确匹配，避免 com.got.china 匹配到 com.got.china.clone。
    """
    output = device.shell(f"pm list packages --user {uid}")
    packages = [
        line.replace("package:", "").strip()
        for line in output.strip().splitlines()
        if line.strip()
    ]
    return package in packages


def get_launch_activity(device, uid, package):
    """
    获取指定用户下目标包的启动 Activity。

    使用 cmd package resolve-activity，比 dumpsys 输出更干净。
    返回格式示例：
        com.got.china/com.unity3d.player.DDUnityLaunchActivity
    """
    cmd = (
        f"cmd package resolve-activity --brief --user {uid} "
        f"-a android.intent.action.MAIN "
        f"-c android.intent.category.LAUNCHER {package}"
    )
    output = device.shell(cmd).strip()

    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        return None

    component = lines[-1]
    # 校验格式：包名/Activity名
    if "/" in component and not component.startswith("No activity"):
        return component
    return None


def start_app(device, uid, component):
    """
    用 am start --user 启动指定用户下的应用。
    component 格式：package/activity
    """
    cmd = f"am start --user {uid} -n {component}"
    return device.shell(cmd)


def main():
    # 1. 连接设备
    try:
        device = get_device()
        print(f"✅ 已连接设备: {device.serial}")
    except AdbError as e:
        print(f"❌ 设备连接失败: {e}")
        sys.exit(1)

    # 2. 获取所有用户及其状态
    print("\n==> 获取所有用户...")
    users = get_all_users(device)
    if not users:
        print("❌ 未发现任何用户")
        sys.exit(1)
    print(f"用户列表: {users}")

    # 3. 遍历每个用户
    success_count = 0
    skip_count = 0
    fail_count = 0

    for uid, state in users:
        print(f"\n---- 处理用户 {uid} (状态: {state}) ----")

        # 3.1 检查包是否安装
        if not has_package(device, uid, PACKAGE):
            print(f"  用户 {uid} 下无 {PACKAGE}，跳过")
            skip_count += 1
            continue
        print(f"  用户 {uid} 下存在 {PACKAGE}")

        # 3.2 确保用户运行（FORCE_START 时跳过此检查）
        if not FORCE_START:
            if not ensure_user_running(device, uid, state):
                fail_count += 1
                continue
        else:
            print(f"  FORCE_START 已开启，跳过状态检查")

        # 3.3 获取启动 Activity
        component = get_launch_activity(device, uid, PACKAGE)
        if not component:
            print(f"  未找到启动 Activity，跳过")
            fail_count += 1
            continue
        print(f"  启动组件: {component}")

        # 3.4 启动应用
        try:
            result = start_app(device, uid, component)
            # am start 成功时输出通常包含 "Starting: Intent" 或 "Warning"
            if "Error" in result or "Exception" in result:
                print(f"  ❌ 用户 {uid} 启动失败: {result.strip()}")
                fail_count += 1
            else:
                print(f"  ✅ 用户 {uid} 启动成功")
                success_count += 1
        except AdbError as e:
            print(f"  ❌ 用户 {uid} 启动失败: {e}")
            fail_count += 1

        time.sleep(START_DELAY)

    # 4. 汇总
    print(f"\n{'=' * 40}")
    print(f"完成: 成功 {success_count} | 跳过 {skip_count} | 失败 {fail_count}")


if __name__ == "__main__":
    main()