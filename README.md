<div align="center">

# ZhiZhu (知蛛)

### *编织你的知乎精神镜像*

**Reclaim your digital mind.**

[![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python&logoColor=white)](#系统要求)
[![Playwright](https://img.shields.io/badge/Playwright-Chromium-green?logo=google-chrome&logoColor=white)](#安装)
[![License](https://img.shields.io/badge/License-MIT-yellow)](#免责声明)

</div>

---

> *"我们在互联网上留下的每一个字节，都是灵魂的碎片。是时候把它们找回来了。"*

## 为什么是 ZhiZhu？

多年来，你在知乎上敲下的数万、数十万字，不仅仅是简单的问答。它们是你**知识体系的疆界**，是你**价值观的投影**，是你**逻辑思维的演变轨迹**，更是你在这个喧嚣世界中发出的**独特声音**。

然而，散落在互联网角落的数据是脆弱的，也是割裂的。

**ZhiZhu (知蛛)** 是一台时光穿梭机，也是一位数字考古学家。它致力于将你流落在知乎的精神财富，以最纯粹、最通用的 Markdown 格式完整带回本地。无论是精妙的 LaTeX 数学公式，还是承载记忆的图片，都力求高保真还原。

> *Archiving the past to compute the future.*
> *归档过去，为了计算未来。*

---

## 在 AI 时代，这不仅仅是备份

当你拥有了这份属于自己的全量数据，你就拥有了训练私人 AI 助理的基石。你可以将这些代表你 **"人格（Personality）"** 的文本投喂给 LLM，让 AI 成为你的镜子——

- **自我复盘** — 利用 AI 分析你过去数年的回答，生成你的「认知演变报告」
- **知识图谱** — 从散乱的回答中提取你的知识结构，重组为系统化的文章
- **风格克隆** — 让 AI 学习你的文风与逻辑，成为最懂你的写作助手
- **深度对话** — 与你的「数字孪生」对话，完成一次深度的自我探索与反省

**把数据存成 Markdown，只是第一步；认识你自己，才是 ZhiZhu 的终极目标。**

> *Don't just leave it on the cloud. Own your thoughts.*
> *别把思想只留在云端，拥有它。*

---

## 核心能力

| 能力 | 说明 |
|:---|:---|
| **用户级全量爬取** | 输入用户 URL token，自动收集该用户的全部回答与文章 |
| **用户想法爬取** | 爬取指定用户的所有知乎想法（Pins） |
| **问题级爬取** | 爬取指定问题下的全部或前 N 个回答 |
| **单回答爬取** | 精准爬取某个特定回答，可选附带完整评论区 |
| **评论区提取** | 通过知乎 API 获取全部根评论与子评论，格式化为 Markdown |
| **浏览器指纹伪装** | 内置 WebGL、Canvas、AudioContext 等多维度反检测机制 |
| **智能延迟策略** | 请求间随机等待 10-20 秒（可自定义），以时间换安全 |
| **断点续传** | 自动记录进度，中断后重新运行即从上次位置继续 |
| **LaTeX 公式还原** | 完美转换知乎数学公式为标准 `$...$` / `$$...$$` 语法 |
| **图片本地化** | 自动下载文章图片到本地，重写 Markdown 引用路径 |
| **内容去噪** | 自动去除广告卡片、视频占位符、知乎直答链接等干扰元素 |
| **持久化登录** | 登录一次，后续爬取无需重复登录 |

---

## 系统要求

- **Python** 3.10+
- **操作系统** Windows / macOS / Linux
- **网络** 稳定的互联网连接

## 快速开始

### 1. 安装

```bash
# 安装 Python 依赖
pip install -r requirements.txt

# 安装 Playwright 浏览器内核
playwright install chromium
```

### 2. 使用 Web 界面（推荐）

如果你不熟悉命令行，可以直接使用自带的 Web 图形界面：

```bash
python webui.py
```

启动后会自动在浏览器中打开一个页面，包含多个标签页：

- 登录知乎
- 爬取用户回答 / 文章
- 爬取用户想法（Pins）
- 爬取问题下的回答
- 爬取单个回答（可选附带评论）
- 合并某个目录下的所有 Markdown 文件

所有爬取进度和日志会实时显示在页面下方，你只需要填写参数并点击按钮即可。

### 3. 登录知乎（命令行）

首次使用需要登录，登录状态会持久化保存在 `browser_data/` 目录中。

```bash
python main.py login
```

程序会打开浏览器窗口，请在浏览器中手动完成登录。登录成功后程序会自动检测并保存状态。

### 4. 爬取用户内容（命令行）

```bash
# 爬取某用户的所有回答和文章
python main.py scrape zhang-jia-wei
```

其中 `zhang-jia-wei` 是知乎用户个人主页 URL 中的标识符：

```
https://www.zhihu.com/people/zhang-jia-wei
                             ^^^^^^^^^^^^^^ 这部分
```

### 5. 爬取问题下的回答（命令行）

```bash
# 爬取某个问题下的全部回答
python main.py question https://www.zhihu.com/question/12345

# 也可以直接用问题 ID
python main.py question 12345

# 只爬取默认排序下的前 20 个回答
python main.py question 12345 -n 20
```

### 6. 爬取单个回答（命令行）

```bash
# 爬取某个特定回答
python main.py answer https://www.zhihu.com/question/12345/answer/67890

# 爬取回答并附带完整评论区
python main.py answer https://www.zhihu.com/question/12345/answer/67890 --with-comments
```

### 7. 爬取用户想法（命令行）

```bash
# 爬取某用户的所有想法
python main.py pins zhang-jia-wei
```

### 8. 通用选项（命令行）

以下选项适用于 `scrape`、`question`、`answer`、`pins` 所有命令：

```bash
# 不下载图片（加快速度，启用纯文本模式）
--no-images

# 自定义延迟（更安全）
--delay-min 15 --delay-max 30

# 指定输出目录
--output ./my_backup

# 无头模式（不显示浏览器窗口）
--headless
```

示例：
```bash
python main.py scrape zhang-jia-wei --only-answers --no-images --headless
python main.py question 12345 -n 10 --delay-min 15 --delay-max 30
python main.py answer https://www.zhihu.com/question/12345/answer/67890 --with-comments --output ./data
python main.py pins zhang-jia-wei --no-images
```

---

## 输出结构

默认情况下（下载图片，推荐用于高保真归档），输出结构如下：

```text
output/
└── zhang-jia-wei/
    ├── links.json              # 所有链接列表
    ├── progress.json           # 爬取进度（用于断点续传）
    ├── answers/                # 回答
    │   ├── [2024-01-01] 问题标题 - 作者/
    │   │   ├── index.md        # Markdown 内容
    │   │   └── images/         # 本地化图片
    │   └── ...
    ├── articles/               # 文章
    │   ├── [2024-02-01] 文章标题 - 作者/
    │   │   ├── index.md
    │   │   └── images/
    │   └── ...
    └── pins/                   # 想法
        ├── [2024-03-01] 想法内容前50字 - 作者/
        │   ├── index.md
        │   └── images/
        └── ...
```

如果使用 `--no-images`（纯文本模式），则不再为每篇内容创建子文件夹，所有 Markdown 文件会直接放在类型目录下，文件名为 `日期_标题.md`，且文中图片使用占位符 `[图片]` 表示，不包含本地图片路径：

```text
output/
└── zhang-jia-wei/
    ├── links.json
    ├── progress.json
    ├── answers/
    │   ├── 2025-02-10_如何看待猫眼预测《哪吒 2》票房最终将达到 120 亿？.md
    │   ├── 2025-04-05_在你所知道的历史或体育赛事中，“Speed” 在 4 月 4 日的那次表现有什么特别之处？.md
    │   └── ...
    ├── articles/
    │   ├── 2024-02-01_文章标题示例.md
    │   └── ...
    └── pins/
        ├── 2024-03-01_想法内容前50字示例.md
        └── ...
```

## 断点续传

爬取过程中如果中断（网络问题、手动中止等），重新运行相同命令即可从断点继续：

```bash
# 程序会自动检测 progress.json，跳过已完成的内容
python main.py scrape zhang-jia-wei
```

---

## 合并多个 Markdown 为单个文档

ZhiZhu 附带了一个简单的合并脚本 `merge_md.py`，可以把某个目录下的所有 `.md` 文件合并成一个大纲清晰的 Markdown 文件，方便导入到笔记软件或作为语料输入给 LLM。

常见用法示例（在项目根目录执行）：

```bash
# 合并某用户的所有回答（output/heroblast/answers 下的所有 .md）
python merge_md.py output/heroblast/answers

# 合并某个问题下的所有回答，并指定输出文件名
python merge_md.py output/question_46631426/answers -o output/gp_qa_merged.md

# 按文件名排序（默认按文件内部的日期字段排序）
python merge_md.py output/heroblast/answers --sort-by name

# 自定义总标题与分隔符
python merge_md.py output/heroblast/answers \
  --title "我的知乎回答合集（按时间排序）" \
  --separator "====="
```

合并后的文件会在命令行中打印路径，同时在文件头部标注合并来源目录与总篇数。

---

## 注意事项

- **请求频率** — 默认每次请求间隔 10-20 秒。如遇反爬，建议增大延迟
- **登录状态** — 建议在登录状态下爬取，可获取完整内容
- **反爬触发** — 如果触发知乎反爬机制，程序会自动增加额外等待时间
- **大量内容** — 如果用户有数百个回答，爬取可能需要较长时间，请耐心等待

## 免责声明

- 本工具仅供个人学习、研究和数据备份使用
- 使用本工具抓取的内容，版权归原作者所有
- 请遵守知乎的使用条款和 `robots.txt` 规则
- 请勿用于商业用途或大规模爬取
- 使用频率过高可能导致 IP 被限制，请合理控制爬取频率

---

<div align="center">

*ZhiZhu，为你编织一张数据之网，捕获那个名为「自我」的幽灵。*

**Your Data, Your Model, Your Mirror.**

</div>
