from crawler.base_crawler import BaseCrawler, Tender
from utils.log import logger


class JiangSu(BaseCrawler):
    """江苏省公共资源交易网（http://jsggzy.jszwfw.gov.cn）
    列表经 /inteligentsearch/rest/esinteligentsearch/getFullTextDataNew 接口异步渲染到
    div.table-list div.table-row；翻页通过点击 #pager 分页器页码（data-page-index，0-based）
    触发接口重渲染。注意：页面默认只查询"当天"公告，当天无公告时列表为空（分页器无页码），
    此时保持单页不抓取，属站点正常行为。
    """

    def __init__(self):
        super().__init__('jiangsu', max_page_num=None)
        self.index_url = 'http://jsggzy.jszwfw.gov.cn/jyxx/tradeInfonew.html'
        self.base_url = 'http://jsggzy.jszwfw.gov.cn'
        self.max_pages = 50  # 默认抓取前 50 页（或累计 500 条，取先到者）

    def _crawl(self, context):
        page = context.new_page()
        page.goto(self.index_url, wait_until='domcontentloaded', timeout=60000)
        page.wait_for_selector('div.table-list', timeout=30000)
        # 列表为接口异步渲染，等待首屏行出现（当天无公告时可能始终为空）
        try:
            page.wait_for_selector('div.table-list div.table-row', timeout=15000)
        except Exception:
            pass
        # 先在列表页解析全部数据（locator 在页面导航后会失效，不能边导航边遍历）
        records = []
        for page_no in range(self.max_pages):
            page.wait_for_timeout(1000)
            for row in page.locator('div.table-list div.table-row').all():
                a = row.locator('div.col-title a').first
                if not a.count():
                    continue
                href = a.get_attribute('href')
                if not href:
                    continue
                href = href if href.startswith('http') else self.base_url + href
                title = (a.inner_text() or '').strip()
                date_text = ''
                if row.locator('div.col-date').count():
                    date_text = (row.locator('div.col-date').first.inner_text() or '').strip()
                records.append((href, title, date_text))
            if not records or len(records) >= 100:
                break
            # 翻页：点击下一页页码（mricode.pagination 组件，data-page-index 为 0-based 页码）
            next_idx = page_no + 1
            next_link = page.locator(f'#pager .m-pagination-page a[data-page-index="{next_idx}"]')
            if not next_link.count():
                break
            # 记录点击前当前页首行标题，用于等待新页数据渲染（防止翻页前误解析旧数据）
            prev_first_title = ''
            first_a = page.locator('div.table-list div.table-row div.col-title a').first
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
                    arg=['div.table-list div.table-row div.col-title a', prev_first_title],
                    timeout=30000,
                )
            except Exception:
                break
        logger.info(f"[{self.region}]get {len(records)} records from list page")
        for href, title, date_text in records:
            if href in self.exists_urls:
                continue
            page.goto(href, wait_until='domcontentloaded', timeout=60000)
            page.wait_for_selector('div.ewb-trade-con', timeout=30000)
            html = page.locator('div.ewb-trade-con').inner_html()
            tender = Tender(self.region, href, title, date_text, html, self._get_crawl_date())
            self.save_tender_to_es(tender)
            self.tenders[href] = tender
            self._random_sleep(_max=3)
        page.close()
        logger.info(f"[{self.region}]crawl done, new {len(self.tenders)} tenders.")


if __name__ == '__main__':
    JiangSu().run()
