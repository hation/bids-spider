from dataclasses import asdict, is_dataclass
from datetime import datetime
from typing import Iterable, Mapping, Optional, Union
from elasticsearch import Elasticsearch, helpers

from utils.log import logger

superuser_name = 'elastic'
superuser_pwd = '7aNJbD0LTxsVLyuRcHSQ'
host = 'http://127.0.0.1:9200'

DEFAULT_TENDER_INDEX = "tenders"
DEFAULT_LLM_READS_INDEX = "llm_reads"

# LLM 精读结果缓存索引：key=分析范围标识，input_hash 用于判断输入数据是否变化
LLM_READS_INDEX_BODY = {
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
    },
    "mappings": {
        "dynamic": "false",
        "properties": {
            "key": {"type": "keyword"},        # 分析范围标识，如 daily_2026-09-22 / window_2026-10-01_2026-10-31
            "scope": {"type": "keyword"},      # 人类可读范围描述
            "input_hash": {"type": "keyword"}, # 输入清单 hash，用于判断数据是否变化
            "model": {"type": "keyword"},      # 使用的模型
            "result": {"type": "text", "index": False},  # 精读洞察全文
            "updated_at": {"type": "date", "format": "yyyy-MM-dd HH:mm:ss||strict_date_optional_time||epoch_millis"},
        },
    },
}

TENDER_INDEX_BODY = {
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
    },
    "mappings": {
        "dynamic": "false",
        "properties": {
            "region": {"type": "keyword"},
            "href": {"type": "keyword"},
            "title": {"type": "text", "fields": {"keyword": {"type": "keyword", "ignore_above": 256}}},
            "release_date": {"type": "keyword"},
            "crawl_date": {"type": "date", "format": "yyyy-MM-dd HH:mm:ss||strict_date_optional_time||epoch_millis"},
            "amount_wan": {"type": "double"},              # 项目金额（万元）
            "bid_deadline": {"type": "keyword"},           # 投标截止时间（ISO）
            "html": {"type": "text", "index": False},
        },
    },
}


