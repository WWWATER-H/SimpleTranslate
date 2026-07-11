"""术语表: JSON 加载 + 三模式(bilingual/chinese_only/glossary)切换。

模式说明:
  bilingual    - 输出 "PWM (脉宽调制)" 形式, 保留英文便于对照原手册
  chinese_only - 全部译为中文, 追求阅读流畅
  glossary     - 术语表优先, API 译文中的术语被强制替换为术语表译法

term_signature 用于缓存键, 当术语表内容变化时缓存自动失效。
"""
import json
import hashlib
import threading
import re
from pathlib import Path

from . import config


def _word_pattern(word: str) -> re.Pattern:
    """匹配前后非英文字母的术语(避免 DMA 误匹配 DMAX)。中文上下文也适用。"""
    return re.compile(rf"(?<![A-Za-z]){re.escape(word)}(?![A-Za-z])")


def _search_word(word: str, text: str) -> bool:
    return _word_pattern(word).search(text) is not None


def _sub_word(word: str, repl: str, text: str, count: int = 0) -> str:
    return _word_pattern(word).sub(repl, text, count=count)


class TermManager:
    def __init__(self):
        self._lock = threading.Lock()
        self.tables: dict[str, dict[str, str]] = {}  # name -> {en: zh}
        self.active_tables: set[str] = set()
        self.mode: str = "chinese_only"
        self._signature: str = ""

    def load_config(self):
        """按 config 加载激活的术语表与模式。"""
        self.mode = config.get("term.mode", "chinese_only") or "chinese_only"
        names = [n.strip() for n in config.get("term.active_tables", "embedded,software").split(",") if n.strip()]
        self.active_tables = set(names)
        for n in names:
            self.load_table(n)

    def load_table(self, name: str, data: dict | None = None):
        """加载某张术语表。data 为 None 时从 resources/terms/{name}.json 读。"""
        with self._lock:
            if data is None:
                path = config.resource_path(f"resources/terms/{name}.json")
                if not path.exists():
                    return
                data = json.loads(path.read_text(encoding="utf-8"))
            # 兼容 {"PWM":"脉宽调制"} 与 {"terms":{"PWM":"脉宽调制"}} 两种格式
            if isinstance(data, dict) and "terms" in data and isinstance(data["terms"], dict):
                data = data["terms"]
            self.tables[name] = {k.strip(): v for k, v in data.items()}
            self._rebuild_signature()

    def set_mode(self, mode: str):
        with self._lock:
            self.mode = mode
            self._rebuild_signature()

    def toggle_table(self, name: str, on: bool):
        with self._lock:
            if on:
                self.active_tables.add(name)
            else:
                self.active_tables.discard(name)
            self._rebuild_signature()

    def all_table_names(self) -> list[str]:
        return sorted(self.tables.keys())

    def signature(self) -> str:
        return self._signature

    def _rebuild_signature(self):
        parts = [self.mode]
        for n in sorted(self.active_tables):
            tbl = self.tables.get(n, {})
            # 用表内容hash, 内容变则缓存失效
            h = hashlib.sha1(
                json.dumps(tbl, sort_keys=True, ensure_ascii=False).encode("utf-8")
            ).hexdigest()[:8]
            parts.append(f"{n}:{h}")
        self._signature = "|".join(parts)

    def _merged_table(self) -> dict[str, str]:
        merged = {}
        for n in self.active_tables:
            merged.update(self.tables.get(n, {}))
        return merged

    def apply(self, source: str, translated: str) -> str:
        """按当前模式对译文进行后处理。"""
        if not source or not translated:
            return translated
        tbl = self._merged_table()
        if not tbl:
            return translated

        if self.mode == "bilingual":
            # 在译文中首次出现的术语处补标注:
            #   - 若译文含中文译法 -> 标为 "中文 (English)"
            #   - 若译文含英文原文 -> 标为 "English (中文)"
            result = translated
            for en, zh in tbl.items():
                if not en or not zh or en not in source:
                    continue
                # 情况1: 译文用了中文译法
                if zh in result:
                    tag = f"{zh} ({en})"
                    if tag not in result:
                        result = result.replace(zh, tag, 1)
                # 情况2: 译文直接保留了英文术语(前后非英文字母才算匹配)
                elif _search_word(en, result):
                    tag = f"{en} ({zh})"
                    if tag not in result:
                        result = _sub_word(en, tag, result, count=1)
            return result

        elif self.mode == "glossary":
            # 强制把译文中相关术语替换为术语表译法
            result = translated
            for en, zh in tbl.items():
                if en in source and zh:
                    result = _sub_word(en, zh, result)
            return result

        # chinese_only: 不做处理, 直接返回译文
        return translated

    def add_term(self, table: str, en: str, zh: str):
        with self._lock:
            if table not in self.tables:
                self.tables[table] = {}
            self.tables[table][en.strip()] = zh.strip()
            self._persist(table)
            self._rebuild_signature()

    def remove_term(self, table: str, en: str):
        with self._lock:
            if table in self.tables and en in self.tables[table]:
                del self.tables[table][en]
                self._persist(table)
                self._rebuild_signature()

    def _persist(self, table: str):
        """写回 JSON 文件。"""
        path = config.resource_path(f"resources/terms/{table}.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.tables[table], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


# 全局单例
_term: TermManager | None = None


def get_term_manager() -> TermManager:
    global _term
    if _term is None:
        _term = TermManager()
        _term.load_config()
    return _term
