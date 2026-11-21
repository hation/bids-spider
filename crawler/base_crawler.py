import os
import random
import re
from dataclasses import dataclass, asdict
from datetime import datetime
import time

import html2text
import pandas as pd
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

from utils.log import logger
from utils.es import ESConnection
from utils.throttle import Throttle, CircuitOpenError

OUTPUT_DIR = "output"
EXCEL_CELL_LIMIT = 32767  # Excel 单格最大字符数，超过会被截断

# Excel 导出统一使用中文列名（内部字段保持英文）
CN_EXPORT_COLS = {
    'region': '地区', 'href': '公告链接', 'title': '商机标题',
    'release_date': '发布日期', 'crawl_date': '抓取日期',
    'html': '商机详情', 'truncated': '详情截断',
    'announce_type': '公告类型', 'win_company': '中标单位', 'win_amount_wan': '中标金额(万元)',
}


class _DateBoundaryReached(Exception):
    """扫描模式下已翻过目标日期范围（列表按日期倒序），提前停止翻页。"""


class _NoDateStop(Exception):
    """扫描模式下连续大量公告无日期（站点列表不展示日期），继续翻页无意义，提前停止。"""


@dataclass
class Tender:
    region: str
    href: str
    title: str
    release_date: str = ''
    html: str = ''
    crawl_date: str = ''
    amount_wan: float = None      # 项目金额（万元），入库时从正文提取
    bid_deadline: str = ''        # 投标截止时间（ISO YYYY-MM-DD[ HH:MM]），入库时从正文提取
    announce_type: str = ''       # 招标公告 / 中标公告 / 废标公告（入库时按标题分类或由结果采集器预设）
    win_company: str = ''         # 中标单位（仅中标公告）
    win_amount_wan: float = None  # 中标金额（万元，仅中标公告）
    void_reason: str = ''         # 废标原因（仅废标公告）


class BaseCrawler:

    def __init__(self, region, max_page_num=None):
        self.region = region
        self.max_page_num = max_page_num
        self.tenders = {}
        self.exists_urls = set()
        self.es_conn = ESConnection()
        self._throttler = Throttle(
            rate_per_min=getattr(self, 'rate_per_min', 30),
            max_fail_streak=getattr(self, 'max_fail_streak', 5),
        )

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
                        '--start-maximized',
                        # 强制直连：macOS 系统代理（xray 10808）失效时 --no-proxy-server 无法覆盖，direct:// 才能绕过
                        '--proxy-server=direct://'
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

    def _throttle(self):
        """请求前限速（令牌桶）。"""
        self._throttler.wait()

    def _with_retry(self, fn):
        """带指数退避的请求包装；连续失败超过阈值抛 CircuitOpenError。"""
        for attempt in range(self._throttler.retries):
            try:
                self._throttle()
                result = fn()
                self._throttler.on_success()
                return result
            except CircuitOpenError:
                raise
            except Exception as e:
                msg = str(e).lower()
                if any(k in msg for k in ('429', '403', 'too many', 'forbidden', 'rate limit')):
                    self._throttler.on_blocked()
                self._throttler.on_fail()
                if attempt == self._throttler.retries - 1:
                    raise
                self._throttler.backoff(attempt)

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
        """HTML 源码转可读纯文本，用于 Excel 导出。

        html2text 转正文后，再兜底剥离混在文本里的残留标签源码（如浙江站点
        内嵌的 <span id=...>），保证导出的是纯正文。
        """
        h = html2text.HTML2Text()
        h.ignore_links = True
        h.ignore_images = True
        h.body_width = 0
        text = h.handle(html or '')
        # 兜底清除残留 HTML 标签（<tagname ...> / </tagname> / <!-- -->）
        text = re.sub(r'<[/!]?[a-zA-Z][^>]*>', '', text)
        return text

    def _enrich_meta(self, tender):
        """入库前从正文提取结构化字段：金额、投标截止时间，并按标题分类公告类型。

        - announce_type 为空时按标题关键词分类（结果采集器可预设，跳过分类）
        - 中标公告额外提取 win_company / win_amount_wan
        - amount_wan 复用 analyze_today 提取逻辑（金额兼容元/万元）
        """
        try:
            text = self._html_to_text(tender.html)
        except Exception:
            text = ''
        try:
            from utils.announce import classify_announce_type, extract_win_company, extract_win_amount, extract_void_reason
            if not tender.announce_type:
                tender.announce_type = classify_announce_type(tender.title)
            # 正文信号兜底：辽宁等站点"结果公告"栏目混入废标结果，标题命中中标但正文
            # 含"结果类型：废标/废标情形/流标"时改为废标公告（避免误归中标且无中标单位）
            if tender.announce_type == '中标公告' and re.search(
                    r'(结果类型[:：]\s*(废标|流标)|废标情形|流标情形)', text):
                tender.announce_type = '废标公告'
            if tender.announce_type == '中标公告':
                if not tender.win_company:
                    tender.win_company = extract_win_company(text)
                if tender.win_amount_wan is None:
                    tender.win_amount_wan = extract_win_amount(text)
            if tender.announce_type == '废标公告' and not tender.void_reason:
                tender.void_reason = extract_void_reason(text)
        except Exception as e:
            logger.warning(f"[{self.region}] 分类/中标信息提取失败: {e}")
        if tender.amount_wan is not None and tender.bid_deadline:
            return  # 已提取过，避免重复
        try:
            from analyze_today import extract_money, extract_timeline
            unit, val = extract_money(text)
            if unit == '万元':
                tender.amount_wan = float(val)
            elif unit == '元':
                tender.amount_wan = float(val) / 10000
            tl = extract_timeline(text)
            tender.bid_deadline = tl.get('投标截止') or ''
        except Exception as e:
            logger.warning(f"[{self.region}] 提取金额/截止时间失败: {e}")

    def save_tender_to_es(self, tender):
        self._enrich_meta(tender)
        logger.info(f"[{self.region}]Save {tender.title} tenders to Elasticsearch.")
        self.es_conn.save_tender(tender)

    def save_tenders_to_es(self):
        if not self.tenders:
            logger.info(f"[{self.region}]Nothing to save.")
            return
        for t in self.tenders.values():
            self._enrich_meta(t)
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
                        # 强制直连：macOS 系统代理（xray 10808）失效时 --no-proxy-server 无法覆盖，direct:// 才能绕过
                        '--proxy-server=direct://'
                    ]
                )
                context = browser.new_context()
                self._crawl_by_date(context)
        except _DateBoundaryReached:
            logger.info(f"[{self.region}]已翻过目标日期范围，提前停止翻页")
        except _NoDateStop:
            logger.info(f"[{self.region}]列表页大量公告无日期，提前停止翻页")
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
                # 站点列表不展示日期时（如 henan），无日期条目不间断累加，
                # 继续翻页也永远到不了目标日，提前停止避免白翻 200 页兜底
                if self._date_no_date >= getattr(self, '_max_no_date_stop', 100):
                    raise _NoDateStop()
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