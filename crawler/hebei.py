import time

from crawler.base_crawler import BaseCrawler, Tender
from utils.log import logger


class HeBei(BaseCrawler):
    def __init__(self, max_pages=5):
        super().__init__('hebei', max_page_num=None)
        self.max_pages = max_pages
        self.index_url = 'https://szj.hebei.gov.cn/hbggfwpt/jydt/salesPlat.html'
        self.headers = {}

    def _crawl(self, context):
        page = context.new_page()
        detail_page = context.new_page()
        page.goto("https://szj.hebei.gov.cn/", wait_until="domcontentloaded", timeout=60000)
        time.sleep(1)
        page.goto(self.index_url, wait_until="domcontentloaded", timeout=5000)
        page.wait_for_selector("ul#content", timeout=30000)
        page.locator('a:text-is("政府采购")').click()
        time.sleep(.5)
        try:
            for page_no in range(self.max_pages):
                results = self._parse_list(page)
                logger.info(f"[{self.region}]第 {page_no + 1} 页 {len(results)} 条")
                for result in results:
                    if result['href'] in self.exists_urls or result['href'] in self.tenders:
                        continue
                    html = self._get_detail(detail_page, result['href'])
                    tender = Tender(self.region, result['href'], result['title'], result['releaseDate'], html, self._get_crawl_date())
                    self.save_tender_to_es(tender)
                    self.tenders[result['href']] = tender
                    self._random_sleep(_max=3)
                if not self._goto_next_page(page):
                    break
        finally:
            page.close()
            detail_page.close()
        logger.info(f"[{self.region}]crawl done, new {len(self.tenders)} tenders.")

    @staticmethod
    def _parse_list(page):
        """解析当前页列表，返回 [{href, title, releaseDate}]"""
        return page.evaluate('''() => {
                        const items = document.querySelectorAll('#content li');
                        const data = [];
                        items.forEach(item => {
                            const link = item.querySelector('a');
                            const href = link ? link.href : null;
                            const title = link ? link.title : null;
                            const span = item.querySelector('span.r');
                            const releaseDate = span ? span.textContent.trim() : null;
                            if (href || title || releaseDate) {
                                data.push({ href, title, releaseDate });
                            }
                        });
                        return data;
                    }''')

    @staticmethod
    def _get_detail(page, href):
        page.goto(href, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector("div.ewb-copy", timeout=10000)
        return page.locator("div.ewb-copy").inner_html()

    @staticmethod
    def _goto_next_page(page):
        """点击下一页（»）按钮；分页器：#page ul.m-pagination-page a，» 为下一页"""
        next_btn = page.locator('#page ul.m-pagination-page a:text-is("»")').first
        if not next_btn.count():
            return False
        # 记录当前首条标题用于等待列表刷新
        prev = ''
        first = page.locator('#content li a').first
        if first.count():
            try:
                prev = (first.inner_text() or '').strip()
            except Exception:
                prev = ''
        try:
            next_btn.click(timeout=10000)
            page.wait_for_function(
                """([prev]) => {
                    const first = document.querySelector('#content li a');
                    if (!first) return false;
                    return (first.innerText || '').trim() !== prev;
                }""",
                arg=[prev],
                timeout=15000,
            )
            return True
        except Exception as e:
            logger.warning(f"hebei 翻页失败: {e}")
            return False


if __name__ == '__main__':
    HeBei().run()
