from crawler.base_crawler import BaseCrawler, Tender
from utils.log import logger


class ChongQing(BaseCrawler):
    """重庆市公共资源交易网（Nuxt SSR 列表，正文 div.app-detail）

    分页：URL 参数 pageNum=N 直接跳转翻页。默认抓取前 5 页（或累计
    100 条，取先到者）。
    """

    def __init__(self):
        super().__init__('chongqing')
        self.list_url = 'https://www.cqggzy.com/trade/014005?categoryNum=014005001'
        self.base_url = 'https://www.cqggzy.com'
        self.max_pages = 50  # 最多抓取页数
        self.max_items = 5000  # 最多抓取条数（与 max_pages 取先到者；已放宽，避免到达目标日前截断）

    def _crawl(self, context):
        page = context.new_page()
        processed = 0
        for page_no in range(1, self.max_pages + 1):
            if processed >= self.max_items:
                break
            url = self.list_url if page_no == 1 else f"{self.list_url}&pageNum={page_no}"
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_selector("ul.min-h-80 li", timeout=30000)
            page.wait_for_timeout(2000)
            results = self._parse_list(page)
            logger.info(f"[{self.region}]page {page_no} get {len(results)} tenders list success.")
            for result in results:
                href = result['href']
                if href.startswith('/'):
                    href = self.base_url + href
                if href in self.exists_urls or href in self.tenders:
                    continue
                html = self._get_detail(page, href)
                tender = Tender(
                    region=self.region,
                    href=href,
                    title=result['title'],
                    release_date=result['date'],
                    html=html,
                    crawl_date=self._get_crawl_date(),
                )
                self.save_tender_to_es(tender)
                self.tenders[href] = tender
                self._random_sleep(_max=3)
                processed += 1
                if processed >= self.max_items:
                    break
        logger.info(f"[{self.region}]crawl done, new {len(self.tenders)} tenders.")

    def _parse_list(self, page):
        return page.evaluate("""() => {
            const out = [];
            document.querySelectorAll('ul.min-h-80 li').forEach(li => {
                const a = li.querySelector('a');
                const href = a ? a.href : '';
                const title = a ? (a.innerText || '').trim() : '';
                // 日期为 li 下直接文本节点
                let date = '';
                li.childNodes.forEach(n => {
                    if (n.nodeType === 3) {
                        const t = (n.textContent || '').trim();
                        if (t && /\\d{4}-\\d{2}-\\d{2}/.test(t)) date = t;
                    }
                });
                if (href && title) out.push({href, title, date});
            });
            return out;
        }""")

    def _get_detail(self, page, href):
        try:
            page.goto(href, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_selector("div.app-detail", timeout=20000)
            return page.locator("div.app-detail").inner_html()
        except Exception as e:
            logger.error(f"[{self.region}]parse detail failed {href}: {e}")
            return ''


if __name__ == '__main__':
    ChongQing().run()
