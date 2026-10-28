from datetime import datetime, timezone
from pathlib import Path

from tinydb import Query

from database import database_path, open_database
from models import Supplier, SupplierCreate


SUPPLIERS_SEED = [
    {
        "name": "UPS Ground", "country": "USA", "categories": ["carrier_last_mile"],
        "rate_per_shipment": 7.45, "currency": "USD", "status": "active", "service_zone": "West Coast",
        "contact_email": "business@ups.com",
        "notes": "Carrier principal para entregas locales en Los Ángeles y alrededores.",
    },
    {
        "name": "FedEx Ground", "country": "USA", "categories": ["carrier_last_mile"],
        "rate_per_shipment": 7.90, "currency": "USD", "status": "active", "service_zone": "Continental USA",
        "contact_email": "business.solutions@fedex.com",
    },
    {
        "name": "DHL Express USA", "country": "USA", "categories": ["carrier_last_mile", "carrier_international"],
        "rate_per_shipment": 14.20, "currency": "USD", "status": "active", "service_zone": "Continental USA + International",
        "contact_email": "business.us@dhl.com", "notes": "Usado para envíos urgentes y exportaciones a Europa.",
    },
    {
        "name": "OnTrac", "country": "USA", "categories": ["carrier_last_mile"],
        "rate_per_shipment": 6.10, "currency": "USD", "status": "active", "service_zone": "West Coast",
        "contact_email": "solutions@ontrac.com", "notes": "Carrier regional. Mejor tarifa en la zona de Los Ángeles.",
    },
    {
        "name": "Laser Ship", "country": "USA", "categories": ["carrier_last_mile"],
        "rate_per_shipment": 5.80, "currency": "USD", "status": "suspended", "service_zone": "East Coast",
        "contact_email": "business@lasership.com", "notes": "Suspendido. Tasa de incidencias superior al 8% en Q3.",
    },
    {
        "name": "PackSource LA", "country": "USA", "categories": ["packaging_materials"],
        "rate_per_shipment": 0.42, "currency": "USD", "status": "active", "contact_email": "orders@packsource.com",
        "notes": "Cajas, relleno y precinto para el almacén de Los Ángeles.",
    },
    {
        "name": "CleanTeam West", "country": "USA", "categories": ["cleaning_and_facilities"],
        "rate_per_shipment": 1800.0, "currency": "USD", "status": "active", "contact_email": "accounts@cleanteamwest.com",
        "notes": "Tarifa mensual por servicio de limpieza del almacén de LA.",
    },
    {
        "name": "MRW España", "country": "Spain", "categories": ["carrier_last_mile"],
        "rate_per_shipment": 4.90, "currency": "EUR", "status": "active", "service_zone": "Península Ibérica",
        "contact_email": "clientes.empresa@mrw.es", "notes": "Carrier principal para entregas en España. Contrato negociado por volumen.",
    },
    {
        "name": "SEUR", "country": "Spain", "categories": ["carrier_last_mile"],
        "rate_per_shipment": 5.20, "currency": "EUR", "status": "active", "service_zone": "Península Ibérica + Baleares",
        "contact_email": "grandes.cuentas@seur.com",
    },
    {
        "name": "DHL Express España", "country": "Spain", "categories": ["carrier_last_mile", "carrier_international"],
        "rate_per_shipment": 12.80, "currency": "EUR", "status": "active", "service_zone": "España + Internacional",
        "contact_email": "business.es@dhl.com", "notes": "Envíos urgentes y exportaciones desde Zaragoza.",
    },
    {
        "name": "Nacex", "country": "Spain", "categories": ["carrier_last_mile"],
        "rate_per_shipment": 4.60, "currency": "EUR", "status": "active", "service_zone": "Aragón y zona norte",
        "contact_email": "empresas@nacex.es", "notes": "Carrier regional con buena cobertura en Aragón.",
    },
    {
        "name": "Logística Inversa Iberia", "country": "Spain", "categories": ["reverse_logistics"],
        "rate_per_shipment": 6.30, "currency": "EUR", "status": "active", "contact_email": "operaciones@liiberia.es",
        "notes": "Gestión de devoluciones para el almacén de Zaragoza.",
    },
    {
        "name": "Embalajes Zaragoza S.L.", "country": "Spain", "categories": ["packaging_materials"],
        "rate_per_shipment": 0.28, "currency": "EUR", "status": "active", "contact_email": "pedidos@embalajeszgz.es",
    },
    {
        "name": "SAP WM Cloud", "country": "USA", "categories": ["it_and_wms_software"],
        "rate_per_shipment": 2200.0, "currency": "USD", "status": "suspended", "contact_email": "enterprise@sap.com",
        "notes": "Suspendido. Andrés está evaluando alternativas más ligeras para el almacén de LA.",
    },
    {
        "name": "ReturnBear", "country": "USA", "categories": ["reverse_logistics"],
        "rate_per_shipment": 4.15, "currency": "USD", "status": "active", "service_zone": "West Coast",
        "contact_email": "partnerships@returnbear.com", "notes": "Gestión de devoluciones para clientes de Los Ángeles.",
    },
]

