"""PDF 导入 + 整篇翻译 + 导出。

- 导入: PyMuPDF 提取文字(按页/按段落)
- 翻译: 调 Translator, 术语后处理, 单段失败不终止整体导出
- 导出模式:
    bilingual    — 上英下中对照 (Markdown 用引用块, PDF 用缩进中文)
    chinese_only — 仅中文
    page_insert  — 原页后插入译文页 (仅 PDF 格式)
- 导出格式: Markdown (.md) / PDF (.pdf)
"""
import os
import re
import threading
from pathlib import Path
from collections.abc import Callable

from .translator import get_translator
from .terms import get_term_manager
from . import config

# 导出模式常量, 供对话框引用
EXPORT_MODE_BILINGUAL = "bilingual"
EXPORT_MODE_CHINESE_ONLY = "chinese_only"
EXPORT_MODE_PAGE_INSERT = "page_insert"

# 导出格式常量
EXPORT_FMT_MD = "md"
EXPORT_FMT_PDF = "pdf"

_PARA_SEP = re.compile(r"\n{2,}")


def extract_paragraphs(pdf_path: str) -> list[tuple[int, str]]:
    """提取 PDF 文字, 返回 [(page_no, paragraph_text), ...]。

    尽量保留段落结构: 同页内以空行分隔的视为不同段。
    跳过纯空白与纯页码行。
    """
    import fitz  # PyMuPDF — 延迟导入, 避免拖慢启动

    doc = fitz.open(pdf_path)
    paragraphs = []
    for page_no in range(len(doc)):
        text = doc[page_no].get_text("text") or ""
        # 规范化: 多空格合并, 去行尾空白
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        chunks = _PARA_SEP.split(text)
        for chunk in chunks:
            chunk = chunk.strip()
            if not chunk:
                continue
            # 过滤纯页码行
            if chunk.isdigit() and len(chunk) <= 4:
                continue
            paragraphs.append((page_no + 1, chunk))
    doc.close()
    return paragraphs


def _register_chinese_font() -> str:
    """尝试注册系统中文字体, 返回字体名; 失败则返回 "Helvetica"。"""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    candidates = [
        ("C:/Windows/Fonts/msyh.ttc", "msyh"),
        ("C:/Windows/Fonts/msyhbd.ttc", "msyh"),
        ("C:/Windows/Fonts/simsun.ttc", "simsun"),
        ("C:/Windows/Fonts/simhei.ttf", "simhei"),
    ]
    for path, fn in candidates:
        if os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont(fn, path))
                return fn
            except Exception:
                continue
    return "Helvetica"


