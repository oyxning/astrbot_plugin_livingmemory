# 关于 livingmemory 插件的 TODO 列表

- [ ] 设计记忆的数据结构
- [ ] 设计记忆的召回方式
- [ ] 设计记忆的更新方式
- [ ] 记忆的遗忘逻辑

# 一些潜在的规划

{
  "memory_id": "String", // [主键] 记忆的全局唯一标识符 (建议使用UUID)
  "timestamp": "String", // 事件发生的时间戳 (ISO 8601格式, e.g., "2025-07-26T14:30:00Z")
  "summary": "String", // AI生成的、可供快速预览的单行摘要或标题
  "description": "String", // 对事件的完整、详细的自然语言描述
  "embedding": "Array<Float>", // 由`description`或`summary`+`description`生成的文本嵌入向量，用于Faiss

  "linked_media": [
    // 多模态内容部分
    {
      "media_id": "String", // 媒体文件的唯一ID
      "media_type": "String", // 'image', 'audio', 'document', 'video', 'code_snippet'
      "url": "String", // 文件的存储位置 (e.g., S3 URL or local path)
      "caption": "String", // 对媒体内容的简短描述
      "embedding": "Array<Float>" // 媒体文件本身的多模态嵌入 (e.g., CLIP vector for images)
    }
  ],
  "metadata": {
    "source_conversation_id": "String", // 此记忆来源的对话ID，用于溯源
    "memory_type": "String", // 记忆类型: 'episodic' (情景), 'semantic' (事实), 'procedural' (程序)
    "importance_score": "Float", // [遗忘引擎关键输入] 记忆的重要性评分 (0.0 to 1.0)
    "confidence_score": "Float", // [可选] NLU模型提取此记忆信息的置信度 (0.0 to 1.0)
    "access_info": {
      "initial_creation_timestamp": "String", // 记忆被创建的时间
      "last_accessed_timestamp": "String", // [遗忘引擎关键输入] 记忆最近被访问的时间
      "access_count": "Integer" // [遗忘引擎关键输入] 记忆被访问的总次数
    },
    "emotional_valence": {
      "sentiment": "String", // 情感倾向: 'positive', 'negative', 'neutral'
      "intensity": "Float" // 情感强度 (0.0 to 1.0)
    },
    "user_feedback": {
      "is_accurate": "Boolean", // 用户是否标记此记忆为准确 (null, true, false)
      "is_important": "Boolean", // 用户是否标记此记忆为重要 (null, true, false)
      "correction_text": "String" // 用户提供的修正文本
    },
    "community_info": {
      // 用于图社区发现
      "id": "String", // 此记忆所属社区/簇的唯一ID
      "last_calculated": "String" // 上次计算社区分配的时间戳
    }
  },

  "knowledge_graph_payload": {
    "event_entity": {
      "event_id": "String", // [图节点] 事件的唯一ID
      "event_type": "String" // 事件的分类, e.g., 'ProjectInitiation', 'TravelPlanning'
    },
    "entities": [
      {
        "entity_id": "String", // [图节点] 实体的全局唯一ID
        "name": "String", // 实体的名称
        "type": "String", // 实体类型, e.g., 'PERSON', 'PROJECT', 'ORGANIZATION'
        "role": "String" // [可选] 实体在此次事件中的具体角色
      }
    ],
    "relationships": [
      "Array<String>" // [图的边] 定义关系的三元组 [主体ID, 关系谓词, 客体ID]
    ]
  }
}


示例

