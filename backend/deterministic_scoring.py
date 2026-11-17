"""Pure deterministic fit scoring; embedding similarity is supplied by the caller."""

import json
import math
import re
from numbers import Real
from datetime import date

from backend import matching_extraction as extraction


SCORER_VERSION = "deterministic-v18"
WEIGHTS_VERSION = "weights-v3-clause-rubric"
WEIGHTS = {
    "must_have": 30,
    "nice_to_have": 10,
    "semantic": 20,
    "experience": 30,
    "education_title": 10,
}
MISSING_MUST_HAVE_FRACTION = 0.5
MISSING_MUST_HAVE_SCORE_CAP = 60
MISSING_DEGREE_SCORE_CAP = 30
MISSING_SPECIALIST_SCORE_CAP = 30
MISSING_ENROLLMENT_SCORE_CAP = 40
SCOPE_CEILING_FLOOR = 25
SCOPE_SHORTFALL_POINTS_PER_YEAR = 5
NEUTRAL_COMPONENT = 0.7
UNOBSERVED_SKILL_COMPONENT = 0.4
_DEGREE_LEVEL = {"diploma": 1, "bachelors": 2, "masters": 3, "doctorate": 4}
_SENIORITY_LEVEL = {"intern": 0, "entry": 1, "mid": 2, "senior": 3, "lead": 4, "management": 5}
_EDUCATION_REQUIREMENT_CUE = re.compile(
    r"\b(?:degree|education(?:al)?|bachelor(?:'s)?|master(?:'s)?|ph\.?d\.?|doctorate|"
    r"diploma|graduate|undergraduate|postgraduate|major\s+in|field of study)\b", re.I)
_ENROLLMENT_REQUIREMENT = re.compile(
    r"\b(?:pursuing|enrolled in|currently studying|current(?:ly)? (?:undergraduate|graduate|student))\b", re.I)


def _fraction(value):
    return round(min(1.0, max(0.0, value)), 4)


def cache_version(resume_text, as_of_date, embedding_revision):
    """Include the date when work duration or education eligibility can change."""
    if type(as_of_date) is not date or not isinstance(embedding_revision, str) or not embedding_revision.strip():
        raise ValueError("Cache version inputs are invalid")
    education_date_sensitive = any(section["name"] == "education" for section in extraction.split_sections(resume_text))
    date_key = as_of_date.isoformat() if extraction.has_current_experience_date(resume_text) or education_date_sensitive else "static"
    return ":".join((SCORER_VERSION, WEIGHTS_VERSION, "requirement-evidence-v11", extraction.PARSER_VERSION,
                     extraction.TAXONOMY_VERSION,
                     embedding_revision, date_key))


def template_explanation(result):
    """Describe only the evidence and gaps already present in a deterministic result."""
    if not isinstance(result, dict) or type(result.get("score")) is not int:
        raise ValueError("A valid deterministic result is required")
    matched = result.get("matched", [])
    gaps = result.get("missing_must_have_requirements", [])
    missing_skills = result.get("missing_must_have", [])
    parts = [f"Deterministic fit score: {result['score']}/100.",
             f"{len(matched)} evidence-backed match(es)."]
    coverage = result.get("requirement_coverage")
    if isinstance(coverage, dict):
        parts.append(f"Recognized {coverage['recognized']} of {coverage['total']} requirement clauses; "
                     "unrecognized clauses need manual review.")
    evidence_summary = result.get("requirement_summary", {})
    if evidence_summary.get("no_evidence", 0):
        parts.append(f"No resume evidence was detected for {evidence_summary['no_evidence']} mapped requirement(s).")
    if evidence_summary.get("partial", 0):
        parts.append(f"Partial resume evidence was detected for {evidence_summary['partial']} mapped requirement(s).")
    if evidence_summary.get("manual_review", 0):
        parts.append(f"{evidence_summary['manual_review']} mapped requirement(s) need manual review.")
    if gaps or missing_skills:
        parts.append("Review the required clauses with unmet criteria below.")
    elif not evidence_summary:
        parts.append("This saved result predates clause-level evidence; an empty missing-skills list does not mean every clause is fully supported.")
    elif (evidence_summary.get("supported", 0) and not evidence_summary.get("no_evidence") and
          not evidence_summary.get("partial") and not evidence_summary.get("manual_review")):
        parts.append("Resume evidence was detected for all mapped requirements; review the clauses and evidence below.")
    return " ".join(parts)


