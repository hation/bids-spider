"""青海中标公示采集器（全省频道）

全省中标公示列表：https://www.qhggzyjy.gov.cn/ggzy/jyxx/001002/001002004/transinfo_list.html
复用 qinghai.py 的列表解析/分页/详情逻辑，仅替换列表页。
类型无需预设：中标公示标题均含"中标(成交)结果公告"（_enrich_meta 标题分类→中标公告），
含"终止/废标/流标"的标题自动归为废标公告。
"""
from crawler.qinghai import QingHai


class QingHaiResult(QingHai):
    """青海中标公示采集器：抓全省中标公示列表，类型由标题关键词自动分类。"""

    def __init__(self):
        super().__init__()
        # 全省中标公示列表（原采购公告 001002001；此处 001002004 中标公示）
        self.index_url = 'https://www.qhggzyjy.gov.cn/ggzy/jyxx/001002/001002004/transinfo_list.html'


if __name__ == '__main__':
    QingHaiResult().run()
