"""Evidence validation and optional model proposals for individual education records."""

from hashlib import sha256
import json
import re

from boss_agent.major_catalog import describe_major, suggest_majors
from boss_agent.resume_structure import DEGREE, DEGREE_NAME, KNOWN_SCHOOLS, SCHOOL_ACTIVITY, section_lines


FIELDS = ("school", "major", "major_group", "education_level", "degree", "education_mode",
          "education_start_year", "graduation_year")
VERSION = "education-record-v1"


def validate_value(field, raw):
    if field not in FIELDS or not isinstance(raw, str) or not 0 < len(raw.strip()) <= 60:
        return None
    value = raw.strip()
    if field in {"major", "major_group"}:
        major = describe_major(value)
        return major["normalized_major"] or major["major_category"]
    if field == "education_level":
        if re.fullmatch(DEGREE, value, re.I) and value in {"博士", "硕士", "本科", "大专", "中专"}:
            return DEGREE_NAME[value]
        return None
    if field == "school":
        return value if re.fullmatch(r"[\u3400-\u9fff·（）()]{2,38}(?:大学|学院|学校)", value) else None
    if field == "degree":
        return value if re.fullmatch(r"学士|硕士学位|博士学位|bachelor(?:'s)?|master(?:'s)?|doctorate|phd", value, re.I) else None
    if field == "education_mode":
        return value if value in {"全日制", "非全日制"} else None
    return value if re.fullmatch(r"(?:19|20)\d{2}", value) else None


def build_education(index, records, facts, capture_status, model=None):
    by_id = {fact["fact_id"]: fact for fact in facts}
    for fact in facts:
        raw, field = fact["raw_value"], fact["field"]
        fact.update(method="local_rules", source_version=VERSION)
        if field == "education_level" and validate_value(field, raw) is None:
            fact["normalized_value"] = None
        if field == "school":
            fact["normalization_basis"] = "school_dictionary" if raw in KNOWN_SCHOOLS else "school_name_pattern"
            if raw not in KNOWN_SCHOOLS:
                fact["suggestions"] = [name for name in KNOWN_SCHOOLS if len(name) == len(raw) and
                                       sum(a != b for a, b in zip(name, raw)) <= 2]
        else:
            fact["normalization_basis"] = (fact.get("major") or {}).get("mapping_method") or "field_validation"
            if field == "major" and not fact["normalized_value"]:
                fact["suggestions"] = suggest_majors(raw)

    model_status = "disabled"
    if model is not None:
        eligible = {}
        for record in records[:8]:
            existing = [by_id[key] for key in record["fact_ids"]]
            confirmed = {fact["field"] for fact in existing if validate_value(fact["field"], fact["raw_value"]) is not None
                         and (fact["field"] != "school" or fact["raw_value"] in KNOWN_SCHOOLS)}
            wanted = set(FIELDS) - confirmed
            refs = list(dict.fromkeys(ref for fact in existing for ref in fact["evidence_ids"]))
            if wanted.intersection({"school", "major", "education_level"}) and refs:
                eligible[record["record_id"]] = (record, wanted, refs[:6])
        if not records:
            for source, text in index.sources.items():
                for section, line in section_lines(text):
                    if section != "education" or SCHOOL_ACTIVITY.search(line[0].strip()) or len(eligible) >= 8:
                        continue
                    ref = index.add(source, line.start(), line.end(), line[0])
                    if ref:
                        key = f"{source}:{line.start()}"
                        eligible[key] = (dict(record_id=key, school=None, fact_ids=[]), set(FIELDS), [ref])
        if eligible:
            try:
                # Narrow payload: only cited education lines. The model can select an explicit substring.
                response = model([{"record_id": key, "fields": sorted(wanted),
                                   "citations": {ref: index.refs[ref]["quote"][:320] for ref in refs}}
                                  for key, (_, wanted, refs) in eligible.items()])
                proposals = json.loads(response)["fields"]
                if not isinstance(proposals, list) or len(proposals) > 64:
                    raise ValueError("invalid education proposals")
                model_status = "completed"
                for proposal in proposals:
                    if not isinstance(proposal, dict):
                        continue
                    record_id, field, raw, ref = (proposal.get(key) for key in ("record_id", "field", "raw", "evidence_id"))
                    if not all(isinstance(value, str) for value in (record_id, field, raw, ref)) or record_id not in eligible:
                        continue
                    record, wanted, refs = eligible[record_id]
                    normalized = validate_value(field, raw)
                    if field not in wanted or ref not in refs or not normalized:
                        continue
                    quote = index.refs[ref]["quote"][:320]
                    if raw.strip() not in quote or (field == "education_mode" and raw == "全日制" and "非全日制" in quote):
                        continue
                    key = sha256(json.dumps([index.id, record_id, field, raw], ensure_ascii=False).encode()).hexdigest()[:24]
                    if key in by_id or any(by_id[k]["field"] == field and by_id[k]["raw_value"] == raw for k in record["fact_ids"]):
                        continue
                    evidence = index.refs[ref]
                    offset = evidence["start"] + quote.index(raw.strip())
                    precise = index.add(evidence["source_type"].lower(), offset, offset + len(raw.strip()), raw.strip())
                    if not precise:
                        continue
                    fact = dict(fact_id=key, record_id=record_id, field=field, raw_value=raw,
                                normalized_value=normalized, evidence_ids=[precise], method="model",
                                normalization_basis="validated_source_substring", normalization_status="NORMALIZED",
                                extraction_status="EXTRACTED", source_version=VERSION)
                    if field in {"major", "major_group"}:
                        fact["major"] = describe_major(raw)
                    facts.append(fact)
                    by_id[key] = fact
                    if not record["fact_ids"] and record not in records:
                        records.append(record)
                    record["fact_ids"].append(key)
            except (OSError, ValueError, RuntimeError, KeyError, TypeError):
                model_status = "failed"

    for record in records:
        record["fields"] = {}
        for field in FIELDS:
            candidates = [by_id[key] for key in record["fact_ids"] if by_id[key]["field"] == field]
            values = {str(fact["normalized_value"] or fact["raw_value"]) for fact in candidates}
            conflict = len(values) > 1 or any(fact["extraction_status"] == "CONFLICT" for fact in candidates)
            for fact in candidates:
                trusted = (fact["method"] == "local_rules" and validate_value(field, fact["raw_value"]) is not None
                           and (field != "school" or fact["raw_value"] in KNOWN_SCHOOLS
                                or any(index.refs[ref]["source_type"] == "DOM" for ref in fact["evidence_ids"])))
                fact["status"] = "conflict" if conflict else "confirmed" if trusted else "pending"
            preferred = next((fact for fact in candidates if fact["status"] == "confirmed"), None)
            record["fields"][field] = {
                "value": preferred["normalized_value"] if preferred else None,
                "status": "conflict" if conflict else "confirmed" if preferred else "pending" if candidates else "missing",
                "reason": None if candidates else (
                    "capture_incomplete" if capture_status != "complete" else
                    "parse_failed" if model_status == "failed" else "not_found"),
                "fact_ids": [fact["fact_id"] for fact in candidates],
            }
    return {"version": VERSION, "model_status": model_status,
            "status": "found" if records else "capture_incomplete" if capture_status != "complete" else
            "parse_failed" if model_status == "failed" else "not_found"}
