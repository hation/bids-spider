import os
import random
from dataclasses import dataclass, asdict
from datetime import datetime
import time

import html2text
import pandas as pd
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

from utils.log import logger
from utils.es import ESConnection

OUTPUT_DIR = "output"
EXCEL_CELL_LIMIT = 32767  # Excel 单格最大字符数，超过会被截断

# Excel 导出统一使用中文列名（内部字段保持英文）
CN_EXPORT_COLS = {
    'region': '地区', 'href': '公告链接', 'title': '商机标题',
    'release_date': '发布日期', 'crawl_date': '抓取日期',
    'html': '商机详情', 'truncated': '详情截断',
}


class _DateBoundaryReached(Exception):
    """扫描模式下已翻过目标日期范围（列表按日期倒序），提前停止翻页。"""


@dataclass
class Tender:
    region: str
    href: str
    title: str
    release_date: str = ''
    html: str = ''
    crawl_date: str = ''


class BaseCrawler:

    def __init__(self, region, max_page_num=None):
        self.region = region
        self.max_page_num = max_page_num
        self.tenders = {}
        self.exists_urls = set()
        self.es_conn = ESConnection()

    def run(self, increment=True):
        self.exists_urls = self.get_exists_url_from_es()
        try:
            with Stealth().use_sync(sync_playwright()) as p:
                # 创建真正的浏览器实例，使用全局初始化脚本
                browser = p.chromium.launch(
                    headless=False,
                    args=[
                        '--no-sandbox',
                        '--disable-setuid-sandbox',
                        '--disable-blink-features=AutomationControlled',
                        '--start-maximized'
                    ]
                )
                context = browser.new_context()
                if increment:
                    self._crawl(context)
                else:
                    self._crawl_history(context)
        finally:
            ...
            self.save_tenders_to_es()
            self.save_tenders_to_excel()

    def _crawl(self, context):
        ...

    def _crawl_history(self, context):
        ...

    def _get_crawl_date(self):
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _execute_by_new_page(self, context, url, func, *args, **kwargs):
        with context.new_page() as page:
            logger.info(f"[{self.region}]start to goto: {url}")
            page.goto(url, wait_until="domcontentloaded")
            return func(page, *args, **kwargs)

    @staticmethod
    def _random_sleep(_min=1, _max=60):
        sleep_seconds = random.uniform(_min, _max)
        time.sleep(sleep_seconds)

    def save_tenders_to_excel(self):
        if not self.tenders:
            logger.info(f"[{self.region}]Nothing to save.")
            return
        data = [asdict(tender) for tender in self.tenders.values()]
        for record in data:
            record['html'] = self._html_to_text(record.get('html', ''))
            record['truncated'] = '是' if len(record['html']) > EXCEL_CELL_LIMIT else '否'
        # 清洗所有字段中的 XML 非法控制字符，避免 openpyxl 写入报错
        _illegal = __import__('re').compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')
        for record in data:
            for k, v in record.items():
                if isinstance(v, str):
                    record[k] = _illegal.sub('', v)
        file_name = getattr(self, '_excel_name', None) or (
            f"{self.region}_"
            f"{str(datetime.now()).replace(' ', '_').replace('-', '_').replace(':', '_').replace('.', '_')}.xlsx"
        )
        excel_dir = getattr(self, '_excel_dir', None) or OUTPUT_DIR
        os.makedirs(excel_dir, exist_ok=True)
        file_path = os.path.join(excel_dir, file_name)
        logger.info(f"[{self.region}]Save tenders to {file_path}")
        df = pd.DataFrame(data).rename(columns={c: e for c, e in CN_EXPORT_COLS.items() if c in data[0]})
        df.to_excel(file_path, index=False)

    @staticmethod
    def _html_to_text(html):
        """HTML 源码转可读纯文本，用于 Excel 导出"""
        h = html2text.HTML2Text()
        h.ignore_links = True
        h.ignore_images = True
        h.body_width = 0
        return h.handle(html or '')

    def save_tender_to_es(self, tender):
        logger.info(f"[{self.region}]Save {tender.title} tenders to Elasticsearch.")
        self.es_conn.save_tender(tender)

    def save_tenders_to_es(self):
        if not self.tenders:
            logger.info(f"[{self.region}]Nothing to save.")
            return
        logger.info(f"[{self.region}]Save {len(self.tenders)} tenders to Elasticsearch.")
        self.es_conn.save_tenders_bulk(self.tenders.values())

    def get_exists_url_from_es(self):
        query_body = {
            "query": {
                "term": {
                    "region": self.region
                }
            },
            "sort": [
                {
                    "release_date": {
                        "order": "desc"  # 按 release_date 降序排列
                    }
                }
            ],
            "_source": False,  # 只返回 href 字段，减少数据传输
            "size": 10000  # 调整返回结果数量，根据你的数据量设置
        }
        data = self.es_conn.search_data(query_body) or set()
        return set(i['_id'] for i in data)

    def run_by_date(self, start_date, end_date=None):
        """按日期抓取：只保留 release_date 落在 [start_date, end_date] 的公告。

        - 声明了 date_filter='url_param' 的站点：列表请求直接带日期参数，精准定位。
        - 未声明（扫描模式）：复用 _crawl 翻页，遇到早于 start_date 的公告提前停止。
        """
        self._date_start = start_date
        self._date_end = end_date or start_date
        self._date_no_date = 0
        self._excel_name = f"date_{start_date}_{self.region}.xlsx"
        self._excel_dir = os.path.join(OUTPUT_DIR, start_date)  # 单地区 Excel 也按日期归档
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
                self._crawl_by_date(context)
        except _DateBoundaryReached:
            logger.info(f"[{self.region}]已翻过目标日期范围，提前停止翻页")
        finally:
            # 先入库全部（含补爬时顺带收录的今天公告，供今日查询直接复用）
            self.save_tenders_to_es()
            # 再严格过滤到目标日期范围，用于 Excel 导出（Excel 仍只含目标日）
            self._filter_tenders_by_date()
            self.save_tenders_to_excel()

    def _crawl_by_date(self, context):
        """日期模式入口：安装日期过滤守卫后复用各爬虫的 _crawl。"""
        if getattr(self, 'date_filter', None) == 'url_param' and hasattr(self, 'build_list_url'):
            # 原生日期参数站点：让列表请求直接带上日期范围（保留 pageNum=1 供 _crawl 分页替换）
            base_url = self.build_list_url(1)
            for attr in ('api_url', 'list_url'):
                if hasattr(self, attr):
                    setattr(self, attr, base_url)
        else:
            # 扫描模式：翻页深度由日期判断决定（guarded_save 遇早于目标日即抛异常停止），
            # 页数仅作防死循环兜底（200 页），不再用 8 页硬截断目标日
            self.max_pages = min(getattr(self, 'max_pages', 50), 200)

        orig_save = self.save_tender_to_es

        def guarded_save(tender):
            d = (tender.release_date or '').strip()[:10]
            if not d:
                self._date_no_date += 1
                return
            if d < self._date_start:  # 列表按日期倒序，遇到更早的即翻过了目标日期
                raise _DateBoundaryReached()
            # 目标日及之后的公告都入库（补爬时顺带收录今天发布的公告，供今日查询直接复用）
            orig_save(tender)

        self.save_tender_to_es = guarded_save
        self._crawl(context)
        self.save_tender_to_es = orig_save

    def _filter_tenders_by_date(self):
        """严格过滤兜底：只保留日期范围内的公告（批量入库/Excel 导出前调用）。"""
        self.tenders = {
            href: t for href, t in self.tenders.items()
            if self._date_start <= (t.release_date or '').strip()[:10] <= self._date_end
        }


if __name__ == '__main__':
    a = Tender("1", "2", "3")
    print(asdict(a))