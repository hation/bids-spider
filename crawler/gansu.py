from crawler.hlj import HeiLongJiang


class GanSu(HeiLongJiang):
    """甘肃省采购网（maincms-web 平台，验证码流程同黑龙江）。"""

    def __init__(self, max_pages=50):
        super().__init__(max_pages=max_pages)
        self.region = 'gansu'
        self.list_url = 'https://www.ccgp-gansu.gov.cn/maincms-web/massageListPageGs'
        self.verify_api = 'https://www.ccgp-gansu.gov.cn/gpcms/rest/web/v2/index/getVerify'
        self.list_api = 'https://www.ccgp-gansu.gov.cn/gpcms/rest/web/v2/info/selectInfoForIndex'
        self.base_url = 'https://www.ccgp-gansu.gov.cn'
        self.notice_route = 'noticeGs'


if __name__ == '__main__':
    GanSu().run()
