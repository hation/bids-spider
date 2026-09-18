import time
import urllib.parse

from crawler.base_crawler import BaseCrawler, Tender
from utils.log import logger


class HuNan(BaseCrawler):
    """湖南省政府采购网（DOM 列表，正文走 portal/detail JSON 接口）

    任务给出的 /page/notice/more.jsp 已 404，改用真实的采购公告分类列表页
    /luban/front/category?parentId=622707 替代；详情正文由页面异步接口
    /portal/detail 返回 result.data.content。

    分页：Element Plus 风格 po-pagination，点击页码 li.number / 下一页按钮
    button.btn-next JS 异步加载（无 URL 变化）。默认抓取前 5 页（或累计
    100 条，取先到者）。
    """

    def __init__(self):
        super().__init__('hunan')
        self.index_url = (
            'http://www.ccgp-hunan.gov.cn/luban/front/category'
            '?districtCode=430000&isProvince=true&parentId=622707&childrenCode=160-945988'
        )
        self.base_url = 'http://www.ccgp-hunan.gov.cn'
        self.detail_api = 'http://www.ccgp-hunan.gov.cn/portal/detail'
        self.max_pages = 50  # 最多抓取页数
        self.max_items = 500  # 最多抓取条数（与 max_pages 取先到者）

    def _crawl(self, context):
        page = context.new_page()
        page.goto(self.index_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector("li:has(span.publish-time)", timeout=30000)
        page.wait_for_timeout(2000)
        processed = 0
        for page_no in range(1, self.max_pages + 1):
            if processed >= self.max_items:
                break
            tenders = self.get_one_page_titles(page)
            logger.info(f"[{self.region}]page {page_no} get {len(tenders)} tenders list success.")
            for href, tender in tenders.items():
                if href in self.exists_urls or href in self.tenders:
                    continue
                tender.html = self._fetch_detail(context, href)
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
        page.close()
        logger.info(f"[{self.region}]crawl done, new {len(self.tenders)} tenders.")

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

    def get_one_page_titles(self, page):
        """解析列表页第一页公告，返回 {href: Tender}"""
        tenders = {}
        items = page.locator("li:has(span.publish-time)").all()
        for item in items:
            a_tag = item.locator("div.list-title > a")
            href = a_tag.get_attribute("href") or ''
            title = a_tag.inner_text().strip()
            date_text = item.locator("span.publish-time").inner_text().strip()
            if not href or not title:
                logger.warning(f"item missing href or title, skip: {title}")
                continue
            if href.startswith('/'):
                href = self.base_url + href
            tenders[href] = Tender(self.region, href, title, date_text)
        return tenders

    def _fetch_detail(self, context, href, retries=3):
        """详情正文经 JSON 接口获取，对异常结构做容错并重试"""
        params = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
        article_id = (params.get('articleId') or [''])[0]
        parent_id = (params.get('parentId') or [''])[0]
        if not article_id:
            return ''
        api_url = f"{self.detail_api}?articleId={urllib.parse.quote(article_id)}&parentId={parent_id}"
        for attempt in range(1, retries + 1):
            try:
                response = context.request.get(
                    api_url,
                    headers={'Referer': href, 'Accept': 'application/json, text/plain, */*'},
                )
                data = response.json()
                result = data.get('result') or {}
                detail = result.get('data') if isinstance(result, dict) else None
                content = detail.get('content') if isinstance(detail, dict) else None
                if content:
                    return content
                logger.warning(f"[{self.region}]detail empty/abnormal (attempt {attempt}/{retries}): {href}")
            except Exception as e:
                logger.warning(f"[{self.region}]fetch detail failed {href} (attempt {attempt}/{retries}): {e}")
            if attempt < retries:
                time.sleep(1)
        return ''


if __name__ == '__main__':
    HuNan().run()
