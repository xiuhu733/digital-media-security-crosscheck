# CFEVER training data

## Source data

CFEVER is a Chinese fact-checking dataset with 24,012 training records and 3,000 development records. Each record contains a claim, one of three labels, and evidence references expressed as Wikipedia page titles and sentence IDs. The local import workflow uses 24 Wikipedia evidence shards to resolve those references into text.

- [Dataset home](https://ikmlab.github.io/CFEVER/)
- [Hugging Face dataset](https://huggingface.co/datasets/IKMLab-team/cfever)
- [Paper](https://doi.org/10.1609/aaai.v38i17.29825)

Check the source dataset's `LICENSE` and `README.md` for its terms. Wikipedia content also carries copyright and attribution obligations. Raw data and converted samples are under `data/`, which is excluded from this Git repository.

## Convert evidence references

The converter writes one JSON object per line with a claim, evidence text, label, source group, and metadata. This record illustrates the format; it is not quoted from the dataset:

```json
{"claim":"北京是中国的首都","evidence":"北京是中华人民共和国的首都。","label":"supports","group":"cfever-pages:北京","metadata":{"source":"CFEVER"}}
```

After obtaining the source files under `data/external/cfever/`, run:

```bash
./.venv/bin/python -m crosscheck.ml.import_cfever \
  --train data/external/cfever/train.jsonl \
  --dev data/external/cfever/dev.jsonl \
  --wiki-dir data/external/cfever/wiki \
  --output data/external/cfever/relation_training.jsonl
```

CFEVER's `NOT ENOUGH INFO` records have no gold evidence sentence. The converter selects a passage from another page in the same domain as constructed negative evidence and marks it with `metadata.constructed_insufficient: true`. These examples are useful for an initial classifier but are not original, manually annotated evidence pairs. Final evaluation needs an independent, manually labeled set.

## Train and evaluate

```bash
./.venv/bin/python -m crosscheck.ml.train \
  --data data/external/cfever/relation_training.jsonl \
  --model models/evidence_relation_cfever.json.gz \
  --report models/evidence_relation_cfever_metrics.json
```

The current conversion produced 27,012 pairs: 12,085 `supports`, 8,113 `refutes`, and 6,814 `insufficient`. The [saved evaluation](../models/evidence_relation_cfever_metrics.json) reports 0.673 accuracy and 0.687 macro F1 on 5,524 group-held-out pairs. Because the evaluation includes constructed `insufficient` examples and Wikipedia sentences rather than live web pages, treat these figures as a baseline for this dataset, not as live-web accuracy.
