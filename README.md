# 多源信息交叉验证系统

模块化后端骨架，默认使用 mock 数据演示；配置 Firecrawl、Exa 后可切换真实检索。

## 启动

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
uvicorn crosscheck.main:app --reload
```

打开 http://127.0.0.1:8000/docs 查看接口。

打开 http://127.0.0.1:8000/ 查看中文核验工作台界面。界面由 FastAPI 直接提供，无需单独启动前端开发服务器。

提交核验后，页面会通过流式接口实时显示主张解析、并行搜索、页面抓取和证据比对进度；普通接口 `/api/v1/verifications` 仍保留，便于脚本调用。

点击左侧“来源管理”即可配置 Mock、Exa 和 Firecrawl：启用搜索后端、填写 API 地址和 API Key、选择正文抓取服务，然后点击“保存配置”。配置会保存到项目根目录的 `config.local.json`，该文件已加入 `.gitignore`；页面只显示 API Key 的掩码。保存后下一次核验立即使用新配置。

## 配置真实供应商

```dotenv
SEARCH_PROVIDERS=exa,firecrawl
CONTENT_PROVIDER=firecrawl
EXA_API_KEY=...
FIRECRAWL_API_KEY=...
```

搜索供应商只负责发现候选网页，正文由内容供应商获取；业务层不依赖具体供应商。

当 Exa 和 Firecrawl 都配置了密钥时，正文会并行从两个服务获取：默认优先使用页面配置中选择的服务，另一个成功结果会记录在证据元数据中；首选服务失败时自动回退到另一个服务。

来源管理页面还可以配置一个 OpenAI 兼容的大模型。配置后，大模型负责把自然语言主张拆解为主体、对象、行为、事件、时间、地点、数量和范围；调用失败时自动回退到规则解析，并在报告中标明解析方式。

同时可以配置备用语言模型。系统先调用主模型，主模型超时、返回错误或输出无法解析时自动调用备用模型；两者都失败才使用规则解析。

“来源管理”可以分别选择主张解析器和证据判定器。默认主张解析使用兼容接口模型，证据判定使用“本地模型 + 大模型”模式。默认本地模型为压缩的 CFEVER 训练模型 `models/evidence_relation_cfever.json.gz`。训练数据放在 `data/relation_training.jsonl`，模型和评估结果放在 `models/`。

混合模式会让本地模型和兼容接口大模型独立判断每条证据：两者一致时保留该关系，分歧时标为“证据不足”并在证据理由中展示双方判断。任一模型不可用时使用另一方，并在报告中提示；两者都不可用时回退规则判断。证据判定器也可在页面单独切换为本地模型、大模型或规则模式。混合模式会产生证据判定阶段的大模型请求。

训练或重新训练：

```bash
python -m crosscheck.ml.train --create-starter-data
```

本地模型是项目内从零实现的多分类线性模型：用中文字符 2/3-gram、主张与证据的重叠程度、主体/时间/对象/行为是否出现、数量差异和否定词等特征，经过在线梯度更新学习 `supports`、`refutes`、`insufficient` 三类。运行时不会加载预训练词向量、第三方 NLP 模型或机器学习运行库。规则分析先做主体、年份和关键数量的安全检查，模型只在证据没有明显硬冲突且置信度达到阈值时改变关系。

替换为人工标注数据时，每行 JSONL 至少包含：

```json
{"claim":"上海市2026年所有电动自行车上路","evidence":"政策原文……","label":"refutes","group":"source-001"}
```

`label` 只能是 `supports`、`refutes` 或 `insufficient`；同一来源或同一事件应使用相同的 `group`，训练脚本会按 group 划分测试集，避免同一来源同时出现在训练和测试中。

默认样本是可运行基线，不代表真实网页准确率。部署前应替换为人工标注的主张—证据数据，并按 `group` 划分训练集和测试集。

项目还提供了 CFEVER 中文事实核查数据的导入说明，见 [`data/external/cfever/README.md`](data/external/cfever/README.md)。导入后的数据包含真实中文主张和 Wikipedia 证据句，可使用 `crosscheck.ml.train` 单独训练和评估；其中 CFEVER 的证据不足样本需要构造负例，不能直接当作完全人工标注数据。

## 排查问题

诊断日志自动保存到 `logs/crosscheck-进程号.jsonl`，记录请求 ID、阶段耗时、模型主备切换、HTTP 状态和失败原因。报告编号对应日志中的请求 ID；默认不保存主张、正文或模型原文。

查看 [日志排查说明](docs/diagnostics.md)，了解如何筛选某次核验、查看超时原因，以及临时开启脱敏的模型输出片段。

## 目录

```text
src/crosscheck/
  api/          HTTP 路由
  domain/       领域模型和接口协议
  providers/    Exa、Firecrawl、Mock 适配器
  services/     编排、来源去重、证据判断
```
