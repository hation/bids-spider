"""天津结果公告采集器（复用 TianJin 的请求头捕获 + 列表接口 + 详情接口）

结果列表：POST /api/api/portal/Announcement/Page
  industryType=["4"]（政府采购）+ announcementType=10（中标结果）/ 16（废标终止）
详情：POST /api/api/portal/Announcement/GetDetail
"""
from crawler.tianjin import TianJin
from crawler.base_crawler import Tender
from utils.log import logger


class TianJinResult(TianJin):
    def _crawl(self, context):
        page = context.new_page()
        self.authorization = None

        def handle_request(request):
            headers = request.headers
            if 'authorization' in headers:
                self.authorization = headers['authorization']

        page.on("request", handle_request)
        page.goto("https://www.tjggzy.cn/announcementIndex")
        page.wait_for_load_state("networkidle")
        if not self.authorization:
            try:
                auth_storage = page.evaluate("() => localStorage.getItem('authorization')")
                if auth_storage:
                    self.authorization = auth_storage
            except Exception:
                pass
        self.headers = {
            "Content-Type": "application/json",
            "Referer": "https://www.tjggzy.cn/announcementIndex",
            "User-Agent": page.evaluate("navigator.userAgent"),
        }
        if self.authorization:
            self.headers["Authorization"] = self.authorization

        for announcement_type, announce_type in ((10, '中标公告'), (16, '废标公告')):
            tender_list = self._get_tender_list(context, '4', announcement_type)
            for tender_id in tender_list:
                if tender_id in self.exists_urls:
                    continue
                self._get_tender_details(context, tender_id, announce_type)
                self._random_sleep(_max=30)

    def _get_tender_details(self, context, tender_id, announce_type=None):
        logger.info(f"start to request tender details for {tender_id}")
        response = context.request.post(
            url="https://www.tjggzy.cn/api/api/portal/Announcement/GetDetail",
            data={"Id": tender_id},
            headers=self.headers,
        )
        data = response.json()
        if data.get("statusCode") != 2000:
            logger.error(f"get tender details error:{data}")
            return
        tender = Tender(region=self.region, href=tender_id,
                        title=data['responseData']['announcementName'],
                        release_date=data['responseData']['publishTime'],
                        html=data['responseData']['noticeContent'],
                        crawl_date=self._get_crawl_date())
        if announce_type:
            tender.announce_type = announce_type
        self.save_tender_to_es(tender)
        self.tenders[tender_id] = tender
        logger.info(f"get tender details for {tender_id} success")


if __name__ == '__main__':
    TianJinResult().run()
