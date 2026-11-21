import json
import time
from datetime import datetime
from urllib.parse import quote

import requests

from crawler.base_crawler import BaseCrawler, Tender
from utils.log import logger


class GuiZhou(BaseCrawler):
    def __init__(self):
        super().__init__('guizhou')
        self.max_pages = 50
        self.list_url = 'http://www.ccgp-guizhou.gov.cn/site/category?parentId=190013&childrenCode=ZcyAnnouncement'
        self.list_api = 'http://www.ccgp-guizhou.gov.cn/portal/category'
        self.detail_api = 'http://www.ccgp-guizhou.gov.cn/portal/detail'
        self.parent_id = '190013'
        self.category_code = 'ZcyAnnouncement1'
        self.headers = {
            'User-Agent': ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
                           '(KHTML, like Gecko) Chrome/120.0 Safari/537.36'),
            'Content-Type': 'application/json',
            'Referer': (self.list_url + '&categoryCode=ZcyAnnouncement1'),
            'Accept': 'application/json, text/plain, */*',
        }

    def _crawl(self, context):
        # 2026-11-20：页面触发式捕获请求头失效（列表页改版），改直连接口。
        # 接口 body 必须为 categoryCode 结构，否则 result.data 为 None。
        import time as _time
        for page_no in range(1, self.max_pages + 1):
            body = {'pageNo': page_no, 'pageSize': 15,
                    'categoryCode': self.category_code, '_t': int(_time.time() * 1000)}
            try:
                resp = requests.post(self.list_api, json=body, headers=self.headers, timeout=30)
                data = resp.json()
            except Exception as e:
                logger.error(f"[{self.region}]list request failed: {e}")
                break
            result = data.get('result') if isinstance(data, dict) else None
            inner = result.get('data') if isinstance(result, dict) else None
            records = inner.get('data') if isinstance(inner, dict) else None
            if not records:
                break
            logger.info(f"[{self.region}]get {len(records)} tenders list success (page {page_no}).")
            for record in records:
                if not isinstance(record, dict):
                    continue
                article_id = record.get('articleId') or ''
                title = (record.get('title') or '').strip()
                if not article_id or not title:
                    continue
                href = f'http://www.ccgp-guizhou.gov.cn/site/detail?parentId={self.parent_id}&articleId={quote(article_id)}'
                if href in self.exists_urls:
                    continue
                release_date = self._parse_date(record.get('publishDate'))
                html = self._get_detail(context, article_id)
                tender = Tender(region=self.region, href=href, title=title,
                                release_date=release_date, html=html,
                                crawl_date=self._get_crawl_date())
                self.save_tender_to_es(tender)
                self.tenders[href] = tender
                self._random_sleep(_max=3)
            if len(records) < 15:
                break
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
