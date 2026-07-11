"""后台翻译任务: 让网络请求离开 Qt 主线程。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import itertools

from PySide6.QtCore import QObject, Signal


@dataclass
class TranslationTaskResult:
    task_id: int
    source: str
    target: str
    engine: str
    cached: bool
    error: str | None


class TranslationTaskRunner(QObject):
    """串行执行翻译任务, 只让最新任务更新界面。"""

    started = Signal(int, str)
    finished = Signal(object)

    def __init__(self, translator, term, parent=None):
        super().__init__(parent)
        self._translator = translator
        self._term = term
        self._ids = itertools.count(1)
        self._latest_task_id = 0
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="translate")

    def submit(self, text: str) -> int:
        task_id = next(self._ids)
        self._latest_task_id = task_id
        self.started.emit(task_id, text)
        future = self._executor.submit(self._translate, task_id, text)
        future.add_done_callback(self._emit_result)
        return task_id

    def is_latest(self, task_id: int) -> bool:
        return task_id == self._latest_task_id

    def shutdown(self):
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _translate(self, task_id: int, text: str) -> TranslationTaskResult:
        mode = self._term.mode
        sig = self._term.signature()
        result = self._translator.translate(text, term_mode=mode, term_signature=sig)
        target = self._term.apply(text, result.text) if not result.error else ""
        return TranslationTaskResult(
            task_id=task_id,
            source=text,
            target=target,
            engine=result.engine,
            cached=result.cached,
            error=_friendly_error(result.error),
        )

    def _emit_result(self, future):
        try:
            result = future.result()
        except Exception as exc:
            result = TranslationTaskResult(
                task_id=self._latest_task_id,
                source="",
                target="",
                engine="none",
                cached=False,
                error=_friendly_error(str(exc)),
            )
        self.finished.emit(result)


def _friendly_error(error: str | None) -> str | None:
    """把原始异常/错误信息转为用户可读的中文提示。"""
    if not error:
        return None
    text = str(error)
    lowered = text.lower()
    # API key / 认证
    if any(k in lowered for k in ("api key", "auth", "401", "403", "unauthorized", "forbidden")):
        return "API key 无效或权限不足，请检查设置中的密钥配置。"
    # 网络超时
    if any(k in lowered for k in ("timeout", "timed out", "timedout")):
        return "网络请求超时，请检查网络连接或稍后重试。"
    # 额度用尽
    if any(k in lowered for k in ("quota", "limit", "exceeded", "insufficient")) or "额度" in text:
        return "API 额度可能已用尽，请等待下月重置或更换引擎。"
    # DNS / 连接失败
    if any(k in lowered for k in ("connection", "refused", "reset", "dns", "name or service", "getaddrinfo", "econn", "enotfound")):
        return "网络连接失败，请检查网络状态或代理设置。"
    # HTTP 服务端错误
    if any(k in lowered for k in ("500", "502", "503", "504", "server error")):
        return "翻译服务暂时不可用(服务器错误)，请稍后重试。"
    # 无可用引擎 (来自 translator 层, 直接透传)
    if "无可用引擎" in text:
        return text
    # 通用兜底
    return f"翻译失败: {text}"
