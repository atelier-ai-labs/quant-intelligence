from datetime import date
from typing import Any
from pydantic import BaseModel, Field, field_validator

class HealthResponse(BaseModel):
    status: str
    service: str

class ExperimentCreateRequest(BaseModel):
    symbol: str = Field(min_length=1)
    strategy: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    start: date | None = None
    end: date | None = None
    initial_capital: float = Field(gt=0)
    transaction_cost_bps: float = Field(ge=0)
    benchmark: str = "buy_and_hold"
    data_path: str = Field(min_length=1)

    @field_validator("end")
    @classmethod
    def end_not_before_start(cls, value: date | None, info):
        start = info.data.get("start")
        if value is not None and start is not None and value < start: raise ValueError("end must not be before start")
        return value

class ExperimentCreateResponse(BaseModel):
    experiment_id: str
    result: dict[str, Any]
