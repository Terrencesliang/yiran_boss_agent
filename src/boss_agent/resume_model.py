"""AI recognition with source-bound validation for recruitment previews."""

from dataclasses import asdict, replace
import json
import re

from boss_agent.education_pipeline import validate_value
from boss_agent.resume_structure import EDUCATION_FIELDS, ResumeObservation
from boss_agent.work_timeline import PERIOD, WorkRecord


def requirement_key(rule):
    return json.dumps(asdict(rule), sort_keys=True, ensure_ascii=False)


def recognize_resume(sources, request_model, requirements=()):
    prompt_sources = {key: re.sub(r"(?<!\d)\d{1,3}\s*岁", lambda match: "█"*len(match[0]), value[:16000])
                      for key, value in sources.items()}
    try:
        payload = {"resume": prompt_sources,
                   "requirements": [{"id": index, **asdict(rule)} for index, rule in enumerate(requirements)]}
        result = json.loads(request_model(payload))
        education, work = result["education"], result["work"]
        if not isinstance(education, list) or not isinstance(work, list) or len(education) + len(work) > 100:
            raise ValueError("invalid resume recognition")
    except (OSError, ValueError, TypeError, RuntimeError, KeyError):
        return [], [], {}, "failed"

    def quote_anchor(item):
        if not isinstance(item, dict):
            return None
        source, quote = item.get("source"), item.get("quote")
        if not isinstance(source, str) or not isinstance(quote, str) or not quote.strip():
            return None
        text = prompt_sources.get(source, "")
        start = text.find(quote)
        if start < 0 or text.find(quote, start+1) != -1 or len(quote) > 900 or sources[source][start:start+len(quote)] != quote:
            return None
        return source, quote, start

    observations, records = [], []
    for item in education:
        anchor = quote_anchor(item)
        if not anchor or not isinstance(item.get("fields"), dict):
            continue
        source, quote, start = anchor
        values = {field: raw for field, raw in item["fields"].items()
                  if isinstance(raw, str) and raw in quote and (validate_value(field, raw) or
                     field == "major" and re.fullmatch(r"[\w（）()· -]{2,40}", raw))
                  and not (field == "education_mode" and raw == "全日制" and "非全日制" in quote)}
        for field, value in values.items():
            observations.append(ResumeObservation(field, value, source, quote, start, start+len(quote),
                                                 "ai_verified_text", record_id=f"{source}:{start}",
                                                 record_school=values.get("school")))
    for item in work:
        anchor = quote_anchor(item)
        if not anchor or item.get("hr") not in ("yes", "no", "unclear"):
            continue
        source, quote, start = anchor
        periods = list(PERIOD.finditer(quote))
        begin = end = None
        if len(periods) == 1:
            period = periods[0]
            begin, end = period[1], (period[2] or period[4]).lower()
        related = {"yes": True, "no": False, "unclear": None}[item["hr"]]
        # Missing/ambiguous dates remain visible and make the duration unbounded.
        records.append(WorkRecord(ResumeObservation("work_period", quote, source, quote, start, start+len(quote),
                                                    "ai_verified_text"), begin, end, (), "work_unspecified", related))
    from boss_agent.rule_engine import Finding, _read_model_finding

    decisions = {}
    provided = {key: (key, 0, text) for key, text in prompt_sources.items()}
    responses = result.get("decisions")
    for index, rule in enumerate(requirements):
        entries = [item for item in responses if isinstance(item, dict) and type(item.get("id")) is int and item["id"] == index
                   ] if isinstance(responses, list) else []
        if len(entries) != 1:
            continue
        item = entries[0]
        try:
            status, evidence = _read_model_finding(json.dumps({"status": item.get("status"), "evidence": item.get("evidence")}), provided)
            if any(sources[ref.source][ref.start:ref.end] != ref.quote for ref in evidence):
                continue
        except (ValueError, TypeError, KeyError):
            continue
        detail = item.get("reason")
        reason = detail.strip()[:250] if evidence and isinstance(detail, str) and detail.strip() else (
            "AI 引用简历原文判定" if status != "UNKNOWN" else "简历证据不足，AI 未能判定")
        decisions[requirement_key(rule)] = Finding(rule.field, rule.value, status, reason, evidence, method="llm")
    return list(dict.fromkeys(observations)), list(dict.fromkeys(records)), decisions, "completed"


def with_recognized_resume(resume, candidate_id, request_model, requirements=()):
    from boss_agent.rule_engine import extract_resume_evidence

    observations, work, decisions, status = recognize_resume(resume.sources, request_model, requirements)
    # Reuse the existing source-bound fact conversion and discard heuristic education and HR claims.
    converted = extract_resume_evidence(resume.sources, as_of=resume.as_of, candidate_id=candidate_id,
                                        capture_status=resume.capture_status, education_observations=observations)
    facts = {field: values for field, values in resume.facts.items()
             if field not in EDUCATION_FIELDS | {"school", "hr_experience_months", "graduate_status"}}
    facts.update({field: values for field, values in converted.facts.items() if field in EDUCATION_FIELDS | {"school"}})
    other = [item for item in resume.observations if item.field not in EDUCATION_FIELDS | {"graduate_status"}]
    return replace(resume, facts=facts, observations=tuple(other+observations), work_records=work), decisions, status
