from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


Warehouse = Literal["LA", "ZGZ"]
Category = Literal["fashion", "electronics", "cosmetics"]
ExitType = Literal["dispatch", "loss"]


class SKUCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1)
    sku: str = Field(min_length=1)
    client_name: str = Field(min_length=1)
    client_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    category: Category
    warehouse: Warehouse
    minimum_stock: int | None = Field(default=None, gt=0)


class SKUResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    sku: str
    client_name: str
    client_id: str
    category: Category
    warehouse: Warehouse
    current_stock: int
    minimum_stock: int | None


InventoryTelemetryEventType = Literal["stock_threshold_triggered", "inventory_discrepancy_detected"]


class InventoryTelemetrySignal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: InventoryTelemetryEventType
    properties: dict[str, str | int]


class StockEntryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    sku_id: int = Field(gt=0)
    quantity: int = Field(gt=0)
    reference: str = Field(min_length=1)
    warehouse: Warehouse


class StockMovementCreatedResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    telemetry_events: list[InventoryTelemetrySignal] = Field(default_factory=list)


class StockThresholdUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    minimum_stock: int = Field(gt=0)


class DirectStockEditAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quantity: int = Field(ge=0)


class InventoryAuditCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku_id: int = Field(gt=0)
    physical_stock: int = Field(ge=0)


class InventoryAuditResponse(BaseModel):
    id: int
    sku_id: int
    warehouse: Warehouse
    system_stock: int
    physical_stock: int
    discrepancy_quantity: int
    discrepancy_direction: Literal["physical_lower", "physical_higher"] | None
    created_at: datetime
    telemetry_event: InventoryTelemetrySignal | None = None


class StockExitCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    sku_id: int = Field(gt=0)
    quantity: int = Field(gt=0)
    exit_type: ExitType
    tracking_number: str | None = None
    warehouse: Warehouse

    @model_validator(mode="after")
    def validate_tracking_number(self) -> "StockExitCreate":
        if self.exit_type == "dispatch" and not self.tracking_number:
            raise ValueError("tracking_number es obligatorio para exit_type=dispatch.")
        if self.exit_type == "loss" and self.tracking_number is not None:
            raise ValueError("tracking_number debe ser nulo para exit_type=loss.")
        return self


class StockOrderListItemResponse(BaseModel):
    id: int
    order_type: Literal["inbound", "outbound"]
    sku_id: int
    product_name: str
    quantity: int
    warehouse: Warehouse
    created_at: datetime
    user_uuid: str
    reference: str | None = None
    exit_type: ExitType | None = None
    tracking_number: str | None = None