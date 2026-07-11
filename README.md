# 简单翻译 (SimpleTranslate)

一款基于 PySide6 的 Windows 桌面翻译软件，支持**划词翻译**和 **PDF 文档翻译**，集成 DeepL、百度翻译、OpenAI 兼容 API 三大引擎，失败自动切换，带术语表、SQLite 缓存与用量统计。

## 功能特性

- **划词翻译**：选中任意文本自动弹出译文悬浮窗，支持防抖、去重、过滤网址/邮箱/路径/纯符号
- **PDF 文档翻译**：整篇导入翻译，支持双语对照、仅中文、原页后插入译文三种导出模式，可导出 Markdown 或 PDF
- **多引擎支持**：DeepL Free、百度翻译、OpenAI 兼容 API（可对接 GPT / DeepSeek / 通义千问 / Moonshot / 本地 Ollama 等），默认引擎失败自动切换备选
- **术语表**：内置嵌入式（embedded）与软件（software）两类术语表，支持三种模式
  - `bilingual` 原文+译文对照，如 `PWM (脉宽调制)`
  - `chinese_only` 全部译为中文，阅读流畅
  - `glossary` 术语表优先，强制替换 API 译文中的术语
- **SQLite 缓存**：文本归一化（NFKC、合并空白、去尾标点）后复用缓存，命中零延迟零额度消耗
- **用量统计**：按引擎按月统计字符数/调用数/错误数，跨月自动重置，持久化到配置文件
- **系统托盘**：最小化到托盘，双击显示主窗，右键菜单快捷操作
- **单例运行**：基于 Windows Mutex 的单例检查，重复启动时激活已有窗口
- **打包 exe**：附带 PyInstaller spec 与一键打包脚本

## 目录结构

```
SimpleTranslate/
├── main.py                    # 程序入口
├── make_icon.py               # 生成 app.ico 图标
├── requirements.txt           # Python 依赖
├── app.ico                    # 应用图标
├── config.properties.example  # 配置文件模板
├── src/
│   ├── config.py              # 配置加载（key=value 格式）
│   ├── cache.py               # SQLite 翻译缓存（归一化命中）
│   ├── terms.py               # 术语表管理与三种模式
│   ├── translator.py          # 三大引擎 + 失败切换 + 用量统计
│   ├── selection.py           # pynput 划词监听
│   ├── translate_tasks.py     # 后台翻译任务（离开主线程）
│   ├── ui.py                  # 译文悬浮窗 + 控制窗
│   ├── dialogs.py             # PDF导出/术语表/设置 对话框
│   ├── pdf_export.py          # PDF 提取/翻译/导出
│   └── app_controller.py      # 应用控制器，串联各模块
└── resources/
    └── terms/
        ├── embedded.json      # 示例：嵌入式术语表
        └── software.json      # 示例：软件术语表
```

## 安装

需要 Python 3.10+。

```bash
pip install -r requirements.txt
```

依赖清单：

- `PySide6` — Qt 界面
- `pynput` — 鼠标/键盘监听（划词）
- `pyperclip` — 剪贴板操作
- `PyMuPDF` — PDF 文字提取
- `requests` — 翻译 API 调用
- `reportlab` — PDF 导出

## 配置

1. 复制配置模板：

   ```bash
   cp config.properties.example config.properties
   ```

2. 编辑 `config.properties`，填入至少一个引擎的密钥：

   ```properties
   # DeepL Free API（https://www.deepl.com/pro-api 选 Free 计划）
   deepl.api_key=你的密钥

   # 百度翻译（https://fanyi-api.baidu.com/）
   baidu.app_id=你的APP_ID
   baidu.secret_key=你的密钥

   # OpenAI 兼容 API（可对接 GPT/DeepSeek/通义千问/Moonshot/Ollama 等）
   openai.api_key=你的密钥
   openai.base_url=https://api.openai.com/v1
   openai.model=gpt-4o-mini

   # 默认引擎：deepl / baidu / openai
   translate.default_engine=deepl
   ```

> 所有配置项均可在应用内「设置」对话框修改，保存后立即生效。

## 使用指南

- **划词翻译**：点击控制窗「开始」按钮启用监听，在任意应用中选中英文文本即可弹出译文
- **折叠悬浮球**：点击“ - ”号折叠成胶囊
- **PDF 翻译**：控制窗或托盘菜单点「导入 PDF」，选择文件与导出模式
- **术语表**：托盘菜单「术语表」可查看/编辑术语
- **设置**：托盘菜单「设置」可切换引擎、调整划词与悬浮窗参数


## 技术要点

- **启动优化**：单例检查置于所有 import 之前；闪屏先行显示；重型依赖（PyMuPDF、reportlab、pynput）延迟到实际使用时才导入
- **线程安全**：划词监听独立线程，翻译任务走 `ThreadPoolExecutor`，通过 Qt Signal 回主线程更新 UI
- **缓存归一化**：Unicode NFKC + 合并空白 + 去尾标点 + 统一标点，大幅提升命中率
- **用量持久化**：每次翻译后即时写回配置文件，异常退出不丢失

## 适用平台

仅支持 Windows（使用了 `ctypes.windll`、Win32 Mutex、单例窗口枚举等平台 API）。
后续可能开发安卓版本

## 许可

本项目基于 [GPL-3.0](LICENSE) 许可证发布。任何人可自由使用、修改和分发，但衍生作品必须同样以 GPL-3.0 开源。
