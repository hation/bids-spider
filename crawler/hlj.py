import re
import time

from crawler.base_crawler import BaseCrawler, Tender
from utils.captcha import DdddOCR
from utils.log import logger


class HeiLongJiang(BaseCrawler):
    """黑龙江省采购网（maincms-web 平台）：列表需先通过图片验证码。

    流程：加载列表页 -> ddddocr 识别验证码 -> 填入并点击"查询"，
    从页面自身的 selectInfoForIndex 请求中捕获 siteId/channel/verifyCode，
    随后直连接口翻页（currPage）抓取多页。
    """

    def __init__(self, max_pages=50):
        super().__init__('hlj', max_page_num=None)
        self.max_pages = max_pages
        self.max_items = 5000
        self.list_url = 'https://hljcg.hlj.gov.cn/maincms-web/massageListPageHlj'
        self.verify_api = 'https://hljcg.hlj.gov.cn/gpcms/rest/web/v2/index/getVerify'
        self.list_api = 'https://hljcg.hlj.gov.cn/gpcms/rest/web/v2/info/selectInfoForIndex'
        self.base_url = 'https://hljcg.hlj.gov.cn'
        self.notice_route = 'noticeHlj'
        self.ocr = DdddOCR()

    def _crawl(self, context):
        page = context.new_page()
        page.goto(self.list_url, wait_until='domcontentloaded', timeout=60000)
        page.wait_for_timeout(4000)

        code, query = self._solve_and_fetch_first_page(context, page)
        page.close()
        if not query:
            logger.error(f"[{self.region}]验证码多次尝试后仍无法获取列表")
            return

        self._get_tenders(context, code, query)

    def _solve_and_fetch_first_page(self, context, page):
        """识别验证码并点击查询，返回 (verifyCode, 接口查询参数dict)；失败返回 (None, {})"""
        for attempt in range(6):
            try:
                img = context.request.get(self.verify_api).body()
            except Exception as e:
                logger.warning(f"[{self.region}]获取验证码图片失败: {e}")
                continue
            code = self.ocr.recognize_bytes(img)
            page.fill('#verifycode', code)
            captured = {}

            def on_request(req):
                if self.list_api in req.url:
                    captured['url'] = req.url

            page.on('request', on_request)
            try:
                with page.expect_response(
                        lambda r: self.list_api in r.url and r.status == 200, timeout=15000) as resp_info:
                    page.locator("button:has-text('查询')").first.click()
                rows = ((resp_info.value.json().get('data') or {}).get('rows')) or []
            except Exception:
                rows = []
            page.remove_listener('request', on_request)

            if rows:
                query = self._parse_query(captured.get('url', ''))
                logger.info(f"[{self.region}]验证码 {code!r} 通过，第1页 {len(rows)} 条")
                return code, query
            logger.warning(f"[{self.region}]验证码 {code!r} 未通过（尝试 {attempt + 1}/6）")
        return None, {}

    @staticmethod
    def _parse_query(url):
        query = {}
        for pair in url.split('?', 1)[-1].split('&'):
            if '=' in pair:
                k, _, v = pair.partition('=')
                query[k] = v
        return query

    def _get_tenders(self, context, code, query):
        total = 0
        for page_no in range(1, self.max_pages + 1):
            if total >= self.max_items:
                break
            url = (f"{self.list_api}?siteId={query.get('siteId', '')}"
                   f"&channel={query.get('channel', '')}"
                   f"&currPage={page_no}&pageSize=10"
                   f"&title=&region=&noticeType=&cityOrArea=&purchaseManner="
                   f"&openTenderCode=&purchaser=&agency=&purchaseNature="
                   f"&operationStartTime=&operationEndTime="
                   f"&verifyCode={code}&_t={int(time.time() * 1000)}")
            resp = context.request.get(url, headers={'Referer': self.list_url})
            data = resp.json()
            rows = (data.get('data') or {}).get('rows') or []
            if not rows:
                break
            logger.info(f"[{self.region}]第 {page_no} 页 {len(rows)} 条")
            for record in rows:
                if total >= self.max_items:
                    break
                record_id = record.get('id') or ''
                title = (record.get('title') or '').strip()
                if not record_id or not title:
                    continue
                href = f"{self.base_url}/maincms-web/{self.notice_route}?type=notice&id={record_id}"
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


if __name__ == '__main__':
    HeiLongJiang().run()
