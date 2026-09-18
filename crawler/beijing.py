from datetime import datetime

from crawler.base_crawler import Tender, BaseCrawler
from utils.log import logger


class BeiJing(BaseCrawler):
    def __init__(self, max_pages=50):
        super().__init__("beijing", max_page_num=140)
        self.max_pages = max_pages
        self.page_url = "http://www.ccgp-beijing.gov.cn/xxgg/sjxxgg/A002004001index_{}.htm"

    def _crawl(self, context):
        for i in range(1, self.max_pages + 1):
            self._crawl_one_page(context, self.page_url.format(i))

    def _crawl_history(self, context):
        for i in range(1, self.max_page_num + 1):
            self._crawl_one_page(context, self.page_url.format(i))

    def _crawl_one_page(self, context, page_url):
        logger.info(f"start to crawl: {page_url}")
        tenders = self._execute_by_new_page(context, page_url, self.get_one_page_titles)
        for url, tender in tenders.items():
            if url in self.exists_urls or url in self.tenders:
                continue
            try:
                tender.html = self._execute_by_new_page(context, url, self.parse_detail)
            except Exception as e:
                logger.error(f"[{self.region}]parse detail failed {url}: {e}")
            tender.crawl_date = self._get_crawl_date()
            self.tenders[url] = tender
            self._random_sleep(_max=30)

    def get_one_page_titles(self, page):
        li_elements = page.locator("li:has(a):has(span.datetime)").all()
        if not li_elements:
            logger.warning("No details found.")
            return {}
        tenders = {}
        # 4. 循环解析每个<li>的href、text、日期
        for li in li_elements:
            # 4.1 提取<a>标签的href和文本
            a_tag = li.locator("a")
            # 提取href（补全为完整URL，避免相对路径）
            href = a_tag.get_attribute("href")
            href_full = f"http:{href}" if href.startswith("//") else href
            # 提取<a>标签的文本（去除首尾空白）
            a_text = a_tag.inner_text()
            # 4.2 提取<span.datetime>的日期文本
            date_text = li.locator("span.datetime").inner_text()
            tender = Tender(self.region, href_full, a_text, date_text)
            tenders[href_full] = tender
            logger.info(f"Found tender: {tender}")
        return tenders

    @staticmethod
    def parse_detail(page):
        return page.locator(".mainTextBox").inner_html()


if __name__ == "__main__":
    BeiJing().run(increment=True)
