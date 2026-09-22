import time
from urllib.parse import urlencode

from crawler.base_crawler import BaseCrawler, Tender
from utils.captcha import DdddOCR
from utils.log import logger


class ShaanXi(BaseCrawler):
    """陕西省采购网（freecms 平台）：列表需通过图片验证码。

    流程：加载列表页 -> 调用页面 refreshCode() 取验证码图 -> ddddocr 识别 ->
    填入并点击"查询"，捕获 selectInfoMoreChannel.do 的查询参数与响应，
    随后直连接口翻页（currPage）抓取多页；详情页取 noticeDetailUrl。
    """

    def __init__(self, max_pages=50):
        super().__init__('shaanxi', max_page_num=None)
        self.max_pages = max_pages
        self.max_items = 5000
        self.list_url = 'https://www.ccgp-shaanxi.gov.cn/cms-sx/site/shanxi/xxgg/index.html?result=result'
        self.list_api = 'https://www.ccgp-shaanxi.gov.cn/freecms/rest/v1/notice/selectInfoMoreChannel.do'
        self.base_url = 'https://www.ccgp-shaanxi.gov.cn'
        self.ocr = DdddOCR()

    def _crawl(self, context):
        page = context.new_page()
        page.goto(self.list_url, wait_until='domcontentloaded', timeout=60000)
        page.wait_for_timeout(5000)

        code, query, _ = self._solve_and_fetch_first_page(context, page)
        page.close()
        if not query:
            logger.error(f"[{self.region}]验证码多次尝试后仍无法获取列表")
            return
        self._get_tenders(context, code, query)

    def _captcha_image_url(self, page):
        """调用页面 refreshCode 获取验证码图片 src"""
        page.evaluate("() => { if (typeof refreshCode === 'function') refreshCode(); }")
        page.wait_for_timeout(1500)
        src = page.locator('#code_img').get_attribute('src') or ''
        if not src:
            return ''
        return src if src.startswith('http') else self.base_url + src

    def _solve_and_fetch_first_page(self, context, page):
        for attempt in range(6):
            src = self._captcha_image_url(page)
            if not src:
                continue
            try:
                img = context.request.get(src).body()
            except Exception as e:
                logger.warning(f"[{self.region}]获取验证码图片失败: {e}")
                continue
            code = self.ocr.recognize_bytes(img)
            page.fill('#verifycode', code)
            try:
                with page.expect_response(
                        lambda r: 'selectInfoMoreChannel.do' in r.url and r.status == 200, timeout=15000) as resp_info:
                    for sel in ("button:has-text('查询')", "input[value='查询']"):
                        if page.locator(sel).count():
                            page.locator(sel).first.click()
                            break
                response = resp_info.value
                data = response.json()
                rows = data.get('data') or []
                query = self._parse_query(response.url)
            except Exception:
                rows = []
                query = {}
            if rows:
                logger.info(f"[{self.region}]验证码 {code!r} 通过，第1页 {len(rows)} 条")
                return code, query, rows
            logger.warning(f"[{self.region}]验证码 {code!r} 未通过（尝试 {attempt + 1}/6）")
        return None, {}, []

    @staticmethod
    def _parse_query(url):
        query = {}
        for pair in url.split('?', 1)[-1].split('&'):
            if '=' in pair:
                k, _, v = pair.partition('=')
                query[k] = v
        return query

    def _fetch_page(self, context, query, verify_code, page_no):
        # 去掉时间过滤参数，返回全部公告；保留其余站点参数
        params = {k: v for k, v in query.items()
                  if k not in ('currPage', 'pageSize', 'verifyCode',
                               'operationStartTime', 'operationEndTime', 'selectTimeName')}
        params.update({'currPage': page_no, 'pageSize': 10, 'verifyCode': verify_code})
        url = self.list_api + '?' + urlencode(params)
        resp = context.request.get(url, headers={'Referer': self.list_url})
        try:
            data = resp.json()
        except Exception as e:
            logger.warning(f"[{self.region}]list api json parse failed: {e}")
            return []
        return data.get('data') or []

    def _get_tenders(self, context, code, query):
        page_no = 1
        total = 0
        while page_no <= self.max_pages and total < self.max_items:
            rows = self._fetch_page(context, query, code, page_no)
            if not rows:
                break
            logger.info(f"[{self.region}]第 {page_no} 页 {len(rows)} 条")
            for record in rows:
                if total >= self.max_items:
                    break
                title = (record.get('title') or '').strip()
                href = record.get('noticeDetailUrl') or record.get('url') or record.get('pageurl') or ''
                if not href or not title:
                    continue
                if not href.startswith('http'):
                    href = self.base_url + href
                if href in self.exists_urls or href in self.tenders:
                    continue
                html = self._get_detail(context, href)
                tender = Tender(
                    region=self.region,
                    href=href,
                    title=title,
                    release_date=(record.get('noticeTime') or '')[:10],
                    html=html,
                    crawl_date=self._get_crawl_date(),
                )
                self.save_tender_to_es(tender)
                self.tenders[href] = tender
                total += 1
                self._random_sleep(_max=3)
            if len(rows) < 10:
                break
            page_no += 1
        logger.info(f"[{self.region}]crawl done, new {len(self.tenders)} tenders.")

    def _get_detail(self, context, href):
        try:
            with context.new_page() as detail_page:
                detail_page.goto(href, wait_until='domcontentloaded', timeout=60000)
                detail_page.wait_for_timeout(3000)
                for sel in ('div.info-article', 'div.wrap_content_detail', 'div.protect'):
                    locator = detail_page.locator(sel).first
                    if locator.count() and locator.inner_text().strip():
                        return locator.inner_html()
                return detail_page.content()
        except Exception as e:
            logger.error(f"[{self.region}]parse detail failed {href}: {e}")
            return ''


if __name__ == '__main__':
    ShaanXi().run()
