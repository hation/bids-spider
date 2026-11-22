"""黑龙江省采购网结果公告采集器（maincms-web 平台，"采购公告"栏目）。

2026-11-22：主采集器走"信息公开"(massageListPageHlj)，中标/废标覆盖不足；
本采集器改用"采购公告"(noticeInformationHlj) 栏目，覆盖中标/成交/废标。
"""
from crawler.result_maincms import MaincmsResultCollector


class HeiLongJiangResult(MaincmsResultCollector):
    site_id = '94c965cc-c55d-4f92-8469-d5875c68bd04'
    channel = 'c5bff13f-21ca-4dac-b158-cb40accd3035'  # 采购公告
    list_api = 'https://hljcg.hlj.gov.cn/gpcms/rest/web/v2/info/selectInfoForIndex'
    verify_api = 'https://hljcg.hlj.gov.cn/gpcms/rest/web/v2/index/getVerify'
    base_url = 'https://hljcg.hlj.gov.cn'
    notice_route = 'noticeHlj'

    def __init__(self, max_pages=50):
        super().__init__('hlj', max_pages=max_pages)


if __name__ == '__main__':
    HeiLongJiangResult().run()
