from crawler.base_crawler import BaseCrawler, Tender
from utils.log import logger


class FuJian(BaseCrawler):
    """福建省公共资源交易电子公共服务平台（https://ggzyfw.fj.gov.cn）
    注：任务给定的 index/new#/404 为 SPA 404 兜底路由（显示新闻动态），
    真实交易公告列表入口为 /business/list（点击导航"交易信息"跳转），故以此作为列表页。
    详情页在新标签页打开，正文容器为 #bodyDiv。
    翻页：点击列表页底部分页器（.pagination ul.el-pager li.number，页码为 1-based）触发异步加载。
    """

    def __init__(self):
        super().__init__('fujian', max_page_num=None)
        self.index_url = 'https://ggzyfw.fj.gov.cn/business/list'
        self.max_pages = 50  # 默认抓取前 50 页（或累计 500 条，取先到者）
        # 列表页筛选（label 文本 -> 点击项）：交易类型=政府采购、信息类型=采购/资格预审公告、行业类型=服务类
        self.filter_labels = ['政府采购', '采购/资格预审公告', '服务类']

    def _crawl(self, context):
        page = context.new_page()
        page.goto(self.index_url, wait_until='domcontentloaded', timeout=60000)
        page.wait_for_selector('div.list-item', timeout=30000)
        # 切换筛选：政府采购 + 采购/资格预审公告 + 服务类（默认列表是工程建设，需切换）
        for label in self.filter_labels:
            loc = page.locator(f'label:has-text("{label}")').first
            if loc.count():
                loc.click()
                page.wait_for_timeout(2000)
        # 列表为异步加载，等待筛选后列表项渲染
        try:
            page.wait_for_selector('div.list-item a.title', timeout=15000)
        except Exception:
            pass
        page.wait_for_timeout(2000)
        # 先在列表页解析全部数据（locator 在页面导航后会失效，不能边导航边遍历）
        records = []
        stop_paging = False
        for page_no in range(self.max_pages):
            page.wait_for_timeout(1000)
            items = page.locator('div.list-item').all()
            for item in items:
                a = item.locator('a.title').first
                if not a.count():
                    continue
                href = a.get_attribute('href')
                if not href:
                    continue
                if not href.startswith('http'):
                    href = 'https://ggzyfw.fj.gov.cn' + href
                title = (a.get_attribute('title') or a.inner_text() or '').strip()
                date_text = ''
                if item.locator('label.time').count():
                    date_text = (item.locator('label.time').first.inner_text() or '').strip()
                records.append((href, title, date_text))
                # by_date 模式：列表按时间倒序，遇早于目标日即停止翻页（后续页都是更早的）
                if getattr(self, '_date_start', '') and date_text and date_text < self._date_start:
                    stop_paging = True
            if not records:
                break
            if stop_paging:
                break
            # 翻页：点击下一页页码（分页器为 el-pagination，页码 li.number 为 1-based）
            next_page = page_no + 2
            next_link = page.locator(f'.pagination ul.el-pager li.number:text-is("{next_page}")').first
            if not next_link.count():
                break
            # 记录点击前当前页首行标题，用于等待新页数据渲染
            prev_first_title = ''
            first_a = page.locator('div.list-item a.title').first
            if first_a.count():
                prev_first_title = (first_a.get_attribute('title') or first_a.inner_text() or '').strip()
            next_link.click(timeout=10000)
            # 等待新页数据渲染；站点偶发慢导致 wait_for_function 超时（30s），
            # 重试 2 次后再放弃翻页（已收集的 records 仍会处理）
            loaded = False
            for _ in range(3):
                try:
                    page.wait_for_function(
                        """([sel, prev]) => {
                            const el = document.querySelector(sel);
                            if (!el) return false;
                            const t = (el.getAttribute('title') || el.innerText || '').trim();
                            return !!t && t !== prev;
                        }""",
                        arg=['div.list-item a.title', prev_first_title],
                        timeout=30000,
                    )
                    loaded = True
                    break
                except Exception:
                    page.wait_for_timeout(2000)
            if not loaded:
                break
        logger.info(f"[{self.region}]get {len(records)} records from list page")
        for href, title, date_text in records:
            if href in self.exists_urls:
                continue
            try:
                detail_page = context.new_page()
                detail_page.goto(href, wait_until='domcontentloaded', timeout=60000)
                html = self._fetch_detail_content(detail_page)
                detail_page.close()
                tender = Tender(self.region, href, title, date_text, html, self._get_crawl_date())
                # 结果采集器预设公告类型（中标公告标题常无"中标/结果"字样，标题分类会误判为招标）
                if getattr(self, 'preset_announce_type', ''):
                    tender.announce_type = self.preset_announce_type
                self.save_tender_to_es(tender)
                self.tenders[href] = tender
            except Exception as e:
                logger.error(f"[{self.region}]detail failed {href}: {e}")
                continue
            self._random_sleep(_max=3)
        page.close()
        logger.info(f"[{self.region}]crawl done, new {len(self.tenders)} tenders.")

    def _fetch_detail_content(self, detail_page):
        """抓取详情页正文（政府采购详情页正文在 Element UI 折叠面板 el-collapse-item）。

        默认点击首个折叠标题展开后正文出现于 .el-collapse-item__content；
        兼容旧版 div.content 直出模板。
        """
        detail_page.wait_for_timeout(2000)
        if detail_page.locator('.el-collapse-item__header').count():
            detail_page.locator('.el-collapse-item__header').first.click(timeout=10000)
            detail_page.wait_for_function(
                "() => (document.querySelector('.el-collapse-item__content')?.innerText || '').trim().length > 100",
                timeout=30000,
            )
            return detail_page.locator('.el-collapse-item__content').first.inner_html()
        detail_page.wait_for_selector('div.content', state='attached', timeout=30000)
        detail_page.wait_for_function(
            "() => (document.querySelector('div.content')?.innerText || '').trim().length > 100",
            timeout=30000,
        )
        return detail_page.locator('div.content').inner_html()


if __name__ == '__main__':
    FuJian().run()
