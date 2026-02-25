"""
webui.py — ZhiZhu 知蛛 Web 交互界面

使用方法:
    python webui.py

会自动在本地浏览器中打开界面（默认 http://127.0.0.1:7860）。

功能：提供基于 Gradio 的 Web UI，覆盖 ZhiZhu 的全部功能：
  - 登录知乎
  - 爬取用户回答 / 文章
  - 爬取用户想法（Pins）
  - 爬取问题下的回答
  - 爬取单个回答（可选附带评论）
  - 合并 Markdown 文件

原有命令行方式（python main.py ...）完全不受影响。
"""

from __future__ import annotations

import asyncio
import queue
import sys
import threading
from pathlib import Path
from typing import Generator

import gradio as gr


# ── stdout 重定向 ──────────────────────────────────────────────

class _QueueWriter:
    """将 print() 输出收集到 queue.Queue，供生成器逐行 yield 给 Gradio。"""

    def __init__(self, q: queue.Queue) -> None:
        self._q = q
        self._buf = ""

    def write(self, text: str) -> None:
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self._q.put(line)

    def flush(self) -> None:
        if self._buf:
            self._q.put(self._buf)
            self._buf = ""

    def fileno(self) -> int:
        return sys.__stdout__.fileno()


def _run_in_thread(fn, q: queue.Queue) -> None:
    """在子线程中执行 fn()，完成后向队列推送 None（结束信号）。"""
    old_stdout = sys.stdout
    sys.stdout = _QueueWriter(q)  # type: ignore[assignment]
    try:
        fn()
    except Exception as e:
        sys.stdout.write(f"\n[ERROR] {e}\n")  # type: ignore[union-attr]
    finally:
        sys.stdout.flush()  # type: ignore[union-attr]
        sys.stdout = old_stdout
        q.put(None)  # 结束信号


def _stream_logs(fn) -> Generator[str, None, None]:
    """
    通用流式日志生成器。
    将 fn() 产生的所有 print() 输出实时 yield 给 Gradio Textbox。
    """
    q: queue.Queue = queue.Queue()
    t = threading.Thread(target=_run_in_thread, args=(fn, q), daemon=True)
    t.start()
    log_lines: list[str] = []
    while True:
        item = q.get()
        if item is None:
            break
        log_lines.append(item)
        yield "\n".join(log_lines)
    t.join()
    yield "\n".join(log_lines)


# ── 辅助：解析输出目录 ─────────────────────────────────────────

def _parse_output(val: str) -> Path | None:
    val = val.strip()
    return Path(val) if val else None


# ── 登录 ───────────────────────────────────────────────────────

def _login_fn(timeout: int):
    from scraper import login
    asyncio.run(login(timeout=int(timeout)))


def login_tab() -> None:
    gr.Markdown("## 登录知乎\n首次使用需要手动在浏览器中完成登录，登录状态会持久化保存。")
    timeout = gr.Number(value=300, label="等待超时（秒）", precision=0)
    btn = gr.Button("打开浏览器并登录", variant="primary")
    log = gr.Textbox(label="日志", lines=12, interactive=False, autoscroll=True)

    def run(t):
        yield from _stream_logs(lambda: _login_fn(t))

    btn.click(fn=run, inputs=[timeout], outputs=[log])


# ── 爬取用户 ──────────────────────────────────────────────────

def _scrape_user_fn(token, do_answers, do_articles, no_images, delay_min, delay_max, headless, out_dir):
    from scraper import scrape_user
    asyncio.run(scrape_user(
        user_url_token=token.strip(),
        scrape_answers=do_answers,
        scrape_articles=do_articles,
        download_img=not no_images,
        delay_min=float(delay_min),
        delay_max=float(delay_max),
        headless=headless,
        output_dir=_parse_output(out_dir),
    ))


def scrape_user_tab() -> None:
    gr.Markdown("## 爬取用户回答 / 文章\n输入知乎用户的 URL Token，批量爬取其全部回答和/或文章。")
    with gr.Row():
        token = gr.Textbox(label="用户 URL Token（必填）", placeholder="例：zhang-jia-wei")
        out_dir = gr.Textbox(label="输出目录（留空使用默认）", placeholder="例：output/my_backup")
    with gr.Row():
        do_answers = gr.Checkbox(label="爬取回答", value=True)
        do_articles = gr.Checkbox(label="爬取文章", value=True)
        no_images = gr.Checkbox(label="不下载图片（--no-images）", value=False)
        headless = gr.Checkbox(label="无头模式（不显示浏览器）", value=False)
    with gr.Row():
        delay_min = gr.Number(value=10, label="最小延迟（秒）", precision=1)
        delay_max = gr.Number(value=20, label="最大延迟（秒）", precision=1)
    btn = gr.Button("开始爬取", variant="primary")
    log = gr.Textbox(label="实时日志", lines=18, interactive=False, autoscroll=True)

    def run(tok, da, dart, ni, dmin, dmax, hl, od):
        if not tok.strip():
            yield "请先填写用户 URL Token"
            return
        yield from _stream_logs(lambda: _scrape_user_fn(tok, da, dart, ni, dmin, dmax, hl, od))

    btn.click(fn=run, inputs=[token, do_answers, do_articles, no_images, delay_min, delay_max, headless, out_dir], outputs=[log])


