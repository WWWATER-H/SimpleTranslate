"""PySide6 界面: 译文悬浮窗 + 右上角控制窗。

- TranslationPopup: 无边框、置顶、半透明, 跟随鼠标附近显示译文
- ControlPanel: 右上角常驻, 控制开始/暂停、模式切换、查用量、导入PDF
"""
from PySide6.QtCore import Qt, QTimer, Signal, QPoint, QRect, QEvent
from PySide6.QtGui import QCursor, QGuiApplication, QFont, QColor, QPalette, QAction
from PySide6.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QHBoxLayout, QPushButton,
    QFrame, QComboBox, QToolButton, QApplication, QCheckBox,
    QMainWindow, QSystemTrayIcon, QMenu, QMessageBox,
    QFileDialog, QProgressBar, QStatusBar, QTableWidget, QTableWidgetItem,
    QDialog, QHeaderView, QLineEdit, QInputDialog, QScrollArea, QSizeGrip,
)

from . import config
from .translator import get_translator
from .terms import get_term_manager


MODE_LABELS = {
    "chinese_only": "仅中文",
    "bilingual": "原文+译文",
    "glossary": "术语覆盖",
}


def _cfg_bool(key: str, default: bool) -> bool:
    raw = config.get(key, "1" if default else "0").strip().lower()
    return raw in {"1", "true", "yes", "on", "y"}


