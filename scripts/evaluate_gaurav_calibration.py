"""Offline calibration against frozen reviewer labels; never writes application data."""

import argparse
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from backend import pipeline_skeleton
from backend.local_embeddings import LocalScorer


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/gaurav-calibration-20261116"
SOURCES = ("backend/deterministic_scoring.py", "backend/matching_extraction.py",
           "backend/semantic_similarity.py", "backend/pipeline_skeleton.py",
           "backend/local_embeddings.py", "backend/groq_requirement_assessor.py",
           "backend/taxonomy/skills.json")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--split", choices=("development", "holdout"), default="development")
    parser.add_argument("--cohort", choices=("original", "round2", "round3", "round4-distinct", "round5-distinct", "round6-distinct"), default="original")
    args = parser.parse_args()
    args.output = args.output.resolve()
    if args.output.parent != DATA.resolve() or args.output.suffix != ".json":
        raise ValueError("Evaluation output must be a JSON file inside the private calibration directory")
    if args.output.exists():
        raise FileExistsError("Evaluation output already exists; choose a new run path")
    jobs_name = "jobs.json" if args.cohort == "original" else "jobs-" + args.cohort + ".json"
    reference_name = "reference.json" if args.cohort == "original" else "reference-" + args.cohort + ".json"
    jobs = json.loads((DATA / jobs_name).read_text(encoding="utf-8"))
    reference = json.loads((DATA / reference_name).read_text(encoding="utf-8"))
    labels = {row[0]: row[1:] for row in reference["labels"]}
    resume = (DATA / "resume.txt").read_text(encoding="utf-8")
    selected = jobs if args.cohort != "original" else (jobs[:20] if args.split == "development" else jobs[20:])
    hashes = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES}
    report = {"split": args.split if args.cohort == "original" else "validation", "cohort": args.cohort, "source_hashes": hashes,
              "input_hashes": {name: hashlib.sha256((DATA / name).read_bytes()).hexdigest()
                               for name in (jobs_name, reference_name, "resume.txt")},
              "external_requirement_assistance": "omitted: no live services authorized",
              "rows": []}
    cache_path = args.output.with_suffix(".sqlite3")
    if cache_path.exists():
        raise FileExistsError("Evaluation cache already exists; use a fresh run path")
    scorer = LocalScorer(cache_path=cache_path)
    try:
        # Use stored full JDs. Any unexpected network attempt fails the run.
        with patch("requests.sessions.Session.request", side_effect=RuntimeError("Network forbidden in offline evaluation")), \
                patch("backend.career_sources.prepare_job", side_effect=lambda job: dict(job)), \
                patch("backend.groq_requirement_assessor.assess", return_value={}):
            for job in selected:
                row = dict(job, raw_json=json.dumps({"descriptionPlain": job["description"]}))
                result = pipeline_skeleton.score_job_deterministic(
                    {"id": 1, "resume_text": resume}, row, job["company"], scorer)
                expected, rationale = labels[job["id"]]
                error = abs(result["fit_score"] - expected)
                report["rows"].append(dict(job_id=job["id"], title=job["title"], company=job["company"],
                                           reference=expected, predicted=result["fit_score"], error=error,
                                           rationale=rationale, breakdown=result["deterministic_breakdown"]))
                print(json.dumps({key: report["rows"][-1][key]
                                  for key in ("job_id", "reference", "predicted", "error")}), flush=True)
    finally:
        scorer.close()
    after = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES}
    if hashes != after:
        raise RuntimeError("Scorer source changed during evaluation")
    streak = maximum = 0
    for row in report["rows"]:
        streak = streak + 1 if row["error"] <= 5 else 0
        maximum = max(maximum, streak)
    report.update(mean_absolute_error=sum(row["error"] for row in report["rows"]) / len(selected),
                  within_five=sum(row["error"] <= 5 for row in report["rows"]),
                  longest_streak=maximum, final_streak=streak)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, ensure_ascii=True)
    print(json.dumps({key: report[key] for key in
                      ("split", "mean_absolute_error", "within_five", "longest_streak", "final_streak")}))


if __name__ == "__main__":
    main()
