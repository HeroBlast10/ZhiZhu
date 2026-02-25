"""
tui.py — ZhiZhu 知蛛 终端交互界面

使用方法:
    python tui.py

功能：提供基于 Textual 的 TUI 界面，覆盖 ZhiZhu 的全部功能：
  - 登录知乎
  - 爬取用户回答 / 文章 / 想法
  - 爬取问题下的回答
  - 爬取单个回答（可选附带评论）
  - 合并 Markdown 文件

原有命令行方式（python main.py ...）完全不受影响。
"""

from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path
from typing import Any

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.widgets import (
    Button,
    Checkbox,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    RichLog,
    Select,
    Static,
)

# ── stdout 重定向 ──────────────────────────────────────────────

class _TUIWriter:
    """将 print() 输出实时转发到 Textual RichLog 控件。"""

    def __init__(self, log: RichLog) -> None:
        self._log = log
        self._buf = ""

    def write(self, text: str) -> None:
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self._log.write(line)

    def flush(self) -> None:
        if self._buf:
            self._log.write(self._buf)
            self._buf = ""

    def fileno(self) -> int:
        return sys.__stdout__.fileno()


# ── 面板基类 ───────────────────────────────────────────────────

class _Panel(ScrollableContainer):
    """各功能面板的基类，提供标题 + 表单 + 按钮的通用骨架。"""

    DEFAULT_CSS = """
    _Panel {
        padding: 1 2;
        height: 1fr;
    }
    _Panel Label {
        margin-top: 1;
        color: $text-muted;
    }
    _Panel Input {
        margin-bottom: 0;
    }
    _Panel Checkbox {
        margin-top: 1;
    }
    _Panel Button {
        margin-top: 2;
        width: 20;
    }
    """

    panel_title: str = ""

    def _title(self) -> Static:
        return Static(f"[bold]{self.panel_title}[/bold]", classes="panel-heading")

    def collect_params(self) -> dict[str, Any]:
        """子类实现：收集表单值，返回传给 scraper 函数的参数字典。"""
        raise NotImplementedError

    def _common_inputs(
        self,
        show_output: bool = True,
        show_delay: bool = True,
        show_images: bool = True,
        show_headless: bool = True,
    ) -> list:
        """生成公共表单元素列表。"""
        widgets: list = []
        if show_images:
            widgets += [
                Checkbox("不下载图片 (--no-images)", id="no_images"),
            ]
        if show_delay:
            widgets += [
                Label("最小延迟（秒）"),
                Input("10", id="delay_min", placeholder="默认 10"),
                Label("最大延迟（秒）"),
                Input("20", id="delay_max", placeholder="默认 20"),
            ]
        if show_headless:
            widgets += [
                Checkbox("无头模式（不显示浏览器）", id="headless"),
            ]
        if show_output:
            widgets += [
                Label("输出目录（留空使用默认）"),
                Input("", id="output_dir", placeholder="例：output/my_backup"),
            ]
        return widgets

    def _get_common(self) -> dict[str, Any]:
        """读取公共表单值。"""
        params: dict[str, Any] = {}
        if self.query("#no_images"):
            try:
                params["download_img"] = not self.query_one("#no_images", Checkbox).value
            except Exception:
                pass
        if self.query("#delay_min"):
            try:
                params["delay_min"] = float(self.query_one("#delay_min", Input).value or 10)
            except ValueError:
                params["delay_min"] = 10.0
        if self.query("#delay_max"):
            try:
                params["delay_max"] = float(self.query_one("#delay_max", Input).value or 20)
            except ValueError:
                params["delay_max"] = 20.0
        if self.query("#headless"):
            try:
                params["headless"] = self.query_one("#headless", Checkbox).value
            except Exception:
                pass
        if self.query("#output_dir"):
            try:
                val = self.query_one("#output_dir", Input).value.strip()
                params["output_dir"] = Path(val) if val else None
            except Exception:
                params["output_dir"] = None
        return params


# ── 登录面板 ───────────────────────────────────────────────────