```json
{
  "memory_id": "mem_e2a1b3c4-d5e6-f7g8-h9i0-j1k2l3m4n5o6",
  "timestamp": "2025-07-28T11:00:00Z",
  "summary": "张伟分享了'凤凰计划'的登录页设计图",
  "description": "用户张伟分享了一张关于'凤凰计划'的登录页面初稿图片，并征求反馈。他还提及这张设计图关联到他们上一次会议中确定的蓝色主题。",
  "embedding": [-0.15, 0.33, 0.81, ..., -0.05],

  "linked_media": [
    {
      "media_id": "img_login_mockup_v1",
      "media_type": "image",
      "url": "s3://my-ai-project-bucket/media/img_login_mockup_v1.png",
      "caption": "'凤凰计划'登录页面的UI设计初稿",
      "embedding": [0.67, 0.12, -0.29, ..., 0.44]
    }
  ],

  "metadata": {
    "source_conversation_id": "conv_z1y2x3w-v4u5-t6s7-r8q9-p0o9n8m7l6k5",
    "memory_type": "episodic",
    "importance_score": 0.85,
    "confidence_score": 0.99,
    "access_info": {
      "initial_creation_timestamp": "2025-07-28T11:01:30Z",
      "last_accessed_timestamp": "2025-07-28T11:01:30Z",
      "access_count": 1
    },
    "emotional_valence": {
      "sentiment": "neutral",
      "intensity": 0.3
    },
    "user_feedback": {
      "is_accurate": null,
      "is_important": null,
      "correction_text": null
    },
    "community_info": {
      "id": "community_proj_phoenix_001",
      "last_calculated": "2025-07-27T04:00:00Z"
    }
  },

  "knowledge_graph_payload": {
    "event_entity": {
      "event_id": "evt_design_review_001",
      "event_type": "DesignReview"
    },
    "entities": [
      { "entity_id": "person_zhang_wei_001", "name": "张伟", "type": "PERSON", "role": "author" },
      { "entity_id": "project_phoenix_001", "name": "凤凰计划", "type": "PROJECT", "role": "context" },
      { "entity_id": "asset_login_mockup_001", "name": "登录页面初稿", "type": "DESIGN_ASSET", "role": "subject" }
    ],
    "relationships": [
      ["person_zhang_wei_001", "CREATED", "asset_login_mockup_001"],
      ["asset_login_mockup_001", "IS_PART_OF", "project_phoenix_001"],
      ["evt_design_review_001", "IS_ABOUT", "asset_login_mockup_001"],
      ["evt_design_review_001", "REFERENCES", "evt_meeting_042"]
    ]
  }
}
```

### 核心 AI 生成内容

这部分是 AI 的核心创造性工作，直接体现了模型的智能。

| 字段路径                    | 字段名           | AI 负责的工作                                                           | 需要的模型/技术                      |
| :-------------------------- | :--------------- | :---------------------------------------------------------------------- | :----------------------------------- |
| `summary`                   | **摘要**         | 阅读`description`全文，生成一段简短、精炼的标题或摘要。                 | 大型语言模型 (LLM) - 文本摘要任务    |
| `embedding`                 | **文本嵌入向量** | 将`description`的语义信息编码成一个高维浮点数向量。                     | 文本嵌入模型 (Text Embedding Model)  |
| `linked_media[].embedding`  | **媒体嵌入向量** | 将图片、音频等媒体文件编码成一个与文本在同一空间的高维向量。            | 多模态嵌入模型 (e.g., CLIP)          |
| `linked_media[].caption`    | **媒体内容描述** | (可选) 如果用户没有提供，AI 可以“看图说话”，为上传的图片生成描述。      | 视觉语言模型 (Vision-Language Model) |
| `metadata.importance_score` | **重要性评分**   | 根据`description`的内容，判断该记忆对用户的重要性，并给出一个量化分数。 | 大型语言模型 (LLM) - 分类/回归任务   |
| `metadata.confidence_score` | **置信度评分**   | NLU 模型在完成实体和关系提取后，对其结果的确定程度给出的一个分数。      | 自然语言理解模型 (NLU Model)         |

### AI 提取与结构化内容

这部分是 AI 的“理解”工作，将非结构化的对话转化为机器可读的结构化数据。

