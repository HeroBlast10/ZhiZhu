"""
scraper.py — 知乎内容爬虫核心模块

功能：
1. 使用 Playwright 持久化上下文登录知乎（手动登录，保存 Cookie）
2. 爬取指定用户的所有回答和文章链接
3. 爬取指定问题下的所有（或前 N 个）回答
4. 爬取单个回答，可选附带评论区
5. 逐个访问并提取内容，转为 Markdown 保存
6. 内置反检测（stealth JS 注入、指纹伪装）
7. 请求间隔随机延迟，降低被封风险
"""

import asyncio
import hashlib
import json
import random
import re
import time
from datetime import date as dt_date, datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx
from playwright.async_api import async_playwright, Page, BrowserContext

from stealth import STEALTH_JS
from converter import ZhihuConverter

# ── 配置 ─────────────────────────────────────────────────────

USER_DATA_DIR = Path(__file__).parent / "browser_data"
DEFAULT_OUTPUT_DIR = Path(__file__).parent / "output"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)

IMG_HEADERS = {
    "Referer": "https://www.zhihu.com/",
    "User-Agent": USER_AGENT,
}

# 每次请求之间的延迟范围（秒）
MIN_DELAY = 5
MAX_DELAY = 10


# ── 工具函数 ──────────────────────────────────────────────────

def sanitize_filename(name: str) -> str:
    """清理文件名中不允许的字符。"""
    name = re.sub(r'[/\\:*?"<>|\x00-\x1f]', "_", name)
    name = name.strip(" .")
    if len(name) > 120:
        name = name[:120].rstrip(" .")
    return name or "untitled"


def random_delay():
    """返回一个随机延迟时间。"""
    return MIN_DELAY + random.random() * (MAX_DELAY - MIN_DELAY)


def _nested_get(d: dict, *keys):
    """安全地从嵌套字典中获取值。"""
    for key in keys:
        if isinstance(d, dict):
            d = d.get(key, {})
        else:
            return None
    return d if d != {} else None


def parse_question_id(input_str: str) -> str:
    """从 URL 或纯数字中提取问题 ID。"""
    match = re.search(r'question/(\d+)', input_str)
    if match:
        return match.group(1)
    if input_str.strip().isdigit():
        return input_str.strip()
    raise ValueError(f"无法识别问题 ID: {input_str}")


def parse_answer_url(input_str: str) -> tuple[str, str, str]:
    """
    从 URL 中提取信息，返回 (完整 URL, 问题 ID, 回答 ID)。

    支持格式:
        https://www.zhihu.com/question/12345/answer/67890
        /question/12345/answer/67890
    """
    match = re.search(r'question/(\d+)/answer/(\d+)', input_str)
    if match:
        qid, aid = match.group(1), match.group(2)
        full_url = f"https://www.zhihu.com/question/{qid}/answer/{aid}"
        return full_url, qid, aid
    raise ValueError(f"无法识别回答 URL: {input_str}")


# ── 浏览器上下文管理 ─────────────────────────────────────────

async def create_browser_context(pw, headless=False) -> BrowserContext:
    """创建带有反检测的持久化浏览器上下文。"""
    USER_DATA_DIR.mkdir(parents=True, exist_ok=True)

    width = 1920 + random.randint(-100, 100)
    height = 1080 + random.randint(-50, 50)

    launch_args = [
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-blink-features=AutomationControlled",
        "--disable-features=IsolateOrigins,site-per-process",
        "--disable-site-isolation-trials",
        "--disable-infobars",
        f"--window-size={width},{height}",
    ]

    context = await pw.chromium.launch_persistent_context(
        user_data_dir=str(USER_DATA_DIR),
        headless=headless,
        slow_mo=50,
        args=launch_args,
        viewport={"width": width, "height": height},
        user_agent=USER_AGENT,
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
        java_script_enabled=True,
    )

    # 注入反检测脚本
    await context.add_init_script(STEALTH_JS)

    return context


# ── 登录 ─────────────────────────────────────────────────────

async def login(timeout: int = 300):
    """
    打开知乎登录页面，等待用户手动登录。
    登录状态会保存在 browser_data 目录中，后续爬取无需重复登录。

    Args:
        timeout: 等待登录的超时时间（秒），默认 300 秒
    """
    print("=" * 60)
    print("🔐 知乎登录")
    print("=" * 60)
    print(f"将打开浏览器，请在 {timeout} 秒内完成登录。")
    print("登录成功后，程序会自动检测并保存登录状态。\n")

    async with async_playwright() as pw:
        context = await create_browser_context(pw, headless=False)
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto("https://www.zhihu.com/signin", wait_until="domcontentloaded")

            print("⏳ 等待登录... 请在浏览器中完成登录操作。")

            # 等待用户登录成功（检测跳转到首页或出现用户头像）
            start_time = time.time()
            while time.time() - start_time < timeout:
                url = page.url
                # 登录成功后一般会跳转到首页
                if "signin" not in url and "signup" not in url:
                    # 额外等待几秒确保 Cookie 完全写入
                    await asyncio.sleep(3)
                    print("✅ 登录成功！登录状态已保存。")
                    print(f"   数据目录: {USER_DATA_DIR.resolve()}")
                    return True
                await asyncio.sleep(2)

            print("❌ 登录超时，请重试。")
            return False

        finally:
            await context.close()