class LoginPanel(_Panel):
    panel_title = "登录知乎"

    def compose(self) -> ComposeResult:
        yield self._title()
        yield Label("等待超时（秒）")
        yield Input("300", id="timeout", placeholder="默认 300")
        yield Button("开始登录", id="start_btn", variant="primary")

    def collect_params(self) -> dict[str, Any]:
        try:
            timeout = int(self.query_one("#timeout", Input).value or 300)
        except ValueError:
            timeout = 300
        return {"timeout": timeout}


# ── 爬取用户面板 ───────────────────────────────────────────────

class ScrapeUserPanel(_Panel):
    panel_title = "爬取用户回答 / 文章 / 想法"

    def compose(self) -> ComposeResult:
        yield self._title()
        yield Label("用户 URL Token（必填）")
        yield Input("", id="user_token", placeholder="例：zhang-jia-wei")
        yield Label("爬取内容")
        yield Checkbox("回答", id="scrape_answers", value=True)
        yield Checkbox("文章", id="scrape_articles", value=True)
        for w in self._common_inputs():
            yield w
        yield Button("开始爬取", id="start_btn", variant="primary")

    def collect_params(self) -> dict[str, Any]:
        token = self.query_one("#user_token", Input).value.strip()
        if not token:
            raise ValueError("请填写用户 URL Token")
        params = self._get_common()
        params["user_url_token"] = token
        params["scrape_answers"] = self.query_one("#scrape_answers", Checkbox).value
        params["scrape_articles"] = self.query_one("#scrape_articles", Checkbox).value
        return params


# ── 爬取用户想法面板 ───────────────────────────────────────────

class ScrapePinsPanel(_Panel):
    panel_title = "爬取用户想法（Pins）"

    def compose(self) -> ComposeResult:
        yield self._title()
        yield Label("用户 URL Token（必填）")
        yield Input("", id="user_token", placeholder="例：zhang-jia-wei")
        for w in self._common_inputs():
            yield w
        yield Button("开始爬取", id="start_btn", variant="primary")

    def collect_params(self) -> dict[str, Any]:
        token = self.query_one("#user_token", Input).value.strip()
        if not token:
            raise ValueError("请填写用户 URL Token")
        params = self._get_common()
        params["user_url_token"] = token
        return params


# ── 爬取问题面板 ───────────────────────────────────────────────

class ScrapeQuestionPanel(_Panel):
    panel_title = "爬取问题下的回答"

    def compose(self) -> ComposeResult:
        yield self._title()
        yield Label("问题 URL 或问题 ID（必填）")
        yield Input("", id="question_input", placeholder="例：https://www.zhihu.com/question/12345 或 12345")
        yield Label("最大回答数（留空爬取全部）")
        yield Input("", id="max_answers", placeholder="例：20")
        for w in self._common_inputs():
            yield w
        yield Button("开始爬取", id="start_btn", variant="primary")

    def collect_params(self) -> dict[str, Any]:
        q = self.query_one("#question_input", Input).value.strip()
        if not q:
            raise ValueError("请填写问题 URL 或 ID")
        params = self._get_common()
        params["question_input"] = q
        raw = self.query_one("#max_answers", Input).value.strip()
        params["max_answers"] = int(raw) if raw.isdigit() else None
        return params


# ── 爬取单个回答面板 ───────────────────────────────────────────

class ScrapeAnswerPanel(_Panel):
    panel_title = "爬取单个回答"

    def compose(self) -> ComposeResult:
        yield self._title()
        yield Label("回答 URL（必填）")
        yield Input(
            "",
            id="answer_url",
            placeholder="例：https://www.zhihu.com/question/12345/answer/67890",
        )
        yield Checkbox("附带评论区 (--with-comments)", id="with_comments")
        for w in self._common_inputs():
            yield w
        yield Button("开始爬取", id="start_btn", variant="primary")

    def collect_params(self) -> dict[str, Any]:
        url = self.query_one("#answer_url", Input).value.strip()
        if not url:
            raise ValueError("请填写回答 URL")
        params = self._get_common()
        params["answer_input"] = url
        params["with_comments"] = self.query_one("#with_comments", Checkbox).value
        return params


# ── 合并文档面板 ───────────────────────────────────────────────

