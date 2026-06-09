from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import FileResponse

from apps.api.routes.sidecar import SIDECAR_ROOT

router = APIRouter(tags=["governance"])


@router.get("/governance", include_in_schema=False)
@router.get("/governance/", include_in_schema=False)
def get_governance_workbench() -> FileResponse:
    return FileResponse(SIDECAR_ROOT / "index.html", media_type="text/html")
