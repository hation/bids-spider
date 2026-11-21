"""北京区级中标/废标公告采集器（复用 BeiJing 的列表/详情/入库逻辑）

- 中标列表：http://www.ccgp-beijing.gov.cn/xxgg/qjxxgg/qjzbggs/A002004002002index_{n}.htm
- 废标列表：http://www.ccgp-beijing.gov.cn/xxgg/qjxxgg/qjfbgg/A002004002005index_{n}.htm
结构兼容现有 beijing.py（li > a + span.datetime，详情 div.mainTextBox）。
"""
from crawler.beijing import BeiJing
from utils.log import logger


class BeiJingResult(BeiJing):
    def _crawl(self, context):
        self._crawl_type(context, '中标公告',
                         'http://www.ccgp-beijing.gov.cn/xxgg/qjxxgg/qjzbggs/A002004002002index_{}.htm')
        self._crawl_type(context, '废标公告',
                         'http://www.ccgp-beijing.gov.cn/xxgg/qjxxgg/qjfbgg/A002004002005index_{}.htm')

    def _crawl_type(self, context, announce_type, page_url_tpl):
        for i in range(1, self.max_pages + 1):
            page_url = page_url_tpl.format(i)
            logger.info(f"start to crawl: {page_url}")
            tenders = self._execute_by_new_page(context, page_url, self.get_one_page_titles)
            for url, tender in tenders.items():
                if url in self.exists_urls or url in self.tenders:
                    continue
                try:
                    tender.html = self._execute_by_new_page(context, url, self.parse_detail)
                except Exception as e:
                    logger.error(f"[{self.region}]parse detail failed {url}: {e}")
                tender.announce_type = announce_type
                tender.crawl_date = self._get_crawl_date()
                self.tenders[url] = tender
                self._random_sleep(_max=30)


if __name__ == '__main__':
    BeiJingResult().run()
