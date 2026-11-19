"""ES tenders 索引重建：html 字段从 index:false 改为可搜索。

步骤：
1. 新建 tenders_v2（用新 mapping，html 可搜索）
2. reindex tenders → tenders_v2（节流 500/s 避免压 ES）
3. 删旧索引 tenders（数据已在 tenders_v2）
4. 给 tenders_v2 加 is_write_index alias tenders（爬虫写入透明切到新索引）
5. 验证：搜 html:"内存条" 能命中

执行前请确保没有爬虫正在写入（ps aux | grep fast_run.py | grep -v grep，有则停）。
"""
import sys, os, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from elasticsearch import Elasticsearch

from utils.es import TENDER_INDEX_BODY

HOST = "http://127.0.0.1:9200"
AUTH = ("elastic", "7aNJbD0LTxsVLyuRcHSQ")
SRC = "tenders"
NEW = "tenders_v2"

es = Elasticsearch([HOST], basic_auth=AUTH, timeout=30)

def step(msg):
    print(f"\n{'='*60}\n{msg}\n{'='*60}", flush=True)

# 0. 检查源索引
step("0. 检查源索引 tenders 状态")
if not es.indices.exists(index=SRC):
    print("❌ 源索引不存在，退出")
    sys.exit(1)
src_count = es.count(index=SRC)["count"]
print(f"✅ 源索引 tenders 存在，文档数: {src_count}")

# 1. 若旧新索引都存在，提示并退出（避免误覆盖）
if es.indices.exists(index=NEW):
    print(f"⚠️  目标索引 {NEW} 已存在，先删掉或改名")
    sys.exit(1)

# 2. 新建 tenders_v2（新 mapping）
step(f"1. 新建索引 {NEW}（html 可搜索 mapping）")
resp = es.indices.create(index=NEW, body=TENDER_INDEX_BODY)
print(f"✅ 创建成功: {resp.get('acknowledged')}")

# 3. Reindex（同步执行，节流 500/s）
step(f"2. Reindex {SRC} → {NEW}（节流 500 docs/s）")
print(f"   源文档: {src_count}，请稍候…")
task = es.reindex(
    source={"index": SRC},
    dest={"index": NEW},
    wait_for_completion=False,
    requests_per_second=500,
)
task_id = task["task"]
print(f"   task_id: {task_id}")

# 轮询等完成
start = time.time()
while True:
    status = es.tasks.get(task_id=task_id)
    s = status["task"]["status"]
    done = s.get("total", 0)
    total = s.get("total", 0)
    if status.get("completed") or s.get("updated") and s["updated"] + s.get("created", 0) >= total:
        break
    elapsed = int(time.time() - start)
    if elapsed % 15 == 0:
        print(f"   [{elapsed}s] 已处理 {s.get('created',0)+s.get('updated',0)}/{total}", flush=True)
    time.sleep(5)

# 最终状态
s = status["task"]["status"]
created = s.get("created", 0)
updated = s.get("updated", 0)
print(f"✅ Reindex 完成: created={created}, updated={updated}, 耗时 {int(time.time()-start)}s")

# 4. 验证新索引文档数
new_count = es.count(index=NEW)["count"]
print(f"   新索引 {NEW} 文档数: {new_count} (源 {src_count})")
assert new_count >= src_count - 10, f"文档数不匹配！源 {src_count}, 新 {new_count}"

# 5. 切换：删旧索引 tenders → alias tenders 指向 tenders_v2
step(f"3. 删旧索引 {SRC} 并切 alias")
es.indices.delete(index=SRC)
print(f"✅ 旧索引 {SRC} 已删除")

es.indices.put_alias(index=NEW, name=SRC, body={"is_write_index": True})
print(f"✅ alias {SRC} → {NEW}（is_write_index: true）")

# 6. 最终验证
step("4. 验证 alias + 正文搜索")
# alias 检查
aliases = es.indices.get_alias(name=SRC)
print(f"   alias {SRC} 指向索引: {list(aliases.keys())}")

# 正文搜索（用 alias 名 tenders 查，应该命中）
q = {"query": {"match": {"html": "内存条"}}, "size": 3}
resp = es.search(index=SRC, body=q)
print(f"   搜 html:'内存条' 命中 {resp['hits']['total']['value']} 条")

print("\n🎉 全部完成！爬虫现在写入 alias tenders → 实际落在 tenders_v2")
print(f"   旧索引已删除。如需回滚：直接删掉 alias tenders 并重建 tenders（mapping 旧版）")
