from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from database import get_db
from models import SKU, StockEntry, StockExit, User
from schemas import (
    SKUCreate,
    SKUResponse,
    StockEntryCreate,
    StockEntryResponse,
    StockExitCreate,
    StockExitResponse,
    StockOrderResponse,
)
from security import get_current_user


router = APIRouter(prefix="/inventory", tags=["inventory"])


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
        category=product.category,
        warehouse=product.warehouse,
        current_stock=current_stock(session, product.id, product.warehouse),
    )


@router.get("/products", response_model=list[SKUResponse])
def list_products(session: Session = Depends(get_db)):
    products = session.exec(select(SKU).order_by(SKU.id)).all()
    return [product_response(product, session) for product in products]


@router.post(
    "/products",
    response_model=SKUResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(get_current_user)],
)
def create_product(payload: SKUCreate, session: Session = Depends(get_db)):
    product = SKU(**payload.model_dump())
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


@router.post(
    "/orders/inbound",
    response_model=StockEntryResponse,
    status_code=status.HTTP_201_CREATED,
)
def register_inbound(
    payload: StockEntryCreate,
    session: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if session.get(SKU, payload.sku_id) is None:
        raise HTTPException(status_code=404, detail="SKU no encontrado.")
    entry = StockEntry(**payload.model_dump(), user_uuid=str(current_user.user_uuid))
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


@router.post(
    "/orders/outbound",
    response_model=StockExitResponse,
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
    available = current_stock(session, payload.sku_id, payload.warehouse)
    if payload.quantity > available:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Insufficient stock for SKU '{product.sku}'. "
                f"Available: {available}, requested: {payload.quantity}."
            ),
        )
    stock_exit = StockExit(**payload.model_dump(), user_uuid=str(current_user.user_uuid))
    session.add(stock_exit)
    session.commit()
    session.refresh(stock_exit)
    return stock_exit


@router.get("/orders", response_model=list[StockOrderResponse])
def list_orders(session: Session = Depends(get_db)):
    orders = []
    entries = session.exec(select(StockEntry)).all()
    exits = session.exec(select(StockExit)).all()

    for entry in entries:
        product = session.get(SKU, entry.sku_id)
        if product is None:
            continue
        orders.append(
            StockOrderResponse(
                id=entry.id,
                order_type="inbound",
                sku_id=entry.sku_id,
                quantity=entry.quantity,
                warehouse=entry.warehouse,
                created_at=entry.created_at,
                user_uuid=entry.user_uuid,
                product=product_response(product, session),
                reference=entry.reference,
            )
        )

    for stock_exit in exits:
        product = session.get(SKU, stock_exit.sku_id)
        if product is None:
            continue
        orders.append(
            StockOrderResponse(
                id=stock_exit.id,
                order_type="outbound",
                sku_id=stock_exit.sku_id,
                quantity=stock_exit.quantity,
                warehouse=stock_exit.warehouse,
                created_at=stock_exit.created_at,
                user_uuid=stock_exit.user_uuid,
                product=product_response(product, session),
                exit_type=stock_exit.exit_type,
                tracking_number=stock_exit.tracking_number,
            )
        )

    return sorted(orders, key=lambda order: (order.created_at, order.id), reverse=True)