# LivingMemory - 动态生命周期记忆插件


<div align="center">

<img src="https://img.shields.io/badge/状态-开发中-critical?style=for-the-badge&logo=github" alt="开发中" />

<details open>
<summary><strong>🚧 施工警示卡</strong></summary>

> ⚠️ **本插件处于开发阶段**  
> 部分功能尚未完善或可能存在不稳定情况。  
> 使用过程中如遇到问题，欢迎通过 Issue 反馈！

</details>

</div>


<p align="center">
  <i>为 AstrBot 打造的、拥有完整记忆生命周期的智能长期记忆插件。</i>
  <br><br>
  <!-- 技术徽章 -->
  <img src="https://img.shields.io/badge/Python-3.8+-blue.svg" alt="Python">
  <img src="https://img.shields.io/badge/Faiss-CPU-orange.svg" alt="Faiss">
  <img src="https://img.shields.io/github/license/lxfight/astrbot_plugin_livingmemory?style=flat-square&color=green" alt="License">
  <!-- GitHub 统计 -->
  <a href="https://github.com/lxfight/astrbot_plugin_livingmemory">
    <img src="https://img.shields.io/github/stars/lxfight/astrbot_plugin_livingmemory?style=social" alt="GitHub Stars">
  </a>
  <!-- 访客计数器 -->
  <img src="https://komarev.com/ghpvc/?username=lxfight&repo=astrbot_plugin_livingmemory&color=blueviolet" alt="Visitor Count">
</p>

<p align="center">
  <a href="https://github.com/anuraghazra/github-readme-stats">
    <img align="center" src="https://github-readme-stats.vercel.app/api?username=lxfight&show_icons=true&theme=radical&rank_icon=github" />
  </a>
  <a href="https://github.com/anuraghazra/github-readme-stats">
    <img align="center" src="https://github-readme-stats.vercel.app/api/top-langs/?username=lxfight&layout=compact&theme=radical" />
  </a>
</p>

---

`LivingMemory` 告别了传统记忆插件`astrbot_plugin_mnemosyne`对大型数据库的依赖，创新性地采用轻量级的 `Faiss` 和 `SQLite` 作为存储后端。这不仅实现了 **零配置部署** 和 **极低的资源消耗**，更引入了革命性的 **动态记忆生命周期 (Dynamic Memory Lifecycle)** 模型。

## ✨ 核心特性：动态记忆生命周期

本插件通过三大智能引擎的协同工作，完美模拟了人类记忆的形成、巩固、联想和遗忘的全过程。

| 引擎 | 图标 | 核心功能 | 描述 |
| :--- | :---: | :--- | :--- |
| **反思引擎** | 🧠 | `智能总结` & `重要性评估` | 在对话中自动提炼关键信息，形成结构化记忆，并利用 LLM 评估其重要性，确保高价值信息得以保留。 |
| **回忆引擎** | 🔍 | `多策略召回` & `动态刷新` | 综合考虑记忆的 **相关性**、**重要性** 和 **新近度**，精准召回最匹配的记忆。每次成功回忆都会强化该记忆，模拟人类的记忆强化效应。 |
| **遗忘代理** | 🗑️ | `模拟遗忘曲线` & `自动修剪` | 作为后台任务，它会模拟艾宾浩斯遗忘曲线，让记忆的重要性随时间自然衰减，并自动清理那些陈旧且价值低的记忆，保持记忆库的健康与高效。 |

## 🚀 快速开始

### 1. 安装

将 `astrbot_plugin_livingmemory` 文件夹放置于 AstrBot 的 `data/plugins` 目录下。AstrBot 将自动检测并安装 `requirements.txt` 中声明的依赖。

**依赖项:**
```
faiss-cpu
```
> **注意**: 请确保您的系统环境支持 `faiss-cpu` 的编译和安装。

### 2. 配置

所有详细配置均可在 **AstrBot WebUI -> 插件管理 -> LivingMemory** 中进行调整。每个配置项都提供了清晰的中文说明，方便您快速上手。

<details>
<summary><strong>⚙️ 点击展开详细配置说明</strong></summary>

#### Provider 设置
- **Embedding Provider ID**: 用于生成向量的 Embedding Provider。留空则自动使用第一个加载的向量服务。
- **LLM Provider ID**: 用于总结和评估记忆的 LLM Provider。留空则使用 AstrBot 的默认 LLM 服务。
- **Timezone**: 用于解析和显示时间的时区。默认为 `Asia/Shanghai`。

#### 回忆引擎 (Recall Engine)
- **top_k**: 单次检索返回的记忆数量。
- **recall_strategy**: 召回策略。`similarity` (仅相似度) 或 `weighted` (综合加权)。
- **权重配置**: 当使用 `weighted` 策略时，可自由调整 **相似度**、**重要性** 和 **新近度** 的权重。

#### 过滤设置 (Filtering Settings)
- **use_persona_filtering**: 开启后，只会召回和总结与当前人格相关的记忆。
- **use_session_filtering**: 开启后，每个会话的记忆将是独立的。

#### 反思引擎 (Reflection Engine)
- **summary_trigger_rounds**: 触发对话历史总结的对话轮次。
- **importance_threshold**: 记忆重要性得分的最低阈值。
- **summary_prompt / evaluation_prompt**: 可自定义用于指导 LLM 进行总结和评估的提示。

#### 遗忘代理 (Forgetting Agent)
- **enabled**: 是否启用自动遗忘。
- **check_interval_hours**: 遗忘代理的运行周期（小时）。
- **retention_days**: 记忆的最长无条件保留天数。
- **importance_decay_rate**: 重要性得分的每日衰减率。

</details>

## 🛠️ 管理命令

插件在后台自动运行，无需用户干预。同时，也为管理员提供了以下命令来便捷地管理记忆库：

> **数据模型变更**: 请注意，从最新版本开始，记忆的唯一标识符（ID）已从 UUID 更改为自增整数。这简化了管理操作。

| 命令 | 参数 | 描述 |
| :--- | :--- | :--- |
| `/lmem status` | - | 查看当前记忆库的状态，如总记忆数。 |
| `/lmem search` | `<query> [k=3]` | 手动搜索与 `<query>` 相关的记忆，并以卡片形式展示结果。 |
| `/lmem forget` | `<memory_id>` | 强制删除一条指定整数 ID 的记忆。 |
| `/lmem run_forgetting_agent` | - | 手动触发一次遗忘代理的清理任务。 |

---

## 🤝 贡献

欢迎各种形式的贡献，包括提交问题 (Issues)、请求新功能 (Feature Requests) 和代码贡献 (Pull Requests)。

## 📄 许可证

本项目遵循 AGPLv3 许可证。请查看 [LICENSE](LICENSE) 文件以获取更多信息。
