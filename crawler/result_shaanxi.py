"""陕西中标/废标公告采集器（freecms 平台，公告类型筛选）

流程（先填时间 → 选公告类型 → 再填验证码 → 查询）：
1. 打开信息公告页，填 `#stateDate`/`#endDate` 为目标日区间。
2. 展开"公告类型"下拉，选"结果公告"（中标为主，含部分废标标题）或"终止公告"（废标）。
3. ddddocr 识别验证码填入 → 点查询，捕获 selectInfoMoreChannel.do 接口参数（含
   noticeType 组合、operationStartTime/EndTime）。
4. 接口直连翻页（currPage）抓多页；类型由标题关键词自动分类（结果公告栏目标题
   常含"废标公告"字样，classify_announce_type 先匹配废标）。
"""
import time

from crawler.shaanxi import ShaanXi
from utils.log import logger

ANNOUNCE_TYPE_LABELS = ['结果公告', '终止公告']


class ShaanXiResult(ShaanXi):
    """陕西结果公告采集器：公告类型筛选结果公告/终止公告，标题关键词自动分类。"""

    def _crawl(self, context):
        for label in ANNOUNCE_TYPE_LABELS:
            self._crawl_type(context, label)

    def _crawl_type(self, context, label):
        page = context.new_page()
        page.goto(self.list_url, wait_until='domcontentloaded', timeout=60000)
        page.wait_for_timeout(5000)
        # 1) 先填时间（先填时间再填验证码）
        start = getattr(self, '_date_start', '') or ''
        end = getattr(self, '_date_end', '') or start
        if start:
            page.fill('#stateDate', f'{start} 00:00:00')
            page.fill('#endDate', f'{end} 23:59:59')
        # 2) 选公告类型（下拉：招标公告(默认)/结果公告/终止公告）
        try:
            page.locator('span:has-text("公告类型")').first.click(timeout=5000)
            page.wait_for_timeout(500)
            page.locator(f'li:has-text("{label}")').last.click(timeout=5000)
            page.wait_for_timeout(500)
        except Exception as e:
            logger.warning(f"[{self.region}]选公告类型 {label} 失败: {e}")
            page.close()
            return
        # 3) 验证码 + 第一页
        code, query, rows = self._solve_and_fetch_first_page(context, page)
        page.close()
        if not query:
            logger.error(f"[{self.region}]{label} 验证码多次尝试后仍无法获取列表")
            return
        self._get_tenders(context, code, query)

    def _parse_query(self, url):
        """解析接口 URL 参数并解码（时间值含 %20 编码空格，不解码会导致 urlencode 双重编码 400）。"""
        from urllib.parse import unquote
        query = {}
        for pair in url.split('?', 1)[-1].split('&'):
            if '=' in pair:
                k, _, v = pair.partition('=')
                query[k] = unquote(v)
        return query

    def _fetch_page(self, context, query, verify_code, page_no):
        """翻页：保留时间过滤参数（结果采集按目标日，不能像主爬虫那样去掉时间全量抓）。"""
        params = {k: v for k, v in query.items()
                  if k not in ('currPage', 'pageSize', 'verifyCode')}
        params.update({'currPage': page_no, 'pageSize': 10, 'verifyCode': verify_code})
        url = self.list_api + '?' + self._urlencode(params)
        resp = context.request.get(url, headers={'Referer': self.list_url})
        try:
            data = resp.json()
        except Exception as e:
            logger.warning(f"[{self.region}]list api json parse failed: {e}")
            return []
        return data.get('data') or []

    @staticmethod
    def _urlencode(params):
        from urllib.parse import urlencode
        return urlencode(params)


if __name__ == '__main__':
    ShaanXiResult().run()
