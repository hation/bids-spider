from crawler.base_crawler import BaseCrawler, Tender
from utils.log import logger


class JiLin(BaseCrawler):
    """吉林省公共资源交易中心政府采购公告（DOM 列表，正文 #detailCnt）。

    分页：#JgcxPage 分页组件，点击 a#nextPage JS 异步加载。默认抓取前
    5 页（或累计 100 条，取先到者）。
    """

    def __init__(self):
        super().__init__('jilin', max_page_num=None)
        self.page_url = 'http://www.ggzyzx.jl.gov.cn/jyxx/zfcg/'
        self.max_pages = 50  # 最多抓取页数
        self.max_items = 5000  # 最多抓取条数（与 max_pages 取先到者；已放宽，避免到达目标日前截断）

    def _crawl(self, context):
        page = context.new_page()
        logger.info(f"start to crawl: {self.page_url}")
        try:
            page.goto(self.page_url, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            logger.warning(f"goto {self.page_url} failed: {e}")
        page.wait_for_timeout(2000)
        processed = 0
        for page_no in range(1, self.max_pages + 1):
            if processed >= self.max_items:
                break
            items = self._parse_list(page)
            logger.info(f"[{self.region}]page {page_no} get {len(items)} tenders list.")
            for item in items:
                href = item['href']
                if href in self.exists_urls or href in self.tenders:
                    continue
                html = self._get_detail(context, href)
                tender = Tender(
                    region=self.region,
                    href=href,
                    title=item['title'],
                    release_date=item['date'],
                    html=html,
                    crawl_date=self._get_crawl_date(),
                )
                self.save_tender_to_es(tender)
                self.tenders[href] = tender
                logger.info(f"Found tender: {tender.title}")
                self._random_sleep(_max=3)
                processed += 1
                if processed >= self.max_items:
                    break
            if page_no < self.max_pages and processed < self.max_items:
                if not self._goto_next_page(page, page_no):
                    break
        logger.info(f"[{self.region}]crawl done, new {len(self.tenders)} tenders.")

    def _parse_list(self, page):
        return page.evaluate('''() => {
            const out = [];
            document.querySelectorAll('#jyul .TradeInfo').forEach(item => {
                const a = item.querySelector('.titH2 a');
                if (!a) return;
                const href = a.href || '';
                const title = (a.getAttribute('title') || a.textContent || '').trim();
                if (!href || !title) return;
                const dateEl = item.querySelector('.fbrq');
                out.push({href, title, date: dateEl ? dateEl.textContent.trim() : ''});
            });
            return out;
        }''')

    def _goto_next_page(self, page, current_page):
        """点击下一页，等待 #JgcxPage a.current 页码变为 current_page + 1"""
        try:
            page.locator('#JgcxPage #nextPage').click()
            page.wait_for_function(
                """expected => {
                    const el = document.querySelector('#JgcxPage a.current');
                    return el && el.textContent.trim() === String(expected);
                }""",
                arg=current_page + 1, timeout=15000,
            )
            page.wait_for_timeout(1000)
            return True
        except Exception as e:
            logger.warning(f"[{self.region}]goto page {current_page + 1} failed: {e}")
            return False

    def _get_detail(self, context, href):
        with context.new_page() as detail_page:
            detail_page.goto(href, wait_until="domcontentloaded", timeout=60000)
            detail_page.wait_for_selector("#detailCnt", timeout=15000)
            return detail_page.locator("#detailCnt").inner_html()


if __name__ == "__main__":
    JiLin().run(increment=True)
