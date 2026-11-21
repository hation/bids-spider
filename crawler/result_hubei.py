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

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

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
        if not self._query_and_wait(page, gglx, start, end):
            logger.warning(f"[{self.region}]{gglx} 多次查询失败（站点限速），跳过本类型")
            return
        for page_no in range(1, self.max_pages + 1):
            items = self._parse_list(page)
            logger.info(f"[{self.region}]{gglx} get {len(items)} records (page {page_no}).")
            if not items:
                break
            for it in items:
                href = it['href']
                title = it['title']
                date = it['date']
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

    def _query_and_wait(self, page, gglx, start, end):
        """填表单提交查询，等待真实列表渲染；站点间歇性返回"查询失败"（限速），最多重试 3 次。"""
        for attempt in range(1, 4):
            page.goto(SEARCH_PAGE, wait_until='domcontentloaded', timeout=60000)
            page.wait_for_selector('form#noticeForm', timeout=30000)
            page.select_option('select[name="queryInfo.gglx"]', gglx)
            if start:
                page.fill('input[name="queryInfo.begin"]', start)
                page.fill('input[name="queryInfo.end"]', end)
            page.click('form#noticeForm button:has-text("查询")')
            try:
                page.wait_for_load_state('networkidle', timeout=30000)
            except Exception:
                pass
            body = page.evaluate("() => (document.body.innerText || '').trim()")
            if '查询失败' in body:
                logger.warning(f"[{self.region}]{gglx} attempt {attempt} 查询失败（站点限速），60s 后重试")
                page.wait_for_timeout(60000)
                continue
            try:
                page.wait_for_selector('li.serach-page-results-item', timeout=30000)
                self._wait_real_items(page)
            except Exception as e:
                logger.warning(f"[{self.region}]{gglx} attempt {attempt} 列表未渲染: {e}")
                page.wait_for_timeout(60000)
                continue
            return True
        return False

    def _wait_real_items(self, page):
        """等待真实列表渲染完成（跳过页面骨架里的 JS 模板占位符）。"""
        page.wait_for_function("""() => {
            const a = document.querySelector('li.serach-page-results-item .title a');
            if (!a) return false;
            const t = (a.textContent || '').trim();
            const h = (a.getAttribute('href') || '').trim();
            return !!t && t !== 'title' && h !== 'href';
        }""", timeout=30000)

    def _parse_list(self, page):
        return page.evaluate("""() => {
            const out = [];
            document.querySelectorAll('li.serach-page-results-item').forEach(li => {
                const a = li.querySelector('.title a');
                if (!a) return;
                const href = (a.getAttribute('href') || '').trim();
                const title = (a.textContent || '').trim();
                const time = li.querySelector('.time');
                const date = time ? time.textContent.trim() : '';
                // 跳过页面骨架里的 JS 模板占位符（href="href" / title="title"）
                if (!href || !title || href === 'href' || title === 'title') return;
                if (href && title) out.push({href, title, date});
            });
            return out;
        }""")

    def _goto_next_page(self, page, current_page):
        """点击下一页，等待列表首行标题变化（页面骨架含占位符，须等真实标题）。"""
        prev_first = self._first_title(page)
        for attempt in range(1, 4):
            try:
                next_link = None
                # 1) 常见翻页链接
                for sel in [
                    'a[href*="pageNo=%d"]' % (current_page + 1),
                    'li.next a', 'a.next', 'a:has-text("下一页")', 'a:has-text("下页")',
                ]:
                    loc = page.locator(sel).first
                    if loc.count():
                        next_link = loc
                        break
                # 2) 兜底：结果页 pageNo 表单（hidden input），改页码后提交所在表单
                if not next_link:
                    page_no = page.locator('input[name="queryInfo.pageNo"], input[name*="pageNo"]').first
                    if page_no.count():
                        # hidden input 不可 fill，须 JS 直接设值再提交所在表单
                        page_no.evaluate("(el, v) => { el.value = v; }", str(current_page + 1))
                        if page_no.evaluate("(el) => !!el.closest('form')"):
                            page.evaluate("(el) => el.closest('form').submit()", page_no.element_handle())
                            next_link = 'form-submit'
                if not next_link:
                    return False
                if next_link != 'form-submit':
                    next_link.click(timeout=10000)
                try:
                    page.wait_for_load_state('networkidle', timeout=30000)
                except Exception:
                    pass
                body = page.evaluate("() => (document.body.innerText || '').trim()")
                if '查询失败' in body:
                    logger.warning(f"[{self.region}]goto page {current_page + 1} attempt {attempt} 查询失败（限速），60s 后重试")
                    page.wait_for_timeout(60000)
                    continue
                # 等真实列表（非模板占位）且首行标题变化；超时（标题未变）= 已翻过最后一页
                # （服务端钳制返回原页），非失败，直接结束翻页
                try:
                    page.wait_for_function(
                        """([sel, prev]) => {
                            const a = document.querySelector(sel);
                            if (!a) return false;
                            const t = (a.textContent || '').trim();
                            const h = (a.getAttribute('href') || '').trim();
                            return !!t && t !== 'title' && h !== 'href' && t !== prev;
                        }""",
                        arg=['li.serach-page-results-item .title a', prev_first],
                        timeout=20000,
                    )
                except PlaywrightTimeoutError:
                    return False  # 等标题变化超时 = 服务端钳制在末页，非失败，直接结束翻页
                page.wait_for_timeout(500)
                return True
            except Exception as e:
                logger.warning(f"[{self.region}]goto page {current_page + 1} attempt {attempt} failed: {e}")
                page.wait_for_timeout(30000)
        return False

    def _first_title(self, page):
        try:
            a = page.locator('li.serach-page-results-item .title a').first
            return a.inner_text().strip() if a.count() else ''
        except Exception:
            return ''

    def parse_detail(self, page):
        """详情正文容器 div.art_con（兼容父类 HuBei.parse_detail）。"""
        page.wait_for_selector('div.art_con', timeout=30000)
        return page.locator('div.art_con').inner_html()


if __name__ == '__main__':
    HuBeiResult().run()