# ── 收集用户回答/文章列表 ────────────────────────────────────

async def _scroll_and_collect_links(
    page: Page, base_url: str, css_selector: str, url_filter_keywords: list[str]
) -> list[str]:
    """
    在用户的回答/文章列表页面中不断向下滚动，收集所有内容链接。

    Args:
        page: Playwright 页面对象
        base_url: 用户回答或文章页面 URL
        css_selector: 用于定位链接元素的 CSS 选择器
        url_filter_keywords: 用于过滤有效链接的关键词列表（匹配任一即可）

    Returns:
        去重后的链接列表
    """
    print(f"🌍 访问: {base_url}")
    await page.goto(base_url, wait_until="domcontentloaded")
    await asyncio.sleep(5)

    # 关闭可能的登录弹窗
    await _dismiss_popup(page)

    collected_links = set()
    no_new_count = 0
    max_no_new = 10  # 连续 10 次滚动没有新链接则认为到底了

    scroll_count = 0
    prev_scroll_height = 0

    while no_new_count < max_no_new:
        # 使用 CSS 选择器提取链接（比 JS 正则更可靠）
        link_elements = await page.query_selector_all(css_selector)
        links = []
        for el in link_elements:
            href = await el.get_attribute("href")
            if href:
                # 处理不同格式的 URL
                if href.startswith("//"):
                    href = "https:" + href
                elif href.startswith("/"):
                    href = "https://www.zhihu.com" + href
                elif not href.startswith("http"):
                    href = "https://www.zhihu.com/" + href
                if any(kw in href for kw in url_filter_keywords):
                    links.append(href.split("?")[0])

        prev_count = len(collected_links)
        collected_links.update(links)

        new_count = len(collected_links) - prev_count
        if new_count == 0:
            no_new_count += 1
        else:
            no_new_count = 0

        scroll_count += 1
        print(f"   📜 第 {scroll_count} 次滚动，已发现 {len(collected_links)} 个链接"
              + (f"（新增 {new_count}）" if new_count > 0 else "（无新增）"))

        # 检查页面是否包含明确的"到底"标识
        end_marker = await page.evaluate("""() => {
            const bodyText = document.body.innerText;
            return bodyText.includes('已显示全部') || bodyText.includes('没有更多了');
        }""")

        if end_marker and no_new_count >= 3:
            print("   📋 已到达列表底部（页面提示已显示全部）。")
            break

        # 检查页面高度是否还在增长（懒加载是否还在工作）
        current_scroll_height = await page.evaluate("document.body.scrollHeight")
        height_changed = current_scroll_height != prev_scroll_height
        prev_scroll_height = current_scroll_height

        # 只有在页面高度不再变化且连续多次无新链接时才认为到底
        if not height_changed and no_new_count >= 5:
            print("   📋 页面不再加载新内容，停止滚动。")
            break

        # 滚动 — 使用多种方式触发知乎的懒加载
        # window.scrollBy 无法触发知乎的 scroll 事件监听器，
        # 必须使用键盘 End 键或直接操作 documentElement.scrollTop
        scroll_distance = random.randint(800, 1500)
        await page.keyboard.press("End")
        await asyncio.sleep(0.5)
        await page.evaluate(f"document.documentElement.scrollTop += {scroll_distance}")
        await asyncio.sleep(0.3)
        await page.keyboard.press("End")

        # 等待新内容加载
        await asyncio.sleep(2.0 + random.random() * 2)
        # 额外等待：如果上次没有新链接，多等一会让懒加载有时间完成
        if new_count == 0:
            await asyncio.sleep(2.0)

    return sorted(collected_links)


async def collect_user_answers(page: Page, user_url_token: str) -> list[str]:
    """收集用户的所有回答链接。"""
    url = f"https://www.zhihu.com/people/{user_url_token}/answers"
    # 使用 CSS 选择器定位回答标题链接
    css_selector = ".ContentItem h2 a, .AnswerItem h2 a, h2.ContentItem-title a"
    return await _scroll_and_collect_links(page, url, css_selector, ["/answer/"])


async def collect_user_articles(page: Page, user_url_token: str) -> list[str]:
    """收集用户的所有文章链接。"""
    url = f"https://www.zhihu.com/people/{user_url_token}/posts"
    # 使用 CSS 选择器定位文章标题链接
    css_selector = ".ContentItem h2 a, .ArticleItem h2 a, h2.ContentItem-title a"
    return await _scroll_and_collect_links(page, url, css_selector, ["zhuanlan", "/p/"])