# ── 爬取用户想法 ───────────────────────────────────────────────

def _scrape_pins_fn(token, no_images, delay_min, delay_max, headless, out_dir):
    from scraper import scrape_user_pins
    asyncio.run(scrape_user_pins(
        user_url_token=token.strip(),
        download_img=not no_images,
        delay_min=float(delay_min),
        delay_max=float(delay_max),
        headless=headless,
        output_dir=_parse_output(out_dir),
    ))


def scrape_pins_tab() -> None:
    gr.Markdown("## 爬取用户想法（Pins）\n爬取指定用户发布的所有「想法」。")
    with gr.Row():
        token = gr.Textbox(label="用户 URL Token（必填）", placeholder="例：zhang-jia-wei")
        out_dir = gr.Textbox(label="输出目录（留空使用默认）", placeholder="例：output/my_backup")
    with gr.Row():
        no_images = gr.Checkbox(label="不下载图片（--no-images）", value=False)
        headless = gr.Checkbox(label="无头模式（不显示浏览器）", value=False)
    with gr.Row():
        delay_min = gr.Number(value=10, label="最小延迟（秒）", precision=1)
        delay_max = gr.Number(value=20, label="最大延迟（秒）", precision=1)
    btn = gr.Button("开始爬取", variant="primary")
    log = gr.Textbox(label="实时日志", lines=18, interactive=False, autoscroll=True)

    def run(tok, ni, dmin, dmax, hl, od):
        if not tok.strip():
            yield "请先填写用户 URL Token"
            return
        yield from _stream_logs(lambda: _scrape_pins_fn(tok, ni, dmin, dmax, hl, od))

    btn.click(fn=run, inputs=[token, no_images, delay_min, delay_max, headless, out_dir], outputs=[log])


# ── 爬取问题 ──────────────────────────────────────────────────

def _scrape_question_fn(question_input, max_answers, no_images, delay_min, delay_max, headless, out_dir):
    from scraper import scrape_question
    max_n = int(max_answers) if str(max_answers).strip().isdigit() else None
    asyncio.run(scrape_question(
        question_input=question_input.strip(),
        max_answers=max_n,
        download_img=not no_images,
        delay_min=float(delay_min),
        delay_max=float(delay_max),
        headless=headless,
        output_dir=_parse_output(out_dir),
    ))


def scrape_question_tab() -> None:
    gr.Markdown("## 爬取问题下的回答\n输入问题 URL 或问题 ID，批量爬取该问题下的回答。")
    with gr.Row():
        question_input = gr.Textbox(
            label="问题 URL 或问题 ID（必填）",
            placeholder="例：https://www.zhihu.com/question/12345 或 12345",
        )
        out_dir = gr.Textbox(label="输出目录（留空使用默认）", placeholder="例：output/question_12345")
    with gr.Row():
        max_answers = gr.Textbox(label="最大回答数（留空爬取全部）", placeholder="例：20", value="")
        no_images = gr.Checkbox(label="不下载图片（--no-images）", value=False)
        headless = gr.Checkbox(label="无头模式（不显示浏览器）", value=False)
    with gr.Row():
        delay_min = gr.Number(value=10, label="最小延迟（秒）", precision=1)
        delay_max = gr.Number(value=20, label="最大延迟（秒）", precision=1)
    btn = gr.Button("开始爬取", variant="primary")
    log = gr.Textbox(label="实时日志", lines=18, interactive=False, autoscroll=True)

    def run(qi, ma, ni, dmin, dmax, hl, od):
        if not qi.strip():
            yield "请先填写问题 URL 或 ID"
            return
        yield from _stream_logs(lambda: _scrape_question_fn(qi, ma, ni, dmin, dmax, hl, od))

    btn.click(fn=run, inputs=[question_input, max_answers, no_images, delay_min, delay_max, headless, out_dir], outputs=[log])


# ── 爬取单个回答 ───────────────────────────────────────────────

def _scrape_answer_fn(answer_url, with_comments, no_images, delay_min, delay_max, headless, out_dir):
    from scraper import scrape_single_answer
    asyncio.run(scrape_single_answer(
        answer_input=answer_url.strip(),
        with_comments=with_comments,
        download_img=not no_images,
        delay_min=float(delay_min),
        delay_max=float(delay_max),
        headless=headless,
        output_dir=_parse_output(out_dir),
    ))


