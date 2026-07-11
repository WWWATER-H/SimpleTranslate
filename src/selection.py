"""划词监听: pynput 监听鼠标释放, 模拟 Ctrl+C 取选中文字。

注意: 模拟 Ctrl+C 会临时改写剪贴板, 这里做了备份-恢复。
若用户原剪贴板含复杂格式(图片/HTML), 仅恢复文本部分。

另外监听鼠标按下和键盘按下, 通知 UI 隐藏悬浮窗(常驻显示模式下,
用户有新输入动作即隐藏, 新划词会再次显示)。

pynput 导入延迟到 start() 调用时, 避免拖慢应用启动。
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import time
import threading
import re
import pyperclip
from collections.abc import Callable

from . import config

# ── Win32 API 辅助: 判断点击是否在自家窗口上 ──────────────────

_user32 = ctypes.windll.user32


class _POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


# 主线程注册的自家窗口句柄集合, 监听线程只读
_own_hwnds: set[int] = set()


def register_own_window(hwnd: int):
    """注册自家窗口句柄(从 Qt 主线程调用)。"""
    _own_hwnds.add(hwnd)


def unregister_own_window(hwnd: int):
    """移除窗口句柄。"""
    _own_hwnds.discard(hwnd)


def _is_click_on_own_window(x: int, y: int) -> bool:
    """判断屏幕坐标 (x, y) 是否落在自家窗口内。"""
    if not _own_hwnds:
        return False
    try:
        pt = _POINT(x, y)
        hwnd = _user32.WindowFromPoint(pt)
        while hwnd:
            if hwnd in _own_hwnds:
                return True
            hwnd = _user32.GetParent(hwnd)
        return False
    except Exception:
        return False


_URL_RE = re.compile(r"^(?:https?://|www\.)\S+$", re.IGNORECASE)
_EMAIL_RE = re.compile(r"^[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}$")
_WINDOWS_PATH_RE = re.compile(r"^[A-Za-z]:[\\/].+")
_SYMBOL_RE = re.compile(r"^[\W_]+$", re.UNICODE)


def _cfg_bool(key: str, default: bool) -> bool:
    raw = config.get(key, "1" if default else "0").strip().lower()
    return raw in {"1", "true", "yes", "on", "y"}


def _normalize_text(text: str) -> str:
    """把多行/多空格选区压成稳定文本, 方便过滤和缓存命中。"""
    return re.sub(r"\s+", " ", (text or "").strip())


class SelectionListener:
    """后台线程监听鼠标释放, 提取选中文本并通过回调返回。

    on_input: 用户有新的鼠标按下/键盘按下时触发(用于隐藏悬浮窗)。
    """

    def __init__(self, on_text: Callable[[str], None],
                 on_error: Callable[[str], None] | None = None,
                 on_input: Callable[[], None] | None = None):
        self._on_text = on_text
        self._on_error = on_error
        self._on_input = on_input
        self._enabled = False
        self._mouse_listener: mouse.Listener | None = None
        self._kb_listener: keyboard.Listener | None = None
        self._kb_ctrl = None  # 在 start() 中延迟创建
        self._last_text = ""
        self._last_emit_at = 0.0
        self._lock = threading.Lock()
        self._suppress = False  # 恢复剪贴板时避免触发回调

    def set_enabled(self, on: bool):
        self._enabled = on

    def is_enabled(self) -> bool:
        return self._enabled

    def start(self):
        from pynput import mouse, keyboard  # 延迟导入, 避免拖慢应用启动

        # 保存模块引用供回调方法使用
        self._mouse_mod = mouse
        self._kb_mod = keyboard

        if self._kb_ctrl is None:
            self._kb_ctrl = keyboard.Controller()
        self._mouse_listener = mouse.Listener(on_click=self._on_click)
        self._mouse_listener.daemon = True
        self._mouse_listener.start()
        self._kb_listener = keyboard.Listener(on_press=self._on_key)
        self._kb_listener.daemon = True
        self._kb_listener.start()

    def stop(self):
        if self._mouse_listener:
            self._mouse_listener.stop()
            self._mouse_listener = None
        if self._kb_listener:
            self._kb_listener.stop()
            self._kb_listener = None

    def _on_key(self, _key):
        # 任何按键都视为"新的输入动作", 通知隐藏悬浮窗
        if self._suppress:
            return
        if self._on_input:
            self._on_input()

    def _on_click(self, x, y, button, pressed):
        # 鼠标按下时通知隐藏悬浮窗(无论是否启用监听)
        if pressed and self._on_input:
            self._on_input()
        if not self._enabled:
            return
        # 左键释放时尝试取选中文字
        if button != self._mouse_mod.Button.left or pressed:
            return
        # 如果点击在自家窗口上(悬浮窗/控制窗), 不捕获
        if _is_click_on_own_window(x, y):
            return
        # 稍等让 UI 完成选中
        threading.Thread(target=self._capture, daemon=True).start()

    def _capture(self):
        if not self._lock.acquire(blocking=False):
            return
        try:
            if self._too_soon():
                return
            # 备份当前剪贴板文本(图片等格式无法恢复)
            try:
                backup = pyperclip.paste()
            except Exception:
                backup = ""
            # 清空以便检测新内容
            self._suppress = True
            try:
                pyperclip.copy("")
            except Exception:
                pass
            # 模拟 Ctrl+C
            with self._kb_ctrl.pressed(self._kb_mod.Key.ctrl):
                self._kb_ctrl.press('c')
                self._kb_ctrl.release('c')
            # 等待剪贴板更新
            text = ""
            for _ in range(10):
                time.sleep(0.02)
                try:
                    text = pyperclip.paste()
                except Exception:
                    text = ""
                if text:
                    break
            # 恢复原剪贴板
            try:
                pyperclip.copy(backup)
            except Exception:
                pass
            self._suppress = False

            text = _normalize_text(text)
            if not self._should_translate(text):
                return
            self._mark_emitted(text)
            self._on_text(text)
        except Exception as e:
            self._suppress = False
            if self._on_error:
                self._on_error(str(e))
        finally:
            if self._suppress:
                self._suppress = False
            self._lock.release()

    def _too_soon(self) -> bool:
        debounce_ms = config.get_int("selection.debounce_ms", 350)
        if debounce_ms <= 0:
            return False
        return (time.monotonic() - self._last_emit_at) * 1000 < debounce_ms

    def _mark_emitted(self, text: str):
        self._last_text = text
        self._last_emit_at = time.monotonic()

    def _should_translate(self, text: str) -> bool:
        """过滤明显不适合翻译的选区, 降低误触发和无效 API 消耗。"""
        if not text:
            return False
        min_len = config.get_int("selection.min_chars", 2)
        max_len = config.get_int("selection.max_chars", 5000)
        if len(text) < min_len or len(text) > max_len:
            return False

        duplicate_window_ms = config.get_int("selection.duplicate_window_ms", 2000)
        if text == self._last_text:
            if duplicate_window_ms <= 0:
                return False
            if (time.monotonic() - self._last_emit_at) * 1000 < duplicate_window_ms:
                return False

        if _cfg_bool("selection.require_ascii_letter", True):
            # 仅处理含英文字母的文本(避免在中文输入框选中触发)
            if not any(c.isalpha() and ord(c) < 128 for c in text):
                return False

        if _cfg_bool("selection.ignore_urls", True) and _URL_RE.match(text):
            return False
        if _cfg_bool("selection.ignore_emails", True) and _EMAIL_RE.match(text):
            return False
        if _cfg_bool("selection.ignore_paths", True) and _WINDOWS_PATH_RE.match(text):
            return False
        if _cfg_bool("selection.ignore_symbols", True) and _SYMBOL_RE.match(text):
            return False
        if _cfg_bool("selection.ignore_mostly_numbers", True):
            non_space = [c for c in text if not c.isspace()]
            if non_space:
                numeric = sum(1 for c in non_space if c.isdigit() or c in ".,:%+-")
                if numeric / len(non_space) >= 0.8:
                    return False
        return True
