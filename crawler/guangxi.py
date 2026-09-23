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
        # 风控缓解参数（广西对请求频率敏感，间歇性 429 验证码 + 高频触发 IP 403）
        self.detail_retries = 2            # 详情被风控时的重试次数
        self.detail_retry_cooldown = 15    # 每次风控后的冷却秒数
        self.max_consecutive_blocked = 5   # 连续风控条数上限，超过即熔断停止本轮
        self.detail_sleep_range = (2, 5)   # 详情请求之间随机间隔（秒）
        self.page_sleep_range = (1, 3)     # 列表翻页之间随机间隔（秒）

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
        blocked_streak = 0  # 连续被风控条数（熔断保护：避免持续请求触发 IP 403）
        # 分页循环：翻页深度由日期判断决定（guarded_save 遇早于目标日停止）
        for page_no in range(1, self.max_pages + 1):
            self.list_body.update({'pageNo': page_no})
            response = context.request.post(
                self.list_url,
                data=json.dumps(self.list_body),
                headers={**self.list_headers, 'Content-Type': 'application/json;charset=UTF-8'},
            )
            data = response.json()
            result = data.get('result')
            if not isinstance(result, dict):  # 风控时 result 为字符串（如 "cc"）
                logger.error(f"[{self.region}]列表接口风控拦截，提前停止翻页")
                break
            records = (result.get('data') or {}).get('data') or []
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
                if detail is None:
                    # 风控拦截：累计熔断，保护 IP 不被持续请求打成 403
                    blocked_streak += 1
                    if blocked_streak >= self.max_consecutive_blocked:
                        logger.error(f"[{self.region}]连续 {blocked_streak} 条详情被风控，熔断停止本轮")
                        return
                    continue
                if not detail:
                    continue  # 无正文等非风控失败，跳过该条
                blocked_streak = 0
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
                self._random_sleep(*self.detail_sleep_range)
            self._random_sleep(*self.page_sleep_range)  # 翻页间隔，避免列表接口高频触发
        logger.info(f"[{self.region}]crawl done, new {len(self.tenders)} tenders.")

    def _get_detail(self, context, article_id):
        """获取详情：识别验证码风控（HTTP 429 / result 为字符串 / NEED_CAPTCHA）。

        风控命中时冷却后重试，仍失败返回 None（上层据此熔断/跳过）；
        非风控失败返回 {}（跳过该条）。
        """
        for attempt in range(self.detail_retries + 1):
            ts = int(time.time() * 1000)
            url = f"{self.detail_url}?articleId={quote(article_id, safe='')}&parentId={self.parent_id}&timestamp={ts}"
            try:
                resp = context.request.get(url, headers={'Referer': self.index_url})
                if resp.status == 429:
                    logger.warning(f"[{self.region}]详情接口风控(429) {article_id[:8]}，冷却 {self.detail_retry_cooldown}s 后第 {attempt + 1} 次重试")
                    time.sleep(self.detail_retry_cooldown)
                    continue
                data = resp.json()
                result = data.get('result')
                if data.get('code') == 'NEED_CAPTCHA' or not isinstance(result, dict):
                    logger.warning(f"[{self.region}]详情接口验证码拦截 {article_id[:8]}，冷却 {self.detail_retry_cooldown}s 后第 {attempt + 1} 次重试")
                    time.sleep(self.detail_retry_cooldown)
                    continue
                return result.get('data') or {}
            except Exception as e:
                if attempt < self.detail_retries:
                    logger.warning(f"[{self.region}]get detail retry {attempt + 1} for {article_id[:8]}: {e}")
                    time.sleep(1 + attempt)
                else:
                    logger.error(f"get tender detail failed for {article_id}: {e}")
                    return {}
        logger.warning(f"[{self.region}]详情持续被风控，跳过 {article_id[:8]}")
        return None

    @staticmethod
    def _fmt_date(ms):
        try:
            return datetime.fromtimestamp(int(ms) / 1000).strftime('%Y-%m-%d')
        except Exception:
            return ''


if __name__ == '__main__':
    GuangXi().run()
