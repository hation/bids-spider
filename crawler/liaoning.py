import json

from crawler.base_crawler import BaseCrawler, Tender
from utils.log import logger


class LiaoNing(BaseCrawler):
    def __init__(self, max_records=100):
        super().__init__('liaoning')
        self.max_records = max_records
        self.url = 'http://www.ccgp-liaoning.gov.cn/portalindex?currentKey=pubAnnounce'
        self.api_url = 'http://www.ccgp-liaoning.gov.cn/gateway/esservice/homePage/getHomePunInfoList'
        self.headers = {}
        self.body = {}

    def _crawl(self, context):
        page = context.new_page()

        def handle_request(request):
            # 复用门户页面自身的列表接口请求头（含 fn 签名与 cookie）
            if self.api_url in request.url:
                self.headers = {k: v for k, v in request.headers.items() if k.lower() != 'content-length'}
                try:
                    self.body = json.loads(request.post_data or '{}')
                except Exception:
                    self.body = {}

        page.on("request", handle_request)
        page.goto(self.url, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)  # 等待页面发起列表接口请求，捕获 fn 头
        if not self.headers:
            logger.error("未捕获到列表接口请求头，可能页面结构已变化")
            return
        self._get_tenders(context)

    def _get_tenders(self, context):
        self.body.update({'current': 1, 'rowCount': self.max_records})
        response = context.request.post(self.api_url, data=self.body, headers=self.headers)
        data = response.json()
        if data.get("code") != 200:
            logger.error(f"get tenders list failed: {data}")
            return
        records = data.get("data", {}).get("data", []) or []
        logger.info(f"[{self.region}]get {len(records)} tenders list success.")
        for record in records:
            href = record.get('infoPath') or ''
            if not href:
                logger.warning(f"record has no infoPath, skip: {record.get('title')}")
                continue
            if not href.startswith('http'):
                href = 'http://218.60.151.59:9004/' + href
            if href in self.exists_urls:
                continue
            tender = Tender(
                region=self.region,
                href=href,
                title=record.get('title', ''),
                release_date=record.get('releaseDate', ''),
                html=record.get('content', ''),
                crawl_date=self._get_crawl_date(),
            )
            self.save_tender_to_es(tender)
            self.tenders[href] = tender
            self._random_sleep(_max=3)
        logger.info(f"[{self.region}]crawl done, new {len(self.tenders)} tenders.")


if __name__ == '__main__':
    LiaoNing().run()
