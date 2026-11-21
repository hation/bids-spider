"""广西中标/废标公告采集器（政采云 portal 接口直连，复用 GuangXi 风控缓解）

列表：POST /portal/category，categoryCode=ZcyAnnouncement2（结果公告栏目，混合
      中标"成交公告"与废标/终止公告，title 关键词自动分类：含废标/流标/终止/失败→废标，
      含中标/成交/结果→中标，由 _enrich_meta 的 classify_announce_type 完成）。
详情：GET /portal/detail?articleId=&parentId=66485&timestamp=，复用 GuangXi._get_detail
      的 429 / NEED_CAPTCHA 风控识别与熔断保护。
注意：广西对请求频率敏感，间歇性 429 验证码 + 高频触发 IP 403；本采集器保持低频节奏。
"""
import json
import time

from crawler.base_crawler import Tender
from crawler.guangxi import GuangXi
from utils.log import logger

CATEGORY_CODE = 'ZcyAnnouncement2'


class GuangXiResult(GuangXi):
    """广西结果公告采集器：接口直连结果公告栏目，标题关键词自动分类中标/废标。"""

    def _crawl(self, context):
        self._crawl_api(context)

    def _crawl_api(self, context):
        blocked_streak = 0
        for page_no in range(1, self.max_pages + 1):
            body = {'pageNo': page_no, 'pageSize': 15, 'categoryCode': CATEGORY_CODE,
                    '_t': int(time.time() * 1000)}
            headers = {
                'User-Agent': ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                               'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/158.0.0.0 Safari/537.36'),
                'Content-Type': 'application/json;charset=UTF-8',
                'Accept': 'application/json, text/plain, */*',
                'X-Requested-With': 'XMLHttpRequest',
                'Origin': 'https://zfcg.gxzf.gov.cn',
                'Referer': ('https://zfcg.gxzf.gov.cn/site/category?parentId=66485'
                            '&childrenCode=ZcyAnnouncement2'),
            }
            try:
                response = context.request.post(
                    self.list_url, data=json.dumps(body), headers=headers, timeout=30000)
                data = response.json()
            except Exception as e:
                logger.error(f"[{self.region}]list request failed (page {page_no}): {e}")
                break
            result = data.get('result')
            if not isinstance(result, dict):  # 风控时 result 为字符串（如 "cc"/"sp"）
                logger.error(f"[{self.region}]列表接口风控拦截（{result}），提前停止翻页")
                break
            records = (result.get('data') or {}).get('data') or []
            logger.info(f"[{self.region}] get {len(records)} records (page {page_no}).")
            if not records:
                break
            for rec in records:
                article_id = rec.get('articleId') or ''
                if not article_id:
                    continue
                href = (f'https://zfcg.gxzf.gov.cn/site/detail?parentId={self.parent_id}'
                        f'&articleId={self._quote(article_id)}')
                if href in self.exists_urls or href in self.tenders:
                    continue
                detail = self._get_detail(context, article_id)
                if detail is None:
                    blocked_streak += 1
                    if blocked_streak >= self.max_consecutive_blocked:
                        logger.error(f"[{self.region}]连续 {blocked_streak} 条详情被风控，熔断停止本轮")
                        return
                    continue
                if not detail:
                    continue
                blocked_streak = 0
                title = detail.get('title') or rec.get('title') or ''
                tender = Tender(
                    region=self.region, href=href, title=title,
                    release_date=self._fmt_date(detail.get('publishDate') or rec.get('publishDate')),
                    html=detail.get('content', ''), crawl_date=self._get_crawl_date(),
                )
                # announce_type 留空，由 _enrich_meta 按标题关键词分类
                self.save_tender_to_es(tender)
                self.tenders[href] = tender
                self._random_sleep(*self.detail_sleep_range)
            self._random_sleep(*self.page_sleep_range)
        logger.info(f"[{self.region}]crawl done, new {len(self.tenders)} tenders.")

    @staticmethod
    def _quote(article_id):
        from urllib.parse import quote
        return quote(article_id, safe='')


if __name__ == '__main__':
    GuangXiResult().run()
