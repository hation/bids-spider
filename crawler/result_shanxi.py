"""山西结果公告采集器（政采云 portal 接口直连模式）

列表：POST /portal/category，categoryCode=ZcyAnnouncement2（结果公告栏目，330946 条）
详情：GET  /portal/detail?articleId=... （无需 cookie，Referer 即可；正文含
      "中标（成交）信息"表格：供应商名称|中标金额|评审总得分，与云南同格式）
日期：记录 publishDate 为毫秒时间戳，转 ISO YYYY-MM-DD。
类型：标题含"结果/中标/成交"→中标公告、"终止/废标/流标"→废标公告，由标题关键词自动分类。
"""
import time
from datetime import datetime
from urllib.parse import quote

import requests

from crawler.base_crawler import Tender
from crawler.shanxi import ShanXi
from utils.log import logger

LIST_API = 'http://www.ccgp-shanxi.gov.cn/portal/category'
DETAIL_API = 'http://www.ccgp-shanxi.gov.cn/portal/detail'
PARENT_ID = '138010'
CATEGORY_CODE = 'ZcyAnnouncement2'

_HEADERS = {
    'User-Agent': ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
                   '(KHTML, like Gecko) Chrome/158.0.0.0 Safari/537.36'),
    'Content-Type': 'application/json;charset=UTF-8',
    'Accept': 'application/json, text/plain, */*',
    'X-Requested-With': 'XMLHttpRequest',
    'Referer': (f'http://www.ccgp-shanxi.gov.cn/site/category?parentId={PARENT_ID}'
                f'&childrenCode={CATEGORY_CODE}'),
}


class ShanXiResult(ShanXi):
    """山西结果公告采集器：政采云接口直连抓结果公告栏目，类型由标题关键词自动分类。"""

    def _crawl(self, context):
        # 接口直连模式，不依赖 Playwright 页面；context 仅用于与 run_by_date 框架兼容
        self._crawl_api()

    def _crawl_api(self):
        for page_no in range(1, self.max_pages + 1):
            body = {'pageNo': page_no, 'pageSize': 15, 'categoryCode': CATEGORY_CODE,
                    '_t': int(time.time() * 1000)}
            try:
                resp = requests.post(LIST_API, json=body, headers=_HEADERS, timeout=30)
                data = resp.json()
            except Exception as e:
                logger.error(f"[{self.region}]list request failed (page {page_no}): {e}")
                break
            records = (((data.get('result') or {}).get('data') or {}).get('data') or [])
            logger.info(f"[{self.region}]get {len(records)} records (page {page_no}).")
            if not records:
                break
            for rec in records:
                self._handle_record(rec)
            if len(records) < 15:
                break

    def _handle_record(self, rec):
        article_id = rec.get('articleId') or ''
        title = (rec.get('title') or '').strip()
        if not article_id or not title:
            return
        href = (f'http://www.ccgp-shanxi.gov.cn/site/detail?parentId={PARENT_ID}'
                f'&articleId={quote(article_id, safe="")}')
        if href in self.exists_urls or href in self.tenders:
            return
        pub = rec.get('publishDate') or ''
        if isinstance(pub, (int, float)):
            try:
                pub = datetime.fromtimestamp(pub / 1000).strftime('%Y-%m-%d %H:%M:%S')
            except Exception:
                pub = ''
        tender = Tender(region=self.region, href=href, title=title,
                        release_date=str(pub), crawl_date=self._get_crawl_date())
        tender.html = self._fetch_detail(article_id)
        self.save_tender_to_es(tender)
        self.tenders[href] = tender
        self._random_sleep(_max=3)

    def _fetch_detail(self, article_id):
        api_url = (f'{DETAIL_API}?articleId={quote(article_id, safe="")}&parentId={PARENT_ID}')
        for _ in range(3):
            try:
                resp = requests.get(api_url, headers=_HEADERS, timeout=30)
                data = resp.json()
                content = ((data.get('result') or {}).get('data') or {}).get('content') or ''
                if content:
                    return content
            except Exception as e:
                logger.warning(f"[{self.region}]detail failed {article_id}: {e}")
            self._random_sleep(_max=3)
        return ''


if __name__ == '__main__':
    ShanXiResult().run()
