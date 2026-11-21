"""内蒙古中标公告采集器（searchPublishResource 接口直连 + Playwright 详情）

列表：GET /trssearch/openSearch/searchPublishResource
  transactionTypeName=政府采购 + noticeTypeName=中标公告（页面切换"政府采购"后公告类型为
  "采购"或"中标公告"；中标公告 noticeTypeName 实测有效，采购公告=采购/资格预审公告）。
记录：sourceDataKey / noticeName / noticeSendTime（YYYY-MM-DD HH:MM:SS）。
详情：index_39.html?id=<sourceDataKey>，正文 #cont_text 由 JS 异步渲染（requests 拿不到），
  用 Playwright 打开等待渲染；正文含"供应商名称|中标（成交）金额|评审总得分"表格。
类型：本采集器只抓中标公告栏目，预设 announce_type=中标公告。
"""
import requests

from crawler.base_crawler import Tender
from crawler.neimenggu import NeiMengGu
from utils.log import logger

# 中标公告 noticeTypeName（URL 编码：中标公告）
NOTICE_TYPE = '%E4%B8%AD%E6%A0%87%E5%85%AC%E5%91%8A'


class NeiMengGuResult(NeiMengGu):
    """内蒙古中标公告采集器：接口直连抓中标公告，详情用 Playwright 抓 #cont_text。"""

    def _crawl(self, context):
        for page_num in range(1, self.max_pages + 1):
            api_url = self.api_url.replace('pageNum=1', f'pageNum={page_num}')
            # 换 noticeTypeName 为中标公告（原为采购/资格预审公告）
            api_url = api_url.replace(
                'noticeTypeName=%E9%87%87%E8%B4%AD%2F%E8%B5%84%E6%A0%BC%E9%A2%84%E5%AE%A1%E5%85%AC%E5%91%8A',
                f'noticeTypeName={NOTICE_TYPE}')
            try:
                response = requests.get(api_url, headers=self.headers, timeout=30)
                data = response.json()
            except Exception as e:
                logger.error(f"[{self.region}]get list json failed: {e}")
                break
            if data.get("state") != 200:
                logger.error(f"[{self.region}]get list failed: {data}")
                break
            records = (data.get("data", {}) or {}).get("data", []) or []
            logger.info(f"[{self.region}]get {len(records)} tenders list success (page {page_num}).")
            if not records:
                break
            for record in records:
                source_data_key = record.get('sourceDataKey') or ''
                if not source_data_key:
                    continue
                href = 'https://ggzyjy.nmg.gov.cn/jyxx/index_39.html?id=' + source_data_key
                if href in self.exists_urls or href in self.tenders:
                    continue
                html = self._get_detail(context, href)
                tender = Tender(
                    region=self.region,
                    href=href,
                    title=record.get('noticeName', ''),
                    release_date=(record.get('noticeSendTime') or '')[:10],
                    html=html,
                    crawl_date=self._get_crawl_date(),
                )
                tender.announce_type = '中标公告'
                self.save_tender_to_es(tender)
                self.tenders[href] = tender
                self._random_sleep(_max=3)
        logger.info(f"[{self.region}]crawl done, new {len(self.tenders)} tenders.")


if __name__ == '__main__':
    NeiMengGuResult().run()
