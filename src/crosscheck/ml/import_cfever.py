"""Convert CFEVER claim/evidence references into this project's JSONL format.

CFEVER stores gold evidence as (Wikipedia page, sentence id) references. This
adapter resolves those references from the downloaded wiki shards. Its
NOT-ENOUGH-INFO rows have no gold evidence, so the adapter creates a clearly
marked same-domain negative passage from another page. Those constructed rows
should be kept separate when reporting benchmark results.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

LABEL_MAP = {
    "supports": "supports",
    "refutes": "refutes",
    "NOT ENOUGH INFO": "insufficient",
    "not enough info": "insufficient",
}


def load_rows(paths: list[Path]) -> list[dict]:
    rows = []
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def referenced_pages(rows: list[dict]) -> set[str]:
    pages = set()
    for row in rows:
        for evidence_set in row.get("evidence", []):
            for item in evidence_set:
                if item.get("page_title"):
                    pages.add(item["page_title"])
    return pages


def load_wiki_pages(wiki_dir: Path, wanted: set[str]) -> dict[str, dict[int, str]]:
    pages: dict[str, dict[int, str]] = {}
    remaining = set(wanted)
    for path in sorted(wiki_dir.glob("wiki-*.jsonl")):
        if not remaining:
            break
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                page = json.loads(line)
                page_id = page.get("id")
                if page_id not in remaining:
                    continue
                lines: dict[int, str] = {}
                for raw_line in str(page.get("lines", "")).splitlines():
                    line_id, separator, text = raw_line.partition("\t")
                    if separator:
                        try:
                            lines[int(line_id)] = text.split("\t", 1)[0].strip()
                        except ValueError:
                            continue
                pages[page_id] = lines
                remaining.remove(page_id)
    return pages


def gold_evidence(row: dict, pages: dict[str, dict[int, str]]) -> tuple[str, list[str]]:
    for evidence_set in row.get("evidence", []):
        texts = []
        page_names = []
        for item in evidence_set:
            page = item.get("page_title")
            sentence_id = item.get("sentence_id")
            if not page or sentence_id is None or page not in pages:
                texts = []
                break
            text = pages[page].get(int(sentence_id), "")
            if not text:
                texts = []
                break
            texts.append(text)
            page_names.append(page)
        if texts:
            return " ".join(texts), sorted(set(page_names))
    return "", []


def _negative_pool(rows: list[dict], pages: dict[str, dict[int, str]]) -> dict[str, list[tuple[str, str]]]:
    pool: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for row in rows:
        label = LABEL_MAP.get(row.get("label"))
        if label not in {"supports", "refutes"}:
            continue
        text, page_names = gold_evidence(row, pages)
        if text and page_names:
            pool[str(row.get("domain", "unknown"))].append((page_names[0], text))
    return pool


def convert(rows: list[dict], pages: dict[str, dict[int, str]]) -> list[dict]:
    negative_pool = _negative_pool(rows, pages)
    output = []
    for row in rows:
        label = LABEL_MAP.get(row.get("label"))
        if label is None:
            continue
        evidence_text, page_names = gold_evidence(row, pages)
        constructed = False
        if not evidence_text and label == "insufficient":
            candidates = negative_pool.get(str(row.get("domain", "unknown")), [])
            if candidates:
                index = int(row.get("id", 0)) % len(candidates)
                page_name, evidence_text = candidates[index]
                page_names = [page_name]
                constructed = True
        if not evidence_text:
            continue
        group = "cfever-pages:" + "|".join(page_names)
        output.append({
            "claim": row["claim"],
            "evidence": evidence_text,
            "label": label,
            "group": group,
            "metadata": {
                "source": "CFEVER",
                "source_id": str(row.get("id")),
                "domain": row.get("domain"),
                "constructed_insufficient": constructed,
            },
        })
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="导入 CFEVER 中文事实核查数据")
    parser.add_argument("--train", type=Path, required=True)
    parser.add_argument("--dev", type=Path)
    parser.add_argument("--wiki-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    input_paths = [args.train] + ([args.dev] if args.dev else [])
    rows = load_rows(input_paths)
    pages = load_wiki_pages(args.wiki_dir, referenced_pages(rows))
    converted = convert(rows, pages)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in converted), encoding="utf-8")
    labels = defaultdict(int)
    constructed = 0
    for row in converted:
        labels[row["label"]] += 1
        constructed += int(row.get("metadata", {}).get("constructed_insufficient", False))
    print(json.dumps({"input_rows": len(rows), "resolved_pages": len(pages), "output_rows": len(converted),
                      "labels": dict(labels), "constructed_insufficient": constructed,
                      "output": str(args.output)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
