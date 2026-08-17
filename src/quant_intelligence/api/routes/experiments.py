from pathlib import Path
from typing import Any
from fastapi import APIRouter, HTTPException, Request, status

from quant_intelligence.api.schemas import ExperimentCreateRequest, ExperimentCreateResponse
from quant_intelligence.experiments.store import ExperimentNotFound

router = APIRouter(prefix="/api/experiments", tags=["experiments"])

def _store(request: Request):
    return request.app.state.experiment_store

@router.get("")
def list_experiments(request: Request) -> list[dict[str, Any]]:
    return _store(request).list_summaries()

@router.get("/{experiment_id}")
def get_experiment(experiment_id: str, request: Request) -> dict[str, Any]:
    try:
        return _store(request).get_payload(experiment_id)
    except ExperimentNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="experiment not found")

@router.post("", response_model=ExperimentCreateResponse, status_code=status.HTTP_201_CREATED)
def create_experiment(payload: ExperimentCreateRequest, request: Request) -> ExperimentCreateResponse:
    service = request.app.state.experiment_service
    try:
        experiment_id, result = service.create(**payload.model_dump())
    except (ValueError, FileNotFoundError, IsADirectoryError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    except OSError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"unable to read data_path: {exc}")
    result_payload = request.app.state.experiment_store.get_payload(experiment_id)
    return ExperimentCreateResponse(experiment_id=experiment_id, result=result_payload)
