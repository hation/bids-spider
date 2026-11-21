"""贵州中标结果公告采集器（复用 GuiZhou 的 portal/category 接口）

列表：portal/category 接口 categoryCode=ZcyAnnouncement5（中标结果公告；原 ZcyAnnouncement1 为采购意向）
"""
from crawler.guizhou import GuiZhou


class GuiZhouResult(GuiZhou):
    def __init__(self):
        super().__init__()
        self.category_code = 'ZcyAnnouncement5'
        # Referer 与 categoryCode 保持一致，避免接口被拒
        self.headers['Referer'] = self.list_url + '&categoryCode=ZcyAnnouncement5'


if __name__ == '__main__':
    GuiZhouResult().run()
