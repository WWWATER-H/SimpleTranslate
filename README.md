## 项目背景

- 作为一位中国开发者，某些数据手册上百页的英文看着就头大，自己翻译是一件又累又耗时的事情。PDF全导入到AI大模型很方便，小文档还好，但是如果是上百页的技术手册，容易丢上下文，翻译不一定准，token还烧了不少，总结下来就是方便但是费钱。选用现有的翻译软件经常出现误识别的情况，如“I OUT”会翻译成“我出去”，需要联网才能使用，也无法适配所有软件，而且高级功能还需要付费，价格并不便宜。于是想着能不能开发一个简单的翻译软件，能够做到实时翻译，设定好术语表后特定术语能不被替换，顺便试试PDF的整理，基于这个想法，SimpleTranslate诞生了。
- 目前，SimpleTranslate支持**划词翻译**和 **PDF 文档翻译**，集成 DeepL、百度翻译、OpenAI 兼容 API 三大引擎接口，失败自动切换，带术语表、SQLite 缓存与用量统计。

## 功能特性

- **划词翻译**：选中任意文本自动弹出译文悬浮窗，支持防抖、去重、过滤网址/邮箱/路径/纯符号
- **PDF 文档翻译**：整篇导入翻译，支持双语对照、仅中文、原页后插入译文三种导出模式，可导出 Markdown 或 PDF
- **多引擎支持（支持本地大模型）**：DeepL Free、百度翻译、OpenAI 兼容 API（可对接 GPT / DeepSeek / 通义千问 / Moonshot / 本地 Ollama 等），默认引擎失败自动切换备选
- **术语表**：支持增改删整个表，支持添加自定义术语到表中
- 示例内置嵌入式（embedded）与软件（software）两类术语表，支持三种模式
  - `bilingual` 原文+译文对照，如 `PWM (脉宽调制)`
  - `chinese_only` 只显示中文，术语不替换
  - `glossary` 术语表优先，强制替换 API 译文中的术语
- **SQLite 缓存**：文本归一化（NFKC、合并空白、去尾标点）后复用缓存，命中零延迟零额度消耗
- **用量统计**：按引擎按月统计字符数/调用数/错误数，跨月自动重置，持久化到配置文件
- **系统托盘**：标题栏点X最小化到托盘，双击托盘图标显示主窗，右键托盘图标进入菜单快捷操作，点-最小化到任务栏
- **单例运行**：基于 Windows Mutex 的单例检查，重复启动时提示

## 便捷功能演示

- **划词翻译**

<img width="700" height="306" alt="202607130008" src="https://github.com/user-attachments/assets/8ca0333e-e270-41a8-a6f0-f646ee635b0d" />


固定：再进行任意操作时悬浮窗不消失

锁定：位置锁定，悬浮窗不再跟随鼠标

复制：一键复制到剪贴板


- **术语表**

用来写入专业术语，遇到这些词后程序会进行特殊处理而不是无脑替换，更适合阅读

<img width="400" height="375" alt="202607130008(3)" src="https://github.com/user-attachments/assets/5e7dcd32-0fae-41d3-945b-84fdad1ece23" />

- **术语表编写**

可以根据工作的不同新建术语表，可以获取其他用户分享的术语表，省去编写时间

- **悬浮球功能**

点击“ - ”就可以让主面板缩小为胶囊，不占地方

<img width="400" height="490" alt="202607130008(5)" src="https://github.com/user-attachments/assets/1e356df4-32f8-43de-af69-ee1cf103af83" />


- **PDF翻译**

导入PDF，翻译成中文，现在有如下图这几个模式：

  <img width="462" height="266" alt="屏幕截图 2026-07-13 220003" src="https://github.com/user-attachments/assets/7ba4d927-0489-43d0-aded-ee22e7e5ac9d" />

  没有图的123选项都可，有图的可以试试4选项

## 安装

在release里找到最新版本，下载压缩包
即开即用，非常方便

## 配置

1. 复制配置模板：


2. 编辑 `config.properties`，填入至少一个引擎的密钥：（设置里带直达链接，注册一下每月能免费领2000000词的额度）

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
> API密钥请勿随意分享给他人，复制给他人此软件时记得把自己的config.properties删除

## 使用指南

- **划词翻译**：点击控制窗「开始监听」按钮启用监听，在任意应用中选中英文文本即可弹出译文
- **折叠悬浮球**：点击“ - ”号折叠成胶囊
- **PDF 翻译**：控制窗或托盘菜单点「导入 PDF」，选择文件与导出模式，支持选定特定范围的页
- **设置**：托盘菜单「设置」可切换引擎、调整划词与悬浮窗参数

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

## 技术要点

- **启动优化**：单例检查置于所有 import 之前；闪屏先行显示；重型依赖（PyMuPDF、reportlab、pynput）延迟到实际使用时才导入
- **线程安全**：划词监听独立线程，翻译任务走 `ThreadPoolExecutor`，通过 Qt Signal 回主线程更新 UI
- **缓存归一化**：Unicode NFKC + 合并空白 + 去尾标点 + 统一标点，大幅提升命中率
- **用量持久化**：每次翻译后即时写回配置文件，异常退出不丢失
- **支持本地大模型**：无需联网，就可用本地大模型进行翻译，适合某些生产环境

## 注意事项
- 在不用时请随手关闭监听，不然可能会造成意想不到的后果，例如开启监听的时候操作服务器可能会导致误操作
- API密钥是隐私数据，不应该传给任何人，请自行保管好

## 说明
- 目前仅支持 Windows（使用了 `ctypes.windll`、Win32 Mutex、单例窗口枚举等平台 API）。
- 后续可能开发安卓版本，平板查看技术手册也能更方便
- 可能实现OCR扫描功能
- 将会支持多语言翻译

## 许可

本项目基于 [GPL-3.0](LICENSE) 许可证发布。任何人可自由使用、修改和分发，但衍生作品必须同样以 GPL-3.0 开源。
