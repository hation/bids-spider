from crawler.base_crawler import BaseCrawler, Tender
from utils.log import logger


class ZheJiang(BaseCrawler):
    """浙江省公共资源交易服务平台（https://ggzy.zj.gov.cn）"""

    def __init__(self):
        super().__init__('zhejiang', max_page_num=None)
        self.index_url = 'https://ggzy.zj.gov.cn/jyxxgk/002002/list.html'
        self.base_url = 'https://ggzy.zj.gov.cn'
        self.max_pages = 50  # 默认抓取前 50 页（或累计 500 条，取先到者）

    def _crawl(self, context):
        page = context.new_page()
        page.goto(self.index_url, wait_until='domcontentloaded', timeout=60000)
        page.wait_for_selector('ul.ewb-public-items', timeout=30000)
        # 列表为异步加载，等待首屏列表项渲染
        try:
            page.wait_for_selector('ul.ewb-public-items li.ewb-public-item', timeout=15000)
        except Exception:
            pass
        # 先在列表页解析全部数据（locator 在页面导航后会失效，不能边导航边遍历）
        records = []
        for page_no in range(self.max_pages):
            page.wait_for_timeout(1000)
            for item in page.locator('ul.ewb-public-items li.ewb-public-item').all():
                a = item.locator('a.infotitle').first
                if not a.count():
                    continue
                href = a.get_attribute('href')
                if not href:
                    continue
                href = href if href.startswith('http') else self.base_url + href
                title = (a.inner_text() or '').strip()
                date_text = ''
                if item.locator('span.ewb-date').count():
                    date_text = (item.locator('span.ewb-date').first.inner_text() or '').strip()
                records.append((href, title, date_text))
            if not records:
                break
            # 翻页：点击下一页页码（mricode.pagination，data-page-index 为 0-based 页码）
            next_idx = page_no + 1
            next_link = page.locator(f'.pager .m-pagination-page a[data-page-index="{next_idx}"]')
            if not next_link.count():
                break
            # 记录点击前当前页首行标题，用于等待新页数据渲染
            prev_first_title = ''
            first_a = page.locator('ul.ewb-public-items li.ewb-public-item a.infotitle').first
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
                    arg=['ul.ewb-public-items li.ewb-public-item a.infotitle', prev_first_title],
                    timeout=30000,
                )
            except Exception:
                break
        logger.info(f"[{self.region}]get {len(records)} records from list page")
        for href, title, date_text in records:
            if href in self.exists_urls:
                continue
            page.goto(href, wait_until='domcontentloaded', timeout=60000)
            page.wait_for_selector('div.article-info', timeout=30000)
            html = page.locator('div.article-info').inner_html()
            tender = Tender(self.region, href, title, date_text, html, self._get_crawl_date())
            self.save_tender_to_es(tender)
            self.tenders[href] = tender
            self._random_sleep(_max=3)
        page.close()
        logger.info(f"[{self.region}]crawl done, new {len(self.tenders)} tenders.")


if __name__ == '__main__':
    ZheJiang().run()
