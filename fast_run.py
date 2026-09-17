"""快速爬取脚本：无头模式 + 短随机延时（不修改现有爬虫代码）

用法：
    python fast_run.py tianjin
    python fast_run.py hebei
    python fast_run.py beijing

原理：运行时在内存中给 BaseCrawler 打补丁（覆盖 run 与 _random_sleep），
不触碰任何现有文件；已入库的数据会按 href 自动跳过。
"""
import random
import sys
import time

from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

from crawler.base_crawler import BaseCrawler


def fast_sleep(_min=1, _max=60):
    """覆盖原随机延时（1-60s）为固定 1-3s"""
    time.sleep(random.uniform(1, 3))


def fast_run(self, increment=True):
    """覆盖 BaseCrawler.run：headless=True 启动浏览器"""
    self.exists_urls = self.get_exists_url_from_es()
    try:
        with Stealth().use_sync(sync_playwright()) as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-blink-features=AutomationControlled',
                ]
            )
            context = browser.new_context()
            if increment:
                self._crawl(context)
            else:
                self._crawl_history(context)
    finally:
        self.save_tenders_to_es()
        self.save_tenders_to_excel()


# 内存打补丁，不改动任何现有文件
BaseCrawler.run = fast_run
BaseCrawler._random_sleep = staticmethod(fast_sleep)


def main():
    region = sys.argv[1] if len(sys.argv) > 1 else 'tianjin'
    if region == 'tianjin':
        from crawler.tianjin import TianJin
        TianJin().run()
    elif region == 'hebei':
        from crawler.hebei import HeBei
        HeBei().run()
    elif region == 'beijing':
        from crawler.beijing import BeiJing
        BeiJing().run()
    else:
        print(f'unknown region: {region}')
        sys.exit(1)


if __name__ == '__main__':
    main()
