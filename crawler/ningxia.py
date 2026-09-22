from urllib.parse import urljoin

from crawler.base_crawler import BaseCrawler, Tender
from utils.log import logger


class NingXia(BaseCrawler):
    """宁夏回族自治区公共资源交易网：HTML 列表页（tr.com-table-bd-row），正文容器 div.particulars-article。

    分页：#pager 分页组件（a[data-page-index]，0 起），点击"下页 >" JS 异步
    加载。默认抓取前 50 页（或累计 500 条，取先到者）。
    注意：站点接入中国电信网站安全防护（WAF），高频请求可能被 405 拦截，需
    Stealth 伪装 + 控制抓取频率。
    """

    def __init__(self):
        super().__init__('ningxia', max_page_num=None)
        self.index_url = 'https://ggzyjy.fzggw.nx.gov.cn/dzjy/001001/trade_infomation.html'
        self.max_pages = 50  # 最多抓取页数
        self.max_items = 5000  # 最多抓取条数（与 max_pages 取先到者；已放宽，避免到达目标日前截断）

    def _crawl(self, context):
        page = context.new_page()
        page.goto(self.index_url, wait_until='domcontentloaded', timeout=60000)
        page.wait_for_selector('tr.com-table-bd-row', timeout=30000)
        page.wait_for_timeout(1500)
        processed = 0
        for page_no in range(1, self.max_pages + 1):
            if processed >= self.max_items:
                break
            results = self._parse_list(page)
            logger.info(f"[{self.region}] page {page_no} get {len(results)} tenders from list page.")
            for result in results:
                href = result['href']
                if not href.startswith('http'):
                    href = urljoin(page.url, href)
                if href in self.exists_urls or href in self.tenders:
                    continue
                tender = Tender(self.region, href, result['title'], result['date'])
                tender.html = self._execute_by_new_page(context, href, self.parse_detail)
                tender.crawl_date = self._get_crawl_date()
                self.save_tender_to_es(tender)
                self.tenders[href] = tender
                self._random_sleep(_max=3)
                processed += 1
                if processed >= self.max_items:
                    break
            if page_no < self.max_pages and processed < self.max_items:
                if not self._goto_next_page(page, page_no):
                    break

    def _parse_list(self, page):
        return page.evaluate("""() => {
            const out = [];
            for (const tr of document.querySelectorAll('tr.com-table-bd-row')) {
                const tds = tr.querySelectorAll('td');
                if (tds.length < 2) continue;
                const a = tds[0] ? tds[0].querySelector('a[href]') : null;
                if (!a) continue;
                const title = (a.getAttribute('title') || a.innerText || '').trim().replace(/\\s+/g, ' ');
                const date = tds[1] ? tds[1].innerText.trim() : '';
                if (title.length >= 8) out.push({title, href: a.href, date});
            }
            return out;
        }""")

    def _goto_next_page(self, page, current_page):
        """点击下页，等待 #pager active 页码变为 current_page + 1"""
        try:
            page.locator('#pager a', has_text='下页').click()
            page.wait_for_function(
                """expected => {
                    const el = document.querySelector('#pager li.active a');
                    return el && el.textContent.trim() === String(expected);
                }""",
                arg=current_page + 1, timeout=15000,
            )
            page.wait_for_timeout(1000)
            return True
        except Exception as e:
            logger.warning(f"[{self.region}]goto page {current_page + 1} failed: {e}")
            return False

    @staticmethod
    def parse_detail(page):
        return page.locator('div.particulars-article').inner_html()


if __name__ == '__main__':
    NingXia().run()