class PdfExporter:
    """后台线程执行翻译导出, 通过回调报告进度。

    单段翻译失败不会终止导出 — 失败段落以占位标记保留,
    最终 on_done 回调携带 (out_path, cancelled, failed_count)。
    """

    def __init__(self):
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def cancel(self):
        self._cancel.set()

    def export(self, pdf_path: str, out_path: str, *,
               mode: str = EXPORT_MODE_BILINGUAL,
               fmt: str = EXPORT_FMT_MD,
               on_progress: Callable[[int, int, int], None] | None = None,
               on_done: Callable[[str, bool, int], None] | None = None,
               on_error: Callable[[str], None] | None = None):
        """启动导出任务。

        on_done 签名: (out_path, cancelled, failed_count)
        """
        if self.is_running():
            if on_error:
                on_error("已有导出任务在运行")
            return
        self._cancel.clear()
        self._thread = threading.Thread(
            target=self._run,
            args=(pdf_path, out_path, mode, fmt, on_progress, on_done, on_error),
            daemon=True,
        )
        self._thread.start()

    def _run(self, pdf_path, out_path, mode, fmt, on_progress, on_done, on_error):
        failed_count = 0
        try:
            # page_insert 模式仅对 PDF 有意义, Markdown 退化为 bilingual
            effective_mode = mode
            if mode == EXPORT_MODE_PAGE_INSERT and fmt == EXPORT_FMT_MD:
                effective_mode = EXPORT_MODE_BILINGUAL

            paragraphs = extract_paragraphs(pdf_path)
            total = len(paragraphs)
            if total == 0:
                if on_error:
                    on_error("PDF 中未提取到任何文字(可能是扫描件, 暂不支持 OCR)")
                return

            translator = get_translator()
            term = get_term_manager()
            term_mode = term.mode
            term_sig = term.signature()

            results: list[tuple[int, str, str, str | None]] = []  # page, src, dst, err
            for i, (page_no, src) in enumerate(paragraphs):
                if self._cancel.is_set():
                    if on_done:
                        on_done(out_path, cancelled=True, failed_count=failed_count)
                    return
                try:
                    r = translator.translate(src, term_mode=term_mode, term_signature=term_sig)
                    dst = term.apply(src, r.text) if not r.error else ""
                    err = r.error
                    if err:
                        failed_count += 1
                except Exception as e:
                    dst = ""
                    err = str(e)
                    failed_count += 1
                results.append((page_no, src, dst, err))
                if on_progress:
                    on_progress(i + 1, total, page_no)

            # 写文件
            if fmt == EXPORT_FMT_MD:
                self._write_markdown(out_path, pdf_path, results, effective_mode, failed_count)
            elif effective_mode == EXPORT_MODE_PAGE_INSERT:
                self._write_pdf_page_insert(out_path, pdf_path, results)
            else:
                self._write_pdf(out_path, pdf_path, results, effective_mode, failed_count)

            if on_done:
                on_done(out_path, cancelled=False, failed_count=failed_count)
        except Exception as e:
            if on_error:
                on_error(str(e))

    # ── Markdown 导出 ─────────────────────────────────────────

    def _write_markdown(self, out_path: str, pdf_path: str,
                        results: list, mode: str, failed_count: int):
        name = Path(pdf_path).stem
        lines = [f"# {name} — 中文翻译", ""]
        last_page = 0
        for page_no, src, dst, err in results:
            # 页码分隔 (chinese_only 模式下也显示, 方便定位)
            if page_no != last_page:
                if last_page != 0:
                    lines.append("")
                lines.append(f"---")
                lines.append(f"### 第 {page_no} 页")
                lines.append("")
                last_page = page_no
            if mode == EXPORT_MODE_BILINGUAL:
                # 原文用普通段落, 译文用引用块
                lines.append(_wrap_text(src, width=80))
                lines.append("")
                if err:
                    lines.append(f"> ⚠ 翻译失败: {_escape(err)}")
                else:
                    lines.append(f"> {dst}")
            else:
                # chinese_only: 仅译文
                if err:
                    lines.append(f"⚠ 翻译失败: {_escape(err)}")
                else:
                    lines.append(dst)
            lines.append("")

        # 完成提示
        if failed_count > 0:
            lines.append("---")
            lines.append(f"*导出完成, 共 {failed_count} 段翻译失败。*")
        else:
            lines.append("---")
            lines.append("*导出完成, 全部段落翻译成功。*")

        Path(out_path).write_text("\n".join(lines), encoding="utf-8")

    # ── 简版 PDF 导出 ─────────────────────────────────────────

    def _write_pdf(self, out_path: str, pdf_path: str,
                   results: list, mode: str, failed_count: int):
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                         PageBreak, HRFlowable)
        from reportlab.lib.units import cm

        font_name = _register_chinese_font()

        doc = SimpleDocTemplate(out_path, pagesize=A4,
                                leftMargin=2 * cm, rightMargin=2 * cm,
                                topMargin=2 * cm, bottomMargin=2 * cm)
        styles = getSampleStyleSheet()

        # 样式定义
        style_h1 = ParagraphStyle("h1", parent=styles["Heading1"],
                                  fontName=font_name, fontSize=16, leading=22,
                                  spaceAfter=12)
        style_page = ParagraphStyle("page", parent=styles["Heading2"],
                                    fontName=font_name, fontSize=13, leading=18,
                                    textColor="#4a90e2", spaceBefore=10, spaceAfter=4)
        style_en = ParagraphStyle("en", parent=styles["BodyText"],
                                  fontName="Helvetica", fontSize=10, leading=14,
                                  textColor="#333333", spaceAfter=2)
        style_zh = ParagraphStyle("zh", parent=styles["BodyText"],
                                  fontName=font_name, fontSize=11, leading=17,
                                  textColor="#000000", leftIndent=12,
                                  spaceAfter=6)
        style_err = ParagraphStyle("err", parent=styles["BodyText"],
                                   fontName=font_name, fontSize=9,
                                   textColor="#cc0000", leftIndent=12,
                                   spaceAfter=6)
        style_summary = ParagraphStyle("summary", parent=styles["BodyText"],
                                       fontName=font_name, fontSize=10,
                                       textColor="#666666", alignment=1)

        story = [Paragraph(f"{Path(pdf_path).stem} — 中文翻译", style_h1),
                 Spacer(1, 0.3 * cm)]

        last_page = 0
        for page_no, src, dst, err in results:
            # 页码分隔
            if page_no != last_page:
                if last_page != 0:
                    story.append(PageBreak())
                story.append(HRFlowable(width="100%", thickness=0.5,
                                         color="#cccccc", spaceAfter=6))
                story.append(Paragraph(f"第 {page_no} 页", style_page))
                story.append(Spacer(1, 0.2 * cm))
                last_page = page_no

            if mode == EXPORT_MODE_BILINGUAL:
                story.append(Paragraph(_escape(src), style_en))
                story.append(Spacer(1, 0.1 * cm))
            if err:
                story.append(Paragraph(f"⚠ 翻译失败: {_escape(err)}", style_err))
            else:
                story.append(Paragraph(_escape(dst), style_zh))
            story.append(Spacer(1, 0.15 * cm))

        # 完成提示
        story.append(Spacer(1, 0.5 * cm))
        story.append(HRFlowable(width="100%", thickness=0.5,
                                 color="#cccccc", spaceAfter=6))
        if failed_count > 0:
            story.append(Paragraph(
                f"导出完成 — 共 {len(results)} 段, 其中 {failed_count} 段翻译失败。",
                style_summary))
        else:
            story.append(Paragraph(
                f"导出完成 — 全部 {len(results)} 段翻译成功。",
                style_summary))

        doc.build(story)

    # ── 原页后插入译文页 PDF ──────────────────────────────────

    def _write_pdf_page_insert(self, out_path: str, pdf_path: str,
                               results: list):
        """每页插入模式: 用 PyMuPDF 复制原 PDF, 每页后插入一页翻译。

        翻译页保留原文段落(灰色)+译文(黑色), 失败段落标红占位。
        """
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                         HRFlowable)
        from reportlab.lib.units import cm
        import io

        import fitz  # PyMuPDF — 延迟导入, 避免拖慢启动

        font_name = _register_chinese_font()

        # 按页分组翻译结果
        page_map: dict[int, list[tuple[str, str, str | None]]] = {}
        for page_no, src, dst, err in results:
            page_map.setdefault(page_no, []).append((src, dst, err))

        src_doc = fitz.open(pdf_path)
        out_doc = fitz.open()

        styles = getSampleStyleSheet()
        style_page = ParagraphStyle("tpage", parent=styles["Heading2"],
                                    fontName=font_name, fontSize=13, leading=18,
                                    textColor="#4a90e2", spaceAfter=6)
        style_en = ParagraphStyle("ten", parent=styles["BodyText"],
                                  fontName="Helvetica", fontSize=9, leading=12,
                                  textColor="#888888", spaceAfter=2)
        style_zh = ParagraphStyle("tzh", parent=styles["BodyText"],
                                  fontName=font_name, fontSize=11, leading=16,
                                  textColor="#000000", spaceAfter=6)
        style_err = ParagraphStyle("terr", parent=styles["BodyText"],
                                   fontName=font_name, fontSize=9,
                                   textColor="#cc0000", spaceAfter=4)

        for page_no in range(1, len(src_doc) + 1):
            # 1. 复制原页
            out_doc.insert_pdf(src_doc, from_page=page_no - 1, to_page=page_no - 1)

            # 2. 生成翻译页
            items = page_map.get(page_no, [])
            if not items:
                continue

            story = []
            story.append(Paragraph(f"第 {page_no} 页 — 中文翻译", style_page))
            story.append(Spacer(1, 0.3 * cm))
            story.append(HRFlowable(width="100%", thickness=0.5,
                                     color="#cccccc", spaceAfter=6))

            for src, dst, err in items:
                if src:
                    story.append(Paragraph(_escape(src), style_en))
                if err:
                    story.append(Paragraph(f"⚠ 翻译失败: {_escape(err)}", style_err))
                else:
                    story.append(Paragraph(_escape(dst), style_zh))

            buf = io.BytesIO()
            tmp_doc = SimpleDocTemplate(buf, pagesize=A4,
                                        leftMargin=2 * cm, rightMargin=2 * cm,
                                        topMargin=2 * cm, bottomMargin=2 * cm)
            tmp_doc.build(story)
            buf.seek(0)
            trans_doc = fitz.open("pdf", buf.read())
            out_doc.insert_pdf(trans_doc)
            trans_doc.close()

        out_doc.save(out_path)
        out_doc.close()
        src_doc.close()


# ── 工具函数 ──────────────────────────────────────────────────

def _escape(text: str) -> str:
    """XML/ReportLab 转义。"""
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _wrap_text(text: str, width: int = 80) -> str:
    """在 Markdown 中适度折行, 保留原文可读性。"""
    text = text or ""
    if len(text) <= width:
        return text
    # 简单按单词折行, 不引入额外依赖
    result = []
    line = ""
    for word in text.split(" "):
        if len(word) > width:
            if line:
                result.append(line.rstrip())
                line = ""
            result.append(word)
            continue
        if line and len(line) + 1 + len(word) > width:
            result.append(line.rstrip())
            line = word
        else:
            line = line + " " + word if line else word
    if line:
        result.append(line.rstrip())
    return "\n".join(result)
