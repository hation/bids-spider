"""辽宁结果公告采集器（getHomePunInfoList 接口 + 页面捕获 fn 签名头）

栏目切换关键字段 infoTypeCode：
- 采购公告：["1001","100101"..."1401"]
- 结果公告：["1002","100201","100202","100203","100204","100205","1004","1403","140301"]
  （100205=终止公告，作废标处理；其余 1002 系=结果公告）
详情在列表记录 content 字段（HTML，含"供应商名称：××"+"中标（成交）金额"），无需二次请求。
请求头带 fn 签名（页面脚本生成），复用现有 liaoning.py 的"打开门户页捕获请求头"机制。
"""
import json

from crawler.base_crawler import Tender
from crawler.liaoning import LiaoNing
from utils.log import logger

# 结果公告 tab 的 infoTypeCode（含 100205 终止公告）
RESULT_INFO_TYPE = '["1002","100201","100202","100203","100204","100205","1004","1403","140301"]'


class LiaoNingResult(LiaoNing):
    """辽宁结果公告采集器：切换 infoTypeCode 为结果公告栏目，类型由标题关键词自动分类。"""

    def __init__(self, max_records=100):
        super().__init__(max_records=max_records)
        self.max_pages = 50  # 最多抓取页数（结果公告按日期翻页，由 guarded_save 停止）

    def _get_tenders(self, context):
        for page_no in range(1, self.max_pages + 1):
            body = dict(self.body)
            body.update({'infoTypeCode': RESULT_INFO_TYPE, 'current': page_no,
                         'rowCount': self.max_records})
            try:
                response = context.request.post(self.api_url, data=body, headers=self.headers)
                data = response.json()
            except Exception as e:
                logger.error(f"[{self.region}]get list failed (page {page_no}): {e}")
                break
            if data.get("code") != 200:
                logger.error(f"[{self.region}]get list failed: {data}")
                break
            records = data.get("data", {}).get("data", []) or []
            logger.info(f"[{self.region}]get {len(records)} records (page {page_no}).")
            if not records:
                break
            for record in records:
                href = record.get('infoPath') or ''
                if not href:
                    continue
                if not href.startswith('http'):
                    href = 'http://218.60.151.59:9004/' + href
                if href in self.exists_urls or href in self.tenders:
                    continue
                tender = Tender(
                    region=self.region,
                    href=href,
                    title=record.get('title', ''),
                    release_date=record.get('releaseDate', ''),
                    html=record.get('content', ''),
                    crawl_date=self._get_crawl_date(),
                )
                self.save_tender_to_es(tender)
                self.tenders[href] = tender
                self._random_sleep(_max=3)
            if len(records) < self.max_records:
                break
        logger.info(f"[{self.region}]crawl done, new {len(self.tenders)} tenders.")


if __name__ == '__main__':
    LiaoNingResult().run()
