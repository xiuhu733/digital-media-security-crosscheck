import re

from crosscheck.domain.models import Claim, Document, Evidence, Relation


def _year_matches(claim: Claim, text: str) -> bool:
    if not claim.time or not re.match(r"\d{4}年", claim.time):
        return True
    return claim.time[:4] in text


def _subject_matches(claim: Claim, text: str) -> bool:
    if not claim.subject:
        return True
    aliases = {"北京理工大学": ("北京理工大学", "北理工"), "上海": ("上海",)}
    return any(alias in text for alias in aliases.get(claim.subject, (claim.subject,)))


def _duration(text: str) -> int | None:
    patterns = (r"放假[^。；\n]{0,40}?共\s*(\d+)\s*天", r"共\s*(\d+)\s*天", r"放假[^。；\n]{0,30}?(\d+)\s*天")
    durations = set()
    for pattern in patterns:
        durations.update(int(match.group(1)) for match in re.finditer(pattern, text))
    return durations.pop() if len(durations) == 1 else None


def _scope_refuted(claim: Claim, content: str) -> bool:
    if not claim.scope or not claim.object:
        return False
    if claim.subject and not _subject_matches(claim, content):
        return False
    if not _year_matches(claim, content):
        return False
    for sentence in re.split(r"[。！？；\n]", content):
        if claim.object not in sentence:
            continue
        if any(f"{prefix}{claim.object}" in sentence for prefix in ("不涉及所有", "并非全部", "不是所有", "并不是所有")):
            return True
    return False


def _relevant_excerpt(claim: Claim, content: str, max_chars: int = 1800) -> str:
    """Keep evidence-bearing sentences even when a page has a long preamble."""
    if len(content) <= max_chars:
        return content
    sentences = [part.strip() for part in re.split(r"(?<=[。！？；])|\n+", content) if part.strip()]
    terms = [value for value in (claim.subject, claim.object, claim.action, claim.event, claim.location) if value]
    year = re.search(r"\d{4}", claim.time or "")

    def score(sentence: str) -> int:
        points = sum(3 for term in terms if term in sentence)
        if year and year.group() in sentence:
            points += 2
        if claim.duration_days is not None and re.search(r"\d+\s*天", sentence):
            points += 2
        return points

    ranked = sorted(enumerate(sentences), key=lambda pair: (-score(pair[1]), pair[0]))
    selected = []
    size = 0
    for index, sentence in ranked:
        if size >= max_chars or len(selected) >= 6:
            break
        if score(sentence) == 0 and selected:
            break
        selected.append((index, sentence[: max_chars - size]))
        size += len(selected[-1][1])
    if not selected:
        return content[:max_chars]
    return "\n…\n".join(sentence for _, sentence in sorted(selected))[:max_chars]


def analyze_evidence(claim: Claim, documents: list[Document]) -> list[Evidence]:
    evidence = []
    for index, document in enumerate(documents, start=1):
        content = document.content
        fetch_error = document.metadata.get("fetch_error")
        searchable = f"{document.title} {content}"
        evidence_duration = _duration(searchable)
        subject_match = _subject_matches(claim, searchable)
        year_match = _year_matches(claim, searchable)
        if fetch_error or not content.strip():
            relation = Relation.INSUFFICIENT
            reason = "正文抓取失败或内容为空，无法完成内容比对。"
        elif not subject_match:
            relation = Relation.INSUFFICIENT
            reason = f"来源与主题相关，但没有确认“{claim.subject or '该主体'}”这一主体。"
        elif not year_match:
            relation = Relation.INSUFFICIENT
            reason = f"来源主体匹配，但年份与待核验的{claim.time}不一致。"
        elif _scope_refuted(claim, content):
            relation = Relation.REFUTES
            reason = f"来源明确否定了“所有{claim.object}”这一适用范围。"
        elif claim.duration_days and evidence_duration and claim.duration_days != evidence_duration:
            relation = Relation.REFUTES
            reason = f"来源明确写明放假 {evidence_duration} 天，与主张的 {claim.duration_days} 天不一致。"
        elif claim.duration_days and evidence_duration == claim.duration_days:
            relation = Relation.SUPPORTS
            reason = f"来源主体、年份匹配，且明确写明放假 {evidence_duration} 天。"
        elif claim.action and claim.duration_days is None and claim.action in content and (not claim.object or claim.object in content) and not claim.scope:
            relation = Relation.SUPPORTS
            reason = f"来源主体、年份匹配，并明确提到“{claim.object or ''}{claim.action}”。"
        elif any(word in searchable for word in ("通知", "放假", "教学安排", "节假日")):
            relation = Relation.INSUFFICIENT
            reason = "来源主体和事件相关，但没有找到足够精确的天数或条件。"
        else:
            relation = Relation.INSUFFICIENT
            reason = "来源与待核验主张的对应关系不足。"
        excerpt = f"正文抓取失败：{fetch_error}" if fetch_error else _relevant_excerpt(claim, content)
        evidence.append(
            Evidence(
                id=f"E{index}",
                url=document.url,
                title=document.title,
                excerpt=excerpt,
                provider=document.provider,
                relation=relation,
                published_at=document.published_at,
                content_sources=document.metadata.get("content_sources", [document.provider]),
                fetch_failures=document.metadata.get("content_failures", []),
                analysis_reason=reason,
            )
        )
    return evidence


def summarize_relation(evidence: list[Evidence]) -> tuple[Relation, str]:
    relations = {item.relation for item in evidence}
    if Relation.SUPPORTS in relations and Relation.REFUTES in relations:
        return Relation.CONFLICTS, "证据中同时存在支持和反驳内容，需要人工复核来源时间与适用范围。"
    if Relation.REFUTES in relations:
        return Relation.REFUTES, "找到的证据对输入说法存在明确反驳。"
    if Relation.SUPPORTS in relations:
        return Relation.SUPPORTS, "找到的证据对输入说法提供了支持，但仍应核对原文适用范围。"
    return Relation.INSUFFICIENT, "当前证据不足以支持或反驳该说法。"
