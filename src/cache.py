"""SQLite 翻译缓存: key = (归一化原文 + 模式 + 术语表hash), 命中则零延迟零额度。

归一化处理: 文本差异不影响语义时复用缓存, 大幅提升命中率。
  - Unicode NFKC 规范化(全角→半角, 兼容字符统一)
  - 合并连续空白为单个空格
  - 去首尾空白
  - 去尾标点(. , ! ? ; :)  — "Hello world." 与 "Hello world" 命中同一条
  - 统一常见 Unicode 标点  — 弯引号→直引号, 全角逗号→半角
"""
import sqlite3
import hashlib
import threading
import re
import unicodedata
from pathlib import Path

from . import config


_lock = threading.Lock()
_conn = None

# 预编译正则: 匹配所有连续空白字符(含 \t \n \r \v \f 及 Unicode 空白)
_WHITESPACE_RE = re.compile(r"\s+")

# 尾部标点: 英文 + 中文常见句尾/句中停顿符
_TRAILING_PUNCT_RE = re.compile(r"[.,!?;:。，！？；：、…]+$")

# Unicode 标点 → ASCII 对照 (不影响翻译结果的纯格式差异)
_PUNCT_MAP = {
    "“": '"',  # " (左双弯引号)
    "”": '"',  # " (右双弯引号)
    "‘": "'",  # ' (左单弯引号)
    "’": "'",  # ' (右单弯引号)
    "，": ",",  # ，(全角逗号)
    "．": ".",  # ．(全角句号)
    "—": "-",  # — (em-dash)
    "–": "-",  # – (en-dash)
}
_PUNCT_TRANS = str.maketrans(_PUNCT_MAP)


def normalize_text(text: str) -> str:
    """归一化文本, 用于缓存键计算。不改变语义, 只消除无意义差异。

    - NFKC: 全角字母数字→半角, 连字→拆分(ﬁ→fi), 兼容字符统一
    - Unicode 标点统一: 弯引号→直引号, 全角逗号→半角, em-dash→减号
    - 去尾部标点: "Hello." 与 "Hello" 视为同一原文
    - 合并连续空白(含换行/制表符)为单个空格
    - 去首尾空白
    """
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(_PUNCT_TRANS)
    text = _TRAILING_PUNCT_RE.sub("", text)
    text = _WHITESPACE_RE.sub(" ", text)
    return text.strip()


def _connect() -> sqlite3.Connection:
    global _conn
    if _conn is not None:
        return _conn
    db_path = config.resource_path(config.get("cache.db_path", "cache/translation_cache.db"))
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    _conn = sqlite3.connect(str(db_path), check_same_thread=False)
    _conn.execute("""
        CREATE TABLE IF NOT EXISTS translations (
            cache_key TEXT PRIMARY KEY,
            source_text TEXT NOT NULL,
            target_text TEXT NOT NULL,
            engine TEXT,
            term_mode TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # 为归一化查询添加索引(可选, 加速模糊查找)
    _conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_source ON translations(source_text)
    """)
    _conn.commit()
    return _conn


def _make_key(source_text: str, term_mode: str, term_signature: str, engine: str) -> str:
    """用归一化文本生成缓存键, 相似文本共享同一 key。"""
    norm = normalize_text(source_text)
    raw = f"{engine}|{term_mode}|{term_signature}|{norm}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def get(source_text: str, term_mode: str, term_signature: str, engine: str) -> str | None:
    """命中返回译文, 否则 None。自动归一化原文。"""
    key = _make_key(source_text, term_mode, term_signature, engine)
    with _lock:
        cur = _connect().execute(
            "SELECT target_text FROM translations WHERE cache_key = ?", (key,)
        )
        row = cur.fetchone()
    return row[0] if row else None


def put(source_text: str, target_text: str, term_mode: str,
        term_signature: str, engine: str):
    """存入缓存。原文归一化后存储, 节省空间且便于人工查看。"""
    norm = normalize_text(source_text)
    key = _make_key(source_text, term_mode, term_signature, engine)
    with _lock:
        _connect().execute(
            "INSERT OR REPLACE INTO translations "
            "(cache_key, source_text, target_text, engine, term_mode) "
            "VALUES (?, ?, ?, ?, ?)",
            (key, norm, target_text, engine, term_mode)
        )
        _connect().commit()


def stats() -> int:
    """返回已缓存条目数。"""
    with _lock:
        cur = _connect().execute("SELECT COUNT(*) FROM translations")
        return cur.fetchone()[0]