class ESConnection:
    def __init__(self, index_name: str = DEFAULT_TENDER_INDEX):
        self.check_es_version()
        self.es: Optional[Elasticsearch] = None
        self.default_index = index_name

    def check_es_version(self):
        """检查elasticsearch版本，用于诊断"""
        try:
            import elasticsearch
            version = elasticsearch.__version__
            if version[0] >= 9:
                logger.warning(f"warning: detect elasticsearch version {version}, may be incompatible with ES 8.x")
                logger.warning("Try pip uninstall elasticsearch -y && pip install 'elasticsearch<9.0.0'")
                return False
            return True
        except Exception as e:
            logger.error(f"check es version failed: {e}")
            return True

    def _client(self) -> Optional[Elasticsearch]:
        if self.es is None:
            self.create_connection()
        return self.es

    def create_connection(self):
        """连接Elasticsearch"""
        try:
            self.es = Elasticsearch(
                host,
                basic_auth=(superuser_name, superuser_pwd),
                request_timeout=10,
                max_retries=3,
                retry_on_timeout=False,
                http_compress=False,
            )
            return self.es
        except Exception as e:
            logger.error(f"connection es failed: {e}")
            self.es = None
            return None

    def test_connection(self):
        """测试连接"""
        client = self._client()
        if not client:
            return False
        try:
            info = client.info()
            logger.info(f"connect Elasticsearch successfully, version: {info['version']['number']}, cluster_name: {info['cluster_name']}")
        except Exception as e:
            logger.error(f"connect Elasticsearch failed: {e}")
            return False
        return True
    
    def ensure_index(self, index_name: Optional[str] = None, body: Optional[dict] = None) -> bool:
        """确保索引存在，若不存在则按照提供的mapping创建"""
        client = self._client()
        if not client:
            return False
        index = index_name or self.default_index
        try:
            if client.indices.exists(index=index):
                # 索引已存在：补充新字段 mapping（如 amount_wan / bid_deadline）
                mapping_body = body or TENDER_INDEX_BODY
                new_props = mapping_body.get("mappings", {}).get("properties", {})
                if new_props:
                    client.indices.put_mapping(index=index, properties=new_props)
                return True
            mapping_body = body or TENDER_INDEX_BODY
            client.indices.create(index=index, body=mapping_body)
            logger.info(f"create index {index} successfully with mapping.")
            return True
        except Exception as e:
            logger.error(f"create index {index} failed: {e}")
            return False

    def create_index(self, index_name, body: Optional[dict] = None):
        """创建索引"""
        return self.ensure_index(index_name, body)

    def insert_data(self, index_name, data, doc_id: Optional[str] = None):
        """插入数据"""
        client = self._client()
        if not client:
            return None
        try:
            resp = client.index(index=index_name, id=doc_id, document=data)
            logger.info(f"insert data to {index_name} successfully, response: {resp}")
            return resp['_id']
        except Exception as e:
            logger.error(f"insert data to {index_name} failed: {e}")
            return None
    
    def delete_data(self, index_name, id):
        """删除数据"""
        try:
            client = self._client()
            resp = client.delete(index=index_name, id=id)
            logger.info(f"delete data from {index_name} successfully, response: {resp}")
            return resp['_id']
        except Exception as e:
            logger.error(f"delete data from {index_name} failed: {e}")
            return None

    def update_data(self, index_name, id, data):
        """更新数据"""
        try:
            client = self._client()
            resp = client.update(index=index_name, id=id, doc=data)
            logger.info(f"update data in {index_name} successfully, response: {resp}")
            return resp['_id']
        except Exception as e:
            logger.error(f"update data in {index_name} failed: {e}")
            return False

    # ---------------- LLM 精读结果缓存（llm_reads 索引） ----------------

    def get_llm_read(self, key, index_name: Optional[str] = None):
        """按 key 读取 LLM 精读缓存，未命中返回 None。"""
        index_name = index_name or DEFAULT_LLM_READS_INDEX
        if not self.ensure_index(index_name, LLM_READS_INDEX_BODY):
            return None
        try:
            client = self._client()
            resp = client.get(index=index_name, id=key)
            return resp.get('_source')
        except Exception:
            return None

    def save_llm_read(self, key, scope, input_hash, model, result, index_name: Optional[str] = None):
        """写入/更新 LLM 精读缓存（以 key 为文档 ID 幂等覆盖）。"""
        index_name = index_name or DEFAULT_LLM_READS_INDEX
        if not self.ensure_index(index_name, LLM_READS_INDEX_BODY):
            return False
        try:
            client = self._client()
            doc = {
                "key": key,
                "scope": scope,
                "input_hash": input_hash,
                "model": model,
                "result": result,
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            client.index(index=index_name, id=key, document=doc)
            logger.info(f"save llm read cache {key} successfully.")
            return True
        except Exception as e:
            logger.error(f"save llm read cache {key} failed: {e}")
            return False

    def search_data(self, query, index_name=None):
        """搜索数据"""
        try:
            index_name = index_name or self.default_index
            client = self._client()
            result = client.search(index=index_name, body=query)
            logger.info(f"search data in {index_name} successfully, response: {result}")
            return result['hits']['hits']
        except Exception as e:
            logger.error(f"search data in {index_name} failed: {e}")
            return None
    
    def delete_index(self, index_name):
        """删除索引"""
        try:
            client = self._client()
            resp = client.indices.delete(index=index_name)
            logger.info(f"delete index {index_name} successfully, response: {resp}")
            return True
        except Exception as e:
            logger.error(f"delete index {index_name} failed: {e}")
            return False

    def close_connection(self):
        """关闭连接"""
        try:
            client = self._client()
            client.close()
            logger.info("close connection successfully")
            return True
        except Exception as e:
            logger.error(f"close connection failed: {e}")
            return False
    
    # Tender specific helpers

    def _tender_to_doc(self, tender: Union[Mapping, object]) -> Mapping:
        """将Tender类型（dataclass或dict）转换为ES文档"""
        if tender is None:
            raise ValueError("tender is None")
        if is_dataclass(tender):
            doc = asdict(tender)
        elif isinstance(tender, Mapping):
            doc = dict(tender)
        else:
            raise TypeError(f"Unsupported tender type: {type(tender)}")
        doc.setdefault("region", "")
        doc.setdefault("href", "")
        doc.setdefault("title", "")
        doc.setdefault("release_date", "")
        doc.setdefault("crawl_date", "")
        doc.setdefault("html", "")
        return doc

    def save_tender(self, tender: Union[Mapping, object], index_name: Optional[str] = None) -> Optional[str]:
        """单条保存Tender，使用href作为文档ID"""
        if not self.ensure_index(index_name):
            return None
        doc = self._tender_to_doc(tender)
        doc_id = doc.get("href")
        if not doc_id:
            logger.error("tender href is empty, skip saving.")
            return None
        target_index = index_name or self.default_index
        return self.insert_data(target_index, doc, doc_id=doc_id)

    def save_tenders_bulk(
        self,
        tenders: Iterable[Union[Mapping, object]],
        index_name: Optional[str] = None,
        chunk_size: int = 500,
    ) -> Optional[dict]:
        """批量保存Tender，使用href作为文档ID"""
        if not self.ensure_index(index_name):
            return None
        client = self._client()
        if not client:
            return None
        target_index = index_name or self.default_index

        actions = []
        for tender in tenders:
            doc = self._tender_to_doc(tender)
            doc_id = doc.get("href")
            if not doc_id:
                logger.warning("Encountered tender without href, skip.")
                continue
            actions.append({
                "_index": target_index,
                "_id": doc_id,
                "_source": doc,
            })

        if not actions:
            logger.info("No valid tender data to save.")
            return None

        try:
            resp = helpers.bulk(client, actions, chunk_size=chunk_size, raise_on_error=False, stats_only=False)
            success_count, errors = resp[0], resp[1]
            failed_count = len(actions) - success_count
            if errors:
                logger.error(f"bulk save tenders failed {failed_count}/{len(actions)}: "
                             f"{errors[0] if isinstance(errors, list) and errors else errors}")
            else:
                logger.info(f"save {len(actions)} tenders to {target_index} successfully.")
            return {"took": resp[1], "success_count": success_count, "failed_count": failed_count}
        except Exception as e:
            logger.error(f"bulk save tenders failed: {e}")
            return None

    def __del__(self):
        self.close_connection()


if __name__ == "__main__":
    es = ESConnection()
    es.create_connection()
    es.test_connection()
    query_body = {
        "query": {
            "term": {
                "region": "beijing"
            }
        },
        "_source": False,  # 只返回 href 字段，减少数据传输
        "size": 10000  # 调整返回结果数量，根据你的数据量设置
    }
    d = es.search_data("tenders", query_body)
    print(d)