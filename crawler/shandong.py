import json

from crawler.base_crawler import BaseCrawler, Tender
from utils.log import logger


class ShanDong(BaseCrawler):
    """山东省政府采购信息公开平台（SPA，列表走 JSON 接口，详情取 div.site-content）"""

    def __init__(self):
        super().__init__('shandong')
        self.max_pages = 5  # 默认抓取前 5 页（或累计 100 条，取先到者）
        self.index_url = 'http://www.ccgp-shandong.gov.cn/#/projectInformation/0'
        self.api_url = 'https://www.ccgp-shandong.gov.cn:8087/api/website/site/getListByCode'
        self.headers = {
            'Content-Type': 'application/json;charset=UTF-8',
            'Referer': 'https://www.ccgp-shandong.gov.cn/',
            'Accept': 'application/json, text/plain, */*',
        }
        # colCode=0301 为项目信息下的省级采购公告
        self.body = {
            'colCode': '0301',
            'area': '370000',
            'currentPage': 1,
            'pageSize': 20,
            'homePage': 1,
            'mergeType': 0,
            'cityType': 1,
        }

    def _crawl(self, context):
        page = context.new_page()
        page.goto(self.index_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector("div.list-item", timeout=30000)  # 等待 SPA 异步渲染
        page.wait_for_timeout(2000)
        # 分页循环：默认抓取前 max_pages 页（或累计 100 条，取先到者）
        for page_num in range(1, self.max_pages + 1):
            if len(self.tenders) >= 100:
                break
            self.body['currentPage'] = page_num
            records_count, page_size, tenders = self.get_one_page_titles(context)
            logger.info(f"[{self.region}]get {len(tenders)} tenders list success (page {page_num}).")
            if not tenders or records_count < page_size:
                break
            for href, tender in tenders.items():
                if href in self.exists_urls:
                    continue
                page.goto(href, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_selector("div.site-content", timeout=30000)
                tender.html = self.parse_detail(page)
                tender.crawl_date = self._get_crawl_date()
                self.save_tender_to_es(tender)
                self.tenders[href] = tender
                self._random_sleep(_max=3)
        page.close()
        logger.info(f"[{self.region}]crawl done, new {len(self.tenders)} tenders.")

    def get_one_page_titles(self, context):
        """调用列表 JSON 接口，返回 (records_count, page_size, {href: Tender})"""
        response = context.request.post(self.api_url, data=json.dumps(self.body), headers=self.headers)
        data = response.json()
        inner = (data.get('data') or {}).get('data') or {}
        records = inner.get('records') or []
        # 实际每页条数以接口返回的 size 为准（请求 pageSize 可能被服务端调整）
        page_size = inner.get('size') or self.body['pageSize']
        tenders = {}
        for record in records:
            info_id = record.get('id') or ''
            title = (record.get('title') or '').strip()
            if not info_id or not title:
                logger.warning(f"record missing id or title, skip: {title}")
                continue
            href = (
                f"https://www.ccgp-shandong.gov.cn/detail?id={info_id}"
                f"&colCode={record.get('colCode', '0301')}&urlType=site&oldData={record.get('oldData', 0)}"
            )
            tenders[href] = Tender(self.region, href, title, record.get('date', ''))
        return len(records), page_size, tenders

    @staticmethod
    def parse_detail(page):
        return page.locator("div.site-content").inner_html()


if __name__ == '__main__':
    ShanDong().run()
