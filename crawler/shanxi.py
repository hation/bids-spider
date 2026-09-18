from urllib.parse import quote, urlparse

from crawler.base_crawler import BaseCrawler, Tender
from utils.log import logger


class ShanXi(BaseCrawler):
    """山西政府采购网（DOM 列表，正文走 portal/detail JSON 接口）。

    分页：Element Plus 风格 po-pagination，点击页码 li.number / 下一页按钮
    button.btn-next JS 异步加载（无 URL 变化）。默认抓取前 5 页（或累计
    100 条，取先到者）。
    """

    def __init__(self):
        super().__init__('shanxi', max_page_num=None)
        self.page_url = 'http://www.ccgp-shanxi.gov.cn/site/category?parentId=138010&childrenCode=ZcyAnnouncement'
        self.headers = {}
        self.max_pages = 5  # 最多抓取页数
        self.max_items = 100  # 最多抓取条数（与 max_pages 取先到者）

    def _crawl(self, context):
        page = context.new_page()
        logger.info(f"start to crawl: {self.page_url}")

        def handle_request(request):
            # 捕获门户页请求头（cookie、UA 等），用于详情接口直调
            if 'portal/' in request.url:
                self.headers = {k: v for k, v in request.headers.items() if k.lower() != 'content-length'}

        page.on("request", handle_request)
        try:
            page.goto(self.page_url, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            logger.warning(f"goto {self.page_url} failed: {e}")
        page.wait_for_timeout(4000)
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
            document.querySelectorAll('li').forEach(li => {
                const a = li.querySelector('.list-title a');
                if (!a) return;
                const href = a.href || '';
                const title = (a.getAttribute('title') || a.textContent || '').trim();
                if (!href || !title) return;
                const timeEl = li.querySelector('.publish-time');
                out.push({href, title, date: timeEl ? timeEl.textContent.trim() : ''});
            });
            return out;
        }''')

    def _goto_next_page(self, page, current_page):
        """点击下一页按钮，等待分页组件 active 页码变为 current_page + 1"""
        try:
            page.locator("button.btn-next").click()
            page.wait_for_function(
                """expected => {
                    const el = document.querySelector('ul.po-pager li.number.active');
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
        # 详情正文由 /portal/detail 接口直接返回 HTML，无需打开 SPA 详情页
        # 注意：不能用 parse_qs 解析 articleId，其会把 "+" 解码为空格导致取错文章
        article_id = ''
        parent_id = ''
        for pair in urlparse(href).query.split('&'):
            if pair.startswith('articleId='):
                article_id = pair[len('articleId='):]
            elif pair.startswith('parentId='):
                parent_id = pair[len('parentId='):]
        if not article_id:
            logger.warning(f"detail href has no articleId: {href}")
            return ''
        api_url = (f"http://www.ccgp-shanxi.gov.cn/portal/detail?articleId={quote(article_id, safe='')}"
                   f"&parentId={parent_id}")
        headers = dict(self.headers)
        headers['Referer'] = href
        content = ''
        for attempt in range(3):
            response = context.request.get(api_url, headers=headers)
            try:
                data = response.json()
            except Exception as e:
                logger.warning(f"detail json parse failed: {href} {e}")
                break
            result = (data.get('result') or {}).get('data') or {}
            content = result.get('content') or ''
            if content:
                break
            self._random_sleep(_max=3)  # 接口偶发返回空，重试
        return content


if __name__ == "__main__":
    ShanXi().run(increment=True)
