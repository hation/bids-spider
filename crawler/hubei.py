from crawler.base_crawler import BaseCrawler, Tender
from utils.log import logger


class HuBei(BaseCrawler):
    """中国湖北政府采购网-招标(采购)公告（DOM 列表，详情取 div.art_con）
    翻页：该站为服务端渲染的静态列表页，分页通过 URL index_N.html 翻页（N 从 1 开始）。
    """

    def __init__(self):
        super().__init__('hubei')
        self.index_url = 'https://www.ccgp-hubei.gov.cn/notice/cggg/pzbgg/index_1.html'
        self.base_url = 'https://www.ccgp-hubei.gov.cn'
        self.max_pages = 50  # 默认抓取前 50 页（或累计 500 条，取先到者）

    def _crawl(self, context):
        page = context.new_page()
        page.goto(self.index_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector("li:has(> a):has(> span)", timeout=30000)
        page.wait_for_timeout(1000)
        tenders = {}
        for page_no in range(self.max_pages):
            page.wait_for_timeout(800)
            for href, tender in self.get_one_page_titles(page).items():
                if href not in tenders:
                    tenders[href] = tender
            # 翻页：URL index_N.html（N 为 1-based），导航后等待列表重新渲染
            next_page = page_no + 2
            next_url = f"{self.base_url}/notice/cggg/pzbgg/index_{next_page}.html"
            if next_url == page.url:
                break
            page.goto(next_url, wait_until="domcontentloaded", timeout=60000)
            try:
                page.wait_for_selector("li:has(> a):has(> span)", timeout=30000)
            except Exception:
                break
            page.wait_for_timeout(1000)
        logger.info(f"[{self.region}]get {len(tenders)} tenders list success.")
        for href, tender in tenders.items():
            if href in self.exists_urls:
                continue
            page.goto(href, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_selector("div.art_con", timeout=30000)
            tender.html = self.parse_detail(page)
            tender.crawl_date = self._get_crawl_date()
            self.save_tender_to_es(tender)
            self.tenders[href] = tender
            self._random_sleep(_max=3)
        page.close()
        logger.info(f"[{self.region}]crawl done, new {len(self.tenders)} tenders.")

    def get_one_page_titles(self, page):
        """解析列表页一页公告，返回 {href: Tender}"""
        tenders = {}
        items = page.locator("li:has(> a):has(> span)").all()
        for item in items:
            a_tag = item.locator("> a")
            href = a_tag.get_attribute("href") or ''
            title = a_tag.inner_text().strip()
            date_text = item.locator("> span").inner_text().strip()
            if not href or not title:
                logger.warning(f"item missing href or title, skip: {title}")
                continue
            if href.startswith('/'):
                href = self.base_url + href
            tenders[href] = Tender(self.region, href, title, date_text)
        return tenders

    @staticmethod
    def parse_detail(page):
        return page.locator("div.art_con").inner_html()


if __name__ == '__main__':
    HuBei().run()
