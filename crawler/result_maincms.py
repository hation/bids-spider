"""maincms-web 平台（黑龙江 / 甘肃）结果公告采集器基类。

平台列表接口 selectInfoForIndex 需图片验证码：getVerify 取图 -> DdddOCR 识别 ->
直连接口翻页。招标/中标/废标在同一"采购/信息公告"栏目混排，靠 BaseCrawler._enrich_meta
按标题分类（中标/成交/结果 -> 中标公告；废标/流标/终止 -> 废标公告）。

与主采集器共用 tenders 索引与 href 去重：主采集器已入库的招标公告会被 exists_urls 跳过，
故本采集器只对"新增的结果公告"抓详情，不重复劳动。
"""
import time
from urllib.parse import quote

import requests

from crawler.base_crawler import BaseCrawler, Tender
from utils.captcha import DdddOCR
from utils.log import logger


class MaincmsResultCollector(BaseCrawler):
    site_id = ''
    channel = ''
    list_api = ''
    verify_api = ''
    base_url = ''
    notice_route = ''

    def __init__(self, region, max_pages=50):
        super().__init__(region, max_page_num=None)
        self.max_pages = max_pages
        self.max_items = 5000
        self.ocr = DdddOCR()
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
                           '(KHTML, like Gecko) Chrome/120.0 Safari/537.36'),
            'Referer': f'{self.base_url}/maincms-web/massageListPage',
        })

    def _solve_code(self):
        """取验证码图片并 OCR 识别，返回验证码文本（失败返回空串）。"""
        try:
            img = self.session.get(self.verify_api, timeout=15).content
            return self.ocr.recognize_bytes(img)
        except Exception as e:
            logger.warning(f"[{self.region}]获取验证码失败: {e}")
            return ''

    def _fetch_rows(self, code, page_no):
        """请求一页列表，返回 rows 列表（验证码失效返回 None 表示需重试）。"""
        url = (f"{self.list_api}?siteId={self.site_id}&channel={self.channel}"
               f"&currPage={page_no}&pageSize=10&title=&region=&noticeType={quote('')}"
               f"&cityOrArea=&purchaseManner=&openTenderCode=&purchaser=&agency=&purchaseNature="
               f"&operationStartTime=&operationEndTime=&verifyCode={code}&_t={int(time.time() * 1000)}")
        try:
            resp = self.session.get(url, timeout=20)
            data = resp.json()
        except Exception as e:
            logger.warning(f"[{self.region}]列表请求/解析失败(page {page_no}): {e}")
            return None
        return (data.get('data') or {}).get('rows') or []

    def _crawl(self, context):
        # 首屏验证码（失败重试最多 6 次）
        code = ''
        for attempt in range(6):
            code = self._solve_code()
            if code and self._fetch_rows(code, 1):
                logger.info(f"[{self.region}]验证码 {code!r} 通过")
                break
            logger.warning(f"[{self.region}]验证码 {code!r} 未通过（{attempt + 1}/6）")
            code = ''
        if not code:
            logger.error(f"[{self.region}]验证码多次失败，放弃本轮")
            return

        total = 0
        for page_no in range(1, self.max_pages + 1):
            if total >= self.max_items:
                break
            rows = self._fetch_rows(code, page_no)
            if rows is None:
                # 验证码中途失效：重新识别一次再重试本页
                code = self._solve_code()
                rows = self._fetch_rows(code, page_no) if code else []
            if not rows:
                break
            logger.info(f"[{self.region}]第 {page_no} 页 {len(rows)} 条")
            for record in rows:
                if total >= self.max_items:
                    break
                rid = record.get('id') or ''
                title = (record.get('title') or '').strip()
                if not rid or not title:
                    continue
                href = f"{self.base_url}/maincms-web/{self.notice_route}?type=notice&id={rid}"
                if href in self.exists_urls or href in self.tenders:
                    continue
                html = self._get_detail(context, href)
                tender = Tender(
                    region=self.region,
                    href=href,
                    title=title,
                    release_date=(record.get('noticeTime') or '')[:10],
                    html=html,
                    crawl_date=self._get_crawl_date(),
                )
                self.save_tender_to_es(tender)
                self.tenders[href] = tender
                total += 1
                self._random_sleep(_max=3)
            if len(rows) < 10:
                break
        logger.info(f"[{self.region}]crawl done, new {len(self.tenders)} tenders.")

    def _get_detail(self, context, href):
        try:
            with context.new_page() as detail_page:
                detail_page.goto(href, wait_until='domcontentloaded', timeout=60000)
                detail_page.wait_for_timeout(3000)
                for sel in ('div.articleContent', 'div.innercontent', 'div.u-content'):
                    locator = detail_page.locator(sel).first
                    if locator.count() and locator.inner_text().strip():
                        return locator.inner_html()
                return detail_page.content()
        except Exception as e:
            logger.error(f"[{self.region}]parse detail failed {href}: {e}")
            return ''