def _coverage(skills, resume_mentions, *, inferred=()):
    if not skills:
        return UNOBSERVED_SKILL_COMPONENT
    inferred = set(inferred)
    credits = []
    for skill in skills:
        if skill not in resume_mentions:
            credits.append(0.0)
            continue
        mention = resume_mentions[skill]
        # Presence satisfies a skill criterion; inferred/fuzzy evidence and
        # unsupported keyword lists retain their conservative credit limits.
        credit = mention["evidence_credit"]
        if mention["match_type"] not in {"implies", "fuzzy"} and credit >= 0.75:
            credit = 1.0
        credits.append(min(credit, 0.4) if skill in inferred else credit)
    return _fraction(sum(credits) / len(skills))


def _clause_coverage(requirements, resume_mentions, priorities):
    """Average unique mapped clauses; repeated keywords cannot change their weight."""
    clauses = {}
    for item in requirements:
        if item["priority"] not in priorities or not item["skills"]:
            continue
        skills = tuple(sorted(item["skills"]))
        inferred = skills if item["priority"] == "inferred_must_have" else ()
        key = (item["priority"], skills)
        clauses[key] = _coverage(skills, resume_mentions, inferred=inferred)
    return _fraction(sum(clauses.values()) / len(clauses)) if clauses else UNOBSERVED_SKILL_COMPONENT


def _education_fit(resume_text, jd_text):
    """The legacy education_title response field now measures education only."""
    jd_education = education_requirement_evidence(jd_text)
    resume_education = extraction.extract_education_evidence(resume_text)
    if jd_education:
        education_fit = 1.0 if any(_education_evidence_matches(required, actual)
                                   for required in jd_education for actual in resume_education) else 0.0
    else:
        education_fit = NEUTRAL_COMPONENT

    return _fraction(education_fit)


def education_requirement_evidence(text):
    """Keep education evidence only when the JD explicitly asks about education."""
    return [item for item in extraction.extract_education_evidence(text)
            if item["levels"] or _EDUCATION_REQUIREMENT_CUE.search(item["text"])]


def _education_evidence_matches(required, actual):
    if required["levels"]:
        required_level = min(_DEGREE_LEVEL[level] for level in required["levels"])
        actual_level = max((_DEGREE_LEVEL[level] for level in actual["levels"]), default=0)
        if actual_level < required_level:
            return False
    if required["fields"] and not set(required["fields"]) & set(actual["fields"]):
        return False
    return bool(required["levels"] or required["fields"])


def _resume_evidence(resume_text):
    mentions = {item["skill"]: item for item in extraction.expand_implied_skills(
                extraction.extract_skill_mentions(resume_text)) if item["evidence_credit"] > 0}
    return mentions


def pending_fuzzy_matches(resume_text, jd_text, pairs):
    """Omit already-supported proposals; leave invalid proposals for strict validation."""
    existing = _resume_evidence(resume_text)
    return [pair for pair in pairs if not (
        isinstance(pair, dict) and set(pair) == {"jd_skill", "resume_skill", "cosine", "type"}
        and isinstance(pair.get("jd_skill"), str) and pair["jd_skill"] in existing
        and isinstance(pair.get("resume_skill"), str) and pair.get("type") == "fuzzy"
        and type(pair.get("cosine")) in (int, float) and math.isfinite(pair["cosine"])
        and extraction.FUZZY_SKILL_THRESHOLD <= pair["cosine"] <= 1)]


