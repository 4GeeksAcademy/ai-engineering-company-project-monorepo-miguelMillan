"""Redacción determinista de los resultados de las tools.

Los datos operativos (estado de un ticket, stock de un producto) se muestran
tal cual los devolvió el servicio, sin pasar por el LLM: así es imposible que
el modelo "invente" un estado o un número de unidades. El LLM solo redacta la
parte que viene de la base de conocimiento (RAG). Si una tool falla, la
respuesta lo dice con honestidad y no da ningún estado.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from services.agent.tools import BRANCH_LABELS, CATEGORY_LABELS, ORIGIN_LABELS, STATUS_LABELS

WAREHOUSE_LABELS = {"LA": "Los Ángeles", "ZGZ": "Zaragoza"}
MAX_LISTED = 5


def _date(value: str | None) -> str:
    if not value:
        return "fecha desconocida"
    return datetime.fromisoformat(value).strftime("%d/%m/%Y")


def _filters_description(query: dict[str, Any]) -> str:
    parts = []
    if query.get("status"):
        parts.append(f"en estado {STATUS_LABELS[query['status']]}")
    if query.get("category"):
        parts.append(f"de categoría {CATEGORY_LABELS[query['category']]}")
    if query.get("origin"):
        parts.append(f"con origen {ORIGIN_LABELS[query['origin']]}")
    if query.get("branch"):
        parts.append(f"de la sede {BRANCH_LABELS[query['branch']]}")
    return " ".join(parts)


def render_incidents(result: dict[str, Any]) -> str:
    query = result["query"]
    if not result["ok"]:
        error = result["error_type"]
        if error == "not_found":
            return f"No encontré ningún ticket con el identificador {query['ticket_id']} en el gestor de incidencias."
        if error == "invalid_query":
            return (f"No pude buscar el ticket «{query['raw_ticket_reference']}»: en TrackFlow los tickets se identifican "
                    "con un UUID (por ejemplo 3f2b9c1e-1a2b-4c3d-8e9f-0123456789ab). Compártelo completo y lo consulto.")
        if error == "forbidden":
            return ("No puedo consultar tickets desde tu cuenta: el gestor de incidencias es una herramienta interna del equipo "
                    "de TrackFlow. Si se trata de un envío tuyo, dime su número de seguimiento.")
        if error == "unauthorized":
            return "No pude consultar el gestor de incidencias porque la consulta no tiene credenciales válidas."
        subject = "el estado de ese ticket" if result["mode"] == "detail" else "los tickets"
        cause = "no respondió a tiempo" if error == "timeout" else "no está disponible"
        return (f"No pude confirmar {subject} ahora mismo: el gestor de incidencias {cause}. "
                "No te doy ningún estado para no inventarlo; inténtalo de nuevo en unos minutos.")

    incidents = result["incidents"]
    if result["mode"] == "detail":
        t = incidents[0]
        return (f"El ticket {t['id']} («{t['title']}») está en estado **{STATUS_LABELS[t['status']]}**. "
                f"Categoría: {CATEGORY_LABELS[t['category']]}; origen: {ORIGIN_LABELS[t['origin']]}; "
                f"sede: {BRANCH_LABELS[t['branch']]}. Creado el {_date(t.get('created_at'))}, "
                f"última actualización el {_date(t.get('updated_at'))}. (Fuente: gestor de incidencias, consulta en vivo.)")

    filters = _filters_description(query)
    if result["total"] == 0:
        return f"No hay tickets {filters or 'registrados'} en el gestor de incidencias en este momento."
    lines = [f"Hay {result['total']} tickets {filters} en el gestor de incidencias (consulta en vivo)."
             .replace("  ", " ")]
    for t in incidents[:MAX_LISTED]:
        lines.append(f"- {t['id']} — {STATUS_LABELS[t['status']]} — {t['title'][:70]}")
    if result["total"] > MAX_LISTED:
        lines.append(f"(se muestran los {MAX_LISTED} más recientes)")
    return "\n".join(lines)


def render_inventory(result: dict[str, Any]) -> str:
    query = result["query"]
    if not result["ok"]:
        error = result["error_type"]
        if error == "not_found":
            target = query.get("sku") or " ".join(query.get("name_terms") or [])
            return f"No encontré el producto «{target}» en el gestor de inventario."
        if error == "invalid_query":
            return "Indica el SKU o el nombre del producto para consultar su stock."
        if error == "forbidden":
            return "No puedo consultar el inventario desde tu cuenta: solo lo ven el equipo de TrackFlow y la marca propietaria."
        if error == "unauthorized":
            return "No pude consultar el gestor de inventario porque la consulta no tiene credenciales válidas."
        cause = "no respondió a tiempo" if error == "timeout" else "no está disponible"
        return (f"No pude confirmar el stock ahora mismo: el gestor de inventario {cause}. "
                "No te doy ninguna cantidad para no inventarla; inténtalo de nuevo en unos minutos.")
    lines = []
    for item in result["items"]:
        line = (f"{item['name']} (SKU {item['sku']}, almacén {WAREHOUSE_LABELS[item['warehouse']]}, "
                f"cliente {item['client_name']}): {item['current_stock']} unidades en stock")
        minimum = item.get("minimum_stock")
        if minimum is not None:
            line += f" (mínimo configurado {minimum}{'; POR DEBAJO DEL MÍNIMO' if item['current_stock'] < minimum else ''})"
        lines.append(line + ".")
    lines.append("(Fuente: gestor de inventario, consulta en vivo.)")
    return "\n".join(lines)


def compose(incident_result: dict[str, Any] | None, inventory_result: dict[str, Any] | None,
            rag_answer: str | None, rag_planned: bool, no_context_answer: str) -> str:
    """Respuesta final en el orden de ejecución: tools primero y, si se planificó, la parte de RAG."""
    parts = []
    if incident_result is not None:
        parts.append(render_incidents(incident_result))
    if inventory_result is not None:
        parts.append(render_inventory(inventory_result))
    if rag_planned:
        parts.append(rag_answer or no_context_answer)
    return "\n\n".join(parts)