def scrape_answer_tab() -> None:
    gr.Markdown("## 爬取单个回答\n精准爬取某个特定回答，可选同时爬取评论区。")
    with gr.Row():
        answer_url = gr.Textbox(
            label="回答 URL（必填）",
            placeholder="例：https://www.zhihu.com/question/12345/answer/67890",
        )
        out_dir = gr.Textbox(label="输出目录（留空使用默认）", placeholder="例：output/answer_67890")
    with gr.Row():
        with_comments = gr.Checkbox(label="附带评论区（--with-comments）", value=False)
        no_images = gr.Checkbox(label="不下载图片（--no-images）", value=False)
        headless = gr.Checkbox(label="无头模式（不显示浏览器）", value=False)
    with gr.Row():
        delay_min = gr.Number(value=10, label="最小延迟（秒）", precision=1)
        delay_max = gr.Number(value=20, label="最大延迟（秒）", precision=1)
    btn = gr.Button("开始爬取", variant="primary")
    log = gr.Textbox(label="实时日志", lines=18, interactive=False, autoscroll=True)

    def run(url, wc, ni, dmin, dmax, hl, od):
        if not url.strip():
            yield "请先填写回答 URL"
            return
        yield from _stream_logs(lambda: _scrape_answer_fn(url, wc, ni, dmin, dmax, hl, od))

    btn.click(fn=run, inputs=[answer_url, with_comments, no_images, delay_min, delay_max, headless, out_dir], outputs=[log])


# ── 合并文档 ──────────────────────────────────────────────────

def _merge_fn(source_dir, output_file, sort_by, separator, title):
    from merge_md import merge
    src = Path(source_dir.strip())
    out = Path(output_file.strip()) if output_file.strip() else src.parent / f"{src.name}_merged.md"
    merge(
        source_dir=src,
        output_file=out,
        sort_by=sort_by,
        separator=separator or "---",
        title=title.strip(),
    )


def merge_tab() -> None:
    gr.Markdown("## 合并 Markdown 文件\n将某个目录下的所有 `.md` 文件合并为一个大文件，方便导入笔记或输入给 LLM。")
    with gr.Row():
        source_dir = gr.Textbox(
            label="来源目录（必填）",
            placeholder="例：output/heroblast/answers",
        )
        output_file = gr.Textbox(
            label="输出文件路径（留空自动生成）",
            placeholder="例：output/merged.md",
        )
    with gr.Row():
        sort_by = gr.Dropdown(
            choices=[("按日期（默认）", "date"), ("按文件名", "name")],
            value="date",
            label="排序方式",
        )
        separator = gr.Textbox(label="分隔符（默认 ---）", value="---")
    title = gr.Textbox(label="总标题（留空自动生成）", placeholder="例：我的知乎回答合集")
    btn = gr.Button("开始合并", variant="primary")
    log = gr.Textbox(label="日志", lines=8, interactive=False, autoscroll=True)

    def run(sd, of, sb, sep, t):
        if not sd.strip():
            yield "请先填写来源目录"
            return
        yield from _stream_logs(lambda: _merge_fn(sd, of, sb, sep, t))

    btn.click(fn=run, inputs=[source_dir, output_file, sort_by, separator, title], outputs=[log])


# ── 主界面 ────────────────────────────────────────────────────

def build_app() -> gr.Blocks:
    with gr.Blocks(
        title="ZhiZhu 知蛛 — 知乎内容爬虫",
        theme=gr.themes.Soft(),
        css="""
        #header { text-align: center; margin-bottom: 8px; }
        #header h1 { font-size: 2em; }
        #header p  { color: #888; margin-top: 0; }
        """,
    ) as app:
        with gr.Column(elem_id="header"):
            gr.Markdown(
                "# ZhiZhu 知蛛\n"
                "**知乎内容爬虫 · 将你的知乎精神镜像归档为 Markdown**\n\n"
                "> 命令行方式：`python main.py --help`"
            )

        with gr.Tabs():
            with gr.Tab("登录知乎"):
                login_tab()
            with gr.Tab("爬取用户"):
                scrape_user_tab()
            with gr.Tab("爬取想法"):
                scrape_pins_tab()
            with gr.Tab("爬取问题"):
                scrape_question_tab()
            with gr.Tab("爬取单个回答"):
                scrape_answer_tab()
            with gr.Tab("合并文档"):
                merge_tab()

    return app


# ── 入口 ─────────────────────────────────────────────────────

if __name__ == "__main__":
    app = build_app()
    app.launch(
        server_name="127.0.0.1",
        server_port=7860,
        inbrowser=True,
        share=False,
    )
