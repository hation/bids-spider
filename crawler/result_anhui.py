"""安徽结果公告采集器（ZcyAnnouncement4 结果公告栏目）

结果公告栏目：parentId=oJCosldFbaJFzmyFhz1c6Q%3D%3D & childrenCode=ZcyAnnouncement4
（88455 条，含中标/成交/终止/更正混合，最新 2026-11-20）。
复用 anhui.py 的列表解析/翻页/portal-detail 详情逻辑，仅替换栏目代码。
类型无需预设：标题含"中标/成交/结果"->中标公告、"终止/废标/流标"->废标公告，
由 _enrich_meta 标题分类自动处理。
"""
from crawler.anhui import AnHui


class AnHuiResult(AnHui):
    """安徽结果公告采集器：抓 ZcyAnnouncement4 结果公告栏目，类型由标题关键词自动分类。"""

    def __init__(self):
        super().__init__()
        # 结果公告栏目（原采购公告 ZcyAnnouncement2）
        self.index_url = ('https://www.ccgp-anhui.gov.cn/site/category'
                          '?parentId=oJCosldFbaJFzmyFhz1c6Q%3D%3D&childrenCode=ZcyAnnouncement4')


if __name__ == '__main__':
    AnHuiResult().run()
