"""翻译引擎: DeepL Free + 百度翻译 + OpenAI兼容, 失败自动切换, 带用量统计。

用量数据持久化到 config.properties, 程序重启后保留。
每月自动重置(根据 usage.month 判断, 跨月则清零)。
"""
import time
import random
import hashlib
import urllib.parse
import urllib.request
import json
import threading
from dataclasses import dataclass, field
from datetime import datetime

import requests

from . import config, cache


def _timeout() -> int:
    """获取网络超时秒数(可配置, 默认15秒)。"""
    return config.get_int("network.timeout", 15)


def _current_month() -> str:
    """当前月份标识, 如 '2026-07'。用于判断是否跨月重置。"""
    return datetime.now().strftime("%Y-%m")


@dataclass
class Usage:
    """单引擎用量统计。"""
    chars: int = 0
    calls: int = 0
    errors: int = 0
    limit: int = 0

    def to_dict(self) -> dict:
        return {"chars": self.chars, "calls": self.calls,
                "errors": self.errors, "limit": self.limit}


@dataclass
class TranslateResult:
    text: str
    engine: str
    cached: bool = False
    error: str | None = None


class BaseEngine:
    name = "base"

    def __init__(self, limit: int = 0):
        self.usage = Usage(limit=limit)
        self._lock = threading.Lock()

    def _translate(self, text: str) -> str:
        raise NotImplementedError

    def available(self) -> bool:
        raise NotImplementedError

    def test(self) -> tuple[bool, str]:
        """测试连通性, 子类可覆盖。"""
        return False, "未实现"

    def translate(self, text: str) -> TranslateResult:
        try:
            result = self._translate(text)
            with self._lock:
                self.usage.chars += len(text)
                self.usage.calls += 1
            return TranslateResult(text=result, engine=self.name)
        except Exception as e:
            with self._lock:
                self.usage.errors += 1
            return TranslateResult(text=text, engine=self.name, error=str(e))


class DeepLEngine(BaseEngine):
    name = "deepl"

    def __init__(self):
        super().__init__(limit=config.get_int("deepl.monthly_limit", 500000))
        self.api_key = config.get("deepl.api_key", "").strip()
        self.endpoint = config.get("deepl.endpoint",
                                   "https://api-free.deepl.com/v2/translate")

    def available(self) -> bool:
        return bool(self.api_key)

    def _translate(self, text: str) -> str:
        if not self.api_key:
            raise RuntimeError("DeepL API key 未配置")
        resp = requests.post(
            self.endpoint,
            headers={"Authorization": f"DeepL-Auth-Key {self.api_key}"},
            data={"text": text, "target_lang": "ZH"},
            timeout=_timeout(),
        )
        resp.raise_for_status()
        data = resp.json()
        return data["translations"][0]["text"]

    def test(self) -> tuple[bool, str]:
        """测试连通性, 返回 (是否成功, 描述信息)。"""
        if not self.api_key:
            return False, "API key 未配置"
        try:
            t0 = time.time()
            resp = requests.post(
                self.endpoint,
                headers={"Authorization": f"DeepL-Auth-Key {self.api_key}"},
                data={"text": "hello", "target_lang": "ZH"},
                timeout=_timeout(),
            )
            dt = time.time() - t0
            if resp.status_code == 200:
                return True, f"连接成功, 延迟 {dt:.1f}s"
            return False, f"HTTP {resp.status_code}: {resp.text[:100]}"
        except Exception as e:
            return False, f"连接失败: {e}"


class BaiduEngine(BaseEngine):
    name = "baidu"

    def __init__(self):
        super().__init__(limit=config.get_int("baidu.monthly_limit", 60000))
        self.app_id = config.get("baidu.app_id", "").strip()
        self.secret = config.get("baidu.secret_key", "").strip()
        self.endpoint = config.get(
            "baidu.endpoint",
            "https://fanyi-api.baidu.com/api/trans/vip/translate",
        )

    def available(self) -> bool:
        return bool(self.app_id and self.secret)

    def _translate(self, text: str) -> str:
        if not self.available():
            raise RuntimeError("百度翻译 app_id / secret 未配置")
        salt = str(random.randint(32768, 65536))
        sign = hashlib.md5(
            (self.app_id + text + salt + self.secret).encode("utf-8")
        ).hexdigest()
        params = {
            "q": text, "from": "en", "to": "zh",
            "appid": self.app_id, "salt": salt, "sign": sign,
        }
        url = self.endpoint + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=_timeout()) as r:
            data = json.loads(r.read().decode("utf-8"))
        if "error_code" in data:
            raise RuntimeError(f"百度翻译错误 {data['error_code']}: {data.get('error_msg')}")
        return "\n".join(item["dst"] for item in data["trans_result"])

    def test(self) -> tuple[bool, str]:
        """测试连通性。"""
        if not self.available():
            return False, "APP ID / 密钥 未配置"
        try:
            self._translate("hello")
            return True, "连接成功"
        except Exception as e:
            return False, f"连接失败: {e}"


