# -*- coding: utf-8 -*-
"""
结果融合器 - 实现多种检索结果的融合策略
"""

import math
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
from collections import defaultdict

from astrbot.api import logger

try:
    from astrbot.core.db.vec_db.faiss_impl.vec_db import Result
except ImportError:
    # 定义 Result 类型
    @dataclass
    class Result:
        data: Dict[str, Any]
        similarity: float


@dataclass
class SearchResult:
    """统一搜索结果"""
    doc_id: int
    content: str
    metadata: Dict[str, Any]
    dense_score: Optional[float] = None
    sparse_score: Optional[float] = None
    final_score: float = 0.0


class ResultFusion:
    """结果融合器"""
    
    def __init__(self, strategy: str = "rrf", config: Dict[str, Any] = None):
        self.strategy = strategy
        self.config = config or {}
        
        # RRF 参数
        self.rrf_k = self.config.get("rrf_k", 60)
        
        # 加权融合参数
        self.dense_weight = self.config.get("dense_weight", 0.7)
        self.sparse_weight = self.config.get("sparse_weight", 0.3)
        
        # 分数归一化参数
        self.sparse_alpha = self.config.get("sparse_alpha", 1.0)  # BM25 分数缩放
        self.sparse_epsilon = self.config.get("sparse_epsilon", 0.0)  # 最小分数偏移
        
    def fuse(
        self,
        dense_results: List[Result],
        sparse_results: List["SparseResult"],
        k: int = 10,
        query_info: Optional[Dict[str, Any]] = None
    ) -> List[SearchResult]:
        """融合检索结果"""
        if self.strategy == "rrf":
            return self._rrf_fusion(dense_results, sparse_results, k)
        elif self.strategy == "weighted":
            return self._weighted_fusion(dense_results, sparse_results, k)
        elif self.strategy == "cascade":
            return self._cascade_fusion(dense_results, sparse_results, k)
        elif self.strategy == "adaptive":
            return self._adaptive_fusion(dense_results, sparse_results, k, query_info)
        else:
            raise ValueError(f"Unknown fusion strategy: {self.strategy}")
    
    def _rrf_fusion(
        self,
        dense_results: List[Result],
        sparse_results: List["SparseResult"],
        k: int
    ) -> List[SearchResult]:
        """Reciprocal Rank Fusion (RRF)"""
        # 计算每个结果的 RRF 分数
        rrf_scores = defaultdict(float)
        result_map = {}
        
        # 处理密集检索结果
        for rank, result in enumerate(dense_results):
            doc_id = result.data["id"]
            rrf_scores[doc_id] += 1.0 / (self.rrf_k + rank + 1)
            result_map[doc_id] = result
        
        # 处理稀疏检索结果
        for rank, result in enumerate(sparse_results):
            doc_id = result.doc_id
            rrf_scores[doc_id] += 1.0 / (self.rrf_k + rank + 1)
            if doc_id not in result_map:
                result_map[doc_id] = result
        
        # 排序并返回前 k 个
        sorted_results = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
        
        final_results = []
        for doc_id, rrf_score in sorted_results[:k]:
            result = result_map[doc_id]
            
            if isinstance(result, Result):
                final_result = SearchResult(
                    doc_id=doc_id,
                    content=result.data["text"],
                    metadata=result.data.get("metadata", {}),
                    dense_score=result.similarity,
                    final_score=rrf_score
                )
            else:
                final_result = SearchResult(
                    doc_id=doc_id,
                    content=result.content,
                    metadata=result.metadata,
                    sparse_score=result.score,
                    final_score=rrf_score
                )
            
            final_results.append(final_result)
        
        return final_results
    
    def _weighted_fusion(
        self,
        dense_results: List[Result],
        sparse_results: List["SparseResult"],
        k: int
    ) -> List[SearchResult]:
        """加权融合"""
        # 归一化分数
        dense_max = max(r.similarity for r in dense_results) if dense_results else 1.0
        sparse_max = max(r.score for r in sparse_results) if sparse_results else 1.0
        
        # 合并结果
        result_map = {}
        
        # 处理密集结果
        for result in dense_results:
            doc_id = result.data["id"]
            result_map[doc_id] = SearchResult(
                doc_id=doc_id,
                content=result.data["text"],
                metadata=result.data.get("metadata", {}),
                dense_score=result.similarity / dense_max,
                final_score=0.0
            )
        
        # 处理稀疏结果并融合
        for result in sparse_results:
            doc_id = result.doc_id
            sparse_norm = result.score / sparse_max if sparse_max > 0 else 0
            
            if doc_id in result_map:
                # 已有密集结果，进行加权
                existing = result_map[doc_id]
                existing.sparse_score = sparse_norm
                existing.final_score = (
                    existing.dense_score * self.dense_weight +
                    sparse_norm * self.sparse_weight
                )
            else:
                # 只有稀疏结果
                result_map[doc_id] = SearchResult(
                    doc_id=doc_id,
                    content=result.content,
                    metadata=result.metadata,
                    sparse_score=sparse_norm,
                    final_score=sparse_norm * self.sparse_weight
                )
        
        # 排序并返回
        sorted_results = sorted(
            result_map.values(),
            key=lambda x: x.final_score,
            reverse=True
        )
        
        return sorted_results[:k]
    
    def _cascade_fusion(
        self,
        dense_results: List[Result],
        sparse_results: List["SparseResult"],
        k: int
    ) -> List[SearchResult]:
        """级联融合：先用稀疏检索初筛，再用密集向量精排"""
        if not sparse_results:
            # 没有稀疏结果，直接返回密集结果
            return self._dense_to_search_results(dense_results, k)
        
        # 取稀疏检索的前 2*k 个结果
        sparse_candidates = sparse_results[:k*2]
        candidate_ids = {r.doc_id for r in sparse_candidates}
        
        # 从密集结果中筛选候选
        dense_candidates = [
            r for r in dense_results 
            if r.data["id"] in candidate_ids
        ]
        
        # 如果密集结果不足，用稀疏结果补充
        if len(dense_candidates) < k:
            candidate_ids.update(r.data["id"] for r in dense_candidates)
            additional_sparse = [
                r for r in sparse_results 
                if r.doc_id not in candidate_ids
            ][:k - len(dense_candidates)]
            
            # 合并结果
            all_results = self._dense_to_search_results(dense_candidates, k)
            for sparse in additional_sparse:
                all_results.append(SearchResult(
                    doc_id=sparse.doc_id,
                    content=sparse.content,
                    metadata=sparse.metadata,
                    sparse_score=sparse.score,
                    final_score=sparse.score
                ))
            
            # 按分数排序
            all_results.sort(key=lambda x: x.final_score, reverse=True)
            return all_results[:k]
        else:
            # 直接使用密集结果
            return self._dense_to_search_results(dense_candidates, k)
    
    def _adaptive_fusion(
        self,
        dense_results: List[Result],
        sparse_results: List["SparseResult"],
        k: int,
        query_info: Optional[Dict[str, Any]] = None
    ) -> List[SearchResult]:
        """自适应融合：根据查询特征选择融合策略"""
        if not query_info:
            # 默认使用 RRF
            return self._rrf_fusion(dense_results, sparse_results, k)
        
        query_type = query_info.get("type", "mixed")
        query_length = query_info.get("length", 0)
        
        # 短查询或关键词查询，偏向稀疏检索
        if query_type == "keyword" or query_length < 10:
            self.sparse_weight = 0.7
            self.dense_weight = 0.3
            return self._weighted_fusion(dense_results, sparse_results, k)
        
        # 长查询或语义查询，偏向密集检索
        elif query_type == "semantic" or query_length > 50:
            self.sparse_weight = 0.2
            self.dense_weight = 0.8
            return self._weighted_fusion(dense_results, sparse_results, k)
        
        # 混合查询，使用 RRF
        else:
            return self._rrf_fusion(dense_results, sparse_results, k)
    
    def _dense_to_search_results(self, dense_results: List[Result], k: int) -> List[SearchResult]:
        """将密集结果转换为 SearchResult"""
        results = []
        for result in dense_results[:k]:
            results.append(SearchResult(
                doc_id=result.data["id"],
                content=result.data["text"],
                metadata=result.data.get("metadata", {}),
                dense_score=result.similarity,
                final_score=result.similarity
            ))
        return results
    
    def analyze_query(self, query: str) -> Dict[str, Any]:
        """分析查询特征"""
        # 简单的查询分析
        query_lower = query.lower()
        
        # 检查是否为关键词查询
        keyword_indicators = ["是", "什么", "哪里", "谁", "什么时候", "how", "what", "where", "when", "who"]
        is_keyword = any(indicator in query_lower for indicator in keyword_indicators)
        
        # 检查是否包含实体
        entity_indicators = [":", "：", "的", "'s"]
        has_entities = any(indicator in query for indicator in entity_indicators)
        
        # 确定查询类型
        if is_keyword and len(query.split()) <= 5:
            query_type = "keyword"
        elif has_entities or len(query) > 100:
            query_type = "semantic"
        else:
            query_type = "mixed"
        
        return {
            "type": query_type,
            "length": len(query),
            "word_count": len(query.split()),
            "is_keyword": is_keyword,
            "has_entities": has_entities
        }