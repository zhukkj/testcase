# from .vector_store import MilvusVectorStore
# from .embedding import BGEM3Embedder
import hashlib
import json
import re
from datetime import datetime

from ..core.models import KnowledgeBase
# from typing import List, Dict, Any
from apps.utils.logger_manager import get_logger
from django.apps import apps
from django.conf import settings


class KnowledgeService:
    """知识库服务，整合向量存储和嵌入模型"""
    
    def __init__(self):
        config = apps.get_app_config('knowledge')
        self.vector_store = config.vector_store
        self.embedder = config.embedder
        self.logger = get_logger(self.__class__.__name__)
        
    def add_knowledge(self, title: str, content: str, chunk_size: int = 1000) -> int:
        """手动添加一条知识到知识库（MySQL + Milvus 双写）

        原实现调用 self.vector_store.add_documents()，但 MilvusVectorStore
        上根本没有这个方法，一调就 AttributeError。这里改成按向量表的
        schema 组装记录、走 add_data，并把长文本切块入向量库。
        """
        content = (content or '').strip()
        if not content:
            raise ValueError('知识内容不能为空')

        # 切块：Milvus 的 content 字段上限 4096，且长文本整体 embedding 检索效果差
        chunks = [content[i:i + chunk_size] for i in range(0, len(content), chunk_size)]
        embeddings = self.embedder.get_embeddings(chunks, show_progress_bar=False)

        source = f"manual://{title}"
        upload_time = datetime.now().isoformat()
        title_hash = hashlib.md5(title.encode('utf-8')).hexdigest()[:10]

        records = []
        for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
            vec = emb.tolist() if hasattr(emb, 'tolist') else list(emb)
            records.append({
                "embedding": vec,
                "content": chunk,
                "metadata": json.dumps({"title": title}, ensure_ascii=False),
                "source": source,
                "doc_type": "manual",
                "chunk_id": f"manual_{title_hash}_{i:04d}",
                "upload_time": upload_time,
            })

        self.vector_store.add_data(records)

        knowledge = KnowledgeBase(title=title, content=content)
        knowledge.save()
        self.logger.info(f"手动添加知识「{title}」，切块 {len(records)} 个")
        return knowledge.id

    def list_documents(self) -> dict:
        """列出知识库中的全部来源文档及统计信息"""
        documents = self.vector_store.list_sources()
        return {
            'documents': documents,
            'total_documents': len(documents),
            'total_chunks': sum(d.get('chunks', 0) for d in documents),
        }

    def delete_document(self, source: str) -> int:
        """按来源删除整个文档的所有向量片段

        Returns:
            被删除的片段数
        """
        if not source:
            raise ValueError('来源标识不能为空')

        # 不能靠删除前后的 num_entities 差值：Milvus 的 num_entities 在 flush 后
        # 仍有延迟，经常算出 0。改为删除前先查该来源的片段数。
        removed = 0
        try:
            for doc in self.vector_store.list_sources():
                if doc.get('source') == source:
                    removed = doc.get('chunks', 0)
                    break
        except Exception as e:
            self.logger.warning(f"删除前统计片段数失败（不影响删除）: {e}")

        self.vector_store.delete_by_source(source)
        self.logger.info(f"删除知识文档「{source}」，清理 {removed} 个片段")
        return removed
        
    def search_knowledge(self, query: str, top_k: int = 5) -> list:
        """知识库检索（供 /api/search-knowledge/ 页面查询使用）

        与 search_relevant_knowledge 的区别：返回结构化结果列表（带相似度分数），
        不做阈值和关键词过滤，方便页面展示原始召回情况。

        Args:
            query: 查询文本
            top_k: 返回条数

        Returns:
            [{id, score, content, source, doc_type, chunk_id, upload_time}, ...]
        """
        query_embedding = self.embedder.get_embeddings(query)[0]
        results = self.vector_store.search(query_embedding, top_k=top_k)
        return sorted(results, key=lambda x: x.get("score", 0), reverse=True)[:top_k]

    @staticmethod
    def _extract_keywords(text: str) -> list:
        """提取检索关键词。

        原实现用 text.split()，中文没有空格，整句话会被当成一个 token，
        导致 `keyword in content` 几乎永远不命中，关键词过滤形同虚设。
        """
        if not text:
            return []

        keywords = set()
        for word in re.findall(r'[A-Za-z0-9_]{2,}', text):
            keywords.add(word.lower())
        for chunk in re.findall(r'[\u4e00-\u9fff]+', text):
            if len(chunk) <= 2:
                keywords.add(chunk)
            else:
                for i in range(len(chunk) - 1):
                    keywords.add(chunk[i:i + 2])

        stop = {'的', '了', '和', '是', '在', '有', '请', '为', '与', '或', '等', '中', '个'}
        return [kw for kw in keywords if kw not in stop]

    def search_relevant_knowledge(self, query: str, top_k: int = 5,
                                  min_score_threshold: float = None) -> str:
        """搜索相关知识（阈值默认取 settings.RAG_MIN_SCORE）"""
        if min_score_threshold is None:
            min_score_threshold = getattr(settings, 'RAG_MIN_SCORE', 0.35)

        query_embedding = self.embedder.get_embeddings(query)[0]
        self.logger.info(f"知识库查询: '{query[:60]}' 向量维度: {len(query_embedding)}")

        search_k = max(top_k * 3, 10)
        results = self.vector_store.search(query_embedding, top_k=search_k)

        sorted_results = sorted(
            [item for item in results if item.get("score", 0) >= min_score_threshold],
            key=lambda x: x.get("score", 0),
            reverse=True,
        )
        self.logger.info(
            f"知识库召回 {len(results)} 条，阈值 {min_score_threshold} 过滤后 {len(sorted_results)} 条"
        )

        keywords = self._extract_keywords(query)
        if keywords:
            def hit_count(item):
                content = (item.get("content") or "").lower()
                return sum(1 for kw in keywords if kw in content)
            sorted_results.sort(key=lambda x: (hit_count(x), x.get("score", 0)), reverse=True)

        top_results = sorted_results[:top_k]

        seen, content_list = set(), []
        for item in top_results:
            content = item.get("content")
            if not content:
                continue
            fingerprint = content[:80]
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            content_list.append(content)

        if not content_list:
            self.logger.info("知识库未命中任何内容")
            return ""

        self.logger.info(f"知识库最终注入 {len(content_list)} 条片段")
        return "\n\n".join(content_list)


def get_knowledgeService_instance():
    """获取知识库服务实例"""
    config = apps.get_app_config("knowledge")
    return KnowledgeService()
    # return config.vector_store, config.embedder