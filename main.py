"""程序主入口: 单例检查、QApplication、闪屏和应用控制器启动。"""
# === 单例检查必须在所有其他 import 之前执行 ===
# 因为 PySide6 等 import 过程会调用 Win32 API, 覆盖 GetLastError,
# 导致 CreateMutexW 的 ERROR_ALREADY_EXISTS 检测失效。
import sys
import ctypes
from ctypes import wintypes

_MUTEX_NAME = "Local\\TraeTranslatorApp_SingleInstance_v1"
_MUTEX_HANDLE = None


def _acquire_single_instance() -> bool:
    global _MUTEX_HANDLE
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CreateMutexW.argtypes = [wintypes.LPCVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.GetLastError.restype = wintypes.DWORD
    _MUTEX_HANDLE = kernel32.CreateMutexW(None, True, _MUTEX_NAME)
    last_error = kernel32.GetLastError()
    if last_error == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(_MUTEX_HANDLE)
        _MUTEX_HANDLE = None
        return False
    return True


def _release_single_instance():
    global _MUTEX_HANDLE
    if _MUTEX_HANDLE:
        ctypes.windll.kernel32.CloseHandle(_MUTEX_HANDLE)
        _MUTEX_HANDLE = None


def _bring_existing_to_front():
    user32 = ctypes.windll.user32
    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    found_hwnd = None

    def _enum_proc(hwnd, _lparam):
        nonlocal found_hwnd
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                if "简单翻译" in buf.value:
                    found_hwnd = hwnd
                    user32.ShowWindow(hwnd, 9)
                    user32.SetForegroundWindow(hwnd)
                    return False
        return True

    user32.EnumWindows(EnumWindowsProc(_enum_proc), 0)
    return found_hwnd is not None


# 在 import PySide6 之前就做单例检查
if not _acquire_single_instance():
    _bring_existing_to_front()
    ctypes.windll.user32.MessageBoxW(
        0, "程序已运行", "简单翻译", 0x40
    )
    sys.exit(0)

# === 单例检查结束, 现在可以安全 import 其他模块 ===
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor
from PySide6.QtWidgets import QApplication, QSplashScreen

# 确保能 import src.*
sys.path.insert(0, str(Path(__file__).resolve().parent))

# 仅导入轻量级 config (内部只依赖 os/sys/pathlib)。
# 重型模块(fitz/PyMuPDF、pynput、requests 等)延迟到 main() 内
# 闪屏显示后再导入, 以加快首屏可见速度。
from src.config import ensure_dirs, load_config


def _make_app_icon() -> QIcon:
    """应用图标: 蓝色圆角块 + 白色"译"字。托盘/窗口/闪屏共用。"""
    pix = QPixmap(64, 64)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor("#4a90e2"))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(4, 4, 56, 56, 12, 12)
    p.setPen(QColor("#ffffff"))
    font = p.font()
    font.setBold(True)
    font.setPointSize(32)
    p.setFont(font)
    p.drawText(pix.rect(), Qt.AlignCenter, "译")
    p.end()
    return QIcon(pix)


def _make_splash_pixmap() -> QPixmap:
    """绘制启动闪屏: 与控制窗同宽(280)的朴素白底, 标题 + 介绍 + 进度文字。"""
    w, h = 280, 160
    pix = QPixmap(w, h)
    pix.fill(QColor("#ffffff"))  # 与原生窗口背景一致
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)

    # 标题: 简单翻译
    font = p.font()
    font.setBold(True)
    font.setPointSize(14)
    p.setFont(font)
    p.setPen(QColor("#000000"))
    p.drawText(0, 14, w, 36, Qt.AlignCenter, "简单翻译")

    # 介绍: 两行, 均居中
    font.setBold(False)
    font.setPointSize(9)
    p.setFont(font)
    p.setPen(QColor("#666666"))
    p.drawText(0, 54, w, 20, Qt.AlignCenter, "一款简单的翻译软件")
    p.drawText(0, 74, w, 20, Qt.AlignCenter, "支持划词翻译, PDF文档翻译")

    # 底部分隔线, 与控制窗风格一致
    p.setPen(QColor("#e0e0e0"))
    p.drawLine(12, h - 36, w - 12, h - 36)

    p.end()
    return pix


def main():
    # 单例检查已在模块顶部(所有 import 之前)完成, 到这里说明是第一个实例
    ensure_dirs()
    load_config()

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # 关闭窗口不退出, 由托盘控制
    app.setApplicationName("简单翻译")

    app_icon = _make_app_icon()
    app.setWindowIcon(app_icon)  # 全局窗口图标(控制窗等会继承)

    # 启动闪屏: 朴素白底, 与控制窗风格一致; 带标题栏(可拖动/最小化)
    splash = QSplashScreen(_make_splash_pixmap())
    splash.setWindowFlags(Qt.Window | Qt.WindowMinimizeButtonHint)
    splash.setWindowIcon(app_icon)
    splash.show()
    _msg_flags = Qt.AlignBottom | Qt.AlignHCenter

    def _splash_msg(msg: str):
        splash.showMessage(msg, _msg_flags)
        app.processEvents()

    _splash_msg("正在初始化…")

    _splash_msg("正在加载…")
    from src.app_controller import SimpleTranslateController

    controller = SimpleTranslateController(app_icon, app)
    controller.start(warning_parent=splash)
    app._simple_translate_controller = controller
    splash.finish(controller.control)

    # 退出时停止监听 + 释放单例锁 + 保存用量
    def on_about_to_quit():
        controller.shutdown()
        _release_single_instance()
    app.aboutToQuit.connect(on_about_to_quit)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
