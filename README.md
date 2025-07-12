# LivingMemory - 活的记忆

`LivingMemory` 是一个为 AstrBot 设计的、拥有完整记忆生命周期管理的智能长期记忆插件。

与依赖大型数据库的传统记忆插件不同，`LivingMemory` 使用轻量级的 `Faiss` 和 `SQLite` 作为存储后端，实现了低资源消耗和零配置部署。

## ✨ 核心特性

本插件的核心是 **动态记忆生命周期 (Dynamic Memory Lifecycle)** 模型，由三大引擎协同工作：

1.  🧠 **反思引擎 (Reflection Engine)**
    *   **智能总结**: 在对话进行到一定阶段时，自动将零散的对话历史总结成结构化的、有意义的记忆条目。
    *   **重要性评估**: 利用 LLM 对生成的每条记忆进行重要性打分，确保只有高质量、高价值的信息才会被长期保存。

2.  🔍 **回忆引擎 (Recall Engine)**
    *   **多策略召回**: 不再是简单的文本相似度匹配。回忆引擎会综合考虑记忆的 **相关性**、**重要性** 和 **新近度**，通过加权算法计算出最符合当前上下文的记忆。
    *   **动态更新**: 每次记忆被成功召回，它的“新近度”就会被刷新，使其在短期内更容易被再次访问，模拟了人类的短期记忆强化效应。

3.  🗑️ **遗忘代理 (Forgetting Agent)**
    *   **模拟遗忘曲线**: 作为一个后台任务，遗忘代理会定期运行，模拟人类的遗忘过程。
    *   **价值衰减**: 记忆的重要性会随着时间的推移而自然衰减。
    *   **自动修剪**: 那些长时间未被访问、且重要性已衰减到阈值以下的陈旧记忆，将被自动清理，从而防止信息过载，保持记忆库的高效和健康。

## 🚀 安装

1.  将 `astrbot_plugin_livingmemory` 文件夹放置于 AstrBot 的 `data/plugins` 目录下。
2.  AstrBot 会自动检测 `requirements.txt` 并安装所需依赖。请确保您的环境可以编译 `faiss-cpu`。

    ```
    faiss-cpu
    ```

## ⚙️ 配置

插件的详细配置可以在 AstrBot 的 WebUI -> 插件管理 -> LivingMemory 中找到。所有配置项都有详细的中文说明。

### Provider 设置
- **Embedding Provider ID**: 用于生成向量的 Embedding Provider。如果留空，插件将自动尝试使用第一个加载的向量服务。
- **LLM Provider ID**: 用于总结和评估记忆的 LLM Provider。如果留空，将使用 AstrBot 的默认 LLM 服务。

### 回忆引擎 (Recall Engine)
- **top_k**: 单次检索返回的记忆数量。
- **recall_strategy**: 召回策略。`similarity` 仅基于相似度，`weighted` 会综合考虑相似度、重要性和新近度。
- **权重配置**: 当使用 `weighted` 策略时，可以调整三者的权重。

### 过滤设置 (Filtering Settings)
- **use_persona_filtering**: 是否启用人格记忆过滤。开启后，只会召回和总结与当前人格相关的记忆。
- **use_session_filtering**: 是否启用会话记忆隔离。开启后，每个会话的记忆将是独立的。

### 反思引擎 (Reflection Engine)
- **summary_trigger_rounds**: 触发对话历史总结的对话轮次（一问一答为一轮）。
- **importance_threshold**: 记忆重要性得分的最低阈值，低于此值的记忆将被忽略。
- **summary_prompt / evaluation_prompt**: 可自定义用于指导 LLM 进行总结和评估的提示。

### 遗忘代理 (Forgetting Agent)
- **enabled**: 是否启用自动遗忘。
- **check_interval_hours**: 遗忘代理的运行周期。
- **retention_days**: 记忆的最长无条件保留天数。
- **importance_decay_rate**: 重要性得分的每日衰减率。

## 🛠️ 使用方法

插件通过后台事件钩子自动工作，无需用户干预。同时，也为管理员提供了以下命令来管理记忆库：

-   `/lmem status`
    -   查看当前记忆库的状态，如总记忆数。
-   `/lmem search <query> [k=3]`
    -   手动搜索与 `<query>` 相关的记忆。
-   `/lmem forget <doc_id>`
    -   强制删除一条指定 ID 的记忆。
-   `/lmem run_forgetting_agent`
    -   手动触发一次遗忘代理的清理任务。

