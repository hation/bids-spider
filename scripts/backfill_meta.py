"""存量数据回填：为 ES 中已存在的公告补充 amount_wan / bid_deadline 结构化字段。

背景：2026-09-22 起入库时自动从正文提取金额与投标截止时间写入结构化字段，
本脚本对更早入库的存量数据做一次全量重提取回填。

用法：
    python scripts/backfill_meta.py                 # 全量回填
    python scripts/backfill_meta.py 1000            # 分批回填（每次处理条数），默认全量
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from elasticsearch import helpers

from utils.es import ESConnection
from crawler.base_crawler import BaseCrawler


def backfill(batch=None):
    es = ESConnection()
    client = es._client()
    if not client:
        print('[backfill_meta] ES 连接失败')
        return

    # 只回填两个字段都缺失的文档（amount_wan 与 bid_deadline 均未写入）。
    # 注：ES 中 null 不建索引，若用"任一缺失"会反复命中只提取到单个字段的文档，
    # 因此必须用"两者皆缺"才能保证一次更新后收敛。
    query = {
        "query": {
            "bool": {
                "must_not": [
                    {"exists": {"field": "amount_wan"}},
                    {"exists": {"field": "bid_deadline"}},
                ]
            }
        },
        "sort": ["_doc"],
    }

    seen = 0
    updated = 0
    missing = 0
    actions = []
    bc = BaseCrawler.__new__(BaseCrawler)  # 仅复用静态 _html_to_text，不初始化
    try:
        resp = client.search(index='tenders', body=query, scroll='10m', size=1000, _source=True)
        sid = resp['_scroll_id']
        hits = resp['hits']['hits']
        while hits:
            for h in hits:
                seen += 1
                src = h.get('_source', {})
                html = src.get('html', '') or ''
                doc_id = h.get('_id')
                unit, val = (None, None)
                deadline = ''
                try:
                    from analyze_today import extract_money, extract_timeline
                    text = bc._html_to_text(html)
                    unit, val = extract_money(text)
                    tl = extract_timeline(text)
                    deadline = tl.get('投标截止') or ''
                except Exception:
                    pass
                amount = None
                if unit == '万元':
                    amount = float(val)
                elif unit == '元':
                    amount = float(val) / 10000
                if amount is None and not deadline:
                    missing += 1
                    continue
                actions.append({
                    "_op_type": "update",
                    "_index": "tenders",
                    "_id": doc_id,
                    "doc": {"amount_wan": amount, "bid_deadline": deadline},
                })
                updated += 1
                if batch and updated >= batch:
                    break
            if batch and updated >= batch:
                break
            # 每轮刷新 scroll 上下文，避免提取耗时导致上下文过期
            resp = client.scroll(scroll_id=sid, scroll='10m')
            hits = resp['hits']['hits']
        try:
            client.clear_scroll(scroll_id=sid)
        except Exception:
            pass
    except Exception as e:
        print(f'[backfill_meta] 扫描失败: {e}')
        return

    if actions:
        try:
            success, errors = helpers.bulk(client, actions, chunk_size=500, raise_on_error=False)
            print(f'[backfill_meta] 更新成功 {success} 条，失败 {len(errors) if isinstance(errors, list) else 0} 条')
        except Exception as e:
            print(f'[backfill_meta] bulk 更新失败: {e}')
    print(f'[backfill_meta] 扫描 {seen} 条，其中未提取到金额/截止的 {missing} 条，提交更新 {len(actions)} 条')


if __name__ == '__main__':
    batch = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else None
    backfill(batch)
