from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from database import get_db
from models import InventoryAudit, SKU, StockEntry, StockExit, User, client_id_from_name
from schemas import (
    DirectStockEditAttempt,
    InventoryAuditCreate,
    InventoryAuditResponse,
    InventoryTelemetrySignal,
    SKUCreate,
    SKUResponse,
    StockEntryCreate,
    StockMovementCreatedResponse,
    StockExitCreate,
    StockOrderListItemResponse,
    StockThresholdUpdate,
)
from security import get_current_user


router = APIRouter(prefix="/inventory", tags=["inventory"], dependencies=[Depends(get_current_user)])


def current_stock(session: Session, sku_id: int, warehouse: str) -> int:
    entries = session.exec(
        select(func.coalesce(func.sum(StockEntry.quantity), 0)).where(
            StockEntry.sku_id == sku_id,
            StockEntry.warehouse == warehouse,
        )
    ).one()
    exits = session.exec(
        select(func.coalesce(func.sum(StockExit.quantity), 0)).where(
            StockExit.sku_id == sku_id,
            StockExit.warehouse == warehouse,
        )
    ).one()
    return int(entries or 0) - int(exits or 0)


def product_response(product: SKU, session: Session) -> SKUResponse:
    return SKUResponse(
        id=product.id,
        name=product.name,
        sku=product.sku,
        client_name=product.client_name,
        client_id=product.client_id,
        category=product.category,
        warehouse=product.warehouse,
        current_stock=current_stock(session, product.id, product.warehouse),
        minimum_stock=product.minimum_stock,
    )


@router.get("/products", response_model=list[SKUResponse])
def list_products(session: Session = Depends(get_db)):
    products = session.exec(select(SKU).order_by(SKU.id)).all()
    return [product_response(product, session) for product in products]


@router.post(
    "/products",
    response_model=SKUResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_product(payload: SKUCreate, session: Session = Depends(get_db)):
    values = payload.model_dump()
    values["client_id"] = payload.client_id or client_id_from_name(payload.client_name)
    product = SKU(**values)
    session.add(product)
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(status_code=409, detail="Ya existe un producto con ese SKU.") from error
    session.refresh(product)
    return product_response(product, session)


@router.get("/products/{product_id}", response_model=SKUResponse)
def get_product(product_id: int, session: Session = Depends(get_db)):
    product = session.get(SKU, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="SKU no encontrado.")
    return product_response(product, session)


@router.patch("/products/{product_id}/threshold", response_model=SKUResponse)
def update_stock_threshold(
    product_id: int,
    payload: StockThresholdUpdate,
    session: Session = Depends(get_db),
):
    product = session.get(SKU, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="SKU no encontrado.")
    product.minimum_stock = payload.minimum_stock
    session.add(product)
    session.commit()
    session.refresh(product)
    return product_response(product, session)


@router.put("/products/{product_id}/stock")
def reject_direct_stock_edit(
    product_id: int,
    payload: DirectStockEditAttempt,
    session: Session = Depends(get_db),
):
    product = session.get(SKU, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="SKU no encontrado.")
    properties = {
        "warehouse": "los_angeles" if product.warehouse == "LA" else "zaragoza",
        "client_id": product.client_id,
        "product_id": product.sku,
        "product_category": product.category,
        "quantity": payload.quantity,
    }
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "direct_stock_edit_rejected",
            "message": "El stock solo puede modificarse mediante una entrada o salida de inventario.",
            "telemetry_event": {
                "event_type": "direct_stock_edit_rejected",
                "properties": properties,
            },
        },
    )


