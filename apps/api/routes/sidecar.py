from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter(tags=["sidecar"])

SIDECAR_ROOT = Path(__file__).resolve().parents[2] / "web" / "sidecar"


@router.get("/sidecar", include_in_schema=False)
@router.get("/sidecar/", include_in_schema=False)
def get_sidecar() -> FileResponse:
    return FileResponse(SIDECAR_ROOT / "index.html", media_type="text/html")