async def collect_question_answer_links(
    page: Page, question_id: str, max_answers: int | None = None
) -> list[str]:
    """
    在问题页面中滚动，收集回答链接。

    Args:
        page: Playwright 页面对象
        question_id: 知乎问题 ID
        max_answers: 最多收集的回答数量（None 表示全部）

    Returns:
        去重后的回答链接列表
    """
    url = f"https://www.zhihu.com/question/{question_id}"
    print(f"🌍 访问: {url}")
    await page.goto(url, wait_until="domcontentloaded")
    await asyncio.sleep(5)

    # 关闭可能的登录弹窗
    await _dismiss_popup(page)

    collected_links = set()
    no_new_count = 0
    max_no_new = 10
    scroll_count = 0
    prev_scroll_height = 0

    while no_new_count < max_no_new:
        # 使用 CSS 选择器提取回答链接
        link_elements = await page.query_selector_all('a[href*="/answer/"]')
        links = []
        for el in link_elements:
            href = await el.get_attribute("href")
            if href:
                if href.startswith("//"):
                    href = "https:" + href
                elif href.startswith("/"):
                    href = "https://www.zhihu.com" + href
                elif not href.startswith("http"):
                    href = "https://www.zhihu.com/" + href
                if f"/question/{question_id}/answer/" in href:
                    links.append(href.split("?")[0])

        prev_count = len(collected_links)
        collected_links.update(links)
        new_count = len(collected_links) - prev_count

        if new_count == 0:
            no_new_count += 1
        else:
            no_new_count = 0

        scroll_count += 1
        print(f"   📜 第 {scroll_count} 次滚动，已发现 {len(collected_links)} 个回答链接"
              + (f"（新增 {new_count}）" if new_count > 0 else "（无新增）"))

        # 检查是否已达到目标数量
        if max_answers and len(collected_links) >= max_answers:
            print(f"   📋 已达到目标数量 {max_answers}。")
            break

        # 检查页面是否包含明确的"到底"标识
        end_marker = await page.evaluate("""() => {
            const bodyText = document.body.innerText;
            return bodyText.includes('已显示全部') || bodyText.includes('没有更多了');
        }""")

        if end_marker and no_new_count >= 3:
            print("   📋 已到达列表底部（页面提示已显示全部）。")
            break

        # 检查页面高度是否还在增长
        current_scroll_height = await page.evaluate("document.body.scrollHeight")
        height_changed = current_scroll_height != prev_scroll_height
        prev_scroll_height = current_scroll_height

        if not height_changed and no_new_count >= 5:
            print("   📋 页面不再加载新内容，停止滚动。")
            break

        # 滚动 — 使用多种方式触发知乎的懒加载
        scroll_distance = random.randint(800, 1500)
        await page.keyboard.press("End")
        await asyncio.sleep(0.5)
        await page.evaluate(f"document.documentElement.scrollTop += {scroll_distance}")
        await asyncio.sleep(0.3)
        await page.keyboard.press("End")

        await asyncio.sleep(2.0 + random.random() * 2)
        if new_count == 0:
            await asyncio.sleep(2.0)

    result = sorted(collected_links)
    if max_answers:
        result = result[:max_answers]
    return result


# ── 页面内容提取 ─────────────────────────────────────────────

async def _dismiss_popup(page: Page) -> None:
    """关闭登录弹窗。"""
    try:
        btn = page.locator("button.Modal-closeButton")
        if await btn.count() > 0:
            await btn.click(timeout=2000)
            await page.wait_for_timeout(500)
    except Exception:
        pass


async def _safe_text(page: Page, selector: str, default: str) -> str:
    """安全获取元素文本。"""
    try:
        el = page.locator(selector).first
        return await el.inner_text(timeout=3000)
    except Exception:
        return default


async def _extract_date(page: Page) -> str:
    """提取发布日期。"""
    try:
        meta = await page.locator('meta[itemprop="datePublished"]').get_attribute(
            "content", timeout=2000
        )
        if meta:
            return meta[:10]
    except Exception:
        pass
    # 尝试从页面内容中提取日期
    try:
        date_text = await _safe_text(page, ".ContentItem-time", "")
        if not date_text:
            date_text = await _safe_text(page, ".Post-Header .ContentItem-time", "")
        match = re.search(r"(\d{4}-\d{2}-\d{2})", date_text)
        if match:
            return match.group(1)
    except Exception:
        pass
    return dt_date.today().isoformat()


