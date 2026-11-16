from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


Warehouse = Literal["los_angeles", "zaragoza"]
PipelineStatus = Literal["running", "succeeded", "failed", "unknown", "skipped"]
PipelineTrigger = Literal["scheduled", "manual", "late_event"]


class WeeklyWarehouseClientPerformanceEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    warehouse: Warehouse
    client_id: str
    inbound_units_count: int
    outbound_orders_count: int
    stockout_events_count: int
    discrepancy_events_count: int
    discrepancy_rate: float


class WeeklyWarehouseClientPerformanceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    week_start: date
    entries: list[WeeklyWarehouseClientPerformanceEntry]


class LatestPipelineRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pipeline_name: str
    run_id: UUID
    prefect_run_id: UUID | None
    week_start: date
    status: PipelineStatus
    trigger: PipelineTrigger
    started_at: datetime
    finished_at: datetime | None
    records_read: int
    records_transformed: int
    records_written: int
    error_message: str | None


class PipelineRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    week_start: date | None = None


class PipelineRunAcceptedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    pipeline_name: str
    week_start: date
    status: Literal["running"]
    trigger: Literal["manual"]