def score(resume_text, jd_text, semantic_similarity, *, job_title="", as_of_date, fuzzy_skill_pairs=(),
          requirement_assessments=None):
    """Calculate a versioned score from bounded text and a local cosine value."""
    for label, value in (("resume", resume_text), ("job description", jd_text)):
        if not isinstance(value, str) or not value.strip() or len(value) > 250000 or "\x00" in value:
            raise ValueError(f"{label.capitalize()} must be nonempty text within the matching limit")
    if not isinstance(job_title, str) or len(job_title) > 1000:
        raise ValueError("Job title must be bounded text")
    if type(as_of_date) is not date:
        raise TypeError("Scoring date must be a date")
    if (isinstance(semantic_similarity, bool) or not isinstance(semantic_similarity, Real) or not math.isfinite(semantic_similarity)
            or not -1 <= semantic_similarity <= 1):
        raise ValueError("Semantic similarity must be finite and between -1 and 1")

    jd_mentions = extraction.extract_skill_mentions(jd_text, all_mentions=True)
    requirements = extraction.extract_jd_requirements(jd_text, job_title=job_title)
    groq_assessed_clauses = set()
    assisted_needs_review = {item['text'] for item in requirements if item['unknown_skills']}
    for item in requirements:
        validated = extraction.validate_skill_candidates(item['text'], item['skills'])
        item['skills'] = [mention['skill'] for mention in validated['accepted']]
        # Candidate discovery stays available through extraction and the review queue.
        # Unreviewed labels never enter denominators, gaps, or resume matching.
        item['unknown_skills'] = []
    if requirement_assessments is not None:
        if not isinstance(requirement_assessments, dict):
            raise ValueError("Requirement assessments must be a mapping")
        by_text = {item["text"]: item for item in requirements}
        for clause, terms in requirement_assessments.items():
            if (not isinstance(clause, str) or clause not in by_text or not isinstance(terms, list) or
                    len(terms) > 5 or any(not isinstance(term, str) or not term.strip() or len(term) > 80
                                           for term in terms)):
                raise ValueError("Invalid requirement assessment")
            item = by_text[clause]
            if (item["skills"] or item["minimum_years"] is not None or
                    education_requirement_evidence(item["text"]) or not terms):
                raise ValueError("Requirement assessment does not target an unassessed clause")
            validated = extraction.validate_skill_candidates(clause, terms)
            known_terms = {mention['skill'] for mention in validated['accepted']}
            if validated['review'] or validated['rejected']:
                assisted_needs_review.add(clause)
            item["skills"] = sorted(known_terms)
            groq_assessed_clauses.add(clause)
    resume_mentions = _resume_evidence(resume_text)
    # Resolve explicit adjacent alternatives to one criterion; conjunctions remain separate.
    for item in requirements:
        mentions = extraction.extract_skill_mentions(item["text"], all_mentions=True, section_context="requirements")
        spans = []
        for mention in mentions:
            match = re.search(re.escape(mention["matched_text"]), item["text"], re.I)
            if match:
                spans.append((match.start(), match.end(), mention["skill"]))
        spans.sort()
        groups = []
        for start, end, skill in spans:
            if groups and re.fullmatch(r"\s*(?:,|/|,?\s*or)\s*", item["text"][groups[-1][-1][1]:start], re.I):
                groups[-1].append((start, end, skill))
            else:
                groups.append([(start, end, skill)])
        for group in groups:
            if len(group) < 2:
                continue
            # Explicit alternatives and illustrative examples form one criterion.
            prefix = item["text"][:group[0][0]]
            example_list = bool(re.search(r"\(\s*e\.g\.,?[^()]*$", prefix, re.I))
            trailing_alternative = bool(re.match(r",?\s*or\s+(?:similar|equivalent|another|other)\b",
                                                  item["text"][group[-1][1]:], re.I))
            if not (example_list or trailing_alternative or any(
                    re.search(r"\bor\b", item["text"][left[1]:right[0]], re.I)
                    for left, right in zip(group, group[1:]))):
                continue
            options = [entry[2] for entry in group]
            selected = max(options, key=lambda skill: resume_mentions.get(skill, {}).get("evidence_credit", 0))
            item["skills"] = [skill for skill in item["skills"] if skill not in options or skill == selected]
    inferred_title_skills = {skill for item in requirements
                             if item["priority"] == "inferred_must_have" and item["line"] == 0
                             for skill in item["skills"]}
    if inferred_title_skills:
        jd_mentions.extend(dict(item, line=0, section="job_title")
                           for item in extraction.extract_skill_mentions(job_title, all_mentions=True)
                           if item["skill"] in inferred_title_skills)
    if not isinstance(fuzzy_skill_pairs, (list, tuple)):
        raise ValueError("Fuzzy skill matches must be a list")
    jd_skill_set = {item["skill"] for item in jd_mentions}
    explicit_resume_skills = {item["skill"] for item in extraction.extract_skill_mentions(resume_text)}
    for pair in fuzzy_skill_pairs:
        if (not isinstance(pair, dict) or set(pair) != {"jd_skill", "resume_skill", "cosine", "type"} or
                pair.get("type") != "fuzzy" or not isinstance(pair.get("jd_skill"), str) or
                pair.get("jd_skill") not in jd_skill_set or not isinstance(pair.get("resume_skill"), str) or
                pair.get("resume_skill") not in explicit_resume_skills or
                pair.get("resume_skill") not in resume_mentions or
                pair.get("jd_skill") in resume_mentions or
                pair.get("resume_skill") == pair.get("jd_skill") or
                (pair.get("jd_skill") in extraction.SKILLS and pair.get("resume_skill") in extraction.SKILLS and
                 frozenset((pair.get("jd_skill"), pair.get("resume_skill"))) in extraction.BLOCKED_SKILL_PAIRS) or
                type(pair.get("cosine")) not in (int, float) or not math.isfinite(pair["cosine"]) or
                pair["cosine"] < extraction.FUZZY_SKILL_THRESHOLD or pair["cosine"] > 1):
            raise ValueError("Invalid or below-threshold fuzzy skill match")
        source = resume_mentions[pair["resume_skill"]]
        evidence = dict(source, matched_text=source["matched_text"])
        resume_mentions[pair["jd_skill"]] = dict(evidence, skill=pair["jd_skill"], match_type="fuzzy",
                                                   fuzzy_from=pair["resume_skill"],
                                                   evidence_credit=min(evidence["evidence_credit"], 0.4))
    for item in requirements:
        if item["text"] not in groq_assessed_clauses:
            continue
        if any(skill not in resume_mentions for skill in item["skills"]):
            assisted_needs_review.add(item["text"])
            # An unsupported generated mapping stays outside skill denominators.
            item["skills"] = []
    must_skills = sorted({skill for item in requirements if item["priority"] == "must_have"
                          for skill in item["skills"]})
    inferred_skills = sorted({skill for item in requirements if item["priority"] == "inferred_must_have"
                              for skill in item["skills"]})
    nice_skills = sorted({skill for item in requirements if item["priority"] == "nice_to_have"
                          for skill in item["skills"]})
    missing = [skill for skill in must_skills if skill not in resume_mentions]
    missing_inferred = [skill for skill in inferred_skills if skill not in resume_mentions]
    jd_education = education_requirement_evidence(jd_text)
    resume_education = extraction.extract_education_evidence(resume_text)
    education_by_clause = {item["text"]: education_requirement_evidence(item["text"])
                           for item in requirements}
    education_requirements = [item for item in requirements if item["priority"] == "must_have"
                              and education_by_clause[item["text"]]]
    missing_education = [item for item in education_requirements
                         if not any(_education_evidence_matches(required, candidate)
                                    for required in education_by_clause[item["text"]]
                                    for candidate in resume_education)]
    enrollment_requirements = [item for item in education_requirements
                               if _ENROLLMENT_REQUIREMENT.search(item["text"])]
    enrollment_evidence = extraction.education_enrollment_evidence(resume_text, as_of_date)
    enrollment_by_clause = {item["text"]: [candidate for candidate in enrollment_evidence
                            if any(_education_evidence_matches(required, candidate)
                                   for required in education_by_clause[item["text"]])]
                            for item in enrollment_requirements}
    missing_enrollment = [item for item in enrollment_requirements if not enrollment_by_clause[item["text"]]]
    matched = []
    for jd_mention in jd_mentions:
        skill = jd_mention["skill"]
        if skill not in resume_mentions:
            continue
        resume_mention = resume_mentions[skill]
        if resume_mention["match_type"] in ("implies", "fuzzy"):
            match_type = resume_mention["match_type"]
        else:
            match_type = "exact" if jd_mention["matched_text"].casefold() == resume_mention["matched_text"].casefold() else "alias"
        matched.append({
            "jd": jd_mention["matched_text"],
            "resume": resume_mention["matched_text"],
            "type": match_type,
            "skill": skill,
            "jd_line": jd_mention["line"],
            "resume_line": resume_mention["line"],
        })
    for jd_item in jd_education:
        candidate = next((item for item in resume_education
                          if _education_evidence_matches(jd_item, item)), None)
        if candidate is not None:
            matched.append({"jd": jd_item["text"], "resume": candidate["text"], "type": "education",
                            "jd_line": jd_item["line"], "resume_line": candidate["line"]})

    year_requirements = [item for item in requirements
                         if item["priority"] == "must_have" and item["minimum_years"] is not None]
    required_years = [item["minimum_years"] for item in year_requirements]
    has_duration = any(item["minimum_years"] is not None for item in requirements)
    candidate_years = extraction.experience_years(resume_text, as_of_date) if has_duration else 0.0
    per_skill_years = extraction.skill_experience_years(resume_text, as_of_date) if has_duration else {}

    def observed_years(item):
        if item["skills"]:
            return min((per_skill_years.get(skill, 0.0) for skill in item["skills"]), default=0.0)
        return candidate_years

    missing_year_requirements = [item for item in year_requirements
                                 if observed_years(item) < item["minimum_years"]]
    if required_years:
        ratios = [1.0 if item["minimum_years"] == 0 else
                  min(1.0, observed_years(item) / item["minimum_years"])
                  for item in year_requirements]
        # Every mandatory duration must be met; a general work history cannot
        # compensate for an unmet specialist duration in another clause.
        experience_fit = _fraction(min(ratios))
    else:
        required_capabilities = set(must_skills or inferred_skills)
        generic = {"Artificial intelligence", "Programming", "Developer tooling",
                   "Workflow automation", "Software development lifecycle"}
        core = required_capabilities - generic or required_capabilities
        demonstrated = [resume_mentions[skill]["evidence_credit"] for skill in core
                        if skill in resume_mentions and
                        resume_mentions[skill]["section"] in {"experience", "projects", "other"} and
                        resume_mentions[skill]["evidence_credit"] >= 0.75]
        # Demonstrated delivery satisfies unspecified duration; extra weaker
        # evidence must not dilute an existing delivery example. Generic AI/tool
        # vocabulary cannot substitute for a role's more specific capabilities.
        if demonstrated:
            experience_fit = 1.0
        elif any("Programming" in extraction.IMPLIES.get(skill, ()) for skill in core) and (
                resume_mentions.get("Programming", {}).get("evidence_credit", 0) >= 0.75):
            experience_fit = 0.4
        else:
            experience_fit = NEUTRAL_COMPONENT if not required_capabilities else 0.0

    must_fit = _clause_coverage(requirements, resume_mentions, {"must_have", "inferred_must_have"})
    optional_fit = _clause_coverage(requirements, resume_mentions, {"nice_to_have"})
    responsibility_lines = {section["start_line"] + offset
                            for section in extraction.split_sections(jd_text)
                            if section["name"] == "responsibilities"
                            for offset, _ in enumerate(section["text"].splitlines())}
    duties = [item for item in requirements if item["line"] in responsibility_lines and item["skills"]]
    if duties:
        duty_fit = _clause_coverage(duties, resume_mentions, {"unclassified", "must_have", "inferred_must_have"})
    else:
        duty_fit = must_fit
    # Mapped duties are scored from source evidence. Embedding similarity can
    # rank topical relevance but must neither create nor erase that evidence.
    role_fit = duty_fit
    jd_levels = extraction.extract_seniority(job_title)
    resume_levels = extraction.extract_seniority(resume_text)
    seniority_gap = bool(jd_levels and resume_levels and
                         max(_SENIORITY_LEVEL[x] for x in jd_levels) > max(_SENIORITY_LEVEL[x] for x in resume_levels))

    components = {
        "must_have": must_fit,
        "nice_to_have": optional_fit,
        "semantic": role_fit,
        "experience": experience_fit,
        "education_title": 0.0 if missing_enrollment else _education_fit(resume_text, jd_text),
    }
    weights = dict(WEIGHTS)
    if not nice_skills:
        # Required/preferred criteria share the fixed 40-point skills budget.
        weights["must_have"] += weights["nice_to_have"]
        weights["nice_to_have"] = 0
    component_points = {name: round(components[name] * maximum, 2) for name, maximum in weights.items()}
    raw_score = sum(component_points.values())
    must_have_count = len(must_skills) + len(required_years) + len(education_requirements)
    missing_count = len(missing) + len(missing_year_requirements) + len(missing_education)
    missing_fraction = missing_count / must_have_count if must_have_count else 0.0
    if seniority_gap:
        must_have_count += 1
        missing_count += 1
        missing_fraction = missing_count / must_have_count
    missing_specialist = [item for item in requirements if item["priority"] == "must_have"
                          and re.search(r"\b(?:deep|expert|expertise|extensive)\b", item["text"], re.I)
                          and any(skill in missing for skill in item["skills"])]
    capped = bool(missing_education or missing_enrollment or missing_specialist or (must_have_count and missing_fraction > MISSING_MUST_HAVE_FRACTION)
                  or missing_year_requirements or seniority_gap)
    # One scope ceiling replaces additive tenure deductions. Large mandatory
    # gaps cannot be canceled by unrelated keyword or preferred-skill points.
    tenure_shortfall = max((item["minimum_years"] - observed_years(item)
                            for item in year_requirements), default=0.0)
    scope_ceiling = max(SCOPE_CEILING_FLOOR,
                        MISSING_MUST_HAVE_SCORE_CAP - max(0.0, tenure_shortfall) * SCOPE_SHORTFALL_POINTS_PER_YEAR)
    final_score = round(raw_score)
    if capped:
        final_score = min(final_score, round(scope_ceiling))
    if missing_education:
        final_score = min(final_score, MISSING_DEGREE_SCORE_CAP)
    if missing_specialist:
        final_score = min(final_score, MISSING_SPECIALIST_SCORE_CAP)
    if missing_enrollment:
        final_score = min(final_score, MISSING_ENROLLMENT_SCORE_CAP)
    missing_requirements = [
        {"jd": item["text"], "jd_line": item["line"],
         "reason": "experience_years" if item["minimum_years"] is not None else "missing_skill"}
        for item in requirements
        if item["priority"] == "must_have" and (
            item in missing_year_requirements or
            item in missing_education or
            any(skill in missing for skill in item["skills"]))
    ]
    if seniority_gap:
        missing_requirements.append({"jd": job_title, "jd_line": 0, "reason": "seniority"})
    missing_requirements.extend({"jd": item["text"], "jd_line": item["line"], "reason": "current_enrollment"}
                                for item in missing_enrollment)
    assessed = [item for item in requirements if item["priority"] in {"must_have", "nice_to_have", "inferred_must_have"}]
    recognized = [item for item in assessed if item["text"] not in assisted_needs_review and (
                  item["skills"] or item["minimum_years"] is not None
                  or item["text"] in groq_assessed_clauses
                  or education_by_clause.get(item["text"]))]
    recognized_texts = {item["text"] for item in recognized}
    requirement_evidence = []
    for item in assessed:
        clause = item["text"]
        criteria = []
        for skill in item["skills"]:
            mention = resume_mentions.get(skill)
            credit = mention["evidence_credit"] if mention else 0.0
            if item["priority"] == "inferred_must_have":
                credit = min(credit, 0.4)
            criteria.append({
                "criterion": skill,
                "status": "supported" if credit >= 0.75 else "partial" if credit > 0 else "no_evidence",
                "resume_evidence": mention["matched_text"] if mention else "",
                "evidence_type": mention["match_type"] if mention else "",
            })
        if item["minimum_years"] is not None:
            observed = observed_years(item)
            criteria.append({
                "criterion": f"{item['minimum_years']:g}+ years",
                "status": "supported" if observed >= item["minimum_years"] else "partial" if observed > 0 else "no_evidence",
                "resume_evidence": f"{observed:g} years detected",
                "evidence_type": "experience_duration",
            })
        for education in education_by_clause[clause]:
            candidate = next((candidate for candidate in resume_education
                              if _education_evidence_matches(education, candidate)), None)
            criteria.append({
                "criterion": education["text"],
                "status": "supported" if candidate else "no_evidence",
                "resume_evidence": candidate["text"] if candidate else "",
                "evidence_type": "education" if candidate else "",
            })
        if item in enrollment_requirements:
            current = enrollment_by_clause[clause]
            criteria.append({"criterion": "Current degree enrollment", "status": "supported" if current else "no_evidence",
                             "resume_evidence": current[0]["text"] if current else "",
                             "evidence_type": "education_enrollment"})
        if clause in assisted_needs_review or not criteria:
            status = "manual_review"
        elif all(entry["status"] == "supported" for entry in criteria):
            status = "supported"
        elif any(entry["status"] in {"supported", "partial"} for entry in criteria):
            status = "partial"
        else:
            status = "no_evidence"
        requirement_evidence.append({
            "jd": clause,
            "jd_line": item["line"],
            "priority": item["priority"],
            "status": status,
            "criteria": criteria,
        })
    requirement_summary = {status: sum(entry["status"] == status for entry in requirement_evidence)
                           for status in ("supported", "partial", "no_evidence", "manual_review")}
    return {
        "score": final_score,
        "raw_score": round(raw_score, 2),
        "components": components,
        "component_points": component_points,
        "weights": weights,
        "requirement_coverage": {"total": len(assessed), "recognized": len(recognized),
                                 "ratio": round(len(recognized) / len(assessed), 4) if assessed else 0.0,
                                 "needs_review": [item["text"][:200] for item in assessed if item["text"] not in recognized_texts][:100]},
        "requirement_evidence": requirement_evidence,
        "requirement_summary": requirement_summary,
        "matched": matched,
        "missing_must_have": missing,
        "missing_inferred_skills": missing_inferred,
        "missing_must_have_requirements": missing_requirements,
        "must_have_skills": must_skills,
        "must_have_unknown_skills": [],
        "inferred_must_have_skills": inferred_skills,
        "nice_to_have_skills": nice_skills,
        "nice_to_have_unknown_skills": [],
        "experience_evidence": [
            {"jd": item["text"], "jd_line": item["line"], "required_years": item["minimum_years"],
             "observed_years": observed_years(item), "skills": item["skills"]}
            for item in year_requirements
        ],
        "missing_must_have_fraction": round(missing_fraction, 4),
        "must_have_criterion_count": must_have_count,
        "missing_must_have_criterion_count": missing_count,
        "hard_cap_applied": capped,
        "experience_hard_cap_applied": bool(missing_year_requirements or seniority_gap),
        "scorer_version": SCORER_VERSION,
        "weights_version": WEIGHTS_VERSION,
        "scored_as_of": as_of_date.isoformat(),
    }


