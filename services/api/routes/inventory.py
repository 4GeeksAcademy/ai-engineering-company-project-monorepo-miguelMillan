from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from tinydb import Query

from models import (
    InventoryOrderResponse,
    SKU,
    SKUCreate,
    StockEntry,
    StockEntryCreate,
    StockExit,
    StockExitCreate,
    User,
)
from security import get_current_user


router = APIRouter(prefix="/inventory", tags=["inventory"], dependencies=[Depends(get_current_user)])


def find_sku(request: Request, sku_id: int):
    product = request.app.state.db.table("inventory_products").get(doc_id=sku_id)
    if product is None:
        raise HTTPException(status_code=404, detail="SKU no encontrado.")
    return product


def stock_for(request: Request, sku_id: int, warehouse: str) -> int:
    entries = request.app.state.db.table("inventory_stock_entries").all()
    exits = request.app.state.db.table("inventory_stock_exits").all()
    inbound = sum(
        item["quantity"] for item in entries
        if item["sku_id"] == sku_id and item["warehouse"] == warehouse
    )
    outbound = sum(
        item["quantity"] for item in exits
        if item["sku_id"] == sku_id and item["warehouse"] == warehouse
    )
    return inbound - outbound


def serialize_sku(request: Request, sku_id: int, document=None) -> SKU:
    product = document if document is not None else find_sku(request, sku_id)
    return SKU(id=sku_id, current_stock=stock_for(request, sku_id, product["warehouse"]), **product)


def check_product_warehouse(product: dict, warehouse: str) -> None:
    if product["warehouse"] != warehouse:
        raise HTTPException(status_code=400, detail="El almacén del movimiento debe coincidir con el almacén del SKU.")


@router.post("/products", response_model=SKU, status_code=201)
def create_sku(payload: SKUCreate, request: Request):
    products = request.app.state.db.table("inventory_products")
    with request.app.state.lock:
        duplicate = products.get(
            (Query().sku == payload.sku) & (Query().warehouse == payload.warehouse)
        )
        if duplicate is not None:
            raise HTTPException(status_code=409, detail="Ya existe ese SKU en el almacén indicado.")
        document = payload.model_dump()
        sku_id = products.insert(document)
        return serialize_sku(request, sku_id, document)


@router.get("/products", response_model=list[SKU])
def list_skus(request: Request):
    products = request.app.state.db.table("inventory_products")
    with request.app.state.lock:
        return [serialize_sku(request, document.doc_id, document) for document in products]


@router.get("/products/{sku_id}", response_model=SKU)
def get_sku(sku_id: int, request: Request):
    with request.app.state.lock:
        return serialize_sku(request, sku_id)


@router.post("/orders/inbound", response_model=StockEntry, status_code=201)
def create_stock_entry(payload: StockEntryCreate, request: Request, current_user: User = Depends(get_current_user)):
    with request.app.state.lock:
        product = find_sku(request, payload.sku_id)
        check_product_warehouse(product, payload.warehouse)
        document = {
            **payload.model_dump(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "user_uuid": str(current_user.id),
        }
        order_id = request.app.state.db.table("inventory_stock_entries").insert(document)
    return StockEntry(id=order_id, **document)


@router.post("/orders/outbound", response_model=StockExit, status_code=201)
def create_stock_exit(payload: StockExitCreate, request: Request, current_user: User = Depends(get_current_user)):
    with request.app.state.lock:
        product = find_sku(request, payload.sku_id)
        check_product_warehouse(product, payload.warehouse)
        available = stock_for(request, payload.sku_id, payload.warehouse)
        if payload.quantity > available:
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient stock for SKU '{product['sku']}'. Available: {available}, requested: {payload.quantity}.",
            )
        document = {
            **payload.model_dump(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "user_uuid": str(current_user.id),
        }
        order_id = request.app.state.db.table("inventory_stock_exits").insert(document)
    return StockExit(id=order_id, **document)


@router.get("/orders", response_model=list[InventoryOrderResponse])
def list_stock_orders(request: Request):
    products = request.app.state.db.table("inventory_products")
    entries = request.app.state.db.table("inventory_stock_entries")
    exits = request.app.state.db.table("inventory_stock_exits")
    with request.app.state.lock:
        product_by_id = {product.doc_id: product for product in products}
        orders = []
        for document in entries:
            product = product_by_id.get(document["sku_id"], {})
            orders.append({
                **document,
                "id": document.doc_id,
                "sku_name": product.get("name", f"SKU #{document['sku_id']}"),
                "type": "inbound",
            })
        for document in exits:
            product = product_by_id.get(document["sku_id"], {})
            orders.append({
                **document,
                "id": document.doc_id,
                "sku_name": product.get("name", f"SKU #{document['sku_id']}"),
                "type": "outbound",
            })
    return sorted(orders, key=lambda order: order["created_at"], reverse=True)
