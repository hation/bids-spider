from crawler.base_crawler import BaseCrawler, Tender
from utils.log import logger


class JiangXi(BaseCrawler):
    """江西省公共资源交易平台-政府采购（DOM 列表，详情取 div.content）
    翻页：点击列表页底部分页器（.pager .m-pagination-page a，data-page-index 为 0-based 页码）。
    """

    def __init__(self):
        super().__init__('jiangxi')
        self.index_url = 'https://www.jxsggzy.cn/jyxx/002006/trade.html'
        self.base_url = 'https://www.jxsggzy.cn'
        self.max_pages = 50  # 默认抓取前 50 页（或累计 500 条，取先到者）

    def _crawl(self, context):
        page = context.new_page()
        page.goto(self.index_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector("li.result-list-item", timeout=30000)
        page.wait_for_timeout(2000)
        tenders = {}
        for page_no in range(self.max_pages):
            page.wait_for_timeout(1000)
            for href, tender in self.get_one_page_titles(page).items():
                if href not in tenders:
                    tenders[href] = tender
            if len(tenders) >= 100:
                break
            # 翻页：点击下一页页码（mricode.pagination，data-page-index 为 0-based 页码）
            next_idx = page_no + 1
            next_link = page.locator(f'.pager .m-pagination-page a[data-page-index="{next_idx}"]')
            if not next_link.count():
                break
            # 记录点击前当前页首行标题，用于等待新页数据渲染
            prev_first_title = ''
            first_a = page.locator("li.result-list-item div.clearfix > a.l").first
            if first_a.count():
                prev_first_title = (first_a.inner_text() or '').strip()
            next_link.first.click(timeout=10000)
            try:
                page.wait_for_function(
                    """([sel, prev]) => {
                        const el = document.querySelector(sel);
                        if (!el) return false;
                        const t = (el.innerText || '').trim();
                        return !!t && t !== prev;
                    }""",
                    arg=["li.result-list-item div.clearfix > a.l", prev_first_title],
                    timeout=30000,
                )
            except Exception:
                break
        logger.info(f"[{self.region}]get {len(tenders)} tenders list success.")
        for href, tender in tenders.items():
            if href in self.exists_urls:
                continue
            page.goto(href, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_selector("div.content", timeout=30000)
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
        items = page.locator("li.result-list-item").all()
        for item in items:
            a_tag = item.locator("div.clearfix > a.l")
            href = a_tag.get_attribute("href") or ''
            title = a_tag.inner_text().strip()
            date_text = item.locator("span.result-date.r").inner_text().strip()
            if not href or not title:
                logger.warning(f"item missing href or title, skip: {title}")
                continue
            if href.startswith('/'):
                href = self.base_url + href
            tenders[href] = Tender(self.region, href, title, date_text)
        return tenders

    @staticmethod
    def parse_detail(page):
        return page.locator("div.content").inner_html()


if __name__ == '__main__':
    JiangXi().run()
