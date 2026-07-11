"""配置加载: 读取 config.properties (key=value 格式), 提供全局访问。

采用简单解析(非 configparser), 避免要求 section 头。
key 形如 "deepl.api_key", 取值用 get("deepl.api_key")。

打包成 exe 后, 资源目录(config.properties, resources/, cache/)以 exe 所在目录为基准;
开发模式下以项目根目录(translator-app/)为基准。
"""
import sys
from pathlib import Path


def _detect_base_dir() -> Path:
    """判定资源根目录。

    - PyInstaller 打包: sys.frozen 存在, 用 exe 所在目录
    - 开发模式: 用本文件上两级(translator-app/)
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


_KV: dict[str, str] = {}
_BASE_DIR = _detect_base_dir()
_LOADED = False


def load_config(config_path: str = None) -> object:
    """加载配置文件。始终从 _BASE_DIR/config.properties 读取,
    确保 load 和 save 指向同一文件(避免 cwd 不同导致用量丢失)。"""
    global _LOADED
    if _LOADED:
        return _KV

    if config_path is None:
        config_path = str(_BASE_DIR / "config.properties")

    if Path(config_path).exists():
        _parse(Path(config_path))
    _LOADED = True
    return _KV


def _parse(path: Path):
    """解析 key=value 行, 忽略注释和空行。"""
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        _KV[key.strip()] = value.strip()


def get_config() -> dict:
    if not _LOADED:
        load_config()
    return _KV


def get(key: str, default: str = "") -> str:
    return _KV.get(key, default)


def get_int(key: str, default: int = 0) -> int:
    try:
        return int(_KV.get(key, str(default)))
    except (TypeError, ValueError):
        return default


def set_value(key: str, value: str):
    """更新内存中的配置项。"""
    _KV[key] = value


def save():
    """把当前内存配置写回 config.properties(保留注释与原顺序)。"""
    path = _BASE_DIR / "config.properties"
    # 读取原文件作为模板(保留注释行), 仅替换有变动的 key=value
    lines: list[str] = []
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
    written_keys: set[str] = set()
    out: list[str] = []
    for raw in lines:
        stripped = raw.strip()
        if stripped and not stripped.startswith("#") and not stripped.startswith(";") and "=" in stripped:
            k = stripped.partition("=")[0].strip()
            if k in _KV:
                out.append(f"{k}={_KV[k]}")
                written_keys.add(k)
                continue
        out.append(raw)
    # 追加原文件中没有的新 key
    for k, v in _KV.items():
        if k not in written_keys:
            out.append(f"{k}={v}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    # mark as loaded (ensure we modify the module-level variable)
    global _LOADED
    _LOADED = True


def base_dir() -> Path:
    return _BASE_DIR


def resource_path(rel: str) -> Path:
    """返回资源文件的绝对路径(相对项目根)。"""
    return _BASE_DIR / rel


def ensure_dirs():
    """创建运行时需要的目录。"""
    for d in ["cache", "output", "resources/terms"]:
        p = _BASE_DIR / d
        p.mkdir(parents=True, exist_ok=True)
