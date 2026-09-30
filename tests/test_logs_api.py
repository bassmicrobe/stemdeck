from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.joblog import add_job_log
from app.core.models import Job
from app.core.registry import _jobs


@pytest.fixture(autouse=True)
def _isolate_registry():
    _jobs.clear()
    yield
    _jobs.clear()


def test_job_logs_endpoint_returns_structured_entries():
    job = Job(id="abcdefabcdef", title="Logged song")
    _jobs[job.id] = job
    add_job_log(job, "Separating stems", stage="separate", progress=0.42)

    from app.main import app

    with TestClient(app) as client:
        response = client.get(f"/api/logs?job_id={job.id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["entries"][-1]["message"] == "Separating stems"
    assert payload["entries"][-1]["stage"] == "separate"
    assert payload["entries"][-1]["progress_percent"] == 42
    assert payload["jobs"][0]["job_id"] == job.id


def test_job_logs_endpoint_rejects_unknown_job():
    from app.main import app

    with TestClient(app) as client:
        response = client.get("/api/logs?job_id=abcdefabcdef")

    assert response.status_code == 404


def test_job_log_is_bounded_and_deduplicates_identical_entries():
    job = Job(id="abcdefabcdea")
    add_job_log(job, "Queued", stage="queued", progress=0)
    add_job_log(job, "Queued", stage="queued", progress=0)
    for index in range(350):
        add_job_log(job, f"Progress {index}", stage="separate", progress=index / 350)

    assert len(job.logs) == 300
    assert job.logs[-1]["message"] == "Progress 349"