class TranslationPopup(QFrame):
    """无边框置顶悬浮窗, 常驻显示直到用户有新的输入动作。"""

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        # 内容器: 圆角半透明背景
        self._container = QFrame(self)
        self._container.setObjectName("popupContainer")
        self._container.setStyleSheet("""
            #popupContainer {
                background: rgba(45, 45, 48, 230);
                border: 1px solid rgba(120, 120, 130, 180);
                border-radius: 8px;
            }
        """)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.addWidget(self._container)

        layout = QVBoxLayout(self._container)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)

        # 顶部行: 引擎标记 + 关闭按钮
        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        self._meta_label = QLabel(self._container)
        self._meta_label.setStyleSheet("color: rgba(140, 200, 255, 180); font-size: 10px;")
        top_row.addWidget(self._meta_label)
        top_row.addStretch()
        self._source_toggle_btn = QPushButton("展开原文", self._container)
        self._source_toggle_btn.setFixedSize(68, 20)
        self._source_toggle_btn.setStyleSheet(
            "QPushButton { background: rgba(255,255,255,28); border: none; color: #ccc; "
            "font-size: 11px; border-radius: 10px; }"
            "QPushButton:hover { color: #fff; background: rgba(255,255,255,55); }"
        )
        self._source_toggle_btn.clicked.connect(self._toggle_source)
        self._source_toggle_btn.hide()
        top_row.addWidget(self._source_toggle_btn)
        self._copy_btn = QPushButton("复制", self._container)
        self._copy_btn.setFixedSize(56, 20)
        self._copy_btn.setStyleSheet(
            "QPushButton { background: rgba(255,255,255,28); border: none; color: #ccc; "
            "font-size: 11px; border-radius: 10px; }"
            "QPushButton:hover { color: #fff; background: rgba(255,255,255,55); }"
        )
        self._copy_btn.clicked.connect(self._copy_translation)
        top_row.addWidget(self._copy_btn)
        self._pin_btn = QPushButton("固定", self._container)
        self._pin_btn.setCheckable(True)
        self._pin_btn.setFixedSize(56, 20)
        self._pin_btn.setStyleSheet(
            "QPushButton { background: rgba(255,255,255,28); border: none; color: #ccc; "
            "font-size: 11px; border-radius: 10px; }"
            "QPushButton:hover { color: #fff; background: rgba(255,255,255,55); }"
            "QPushButton:checked { color: #fff; background: rgba(74,144,226,150); }"
        )
        self._pin_btn.toggled.connect(self._on_pin_toggled)
        top_row.addWidget(self._pin_btn)
        self._lock_btn = QPushButton("锁定位置", self._container)
        self._lock_btn.setCheckable(True)
        self._lock_btn.setFixedSize(64, 20)
        self._lock_btn.setStyleSheet(
            "QPushButton { background: rgba(255,255,255,28); border: none; color: #ccc; "
            "font-size: 11px; border-radius: 10px; }"
            "QPushButton:hover { color: #fff; background: rgba(255,255,255,55); }"
            "QPushButton:checked { color: #fff; background: rgba(255,160,40,170); }"
        )
        self._lock_btn.toggled.connect(self._on_lock_toggled)
        top_row.addWidget(self._lock_btn)
        close_btn = QPushButton("×", self._container)
        close_btn.setFixedSize(20, 20)
        close_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none; color: #aaa; "
            "font-size: 16px; font-weight: bold; }"
            "QPushButton:hover { color: #fff; background: rgba(255,80,80,180); border-radius: 10px; }"
        )
        close_btn.clicked.connect(self.hide)
        top_row.addWidget(close_btn)
        layout.addLayout(top_row)

        self._scroll_area = QScrollArea(self._container)
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setFrameShape(QFrame.NoFrame)
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._scroll_area.setStyleSheet("""
            QScrollArea { background: transparent; border: none; }
            QScrollBar:vertical {
                background: rgba(255, 255, 255, 25);
                border: none;
                width: 8px;
                margin: 0;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical {
                background: rgba(255, 255, 255, 90);
                border-radius: 4px;
                min-height: 24px;
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(255, 255, 255, 140);
            }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {
                height: 0;
            }
            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical {
                background: transparent;
            }
        """)

        self._text_widget = QWidget(self._scroll_area)
        self._text_widget.setStyleSheet("background: transparent;")
        text_layout = QVBoxLayout(self._text_widget)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(4)

        self._src_label = QLabel(self._text_widget)
        self._src_label.setStyleSheet("color: rgba(180, 180, 180, 220); font-size: 11px;")
        self._src_label.setWordWrap(True)
        self._src_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self._dst_label = QLabel(self._text_widget)
        self._dst_label.setStyleSheet("color: #f0f0f0; font-size: 14px;")
        self._dst_label.setWordWrap(True)
        self._dst_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        text_layout.addWidget(self._src_label)
        text_layout.addWidget(self._dst_label)
        self._scroll_area.setWidget(self._text_widget)
        layout.addWidget(self._scroll_area)

        self._max_width = 480
        self._min_width = 320
        self._max_src_chars = 240
        self._max_height_percent = 70
        self._current_target = ""
        self._pinned = False
        self._position_locked = False
        self._source_full = ""
        self._source_collapsed = True
        self._show_source = True
        self.setMaximumWidth(self._max_width)
        self.setMinimumWidth(self._min_width)

        # 右下角调整大小手柄
        self._size_grip = QSizeGrip(self._container)
        self._size_grip.setStyleSheet("background: transparent; border: none;")
        self._size_grip.setFixedSize(12, 12)
        layout.addWidget(self._size_grip, 0, Qt.AlignRight | Qt.AlignBottom)

        # 拖拽支持
        self._drag_start: QPoint | None = None
        self._container.installEventFilter(self)

    def apply_config(self):
        self._max_src_chars = max(60, config.get_int("popup.max_source_chars", 240))
        self._max_height_percent = min(90, max(30, config.get_int("popup.max_height_percent", 70)))
        self._show_source = _cfg_bool("popup.show_source", True)
        self._pin_btn.setChecked(_cfg_bool("popup.default_pinned", False))

    def show_loading(self, _task_id: int, source: str):
        self._current_target = ""
        self._set_source_text(source)
        self._dst_label.setText("正在翻译…")
        self._dst_label.setStyleSheet("color: #d7e8ff; font-size: 14px;")
        self._meta_label.setText("处理中")
        self._apply_mode_visibility()
        self._resize_and_show(reset_scroll=True)

    def show_translation(self, source: str, target: str, engine: str, cached: bool, error: str | None):
        if not target and not source:
            self.hide()
            return
        self._set_source_text(source)
        if error:
            self._dst_label.setText(error)
            self._dst_label.setStyleSheet("color: #ff8080; font-size: 14px;")
            self._current_target = ""
        else:
            self._dst_label.setText(target or "(空)")
            self._dst_label.setStyleSheet("color: #f0f0f0; font-size: 14px;")
            self._current_target = target or ""
        tag = "缓存" if cached else engine
        self._meta_label.setText(tag)
        self._apply_mode_visibility()
        self._resize_and_show(reset_scroll=True)

    def _apply_mode_visibility(self):
        """根据术语模式控制原文显隐: chinese_only 时强制隐藏原文。"""
        if get_term_manager().mode == "chinese_only":
            self._src_label.hide()
            self._source_toggle_btn.hide()
        else:
            self._src_label.show()

    def _resize_and_show(self, reset_scroll: bool):
        # 限制最大高度为屏幕高度的 70%, 防止长文本溢出
        screen = QGuiApplication.primaryScreen().geometry()
        max_h = int(screen.height() * self._max_height_percent / 100)
        self.setMaximumHeight(max_h)
        self._scroll_area.setMaximumHeight(max_h - 48)

        # 位置锁定时不自动调整大小, 只更新内容
        if self._position_locked:
            if reset_scroll:
                self._scroll_area.verticalScrollBar().setValue(0)
            self.show()
            self.raise_()
            return

        self.adjustSize()
        # 强制宽度在 [min_width, max_width] 范围内
        w = max(self._min_width, min(self.width(), self._max_width))
        self.resize(w, self.height())
        # 控制最大高度
        if self.height() > max_h:
            self.resize(w, max_h)
        if reset_scroll:
            self._scroll_area.verticalScrollBar().setValue(0)

        self._move_near_cursor()
        self.show()
        self.raise_()

    def _set_source_text(self, source: str):
        source = source or ""
        if source != self._source_full:
            self._source_collapsed = True
        self._source_full = source
        # 仅中文模式: 隐藏原文
        if get_term_manager().mode == "chinese_only":
            self._src_label.hide()
            self._source_toggle_btn.hide()
            return
        self._src_label.show()
        needs_fold = len(self._source_full) > self._max_src_chars
        self._source_toggle_btn.setVisible(needs_fold)
        if needs_fold and self._source_collapsed:
            self._src_label.setText(self._source_full[:self._max_src_chars] + "…")
            self._source_toggle_btn.setText("展开原文")
        else:
            self._src_label.setText(self._source_full)
            self._source_toggle_btn.setText("收起原文")

    def _toggle_source(self):
        self._source_collapsed = not self._source_collapsed
        self._set_source_text(self._source_full)
        self._resize_and_show(reset_scroll=False)

    def hide_on_input(self):
        if not self._pinned:
            # 如果鼠标在悬浮窗范围内, 说明用户在操作窗口本身
            # (点"固定"/"复制"/选中文字等), 不应隐藏
            if self.isVisible():
                cursor_pos = QCursor.pos()
                if self.geometry().contains(cursor_pos):
                    return
            self.hide()

    def _copy_translation(self):
        text = self._current_target or self._dst_label.text()
        if text:
            QApplication.clipboard().setText(text)
            self._copy_btn.setText("已复制")
            QTimer.singleShot(900, lambda: self._copy_btn.setText("复制"))

    def eventFilter(self, obj, event):
        """容器事件过滤器: 在标题栏区域拖拽移动悬浮窗。"""
        if obj == self._container:
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                pos = event.position().toPoint()
                child = self._container.childAt(pos)
                # 只允许在标题栏空白区域或引擎标签上拖拽
                if child is self._meta_label or child is None:
                    self._drag_start = event.globalPosition().toPoint()
                return False
            elif event.type() == QEvent.MouseMove and self._drag_start is not None:
                current = event.globalPosition().toPoint()
                if (current - self._drag_start).manhattanLength() > 3:
                    delta = current - self._drag_start
                    self.move(self.pos() + delta)
                    self._drag_start = current
                return False
            elif event.type() == QEvent.MouseButtonRelease:
                self._drag_start = None
                return False
        return super().eventFilter(obj, event)

    def _on_pin_toggled(self, checked: bool):
        self._pinned = checked
        self._pin_btn.setText("已固定" if checked else "固定")

    def _on_lock_toggled(self, checked: bool):
        self._position_locked = checked
        self._lock_btn.setText("已锁定" if checked else "锁定位置")

    def _move_near_cursor(self):
        cursor = QCursor.pos()
        # 优先放在鼠标右下方
        x = cursor.x() + 16
        y = cursor.y() + 18
        screen = QGuiApplication.screenAt(cursor)
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        geo = screen.geometry()
        if x + self.width() > geo.right() - 8:
            x = cursor.x() - self.width() - 16
        if y + self.height() > geo.bottom() - 8:
            y = cursor.y() - self.height() - 18
        self.move(max(geo.left() + 8, x), max(geo.top() + 8, y))


