import json
import time
from datetime import datetime
from urllib.parse import quote

from crawler.base_crawler import BaseCrawler, Tender
from utils.log import logger


class GuangXi(BaseCrawler):
    """广西政府采购网：SPA 站点，列表与详情均走 JSON 接口。

    列表：POST /portal/category（复用页面自身请求头与 body）
    详情：GET  /portal/detail?articleId=...&parentId=66485&timestamp=...
    正文来自详情接口的 result.data.content。
    """

    def __init__(self):
        super().__init__('guangxi', max_page_num=None)
        self.max_pages = 50  # 默认抓取前 50 页（或累计 500 条，取先到者）
        self.index_url = 'https://zfcg.gxzf.gov.cn/site/category?parentId=66485&childrenCode=ZcyAnnouncement'
        self.list_url = 'https://zfcg.gxzf.gov.cn/portal/category'
        self.detail_url = 'https://zfcg.gxzf.gov.cn/portal/detail'
        self.parent_id = '66485'
        self.list_headers = {}
        self.list_body = {}

    def _crawl(self, context):
        page = context.new_page()

        def handle_request(request):
            # 复用门户页面自身的列表接口请求头与请求体（含 categoryCode）
            if request.url == self.list_url or request.url.endswith('/portal/category'):
                self.list_headers = {k: v for k, v in request.headers.items() if k.lower() != 'content-length'}
                try:
                    self.list_body = json.loads(request.post_data or '{}')
                except Exception:
                    self.list_body = {}

        page.on("request", handle_request)
        page.goto(self.index_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(6000)  # 等待页面发起列表接口请求并渲染
        if not self.list_body:
            logger.error("未捕获到 portal/category 列表接口请求，可能页面结构已变化")
            return
        self._get_tenders(context)

    def _get_tenders(self, context):
        # 页大小优先取页面捕获的 list_body 中的 pageSize，缺省按 10 判断
        page_size = self.list_body.get('pageSize') or 10
        # 分页循环：翻页深度由日期判断决定（guarded_save 遇早于目标日停止）
        for page_no in range(1, self.max_pages + 1):
            self.list_body.update({'pageNo': page_no})
            response = context.request.post(
                self.list_url,
                data=json.dumps(self.list_body),
                headers={**self.list_headers, 'Content-Type': 'application/json;charset=UTF-8'},
            )
            data = response.json()
            records = ((data.get('result') or {}).get('data') or {}).get('data') or []
            logger.info(f"[{self.region}] get {len(records)} tenders list success (page {page_no}).")
            if not records or len(records) < page_size:
                break
            for record in records:
                article_id = record.get('articleId', '')
                if not article_id:
                    logger.warning("record has no articleId, skip.")
                    continue
                href = f"https://zfcg.gxzf.gov.cn/site/detail?parentId={self.parent_id}&articleId={quote(article_id, safe='')}"
                if href in self.exists_urls:
                    continue
                detail = self._get_detail(context, article_id)
                if not detail:
                    continue
                tender = Tender(
                    region=self.region,
                    href=href,
                    title=detail.get('title') or record.get('title', ''),
                    release_date=self._fmt_date(detail.get('publishDate') or record.get('publishDate')),
                    html=detail.get('content', ''),
                    crawl_date=self._get_crawl_date(),
                )
                self.save_tender_to_es(tender)
                self.tenders[href] = tender
                self._random_sleep(_max=3)
        logger.info(f"[{self.region}]crawl done, new {len(self.tenders)} tenders.")

    def _get_detail(self, context, article_id):
        ts = int(time.time() * 1000)
        url = f"{self.detail_url}?articleId={quote(article_id, safe='')}&parentId={self.parent_id}&timestamp={ts}"
        try:
            resp = context.request.get(url, headers={'Referer': self.index_url})
            data = resp.json()
            return (data.get('result') or {}).get('data') or {}
        except Exception as e:
            logger.error(f"get tender detail failed for {article_id}: {e}")
            return {}

    @staticmethod
    def _fmt_date(ms):
        try:
            return datetime.fromtimestamp(int(ms) / 1000).strftime('%Y-%m-%d')
        except Exception:
            return ''


if __name__ == '__main__':
    GuangXi().run()
