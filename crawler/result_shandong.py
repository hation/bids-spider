"""山东结果公告采集器（复用 ShanDong 的 SPA JSON 接口 + div.site-content 详情）

列表：getListByCode 接口 colCode=0302（结果公告；原 0301 为省级采购公告）
类型靠标题关键词分类（结果/终止公告混合）。
"""
from crawler.shandong import ShanDong


class ShanDongResult(ShanDong):
    def __init__(self):
        super().__init__()
        self.body['colCode'] = '0302'


if __name__ == '__main__':
    ShanDongResult().run()
