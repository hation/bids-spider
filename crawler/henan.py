import re

from crawler.base_crawler import BaseCrawler, Tender
from utils.captcha import DdddOCR
from utils.log import logger


class HeNan(BaseCrawler):
    """河南省采购网：查询前需通过图片验证码。

    流程：加载查询页 -> ddddocr 识别验证码 -> 填写并点击"查询"，
    成功后跳转到 ggcx?soCode=<会话> 结果页，解析 /henan/content 公告链接；
    通过结果页分页控件翻页。
    """

    def __init__(self, max_pages=50):
        super().__init__('henan', max_page_num=None)
        self.max_pages = max_pages
        self.max_items = 5000
        self.list_url = 'http://www.ccgp-henan.gov.cn/henan/ggcx'
        self.base_url = 'http://www.ccgp-henan.gov.cn'
        self.ocr = DdddOCR()

    def _crawl(self, context):
        page = context.new_page()
        page.goto(self.list_url, wait_until='domcontentloaded', timeout=60000)
        page.wait_for_timeout(5000)

        if not self._solve_and_submit(page):
            page.close()
            logger.error(f"[{self.region}]验证码多次尝试后仍无法查询")
            return

        seen_urls = set()
        total = 0
        for page_no in range(1, self.max_pages + 1):
            if total >= self.max_items:
                break
            records = self._parse_results(page)
            if not records:
                break
            logger.info(f"[{self.region}]第 {page_no} 页 {len(records)} 条")
            for href, title, date in records:
                if href in seen_urls or total >= self.max_items:
                    continue
                seen_urls.add(href)
                if href in self.exists_urls or href in self.tenders:
                    continue
                html = self._get_detail(context, href)
                tender = Tender(
                    region=self.region,
                    href=href,
                    title=title,
                    release_date=date,
                    html=html,
                    crawl_date=self._get_crawl_date(),
                )
                self.save_tender_to_es(tender)
                self.tenders[href] = tender
                total += 1
                self._random_sleep(_max=3)
            if not self._goto_next_page(page, page_no):
                break
        page.close()
        logger.info(f"[{self.region}]crawl done, new {len(self.tenders)} tenders.")

    def _solve_and_submit(self, page):
        """识别验证码并提交查询，成功（结果列表出现）返回 True。

        验证码图必须取「页面实际加载的响应体」（监听 getImage 响应）：
        不能 request.get(src) 重新请求——服务端每次 GET /getImage 会生成新图，
        与页面当前校验的图错位一拍，导致识别永远提交失败（手动填写能过即此原因）。
        """
        import time
        captcha = {'img': None}

        def on_response(resp):
            if '/getImage/' in resp.url:
                try:
                    captcha['img'] = resp.body()
                except Exception:
                    pass

        page.on('response', on_response)
        for attempt in range(8):
            # 点击刷新触发 getImage 并等待响应捕获（页面加载时捕获过的旧图不可复用）
            page.locator('#recode').click()
            deadline = time.time() + 3
            captcha['img'] = None
            while not captcha['img'] and time.time() < deadline:
                page.wait_for_timeout(100)
            img = captcha['img']
            if not img:
                logger.warning(f"[{self.region}]未捕获到验证码图片（尝试 {attempt + 1}/8）")
                continue
            code = self.ocr.recognize_bytes(img)
            page.fill('input[name=code]', code)
            page.locator("button:has-text('查询')").first.click()
            # 验证码正确才会渲染结果列表（soCode 出现不代表成功）
            try:
                page.wait_for_selector('a[href*="/henan/content?infoId="]', timeout=5000)
                logger.info(f"[{self.region}]验证码 {code!r} 通过")
                return True
            except Exception:
                logger.warning(f"[{self.region}]验证码 {code!r} 未通过（尝试 {attempt + 1}/8）")
        return False

    @staticmethod
    def _parse_results(page):
        """解析结果页公告：返回 [(href, title, date)]"""
        page.wait_for_selector('a[href*="/henan/content?infoId="]', timeout=15000)
        records = []
        links = page.locator('a[href*="/henan/content?infoId="]').all()
        for a in links:
            href = a.get_attribute('href') or ''
            title = (a.inner_text() or '').strip()
            if not href or not title:
                continue
            if href.startswith('/'):
                href = 'https://zfcg.henan.gov.cn' + href
            date = ''
            # 从链接所在行/卡片找日期文本：优先 tr/li 行容器，
            # 兜底向上最多 4 层找包含日期的容器（兼容 div 卡片等非表格布局）
            try:
                tr = a.evaluate("""el => {
                    const row = el.closest('tr') || el.closest('li');
                    if (row) return row.innerText || '';
                    let node = el.parentElement;
                    for (let i = 0; node && i < 4; i++, node = node.parentElement) {
                        const t = node.innerText || '';
                        if (/\\d{4}-\\d{2}-\\d{2}/.test(t)) return t;
                    }
                    return '';
                }""")
            except Exception:
                tr = ''
            m = re.search(r'(\d{4}-\d{2}-\d{2})', tr)
            if m:
                date = m.group(1)
            records.append((href, title, date))
        return records

    def _goto_next_page(self, page, current_page_no):
        """点击下一页/页码链接，成功返回 True"""
        next_link = None
        # 常见分页器：含"下一页"文本的链接
        candidates = page.locator('a:has-text("下一页"), a:has-text("下页"), a:has-text("»")').all()
        for a in candidates:
            cls = (a.get_attribute('class') or '') + ' ' + (a.get_attribute('onclick') or '')
            if 'disabled' in cls or 'current' in cls:
                continue
            next_link = a
            break
        if not next_link:
            # 页码数字链接
            for num in range(current_page_no + 1, current_page_no + 3):
                links = page.locator(f'a:text-is("{num}")').all()
                if links:
                    next_link = links[0]
                    break
        if not next_link:
            return False
        try:
            next_link.click(timeout=10000)
            page.wait_for_timeout(3000)
            return True
        except Exception as e:
            logger.warning(f"[{self.region}]翻页失败: {e}")
            return False

    def _get_detail(self, context, href):
        try:
            with context.new_page() as detail_page:
                detail_page.goto(href, wait_until='domcontentloaded', timeout=60000)
                detail_page.wait_for_timeout(3000)
                for sel in ('div.article', 'div.content', 'div.detail', '.article-content', '#content'):
                    locator = detail_page.locator(sel).first
                    if locator.count() and locator.inner_text().strip():
                        return locator.inner_html()
                return detail_page.content()
        except Exception as e:
            logger.error(f"[{self.region}]parse detail failed {href}: {e}")
            return ''


if __name__ == '__main__':
    HeNan().run()
