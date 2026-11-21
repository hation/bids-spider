"""存量数据回填：为 ES 中缺少 announce_type 的公告按标题分类标记，中标公告补提取中标单位/金额。

用法：
    python scripts/backfill_announce_type.py            # 全量回填（只处理 announce_type 缺失的）
    python scripts/backfill_announce_type.py 1000       # 分批回填（每次处理条数）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from elasticsearch import helpers

from utils.es import ESConnection
from utils.announce import classify_announce_type, extract_win_company, extract_win_amount


def backfill(batch=None):
    es = ESConnection()
    client = es._client()
    if not client:
        print('[backfill_announce_type] ES 连接失败')
        return

    # 只处理 announce_type 缺失的文档（幂等：已标记的一次更新后收敛）
    query = {
        "query": {
            "bool": {
                "must_not": [{"exists": {"field": "announce_type"}}]
            }
        },
        "sort": ["_doc"],
    }

    seen = 0
    updated = 0
    actions = []
    try:
        resp = client.search(index='tenders', body=query, scroll='10m', size=1000, _source=True)
        sid = resp['_scroll_id']
        hits = resp['hits']['hits']
        while hits:
            for h in hits:
                seen += 1
                src = h.get('_source', {})
                doc_id = h.get('_id')
                title = src.get('title', '')
                atype = classify_announce_type(title)
                doc = {'announce_type': atype}
                if atype == '中标公告':
                    html = src.get('html', '') or ''
                    doc['win_company'] = extract_win_company(html)
                    doc['win_amount_wan'] = extract_win_amount(html)
                actions.append({
                    "_op_type": "update",
                    "_index": "tenders",
                    "_id": doc_id,
                    "doc": doc,
                })
                updated += 1
                if batch and updated >= batch:
                    break
            if batch and updated >= batch:
                break
            resp = client.scroll(scroll_id=sid, scroll='10m')
            hits = resp['hits']['hits']
        try:
            client.clear_scroll(scroll_id=sid)
        except Exception:
            pass
    except Exception as e:
        print(f'[backfill_announce_type] 扫描失败: {e}')
        return

    if actions:
        try:
            success, errors = helpers.bulk(client, actions, chunk_size=500, raise_on_error=False)
            print(f'[backfill_announce_type] 更新成功 {success} 条，失败 {len(errors) if isinstance(errors, list) else 0} 条')
        except Exception as e:
            print(f'[backfill_announce_type] bulk 更新失败: {e}')
    print(f'[backfill_announce_type] 扫描 {seen} 条，提交更新 {len(actions)} 条')


if __name__ == '__main__':
    batch = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else None
    backfill(batch)
