from datetime import datetime
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator


class SupplierStatus(str, Enum):
    active = "active"
    suspended = "suspended"


Category = Literal[
    "carrier_last_mile",
    "carrier_international",
    "warehouse_supplies",
    "packaging_materials",
    "reverse_logistics",
    "fleet_maintenance",
    "it_and_wms_software",
    "cleaning_and_facilities",
]
Country = Literal["USA", "Spain"]
PositiveRate = Annotated[float, Field(gt=0, allow_inf_nan=False)]


class SupplierCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1)
    country: Country
    categories: list[Category] = Field(min_length=1)
    rate_per_shipment: PositiveRate
    currency: Literal["USD", "EUR"]
    status: SupplierStatus
    service_zone: str | None = None
    contact_email: EmailStr | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def validate_currency(self) -> "SupplierCreate":
        expected = "USD" if self.country == "USA" else "EUR"
        if self.currency != expected:
            raise ValueError(f"{self.country} requiere currency={expected}")
        return self


class Supplier(SupplierCreate):
    updated_at: datetime


class SupplierResponse(Supplier):
    id: int


class RateUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rate_per_shipment: PositiveRate


class StatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: SupplierStatus