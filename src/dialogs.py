"""对话框: PDF 导出 + 术语表管理 + 设置(引擎/划词监听/悬浮窗/通用)。"""
import os
import sys
import winreg
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QProgressBar, QCheckBox, QComboBox, QFileDialog, QMessageBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QLineEdit, QInputDialog,
    QGroupBox, QFormLayout, QTabWidget, QWidget, QSpinBox, QApplication,
)

from .translator import get_translator
from .terms import get_term_manager
from .pdf_export import (
    PdfExporter,
    EXPORT_MODE_BILINGUAL, EXPORT_MODE_CHINESE_ONLY, EXPORT_MODE_PAGE_INSERT,
    EXPORT_FMT_MD, EXPORT_FMT_PDF,
)
from . import config


class PdfExportDialog(QDialog):
    """选择 PDF -> 选输出格式 -> 显示进度 -> 完成。

    后台翻译线程通过 Qt Signal 把进度/完成/错误转发到主线程,
    避免跨线程直接操作控件导致卡死。
    """

    # 跨线程信号: 参数为 (done, total, page_no)
    progress_sig = Signal(int, int, int)
    # 跨线程信号: 参数为 (out_path, cancelled, failed_count)
    done_sig = Signal(str, bool, int)
    # 跨线程信号: 参数为 (msg,)
    error_sig = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("导入 PDF 翻译")
        self.setMinimumWidth(460)
        self._pdf_path = ""
        self._out_path = ""
        self._exporter = PdfExporter()

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # 文件选择
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("PDF 文件:"))
        self._file_label = QLabel("(未选择)")
        self._file_label.setStyleSheet("color: #666;")
        row1.addWidget(self._file_label, 1)
        b_pick = QPushButton("选择…")
        b_pick.clicked.connect(self._pick_pdf)
        row1.addWidget(b_pick)
        layout.addLayout(row1)

        # 选项行 1: 模式 + 格式
        opts1 = QHBoxLayout()
        opts1.addWidget(QLabel("模式:"))
        self._mode_combo = QComboBox()
        self._mode_combo.addItem("上英下中对照", EXPORT_MODE_BILINGUAL)
        self._mode_combo.addItem("仅中文", EXPORT_MODE_CHINESE_ONLY)
        self._mode_combo.addItem("原页后插入译文页", EXPORT_MODE_PAGE_INSERT)
        opts1.addWidget(self._mode_combo)
        opts1.addSpacing(12)
        opts1.addWidget(QLabel("格式:"))
        self._fmt_combo = QComboBox()
        self._fmt_combo.addItem("Markdown (.md)", EXPORT_FMT_MD)
        self._fmt_combo.addItem("PDF (.pdf)", EXPORT_FMT_PDF)
        self._fmt_combo.currentIndexChanged.connect(self._on_fmt_changed)
        opts1.addWidget(self._fmt_combo)
        layout.addLayout(opts1)

        # 选项行 2: 提示
        self._mode_hint = QLabel("")
        self._mode_hint.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(self._mode_hint)

        # 进度
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        layout.addWidget(self._progress)
        self._status = QLabel("准备就绪")
        self._status.setStyleSheet("color: #666;")
        layout.addWidget(self._status)

        # 按钮
        btns = QHBoxLayout()
        self._cancel_btn = QPushButton("取消")
        self._cancel_btn.clicked.connect(self.reject)
        self._export_btn = QPushButton("开始翻译导出")
        self._export_btn.clicked.connect(self._start)
        btns.addStretch()
        btns.addWidget(self._cancel_btn)
        btns.addWidget(self._export_btn)
        layout.addLayout(btns)

        # 信号连接: 后台线程的回调通过 Signal 转发到主线程更新 UI
        self.progress_sig.connect(self._on_progress)
        self.done_sig.connect(self._on_done)
        self.error_sig.connect(self._on_error)

        self._on_fmt_changed(0)

    def _on_fmt_changed(self, _idx):
        """格式切换时更新模式提示。"""
        fmt = self._fmt_combo.currentData()
        mode = self._mode_combo.currentData()
        if fmt == EXPORT_FMT_MD and mode == EXPORT_MODE_PAGE_INSERT:
            self._mode_hint.setText("提示: Markdown 不支持「原页后插入」, 将退化为上英下中对照。")
        elif mode == EXPORT_MODE_PAGE_INSERT:
            self._mode_hint.setText("提示: 将原 PDF 每页后插入一页中文翻译。")
        else:
            self._mode_hint.setText("")

    def _pick_pdf(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 PDF", "", "PDF 文件 (*.pdf)")
        if path:
            self._pdf_path = path
            self._file_label.setText(Path(path).name)
            self._file_label.setStyleSheet("color: #000;")

    def _start(self):
        if not self._pdf_path:
            QMessageBox.warning(self, "提示", "请先选择 PDF 文件")
            return
        if not get_translator().available():
            QMessageBox.warning(self, "无可用引擎",
                                "请先在 config.properties 配置 DeepL 或百度翻译 API key")
            return
        fmt = self._fmt_combo.currentData()
        suffix = "md" if fmt == EXPORT_FMT_MD else "pdf"
        # 默认保存到用户文档目录(exe 打包后 cwd 可能是系统目录, 无写权限)
        docs = os.path.expanduser("~/Documents")
        os.makedirs(docs, exist_ok=True)
        default_name = os.path.join(docs, Path(self._pdf_path).stem + f"_zh.{suffix}")
        out_path, _ = QFileDialog.getSaveFileName(
            self, "保存到", default_name,
            "Markdown (*.md)" if fmt == EXPORT_FMT_MD else "PDF (*.pdf)")
        if not out_path:
            return
        self._out_path = out_path
        self._export_btn.setEnabled(False)
        self._cancel_btn.setText("取消导出")
        self._status.setText("开始翻译…")

        self._exporter.export(
            self._pdf_path, out_path,
            mode=self._mode_combo.currentData(),
            fmt=fmt,
            on_progress=lambda d, t, p: self.progress_sig.emit(d, t, p),
            on_done=lambda path, cancelled, failed: self.done_sig.emit(path, cancelled, failed),
            on_error=lambda msg: self.error_sig.emit(msg),
        )

    def _on_progress(self, done: int, total: int, page_no: int):
        pct = int(done * 100 / total) if total else 0
        self._progress.setValue(pct)
        self._status.setText(f"已译 {done}/{total} 段 (当前第 {page_no} 页)")

    def _on_done(self, out_path: str, cancelled: bool, failed_count: int):
        self._progress.setValue(0 if cancelled else 100)
        self._export_btn.setEnabled(True)
        self._cancel_btn.setText("取消")
        if cancelled:
            self._status.setText("已取消")
            return
        if failed_count > 0:
            self._status.setText(f"完成 (含 {failed_count} 段失败): {out_path}")
            QMessageBox.information(
                self, "导出完成",
                f"导出完成, 共 {failed_count} 段翻译失败。\n\n文件: {out_path}\n\n失败段落已在文件中以占位标记保留。\n\n是否打开所在目录?")
        else:
            self._status.setText(f"完成: {out_path}")
            QMessageBox.information(self, "完成", f"导出完成:\n{out_path}\n\n是否打开所在目录?")
        # 询问打开目录 — 使用 QMessageBox 的默认按钮判断
        reply = QMessageBox.question(
            self, "打开目录", "是否打开文件所在目录?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if reply == QMessageBox.Yes:
            os.startfile(str(Path(out_path).parent))
        self.accept()

    def _on_error(self, msg: str):
        self._status.setText(f"错误: {msg}")
        self._export_btn.setEnabled(True)
        self._cancel_btn.setText("取消")
        QMessageBox.critical(self, "错误", msg)

    def reject(self):
        # 若正在运行, 通知取消
        if self._exporter.is_running():
            self._exporter.cancel()
            self._status.setText("正在取消…")
            self._cancel_btn.setEnabled(False)
            return
        super().reject()


# ============================================================
# 术语表管理对话框
# ============================================================

class TermsDialog(QDialog):
    """术语表管理: 切换启用表、增删词条。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("术语表管理")
        self.setMinimumSize(560, 460)
        self._term = get_term_manager()

        layout = QVBoxLayout(self)

        # 表选择
        top = QHBoxLayout()
        top.addWidget(QLabel("术语表:"))
        self._table_combo = QComboBox()
        self._table_combo.currentIndexChanged.connect(self._on_table_changed)
        top.addWidget(self._table_combo, 1)
        self._active_cb = QCheckBox("启用此表")
        self._active_cb.toggled.connect(self._on_toggle_active)
        top.addWidget(self._active_cb)
        layout.addLayout(top)

        # 词条表
        self._grid = QTableWidget(0, 2)
        self._grid.setHorizontalHeaderLabels(["English", "中文"])
        self._grid.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._grid.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        layout.addWidget(self._grid, 1)

        # 操作
        row = QHBoxLayout()
        b_add = QPushButton("+ 添加词条")
        b_add.clicked.connect(self._add_term)
        b_del = QPushButton("删除选中")
        b_del.clicked.connect(self._del_term)
        b_new = QPushButton("新建术语表")
        b_new.clicked.connect(self._new_table)
        row.addWidget(b_add)
        row.addWidget(b_del)
        row.addStretch()
        row.addWidget(b_new)
        layout.addLayout(row)

        self._refresh_combo()

    def _refresh_combo(self):
        self._table_combo.blockSignals(True)
        self._table_combo.clear()
        for name in self._term.all_table_names():
            self._table_combo.addItem(name)
        # 确保至少有表可显示
        if self._table_combo.count() == 0:
            self._table_combo.addItem("default")
        self._table_combo.blockSignals(False)
        self._on_table_changed(0)

    def _current_table(self) -> str:
        return self._table_combo.currentText() or "default"

    def _on_table_changed(self, _idx):
        name = self._current_table()
        self._active_cb.blockSignals(True)
        self._active_cb.setChecked(name in self._term.active_tables)
        self._active_cb.blockSignals(False)
        self._load_terms(name)

    def _on_toggle_active(self, on: bool):
        self._term.toggle_table(self._current_table(), on)

    def _load_terms(self, name: str):
        tbl = self._term.tables.get(name, {})
        self._grid.setRowCount(0)
        for en, zh in tbl.items():
            r = self._grid.rowCount()
            self._grid.insertRow(r)
            self._grid.setItem(r, 0, QTableWidgetItem(en))
            self._grid.setItem(r, 1, QTableWidgetItem(zh))

    def _add_term(self):
        en, ok = QInputDialog.getText(self, "添加词条", "English:")
        if not ok or not en.strip():
            return
        zh, ok = QInputDialog.getText(self, "添加词条", "中文译法:")
        if not ok or not zh.strip():
            return
        self._term.add_term(self._current_table(), en.strip(), zh.strip())
        self._load_terms(self._current_table())

    def _del_term(self):
        row = self._grid.currentRow()
        if row < 0:
            return
        en = self._grid.item(row, 0).text()
        self._term.remove_term(self._current_table(), en)
        self._load_terms(self._current_table())

    def _new_table(self):
        name, ok = QInputDialog.getText(self, "新建术语表", "表名(英文):")
        if not ok or not name.strip():
            return
        name = name.strip()
        self._term.load_table(name, {})  # 建一张空表
        self._term.toggle_table(name, True)
        self._refresh_combo()
        # 选中新表
        idx = self._table_combo.findText(name)
        if idx >= 0:
            self._table_combo.setCurrentIndex(idx)


# ============================================================
# 设置对话框: 翻译引擎 + 划词监听 + 悬浮窗
# ============================================================

ENGINE_LABELS = {
    "deepl": "DeepL (推荐, 质量最佳)",
    "baidu": "百度翻译 (国内备选)",
    "openai": "OpenAI / 兼容AI (GPT/DeepSeek/通义等)",
}

MODE_LABELS = {
    "chinese_only": "仅中文",
    "bilingual": "原文+译文",
    "glossary": "术语覆盖",
}


class SettingsDialog(QDialog):
    """带标签页的设置对话框。

    标签页: 翻译引擎 | 划词监听 | 悬浮窗
    保存后立即应用到监听器和悬浮窗, 不要求重启。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setMinimumWidth(580)
        self.setMinimumHeight(480)

        layout = QVBoxLayout(self)

        # 默认引擎选择 (全局, 放标签页上方)
        eng_box = QGroupBox("默认翻译引擎")
        eng_layout = QVBoxLayout(eng_box)
        eng_layout.addWidget(QLabel("选择默认引擎(失败时自动切其他可用引擎):"))
        self._default_combo = QComboBox()
        for k, v in ENGINE_LABELS.items():
            self._default_combo.addItem(v, k)
        eng_layout.addWidget(self._default_combo)

        # 测试连接 + 网络超时
        diag_row = QHBoxLayout()
        b_test = QPushButton("诊断: 测试连接")
        b_test.clicked.connect(self._test_connection)
        diag_row.addWidget(b_test)
        diag_row.addWidget(QLabel("超时(秒):"))
        self._timeout_edit = QLineEdit()
        self._timeout_edit.setFixedWidth(50)
        self._timeout_edit.setPlaceholderText("15")
        diag_row.addWidget(self._timeout_edit)
        diag_row.addStretch()
        eng_layout.addLayout(diag_row)
        layout.addWidget(eng_box)

        # 标签页: 引擎配置 + 划词监听 + 悬浮窗 + 通用
        tabs = QTabWidget()
        tabs.addTab(self._build_engines_tab(), "翻译引擎")
        tabs.addTab(self._build_selection_tab(), "划词监听")
        tabs.addTab(self._build_popup_tab(), "悬浮窗")
        tabs.addTab(self._build_general_tab(), "通用")
        tabs.addTab(self._build_about_tab(), "关于")
        layout.addWidget(tabs, 1)

        # 底部按钮
        btns = QHBoxLayout()
        b_reset = QPushButton("重置本月用量")
        b_reset.clicked.connect(self._reset_usage)
        btns.addWidget(b_reset)
        btns.addStretch()
        b_save = QPushButton("保存并应用")
        b_save.setDefault(True)
        b_save.clicked.connect(self._save)
        b_cancel = QPushButton("取消")
        b_cancel.clicked.connect(self.reject)
        btns.addWidget(b_save)
        btns.addWidget(b_cancel)
        layout.addLayout(btns)

        self._load_current()

    # ── 翻译引擎标签页 ──────────────────────────────────────

    def _build_engines_tab(self) -> QWidget:
        """引擎配置: DeepL / 百度 / OpenAI 三个子标签页。"""
        sub = QTabWidget()
        sub.addTab(self._build_deepl_tab(), "DeepL")
        sub.addTab(self._build_baidu_tab(), "百度翻译")
        sub.addTab(self._build_openai_tab(), "OpenAI/兼容AI")
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(sub)
        return w

    def _build_deepl_tab(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        f.setContentsMargins(16, 16, 16, 16)
        link = QLabel('DeepL Free API, 注册地址: <a href="https://www.deepl.com/pro-api">https://www.deepl.com/pro-api</a>')
        link.setOpenExternalLinks(True)
        link.setTextInteractionFlags(Qt.TextBrowserInteraction)
        f.addRow(link)
        self._deepl_key = QLineEdit()
        self._deepl_key.setPlaceholderText("Authentication Key, 形如 xxx-xxx-xxx:fx")
        self._deepl_key.setEchoMode(QLineEdit.Password)
        f.addRow("API Key:", self._deepl_key)
        return w

    def _build_baidu_tab(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        f.setContentsMargins(16, 16, 16, 16)
        link = QLabel('百度翻译开放平台, 注册地址: <a href="https://fanyi-api.baidu.com">https://fanyi-api.baidu.com</a>')
        link.setOpenExternalLinks(True)
        link.setTextInteractionFlags(Qt.TextBrowserInteraction)
        f.addRow(link)
        self._baidu_id = QLineEdit()
        self._baidu_id.setPlaceholderText("APP ID")
        f.addRow("APP ID:", self._baidu_id)
        self._baidu_secret = QLineEdit()
        self._baidu_secret.setPlaceholderText("密钥")
        self._baidu_secret.setEchoMode(QLineEdit.Password)
        f.addRow("密钥:", self._baidu_secret)
        return w

    def _build_openai_tab(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        f.setContentsMargins(16, 16, 16, 16)
        f.addRow(QLabel("兼容 OpenAI Chat API 格式, 可对接 GPT/DeepSeek/通义千问/Ollama 等"))
        self._openai_key = QLineEdit()
        self._openai_key.setPlaceholderText("sk-...")
        self._openai_key.setEchoMode(QLineEdit.Password)
        f.addRow("API Key:", self._openai_key)
        self._openai_base = QLineEdit()
        self._openai_base.setPlaceholderText("https://api.openai.com/v1")
        f.addRow("Base URL:", self._openai_base)
        self._openai_model = QLineEdit()
        self._openai_model.setPlaceholderText("gpt-4o-mini")
        f.addRow("模型:", self._openai_model)

        # 快捷预设
        preset_box = QGroupBox("快捷预设(点击填入 Base URL 和模型)")
        preset_layout = QHBoxLayout(preset_box)
        for label, url, model in [
            ("OpenAI", "https://api.openai.com/v1", "gpt-4o-mini"),
            ("DeepSeek", "https://api.deepseek.com/v1", "deepseek-chat"),
            ("通义千问", "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-turbo"),
            ("Moonshot", "https://api.moonshot.cn/v1", "moonshot-v1-8k"),
            ("本地Ollama", "http://localhost:11434/v1", "qwen2.5:7b"),
        ]:
            btn = QPushButton(label)
            btn.clicked.connect(
                lambda checked, u=url, m=model: self._apply_preset(u, m)
            )
            preset_layout.addWidget(btn)
        f.addRow(preset_box)
        return w

    # ── 划词监听标签页 ──────────────────────────────────────

    def _build_selection_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        # 字符范围
        grp_len = QGroupBox("选区长度限制")
        flen = QFormLayout(grp_len)
        self._sel_min = QSpinBox()
        self._sel_min.setRange(1, 100)
        self._sel_min.setToolTip("少于该字符数的选区不触发翻译")
        flen.addRow("最小字符数:", self._sel_min)
        self._sel_max = QSpinBox()
        self._sel_max.setRange(10, 50000)
        self._sel_max.setToolTip("超过该字符数的选区不触发翻译")
        flen.addRow("最大字符数:", self._sel_max)
        layout.addWidget(grp_len)

        # 防抖与去重
        grp_timing = QGroupBox("触发控制")
        ftime = QFormLayout(grp_timing)
        self._sel_debounce = QSpinBox()
        self._sel_debounce.setRange(50, 5000)
        self._sel_debounce.setSuffix(" ms")
        self._sel_debounce.setToolTip("两次划词之间的最小间隔, 防止连击误触发")
        ftime.addRow("防抖间隔:", self._sel_debounce)
        self._sel_dup_window = QSpinBox()
        self._sel_dup_window.setRange(0, 30000)
        self._sel_dup_window.setSuffix(" ms")
        self._sel_dup_window.setToolTip("相同文本在此时间窗口内不重复翻译 (0=关闭)")
        ftime.addRow("重复窗口:", self._sel_dup_window)
        layout.addWidget(grp_timing)

        # 过滤规则
        grp_filter = QGroupBox("过滤规则 (勾选 = 忽略, 不触发翻译)")
        ffil = QVBoxLayout(grp_filter)
        self._sel_ascii = QCheckBox("仅含英文字母的文本才触发翻译")
        self._sel_ascii.setToolTip("避免在中文输入框中选中文本时误触发")
        ffil.addWidget(self._sel_ascii)
        self._sel_ignore_url = QCheckBox("忽略网址 (http/www)")
        ffil.addWidget(self._sel_ignore_url)
        self._sel_ignore_email = QCheckBox("忽略邮箱地址")
        ffil.addWidget(self._sel_ignore_email)
        self._sel_ignore_path = QCheckBox("忽略 Windows 文件路径")
        ffil.addWidget(self._sel_ignore_path)
        self._sel_ignore_symbol = QCheckBox("忽略纯符号选区")
        ffil.addWidget(self._sel_ignore_symbol)
        self._sel_ignore_num = QCheckBox("忽略主要含数字的选区 (≥80%)")
        ffil.addWidget(self._sel_ignore_num)
        layout.addWidget(grp_filter)

        layout.addStretch()
        return w

    # ── 悬浮窗标签页 ────────────────────────────────────────

    def _build_popup_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        # 显示
        grp_display = QGroupBox("显示设置")
        fdisp = QFormLayout(grp_display)
        self._popup_max_src = QSpinBox()
        self._popup_max_src.setRange(60, 2000)
        self._popup_max_src.setToolTip("原文超过此长度时折叠, 可点击展开(仅原文+译文模式生效)")
        fdisp.addRow("原文折叠长度 (原文+译文模式):", self._popup_max_src)
        layout.addWidget(grp_display)

        # 窗口行为
        grp_behavior = QGroupBox("窗口行为")
        fbeh = QFormLayout(grp_behavior)
        self._popup_max_h = QSpinBox()
        self._popup_max_h.setRange(30, 90)
        self._popup_max_h.setSuffix(" %")
        self._popup_max_h.setToolTip("悬浮窗最大高度占屏幕高度的百分比")
        fbeh.addRow("最大高度:", self._popup_max_h)
        self._popup_pinned = QCheckBox("默认固定窗口")
        self._popup_pinned.setToolTip("固定后鼠标/键盘输入不会隐藏窗口")
        fbeh.addRow("", self._popup_pinned)
        layout.addWidget(grp_behavior)

        layout.addStretch()
        return w

    # ── 通用标签页 ──────────────────────────────────────────

    def _build_general_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        grp_startup = QGroupBox("启动行为")
        fstart = QFormLayout(grp_startup)
        self._auto_start_cb = QCheckBox("开机自动运行")
        self._auto_start_cb.setToolTip("在 Windows 启动时自动启动简单翻译\n(通过注册表 HKCU\\...\\Run 实现)")
        fstart.addRow("", self._auto_start_cb)
        self._auto_listen_cb = QCheckBox("启动时自动启用划词翻译")
        self._auto_listen_cb.setToolTip("程序启动后自动开启划词监听, 无需手动点击开启按钮")
        fstart.addRow("", self._auto_listen_cb)
        layout.addWidget(grp_startup)

        layout.addStretch()
        return w

    # ── 关于标签页 ──────────────────────────────────────────

    def _build_about_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.addStretch()

        lbl_made = QLabel("Made by WWWATER-H")
        lbl_made.setAlignment(Qt.AlignCenter)
        lbl_made.setStyleSheet("font-size: 16px; font-weight: bold; color: #333;")
        layout.addWidget(lbl_made)

        layout.addSpacing(12)

        lbl_warn = QLabel("如果你是购买来的，说明你被骗了，这是一个开源项目。")
        lbl_warn.setAlignment(Qt.AlignCenter)
        lbl_warn.setStyleSheet("font-size: 12px; color: #999;")
        lbl_warn.setWordWrap(True)
        layout.addWidget(lbl_warn)

        layout.addStretch()
        return w

    # ── 加载 / 保存 ──────────────────────────────────────────

    def _load_current(self):
        """从 config 加载当前值填入界面。"""
        # 引擎
        default_eng = config.get("translate.default_engine", "deepl")
        for i in range(self._default_combo.count()):
            if self._default_combo.itemData(i) == default_eng:
                self._default_combo.setCurrentIndex(i)
                break
        self._deepl_key.setText(config.get("deepl.api_key", ""))
        self._baidu_id.setText(config.get("baidu.app_id", ""))
        self._baidu_secret.setText(config.get("baidu.secret_key", ""))
        self._openai_key.setText(config.get("openai.api_key", ""))
        self._openai_base.setText(config.get("openai.base_url", "https://api.openai.com/v1"))
        self._openai_model.setText(config.get("openai.model", "gpt-4o-mini"))
        self._timeout_edit.setText(config.get("network.timeout", "15"))

        # 划词监听
        self._sel_min.setValue(config.get_int("selection.min_chars", 2))
        self._sel_max.setValue(config.get_int("selection.max_chars", 5000))
        self._sel_debounce.setValue(config.get_int("selection.debounce_ms", 350))
        self._sel_dup_window.setValue(config.get_int("selection.duplicate_window_ms", 2000))
        self._sel_ascii.setChecked(self._cfg_bool("selection.require_ascii_letter", True))
        self._sel_ignore_url.setChecked(self._cfg_bool("selection.ignore_urls", True))
        self._sel_ignore_email.setChecked(self._cfg_bool("selection.ignore_emails", True))
        self._sel_ignore_path.setChecked(self._cfg_bool("selection.ignore_paths", True))
        self._sel_ignore_symbol.setChecked(self._cfg_bool("selection.ignore_symbols", True))
        self._sel_ignore_num.setChecked(self._cfg_bool("selection.ignore_mostly_numbers", True))

        # 悬浮窗
        self._popup_max_src.setValue(config.get_int("popup.max_source_chars", 240))
        self._popup_max_h.setValue(config.get_int("popup.max_height_percent", 70))
        self._popup_pinned.setChecked(self._cfg_bool("popup.default_pinned", False))

        # 通用
        self._auto_start_cb.setChecked(self._is_autostart_enabled())
        self._auto_listen_cb.setChecked(self._cfg_bool("app.auto_listen", False))

    def _save(self):
        """保存配置并立即应用到监听器和悬浮窗, 然后关闭对话框。"""
        self._save_config()
        self._apply_live()
        QMessageBox.information(self, "已保存", "配置已保存并应用。")
        self.accept()

    def _save_config(self):
        """保存配置到文件并重新加载引擎。"""
        # 引擎
        config.set_value("translate.default_engine", self._default_combo.currentData())
        config.set_value("deepl.api_key", self._deepl_key.text().strip())
        config.set_value("baidu.app_id", self._baidu_id.text().strip())
        config.set_value("baidu.secret_key", self._baidu_secret.text().strip())
        config.set_value("openai.api_key", self._openai_key.text().strip())
        config.set_value("openai.base_url", self._openai_base.text().strip() or "https://api.openai.com/v1")
        config.set_value("openai.model", self._openai_model.text().strip() or "gpt-4o-mini")
        timeout_val = self._timeout_edit.text().strip()
        try:
            int(timeout_val)
        except ValueError:
            timeout_val = "15"
        config.set_value("network.timeout", timeout_val)

        # 划词监听
        config.set_value("selection.min_chars", str(self._sel_min.value()))
        config.set_value("selection.max_chars", str(self._sel_max.value()))
        config.set_value("selection.debounce_ms", str(self._sel_debounce.value()))
        config.set_value("selection.duplicate_window_ms", str(self._sel_dup_window.value()))
        config.set_value("selection.require_ascii_letter", "1" if self._sel_ascii.isChecked() else "0")
        config.set_value("selection.ignore_urls", "1" if self._sel_ignore_url.isChecked() else "0")
        config.set_value("selection.ignore_emails", "1" if self._sel_ignore_email.isChecked() else "0")
        config.set_value("selection.ignore_paths", "1" if self._sel_ignore_path.isChecked() else "0")
        config.set_value("selection.ignore_symbols", "1" if self._sel_ignore_symbol.isChecked() else "0")
        config.set_value("selection.ignore_mostly_numbers", "1" if self._sel_ignore_num.isChecked() else "0")

        # 悬浮窗
        config.set_value("popup.max_source_chars", str(self._popup_max_src.value()))
        config.set_value("popup.max_height_percent", str(self._popup_max_h.value()))
        config.set_value("popup.default_pinned", "1" if self._popup_pinned.isChecked() else "0")

        # 通用
        config.set_value("app.auto_listen", "1" if self._auto_listen_cb.isChecked() else "0")
        self._set_autostart_registry(self._auto_start_cb.isChecked())

        try:
            config.save()
            get_translator().reload()
        except Exception as e:
            QMessageBox.critical(self, "保存失败", f"保存配置失败:\n{e}")

    def _apply_live(self):
        """立即应用设置到监听器(实时读配置)和悬浮窗(需主动调 apply_config)。"""
        try:
            app = QApplication.instance()
            if app is not None:
                ctrl = getattr(app, '_simple_translate_controller', None)
                if ctrl is not None and ctrl._popup is not None:
                    ctrl._popup.apply_config()
        except Exception:
            pass  # 离屏/测试环境无完整 controller, 忽略

    def _reset_usage(self):
        """重置本月用量统计(归零)。"""
        ret = QMessageBox.question(
            self, "重置用量",
            "确定要把所有引擎的本月用量统计归零吗?\n(不影响 API 服务端的真实用量, 只重置本地记录)",
            QMessageBox.Yes | QMessageBox.No,
        )
        if ret != QMessageBox.Yes:
            return
        get_translator().reset_usage()
        QMessageBox.information(self, "已重置", "本月用量已归零。")

    def _test_connection(self):
        """诊断: 测试各引擎连通性。先保存当前配置(不关闭对话框), 再逐个测试。"""
        self._save_config()
        from PySide6.QtCore import QThread, Signal as QSignal

        class TestThread(QThread):
            done = QSignal(dict)

            def run(self):
                results = get_translator().test_all()
                self.done.emit(results)

        self._test_thread = TestThread(self)
        self._test_thread.done.connect(self._show_test_results)
        self._test_thread.start()
        self._apply_live()

    def _show_test_results(self, results: dict):
        """显示测试结果。"""
        lines = ["引擎连通性测试结果:", ""]
        for name, (ok, msg) in results.items():
            mark = "✓" if ok else "✗"
            lines.append(f"{mark} {name}: {msg}")
        lines.append("")
        lines.append("提示: 失败的引擎在翻译时会自动跳过, 使用其他可用引擎。")
        QMessageBox.information(self, "诊断结果", "\n".join(lines))

    # ── 开机自动运行 (注册表) ───────────────────────────────

    _AUTOSTART_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
    _AUTOSTART_NAME = "SimpleTranslate"

    def _get_autostart_path(self) -> str:
        """返回用于注册表的可执行文件路径。"""
        if getattr(sys, 'frozen', False):
            # 打包后的 exe
            return sys.executable
        else:
            # 开发环境: pythonw.exe + 脚本路径 (无控制台窗口)
            pythonw = os.path.join(os.path.dirname(sys.executable), 'pythonw.exe')
            script = os.path.abspath(sys.argv[0] if sys.argv else __file__)
            return f'"{pythonw}" "{script}"'

    def _is_autostart_enabled(self) -> bool:
        """检查注册表中是否已设置开机自启。"""
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                               self._AUTOSTART_KEY, 0, winreg.KEY_READ) as key:
                winreg.QueryValueEx(key, self._AUTOSTART_NAME)
                return True
        except FileNotFoundError:
            return False

    def _set_autostart_registry(self, enabled: bool):
        """设置或移除开机自启注册表项。失败时弹警告。"""
        try:
            if enabled:
                path = self._get_autostart_path()
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                   self._AUTOSTART_KEY, 0, winreg.KEY_SET_VALUE) as key:
                    winreg.SetValueEx(key, self._AUTOSTART_NAME, 0, winreg.REG_SZ, path)
            else:
                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                       self._AUTOSTART_KEY, 0, winreg.KEY_SET_VALUE) as key:
                        winreg.DeleteValue(key, self._AUTOSTART_NAME)
                except FileNotFoundError:
                    pass  # 本来就没有, 无需删除
        except Exception as e:
            QMessageBox.warning(self, "开机自启设置失败",
                               f"{'设置' if enabled else '取消'}开机自动运行失败:\n{e}")

    # ── 工具 ──────────────────────────────────────────────────

    @staticmethod
    def _cfg_bool(key: str, default: bool) -> bool:
        raw = config.get(key, "1" if default else "0").strip().lower()
        return raw in {"1", "true", "yes", "on", "y"}

    def _apply_preset(self, url: str, model: str):
        self._openai_base.setText(url)
        self._openai_model.setText(model)
