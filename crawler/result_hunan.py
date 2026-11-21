"""湖南中标/废标公告采集器（政采云 portal 接口直连模式）

列表：POST /portal/category，categoryCode：中标=160-986968、废标=160-253440；
载荷带 `districtCode:["430000"]` 与 `isProvince:true`（全省），分页 pageNo/pageSize=15。
详情：GET /portal/detail?articleId=...&parentId=622707，正文 content（HTML）。
类型：按 categoryCode 预设（废标栏目标题多为"结果公告"字样，不能靠标题关键词分类）。
金额格式：`中标（成交）金额：1545469.56元`（冒号+数字+元，html2text 加粗 `**` 已兼容）。
"""
import time
from datetime import datetime

import requests

from crawler.base_crawler import Tender
from crawler.hunan import HuNan
from utils.log import logger

LIST_API = 'http://www.ccgp-hunan.gov.cn/portal/category'
DETAIL_API = 'http://www.ccgp-hunan.gov.cn/portal/detail'
PARENT_ID = '622707'
# (categoryCode, announce_type)
CATEGORIES = [
    ('160-986968', '中标公告'),
    ('160-253440', '废标公告'),
]

_HEADERS = {
    'User-Agent': ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
                   '(KHTML, like Gecko) Chrome/158.0.0.0 Safari/537.36'),
    'Content-Type': 'application/json;charset=UTF-8',
    'Accept': 'application/json, text/plain, */*',
    'X-Requested-With': 'XMLHttpRequest',
    'Origin': 'http://www.ccgp-hunan.gov.cn',
    'Referer': ('http://www.ccgp-hunan.gov.cn/luban/front/category?districtCode=430000'
                '&isProvince=true&parentId=622707&childrenCode=160-945988'),
}


class HuNanResult(HuNan):
    """湖南结果公告采集器：政采云接口直连抓中标/废标栏目，类型由 categoryCode 预设。"""

    def _crawl(self, context):
        # 接口直连模式，不依赖 Playwright 页面；context 仅用于与 run_by_date 框架兼容
        self._crawl_api()

    def _crawl_api(self):
        from crawler.base_crawler import _DateBoundaryReached
        for cc, atype in CATEGORIES:
            try:
                for page_no in range(1, self.max_pages + 1):
                    body = {'pageNo': page_no, 'pageSize': 15, 'categoryCode': cc,
                            '_t': int(time.time() * 1000),
                            'districtCode': ['430000'], 'isProvince': True}
                    try:
                        resp = requests.post(LIST_API, json=body, headers=_HEADERS, timeout=30)
                        data = resp.json()
                    except Exception as e:
                        logger.error(f"[{self.region}]list request failed (page {page_no}, {cc}): {e}")
                        break
                    records = (((data.get('result') or {}).get('data') or {}).get('data') or [])
                    logger.info(f"[{self.region}]{atype} get {len(records)} records (page {page_no}).")
                    if not records:
                        break
                    for rec in records:
                        self._handle_record(rec, atype)
                    if len(records) < 15:
                        break
            except _DateBoundaryReached:
                # 接口直连无日期参数，翻页会翻过目标日触发日期守卫异常；
                # 只结束当前栏目，继续下一 categoryCode
                logger.info(f"[{self.region}]{atype} 已翻过目标日期范围，结束本栏目")
                continue

    def _handle_record(self, rec, atype):
        article_id = rec.get('articleId') or ''
        title = (rec.get('title') or '').strip()
        if not article_id or not title:
            return
        # href 与主爬虫（hunan.py）入库格式一致：articleId 不做 URL 编码（base64 含 +/=/）
        href = (f'http://www.ccgp-hunan.gov.cn/luban/front/detail?parentId={PARENT_ID}'
                f'&articleId={article_id}')
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
        tender.announce_type = atype
        tender.html = self._fetch_detail(article_id)
        self.save_tender_to_es(tender)
        self.tenders[href] = tender
        self._random_sleep(_max=3)

    def _fetch_detail(self, article_id):
        api_url = f'{DETAIL_API}?articleId={article_id}&parentId={PARENT_ID}'
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
    HuNanResult().run()
