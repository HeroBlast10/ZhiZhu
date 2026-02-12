# 知乎用户内容爬虫

爬取指定知乎用户的 **所有回答** 和 **所有文章**，以高保真 Markdown 格式保存到本地。

## 功能特点

- **用户级爬取**：输入用户 URL token，自动爬取该用户的全部回答和文章
- **反检测机制**：内置浏览器指纹伪装（WebGL、Canvas、AudioContext 等）
- **智能延迟**：请求间随机等待 5-10 秒（可自定义），降低被封风险
- **断点续传**：自动记录进度，中断后重新运行会跳过已完成的内容
- **LaTeX 公式还原**：完美转换知乎数学公式为标准 LaTeX 语法
- **图片本地化**：自动下载文章图片到本地，重写 Markdown 路径
- **内容去噪**：自动去除广告卡片、视频占位符等干扰元素
- **持久化登录**：登录一次，后续爬取无需重复登录

## 系统要求

- Python 3.10+
- Windows / macOS / Linux
- 稳定的网络连接

## 安装

```bash
# 1. 安装 Python 依赖
pip install -r requirements.txt

# 2. 安装 Playwright 浏览器
playwright install chromium
```

## 使用方法

### 第一步：登录知乎

首次使用需要登录，登录状态会保存在 `browser_data/` 目录中。

```bash
python main.py login
```

程序会打开浏览器窗口，请在浏览器中手动完成登录。登录成功后程序会自动检测并保存状态。

### 第二步：爬取用户内容

```bash
# 爬取某用户的所有回答和文章
python main.py scrape zhang-jia-wei
```

其中 `zhang-jia-wei` 是知乎用户个人主页 URL 中的标识符：
`https://www.zhihu.com/people/zhang-jia-wei` → `zhang-jia-wei`

### 更多选项

```bash
# 只爬取回答
python main.py scrape zhang-jia-wei --only-answers

# 只爬取文章
python main.py scrape zhang-jia-wei --only-articles

# 不下载图片（加快速度）
python main.py scrape zhang-jia-wei --no-images

# 自定义延迟（更安全）
python main.py scrape zhang-jia-wei --delay-min 8 --delay-max 15

# 指定输出目录
python main.py scrape zhang-jia-wei --output ./my_backup

# 无头模式（不显示浏览器窗口）
python main.py scrape zhang-jia-wei --headless
```

## 输出结构

```
output/
└── zhang-jia-wei/
    ├── links.json          # 所有链接列表
    ├── progress.json       # 爬取进度（用于断点续传）
    ├── answers/            # 回答
    │   ├── [2024-01-01] 问题标题 - 作者/
    │   │   ├── index.md    # Markdown 内容
    │   │   └── images/     # 本地图片
    │   └── ...
    └── articles/           # 文章
        ├── [2024-02-01] 文章标题 - 作者/
        │   ├── index.md
        │   └── images/
        └── ...
```

## 断点续传

爬取过程中如果中断（网络问题、手动中止等），重新运行相同命令即可从断点继续：

```bash
# 程序会自动检测 progress.json，跳过已完成的内容
python main.py scrape zhang-jia-wei
```

## 注意事项

- **请求频率**：默认每次请求间隔 5-10 秒。如果遇到反爬，建议增大延迟
- **登录状态**：建议在登录状态下爬取，可获取完整内容
- **反爬触发**：如果触发知乎反爬（40362），程序会自动增加额外等待时间
- **大量内容**：如果用户有数百个回答，爬取可能需要较长时间，请耐心等待

## 免责声明

- 本工具仅供学习和研究使用，请遵守知乎的使用条款和 robots.txt 规则
- 使用本工具抓取的内容，版权归原作者所有
- 请勿用于商业用途或大规模爬取
- 使用频率过高可能导致 IP 被限制，请合理控制爬取频率
