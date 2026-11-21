"""浙江中标结果/招标异常公告采集器（接口直连模式）

浙江为 SPA，栏目由 getFullTextDataNew 接口请求体 condition[0].equal（categorynum）区分：
- 002001005 中标结果公告 -> announce_type=中标公告
- 002001015 招标异常公告 -> announce_type=废标公告

接口无需 token/鉴权（实测空 token 直连 200），POST 即返回 JSON：
result.totalcount / result.records[]（title / webdate / linkurl / infod）。
详情页正文容器 div.article-info（与 zhejiang.py 一致），可直接 requests 抓取。
"""
import json
import re

import requests

from crawler.base_crawler import Tender
from crawler.zhejiang import ZheJiang
from utils.log import logger

LIST_API = 'https://ggzy.zj.gov.cn/inteligentsearch/rest/esinteligentsearch/getFullTextDataNew'

# (栏目代码, announce_type 预设)
RESULT_CATEGORIES = [
    ('002001005', '中标公告'),
    ('002001015', '废标公告'),
]

_HEADERS = {
    'Content-Type': 'application/json;charset=UTF-8',
    'Referer': 'https://ggzy.zj.gov.cn/jyxxgk/002002/list.html',
    'X-Requested-With': 'XMLHttpRequest',
    'User-Agent': ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
                   '(KHTML, like Gecko) Chrome/158.0.0.0 Safari/537.36'),
}


class ZheJiangResult(ZheJiang):
    """浙江结果公告采集器：接口直连抓中标结果公告 + 招标异常公告（废标）。"""

    def __init__(self):
        super().__init__()
        self.max_pages = 50

    def _crawl(self, context):
        # 接口直连模式，不依赖 Playwright 页面；context 仅用于与 run_by_date 框架兼容
        self._crawl_api()

    def _crawl_api(self):
        start = getattr(self, '_date_start', None) or ''
        end = getattr(self, '_date_end', None) or ''
        time_range = None
        if start:
            time_range = {"fieldName": "webdate",
                          "startTime": f"{start} 00:00:00", "endTime": f"{end} 23:59:59"}
        for cat, atype in RESULT_CATEGORIES:
            self._crawl_category(cat, atype, time_range)

    def _crawl_category(self, cat, atype, time_range):
        pn = 0
        while pn < self.max_pages:
            body = {
                "token": "", "pn": pn, "rn": 16, "sdt": "", "edt": "", "wd": "",
                "inc_wd": "", "exc_wd": "", "fields": "title", "cnum": "001",
                "sort": "{\"webdate\":\"0\"}", "ssort": "title", "cl": 200, "terminal": "",
                "condition": [
                    {"fieldName": "categorynum", "isLike": True, "likeType": 2, "equal": cat},
                    {"fieldName": "infoc", "isLike": True, "likeType": 2, "equal": "33"},
                ],
                "time": [time_range] if time_range else None,
                "highlights": "", "statistics": None, "unionCondition": None,
                "accuracy": "", "noParticiple": "0", "searchRange": None, "isBusiness": "1",
            }
            try:
                resp = requests.post(LIST_API, data=json.dumps(body), headers=_HEADERS, timeout=30)
                data = resp.json()
            except Exception as e:
                logger.error(f"[{self.region}]list request failed (page {pn}): {e}")
                break
            records = ((data.get('result') or {}).get('records') or [])
            logger.info(f"[{self.region}]栏目 {cat} page {pn}: {len(records)} records")
            if not records:
                break
            for rec in records:
                self._handle_record(cat, atype, rec)
            if len(records) < 16:
                break
            pn += 1

    def _handle_record(self, cat, atype, rec):
        title = (rec.get('title') or '').strip()
        linkurl = rec.get('linkurl') or ''
        webdate = rec.get('webdate') or ''
        if not title or not linkurl:
            return
        href = linkurl if linkurl.startswith('http') else 'https://ggzy.zj.gov.cn' + linkurl
        if href in self.exists_urls or href in self.tenders:
            return
        tender = Tender(region=self.region, href=href, title=title,
                        release_date=webdate, crawl_date=self._get_crawl_date())
        tender.announce_type = atype
        tender.html = self._fetch_detail(href)
        self.save_tender_to_es(tender)
        self.tenders[href] = tender
        self._random_sleep(_max=3)

    @staticmethod
    def _fetch_detail(href):
        """详情页正文：requests 直连抓 div.article-info（与 zhejiang.py 容器一致）。"""
        try:
            resp = requests.get(href, headers=_HEADERS, timeout=30)
            if resp.status_code != 200:
                logger.warning(f"[zhejiang]detail status {resp.status_code}: {href}")
                return ''
            m = re.search(r'<div\s+class="article-info"[^>]*>(.*?)</div>', resp.text, re.S)
            return m.group(1) if m else ''
        except Exception as e:
            logger.warning(f"[zhejiang]parse detail failed {href}: {e}")
            return ''


if __name__ == '__main__':
    ZheJiangResult().run()
