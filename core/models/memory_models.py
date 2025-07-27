# -*- coding: utf-8 -*-

import dataclasses
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional


@dataclass
class LinkedMedia:
    media_id: str
    media_type: str
    url: str
    caption: str
    embedding: List[float] = field(default_factory=list)


@dataclass
class AccessInfo:
    initial_creation_timestamp: str
    last_accessed_timestamp: str
    access_count: int = 1


@dataclass
class EmotionalValence:
    sentiment: str
    intensity: float


@dataclass
class UserFeedback:
    is_accurate: Optional[bool] = None
    is_important: Optional[bool] = None
    correction_text: Optional[str] = None


@dataclass
class CommunityInfo:
    id: Optional[str] = None
    last_calculated: Optional[str] = None


@dataclass
class Metadata:
    source_conversation_id: str
    memory_type: str  # 'episodic', 'semantic', 'procedural'
    importance_score: float
    access_info: AccessInfo
    confidence_score: Optional[float] = None
    emotional_valence: Optional[EmotionalValence] = None
    user_feedback: UserFeedback = field(default_factory=UserFeedback)
    community_info: CommunityInfo = field(default_factory=CommunityInfo)
    session_id: Optional[str] = None
    persona_id: Optional[str] = None


@dataclass
class EventEntity:
    event_id: str
    event_type: str


@dataclass
class Entity:
    entity_id: str
    name: str
    type: str
    role: Optional[str] = None


@dataclass
class KnowledgeGraphPayload:
    event_entity: EventEntity
    entities: List[Entity] = field(default_factory=list)
    relationships: List[List[str]] = field(default_factory=list)


@dataclass
class Memory:
    memory_id: str  # UUID
    timestamp: str  # ISO 8601
    summary: str
    description: str
    metadata: Metadata
    embedding: List[float] = field(default_factory=list)
    linked_media: List[LinkedMedia] = field(default_factory=list)
    knowledge_graph_payload: Optional[KnowledgeGraphPayload] = None

    # 提供一个方便的方法来将 dataclass 转换为 dict
    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    # 提供一个方便的方法从 dict 创建 dataclass 实例
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Memory":
        # 嵌套结构的递归转换
        field_types = {f.name: f.type for f in dataclasses.fields(cls)}

        # 简单递归转换，TODO 对于复杂场景可能需要更完善的库如 dacite
        for name, T in field_types.items():
            if (
                hasattr(T, "from_dict")
                and name in data
                and isinstance(data[name], dict)
            ):
                data[name] = T.from_dict(data[name])
            elif isinstance(data.get(name), list):
                # 处理 dataclass 列表
                origin = getattr(T, "__origin__", None)
                if origin is list:
                    item_type = T.__args__[0]
                    if hasattr(item_type, "from_dict"):
                        data[name] = [item_type.from_dict(item) for item in data[name]]

        return cls(**data)
