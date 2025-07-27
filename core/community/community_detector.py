# -*- coding: utf-8 -*-
import aiosqlite
import networkx as nx
from typing import List, Tuple


class CommunityDetector:
    """
    一个后台服务，负责从 SQLite 加载图数据，
    使用 NetworkX 进行社区发现，并将结果写回数据库。
    """

    def __init__(self, db_path: str):
        self.db_path = db_path

    async def _load_graph_from_db(self) -> nx.Graph:
        """从 SQLite 中加载边，构建一个 NetworkX 图对象。"""
        G = nx.Graph()
        async with aiosqlite.connect(self.db_path) as conn:
            # 我们只需要边的信息来构建图的结构
            cursor = await conn.execute("SELECT source_id, target_id FROM graph_edges")
            edges = await cursor.fetchall()

        G.add_edges_from(edges)
        return G

    async def _save_results_to_db(self, communities: List[Tuple[str]]):
        """将计算出的社区结果批量更新回 memories 表。"""
        # 注意：这里的逻辑需要一个从“图节点ID”到“记忆internal_id”的映射
        # 我们简化一下，假设 Event 节点的 ID 就是 memory_id
        updates = []
        for i, community_nodes in enumerate(communities):
            community_id = f"community_{i}"
            for node_id in community_nodes:
                # 假设事件节点的 entity_id 格式为 'evt_mem_xxx'
                if node_id.startswith("evt_mem_"):
                    memory_id = node_id.split("evt_mem_")[1]
                    updates.append((community_id, memory_id))

        if updates:
            async with aiosqlite.connect(self.db_path) as conn:
                await conn.executemany(
                    "UPDATE memories SET community_id = ? WHERE memory_id = ?", updates
                )
                await conn.commit()

    async def run_detection_and_update(self):
        """
        执行社区发现的完整流程。
        """
        print("开始从数据库加载图...")
        graph = await self._load_graph_from_db()

        if graph.number_of_nodes() == 0:
            print("图中没有节点，跳过社区发现。")
            return

        print("图加载完成，开始运行 Louvain 社区发现算法...")
        # resolution 参数可以调整社区的大小，值越小社区越多越小
        communities = nx.community.louvain_communities(graph, resolution=1.0)

        print(f"发现 {len(communities)} 个社区，开始将结果写回数据库...")
        await self._save_results_to_db(communities)
        print("社区信息更新完成。")
