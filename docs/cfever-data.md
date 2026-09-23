# CFEVER 外部训练数据

## 内容

CFEVER 是中文事实核查数据集，原始训练集 24,012 条，开发集 3,000 条。每条记录包含主张、三分类标签和 Wikipedia 页面/句子编号形式的证据引用。本目录还保存了 24 个 Wikipedia 证据分片，用于把引用还原为实际句子。

原始数据来源：

- 数据集主页：<https://ikmlab.github.io/CFEVER/>
- Hugging Face：<https://huggingface.co/datasets/IKMLab-team/cfever>
- 论文：<https://doi.org/10.1609/aaai.v38i17.29825>

原始数据的许可说明见数据集仓库的 `LICENSE` 和 `README.md`。数据包含 Wikipedia 内容，使用和再分发时还要遵守 Wikipedia 的版权和署名要求。

## 转换后的文件

`relation_training.jsonl` 是项目训练器使用的格式：

```json
{"claim":"……","evidence":"……","label":"supports|refutes|insufficient","group":"cfever-pages:……","metadata":{"source":"CFEVER"}}
```

转换命令：

```bash
./.venv/bin/python -m crosscheck.ml.import_cfever \
  --train data/external/cfever/train.jsonl \
  --dev data/external/cfever/dev.jsonl \
  --wiki-dir data/external/cfever/wiki \
  --output data/external/cfever/relation_training.jsonl
```

CFEVER 的 `NOT ENOUGH INFO` 记录没有金标准证据句。转换脚本从同一领域的其他页面选取一个非目标页面作为构造的 `insufficient` 负例，并在 `metadata.constructed_insufficient` 中标记为 `true`。这些样本适合训练初始分类器，但不应被当成原始人工证据标注；最终评估应使用独立人工标注集。

## 训练和评估

```bash
./.venv/bin/python -m crosscheck.ml.train \
  --data data/external/cfever/relation_training.jsonl \
  --model models/evidence_relation_cfever.json.gz \
  --report models/evidence_relation_cfever_metrics.json
```

当前转换结果为 27,012 条：支持 12,085 条、反驳 8,113 条、证据不足 6,814 条。当前来源级留出评估的 Macro-F1 为 0.687；该数字包含构造的证据不足负例，只能作为当前基线。
