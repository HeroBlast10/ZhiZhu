"""
诊断脚本：检查知乎用户回答页面的滚动容器和链接加载情况。
"""
import asyncio
from playwright.async_api import async_playwright
from stealth import STEALTH_JS
from pathlib import Path

BROWSER_DATA_DIR = Path("browser_data")


async def main():
    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            user_data_dir=str(BROWSER_DATA_DIR),
            headless=False,
            viewport={"width": 1280, "height": 800},
            locale="zh-CN",
        )
        page = context.pages[0] if context.pages else await context.new_page()
        await page.add_init_script(STEALTH_JS)

        url = "https://www.zhihu.com/people/heroblast/answers"
        print(f"访问: {url}")
        await page.goto(url, wait_until="domcontentloaded")
        await asyncio.sleep(5)

        # 关闭弹窗
        for sel in [
            'button:has-text("关闭")',
            'button:has-text("我知道了")',
            ".Modal-closeButton",
            ".css-1mfkfn3",
        ]:
            try:
                btn = page.locator(sel).first
                if await btn.count() > 0 and await btn.is_visible():
                    await btn.click()
                    await asyncio.sleep(0.5)
            except Exception:
                pass

        # 1. 检查初始链接数量
        links = await page.query_selector_all('a[href*="/answer/"]')
        print(f"\n初始链接数量: {len(links)}")

        # 2. 检查所有可能的滚动容器
        scroll_info = await page.evaluate("""() => {
            const results = [];
            // 检查常见的滚动容器
            const candidates = [
                document.documentElement,
                document.body,
                document.querySelector('.App-main'),
                document.querySelector('.ListShortcut'),
                document.querySelector('.Profile-main'),
                document.querySelector('.Profile-mainColumn'),
                document.querySelector('.css-1g4ubql'),
                document.querySelector('[class*="List"]'),
                document.querySelector('[class*="Profile"]'),
            ];
            
            for (const el of candidates) {
                if (!el) continue;
                const tag = el.tagName;
                const cls = el.className ? (typeof el.className === 'string' ? el.className.substring(0, 80) : '') : '';
                const scrollH = el.scrollHeight;
                const clientH = el.clientHeight;
                const scrollT = el.scrollTop;
                const overflow = window.getComputedStyle(el).overflow;
                const overflowY = window.getComputedStyle(el).overflowY;
                const isScrollable = scrollH > clientH;
                results.push({
                    tag, cls, scrollH, clientH, scrollT, overflow, overflowY, isScrollable
                });
            }
            
            // 也找所有 overflow:auto 或 overflow:scroll 的元素
            const allEls = document.querySelectorAll('*');
            const scrollableEls = [];
            for (const el of allEls) {
                const style = window.getComputedStyle(el);
                if ((style.overflow === 'auto' || style.overflow === 'scroll' || 
                     style.overflowY === 'auto' || style.overflowY === 'scroll') &&
                    el.scrollHeight > el.clientHeight + 10) {
                    scrollableEls.push({
                        tag: el.tagName,
                        cls: el.className ? (typeof el.className === 'string' ? el.className.substring(0, 80) : '') : '',
                        scrollH: el.scrollHeight,
                        clientH: el.clientHeight,
                        scrollT: el.scrollTop,
                        overflowY: style.overflowY,
                    });
                }
            }
            
            return { candidates: results, scrollableElements: scrollableEls };
        }""")

        print("\n=== 候选滚动容器 ===")
        for c in scroll_info['candidates']:
            marker = " <<<< SCROLLABLE" if c['isScrollable'] else ""
            print(f"  {c['tag']}.{c['cls'][:50]}: scrollH={c['scrollH']}, clientH={c['clientH']}, "
                  f"scrollT={c['scrollT']}, overflow={c['overflow']}, overflowY={c['overflowY']}{marker}")

        print(f"\n=== 所有可滚动元素 (overflow:auto/scroll 且 scrollH > clientH) ===")
        for s in scroll_info['scrollableElements']:
            print(f"  {s['tag']}.{s['cls'][:50]}: scrollH={s['scrollH']}, clientH={s['clientH']}, "
                  f"scrollT={s['scrollT']}, overflowY={s['overflowY']}")

        # 3. 尝试不同的滚动方式并检查效果
        print("\n=== 测试滚动方式 ===")

        # 方式 A: window.scrollBy
        before_links = len(await page.query_selector_all('a[href*="/answer/"]'))
        before_scroll = await page.evaluate("window.scrollY")
        for i in range(5):
            await page.evaluate("window.scrollBy(0, 1000)")
            await asyncio.sleep(1)
        after_scroll = await page.evaluate("window.scrollY")
        await asyncio.sleep(3)
        after_links = len(await page.query_selector_all('a[href*="/answer/"]'))
        print(f"  window.scrollBy: scrollY {before_scroll} -> {after_scroll}, links {before_links} -> {after_links}")

        # 方式 B: 键盘 End 键
        before_links = len(await page.query_selector_all('a[href*="/answer/"]'))
        for i in range(5):
            await page.keyboard.press("End")
            await asyncio.sleep(1)
        await asyncio.sleep(3)
        after_links = len(await page.query_selector_all('a[href*="/answer/"]'))
        print(f"  keyboard End: links {before_links} -> {after_links}")

        # 方式 C: 鼠标滚轮
        before_links = len(await page.query_selector_all('a[href*="/answer/"]'))
        for i in range(10):
            await page.mouse.wheel(0, 800)
            await asyncio.sleep(1)
        await asyncio.sleep(3)
        after_links = len(await page.query_selector_all('a[href*="/answer/"]'))
        print(f"  mouse.wheel: links {before_links} -> {after_links}")

        # 方式 D: 滚动 document.documentElement
        before_links = len(await page.query_selector_all('a[href*="/answer/"]'))
        for i in range(5):
            await page.evaluate("document.documentElement.scrollTop += 1000")
            await asyncio.sleep(1)
        await asyncio.sleep(3)
        after_links = len(await page.query_selector_all('a[href*="/answer/"]'))
        print(f"  documentElement.scrollTop: links {before_links} -> {after_links}")

        # 4. 检查是否有分页按钮
        pagination = await page.evaluate("""() => {
            const btns = document.querySelectorAll('button, a');
            const results = [];
            for (const b of btns) {
                const text = b.innerText.trim();
                if (text.includes('更多') || text.includes('加载') || text.includes('下一页') || 
                    text.includes('查看全部') || text.match(/^\\d+$/)) {
                    results.push({tag: b.tagName, text: text.substring(0, 50), href: b.href || ''});
                }
            }
            return results;
        }""")
        print(f"\n=== 分页/加载更多按钮 ===")
        for p in pagination:
            print(f"  {p['tag']}: '{p['text']}' href={p['href']}")

        # 5. 检查知乎 API 请求模式
        print("\n=== 等待 10 秒观察网络请求... ===")
        api_requests = []
        page.on("request", lambda req: api_requests.append(req.url) if "api" in req.url.lower() or "answers" in req.url.lower() else None)
        
        # 再滚动一些
        for i in range(5):
            await page.mouse.wheel(0, 1000)
            await asyncio.sleep(2)
        
        print(f"  捕获到 {len(api_requests)} 个 API 请求:")
        for r in api_requests[:20]:
            print(f"    {r[:150]}")

        # 最终链接数
        final_links = await page.query_selector_all('a[href*="/answer/"]')
        print(f"\n最终链接数量: {len(final_links)}")

        # 打印页面 URL（检查是否被重定向）
        print(f"当前页面 URL: {page.url}")

        input("\n按 Enter 关闭浏览器...")
        await context.close()


if __name__ == "__main__":
    asyncio.run(main())
