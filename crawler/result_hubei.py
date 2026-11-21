"""湖北中标/废标公告采集器（Playwright 驱动搜索表单）

湖北搜索为整页刷新（无 JSON 接口）：入口页 `quSer/searchXmgg.html`（表单 form#noticeForm，
POST 到 /quSer/search）。本采集器用 Playwright 打开入口页 → 填公告类型/日期 → 点查询 →
抓 DOM 列表（li.serach-page-results-item：.title a 标题+详情、.time 日期）→ 点分页翻页。
详情：80 端口静态页 http://www.ccgp-hubei.gov.cn/notice/YYYYMM/notice_<md5>.html，正文 div.art_con。
日期：YYYY/MM/DD 斜杠格式（连字符触发 500）。
注意：requests 直调在本机网络被风控（空结果页），Playwright 真实浏览器可靠（与浏览器代理实测一致）。
"""
import re
from urllib.parse import urljoin

from crawler.base_crawler import Tender
from crawler.hubei import HuBei
from utils.log import logger

SEARCH_PAGE = 'https://www.ccgp-hubei.gov.cn:9040/quSer/searchXmgg.html'

# (公告类型中文名, announce_type 预设)
GGLX = [
    ('中标（成交结果）公告', '中标公告'),
    ('终止公告', '废标公告'),
]


def _clean(s):
    return re.sub(r'<[^>]+>', '', s or '').strip()


class HuBeiResult(HuBei):
    """湖北结果公告采集器：Playwright 填搜索表单抓中标/废标公告。"""

    def _crawl(self, context):
        page = context.new_page()
        page.goto(SEARCH_PAGE, wait_until='domcontentloaded', timeout=60000)
        page.wait_for_selector('form#noticeForm', timeout=30000)
        page.wait_for_timeout(1000)
        start = (getattr(self, '_date_start', '') or '').replace('-', '/')
        end = (getattr(self, '_date_end', '') or '').replace('-', '/')
        for gglx, atype in GGLX:
            self._crawl_type(context, page, gglx, atype, start, end)
        page.close()
        logger.info(f"[{self.region}]crawl done, new {len(self.tenders)} tenders.")

    def _crawl_type(self, context, page, gglx, atype, start, end):
        # 重新加载入口页（每次类型独立，避免筛选残留）
        page.goto(SEARCH_PAGE, wait_until='domcontentloaded', timeout=60000)
        page.wait_for_selector('form#noticeForm', timeout=30000)
        # 填公告类型 select
        page.select_option('select[name="queryInfo.gglx"]', gglx)
        # 填日期（斜杠格式）
        if start:
            page.fill('input[name="queryInfo.begin"]', start)
            page.fill('input[name="queryInfo.end"]', end)
        # 点查询按钮（默认 submit，"查询"文本按钮）
        page.click('form#noticeForm button:has-text("查询")')
        page.wait_for_selector('li.serach-page-results-item', timeout=30000)
        page.wait_for_timeout(1000)
        for page_no in range(1, self.max_pages + 1):
            items = self._parse_list(page)
            logger.info(f"[{self.region}]{gglx} get {len(items)} records (page {page_no}).")
            if not items:
                break
            for href, title, date in items:
                href_full = urljoin(page.url, href)
                if href_full in self.exists_urls or href_full in self.tenders:
                    continue
                tender = Tender(self.region, href_full, title, date[:10])
                tender.announce_type = atype
                try:
                    tender.html = self._execute_by_new_page(context, href_full, self.parse_detail)
                except Exception as e:
                    logger.error(f"[{self.region}]parse detail failed {href_full}: {e}")
                tender.crawl_date = self._get_crawl_date()
                self.save_tender_to_es(tender)
                self.tenders[href_full] = tender
                self._random_sleep(_max=3)
            if not self._goto_next_page(page, page_no):
                break

    def _parse_list(self, page):
        return page.evaluate("""() => {
            const out = [];
            document.querySelectorAll('li.serach-page-results-item').forEach(li => {
                const a = li.querySelector('.title a');
                if (!a) return;
                const href = a.href || '';
                const title = (a.textContent || '').trim();
                const time = li.querySelector('.time');
                const date = time ? time.textContent.trim() : '';
                if (href && title) out.push({href, title, date});
            });
            return out;
        }""")

    def _goto_next_page(self, page, current_page):
        """点击下一页，等待列表首行标题变化。"""
        try:
            next_link = page.locator('li.next a, a[href*="pageNo=%d"]' % (current_page + 1)).first
            if not next_link.count():
                next_link = page.locator('a.next').first
            if not next_link.count():
                return False
            prev_first = self._first_title(page)
            next_link.click(timeout=10000)
            page.wait_for_function(
                """([sel, prev]) => {
                    const el = document.querySelector(sel);
                    if (!el) return false;
                    const t = (el.textContent || '').trim();
                    return !!t && t !== prev;
                }""",
                arg=['li.serach-page-results-item .title a', prev_first],
                timeout=20000,
            )
            page.wait_for_timeout(500)
            return True
        except Exception as e:
            logger.warning(f"[{self.region}]goto page {current_page + 1} failed: {e}")
            return False

    def _first_title(self, page):
        try:
            a = page.locator('li.serach-page-results-item .title a').first
            return a.inner_text().strip() if a.count() else ''
        except Exception:
            return ''

    def parse_detail(self, page):
        """详情正文容器 div.art_con（兼容父类 HuBei.parse_detail）。"""
        return page.locator('div.art_con').inner_html()


if __name__ == '__main__':
    HuBeiResult().run()