class MergePanel(_Panel):
    panel_title = "合并 Markdown 文件"

    SORT_OPTIONS = [("按日期（默认）", "date"), ("按文件名", "name")]

    def compose(self) -> ComposeResult:
        yield self._title()
        yield Label("来源目录（必填）")
        yield Input("", id="source_dir", placeholder="例：output/heroblast/answers")
        yield Label("输出文件路径（留空自动生成）")
        yield Input("", id="output_file", placeholder="例：output/merged.md")
        yield Label("排序方式")
        yield Select(
            [(label, value) for label, value in self.SORT_OPTIONS],
            id="sort_by",
            value="date",
        )
        yield Label("分隔符（默认 ---）")
        yield Input("---", id="separator")
        yield Label("总标题（留空自动生成）")
        yield Input("", id="title", placeholder="例：我的知乎回答合集")
        yield Button("开始合并", id="start_btn", variant="primary")

    def collect_params(self) -> dict[str, Any]:
        src = self.query_one("#source_dir", Input).value.strip()
        if not src:
            raise ValueError("请填写来源目录")
        out = self.query_one("#output_file", Input).value.strip()
        sort_val = self.query_one("#sort_by", Select).value
        sep = self.query_one("#separator", Input).value or "---"
        title = self.query_one("#title", Input).value.strip()

        source = Path(src)
        output_file = Path(out) if out else source.parent / f"{source.name}_merged.md"
        return {
            "source_dir": source,
            "output_file": output_file,
            "sort_by": sort_val if sort_val != Select.BLANK else "date",
            "separator": sep,
            "title": title,
        }


# ── 主应用 ─────────────────────────────────────────────────────

_NAV_ITEMS = [
    ("登录知乎",       "login"),
    ("爬取用户",       "scrape_user"),
    ("爬取用户想法",   "scrape_pins"),
    ("爬取问题回答",   "scrape_question"),
    ("爬取单个回答",   "scrape_answer"),
    ("合并文档",       "merge"),
]

_PANEL_MAP: dict[str, type[_Panel]] = {
    "login":           LoginPanel,
    "scrape_user":     ScrapeUserPanel,
    "scrape_pins":     ScrapePinsPanel,
    "scrape_question": ScrapeQuestionPanel,
    "scrape_answer":   ScrapeAnswerPanel,
    "merge":           MergePanel,
}


