"""重庆采购结果公告采集器（复用 ChongQing 的 Nuxt SSR 列表 + div.app-detail 详情）

列表：https://www.cqggzy.com/trade/014005004?categoryNum=014005004
该栏目含"中标（成交）结果公告"与"终止公告"（废标），类型靠标题关键词分类（不预设）。
"""
from crawler.chongqing import ChongQing


class ChongQingResult(ChongQing):
    def __init__(self):
        super().__init__()
        # 采购结果公告栏目（原 014005001 为采购公告）
        self.list_url = 'https://www.cqggzy.com/trade/014005004?categoryNum=014005004'


if __name__ == '__main__':
    ChongQingResult().run()
