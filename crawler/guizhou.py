import json
import time
from datetime import datetime
from urllib.parse import quote

from crawler.base_crawler import BaseCrawler, Tender
from utils.log import logger


class GuiZhou(BaseCrawler):
    def __init__(self):
        super().__init__('guizhou')
        self.max_pages = 50  # 默认抓取前 50 页（或累计 500 条，取先到者）
        self.list_url = 'http://www.ccgp-guizhou.gov.cn/site/category?parentId=190013&childrenCode=ZcyAnnouncement'
        self.list_api = 'http://www.ccgp-guizhou.gov.cn/portal/category'
        self.detail_api = 'http://www.ccgp-guizhou.gov.cn/portal/detail'
        self.parent_id = '190013'
        self.headers = {}
        self.body = {}

    def _crawl(self, context):
        page = context.new_page()

        def handle_request(request):
            # 复用列表页自身的接口请求头与请求体
            if self.list_api in request.url:
                self.headers = {k: v for k, v in request.headers.items() if k.lower() != 'content-length'}
                try:
                    self.body = json.loads(request.post_data or '{}')
                except Exception:
                    self.body = {}

        page.on("request", handle_request)
        page.goto(self.list_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(8000)  # 等待页面发起列表接口请求
        if not self.body:
            logger.error("未捕获到列表接口请求头，可能页面结构已变化")
            return
        self._get_tenders(context)

    def _get_tenders(self, context):
        # 分页循环：默认抓取前 max_pages 页（或累计 100 条，取先到者）
        for page_no in range(1, self.max_pages + 1):
            if len(self.tenders) >= 100:
                break
            self.body.update({'pageNo': page_no, 'pageSize': 15})
            response = context.request.post(self.list_api, data=json.dumps(self.body), headers=self.headers)
            data = response.json()
            result = (data.get("result") or {}).get("data") or {}
            records = result.get("data") or []
            logger.info(f"[{self.region}]get {len(records)} tenders list success (page {page_no}).")
            if not records or len(records) < 15:
                break
            for record in records:
                article_id = record.get('articleId') or ''
                title = (record.get('title') or '').strip()
                if not article_id or not title:
                    continue
                href = f'http://www.ccgp-guizhou.gov.cn/site/detail?parentId={self.parent_id}&articleId={quote(article_id)}'
                if href in self.exists_urls:
                    continue
                release_date = self._parse_date(record.get('publishDate'))
                html = self._get_detail(context, article_id)
                tender = Tender(
                    region=self.region,
                    href=href,
                    title=title,
                    release_date=release_date,
                    html=html,
                    crawl_date=self._get_crawl_date(),
                )
                self.save_tender_to_es(tender)
                self.tenders[href] = tender
                self._random_sleep(_max=3)
        logger.info(f"[{self.region}]crawl done, new {len(self.tenders)} tenders.")

    def _get_detail(self, context, article_id, retries=3):
        """获取详情正文，对接口异常结构做容错并重试"""
        for attempt in range(1, retries + 1):
            try:
                response = context.request.get(
                    self.detail_api,
                    params={'articleId': article_id, 'parentId': self.parent_id,
                            'timestamp': int(time.time() * 1000)},
                    headers=self.headers,
                )
                data = response.json()
                result = data.get("result")
                inner = result.get("data") if isinstance(result, dict) else None
                content = inner.get("content") if isinstance(inner, dict) else None
                if content:
                    return content
                logger.warning(f"[{self.region}]detail empty/abnormal (attempt {attempt}/{retries}): {article_id}")
            except Exception as e:
                logger.warning(f"[{self.region}]parse detail failed {article_id} (attempt {attempt}/{retries}): {e}")
            if attempt < retries:
                time.sleep(1)
        return ''

    @staticmethod
    def _parse_date(epoch_millis):
        try:
            return datetime.fromtimestamp(int(epoch_millis) / 1000).strftime('%Y-%m-%d %H:%M:%S')
        except Exception:
            return ''


if __name__ == '__main__':
    GuiZhou().run()
