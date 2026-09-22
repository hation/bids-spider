import re

from crawler.base_crawler import BaseCrawler, Tender
from utils.log import logger


class XinJiang(BaseCrawler):
    """新疆公共资源交易网：HTML 列表页（trade_info.html），正文容器 div.public-article-container。

    注意：站点已改版，原 tradeInfo_new.html 返回 404，现使用 xinjiangggzy_new 路径。
    列表行无日期列，发布日期从详情链接 URL 中的 8 位日期段提取。

    分页：#pager 分页组件（a[data-page-index]，0 起），页码与"下一页"（span.next
    图标）共享 data-page-index，点击页码链接（DOM 顺序靠前）JS 异步加载。
    默认抓取前 50 页（或累计 500 条，取先到者）。
    """

    def __init__(self):
        super().__init__('xinjiang', max_page_num=None)
        self.index_url = 'https://ggzy.xinjiang.gov.cn/xinjiangggzy_new/jyxx/trade_info.html'
        self.date_pattern = re.compile(r'/(20\d{6})/')
        self.max_pages = 50  # 最多抓取页数
        self.max_items = 5000  # 最多抓取条数（与 max_pages 取先到者；已放宽，避免到达目标日前截断）

    def _crawl(self, context):
        page = context.new_page()
        page.goto(self.index_url, wait_until='domcontentloaded', timeout=60000)
        page.wait_for_selector('tr a[href*=".html"]', timeout=30000)
        page.wait_for_timeout(1500)
        processed = 0
        for page_no in range(1, self.max_pages + 1):
            if processed >= self.max_items:
                break
            results = self._parse_list(page)
            logger.info(f"[{self.region}] page {page_no} get {len(results)} tenders from list page.")
            for result in results:
                href = result['href']
                if href in self.exists_urls or href in self.tenders:
                    continue
                date = result['date']
                release_date = f"{date[:4]}-{date[4:6]}-{date[6:8]}" if len(date) == 8 else ''
                tender = Tender(self.region, href, result['title'], release_date)
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
            for (const tr of document.querySelectorAll('tr')) {
                for (const a of tr.querySelectorAll('a')) {
                    const h = a.href || '';
                    const m = h.match(/\\/20\\d{6}\\//);
                    if (!m) continue;
                    const title = (a.innerText || a.textContent || '').trim().replace(/\\s+/g, ' ');
                    if (title.length >= 8) {
                        out.push({title, href: h, date: m[0].replace(/\\//g, '')});
                        break;
                    }
                }
            }
            return out;
        }""")

    def _goto_next_page(self, page, current_page):
        """点击第 current_page + 1 页页码（0 起 index=current_page），等待 active 页码更新"""
        try:
            page.locator(f'#pager a[data-page-index="{current_page}"]').first.click()
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
        return page.locator('div.public-article-container').inner_html()


if __name__ == '__main__':
    XinJiang().run()
