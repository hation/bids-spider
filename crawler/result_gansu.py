"""甘肃省采购网结果公告采集器（maincms-web 平台，"信息公告"栏目）。

2026-11-22：主采集器走"信息公示"(massageListPageGs) 栏目，抓到的是合同/履约类公告，
不含中标/成交结果；本采集器改用"信息公告"(noticeInformationGs) 栏目，覆盖中标/成交/废标。
"""
from crawler.result_maincms import MaincmsResultCollector


class GanSuResult(MaincmsResultCollector):
    site_id = '793f8fcd-77c4-4c76-9ce6-9de8deddee8d'
    channel = 'c5bff13f-21ca-4dac-b158-cb40accd3035'  # 信息公告
    list_api = 'https://www.ccgp-gansu.gov.cn/gpcms/rest/web/v2/info/selectInfoForIndex'
    verify_api = 'https://www.ccgp-gansu.gov.cn/gpcms/rest/web/v2/index/getVerify'
    base_url = 'https://www.ccgp-gansu.gov.cn'
    notice_route = 'noticeGs'

    def __init__(self, max_pages=50):
        super().__init__('gansu', max_pages=max_pages)


if __name__ == '__main__':
    GanSuResult().run()
