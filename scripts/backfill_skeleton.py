"""回填 Vue 骨架页：对正文过短（<1000 字节）的中标/废标公告重新抓详情，并重提取字段。

背景：部分站点（广东为主，占 88%）为 Vue 异步渲染，旧版详情抓取只等元素出现、不等内容
异步填充，抓到 667 字节空骨架（正文全为 <!----> 占位）。本脚本用 Playwright 重新打开公告，
轮询正文容器长度达阈值后写回 ES，并重跑公告类型判定与中标单位/金额/废标原因提取。

用法：
    .venv/bin/python scripts/backfill_skeleton.py                # 处理全部骨架页
    .venv/bin/python scripts/backfill_skeleton.py --limit 100    # 只处理 100 条
    .venv/bin/python scripts/backfill_skeleton.py --region guangdong
"""
import argparse
import re
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from elasticsearch import Elasticsearch, helpers
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

from utils.announce import (
    classify_announce_type, extract_win_company, extract_win_amount,
    extract_void_reason, _to_text,
)

ES_URL = 'http://127.0.0.1:9200'
SELECTORS = ['div.notice-content', 'div.articleContent', 'div.innercontent', 'div.u-content',
             'div.mainTextBox', 'div.article', 'div#content', 'div.content']
VOID_SIG = re.compile(
    r'(结果类型[:：]\s*(废标|流标|终止)|废标原因|流标原因|终止原因|废标情形|流标情形|'
    r'无供应商(递交|投标)|无有效(投标|供应商)|投标人不足三家)')


def collect_skeletons(es, region=None, limit=None):
    """按 href 排序 search_after 扫描，收集 html<1000 的中标/废标文档。"""
    docs = []
    after = None
    while True:
        must = [{'terms': {'announce_type': ['中标公告', '废标公告']}}]
        if region:
            must.append({'term': {'region': region}})
        body = {
            'size': 500,
            'query': {'bool': {'must': must}},
            'sort': [{'href': 'asc'}],
            '_source': ['href', 'title', 'region', 'html', 'announce_type'],
        }
        if after:
            body['search_after'] = [after]
        r = es.search(index='tenders', body=body)
        hits = r['hits']['hits']
        if not hits:
            break
        for h in hits:
            if len(h['_source'].get('html', '') or '') < 1000:
                docs.append(h['_source'])
                if limit and len(docs) >= limit:
                    return docs
        after = hits[-1]['sort'][0]
        if len(hits) < 500:
            break
    return docs


def fetch_detail(page, href, min_len=1000, tries=20):
    """打开详情并轮询正文容器长度；返回最长的正文 HTML。"""
    try:
        page.goto(href, wait_until='domcontentloaded', timeout=60000)
    except Exception:
        return ''
    for _ in range(tries):
        for sel in SELECTORS:
            loc = page.locator(sel)
            if loc.count():
                try:
                    h = loc.first.inner_html()
                except Exception:
                    continue
                if len(h) >= min_len:
                    return h
        page.wait_for_timeout(500)
    # 兜底：取所有候选里最长的一个
    best = ''
    for sel in SELECTORS:
        loc = page.locator(sel)
        if loc.count():
            try:
                h = loc.first.inner_html()
            except Exception:
                continue
            if len(h) > len(best):
                best = h
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--region', default='')
    args = ap.parse_args()

    es = Elasticsearch([ES_URL], request_timeout=120)
    docs = collect_skeletons(es, args.region or None, args.limit or None)
    print(f'待处理骨架页: {len(docs)} 条', flush=True)
    if not docs:
        return

    updated = 0
    actions = []
    with Stealth().use_sync(sync_playwright()) as p:
        browser = p.chromium.launch(headless=True, args=[
            '--no-sandbox', '--disable-setuid-sandbox', '--proxy-server=direct://'])
        ctx = browser.new_context(ignore_https_errors=True)
        page = ctx.new_page()
        for i, s in enumerate(docs, 1):
            href = s.get('href') or ''
            if not href:
                continue
            html = fetch_detail(page, href)
            if len(html) < 1000:
                print(f'  [{i}/{len(docs)}] 仍为空/过短({len(html)}) {href[:60]}', flush=True)
                continue
            title = s.get('title', '')
            text = _to_text(html)
            atype = s.get('announce_type') or classify_announce_type(title)
            if VOID_SIG.search(text):
                atype = '废标公告'
            doc = {'html': html, 'announce_type': atype}
            if atype == '中标公告':
                wc = extract_win_company(html)
                if wc:
                    doc['win_company'] = wc
                amt = extract_win_amount(html)
                if amt is not None:
                    doc['win_amount_wan'] = amt
            else:
                vr = extract_void_reason(html)
                if vr:
                    doc['void_reason'] = vr
            actions.append({'_op_type': 'update', '_index': 'tenders',
                            '_id': href, 'doc': doc})
            updated += 1
            if len(actions) >= 100:
                helpers.bulk(es, actions, raise_on_error=False)
                actions.clear()
                print(f'  [{i}/{len(docs)}] 已回填 {updated} 条', flush=True)
        browser.close()

    if actions:
        helpers.bulk(es, actions, raise_on_error=False)
    print(f'完成：成功回填 {updated}/{len(docs)} 条', flush=True)


if __name__ == '__main__':
    main()
