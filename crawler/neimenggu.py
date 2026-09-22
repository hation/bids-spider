from crawler.base_crawler import BaseCrawler, Tender
from utils.log import logger


class NeiMengGu(BaseCrawler):
    def __init__(self):
        super().__init__('neimenggu', max_page_num=None)
        self.max_pages = 50  # 默认抓取前 50 页（或累计 500 条，取先到者）
        self.index_url = 'https://ggzyjy.nmg.gov.cn/jyxx/jyxxss/'
        self.api_url = ('https://ggzyjy.nmg.gov.cn/trssearch/openSearch/searchPublishResource'
                        '?noticeName=&projectCode=&bidSectionCodes=&pageSize=10&pageNum=1'
                        '&noticeTypeName=%E9%87%87%E8%B4%AD%2F%E8%B5%84%E6%A0%BC%E9%A2%84%E5%AE%A1%E5%85%AC%E5%91%8A'
                        '&platformCode=&regionCode=&startTime=&endTime='
                        '&transactionTypeName=%E6%94%BF%E5%BA%9C%E9%87%87%E8%B4%AD&industriesTypeName=')
        self.headers = {}
        self.date_filter = 'url_param'

    def build_list_url(self, page_num):
        """原生日期参数：searchPublishResource 接口支持 startTime/endTime 过滤。"""
        return (
            self.api_url.replace('pageNum=1', f'pageNum={page_num}')
            .replace('startTime=', f'startTime={self._date_start}')
            .replace('endTime=', f'endTime={self._date_end}')
        )

    def _crawl(self, context):
        page = context.new_page()

        def handle_request(request):
            # 复用门户页面自身的列表接口请求头（含 cookie、UA、referer）
            if 'searchPublishResource' in request.url:
                self.headers = {k: v for k, v in request.headers.items() if k.lower() != 'content-length'}

        page.on("request", handle_request)
        try:
            page.goto(self.index_url, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            logger.warning(f"goto {self.index_url} failed: {e}")
        page.wait_for_timeout(5000)  # 等待 iframe 内列表接口请求发出，捕获请求头
        if not self.headers:
            logger.error("未捕获到列表接口请求头，可能页面结构已变化")
            return

        # 分页循环：翻页深度由日期判断决定（guarded_save 遇早于目标日停止）
        for page_num in range(1, self.max_pages + 1):
            api_url = self.api_url.replace('pageNum=1', f'pageNum={page_num}')
            response = context.request.get(api_url, headers=self.headers)
            try:
                data = response.json()
            except Exception as e:
                logger.error(f"get tenders list json failed: {e}")
                break
            if data.get("state") != 200:
                logger.error(f"get tenders list failed: {data}")
                break
            records = (data.get("data", {}) or {}).get("data", []) or []
            logger.info(f"[{self.region}]get {len(records)} tenders list success (page {page_num}).")
            if not records or len(records) < 10:
                break
            for record in records:
                source_data_key = record.get('sourceDataKey') or ''
                if not source_data_key:
                    logger.warning(f"record has no sourceDataKey, skip: {record.get('noticeName')}")
                    continue
                href = 'https://ggzyjy.nmg.gov.cn/jyxx/index_39.html?id=' + source_data_key
                if href in self.exists_urls:
                    continue
                html = self._get_detail(context, href)
                tender = Tender(
                    region=self.region,
                    href=href,
                    title=record.get('noticeName', ''),
                    release_date=(record.get('noticeSendTime') or '')[:10],
                    html=html,
                    crawl_date=self._get_crawl_date(),
                )
                self.save_tender_to_es(tender)
                self.tenders[href] = tender
                logger.info(f"Found tender: {tender.title}")
                self._random_sleep(_max=3)
        logger.info(f"[{self.region}]crawl done, new {len(self.tenders)} tenders.")

    def _get_detail(self, context, href):
        with context.new_page() as detail_page:
            detail_page.goto(href, wait_until="domcontentloaded", timeout=60000)
            detail_page.wait_for_selector("#cont_text", timeout=15000)
            return detail_page.locator("#cont_text").inner_html()


if __name__ == "__main__":
    NeiMengGu().run(increment=True)