| 字段路径                                          | 字段名       | AI 负责的工作                                                                              | 需要的模型/技术                                                     |
| :------------------------------------------------ | :----------- | :----------------------------------------------------------------------------------------- | :------------------------------------------------------------------ |
| `metadata.memory_type`                            | **记忆类型** | 分析记忆内容，将其分类为情景记忆、事实记忆或程序记忆。                                     | 自然语言理解 (NLU) - 文本分类                                       |
| `metadata.emotional_valence`                      | **情感倾向** | 分析`description`中的情感色彩（积极/消极/中性）和强度。                                    | 自然语言理解 (NLU) - 情感分析                                       |
| `knowledge_graph_payload.event_entity.event_type` | **事件类型** | 将整个事件归类到一个预定义的类型中 (e.g., `TravelPlanning`)。                              | 自然语言理解 (NLU) - 文本分类                                       |
| `knowledge_graph_payload.entities`                | **实体列表** | 从`description`中识别出所有关键实体（人名、项目、组织等），并进行标准化（如分配唯一 ID）。 | 自然语言理解 (NLU) - 命名实体识别 (NER) & 实体链接 (Entity Linking) |
| `knowledge_graph_payload.relationships`           | **关系列表** | 识别并提取已识别出的实体之间的关系三元组（主语-谓词-宾语）。                               | 自然语言理解 (NLU) - 关系提取 (Relation Extraction)                 |

---

### 非 AI 生成（系统或用户提供）

为了完整性，以下是基本不需要 AI 介入，由系统逻辑、用户输入或其他算法直接填充的字段。

| 字段路径                          | 字段名     | 来源                                          |
| :-------------------------------- | :--------- | :-------------------------------------------- |
| `memory_id`                       | 记忆 ID    | 系统生成 (UUID)                               |
| `timestamp`                       | 事件时间戳 | 系统获取或用户指定                            |
| `description`                     | 详细描述   | 用户的原始输入或对话记录                      |
| `linked_media[].media_id`         | 媒体 ID    | 系统生成                                      |
| `linked_media[].url`              | 文件 URL   | 文件存储系统返回                              |
| `metadata.source_conversation_id` | 对话 ID    | 系统记录                                      |
| `metadata.access_info`            | 访问信息   | 系统根据调用情况更新（时间戳、计数器）        |
| `metadata.user_feedback`          | 用户反馈   | 用户直接提供                                  |
| `metadata.community_info`         | 社区信息   | 后台的图计算**算法**（非生成式 AI）运行后填充 |

## 2. 记忆的召回方式 (设计中)

为实现精准且富有洞察力的回忆，我们设计一个**两阶段混合检索模型**，结合向量的语义相似度和图的结构关联性。

### **阶段一：候选生成 (Candidate Generation) - 追求召回率**

1.  **输入**: 用户的当前查询（文本、图片等）。
2.  **处理**:
    - 将用户查询通过相应的嵌入模型（文本或多模态）转换为查询向量。
    - 在 Faiss 数据库中，使用此查询向量进行`k`-近邻搜索（例如，k=50），得到一个包含 50 个最相似记忆`memory_id`的初始候选集。
    - 同时，NLU 模块从查询中提取核心实体（如“张伟”）。
3.  **输出**: 一个包含`memory_id`、Faiss 相似度分数和查询实体的候选列表。

### **阶段二：重排与扩展 (Re-ranking & Expansion) - 追求精确率**

1.  **输入**: 阶段一生成的候选列表。
2.  **处理**:
    - **加载图**: 使用内存图库（如`NetworkX`）加载所有记忆的`knowledge_graph_payload`构建的知识图谱。
    - **计算图分数 (Graph Score)**: 对于候选集中的每一个记忆，计算其与“查询实体”在图中的关联强度。
      - **方法**: 可以采用个性化 PageRank（从查询实体节点开始游走），或计算图中查询实体节点到记忆事件节点的最短路径长度。路径越短，关联越强，分数越高。
    - **扩展发现**: 从查询实体出发，在图中寻找其一度或二度关联的实体和事件（例如，张伟的同事“陈静”，参与的项目“凤凰计划”）。如果候选记忆与这些扩展出的实体相关，则其图分数应获得额外加成。
    - **计算最终分数**:
      `FinalScore = (w1 * FaissScore) + (w2 * GraphScore)`
      - `w1`和`w2`是可配置的权重，例如`w1=0.6`, `w2=0.4`。
