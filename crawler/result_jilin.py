"""吉林中标/废标公告采集器（海云检索接口直连）

栏目切换：无 URL 变化，纯 POST 海云检索接口 `https://haiyun.jl.gov.cn/irs/front/search`，
请求体 customFilter.properties[0].value（channel_id）区分栏目：
- 中标公告 channel_id=69311
- 废标公告 channel_id=69313
响应 data.middle.listAndBox[].data 为单条记录 dict：
- title_no_tag（标题，含 <font> 标签需清洗）
- table-4（发布日期 YYYY-MM-DD HH:MM:SS）
- url（详情链接，跨域 http://www.ggzyzx.jl.gov.cn/...）
- table-5（**完整公告全文**，含"供应商名称/成交金额"或"废标原因"，无需抓详情页）
分页：pager.pageCount / pageNo 递增。
类型：本采集器分别抓中标/废标栏目，预设 announce_type（中标公告/废标公告）。
"""
import re
import requests

from crawler.base_crawler import Tender
from crawler.jilin import JiLin
from utils.log import logger

API_URL = 'https://haiyun.jl.gov.cn/irs/front/search'
CODE = '1892dd01822'

# (channel_id, announce_type 预设)
CHANNELS = [
    ('69311', '中标公告'),
    ('69313', '废标公告'),
]

_HEADERS = {
    'User-Agent': ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
                   '(KHTML, like Gecko) Chrome/158.0.0.0 Safari/537.36'),
    'Content-Type': 'application/json;charset=UTF-8',
    'Referer': 'https://www.ggzyzx.jl.gov.cn/jyxx/zfcg/',
    'Accept': 'application/json, text/plain, */*',
    'X-Requested-With': 'XMLHttpRequest',
}


def _clean_title(raw):
    """清洗 title_no_tag 的 <font> 等标签与实体。"""
    t = re.sub(r'<[^>]+>', '', raw or '')
    return t.strip()


class JiLinResult(JiLin):
    """吉林结果公告采集器：海云接口直连抓中标/废标栏目，table-5 即全文。"""

    def _crawl(self, context):
        # 接口直连模式，不依赖 Playwright 页面；context 仅用于与 run_by_date 框架兼容
        self._crawl_api()

    def _crawl_api(self):
        for channel_id, atype in CHANNELS:
            self._crawl_channel(channel_id, atype)

    def _crawl_channel(self, channel_id, atype):
        page_no = 1
        while page_no <= self.max_pages:
            body = {
                "code": CODE, "searchWord": None, "orderBy": "time", "dataTypeId": "501",
                "searchBy": "all", "pageNo": page_no, "pageSize": 10, "granularity": "ALL",
                "beginDateTime": "", "endDateTime": "", "isSearchForced": 0,
                "customFilter": {"operator": "and", "properties": [
                    {"property": "channel_id", "operator": "eq", "value": channel_id, "weight": 100}]},
            }
            try:
                resp = requests.post(API_URL, json=body, headers=_HEADERS, timeout=30)
                data = resp.json()
            except Exception as e:
                logger.error(f"[{self.region}]channel {channel_id} request failed (page {page_no}): {e}")
                break
            if not data.get('success'):
                logger.error(f"[{self.region}]channel {channel_id} failed: {data.get('msg')}")
                break
            boxes = (((data.get('data') or {}).get('middle') or {}).get('listAndBox') or [])
            records = [b.get('data') or {} for b in boxes if isinstance(b.get('data'), dict)]
            logger.info(f"[{self.region}]channel {channel_id} get {len(records)} records (page {page_no}).")
            if not records:
                break
            for rec in records:
                href = rec.get('url') or ''
                if not href:
                    continue
                if href in self.exists_urls or href in self.tenders:
                    continue
                tender = Tender(
                    region=self.region,
                    href=href,
                    title=_clean_title(rec.get('title_no_tag')),
                    release_date=(rec.get('table-4') or '')[:10],
                    html=rec.get('table-5') or '',
                    crawl_date=self._get_crawl_date(),
                )
                tender.announce_type = atype
                self.save_tender_to_es(tender)
                self.tenders[href] = tender
                self._random_sleep(_max=3)
            pager = (data.get('data') or {}).get('pager') or {}
            page_count = pager.get('pageCount') or page_no
            if page_no >= page_count or len(records) < 10:
                break
            page_no += 1
        logger.info(f"[{self.region}]channel {channel_id} done, new {len(self.tenders)} tenders.")


if __name__ == '__main__':
    JiLinResult().run()