INVENTORY_PRODUCTS_SEED = [
    {"name": "Zapatilla blanca clásica - Talla 42", "sku": "CLT-SNK-W-42", "client_name": "PureStep Footwear", "category": "fashion", "warehouse": "LA"},
    {"name": "Zapatilla blanca clásica - Talla 42", "sku": "CLT-SNK-W-42-Z", "client_name": "PureStep Footwear", "category": "fashion", "warehouse": "ZGZ"},
    {"name": "Auriculares inalámbricos Pro", "sku": "TEC-EAR-001", "client_name": "SoundWave Electronics", "category": "electronics", "warehouse": "LA"},
    {"name": "Sérum facial hidratante 30ml", "sku": "CSM-SRM-030", "client_name": "GlowLab Cosmetics", "category": "cosmetics", "warehouse": "ZGZ"},
    {"name": "Chino slim fit - marino 32/32", "sku": "CLT-CHN-N-32", "client_name": "UrbanThread", "category": "fashion", "warehouse": "LA"},
    {"name": "Cargador rápido USB-C 65W", "sku": "TEC-CHG-065", "client_name": "SoundWave Electronics", "category": "electronics", "warehouse": "ZGZ"},
]

INVENTORY_ENTRIES_SEED = [
    {"sku": "CLT-SNK-W-42", "warehouse": "LA", "quantity": 80, "reference": "PO-2024-0098"},
    {"sku": "CLT-SNK-W-42", "warehouse": "LA", "quantity": 35, "reference": "GR-LA-0234"},
    {"sku": "CLT-SNK-W-42-Z", "warehouse": "ZGZ", "quantity": 50, "reference": "PO-2024-0102"},
    {"sku": "CSM-SRM-030", "warehouse": "ZGZ", "quantity": 24, "reference": "GR-ZGZ-0117"},
]

INVENTORY_EXITS_SEED = [
    {"sku": "CLT-SNK-W-42", "warehouse": "LA", "quantity": 10, "exit_type": "dispatch", "tracking_number": "1Z999AA10123456784"},
    {"sku": "CLT-SNK-W-42", "warehouse": "LA", "quantity": 2, "exit_type": "loss", "tracking_number": None},
    {"sku": "CLT-SNK-W-42-Z", "warehouse": "ZGZ", "quantity": 8, "exit_type": "dispatch", "tracking_number": "MRW-2024-009812"},
]


def seed_database(path: Path) -> int:
    suppliers = [SupplierCreate.model_validate(record) for record in SUPPLIERS_SEED]
    inserted = 0
    with open_database(path) as database:
        query = Query()
        for payload in suppliers:
            if database.contains((query.name == payload.name) & (query.country == payload.country)):
                continue
            supplier = Supplier(**payload.model_dump(), updated_at=datetime.now(timezone.utc))
            database.insert(supplier.model_dump(mode="json"))
            inserted += 1
    return inserted


def seed_inventory_database(path: Path) -> dict[str, int]:
    inserted = {"products": 0, "entries": 0, "exits": 0}
    with open_database(path) as database:
        products = database.table("inventory_products")
        product_ids = {}
        query = Query()
        for record in INVENTORY_PRODUCTS_SEED:
            existing = products.get((query.sku == record["sku"]) & (query.warehouse == record["warehouse"]))
            if existing is not None:
                product_ids[(record["sku"], record["warehouse"])] = existing.doc_id
                continue
            product_id = products.insert(record)
            product_ids[(record["sku"], record["warehouse"])] = product_id
            inserted["products"] += 1

        entries = database.table("inventory_stock_entries")
        for record in INVENTORY_ENTRIES_SEED:
            sku_id = product_ids[(record["sku"], record["warehouse"])]
            if entries.contains((query.sku_id == sku_id) & (query.reference == record["reference"])):
                continue
            entries.insert({
                "sku_id": sku_id,
                "quantity": record["quantity"],
                "reference": record["reference"],
                "warehouse": record["warehouse"],
                "created_at": datetime.now(timezone.utc).isoformat(),
                "user_uuid": "seed",
            })
            inserted["entries"] += 1

        exits = database.table("inventory_stock_exits")
        for record in INVENTORY_EXITS_SEED:
            sku_id = product_ids[(record["sku"], record["warehouse"])]
            duplicate = (query.sku_id == sku_id) & (query.warehouse == record["warehouse"])
            duplicate &= query.tracking_number == record["tracking_number"] if record["tracking_number"] else query.exit_type == "loss"
            if exits.contains(duplicate):
                continue
            exits.insert({
                "sku_id": sku_id,
                "quantity": record["quantity"],
                "exit_type": record["exit_type"],
                "tracking_number": record["tracking_number"],
                "warehouse": record["warehouse"],
                "created_at": datetime.now(timezone.utc).isoformat(),
                "user_uuid": "seed",
            })
            inserted["exits"] += 1
    return inserted


def main():
    print(f"Proveedores insertados: {seed_database(database_path())}")
    inventory = seed_inventory_database(database_path())
    print(
        "Inventario insertado: "
        f"{inventory['products']} SKU, {inventory['entries']} entradas, {inventory['exits']} salidas"
    )