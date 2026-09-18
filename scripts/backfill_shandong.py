"""补全山东缺失详情：对备份清单中缺失详情的公告，逐条打开详情页抓取正文并写回 Elasticsearch

用途：山东列表接口按日期倒序，部分较早公告无法通过增量重跑自动抓回，
需要直接从备份的 href 清单（logs/shandong_deleted_backup.json）逐条补齐。
"""
import json
import random
import time

from elasticsearch import Elasticsearch
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

BACKUP = 'logs/shandong_deleted_backup.json'
SELECTOR = 'div.site-content'  # 山东详情页正文容器（SPA 异步渲染）
REGION = 'shandong'

# Elasticsearch 连接配置与 utils/es.py 保持一致
ES_URL = 'http://127.0.0.1:9200'
ES_AUTH = ('elastic', '7aNJbD0LTxsVLyuRcHSQ')


def load_pending():
    """读取备份清单，过滤出 ES 中不存在或 html 为空的记录"""
    with open(BACKUP) as f:
        records = json.load(f)
    es = Elasticsearch(ES_URL, basic_auth=ES_AUTH, request_timeout=30)
    pending = []
    skipped = 0
    for r in records:
        href = r.get('href')
        if not href:
            continue
        try:
            doc = es.get(index='tenders', id=href)['_source']
            if (doc.get('html') or '').strip():
                skipped += 1
                continue  # 已有详情，跳过
        except Exception:
            pass  # 文档不存在，需要回填
        pending.append(r)
    print(f'backup: {len(records)} | already has detail: {skipped} | pending: {len(pending)}', flush=True)
    return es, pending


def backfill(es, pending):
    """逐条打开详情页，等待正文渲染后抓取并写回 ES"""
    with Stealth().use_sync(sync_playwright()) as p:
        browser = p.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-blink-features=AutomationControlled'],
        )
        context = browser.new_context()
        ok = 0
        for i, r in enumerate(pending, 1):
            href = r.get('href')
            page = None
            try:
                page = context.new_page()
                page.goto(href, wait_until='domcontentloaded', timeout=60000)
                # 等待 SPA 正文异步渲染完成
                page.wait_for_function(
                    """() => {
                        const el = document.querySelector('div.site-content');
                        return el && el.innerHTML.trim().length > 0;
                    }""",
                    timeout=30000,
                )
                html = page.locator(SELECTOR).inner_html()
                page.close()
                # 写入完整字段，避免产生缺字段的残缺文档
                es.index(
                    index='tenders',
                    id=href,
                    document={
                        'region': REGION,
                        'href': href,
                        'title': r.get('title') or '',
                        'release_date': r.get('release_date') or '',
                        'crawl_date': time.strftime('%Y-%m-%d %H:%M:%S'),
                        'html': html,
                    },
                )
                ok += 1
                print(f'[{i}/{len(pending)}] OK   {href[:90]}', flush=True)
            except Exception as e:
                print(f'[{i}/{len(pending)}] FAIL {href[:90]}: {e}', flush=True)
                if page is not None:
                    try:
                        page.close()
                    except Exception:
                        pass
            time.sleep(random.uniform(1, 3))
        browser.close()
    return ok


def main():
    es, pending = load_pending()
    print(f'pending to backfill: {len(pending)}', flush=True)
    if not pending:
        print('nothing to do')
        return
    ok = backfill(es, pending)
    print(f'done, success {ok}/{len(pending)}', flush=True)


if __name__ == '__main__':
    main()