async def extract_answer(page: Page, url: str) -> dict:
    """
    提取知乎回答内容。

    Returns:
        {"title": str, "author": str, "html": str, "date": str, "type": "answer", "url": str}
    """
    await page.goto(url, wait_until="domcontentloaded")
    await page.wait_for_timeout(3000)
    await _dismiss_popup(page)

    # 检查反爬
    text = await page.locator("body").inner_text()
    if "40362" in text or "请求存在异常" in text:
        raise Exception(f"触发知乎反爬 (40362): {url}")

    # 等待内容加载
    try:
        await page.wait_for_selector(".QuestionAnswer-content, .AnswerCard", timeout=15000)
    except Exception:
        # 有时候页面结构不同，尝试等待 RichText
        await page.wait_for_selector(".RichText", timeout=10000)

    # 点击"阅读全文"
    try:
        read_more = page.locator("button:has-text('阅读全文')").first
        if await read_more.count() > 0:
            await read_more.click()
            await page.wait_for_timeout(1000)
    except Exception:
        pass

    title = await _safe_text(page, "h1.QuestionHeader-title", "未知问题")
    author = await _safe_text(page, ".AuthorInfo-name .UserLink-link", "未知作者")
    if author == "未知作者":
        author = await _safe_text(page, ".AuthorInfo span.UserLink-Name", "未知作者")
    date = await _extract_date(page)

    # 提取回答 HTML
    html = ""
    try:
        html = await page.locator(".QuestionAnswer-content .RichText").first.inner_html()
    except Exception:
        try:
            html = await page.locator(".RichText").first.inner_html()
        except Exception:
            html = await page.locator("body").inner_html()

    return {
        "title": title.strip(),
        "author": author.strip(),
        "html": html,
        "date": date,
        "type": "answer",
        "url": url,
    }


async def extract_article(page: Page, url: str) -> dict:
    """
    提取知乎专栏文章内容。

    Returns:
        {"title": str, "author": str, "html": str, "date": str, "type": "article", "url": str}
    """
    await page.goto(url, wait_until="domcontentloaded")
    await page.wait_for_timeout(3000)
    await _dismiss_popup(page)

    text = await page.locator("body").inner_text()
    if "40362" in text or "请求存在异常" in text:
        raise Exception(f"触发知乎反爬 (40362): {url}")

    try:
        await page.wait_for_selector("h1.Post-Title", timeout=15000)
    except Exception:
        await page.wait_for_selector(".RichText", timeout=10000)

    title = await _safe_text(page, "h1.Post-Title", "未知标题")
    author = await _safe_text(page, ".AuthorInfo span.UserLink-Name", "未知作者")
    if author == "未知作者":
        author = await _safe_text(page, ".AuthorInfo-name .UserLink-link", "未知作者")
    date = await _extract_date(page)

    html = ""
    try:
        rich = page.locator(".Post-RichTextContainer .RichText").first
        if await rich.count() > 0:
            html = await rich.inner_html()
        else:
            html = await page.locator(".RichText").first.inner_html()
    except Exception:
        html = await page.locator("body").inner_html()

    # 尝试获取头图
    try:
        title_img = page.locator("img.TitleImage").first
        if await title_img.count() > 0:
            src = await title_img.get_attribute("src")
            if src:
                html = f'<img src="{src}" alt="TitleImage"><br>{html}'
    except Exception:
        pass

    return {
        "title": title.strip(),
        "author": author.strip(),
        "html": html,
        "date": date,
        "type": "article",
        "url": url,
    }


# ── 评论提取 ─────────────────────────────────────────────────

async def _fetch_comment_page(page: Page, url: str) -> dict:
    """通过浏览器 fetch 获取一页评论数据。"""
    return await page.evaluate("""
        async (url) => {
            try {
                const resp = await fetch(url, { credentials: 'include' });
                if (!resp.ok) return { data: [], paging: { is_end: true } };
                return await resp.json();
            } catch (e) {
                return { data: [], paging: { is_end: true } };
            }
        }
    """, url)