@router.post(
    "/orders/inbound",
    response_model=StockMovementCreatedResponse,
    status_code=status.HTTP_201_CREATED,
)
def register_inbound(
    payload: StockEntryCreate,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    product = session.get(SKU, payload.sku_id)
    if product is None:
        raise HTTPException(status_code=404, detail="SKU no encontrado.")
    if payload.warehouse != product.warehouse:
        raise HTTPException(status_code=400, detail="El almacén debe coincidir con el almacén del SKU.")
    entry = StockEntry(**payload.model_dump(), user_uuid=str(current_user.user_uuid))
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return StockMovementCreatedResponse(id=entry.id)


@router.post(
    "/orders/outbound",
    response_model=StockMovementCreatedResponse,
    status_code=status.HTTP_201_CREATED,
)
def register_outbound(
    payload: StockExitCreate,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    product = session.exec(
        select(SKU).where(SKU.id == payload.sku_id).with_for_update()
    ).first()
    if product is None:
        raise HTTPException(status_code=404, detail="SKU no encontrado.")
    if payload.warehouse != product.warehouse:
        raise HTTPException(status_code=400, detail="El almacén debe coincidir con el almacén del SKU.")
    available = current_stock(session, payload.sku_id, payload.warehouse)
    if payload.quantity > available:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Insufficient stock for SKU '{product.sku}'. "
                f"Available: {available}, requested: {payload.quantity}."
            ),
        )
    stock_before = available
    stock_exit = StockExit(**payload.model_dump(), user_uuid=str(current_user.user_uuid))
    session.add(stock_exit)
    session.flush()
    stock_after = current_stock(session, payload.sku_id, payload.warehouse)
    telemetry_events = []
    if (
        product.minimum_stock is not None
        and stock_before >= product.minimum_stock
        and stock_after < product.minimum_stock
    ):
        telemetry_events.append(
            InventoryTelemetrySignal(
                event_type="stock_threshold_triggered",
                properties={
                    "warehouse": "los_angeles" if product.warehouse == "LA" else "zaragoza",
                    "client_id": product.client_id,
                    "product_id": product.sku,
                    "product_category": product.category,
                    "quantity": stock_after,
                    "threshold": product.minimum_stock,
                },
            )
        )
    session.commit()
    session.refresh(stock_exit)
    return StockMovementCreatedResponse(id=stock_exit.id, telemetry_events=telemetry_events)


@router.post("/audits", response_model=InventoryAuditResponse, status_code=status.HTTP_201_CREATED)
def register_inventory_audit(
    payload: InventoryAuditCreate,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    product = session.get(SKU, payload.sku_id)
    if product is None:
        raise HTTPException(status_code=404, detail="SKU no encontrado.")
    system_stock = current_stock(session, product.id, product.warehouse)
    discrepancy = abs(system_stock - payload.physical_stock)
    direction = (
        "physical_lower" if payload.physical_stock < system_stock
        else "physical_higher" if payload.physical_stock > system_stock
        else None
    )
    audit = InventoryAudit(
        sku_id=product.id,
        warehouse=product.warehouse,
        system_stock=system_stock,
        physical_stock=payload.physical_stock,
        discrepancy_quantity=discrepancy,
        discrepancy_direction=direction,
        user_uuid=str(current_user.user_uuid),
    )
    session.add(audit)
    session.commit()
    session.refresh(audit)
    telemetry_event = None
    if discrepancy:
        telemetry_event = InventoryTelemetrySignal(
            event_type="inventory_discrepancy_detected",
            properties={
                "warehouse": "los_angeles" if product.warehouse == "LA" else "zaragoza",
                "client_id": product.client_id,
                "product_id": product.sku,
                "product_category": product.category,
                "quantity": discrepancy,
                "direction": direction or "physical_lower",
            },
        )
    return InventoryAuditResponse(
        id=audit.id,
        sku_id=audit.sku_id,
        warehouse=product.warehouse,
        system_stock=audit.system_stock,
        physical_stock=audit.physical_stock,
        discrepancy_quantity=audit.discrepancy_quantity,
        discrepancy_direction=audit.discrepancy_direction,
        created_at=audit.created_at,
        telemetry_event=telemetry_event,
    )


@router.get("/orders", response_model=list[StockOrderListItemResponse])
def list_orders(session: Session = Depends(get_db)):
    orders = []
    entries = session.exec(select(StockEntry)).all()
    exits = session.exec(select(StockExit)).all()

    for entry in entries:
        product = session.get(SKU, entry.sku_id)
        if product is None:
            continue
        orders.append(
            StockOrderListItemResponse(
                id=entry.id,
                order_type="inbound",
                sku_id=entry.sku_id,
                product_name=product.name,
                quantity=entry.quantity,
                warehouse=entry.warehouse,
                created_at=entry.created_at,
                user_uuid=entry.user_uuid,
                reference=entry.reference,
            )
        )

    for stock_exit in exits:
        product = session.get(SKU, stock_exit.sku_id)
        if product is None:
            continue
        orders.append(
            StockOrderListItemResponse(
                id=stock_exit.id,
                order_type="outbound",
                sku_id=stock_exit.sku_id,
                product_name=product.name,
                quantity=stock_exit.quantity,
                warehouse=stock_exit.warehouse,
                created_at=stock_exit.created_at,
                user_uuid=stock_exit.user_uuid,
                exit_type=stock_exit.exit_type,
                tracking_number=stock_exit.tracking_number,
            )
        )

    return sorted(orders, key=lambda order: (order.created_at, order.id), reverse=True)