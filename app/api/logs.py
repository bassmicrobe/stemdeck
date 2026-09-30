from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.core.config import JOB_ID_RE
from app.core.joblog import job_logs, system_logs
from app.core.registry import all_jobs as registry_all_jobs
from app.core.registry import get as registry_get

router = APIRouter(tags=["logs"])


@router.get("/logs")
def get_logs(
    job_id: str | None = Query(default=None),
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=500),
) -> dict:
    if job_id is not None:
        if not JOB_ID_RE.match(job_id):
            raise HTTPException(status_code=404, detail="job not found")
        job = registry_get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        entries = job_logs(job, after=after, limit=limit)
    else:
        entries = system_logs(after=after, limit=limit)
    jobs = [
        {
            "job_id": job.id,
            "title": job.title or job.source_url or job.id,
            "status": job.status,
            "created_at": job.created_at,
        }
        for job in sorted(
            registry_all_jobs().values(),
            key=lambda item: item.created_at,
            reverse=True,
        )
        if job.logs
    ][:100]
    return {"entries": entries, "jobs": jobs}
