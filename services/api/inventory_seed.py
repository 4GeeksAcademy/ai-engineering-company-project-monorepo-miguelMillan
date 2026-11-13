from sqlmodel import Session, select

from database import create_sql_engine, initialize_sql_database
from models import SKU, StockEntry, StockExit


SEED_USER_UUID = "00000000-0000-4000-8000-000000000001"

INVENTORY_SKUS = [
    {
        "name": "Zapatilla blanca clásica - Talla 42",
        "sku": "CLT-SNK-W-42",
        "client_name": "PureStep Footwear",
        "client_id": "purestep_footwear",
        "category": "fashion",
        "warehouse": "LA",
    },
    {
        "name": "Zapatilla blanca clásica - Talla 42",
        "sku": "CLT-SNK-W-42-Z",
        "client_name": "PureStep Footwear",
        "client_id": "purestep_footwear",
        "category": "fashion",
        "warehouse": "ZGZ",
    },
    {
        "name": "Auriculares inalámbricos Pro",
        "sku": "TEC-EAR-001",
        "client_name": "SoundWave Electronics",
        "client_id": "soundwave_electronics",
        "category": "electronics",
        "warehouse": "LA",
    },
    {
        "name": "Sérum facial hidratante 30ml",
        "sku": "CSM-SRM-030",
        "client_name": "GlowLab Cosmetics",
        "client_id": "glowlab_cosmetics",
        "category": "cosmetics",
        "warehouse": "ZGZ",
    },
    {
        "name": "Chino slim fit - marino 32/32",
        "sku": "CLT-CHN-N-32",
        "client_name": "UrbanThread",
        "client_id": "urbanthread",
        "category": "fashion",
        "warehouse": "LA",
    },
    {
        "name": "Cargador rápido USB-C 65W",
        "sku": "TEC-CHG-065",
        "client_name": "SoundWave Electronics",
        "client_id": "soundwave_electronics",
        "category": "electronics",
        "warehouse": "ZGZ",
    },
]

INVENTORY_ENTRIES = [
    {"sku": "CLT-SNK-W-42", "quantity": 50, "reference": "PO-2024-0098", "warehouse": "LA"},
    {"sku": "CLT-SNK-W-42", "quantity": 30, "reference": "GR-LA-0234", "warehouse": "LA"},
    {"sku": "CLT-SNK-W-42-Z", "quantity": 40, "reference": "PO-2024-0130", "warehouse": "ZGZ"},
    {"sku": "TEC-EAR-001", "quantity": 80, "reference": "GR-LA-0301", "warehouse": "LA"},
    {"sku": "CSM-SRM-030", "quantity": 100, "reference": "GR-ZGZ-0142", "warehouse": "ZGZ"},
]

INVENTORY_EXITS = [
    {
        "sku": "CLT-SNK-W-42",
        "quantity": 15,
        "exit_type": "dispatch",
        "tracking_number": "1Z999AA10123456784",
        "warehouse": "LA",
    },
    {
        "sku": "CLT-SNK-W-42",
        "quantity": 2,
        "exit_type": "loss",
        "tracking_number": None,
        "warehouse": "LA",
    },
    {
        "sku": "CSM-SRM-030",
        "quantity": 7,
        "exit_type": "dispatch",
        "tracking_number": "1Z999AA10123456785",
        "warehouse": "ZGZ",
    },
]


def seed_inventory() -> dict[str, int]:
    engine = create_sql_engine()
    try:
        initialize_sql_database(engine)
        with Session(engine) as session:
            products_by_code = {product.sku: product for product in session.exec(select(SKU)).all()}
            inserted_skus = 0
            for record in INVENTORY_SKUS:
                if record["sku"] not in products_by_code:
                    product = SKU(**record)
                    session.add(product)
                    products_by_code[product.sku] = product
                    inserted_skus += 1
            session.commit()
            products_by_code = {product.sku: product for product in session.exec(select(SKU)).all()}

            references = set(session.exec(select(StockEntry.reference)).all())
            inserted_entries = 0
            for record in INVENTORY_ENTRIES:
                if record["reference"] in references:
                    continue
                session.add(
                    StockEntry(
                        sku_id=products_by_code[record["sku"]].id,
                        quantity=record["quantity"],
                        reference=record["reference"],
                        warehouse=record["warehouse"],
                        user_uuid=SEED_USER_UUID,
                    )
                )
                references.add(record["reference"])
                inserted_entries += 1

            existing_exits = {
                (
                    stock_exit.sku_id,
                    stock_exit.quantity,
                    stock_exit.exit_type,
                    stock_exit.tracking_number,
                    stock_exit.warehouse,
                    stock_exit.user_uuid,
                )
                for stock_exit in session.exec(select(StockExit)).all()
            }
            inserted_exits = 0
            for record in INVENTORY_EXITS:
                product = products_by_code[record["sku"]]
                key = (
                    product.id,
                    record["quantity"],
                    record["exit_type"],
                    record["tracking_number"],
                    record["warehouse"],
                    SEED_USER_UUID,
                )
                if key in existing_exits:
                    continue
                session.add(
                    StockExit(
                        sku_id=product.id,
                        quantity=record["quantity"],
                        exit_type=record["exit_type"],
                        tracking_number=record["tracking_number"],
                        warehouse=record["warehouse"],
                        user_uuid=SEED_USER_UUID,
                    )
                )
                existing_exits.add(key)
                inserted_exits += 1

            session.commit()
            return {
                "skus": inserted_skus,
                "entries": inserted_entries,
                "exits": inserted_exits,
            }
    finally:
        engine.dispose()


def main() -> None:
    inserted = seed_inventory()
    print(
        "Inventario insertado: "
        f"{inserted['skus']} SKUs, {inserted['entries']} entradas, {inserted['exits']} salidas."
    )