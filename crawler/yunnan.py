import json

from crawler.base_crawler import BaseCrawler, Tender
from utils.log import logger


class YunNan(BaseCrawler):
    """云南省政府采购网：列表走 JSON 接口（Procurement.gghtMoreList.svc），详情打开详情页取正文。

    列表接口返回 rows：bulletin_id / bulletintitle / beginday / finishday / tabletype。
    默认第一页为"采购意向公开"（tabletype=23），详情页为 viewPurchaseInfo；
    其他公告类型回退到 ggmxinfo 详情页。
    注：分页实测（2026-09-18）p>=2 时服务端返回 406"验证码校验不通过"，
    仅 p=1 可正常返回，当前实际只能抓取单页；代码保留翻页循环，遇拦截时自动停止。
    """

    def __init__(self):
        super().__init__('yunnan', max_page_num=None)
        self.max_pages = 50  # 默认抓取前 50 页（或累计 500 条，取先到者）
        self.index_url = 'http://www.yngp.com/page/procurement/procurementList.html'
        self.api_url = 'http://www.yngp.com/api/procurement/Procurement.gghtMoreList.svc?captchaCheckFlag=0&p=1'

    def _crawl(self, context):
        page = context.new_page()
        page.goto(self.index_url, wait_until='domcontentloaded', timeout=60000)
        page.wait_for_selector('a[data-bulletin_id]', timeout=30000)
        page.wait_for_timeout(2000)  # 等待列表渲染完成

        # 分页循环：翻页深度由日期判断决定（guarded_save 遇早于目标日停止）
        for p in range(1, self.max_pages + 1):
            api_url = self.api_url.replace('p=1', f'p={p}')
            response = context.request.get(
                api_url,
                headers={'Referer': self.index_url, 'X-Requested-With': 'XMLHttpRequest'},
            )
            if response.status != 200:
                logger.warning(f"[{self.region}] page {p} blocked by anti-crawl (status {response.status}), stop paging.")
                break
            try:
                data = response.json()
            except Exception as e:
                logger.warning(f"[{self.region}] page {p} response not json ({e}), stop paging.")
                break
            rows = (data.get('data') or {}).get('rows') or []
            logger.info(f"[{self.region}] get {len(rows)} tenders from api (page {p}).")
            if not rows:
                break
            for row in rows:
                bid = row.get('bulletin_id', '')
                if not bid:
                    logger.warning("record has no bulletin_id, skip.")
                    continue
                tabletype = str(row.get('tabletype', ''))
                # 采购意向公开 -> viewPurchaseInfo；其他公告 -> ggmxinfo
                if tabletype == '23':
                    href = f"http://www.yngp.com/viewPurchaseInfo.html?sys_purchaseintention_id={bid}"
                else:
                    href = f"http://www.yngp.com/ggmxinfo.html?bulletinid={bid}"
                if href in self.exists_urls:
                    continue
                tender = Tender(self.region, href, row.get('bulletintitle', ''), row.get('beginday', ''))
                tender.html = self._execute_by_new_page(context, href, self.parse_detail)
                tender.crawl_date = self._get_crawl_date()
                self.save_tender_to_es(tender)
                self.tenders[href] = tender
                self._random_sleep(_max=3)
        logger.info(f"[{self.region}]crawl done, new {len(self.tenders)} tenders.")

    @staticmethod
    def parse_detail(page):
        """正文容器：ggmxinfo 的正文在隐藏的 div.vF_detail_content；viewPurchaseInfo 取内容最长的表格。"""
        return page.evaluate("""() => {
            const hidden = document.querySelector('div.vF_detail_content');
            if (hidden) return hidden.innerHTML;
            let best = null;
            for (const t of document.querySelectorAll('table')) {
                const len = (t.innerText || '').trim().length;
                if (len >= 30 && (!best || len > best.len)) best = {len: len, html: t.outerHTML};
            }
            return best ? best.html : '';
        }""")


if __name__ == '__main__':
    YunNan().run()
