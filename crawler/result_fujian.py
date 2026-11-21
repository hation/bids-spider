"""福建中标公告采集器（政府采购 + 中标公告 + 服务类）

福建公共资源交易平台列表/详情接口数据均加密，必须 Playwright 页面交互：
1. 列表页切筛选：政府采购(KIND=ZFCG) + 中标公告(GGTYPE=2) + 服务类(PROTYPE=D03)，
   列表 DOM `div.list-item`（与主爬虫一致，复用 FuJian 列表/翻页逻辑）。
2. 详情页 `el-tabs` 点"中标公告"tab → 展开 `el-collapse` 折叠项 → 拿"原文链接"
   （zfcg.czt.fujian.gov.cn 福建政府采购网文章页，正文容器 `#articleCont.articleMsg`，
   含中标供应商/金额表格）。
3. 类型预设为中标公告（列表栏目即中标公告）。
"""
import re

from crawler.base_crawler import Tender
from crawler.fujian import FuJian
from utils.log import logger


class FuJianResult(FuJian):
    """福建结果公告采集器：政府采购中标公告栏目，正文取福建政府采购网原文。"""

    def __init__(self):
        super().__init__()
        # 交易类型=政府采购、信息类型=中标公告、行业类型=服务类
        self.filter_labels = ['政府采购', '中标公告', '服务类']
        self.preset_announce_type = '中标公告'

    def _fetch_detail_content(self, detail_page):
        """点"中标公告"tab → 展开折叠项 → 跳转原文链接抓正文（含中标供应商表格）。"""
        detail_page.wait_for_timeout(2000)
        # 1) 点"中标公告" tab（详情页 el-tabs：采购/资审公告 | 中标公告 | 采购合同 | 更正事项）
        tab = detail_page.locator('div.el-tabs__item:has-text("中标公告")').first
        if tab.count():
            tab.click(timeout=10000)
            detail_page.wait_for_timeout(2500)
        # 2) 展开折叠项（原文链接藏在折叠项内）
        header = detail_page.locator('.el-collapse-item__header').first
        if header.count():
            try:
                header.click(timeout=10000)
                detail_page.wait_for_timeout(2000)
            except Exception:
                pass
        # 3) 找原文链接并跳转
        orig = detail_page.locator('a[href*="zfcg.czt.fujian.gov.cn"], a[href*="articleDetail"]').first
        if orig.count():
            orig_url = orig.get_attribute('href')
            if orig_url and orig_url.startswith('http'):
                detail_page.goto(orig_url, wait_until='domcontentloaded', timeout=60000)
                detail_page.wait_for_selector('#articleCont', state='attached', timeout=30000)
                detail_page.wait_for_function(
                    "() => (document.querySelector('#articleCont')?.innerText || '').trim().length > 50",
                    timeout=30000,
                )
                return detail_page.locator('#articleCont').first.inner_html()
        # 兜底：详情页折叠正文（中标 tab 正文通常为空，仅标题/日期）
        try:
            content = detail_page.locator('.el-collapse-item__content').first
            if content.count() and len(content.inner_text().strip()) > 50:
                return content.inner_html()
        except Exception:
            pass
        return ''


if __name__ == '__main__':
    FuJianResult().run()