async def extract_comments(page: Page, answer_id: str) -> list[dict]:
    """
    通过知乎 API 提取回答下的所有评论（包含子评论）。

    Args:
        page: Playwright 页面对象（必须在知乎域名下）
        answer_id: 回答 ID

    Returns:
        评论列表，每个评论包含 author, content, created_time, like_count, child_comments
    """
    print(f"   💬 正在获取评论...")

    all_comments = []
    offset = 0
    limit = 20

    while True:
        api_url = (
            f"https://www.zhihu.com/api/v4/comment_v5/answers/{answer_id}"
            f"/root_comment?order_by=score&limit={limit}&offset={offset}"
        )
        data = await _fetch_comment_page(page, api_url)

        if not data.get("data"):
            break

        for comment in data["data"]:
            root = {
                "author": _nested_get(comment, "author", "member", "name") or "匿名用户",
                "content": comment.get("content", ""),
                "created_time": comment.get("created_time", 0),
                "like_count": comment.get("like_count", 0),
                "child_comments": [],
            }

            # 获取子评论
            child_count = comment.get("child_comment_count", 0)
            if child_count > 0:
                comment_id = comment.get("id", "")
                child_offset = 0
                while True:
                    child_url = (
                        f"https://www.zhihu.com/api/v4/comment_v5/comment/{comment_id}"
                        f"/child_comment?order_by=ts&limit=20&offset={child_offset}"
                    )
                    child_data = await _fetch_comment_page(page, child_url)

                    if not child_data.get("data"):
                        break

                    for child in child_data["data"]:
                        root["child_comments"].append({
                            "author": _nested_get(child, "author", "member", "name") or "匿名用户",
                            "content": child.get("content", ""),
                            "created_time": child.get("created_time", 0),
                            "like_count": child.get("like_count", 0),
                            "reply_to": _nested_get(child, "reply_to_author", "member", "name") or "",
                        })

                    paging = child_data.get("paging", {})
                    if paging.get("is_end", True):
                        break
                    child_offset += 20
                    await asyncio.sleep(0.3)

            all_comments.append(root)

        paging = data.get("paging", {})
        if paging.get("is_end", True):
            break
        offset += limit
        await asyncio.sleep(0.5)

    total = len(all_comments)
    child_total = sum(len(c["child_comments"]) for c in all_comments)
    print(f"   💬 共获取 {total} 条根评论，{child_total} 条子评论")

    return all_comments


def format_comments_markdown(comments: list[dict]) -> str:
    """将评论数据格式化为 Markdown 文本。"""
    if not comments:
        return ""

    lines = ["\n\n---\n", "## 评论区\n"]

    for i, comment in enumerate(comments, 1):
        ts = comment.get("created_time", 0)
        time_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "未知时间"
        author = comment.get("author", "匿名用户")
        likes = comment.get("like_count", 0)
        content = comment.get("content", "")

        lines.append(f"### {i}楼 · {author} · {time_str} · 👍 {likes}\n")
        lines.append(f"{content}\n")

        # 子评论
        for child in comment.get("child_comments", []):
            child_ts = child.get("created_time", 0)
            child_time = datetime.fromtimestamp(child_ts).strftime("%Y-%m-%d %H:%M") if child_ts else "未知时间"
            child_author = child.get("author", "匿名用户")
            child_likes = child.get("like_count", 0)
            child_content = child.get("content", "")
            reply_to = child.get("reply_to", "")

            reply_prefix = f"回复 {reply_to} " if reply_to else ""
            lines.append(f"> **{child_author}** {reply_prefix}· {child_time} · 👍 {child_likes}  ")
            lines.append(f"> {child_content}")
            lines.append(">")

        lines.append("")

    return "\n".join(lines)


# ── 图片下载 ─────────────────────────────────────────────────

async def download_images(img_urls: list[str], dest: Path) -> dict[str, str]:
    """下载图片到本地，返回 URL → 本地路径 的映射。"""
    dest.mkdir(parents=True, exist_ok=True)
    url_to_local: dict[str, str] = {}

    limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
    async with httpx.AsyncClient(
        headers=IMG_HEADERS,
        timeout=30.0,
        follow_redirects=True,
        limits=limits,
    ) as client:
        for img_url in img_urls:
            try:
                if img_url.startswith("//"):
                    img_url = "https:" + img_url

                if "data:image" in img_url or "equation" in img_url:
                    continue

                resp = await client.get(img_url)
                resp.raise_for_status()

                ext = Path(urlparse(img_url).path).suffix or ".jpg"
                if len(ext) > 5:
                    ext = ".jpg"

                fname = hashlib.md5(img_url.encode()).hexdigest()[:12] + ext
                fpath = dest / fname
                fpath.write_bytes(resp.content)
                url_to_local[img_url] = f"images/{fname}"
            except Exception:
                pass

    return url_to_local


# ── 保存单篇内容为 Markdown ──────────────────────────────────

async def save_content_as_markdown(
    info: dict, output_dir: Path, download_img: bool = True,
    comments: list[dict] | None = None,
) -> Path:
    """
    将提取到的内容保存为 Markdown 文件。

    Args:
        info: extract_answer 或 extract_article 返回的字典
        output_dir: 输出根目录
        download_img: 是否下载图片到本地
        comments: 评论列表（可选，传入则追加评论区）

    Returns:
        保存的文件路径
    """
    title = info["title"]
    author = info["author"]
    date = info["date"]
    html = info["html"]
    content_type = info["type"]
    url = info["url"]

    type_label = "回答" if content_type == "answer" else "文章"
    folder_name = sanitize_filename(f"[{date}] {title} - {author}")

    # 按类型分目录
    type_dir = output_dir / ("answers" if content_type == "answer" else "articles")
    folder = type_dir / folder_name
    folder.mkdir(parents=True, exist_ok=True)

    # 下载图片
    img_map = {}
    if download_img:
        img_urls = ZhihuConverter.extract_image_urls(html)
        if img_urls:
            print(f"   🖼️  发现 {len(img_urls)} 张图片，正在下载...")
            img_dir = folder / "images"
            img_map = await download_images(img_urls, img_dir)
            print(f"   ✅ 成功下载 {len(img_map)} 张图片")
            # 清理空目录
            if img_dir.exists() and not any(img_dir.iterdir()):
                img_dir.rmdir()

    # HTML → Markdown
    converter = ZhihuConverter(img_map=img_map)
    md = converter.convert(html)

    # 拼接元信息头
    header = (
        f"# {title}\n\n"
        f"> **类型**: {type_label}  \n"
        f"> **作者**: {author}  \n"
        f"> **来源**: [{url}]({url})  \n"
        f"> **日期**: {date}\n\n"
        f"---\n\n"
    )

    md_path = folder / "index.md"

    # 拼接评论区
    comments_md = ""
    if comments:
        comments_md = format_comments_markdown(comments)

    md_path.write_text(header + md + comments_md, encoding="utf-8")

    return md_path