def validate_adapted_result(result):
    """Validate a deterministic worker score before it enters the shared score table."""
    from backend.store import _deterministic_breakdown

    if isinstance(result, dict) and isinstance(result.get("missing_requirements"), list):
        result = dict(result, missing_requirements=[item[:200] if isinstance(item, str) else item
                                                  for item in result["missing_requirements"]])

    if (not isinstance(result, dict) or type(result.get("fit_score")) is not int or
            not 0 <= result["fit_score"] <= 100 or
            not isinstance(result.get("reasoning"), str) or not result["reasoning"].strip() or
            len(result["reasoning"]) > 2000 or
            any(not isinstance(result.get(key), list) or len(result[key]) > 100 or
                any(not isinstance(item, str) or not item.strip() or len(item) > 200
                    for item in result[key]) for key in ("matched_skills", "missing_requirements")) or
            not isinstance(result.get("deterministic_breakdown"), dict)):
        raise ValueError("Invalid deterministic worker score")
    breakdown = _deterministic_breakdown(json.dumps(result["deterministic_breakdown"], allow_nan=False))
    if (breakdown["score"] != result["fit_score"] or
            breakdown.get("scorer_version") != SCORER_VERSION or
            breakdown.get("weights_version") != WEIGHTS_VERSION or
            not isinstance(result.get("scorer_version"), str) or
            result["scorer_version"] != SCORER_VERSION or
            result.get("weights_version") != WEIGHTS_VERSION):
        raise ValueError("Deterministic worker score versions do not match")
    expected_matches = sorted({item["skill"] for item in breakdown["matched"] if item.get("skill")})
    expected_missing = list(breakdown["missing_must_have"])
    for item in breakdown["missing_must_have_requirements"]:
        if item["jd"] not in expected_missing:
            expected_missing.append(item["jd"])
    if result["matched_skills"] != expected_matches[:100] or result["missing_requirements"] != [item[:200] for item in expected_missing[:100]]:
        raise ValueError("Deterministic worker summary does not match its score breakdown")
    return result
