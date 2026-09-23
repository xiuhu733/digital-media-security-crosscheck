"""Train and evaluate the local evidence classifier.

Input JSONL rows: {"claim": str, "evidence": str, "label":
"supports|refutes|insufficient", "group": str, "fields": {...}}.
Rows sharing a group stay in the same split.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from .relation import LABELS, RelationModel, metrics

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATA = PROJECT_ROOT / "data" / "relation_training.jsonl"
DEFAULT_MODEL = PROJECT_ROOT / "models" / "evidence_relation.json"
DEFAULT_REPORT = PROJECT_ROOT / "models" / "evidence_relation_metrics.json"


def starter_samples() -> list[dict]:
    """Clearly labeled synthetic examples for an initial, limited baseline."""
    rows: list[dict] = []
    schools = ["北京理工大学", "清华大学", "复旦大学", "南京大学", "浙江大学", "武汉大学",
               "中山大学", "厦门大学", "山东大学", "吉林大学", "四川大学", "东南大学"]
    cities = ["北京市", "上海市", "杭州市", "南京市", "广州市", "深圳市",
              "成都市", "武汉市", "西安市", "苏州市", "天津市", "重庆市"]
    companies = ["星河科技", "远航集团", "晨光公司", "海岚科技", "安和公司", "卓越集团",
                 "明远科技", "山海公司", "蓝桥集团", "启明星科技", "宏达公司", "飞云集团"]
    for index, school in enumerate(schools):
        count = 7 + index % 3
        other = count + 1
        claim = f"{school}2026年国庆放假{count}天"
        fields = {"subject": school, "time": "2026年", "action": "放假", "duration_days": count}
        for evidence, label in (
            (f"{school}2026年国庆放假通知：本次假期共{count}天。", "supports"),
            (f"{school}2026年国庆放假安排明确写明共{other}天。", "refutes"),
            (f"{school}2025年国庆放假共{count}天，2026年安排尚未公布。", "insufficient"),
            (f"另一所学校2026年国庆放假共{count}天。", "insufficient"),
        ):
            rows.append({"claim": claim, "evidence": evidence, "label": label,
                         "group": f"school-{school}", "fields": fields})
    for index, city in enumerate(cities):
        object_ = "电动自行车" if index % 2 else "无人机"
        action = "禁行" if object_ == "电动自行车" else "禁飞"
        claim = f"{city}2026年所有{object_}{action}"
        fields = {"subject": city, "time": "2026年", "object_": object_,
                  "action": action, "scope": "全面"}
        for evidence, label in (
            (f"{city}2026年公告：所有{object_}{action}。", "supports"),
            (f"{city}2026年公告仅限制部分{object_}，不涉及所有{object_}。", "refutes"),
            (f"{city}2025年曾讨论{object_}管理办法，2026年政策尚未公布。", "insufficient"),
            (f"{city}2026年发布交通提示，但未说明{object_}是否{action}。", "insufficient"),
        ):
            rows.append({"claim": claim, "evidence": evidence, "label": label,
                         "group": f"city-{city}", "fields": fields})
    for index, company in enumerate(companies):
        object_ = "产品" if index % 2 else "服务"
        claim = f"{company}2026年所有{object_}免费"
        fields = {"subject": company, "time": "2026年", "object_": object_,
                  "action": "免费", "scope": "全面"}
        for evidence, label in (
            (f"{company}公告：2026年所有{object_}均免费提供。", "supports"),
            (f"{company}公告：2026年只有基础版{object_}免费，其他版本收费。", "refutes"),
            (f"{company}发布2026年{object_}介绍，价格尚未公布。", "insufficient"),
            (f"另一家公司宣布2026年全部{object_}免费。", "insufficient"),
        ):
            rows.append({"claim": claim, "evidence": evidence, "label": label,
                         "group": f"company-{company}", "fields": fields})
    return rows


def load_samples(path: Path) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    for index, row in enumerate(rows, start=1):
        if not isinstance(row.get("claim"), str) or not isinstance(row.get("evidence"), str):
            raise TypeError(f"第 {index} 行缺少主张或证据文本。")
        if row.get("label") not in LABELS:
            raise ValueError(f"第 {index} 行标签无效。")
        if not isinstance(row.get("group"), str) or not row["group"]:
            raise ValueError(f"第 {index} 行缺少分组 group。")
    return rows


def split_by_group(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    train, test = [], []
    for row in rows:
        bucket = int(hashlib.sha256(row["group"].encode()).hexdigest()[:8], 16) % 5
        (test if bucket == 0 else train).append(row)
    if {row["label"] for row in train} != set(LABELS) or {row["label"] for row in test} != set(LABELS):
        raise ValueError("按 group 划分后，训练集和测试集均须包含全部三类。")
    return train, test


def main() -> None:
    parser = argparse.ArgumentParser(description="训练本地证据关系分类器")
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--create-starter-data", action="store_true")
    args = parser.parse_args()
    if args.create_starter_data:
        args.data.parent.mkdir(parents=True, exist_ok=True)
        args.data.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in starter_samples()),
                             encoding="utf-8")
    rows = load_samples(args.data)
    train, test = split_by_group(rows)
    model = RelationModel()
    model.train(train)
    dataset_kind = "synthetic_starter" if args.create_starter_data else "user_supplied"
    limitation = (
        "合成样本测试不能代表真实网页上的准确率；部署前需要人工标注的独立测试集。"
        if args.create_starter_data
        else "测试结果取决于输入数据的标注质量和分组方式；请另外保留来源级人工测试集。"
    )
    report = {
        "dataset": str(args.data),
        "dataset_kind": dataset_kind,
        "train_count": len(train),
        "test_count": len(test),
        "group_split": "sha256(group) mod 5",
        "holdout": metrics(model, test),
        "limitation": limitation,
    }
    model.save(args.model, metadata={"dataset_kind": dataset_kind, "train_count": len(train)})
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
