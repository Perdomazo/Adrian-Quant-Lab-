from __future__ import annotations

import json
from pathlib import Path

from research_lab.run_manager import update_run


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_update_run_ignores_status_artifacts_from_other_run(tmp_path):
    root = tmp_path
    storage = tmp_path / "storage"
    results = storage / "results"
    old_run = "old-run"
    new_run = "new-run"

    write_json(results / "data_quality_report.json", {"run_id": old_run, "status": "passed"})
    write_json(results / "account_validation.json", {"run_id": old_run, "status": "passed"})
    write_json(results / "account_oos_validation.json", {"run_id": old_run, "status": "passed"})
    write_json(
        results / "account_candidate_decision.json",
        {"run_id": old_run, "status": "success"},
    )

    update_run(root, storage, new_run, "failed", "pipeline_failed_exit_1")

    manifest = json.loads(
        (results / "runs" / new_run / "run_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["status"] == "failed"
    assert manifest["error"] == "pipeline_failed_exit_1"
    assert "data_quality_status" not in manifest
    assert "account_validation_status" not in manifest
    assert "account_oos_validation_status" not in manifest
    assert "account_candidate_status" not in manifest


def test_update_run_accepts_status_artifacts_from_same_run(tmp_path):
    root = tmp_path
    storage = tmp_path / "storage"
    results = storage / "results"
    run_id = "run-a"

    write_json(results / "data_quality_report.json", {"run_id": run_id, "status": "passed"})
    write_json(results / "account_validation.json", {"run_id": run_id, "status": "passed"})
    write_json(results / "account_oos_validation.json", {"run_id": run_id, "status": "passed"})
    write_json(results / "account_candidate_decision.json", {"run_id": run_id, "status": "success"})

    update_run(root, storage, run_id, "success")

    manifest = json.loads(
        (results / "runs" / run_id / "run_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["status"] == "success"
    assert manifest["data_quality_status"] == "passed"
    assert manifest["account_validation_status"] == "passed"
    assert manifest["account_oos_validation_status"] == "passed"
    assert manifest["account_candidate_status"] == "success"
