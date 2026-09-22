import re

from crawler.base_crawler import BaseCrawler, Tender
from utils.log import logger

class AnHui(BaseCrawler):
    """安徽政府采购网（https://www.ccgp-anhui.gov.cn）
    列表页为 SSR 渲染，正文不在详情页 DOM 中，通过 portal/detail 接口获取 HTML 正文。
    翻页：点击列表页底部分页器（.page ul.po-pager li.number，页码为 1-based）触发异步加载。
    """

    def __init__(self):
        super().__init__('anhui', max_page_num=None)
        self.index_url = ('https://www.ccgp-anhui.gov.cn/site/category'
                          '?parentId=oJCosldFbaJFzmyFhz1c6Q%3D%3D&childrenCode=ZcyAnnouncement3012')
        self.base_url = 'https://www.ccgp-anhui.gov.cn'
        self.detail_api = 'https://www.ccgp-anhui.gov.cn/portal/detail'
        self.detail_headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Referer': 'https://www.ccgp-anhui.gov.cn/',
        }
        self.max_pages = 50  # 默认抓取前 50 页（或累计 500 条，取先到者）

    def _crawl(self, context):
        page = context.new_page()
        page.goto(self.index_url, wait_until='domcontentloaded', timeout=60000)
        page.wait_for_selector('ul.list', timeout=30000)
        # 列表为异步加载，等待首屏列表项渲染
        try:
            page.wait_for_selector('ul.list li', timeout=15000)
        except Exception:
            pass
        # 先在列表页解析全部数据（locator 在页面导航后会失效，不能边导航边遍历）
        records = []
        for page_no in range(self.max_pages):
            page.wait_for_timeout(1000)
            items = page.locator('ul.list li').all()
            for item in items:
                a = item.locator('a[href*="/site/detail"]').first
                href = a.get_attribute('href') if a.count() else None
                if not href:
                    continue
                href_full = href if href.startswith('http') else self.base_url + href
                title = (a.inner_text() or '').strip()
                date_text = ''
                if item.locator('span.publish-time').count():
                    date_text = (item.locator('span.publish-time').first.inner_text() or '').strip()
                records.append((href_full, title, date_text))
            if not records:
                break
            # 翻页：点击下一页页码（分页器为 po-pagination，页码 li.number 为 1-based）
            next_page = page_no + 2
            next_link = page.locator(f'.page ul.po-pager li.number:text-is("{next_page}")').first
            if not next_link.count():
                break
            # 记录点击前当前页首行标题，用于等待新页数据渲染
            prev_first_title = ''
            first_a = page.locator('ul.list li a[href*="/site/detail"]').first
            if first_a.count():
                prev_first_title = (first_a.inner_text() or '').strip()
            next_link.click(timeout=10000)
            try:
                page.wait_for_function(
                    """([sel, prev]) => {
                        const el = document.querySelector(sel);
                        if (!el) return false;
                        const t = (el.innerText || '').trim();
                        return !!t && t !== prev;
                    }""",
                    arg=['ul.list li a[href*="/site/detail"]', prev_first_title],
                    timeout=30000,
                )
            except Exception:
                break
        logger.info(f"[{self.region}]get {len(records)} records from list page")
        for href_full, title, date_text in records:
            if href_full in self.exists_urls:
                continue
            # 正文不在详情页 DOM，改用 portal/detail 接口
            # 注意 articleId 为 base64（含 +），parse_qs 会把 + 解码成空格导致查询失败，故用正则原样提取
            html = ''
            m = re.search(r'articleId=([^&]+)', href_full)
            article_id = m.group(1) if m else ''
            m = re.search(r'parentId=([^&]+)', href_full)
            parent_id = m.group(1) if m else ''
            if article_id:
                resp = context.request.get(self.detail_api,
                                           params={'articleId': article_id, 'parentId': parent_id},
                                           headers=self.detail_headers)
                # 接口偶发返回空响应，容错后继续（正文为空时仅保存标题列表）
                try:
                    data = resp.json()
                except Exception:
                    logger.warning(f"[{self.region}]detail api empty response: {href_full}")
                    data = {}
                if data.get('success'):
                    detail = (data.get('result') or {}).get('data') or {}
                    html = detail.get('content', '') or ''
                    if not date_text and detail.get('publishDate'):
                        import datetime
                        date_text = datetime.datetime.fromtimestamp(detail['publishDate'] / 1000).strftime('%Y-%m-%d')
            tender = Tender(self.region, href_full, title, date_text, html, self._get_crawl_date())
            self.save_tender_to_es(tender)
            self.tenders[href_full] = tender
            self._random_sleep(_max=3)
        page.close()
        logger.info(f"[{self.region}]crawl done, new {len(self.tenders)} tenders.")


if __name__ == '__main__':
    AnHui().run()