3.  **输出**: 一个根据`FinalScore`重新排序的、高质量的记忆列表，提交给上层应用（如 LLM）用于生成最终回复。

---

## 3. 记忆的更新方式 (设计中)

记忆不是一成不变的。插件必须支持记忆的演化和修正，核心原则是**优先创建、避免覆盖**，以保留信息的历史轨迹。

1.  **元数据更新**:

    - **场景**: 记忆被访问。
    - **操作**: 直接修改`metadata.access_info`中的`last_accessed_timestamp`和`access_count`字段。这是唯一允许直接原地修改的操作。

2.  **基于反馈的修正**:

    - **场景**: 用户通过`metadata.user_feedback`提供了修正（例如，“李娜是我的同事，不是朋友”）。
    - **操作**:
      a. 创建一个**新的记忆**，包含正确的`description`和`knowledge_graph_payload`。
      b. 在新记忆的`knowledge_graph_payload.relationships`中，添加一条关系指向旧记忆：`["new_memory_event_id", "CORRECTS", "old_memory_event_id"]`。
      c. 将旧记忆的`importance_score`大幅降低，或者标记为“已修正”。

3.  **事件演化更新**:
    - **场景**: 事情发生了变化（例如，会议从周二改到周三）。
    - **操作**:
      a. 创建一个**新的记忆**来记录新状态（“会议将在周三举行”）。
      b. 在新记忆的图关系中，添加`["new_event_id", "UPDATES", "old_event_id"]`。
      c. 旧记忆保持不变，但其在未来的检索中权重会因“被更新”而降低。

---

## 4. 记忆的遗忘逻辑 (设计中)

为了防止记忆无限膨胀并保持检索效率，需要一个智能的遗忘机制。该机制基于一个可计算的**“记忆衰减分数”(Decay Score)**。

1.  **衰减分数计算**:

    - 一个后台任务会定期（如每天）扫描所有记忆，并计算其衰减分数。
    - **公式**: `DecayScore = f(ElapsedTime, AccessCount, ImportanceScore, UserFeedback)`
    - **示例**: `DecayScore = (CurrentTime - last_accessed_timestamp) / (log(access_count + 1) * (importance_score + user_marked_important * 10))`
      - 这个公式意味着：时间越久，分数越高（越容易遗忘）；访问次数越多、重要性越高、被用户标记为重要，分数越低（越不容易遗忘）。

2.  **分层遗忘策略**:

    - 系统设定几个衰减分数的阈值，对应不同的操作。
    - **第一阈值 (e.g., Score > 100)**: **归档 (Archive)**。
      - **操作**: 为了节省高性能存储和 Faiss 索引空间，可以从记忆中移除`embedding`向量。记忆的 JSON 文本和元数据被转移到更廉价的“冷存储”中。此记忆不再参与常规的向量检索，但仍可通过 ID 或关键词搜索找到。
    - **第二阈值 (e.g., Score > 500)**: **标记为待删除 (Mark for Deletion)**。
      - **操作**: 系统将记忆标记为待删除，进入一个短暂的“回收站”状态。
    - **最终清理**: 一个独立的、执行频率更低的任务会永久删除那些被标记了足够长时间（如 30 天）的记忆。

3.  **豁免机制**:
    - 任何被用户通过`user_feedback`标记为`is_important: true`的记忆，其衰减分数计算将获得极高的权重，或直接豁免于遗忘逻辑。
    - 核心的`semantic`类型记忆（事实性知识）的衰减速度应远低于`episodic`类型（情景性记忆）。
