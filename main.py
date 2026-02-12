"""
main.py — 知乎用户内容爬虫入口

使用方法:
    # 第一步：登录知乎（首次使用需要）
    python main.py login

    # 第二步：爬取用户内容
    python main.py scrape <user_url_token>

    # 更多选项
    python main.py scrape <user_url_token> --no-images --delay-min 8 --delay-max 15

免责声明：
    本工具仅供学习研究使用，请遵守知乎的使用条款。
    请勿用于商业用途或大规模爬取。
"""

import argparse
import asyncio
import sys

from scraper import login, scrape_user
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        description="知乎用户内容爬虫 — 爬取指定用户的所有回答和文章，保存为 Markdown",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  # 登录知乎（首次使用需要，登录状态会保存）
  python main.py login

  # 爬取某用户的所有回答和文章
  python main.py scrape zhang-jia-wei

  # 只爬取回答，不下载图片
  python main.py scrape zhang-jia-wei --only-answers --no-images

  # 只爬取文章
  python main.py scrape zhang-jia-wei --only-articles

  # 自定义延迟和输出目录
  python main.py scrape zhang-jia-wei --delay-min 8 --delay-max 15 --output ./my_data

  # 使用无头模式（不显示浏览器窗口）
  python main.py scrape zhang-jia-wei --headless

说明:
  user_url_token 是知乎用户个人主页 URL 中的标识符。
  例如 https://www.zhihu.com/people/zhang-jia-wei 中的 "zhang-jia-wei"
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # ── login 子命令 ──
    login_parser = subparsers.add_parser("login", help="登录知乎（手动登录，保存 Cookie）")
    login_parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="等待登录的超时时间（秒），默认 300",
    )

    # ── scrape 子命令 ──
    scrape_parser = subparsers.add_parser("scrape", help="爬取指定用户的回答和文章")
    scrape_parser.add_argument(
        "user_url_token",
        type=str,
        help="知乎用户的 URL token（个人主页 URL 中的标识符）",
    )
    scrape_parser.add_argument(
        "--output", "-o",
        type=str,
        default=None,
        help="输出目录路径（默认: ./output/<user_url_token>/）",
    )
    scrape_parser.add_argument(
        "--only-answers",
        action="store_true",
        help="只爬取回答",
    )
    scrape_parser.add_argument(
        "--only-articles",
        action="store_true",
        help="只爬取文章",
    )
    scrape_parser.add_argument(
        "--no-images",
        action="store_true",
        help="不下载图片",
    )
    scrape_parser.add_argument(
        "--delay-min",
        type=float,
        default=10.0,
        help="请求间最小延迟秒数（默认 5）",
    )
    scrape_parser.add_argument(
        "--delay-max",
        type=float,
        default=20.0,
        help="请求间最大延迟秒数（默认 10）",
    )
    scrape_parser.add_argument(
        "--headless",
        action="store_true",
        help="使用无头模式（不显示浏览器窗口）",
    )

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    if args.command == "login":
        asyncio.run(login(timeout=args.timeout))

    elif args.command == "scrape":
        scrape_answers = True
        scrape_articles = True

        if args.only_answers:
            scrape_articles = False
        if args.only_articles:
            scrape_answers = False

        output_dir = Path(args.output) if args.output else None

        asyncio.run(
            scrape_user(
                user_url_token=args.user_url_token,
                output_dir=output_dir,
                scrape_answers=scrape_answers,
                scrape_articles=scrape_articles,
                download_img=not args.no_images,
                delay_min=args.delay_min,
                delay_max=args.delay_max,
                headless=args.headless,
            )
        )


if __name__ == "__main__":
    main()