def _scan_done_urls_from_disk(output_dir: Path) -> set[str]:
    """
    扫描输出目录中已存在的 Markdown 文件，从文件头部提取来源 URL。
    这样即使 progress.json 丢失或不完整，已下载的内容也不会被重复爬取。
    """
    done = set()
    url_pattern = re.compile(r'>\s*\*\*来源\*\*:\s*\[([^\]]+)\]')
    for subdir in ("answers", "articles"):
        type_dir = output_dir / subdir
        if not type_dir.exists():
            continue
        for md_file in type_dir.rglob("index.md"):
            try:
                # 只读前 500 字节即可，URL 在文件头部
                text = md_file.read_text(encoding="utf-8")[:500]
                m = url_pattern.search(text)
                if m:
                    done.add(m.group(1))
            except Exception:
                pass
    return done


# ── 主爬取流程 ────────────────────────────────────────────────

async def scrape_user(
    user_url_token: str,
    output_dir: Path | None = None,
    scrape_answers: bool = True,
    scrape_articles: bool = True,
    download_img: bool = True,
    delay_min: float = 5.0,
    delay_max: float = 10.0,
    headless: bool = False,
):
    """
    爬取指定知乎用户的所有回答和/或文章。

    Args:
        user_url_token: 知乎用户的 URL token（个人主页 URL 中的标识符）
                        例如 https://www.zhihu.com/people/xxx 中的 xxx
        output_dir: 输出目录
        scrape_answers: 是否爬取回答
        scrape_articles: 是否爬取文章
        download_img: 是否下载图片
        delay_min: 请求间最小延迟（秒）
        delay_max: 请求间最大延迟（秒）
        headless: 是否使用无头模式
    """
    global MIN_DELAY, MAX_DELAY
    MIN_DELAY = delay_min
    MAX_DELAY = delay_max

    if output_dir is None:
        output_dir = DEFAULT_OUTPUT_DIR / sanitize_filename(user_url_token)

    output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print(f"📚 开始爬取用户: {user_url_token}")
    print(f"   输出目录: {output_dir.resolve()}")
    print(f"   爬取回答: {'是' if scrape_answers else '否'}")
    print(f"   爬取文章: {'是' if scrape_articles else '否'}")
    print(f"   下载图片: {'是' if download_img else '否'}")
    print(f"   请求延迟: {delay_min}-{delay_max} 秒")
    print("=" * 60)

    async with async_playwright() as pw:
        context = await create_browser_context(pw, headless=headless)

        try:
            page = context.pages[0] if context.pages else await context.new_page()

            # ── 收集链接 ──
            all_urls = []

            if scrape_answers:
                print("\n📝 正在收集回答列表...")
                answer_urls = await collect_user_answers(page, user_url_token)
                print(f"   共发现 {len(answer_urls)} 个回答")
                all_urls.extend([(url, "answer") for url in answer_urls])

            if scrape_articles:
                print("\n📝 正在收集文章列表...")
                # 在收集文章之前添加延迟
                if scrape_answers:
                    delay = random_delay()
                    print(f"   ⏳ 等待 {delay:.1f} 秒...")
                    await asyncio.sleep(delay)
                article_urls = await collect_user_articles(page, user_url_token)
                print(f"   共发现 {len(article_urls)} 篇文章")
                all_urls.extend([(url, "article") for url in article_urls])

            if not all_urls:
                print("\n⚠️  未发现任何内容，请检查用户 URL token 是否正确。")
                return

            total = len(all_urls)
            print(f"\n🚀 共计 {total} 项内容待爬取\n")

            # ── 保存链接列表（用于断点续传） ──
            links_file = output_dir / "links.json"
            links_data = [{"url": url, "type": t} for url, t in all_urls]
            links_file.write_text(
                json.dumps(links_data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"📋 链接列表已保存到: {links_file}\n")

            # ── 检查已爬取的内容（断点续传） ──
            progress_file = output_dir / "progress.json"
            done_urls = set()
            if progress_file.exists():
                try:
                    done_data = json.loads(progress_file.read_text(encoding="utf-8"))
                    done_urls = set(done_data.get("done", []))
                except Exception:
                    pass

            # 扫描磁盘上已存在的文件，补充 progress.json 可能遗漏的记录
            disk_urls = _scan_done_urls_from_disk(output_dir)
            if disk_urls - done_urls:
                print(f"📂 从磁盘扫描发现 {len(disk_urls - done_urls)} 个已下载但未记录的内容")
                done_urls |= disk_urls

            if done_urls:
                # 只统计与当前链接列表匹配的数量
                matched = sum(1 for url, _ in all_urls if url in done_urls)
                print(f"📌 检测到之前的进度，已完成 {matched}/{total} 项，将跳过。\n")

                # 同步更新 progress.json
                progress_file.write_text(
                    json.dumps({"done": list(done_urls)}, ensure_ascii=False),
                    encoding="utf-8",
                )

            # ── 逐个爬取 ──
            success_count = 0
            fail_count = 0

            for idx, (url, content_type) in enumerate(all_urls, 1):
                if url in done_urls:
                    print(f"[{idx}/{total}] ⏭️  跳过（已完成）: {url}")
                    success_count += 1
                    continue

                print(f"[{idx}/{total}] 📥 正在爬取{' 回答' if content_type == 'answer' else '文章'}: {url}")

                try:
                    if content_type == "answer":
                        info = await extract_answer(page, url)
                    else:
                        info = await extract_article(page, url)

                    md_path = await save_content_as_markdown(info, output_dir, download_img)
                    print(f"   💾 已保存: {md_path}")

                    success_count += 1
                    done_urls.add(url)

                    # 更新进度
                    progress_file.write_text(
                        json.dumps({"done": list(done_urls)}, ensure_ascii=False),
                        encoding="utf-8",
                    )

                except Exception as e:
                    fail_count += 1
                    print(f"   ❌ 失败: {e}")

                    # 如果触发反爬，加大延迟
                    if "40362" in str(e) or "反爬" in str(e):
                        extra_wait = 30 + random.random() * 30
                        print(f"   ⚠️  触发反爬机制，额外等待 {extra_wait:.0f} 秒...")
                        await asyncio.sleep(extra_wait)

                # 请求间延迟
                if idx < total:
                    delay = random_delay()
                    print(f"   ⏳ 等待 {delay:.1f} 秒...\n")
                    await asyncio.sleep(delay)

            # ── 汇总 ──
            print("\n" + "=" * 60)
            print("✨ 爬取完成！")
            print(f"   成功: {success_count}")
            print(f"   失败: {fail_count}")
            print(f"   输出目录: {output_dir.resolve()}")
            print("=" * 60)

        finally:
            await context.close()


async def scrape_question(
    question_input: str,
    max_answers: int | None = None,
    output_dir: Path | None = None,
    download_img: bool = True,
    delay_min: float = 10.0,
    delay_max: float = 20.0,
    headless: bool = False,
):
    """
    爬取指定知乎问题下的回答。

    Args:
        question_input: 问题 URL 或纯数字 ID
        max_answers: 最多爬取的回答数量（None 表示全部）
        output_dir: 输出目录
        download_img: 是否下载图片
        delay_min: 请求间最小延迟（秒）
        delay_max: 请求间最大延迟（秒）
        headless: 是否使用无头模式
    """
    global MIN_DELAY, MAX_DELAY
    MIN_DELAY = delay_min
    MAX_DELAY = delay_max

    question_id = parse_question_id(question_input)

    if output_dir is None:
        output_dir = DEFAULT_OUTPUT_DIR / f"question_{question_id}"
    output_dir.mkdir(parents=True, exist_ok=True)

    limit_str = f"前 {max_answers} 个" if max_answers else "全部"

    print("=" * 60)
    print(f"📚 开始爬取问题: {question_id}")
    print(f"   问题链接: https://www.zhihu.com/question/{question_id}")
    print(f"   爬取数量: {limit_str}")
    print(f"   输出目录: {output_dir.resolve()}")
    print(f"   下载图片: {'是' if download_img else '否'}")
    print(f"   请求延迟: {delay_min}-{delay_max} 秒")
    print("=" * 60)

    async with async_playwright() as pw:
        context = await create_browser_context(pw, headless=headless)

        try:
            page = context.pages[0] if context.pages else await context.new_page()

            # ── 收集回答链接 ──
            print("\n📝 正在收集回答列表...")
            answer_urls = await collect_question_answer_links(page, question_id, max_answers)
            print(f"   共发现 {len(answer_urls)} 个回答")

            if not answer_urls:
                print("\n⚠️  未发现任何回答，请检查问题 ID 是否正确。")
                return

            total = len(answer_urls)
            print(f"\n🚀 共计 {total} 个回答待爬取\n")

            # ── 保存链接列表 ──
            links_file = output_dir / "links.json"
            links_data = [{"url": url, "type": "answer"} for url in answer_urls]
            links_file.write_text(
                json.dumps(links_data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"📋 链接列表已保存到: {links_file}\n")

            # ── 断点续传 ──
            progress_file = output_dir / "progress.json"
            done_urls = set()
            if progress_file.exists():
                try:
                    done_data = json.loads(progress_file.read_text(encoding="utf-8"))
                    done_urls = set(done_data.get("done", []))
                except Exception:
                    pass

            # 扫描磁盘上已存在的文件，补充 progress.json 可能遗漏的记录
            disk_urls = _scan_done_urls_from_disk(output_dir)
            if disk_urls - done_urls:
                print(f"📂 从磁盘扫描发现 {len(disk_urls - done_urls)} 个已下载但未记录的内容")
                done_urls |= disk_urls

            if done_urls:
                matched = sum(1 for u in answer_urls if u in done_urls)
                print(f"📌 检测到之前的进度，已完成 {matched}/{total} 项，将跳过。\n")

                progress_file.write_text(
                    json.dumps({"done": list(done_urls)}, ensure_ascii=False),
                    encoding="utf-8",
                )

            # ── 逐个爬取 ──
            success_count = 0
            fail_count = 0

            for idx, url in enumerate(answer_urls, 1):
                if url in done_urls:
                    print(f"[{idx}/{total}] ⏭️  跳过（已完成）: {url}")
                    success_count += 1
                    continue

                print(f"[{idx}/{total}] 📥 正在爬取回答: {url}")

                try:
                    info = await extract_answer(page, url)
                    md_path = await save_content_as_markdown(info, output_dir, download_img)
                    print(f"   💾 已保存: {md_path}")

                    success_count += 1
                    done_urls.add(url)

                    progress_file.write_text(
                        json.dumps({"done": list(done_urls)}, ensure_ascii=False),
                        encoding="utf-8",
                    )

                except Exception as e:
                    fail_count += 1
                    print(f"   ❌ 失败: {e}")

                    if "40362" in str(e) or "反爬" in str(e):
                        extra_wait = 30 + random.random() * 30
                        print(f"   ⚠️  触发反爬机制，额外等待 {extra_wait:.0f} 秒...")
                        await asyncio.sleep(extra_wait)

                if idx < total:
                    delay = random_delay()
                    print(f"   ⏳ 等待 {delay:.1f} 秒...\n")
                    await asyncio.sleep(delay)

            # ── 问题爬取汇总 ──
            print("\n" + "=" * 60)
            print("✨ 问题回答爬取完成！")
            print(f"   成功: {success_count}")
            print(f"   失败: {fail_count}")
            print(f"   输出目录: {output_dir.resolve()}")
            print("=" * 60)

        finally:
            await context.close()


async def scrape_single_answer(
    answer_input: str,
    output_dir: Path | None = None,
    download_img: bool = True,
    with_comments: bool = False,
    delay_min: float = 10.0,
    delay_max: float = 20.0,
    headless: bool = False,
):
    """
    爬取单个知乎回答（可选附带评论区）。

    Args:
        answer_input: 回答 URL（包含 /question/xxx/answer/xxx）
        output_dir: 输出目录
        download_img: 是否下载图片
        with_comments: 是否同时爬取评论区
        delay_min: 请求间最小延迟（秒）
        delay_max: 请求间最大延迟（秒）
        headless: 是否使用无头模式
    """
    global MIN_DELAY, MAX_DELAY
    MIN_DELAY = delay_min
    MAX_DELAY = delay_max

    answer_url, question_id, answer_id = parse_answer_url(answer_input)

    if output_dir is None:
        output_dir = DEFAULT_OUTPUT_DIR / f"answer_{answer_id}"
    output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print(f"📚 爬取单个回答")
    print(f"   回答链接: {answer_url}")
    print(f"   包含评论: {'是' if with_comments else '否'}")
    print(f"   输出目录: {output_dir.resolve()}")
    print(f"   下载图片: {'是' if download_img else '否'}")
    print("=" * 60)

    async with async_playwright() as pw:
        context = await create_browser_context(pw, headless=headless)

        try:
            page = context.pages[0] if context.pages else await context.new_page()

            print(f"\n📥 正在爬取回答: {answer_url}")
            info = await extract_answer(page, answer_url)

            # 获取评论
            comments = None
            if with_comments:
                comments = await extract_comments(page, answer_id)

            md_path = await save_content_as_markdown(
                info, output_dir, download_img, comments=comments
            )
            print(f"   💾 已保存: {md_path}")

            print("\n" + "=" * 60)
            print("✨ 爬取完成！")
            print(f"   输出目录: {output_dir.resolve()}")
            print("=" * 60)

        finally:
            await context.close()