class OpenAIEngine(BaseEngine):
    """兼容 OpenAI Chat API 格式的引擎。

    可对接: OpenAI GPT、DeepSeek、通义千问、Moonshot、本地 Ollama 等
    任何遵循 OpenAI /v1/chat/completions 协议的服务。
    """
    name = "openai"

    def __init__(self):
        super().__init__(limit=config.get_int("openai.monthly_limit", 0))
        self.api_key = config.get("openai.api_key", "").strip()
        self.base_url = config.get("openai.base_url",
                                   "https://api.openai.com/v1").strip().rstrip("/")
        self.model = config.get("openai.model", "gpt-4o-mini").strip()
        self.endpoint = self.base_url + "/chat/completions"

    def available(self) -> bool:
        return bool(self.api_key)

    def _translate(self, text: str) -> str:
        if not self.api_key:
            raise RuntimeError("OpenAI API key 未配置")
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system",
                 "content": "You are a professional translator. "
                            "Translate the following English text to Chinese. "
                            "Output ONLY the translation, no explanations."},
                {"role": "user", "content": text},
            ],
            "temperature": 0.2,
        }
        resp = requests.post(
            self.endpoint,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=_timeout() * 2,  # LLM 响应较慢, 给双倍超时
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()

    def test(self) -> tuple[bool, str]:
        """测试连通性。"""
        if not self.api_key:
            return False, "API key 未配置"
        try:
            t0 = time.time()
            self._translate("hello")
            dt = time.time() - t0
            return True, f"连接成功, 延迟 {dt:.1f}s"
        except Exception as e:
            return False, f"连接失败: {e}"


class Translator:
    """翻译调度: 默认引擎优先, 失败/超额自动切备选。

    用量数据持久化到 config.properties:
      usage.month=2026-07
      usage.deepl.chars=12345
      usage.deepl.calls=23
      usage.baidu.chars=678
      ...
    跨月时自动重置。
    """

    def __init__(self):
        self.engines: list[BaseEngine] = []
        self._init_engines()
        self._load_usage()

    def _init_engines(self):
        # 按默认引擎顺序加载, 默认引擎排首位
        default = config.get("translate.default_engine", "deepl").lower()
        all_engines = {
            "deepl": DeepLEngine,
            "baidu": BaiduEngine,
            "openai": OpenAIEngine,
        }
        order = [default] + [n for n in all_engines if n != default]
        self.engines = []
        for n in order:
            eng = all_engines[n]()
            if eng.available():
                self.engines.append(eng)

    def _load_usage(self):
        """从 config 加载历史用量, 跨月则自动重置。"""
        saved_month = config.get("usage.month", "")
        cur_month = _current_month()
        if saved_month != cur_month:
            # 跨月: 清零
            config.set_value("usage.month", cur_month)
            # 清除旧的各引擎用量
            for eng in self.engines:
                config.set_value(f"usage.{eng.name}.chars", "0")
                config.set_value(f"usage.{eng.name}.calls", "0")
                config.set_value(f"usage.{eng.name}.errors", "0")
            return
        # 同月: 读取历史值
        for eng in self.engines:
            eng.usage.chars = config.get_int(f"usage.{eng.name}.chars", 0)
            eng.usage.calls = config.get_int(f"usage.{eng.name}.calls", 0)
            eng.usage.errors = config.get_int(f"usage.{eng.name}.errors", 0)

    def reload(self):
        """重新加载配置与引擎(设置变更后调用)。"""
        self._init_engines()
        self._load_usage()

    def available(self) -> bool:
        return len(self.engines) > 0

    def active_engine_names(self) -> list[str]:
        return [e.name for e in self.engines]

    def translate(self, text: str, term_mode: str = "",
                  term_signature: str = "") -> TranslateResult:
        text = text.strip()
        if not text:
            return TranslateResult(text="", engine="none", cached=True)

        # 1. 先查缓存
        for eng in self.engines:
            cached = cache.get(text, term_mode, term_signature, eng.name)
            if cached is not None:
                return TranslateResult(text=cached, engine=eng.name, cached=True)

        # 2. 调引擎, 失败逐个切换
        last_err = None
        for eng in self.engines:
            # 跳过已达额度的引擎
            if eng.usage.limit and eng.usage.chars >= eng.usage.limit:
                continue
            result = eng.translate(text)
            if result.error is None:
                cache.put(text, result.text, term_mode, term_signature, eng.name)
                # 每次翻译后立即保存用量, 防止异常退出时丢失
                try:
                    save_usage()
                except Exception:
                    pass
                return result
            last_err = result.error

        return TranslateResult(text=text, engine="none", error=last_err or "无可用引擎")

    def usage_summary(self) -> dict:
        return {e.name: e.usage.to_dict() for e in self.engines}

    def test_all(self) -> dict[str, tuple[bool, str]]:
        """测试所有引擎连通性, 返回 {引擎名: (是否成功, 描述)}。"""
        return {e.name: e.test() for e in self.engines}

    def reset_usage(self):
        """手动重置所有引擎用量(归零)。"""
        for eng in self.engines:
            eng.usage.chars = 0
            eng.usage.calls = 0
            eng.usage.errors = 0
        config.set_value("usage.month", _current_month())
        for eng in self.engines:
            config.set_value(f"usage.{eng.name}.chars", "0")
            config.set_value(f"usage.{eng.name}.calls", "0")
            config.set_value(f"usage.{eng.name}.errors", "0")
        try:
            config.save()
        except Exception:
            pass


# 全局单例
_translator: Translator | None = None


def get_translator() -> Translator:
    global _translator
    if _translator is None:
        _translator = Translator()
    return _translator


def save_usage():
    """把当前内存中的用量数据保存到 config(程序退出时调用)。"""
    t = get_translator()
    config.set_value("usage.month", _current_month())
    for eng in t.engines:
        config.set_value(f"usage.{eng.name}.chars", str(eng.usage.chars))
        config.set_value(f"usage.{eng.name}.calls", str(eng.usage.calls))
        config.set_value(f"usage.{eng.name}.errors", str(eng.usage.errors))
    try:
        config.save()
    except Exception:
        pass