class ZhiZhuApp(App):
    """ZhiZhu 知蛛 TUI 主应用。"""

    TITLE = "ZhiZhu 知蛛 — 知乎内容爬虫"
    SUB_TITLE = "python tui.py | 命令行：python main.py --help"

    CSS = """
    Screen {
        layout: grid;
        grid-size: 2;
        grid-columns: 22 1fr;
    }

    #sidebar {
        background: $panel;
        border-right: solid $primary;
        padding: 1 0;
        height: 100%;
    }

    #sidebar-title {
        text-align: center;
        text-style: bold;
        color: $accent;
        padding: 0 1 1 1;
        border-bottom: solid $primary-darken-2;
    }

    #nav {
        height: 1fr;
    }

    ListItem {
        padding: 0 2;
    }

    ListView > ListItem.--highlight {
        background: $accent 20%;
    }

    #main-area {
        layout: grid;
        grid-size: 1;
        grid-rows: 1fr 1fr;
        height: 100%;
    }

    #panel-container {
        border-bottom: solid $primary-darken-2;
        height: 1fr;
        overflow-y: auto;
    }

    #log-area {
        height: 1fr;
        padding: 0 1;
    }

    #log-title {
        color: $text-muted;
        padding: 0 1;
    }

    .panel-heading {
        text-style: bold underline;
        color: $accent;
        margin-bottom: 1;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "退出"),
        Binding("ctrl+l", "clear_log", "清空日志"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._current_key = "login"
        self._running = False

    def compose(self) -> ComposeResult:
        yield Header()
        # 侧边栏
        with Vertical(id="sidebar"):
            yield Static("ZhiZhu 知蛛", id="sidebar-title")
            with ListView(id="nav"):
                for label, key in _NAV_ITEMS:
                    yield ListItem(Label(label), id=f"nav_{key}")
        # 主区域
        with Vertical(id="main-area"):
            with ScrollableContainer(id="panel-container"):
                yield LoginPanel(id="active_panel")
            yield Static("[bold]实时日志[/bold]", id="log-title")
            yield RichLog(id="log", highlight=True, markup=True, wrap=True)
        yield Footer()

    def on_mount(self) -> None:
        # 默认选中第一项
        nav = self.query_one("#nav", ListView)
        nav.index = 0

    @on(ListView.Selected, "#nav")
    def nav_selected(self, event: ListView.Selected) -> None:
        if event.item.id is None:
            return
        key = event.item.id.replace("nav_", "")
        self._switch_panel(key)

    def _switch_panel(self, key: str) -> None:
        if self._running:
            self.notify("请等待当前任务完成后再切换功能", severity="warning")
            return
        self._current_key = key
        container = self.query_one("#panel-container", ScrollableContainer)
        old = container.query("_Panel")
        for w in old:
            w.remove()
        panel_cls = _PANEL_MAP.get(key, LoginPanel)
        container.mount(panel_cls(id="active_panel"))

    @on(Button.Pressed, "#start_btn")
    def start_task(self) -> None:
        if self._running:
            self.notify("已有任务正在运行，请稍候", severity="warning")
            return
        try:
            panel = self.query_one("#active_panel", _Panel)
            params = panel.collect_params()
        except ValueError as e:
            self.notify(str(e), severity="error")
            return
        except Exception as e:
            self.notify(f"参数错误：{e}", severity="error")
            return

        log = self.query_one("#log", RichLog)
        log.clear()
        log.write(f"[cyan]>>> 开始任务：{self._current_key}[/cyan]")
        self._set_running(True)
        self._run_task(self._current_key, params)

    def _set_running(self, value: bool) -> None:
        self._running = value
        try:
            btn = self.query_one("#start_btn", Button)
            btn.disabled = value
            btn.label = "运行中..." if value else _get_btn_label(self._current_key)
        except Exception:
            pass

    @work(thread=True)
    def _run_task(self, key: str, params: dict[str, Any]) -> None:
        log = self.query_one("#log", RichLog)
        writer = _TUIWriter(log)
        old_stdout = sys.stdout
        sys.stdout = writer  # type: ignore[assignment]
        error: Exception | None = None
        try:
            _dispatch(key, params)
        except Exception as e:
            error = e
        finally:
            sys.stdout = old_stdout
            writer.flush()

        if error:
            self.call_from_thread(
                self.notify, f"任务失败：{error}", severity="error"
            )
            self.call_from_thread(log.write, f"[red]>>> 任务失败：{error}[/red]")
        else:
            self.call_from_thread(self.notify, "任务已完成！", severity="information")
            self.call_from_thread(log.write, "[green]>>> 任务完成[/green]")
        self.call_from_thread(self._set_running, False)

    def action_clear_log(self) -> None:
        self.query_one("#log", RichLog).clear()


# ── 任务分发 ───────────────────────────────────────────────────

def _dispatch(key: str, params: dict[str, Any]) -> None:
    """根据功能 key 调用对应的 scraper/merge 函数（在 Worker 线程中执行）。"""

    if key == "login":
        from scraper import login
        asyncio.run(login(**params))

    elif key == "scrape_user":
        from scraper import scrape_user
        asyncio.run(scrape_user(**params))

    elif key == "scrape_pins":
        from scraper import scrape_user_pins
        asyncio.run(scrape_user_pins(**params))

    elif key == "scrape_question":
        from scraper import scrape_question
        asyncio.run(scrape_question(**params))

    elif key == "scrape_answer":
        from scraper import scrape_single_answer
        asyncio.run(scrape_single_answer(**params))

    elif key == "merge":
        from merge_md import merge
        merge(**params)


def _get_btn_label(key: str) -> str:
    labels = {
        "login":           "开始登录",
        "scrape_user":     "开始爬取",
        "scrape_pins":     "开始爬取",
        "scrape_question": "开始爬取",
        "scrape_answer":   "开始爬取",
        "merge":           "开始合并",
    }
    return labels.get(key, "开始")


# ── 入口 ───────────────────────────────────────────────────────

if __name__ == "__main__":
    ZhiZhuApp().run()
