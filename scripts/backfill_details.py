"""回填缺失的公告详情：从 pending_detail_fill.jsonl 读取缺详情清单，
按地区复用对应站点的详情获取逻辑（JSON 接口 / DOM 页面），抓取正文写回 ES 与 Excel。

用法：
    python scripts/backfill_details.py <excel_path>
    # 从 logs/pending_detail_fill.jsonl 读取清单，回填后更新 <excel_path>
"""
import json
import re
import sys
import time
import urllib.parse

import html2text
import pandas as pd
from elasticsearch import Elasticsearch
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

ES_URL = 'http://127.0.0.1:9200'
ES_AUTH = ('elastic', '7aNJbD0LTxsVLyuRcHSQ')
HUNAN_DETAIL = 'http://www.ccgp-hunan.gov.cn/portal/detail'
GUIZHOU_DETAIL = 'http://www.ccgp-guizhou.gov.cn/portal/detail'
BEIJING_SELECTOR = '.mainTextBox'
_ILLEGAL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')


def clean(s):
    return _ILLEGAL.sub('', str(s or ''))


def to_text(raw):
    s = str(raw or '')
    if '<' in s[:200] and ('</' in s or '<div' in s or '<p' in s or '<table' in s or '<style' in s or '<h' in s):
        h = html2text.HTML2Text()
        h.ignore_links = True
        h.ignore_images = True
        h.body_width = 0
        s = h.handle(s)
    return clean(s)


def fetch_hunan(context, href, retries=3):
    """湖南：portal/detail JSON 接口

    articleId 是 base64 风格字符串，可能含 + / = 字符，
    必须从原始 URL 中按 & 拆取（parse_qs 会把 + 解码为空格导致查询失败）。
    """
    query = urllib.parse.urlparse(href).query
    article_id = ''
    parent_id = ''
    for kv in query.split('&'):
        k, _, v = kv.partition('=')
        if k == 'articleId':
            article_id = v
        elif k == 'parentId':
            parent_id = v
    if not article_id:
        return ''
    api = f"{HUNAN_DETAIL}?articleId={urllib.parse.quote(article_id)}&parentId={parent_id}"
    for _ in range(retries):
        try:
            resp = context.request.get(api, headers={'Referer': href, 'Accept': 'application/json, text/plain, */*'})
            data = resp.json()
            result = data.get('result') or {}
            detail = result.get('data') if isinstance(result, dict) else None
            content = detail.get('content') if isinstance(detail, dict) else None
            if content:
                return content
        except Exception:
            pass
        time.sleep(1)
    return ''


def fetch_guizhou(context, href, retries=3):
    """贵州：portal/detail JSON 接口"""
    params = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
    article_id = (params.get('articleId') or [''])[0]
    parent_id = (params.get('parentId') or [''])[0]
    if not article_id:
        return ''
    for _ in range(retries):
        try:
            resp = context.request.get(
                GUIZHOU_DETAIL,
                params={'articleId': article_id, 'parentId': parent_id, 'timestamp': int(time.time() * 1000)},
                headers={'Referer': href, 'Accept': 'application/json, text/plain, */*'},
            )
            data = resp.json()
            result = data.get('result')
            inner = result.get('data') if isinstance(result, dict) else None
            content = inner.get('content') if isinstance(inner, dict) else None
            if content:
                return content
        except Exception:
            pass
        time.sleep(1)
    return ''


def fetch_beijing(context, href, retries=2):
    """北京：详情页 DOM"""
    for _ in range(retries):
        try:
            page = context.new_page()
            page.goto(href, wait_until='domcontentloaded', timeout=60000)
            page.wait_for_selector(BEIJING_SELECTOR, timeout=30000)
            html = page.locator(BEIJING_SELECTOR).inner_html()
            page.close()
            return html
        except Exception:
            try:
                page.close()
            except Exception:
                pass
            time.sleep(1)
    return ''


FETCHERS = {
    'hunan': fetch_hunan,
    'guizhou': fetch_guizhou,
    'beijing': fetch_beijing,
}


def main():
    excel = sys.argv[1] if len(sys.argv) > 1 else 'output/all_regions_2026_09_18_13_47_45.xlsx'
    pending_file = sys.argv[2] if len(sys.argv) > 2 else 'logs/pending_detail_fill.jsonl'
    with open(pending_file) as f:
        pending = [json.loads(l) for l in f]
    # 只处理支持的地区
    pending = [r for r in pending if r['region'] in FETCHERS]
    print(f'待回填(可处理): {len(pending)}')

    es = Elasticsearch(ES_URL, basic_auth=ES_AUTH, request_timeout=30)
    df = pd.read_excel(excel)
    df['html'] = df['html'].fillna('').astype(str)
    idx_by_href = {str(r['href']): i for i, r in df.iterrows()}

    ok = 0
    fail = 0
    with Stealth().use_sync(sync_playwright()) as p:
        browser = p.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-blink-features=AutomationControlled'],
        )
        context = browser.new_context()
        for i, rec in enumerate(pending, 1):
            href = rec['href']
            fetcher = FETCHERS[rec['region']]
            try:
                raw = fetcher(context, href)
            except Exception as e:
                raw = ''
                print(f'[{i}/{len(pending)}] FAIL {rec["region"]} {str(e)[:60]} | {href[:70]}', flush=True)
            if raw and raw.strip():
                txt = to_text(raw)
                # 写回 ES
                try:
                    es.index(index='tenders', id=href, document={
                        'region': rec['region'], 'href': href, 'title': rec['title'],
                        'release_date': rec['release_date'],
                        'crawl_date': time.strftime('%Y-%m-%d %H:%M:%S'), 'html': raw,
                    })
                except Exception as e:
                    print(f'  ES 写回失败 {href[:60]}: {e}', flush=True)
                # 更新 Excel
                if href in idx_by_href:
                    i2 = idx_by_href[href]
                    df.at[i2, 'html'] = txt
                    df.at[i2, 'crawl_date'] = time.strftime('%Y-%m-%d %H:%M:%S')
                    df.at[i2, 'truncated'] = '是' if len(txt) > 32767 else '否'
                ok += 1
                print(f'[{i}/{len(pending)}] OK   {rec["region"]} len={len(txt)} | {href[:60]}', flush=True)
            else:
                fail += 1
                print(f'[{i}/{len(pending)}] EMPTY {rec["region"]} | {href[:70]}', flush=True)
            time.sleep(1)
        browser.close()

    df.to_excel(excel, index=False)
    print(f'\n完成: 成功 {ok}, 失败/空 {fail}, 已更新 {excel}')


if __name__ == '__main__':
    main()
