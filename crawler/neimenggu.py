from crawler.base_crawler import BaseCrawler, Tender
from utils.log import logger
import requests


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
        self.headers = {
            'User-Agent': ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
                           '(KHTML, like Gecko) Chrome/120.0 Safari/537.36'),
            'Referer': self.index_url,
            'Accept': 'application/json, text/plain, */*',
        }
        self.date_filter = 'url_param'

    def build_list_url(self, page_num):
        """原生日期参数：searchPublishResource 接口要求日期带时分秒（YYYY-MM-DD HH:MM:SS），
        纯日期（YYYY-MM-DD）会返回 state=500「系统错误」（2026-11-20 实测）。"""
        return (
            self.api_url.replace('pageNum=1', f'pageNum={page_num}')
            .replace('startTime=', f'startTime={self._date_start} 00:00:00')
            .replace('endTime=', f'endTime={self._date_end} 23:59:59')
        )

    def _crawl(self, context):
        # 2026-11-20 改版：列表页源码已无 searchPublishResource 引用，页面触发式捕获请求头失效；
        # 接口本身仍可用。Playwright APIRequestContext 走浏览器网络栈返回 500（疑受代理影响），
        # 改用 requests 直连（curl 实测带 Referer/UA 即 200），不依赖页面。
        for page_num in range(1, self.max_pages + 1):
            api_url = self.api_url.replace('pageNum=1', f'pageNum={page_num}')
            try:
                response = requests.get(api_url, headers=self.headers, timeout=30)
                data = response.json()
            except Exception as e:
                logger.error(f"get tenders list json failed: {e}")
                break
            if data.get("state") != 200:
                logger.error(f"get tenders list failed: {data}")
                break
            records = (data.get("data", {}) or {}).get("data", []) or []
            logger.info(f"[{self.region}]get {len(records)} tenders list success (page {page_num}).")
            if not records:
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
