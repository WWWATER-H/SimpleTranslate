"""应用控制器: 管理窗口、托盘、监听器和后台翻译任务。

启动策略 (即开即用):
  阶段 1 — 显示控制窗 (快速, 无重型导入)
  阶段 2 — 启动划词监听 (pynput), 用缓冲区接文本, 用户立即可用
  阶段 3 — 初始化翻译器 (requests/引擎), 就绪后刷缓冲区并连接 runner
对话框 / 翻译器 / fitz / reportlab 全部延迟到实际使用时才导入。
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from .selection import SelectionListener, register_own_window
from .terms import get_term_manager
from .translate_tasks import TranslationTaskResult, TranslationTaskRunner
from .ui import ControlPanel, TranslationPopup
from . import config

# 对话框和翻译器延迟到使用时再导入, 避免启动时加载 fitz/reportlab 等重型依赖
_translator_module = None
_dialogs_module = None


def _get_translator_module():
    global _translator_module
    if _translator_module is None:
        from . import translator as _translator_module
    return _translator_module


def _get_dialogs_module():
    global _dialogs_module
    if _dialogs_module is None:
        from . import dialogs as _dialogs_module
    return _dialogs_module


def _cfg_bool(key: str, default: bool) -> bool:
    """读取布尔型配置项。"""
    raw = config.get(key, "1" if default else "0").strip().lower()
    return raw in {"1", "true", "yes", "on", "y"}


class Bridge(QObject):
    """后台监听线程到 Qt 主线程的信号桥。"""

    text_captured = Signal(str)
    input_action = Signal()


class SimpleTranslateController(QObject):
    """把主流程从 main.py 中拆出来, 方便维护和测试。"""

    def __init__(self, app_icon: QIcon, parent=None):
        super().__init__(parent)
        self._app_icon = app_icon
        self._translator = None
        self._term = None
        self._control: ControlPanel | None = None
        self._popup: TranslationPopup | None = None
        self._bridge: Bridge | None = None
        self._listener: SelectionListener | None = None
        self._runner: TranslationTaskRunner | None = None
        self._tray: QSystemTrayIcon | None = None
        self._pending_texts: list[str] = []  # 监听器已捕获但翻译器未就绪的文本

    @property
    def control(self) -> ControlPanel:
        return self._control

    def start(self, warning_parent=None):
        # ── 阶段 1: 显示 UI (快速, 无重型导入) ─────────────────
        self._term = get_term_manager()

        self._control = ControlPanel()
        self._popup = TranslationPopup()
        self._popup.apply_config()
        self._control.set_mode(self._term.mode)

        # 注册窗口句柄, 避免在自家窗口上划词触发翻译
        register_own_window(int(self._control.winId()))
        register_own_window(int(self._popup.winId()))

        self._setup_tray()
        self._control.show()
        QApplication.processEvents()  # 立即绘制窗口

        # ── 阶段 2: 桥 + 监听器 (划词立即可用) ────────────────
        self._bridge = Bridge(self)
        self._bridge.input_action.connect(self._popup.hide_on_input)
        # 先用缓冲区接文本, 翻译器就绪后切到 runner
        self._bridge.text_captured.connect(self._on_text_buffered)

        self._listener = SelectionListener(
            on_text=self._bridge.text_captured.emit,
            on_input=self._bridge.input_action.emit,
        )
        # 自动启用划词翻译
        if _cfg_bool("app.auto_listen", False):
            self._listener.set_enabled(True)
            self._control._toggle_btn.setChecked(True)
            self._control._mini_toggle.setChecked(True)
        self._listener.start()
        self._control._toggle_btn.clicked.connect(self._listener.set_enabled)
        self._control._mini_toggle.clicked.connect(self._listener.set_enabled)

        self._control.open_pdf_dialog.connect(self.open_pdf)
        self._control.open_terms_dialog.connect(self.open_terms)
        self._control.open_settings_dialog.connect(self.open_settings)

        QApplication.processEvents()  # 保持 UI 响应

        # ── 阶段 3: 翻译器 (网络引擎, 较慢) ───────────────────
        tm = _get_translator_module()
        self._translator = tm.get_translator()

        if not self._translator.available():
            QMessageBox.warning(
                warning_parent, "未配置翻译引擎",
                "未检测到可用的翻译引擎。\n请在 config.properties 中填写 DeepL、百度翻译或 OpenAI 兼容 API key,\n"
                "否则只能查看缓存中的历史译文。\n\n示例配置见 config.properties.example。"
            )

        self._runner = TranslationTaskRunner(self._translator, self._term, self)
        self._runner.started.connect(self._popup.show_loading)
        self._runner.finished.connect(self._on_translation_finished)
        # 刷缓冲区: 翻译器就绪前捕获的文本现在提交
        pending = self._pending_texts
        self._pending_texts = []
        for text in pending:
            self._runner.submit(text)

    def _on_text_buffered(self, text: str):
        """监听器捕获文本时回调: 若 runner 已就绪则直接翻译, 否则暂存。"""
        if self._runner is not None:
            self._runner.submit(text)
        else:
            self._pending_texts.append(text)

    def shutdown(self):
        if self._listener:
            self._listener.stop()
        if self._runner:
            self._runner.shutdown()
        try:
            _get_translator_module().save_usage()
        except Exception:
            pass

    def open_pdf(self):
        if not self._ensure_paused("导入 PDF"):
            return
        dm = _get_dialogs_module()
        dlg = dm.PdfExportDialog(self._control)
        dlg.exec()

    def open_terms(self):
        if not self._ensure_paused("打开术语表"):
            return
        dm = _get_dialogs_module()
        dlg = dm.TermsDialog(self._control)
        dlg.exec()

    def open_settings(self):
        if not self._ensure_paused("打开设置"):
            return
        dm = _get_dialogs_module()
        dlg = dm.SettingsDialog(self._control)
        if dlg.exec():
            if self._popup:
                self._popup.apply_config()
            if self._control:
                self._control._update_status()

    def _on_translation_finished(self, result: TranslationTaskResult):
        if not self._runner or not self._popup:
            return
        if not self._runner.is_latest(result.task_id):
            return
        self._popup.show_translation(
            result.source,
            result.target,
            result.engine,
            result.cached,
            result.error,
        )

    def _ensure_paused(self, action_name: str) -> bool:
        if self._listener and self._listener.is_enabled():
            QMessageBox.warning(self._control, "简单翻译", f"请先暂停监听再{action_name}。")
            return False
        return True

    def _setup_tray(self):
        self._tray = QSystemTrayIcon(self._app_icon, self)
        self._tray.setToolTip("简单翻译")
        menu = QMenu()
        act_show = QAction("显示控制窗", menu)
        act_show.triggered.connect(self._control.show)
        act_pdf = QAction("导入 PDF…", menu)
        act_pdf.triggered.connect(self.open_pdf)
        act_terms = QAction("术语表…", menu)
        act_terms.triggered.connect(self.open_terms)
        act_settings = QAction("设置…", menu)
        act_settings.triggered.connect(self.open_settings)
        act_quit = QAction("退出", menu)
        act_quit.triggered.connect(QApplication.instance().quit)
        menu.addAction(act_show)
        menu.addAction(act_pdf)
        menu.addAction(act_terms)
        menu.addAction(act_settings)
        menu.addSeparator()
        menu.addAction(act_quit)
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(
            lambda reason: self._control.show() if reason == QSystemTrayIcon.DoubleClick else None
        )
        self._tray.show()
