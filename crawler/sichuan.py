import json

from crawler.base_crawler import BaseCrawler, Tender
from utils.log import logger


class SiChuan(BaseCrawler):
    def __init__(self):
        super().__init__('sichuan')
        self.max_pages = 5  # 默认抓取前 5 页（或累计 100 条，取先到者）
        self.list_url = 'https://ggzyjy.sc.gov.cn/jyxx/transactionInfo.html'
        self.api_url = 'https://ggzyjy.sc.gov.cn/inteligentsearch/rest/esinteligentsearch/getFullTextDataNew'
        self.base_url = 'https://ggzyjy.sc.gov.cn'
        self.headers = {}
        self.body = {}

    def _crawl(self, context):
        page = context.new_page()

        def handle_request(request):
            # 复用列表页自身的接口请求头与请求体
            if self.api_url in request.url:
                self.headers = {k: v for k, v in request.headers.items() if k.lower() != 'content-length'}
                try:
                    self.body = json.loads(request.post_data or '{}')
                except Exception:
                    self.body = {}

        page.on("request", handle_request)
        page.goto(self.list_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(8000)  # 等待页面发起列表接口请求
        if not self.body:
            logger.error("未捕获到列表接口请求头，可能页面结构已变化")
            return
        self._get_tenders(context, page)

    def _get_tenders(self, context, page):
        # 分页循环：pn 为偏移量（步长=rn），默认抓取前 max_pages 页（或累计 100 条，取先到者）
        for i in range(self.max_pages):
            if len(self.tenders) >= 100:
                break
            pn = i * 12
            self.body.update({'pn': pn, 'rn': 12})
            response = context.request.post(self.api_url, data=json.dumps(self.body), headers=self.headers)
            data = response.json()
            records = ((data.get("result") or {}).get("records")) or []
            logger.info(f"[{self.region}]get {len(records)} tenders list success (page {i + 1}).")
            if not records or len(records) < 12:
                break
            for record in records:
                href = record.get('linkurl') or ''
                if not href:
                    continue
                if href.startswith('/'):
                    href = self.base_url + href
                if href in self.exists_urls:
                    continue
                title = (record.get('title') or '').strip()
                release_date = record.get('webdate') or ''
                html = self._get_detail(page, href)
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
        logger.info(f"[{self.region}]crawl done, new {len(self.tenders)} tenders.")

    def _get_detail(self, page, href):
        try:
            page.goto(href, wait_until="domcontentloaded", timeout=60000)
            # 不同业务类型详情页结构不同（#noticeArea / 通用正文 #newsText），取公共正文容器 #newsText
            page.wait_for_selector("#newsText", state="attached", timeout=20000)
            return page.locator("#newsText").first.inner_html()
        except Exception as e:
            logger.error(f"[{self.region}]parse detail failed {href}: {e}")
            return ''


if __name__ == '__main__':
    SiChuan().run()
