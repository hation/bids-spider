"""快速爬取脚本：无头模式 + 短随机延时（不修改现有爬虫代码）

用法：
    python fast_run.py <region>     # 爬取单个地区，如 tianjin / jiangsu
    python fast_run.py all          # 依次爬取全部已支持地区
    python fast_run.py              # 默认 tianjin

原理：运行时在内存中给 BaseCrawler 打补丁（覆盖 run 与 _random_sleep），
不触碰任何现有文件；已入库的数据会按 href 自动跳过。
"""
import random
import sys
import time

from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

from crawler.base_crawler import BaseCrawler

# region 名称 -> (模块名, 类名)
CRAWLERS = {
    'beijing': ('beijing', 'BeiJing'),
    'tianjin': ('tianjin', 'TianJin'),
    'hebei': ('hebei', 'HeBei'),
    'liaoning': ('liaoning', 'LiaoNing'),
    'jilin': ('jilin', 'JiLin'),
    'neimenggu': ('neimenggu', 'NeiMengGu'),
    'shanxi': ('shanxi', 'ShanXi'),
    'jiangsu': ('jiangsu', 'JiangSu'),
    'zhejiang': ('zhejiang', 'ZheJiang'),
    'anhui': ('anhui', 'AnHui'),
    'fujian': ('fujian', 'FuJian'),
    'shandong': ('shandong', 'ShanDong'),
    'jiangxi': ('jiangxi', 'JiangXi'),
    'hubei': ('hubei', 'HuBei'),
    'hunan': ('hunan', 'HuNan'),
    'guangdong': ('guangdong', 'GuangDong'),
    'hainan': ('hainan', 'HaiNan'),
    'chongqing': ('chongqing', 'ChongQing'),
    'sichuan': ('sichuan', 'SiChuan'),
    'guizhou': ('guizhou', 'GuiZhou'),
    'yunnan': ('yunnan', 'YunNan'),
    'qinghai': ('qinghai', 'QingHai'),
    'ningxia': ('ningxia', 'NingXia'),
    'xinjiang': ('xinjiang', 'XinJiang'),
    'guangxi': ('guangxi', 'GuangXi'),
}


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


def run_region(region):
    module_name, class_name = CRAWLERS[region]
    module = __import__(f'crawler.{module_name}', fromlist=[class_name])
    crawler_class = getattr(module, class_name)
    crawler_class().run()


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else 'tianjin'
    if arg == 'all':
        for region in CRAWLERS:
            print(f'\n===== 开始爬取 {region} =====')
            try:
                run_region(region)
            except Exception as e:
                print(f'[ERROR] {region} 爬取失败: {e}')
                continue
    elif arg in CRAWLERS:
        run_region(arg)
    else:
        print(f'unknown region: {arg}')
        print('支持:', ', '.join(CRAWLERS))
        sys.exit(1)


if __name__ == '__main__':
    main()
