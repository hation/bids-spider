"""云南中标/废标公告采集器（列表接口 query_type 区分栏目）

云南为 SPA，列表走 POST 接口 Procurement.gghtMoreList.svc，分栏参数为 query_type：
- query_type=2  -> 结果公告（成交公告） -> announce_type=中标公告
- query_type=10 -> 终止公告（含废标）    -> announce_type=废标公告

要点（实测 2026-11-21）：
- 接口必须带页面会话 cookie（xincaigou/route/JSESSIONID），直接 POST 返回 500，
  故先打开列表页种 cookie 再调 context.request.post（Playwright 同源自动带 cookie）。
- body 的 rowCount 必须为 10（页面默认值），传其他值返回空/500。
- 支持 query_startTime / query_endTime 按日期过滤（run_by_date 时填目标日）。
- 详情页 ggmxinfo.html?bulletinid= 重定向到 showBulletinInfo.html?bulletin_id=，
  中标单位在正文表格（div.blobcontent / PlaygroundEditorTheme__table），
  废标原因在 div.vF_detail_content 折叠区，现有 yunnan.py 的 parse_detail 已覆盖两者。
"""
from crawler.yunnan import YunNan
from crawler.base_crawler import Tender
from utils.log import logger

# (query_type, announce_type 预设)
RESULT_TYPES = [
    ('2', '中标公告'),
    ('10', '废标公告'),
]


class YunNanResult(YunNan):
    """云南结果公告采集器：列表接口按 query_type 抓结果/终止公告，预设类型。"""

    def _crawl(self, context):
        page = context.new_page()
        page.goto(self.index_url, wait_until='domcontentloaded', timeout=60000)
        page.wait_for_selector('a[data-bulletin_id]', timeout=30000)
        page.wait_for_timeout(2000)  # 等待列表渲染并种下会话 cookie
        for query_type, atype in RESULT_TYPES:
            self._crawl_type(context, page, query_type, atype)
        page.close()
        logger.info(f"[{self.region}]crawl done, new {len(self.tenders)} tenders.")

    def _crawl_type(self, context, page, query_type, atype):
        start = getattr(self, '_date_start', '') or ''
        end = getattr(self, '_date_end', '') or ''
        for p in range(1, self.max_pages + 1):
            api_url = self.api_url.replace('p=1', f'p={p}')
            body = ("current={p}&rowCount=10&searchPhrase=&query_bulletintitle=&query_startTime={s}"
                    "&query_endTime={e}&query_type={t}&query_code=&query_gglxdm=&query_purchaser="
                    "&query_projectid=&query_code_qx=").format(
                        p=p, s=start, e=end, t=query_type)
            response = context.request.post(
                api_url,
                headers={'Referer': self.index_url, 'X-Requested-With': 'XMLHttpRequest',
                         'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8'},
                data=body,
            )
            if response.status != 200:
                logger.warning(f"[{self.region}] query_type={query_type} page {p} blocked "
                               f"(status {response.status}), stop paging.")
                break
            try:
                data = response.json()
            except Exception as e:
                logger.warning(f"[{self.region}] query_type={query_type} page {p} not json ({e}), stop.")
                break
            rows = (data.get('data') or {}).get('rows') or []
            logger.info(f"[{self.region}] query_type={query_type} get {len(rows)} tenders from api (page {p}).")
            if not rows:
                break
            for row in rows:
                bid = row.get('bulletin_id', '')
                if not bid:
                    continue
                href = f"http://www.yngp.com/ggmxinfo.html?bulletinid={bid}"
                if href in self.exists_urls or href in self.tenders:
                    continue
                tender = Tender(self.region, href, row.get('bulletintitle', ''),
                                row.get('finishday', '') or row.get('beginday', ''))
                tender.announce_type = atype
                try:
                    tender.html = self._execute_by_new_page(context, href, self.parse_detail)
                except Exception as e:
                    logger.error(f"[{self.region}]parse detail failed {href}: {e}")
                tender.crawl_date = self._get_crawl_date()
                self.save_tender_to_es(tender)
                self.tenders[href] = tender
                self._random_sleep(_max=3)
            if len(rows) < 10:
                break

    def parse_detail(self, page):
        """云南结果公告详情页正文：正文（含中标供应商/金额）由 JS 异步渲染进
        div.vF_detail_content 与 div.blobcontent，需等待渲染后再抓。

        vF_detail_content 含公告正文（中标（成交）信息/废标原因），
        blobcontent 含结构化字段（成交供应商/总成交金额/采购单位）。
        """
        try:
            page.wait_for_function(
                """() => {
                    const v = document.querySelector('div.vF_detail_content');
                    const b = document.querySelector('div.blobcontent');
                    return (v && (v.innerText || '').trim().length > 30)
                        || (b && (b.innerText || '').trim().length > 30);
                }""",
                timeout=20000,
            )
        except Exception:
            pass
        return page.evaluate("""() => {
            const v = document.querySelector('div.vF_detail_content');
            const b = document.querySelector('div.blobcontent');
            let html = (v ? v.innerHTML : '');
            if (b) html += '<div class="blobcontent">' + b.innerHTML + '</div>';
            return html;
        }""")


if __name__ == '__main__':
    YunNanResult().run()
