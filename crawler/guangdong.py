import time
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from crawler.base_crawler import BaseCrawler, Tender
from utils.log import logger


class GuangDong(BaseCrawler):
    def __init__(self):
        super().__init__('guangdong')
        self.max_pages = 5  # 默认抓取前 5 页（或累计 100 条，取先到者）
        self.list_url = 'https://gdgpo.czt.gd.gov.cn/maincms-web/noticeInformationGd'
        self.list_api_key = 'gpcms/rest/web/v2/info/selectInfoForIndex'
        self.detail_url = 'https://gdgpo.czt.gd.gov.cn/maincms-web/noticeGd?type=notice&id={}'
        self.list_headers = {}
        self.list_api_url = ''

    def _crawl(self, context):
        page = context.new_page()

        def handle_request(request):
            # 复用列表接口自身的请求头与请求 URL（含 sign/time 等鉴权参数）
            if self.list_api_key in request.url:
                self.list_headers = {k: v for k, v in request.headers.items() if k.lower() != 'content-length'}
                self.list_api_url = request.url

        page.on("request", handle_request)
        page.goto(self.list_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(5000)
        # 先监听列表接口，再点击“查询”按钮触发请求
        try:
            with page.expect_response(
                    lambda r: self.list_api_key in r.url and r.status == 200,
                    timeout=20000) as resp_info:
                page.locator("button:has-text('查询')").first.click()
            response = resp_info.value
            data = response.json()
        except Exception as e:
            logger.error(f"[{self.region}]load tender list failed: {e}")
            return
        rows = (data.get("data") or {}).get("rows") or []
        logger.info(f"[{self.region}]get {len(rows)} tenders list success (page 1).")
        self._get_tenders(context, rows)
        # 分页循环：直接重放接口请求并修改 currPage，默认抓取前 max_pages 页（或累计 100 条，取先到者）
        for curr_page in range(2, self.max_pages + 1):
            if len(self.tenders) >= 100:
                break
            try:
                api_url = self._set_page_param(self.list_api_url, 'currPage', curr_page)
                resp = context.request.get(api_url, headers={**self.list_headers, 'Referer': self.list_url})
                data = resp.json()
            except Exception as e:
                logger.error(f"[{self.region}]load page {curr_page} failed: {e}")
                break
            rows = (data.get("data") or {}).get("rows") or []
            logger.info(f"[{self.region}]get {len(rows)} tenders list success (page {curr_page}).")
            if not rows or len(rows) < 10:
                break
            self._get_tenders(context, rows)
        logger.info(f"[{self.region}]crawl done, new {len(self.tenders)} tenders.")

    @staticmethod
    def _set_page_param(url, key, value):
        """替换 URL 查询串中指定参数的值，用于翻页重放"""
        parsed = urlparse(url)
        qs = parse_qs(parsed.query, keep_blank_values=True)
        qs[key] = [str(value)]
        return urlunparse((
            parsed.scheme, parsed.netloc, parsed.path, parsed.params,
            urlencode(qs, doseq=True), parsed.fragment,
        ))

    def _get_tenders(self, context, rows):
        detail_page = context.new_page()
        try:
            for record in rows:
                record_id = record.get('id')
                if not record_id:
                    continue
                href = self.detail_url.format(record_id)
                if href in self.exists_urls:
                    continue
                title = (record.get('title') or '').strip()
                release_date = record.get('noticeTime') or ''
                html = self._get_detail(detail_page, href)
                tender = Tender(
                    region=self.region,
                    href=href,
                    title=title,
                    release_date=release_date,
                    html=html,
                    crawl_date=self._get_crawl_date(),
                )
                self.save_tender_to_es(tender)
                self.tenders[href] = tender
                self._random_sleep(_max=3)
        finally:
            detail_page.close()

    def _get_detail(self, page, href):
        try:
            page.goto(href, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_selector("div.notice-content", timeout=20000)
            return page.locator("div.notice-content").inner_html()
        except Exception as e:
            logger.error(f"[{self.region}]parse detail failed {href}: {e}")
            return ''


if __name__ == '__main__':
    GuangDong().run()