class ControlPanel(QWidget):
    """右上角常驻控制窗, 支持折叠为迷你悬浮条。"""

    request_translate = Signal(str)  # 选中文字待翻译
    open_pdf_dialog = Signal()
    open_terms_dialog = Signal()
    open_settings_dialog = Signal()

    # 折叠模式尺寸
    _FULL_SIZE = (280, 260)
    _MINI_SIZE = (150, 36)

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setWindowTitle("简单翻译")
        self._collapsed = False

        # 主布局: 包含完整面板和迷你条两个容器
        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(0, 0, 0, 0)
        self._root.setSpacing(0)

        # === 完整面板 ===
        self._full_widget = QWidget(self)
        self._full_widget.setStyleSheet("background: #ffffff;")
        self._full_widget.setAutoFillBackground(True)
        layout = QVBoxLayout(self._full_widget)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # 标题行 + 折叠按钮
        _btn_base = ("QPushButton { background: #e8e8e8; border: 1px solid #ccc; "
                     "border-radius: 3px; padding: 4px 8px; color: #333; }"
                     "QPushButton:hover { background: #d0d0d0; }"
                     "QPushButton:pressed { background: #b8b8b8; }")
        top = QHBoxLayout()
        top.addWidget(QLabel("简单翻译"))
        top.addStretch()
        self._mode_combo = QComboBox()
        for k, v in MODE_LABELS.items():
            self._mode_combo.addItem(v, k)
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        top.addWidget(self._mode_combo)
        b_collapse = QPushButton("—")
        b_collapse.setFixedSize(20, 20)
        b_collapse.setStyleSheet(
            "QPushButton { background: #e8e8e8; border: 1px solid #ccc; border-radius: 3px; "
            "color: #666; font-size: 14px; font-weight: bold; }"
            "QPushButton:hover { background: #d0d0d0; color: #cc0000; }"
        )
        b_collapse.setToolTip("折叠为迷你悬浮条")
        b_collapse.clicked.connect(self.collapse)
        top.addWidget(b_collapse)
        layout.addLayout(top)

        # 开始/暂停
        self._toggle_btn = QPushButton("开始监听")
        self._toggle_btn.setCheckable(True)
        self._toggle_btn.setStyleSheet(_btn_base)
        self._toggle_btn.clicked.connect(self._on_toggle)
        layout.addWidget(self._toggle_btn)

        # 用量
        self._usage_label = QLabel("用量: --")
        self._usage_label.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(self._usage_label)

        # 缓存计数
        self._cache_label = QLabel("缓存: 0 条")
        self._cache_label.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(self._cache_label)

        # 功能按钮 第一行
        row1 = QHBoxLayout()
        b_pdf = QPushButton("导入PDF")
        b_pdf.setStyleSheet(_btn_base)
        b_pdf.clicked.connect(self.open_pdf_dialog.emit)
        b_terms = QPushButton("术语表")
        b_terms.setStyleSheet(_btn_base)
        b_terms.clicked.connect(self.open_terms_dialog.emit)
        row1.addWidget(b_pdf)
        row1.addWidget(b_terms)
        layout.addLayout(row1)

        # 功能按钮 第二行
        row2 = QHBoxLayout()
        b_settings = QPushButton("设置")
        b_settings.setStyleSheet(_btn_base)
        b_settings.clicked.connect(self.open_settings_dialog.emit)
        row2.addWidget(b_settings)
        layout.addLayout(row2)

        layout.addStretch()

        # 署名
        credit = QLabel("Made By WWWATER-H")
        credit.setStyleSheet("color: #999; font-size: 10px;")
        credit.setAlignment(Qt.AlignCenter)
        layout.addWidget(credit)

        # === 迷你悬浮条 ===
        self._mini_widget = QWidget(self)
        self._mini_widget.setStyleSheet("""
            QWidget { background: #ffffff; border: 1px solid #cccccc; border-radius: 4px; }
        """)
        mini_layout = QHBoxLayout(self._mini_widget)
        mini_layout.setContentsMargins(6, 4, 6, 4)
        mini_layout.setSpacing(4)

        self._mini_toggle = QPushButton("开始")
        self._mini_toggle.setCheckable(True)
        self._mini_toggle.setFixedSize(40, 22)
        self._mini_toggle.setStyleSheet(
            "QPushButton { background: transparent; border: none; color: #2a82da; "
            "font-size: 12px; }"
            "QPushButton:checked { color: #d83030; }"
            "QPushButton:hover { color: #4a9ee8; }"
            "QPushButton:checked:hover { color: #e84848; }"
        )
        self._mini_toggle.setToolTip("开始/暂停监听")
        mini_layout.addWidget(self._mini_toggle)

        b_expand = QPushButton("展开")
        b_expand.setFixedSize(40, 22)
        b_expand.setStyleSheet(
            "QPushButton { background: transparent; border: none; color: #666666; "
            "font-size: 12px; }"
            "QPushButton:hover { color: #333333; }"
        )
        b_expand.setToolTip("展开完整面板")
        b_expand.clicked.connect(self.expand)
        mini_layout.addWidget(b_expand)

        self._root.addWidget(self._full_widget)
        self._root.addWidget(self._mini_widget)
        self._mini_widget.hide()

        # 迷你条拖动支持
        self._mini_drag_start: QPoint | None = None
        self._mini_widget.installEventFilter(self)

        self.setFixedSize(*self._FULL_SIZE)

        # 刷新定时器
        self._refresh = QTimer(self)
        self._refresh.timeout.connect(self._update_status)
        self._refresh.start(3000)
        self._update_status()

        # 同步两个按钮的选中状态
        self._mini_toggle.toggled.connect(self._sync_toggle_state)

        self._move_top_right()

    def collapse(self):
        """折叠为迷你悬浮条, 隐藏系统标题栏。"""
        if self._collapsed:
            return
        self._collapsed = True
        self._full_widget.hide()
        self._mini_widget.show()
        # 同步按钮状态
        self._mini_toggle.blockSignals(True)
        self._mini_toggle.setChecked(self._toggle_btn.isChecked())
        self._mini_toggle.setText("停止" if self._toggle_btn.isChecked() else "开始")
        self._mini_toggle.blockSignals(False)
        # 切换为无边框样式, 隐藏系统标题栏
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedSize(*self._MINI_SIZE)
        self._move_top_right()
        self.show()  # setWindowFlags 后需要重新 show

    def expand(self):
        """展开为完整面板, 恢复系统标题栏。"""
        if not self._collapsed:
            return
        self._collapsed = False
        self._mini_widget.hide()
        self._full_widget.show()
        # 恢复标准窗口样式
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setFixedSize(*self._FULL_SIZE)
        self._move_top_right()
        self.show()

    def _sync_toggle_state(self, checked: bool):
        """迷你条按钮同步到主按钮。"""
        self._mini_toggle.setText("停止" if checked else "开始")
        # 同步主按钮状态(不触发信号, 避免循环)
        self._toggle_btn.blockSignals(True)
        self._toggle_btn.setChecked(checked)
        self._toggle_btn.setText("停止监听" if checked else "开始监听")
        self._toggle_btn.blockSignals(False)

    def eventFilter(self, obj, event):
        """迷你条拖动: 在空白区域按住左键拖动整个窗口。"""
        if obj == self._mini_widget:
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                # 只在非按钮区域开始拖动
                pos = event.position().toPoint()
                child = self._mini_widget.childAt(pos)
                if child is None:
                    self._mini_drag_start = event.globalPosition().toPoint() - self.pos()
                return False
            elif event.type() == QEvent.MouseMove and self._mini_drag_start is not None:
                self.move(event.globalPosition().toPoint() - self._mini_drag_start)
                return False
            elif event.type() == QEvent.MouseButtonRelease:
                self._mini_drag_start = None
                return False
        return super().eventFilter(obj, event)

    def _move_top_right(self):
        screen = QGuiApplication.primaryScreen().geometry()
        self.move(screen.right() - self.width() - 12, 12)

    def _on_toggle(self, checked: bool):
        if checked:
            self._toggle_btn.setText("停止监听")
        else:
            self._toggle_btn.setText("开始监听")
        # 同步到迷你按钮
        self._mini_toggle.blockSignals(True)
        self._mini_toggle.setChecked(checked)
        self._mini_toggle.setText("停止" if checked else "开始")
        self._mini_toggle.blockSignals(False)

    def _on_mode_changed(self, _idx: int):
        mode = self._mode_combo.currentData()
        get_term_manager().set_mode(mode)
        # 持久化到配置文件, 重启后保持选择
        from src import config
        config.set_value("term.mode", mode)
        config.save()

    def set_mode(self, mode: str):
        for i in range(self._mode_combo.count()):
            if self._mode_combo.itemData(i) == mode:
                self._mode_combo.blockSignals(True)
                self._mode_combo.setCurrentIndex(i)
                self._mode_combo.blockSignals(False)
                break

    def _update_status(self):
        try:
            from .cache import stats
            self._cache_label.setText(f"缓存: {stats()} 条")
            summary = get_translator().usage_summary()
            parts = []
            for name, u in summary.items():
                used = u["chars"]
                limit = u["limit"]
                if limit:
                    parts.append(f"{name}: {used}/{limit}")
                else:
                    parts.append(f"{name}: {used}")
            self._usage_label.setText("用量: " + (" | ".join(parts) if parts else "无引擎"))
        except Exception:
            pass
