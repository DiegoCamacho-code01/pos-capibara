"""Faro Café POS. Python 3.11+. Ejecutar: streamlit run app.py.

Una sola instancia de Streamlit, varios teléfonos. Registro de eventos inmutable
en Google Sheets; importes enteros en centavos. Ver LEEME.md antes del cambio.
"""
from __future__ import annotations

import copy
import csv
import hashlib
import html
import hmac
import io
import json
import logging
import os
import textwrap
import threading
import time
import unicodedata
import urllib.parse
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from zoneinfo import ZoneInfo

import gspread
import streamlit as st

TZ = ZoneInfo("America/Mexico_City")
LOG_SHEET = "FARO_V2_EVENTOS"
HEADERS = ["id", "fecha", "operador", "tipo", "parte", "partes", "datos_json", "sha256"]
LOCATIONS = ["Carrito", "Puesto"]
METHODS = ["Efectivo", "Transferencia", "Tarjeta"]
STATES = ["Por preparar", "Preparando", "Listo", "En reparto", "Entregado", "Cancelado"]
ADMIN_TYPES = {"bootstrap", "asset", "stock_adjust", "stock_loss", "stock_move", "cancel_line", "refund", "reopen", "close", "edit_line", "balance_adjust", "reverse_payment", "withdraw"}


@st.cache_resource
def error_types():
    # Store sobrevive a los reruns. Sus excepciones deben conservar la misma
    # identidad también; redefinirlas en cada ejecución rompe except RuleError.
    class RuleError(Exception):
        """El movimiento no cumple una regla de negocio; no se escribió."""

    class DataError(Exception):
        """Datos incompletos o alterados: detener, nunca reemplazar por ceros."""

    return RuleError, DataError


RuleError, DataError = error_types()


def is_app_error(exc, error_type):
    # Compatibilidad con Store que siga en caché durante una actualización.
    cls = type(exc)
    return isinstance(exc, error_type) or (
        cls.__name__ == error_type.__name__ and cls.__module__ in {__name__, "__main__", "app"})


def now():
    return datetime.now(TZ)


def day():
    return now().date().isoformat()


def uid():
    return uuid.uuid4().hex


def canon(value):
    return " ".join(str(value).strip().casefold().split())


def sort_key(value):
    return "".join(c for c in unicodedata.normalize("NFD", str(value).casefold()) if not unicodedata.combining(c))


def cents(value):
    try:
        number = Decimal(str(value).replace("$", "").replace(",", "").strip())
        if not number.is_finite():
            raise ValueError
        return int((number * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (ValueError, InvalidOperation):
        raise RuleError("Importe inválido. Usa números, por ejemplo 65.50.") from None


def money(value):
    return f"${value / 100:,.2f}"


def require(condition, message):
    if not condition:
        raise RuleError(message)


def json_text(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def initial_state():
    return dict(products={}, assets={}, customers={}, balances={}, orders={}, payments=[],
                stock={}, cash=[], openings={}, closed={}, transfers={}, counts=[],
                prints={}, events=[], initialized=False, history=[], schema=2, reversed_payments=[], workers={})


def add_stock(s, location, asset, qty):
    key = (location, asset)
    s["stock"][key] = s["stock"].get(key, 0) + qty


def add_balance(s, customer, amount, event, detail):
    s["balances"][customer] = s["balances"].get(customer, 0) + amount
    s["history"].append(dict(customer=customer, amount=amount, at=event["at"],
                             actor=event["actor"], detail=detail, event=event["id"]))


def add_cash(s, event, owner, amount, detail):
    s["cash"].append(dict(at=event["at"], date=event["at"][:10], owner=owner,
                          amount=amount, detail=detail, event=event["id"], actor=event["actor"]))


def consume(s, line):
    if not line.get("consumed"):
        for asset, qty in line["recipe"].items():
            add_stock(s, line["location"], asset, -qty * line["qty"])
        line["consumed"] = True


def apply_event(s, event):
    """Reductor puro. Un evento contiene todos los efectos de la operación."""
    kind, p = event["kind"], copy.deepcopy(event["payload"])
    date, actor = event["at"][:10], event["actor"]
    if kind == "bootstrap":
        s["initialized"] = True
        s["products"].update({x["id"]: x for x in p["products"]})
        s["assets"].update({x["id"]: x for x in p["assets"]})
        s["customers"].update({x["id"]: x for x in p["customers"]})
        for cid, amount in p.get("balances", {}).items():
            if amount:
                add_balance(s, cid, amount, event, "Saldo anterior importado")
        for x in p.get("stock", []):
            add_stock(s, x["location"], x["asset"], x["qty"])
        for order in p.get("legacy_orders", []):
            s["orders"][order["id"]] = order
    elif kind == "inventory_v32":
        for product in s["products"].values():
            if "chilaquiles" in sort_key(product["name"]):
                product["recipe"] = {}
        for order in s["orders"].values():
            for line in order["lines"]:
                if not line.get("consumed") and "chilaquiles" in sort_key(line["name"]):
                    line["recipe"] = {}
        s["inventory_revision"] = 32
    elif kind == "inventory_v31":
        for product in s["products"].values():
            if is_torta(product):
                product["recipe"] = controlled_recipe(product)
                s["assets"].setdefault(product["id"], dict(id=product["id"], name=product["name"], unit="pieza"))
        for order in s["orders"].values():
            for line in order["lines"]:
                if not line.get("consumed") and line["status"] != "Cancelado" and is_torta(line):
                    line["recipe"] = controlled_recipe(line)
        s["inventory_revision"] = 31
    elif kind == "worker":
        s["workers"][actor] = p["name"]
    elif kind == "upgrade_v3":
        # Solo cambia la proyección. El registro original permanece intacto.
        pooled = defaultdict(int)
        for (_, asset), qty in s["stock"].items():
            pooled[("Puesto", asset)] += qty
        s["stock"] = dict(pooled)
        s["assets"].setdefault("vaso_caliente", dict(id="vaso_caliente", name="Vasos de café y té", unit="pieza"))
        s["assets"]["vaso_caliente"]["name"] = "Vasos de café y té"
        for product in s["products"].values():
            product["recipe"] = controlled_recipe(product)
            if is_torta(product):
                s["assets"].setdefault(product["id"], dict(id=product["id"], name=product["name"], unit="pieza"))
        for order in s["orders"].values():
            for line in order["lines"]:
                line["location"] = "Puesto"
                if not line.get("consumed"):
                    line["recipe"] = controlled_recipe(line)
        s["schema"] = 3
    elif kind == "edit_line":
        order = s["orders"][p["order"]]
        line = next(x for x in order["lines"] if x["id"] == p["line"])
        delta = p["qty"] * p["unit"] - line["qty"] * line["unit"]
        line.setdefault("changes", []).append(dict(at=event["at"], actor=actor, reason=p["reason"],
            before={k: copy.deepcopy(line[k]) for k in ("qty", "unit", "notes", "due", "scheduled")},
            after={k: p[k] for k in ("qty", "unit", "notes", "due", "scheduled")}))
        line.update({k: p[k] for k in ("qty", "unit", "notes", "due", "scheduled")})
        line.update(updated_at=event["at"], updated_by=actor)
        order["total"] += delta
        if delta:
            add_balance(s, order["customer"]["id"], delta, event, "Corrección de pedido " + order["folio"] + " · " + p["reason"])
    elif kind == "balance_adjust":
        add_balance(s, p["customer"], p["amount"], event, "Ajuste administrativo · " + p["reason"])
    elif kind == "reverse_payment":
        payment = next(x for x in s["payments"] if x["id"] == p["payment"])
        amount = payment["amount"]
        add_balance(s, payment["customer"], amount, event, "Corrección de cobro · " + p["reason"])
        s["payments"].append(dict(customer=payment["customer"], amount=amount, method=payment["method"],
            at=event["at"], actor=payment["actor"], corrected_by=actor, id=event["id"], refund=True, reversal_of=payment["id"]))
        if payment["method"] == "Efectivo":
            add_cash(s, event, payment["actor"], -amount, "Corrección de cobro · " + p["reason"])
        s["reversed_payments"].append(payment["id"])
    elif kind in {"product", "asset", "customer"}:
        s[{"product": "products", "asset": "assets", "customer": "customers"}[kind]][p["id"]] = p
        if kind == "product" and s.get("schema", 2) >= 3 and is_torta(p):
            s["assets"].setdefault(p["id"], dict(id=p["id"], name=p["name"], unit="pieza"))
    elif kind == "sale":
        order = copy.deepcopy(p)
        order.update(at=event["at"], actor=actor, legacy=False)
        if order["customer"]["id"] not in s["customers"]:
            s["customers"][order["customer"]["id"]] = order["customer"]
        s["orders"][order["id"]] = order
        add_balance(s, order["customer"]["id"], order["total"], event, "Pedido " + order["folio"])
        for line in order["lines"]:
            if line["status"] == "Entregado":
                consume(s, line)
        if p["paid"]:
            payment = dict(customer=p["customer"]["id"], amount=p["paid"], method=p["method"],
                           at=event["at"], actor=actor, id=event["id"], order=p["id"])
            if payment["method"] == "Efectivo" and "received" in p:
                payment.update(received=p.get("received", p["paid"]), change=p.get("received", p["paid"]) - p["paid"])
            s["payments"].append(payment)
            add_balance(s, payment["customer"], -payment["amount"], event, "Cobro " + order["folio"])
            if payment["method"] == "Efectivo":
                add_cash(s, event, actor, payment["amount"], "Cobro " + order["folio"])
    elif kind in {"payment", "refund"}:
        sign = 1 if kind == "refund" else -1
        add_balance(s, p["customer"], sign * p["amount"], event,
                    "Devolución al cliente" if sign == 1 else "Abono · " + p["method"])
        payment = dict(**p, at=event["at"], actor=actor, id=event["id"], refund=sign == 1)
        if kind == "payment" and p["method"] == "Efectivo" and "received" in p:
            payment.update(received=p.get("received", p["amount"]), change=p.get("received", p["amount"]) - p["amount"])
        s["payments"].append(payment)
        if p["method"] == "Efectivo":
            add_cash(s, event, actor, -sign * p["amount"], "Devolución" if sign == 1 else "Abono")
    elif kind == "line_state":
        line = next(x for x in s["orders"][p["order"]]["lines"] if x["id"] == p["line"])
        line["status"] = p["status"]
        line["updated_at"] = event["at"]
        line["updated_by"] = actor
        if p["status"] in {"Preparando", "Listo", "En reparto", "Entregado"}:
            consume(s, line)
    elif kind == "cancel_line":
        order = s["orders"][p["order"]]
        line = next(x for x in order["lines"] if x["id"] == p["line"])
        if p.get("restock") and line.get("consumed"):
            for asset, qty in line["recipe"].items():
                add_stock(s, line["location"], asset, qty * line["qty"])
        line.update(status="Cancelado", cancellation=p["reason"], updated_at=event["at"], updated_by=actor)
        adjustment = line["qty"] * line["unit"]
        order["total"] -= adjustment
        if not order.get("legacy"):
            add_balance(s, order["customer"]["id"], -adjustment, event, "Cancelación " + order["folio"])
    elif kind in {"stock_load", "stock_adjust", "stock_loss", "stock_move", "stock_count"}:
        if kind == "stock_move":
            for asset, qty in p["items"].items():
                add_stock(s, p["source"], asset, -qty)
                add_stock(s, p["target"], asset, qty)
        elif kind == "stock_count":
            result = []
            for asset, counted in p["items"].items():
                expected = s["stock"].get((p["location"], asset), 0)
                result.append(dict(asset=asset, expected=expected, counted=counted, difference=counted - expected))
                if p.get("adjust"):
                    add_stock(s, p["location"], asset, counted - expected)
            s["counts"].append(dict(at=event["at"], actor=actor, rows=result, **p))
        else:
            for asset, qty in p["items"].items():
                add_stock(s, p["location"], asset, -qty if kind == "stock_loss" else qty)
    elif kind == "opening":
        owner = "__puesto__" if p.get("shared") else actor
        s["openings"][(date, owner)] = p["amount"]
        add_cash(s, event, owner, p["amount"], "Fondo inicial · registrado por " + actor)
    elif kind in {"expense", "withdraw"}:
        if p["method"] == "Efectivo":
            add_cash(s, event, actor, -p["amount"], p["reason"])
    elif kind == "transfer":
        s["transfers"][event["id"]] = dict(**p, sender=actor, status="Pendiente", at=event["at"])
        add_cash(s, event, actor, -p["amount"], "Entregado a " + p["target"] + " · por confirmar")
    elif kind == "transfer_accept":
        transfer = s["transfers"][p["transfer"]]
        transfer.update(status="Recibido", received_at=event["at"])
        add_cash(s, event, actor, transfer["amount"], "Recibido de " + transfer["sender"])
    elif kind == "close":
        s["closed"][(date, p.get("owner", actor))] = dict(**p, at=event["at"], actor=actor)
    elif kind == "reopen":
        s["closed"].pop((date, p["owner"]), None)
    elif kind == "print_confirm":
        s["prints"][(p["order"], p["type"])] = event["at"]
    else:
        raise DataError("Tipo de evento desconocido: " + kind)
    s["events"].append(event)
    return s


def replay(events):
    s = initial_state()
    for event in events:
        apply_event(s, copy.deepcopy(event))
    return s


def reserved(s, location, asset):
    return sum(line["qty"] * line["recipe"].get(asset, 0)
               for order in s["orders"].values() for line in order["lines"]
               if line["location"] == location and not line.get("consumed")
               and line["status"] != "Cancelado")


def available(s, location, asset):
    return s["stock"].get((location, asset), 0) - reserved(s, location, asset)


def cash_total(s, owner, date):
    return sum(x["amount"] for x in s["cash"] if (owner == "__puesto__" or x["owner"] == owner) and x["date"] == date)


def cash_owner(s, actor):
    return "__puesto__" if s.get("schema", 2) >= 3 else actor


def opening_total(s, date):
    return sum(amount for (d, _), amount in s["openings"].items() if d == date)


def operating_cash(s, actor, date):
    return cash_total(s, cash_owner(s, actor), date)


def cash_ready(s, actor, date):
    actor = cash_owner(s, actor)
    require(s.get("schema", 2) >= 3 or (date, actor) in s["openings"], "Primero registra el fondo inicial en Más → Caja general; puede ser $0.")
    require((date, actor) not in s["closed"], "La caja general está cerrada. Un administrador debe reabrirla.")


def int_amount(value, positive=False):
    require(type(value) is int and (value > 0 if positive else value >= 0), "Cantidad o importe inválido.")


def validate_event(s, event, role="operador", users=()):
    kind, p, actor = event["kind"], event["payload"], event["actor"]
    date = event["at"][:10]
    require(actor in users, "El operador ya no tiene acceso.")
    if kind in ADMIN_TYPES:
        require(role == "admin", "Esta operación requiere administrador.")
    if kind == "bootstrap":
        require(not s["initialized"], "La importación ya se realizó; no se duplicó.")
        return
    require(s["initialized"], "Primero inicializa Faro V2.")
    if kind == "inventory_v32":
        require(s.get("schema", 2) >= 3 and s.get("inventory_revision", 0) < 32 and not p, "La actualización ya está aplicada.")
        return
    if kind == "inventory_v31":
        require(s.get("schema", 2) >= 3 and s.get("inventory_revision", 0) < 31 and not p, "El ajuste de inventario ya está aplicado o no corresponde.")
        return
    if kind == "worker":
        require(isinstance(p.get("name"), str) and 0 < len(p["name"].strip()) <= 60, "Nombre inválido.")
        return
    if kind == "upgrade_v3":
        require(s.get("schema", 2) < 3, "La actualización ya está aplicada.")
        require(not p, "Actualización inválida.")
        return
    if kind == "edit_line":
        order = s["orders"].get(p["order"])
        require(order and not order.get("legacy"), "Este pedido anterior no permite recalcular importes.")
        line = next((x for x in order["lines"] if x["id"] == p["line"]), None)
        require(line and line["status"] not in {"Cancelado", "Entregado"}, "El producto ya terminó.")
        require(p["expected"] == hashlib.sha256(json_text(line).encode()).hexdigest(), "El pedido cambió. Actualiza antes de editar.")
        require(p["reason"].strip(), "Escribe el motivo del cambio.")
        int_amount(p["qty"], True)
        int_amount(p["unit"])
        require(p["unit"] >= line["base_price"], "El precio debe cubrir el precio base.")
        require(not line.get("consumed") or p["qty"] == line["qty"], "Ya se está preparando: cancela el renglón y agrega un pedido para cambiar cantidades.")
        for asset, qty in line["recipe"].items():
            require(available(s, line["location"], asset) >= max(0, p["qty"] - line["qty"]) * qty, "No alcanzan existencias para aumentar la cantidad.")
        due = datetime.fromisoformat(p["due"])
        require(due.tzinfo is not None, "Falta zona horaria.")
        require(not p["scheduled"] or due >= datetime.fromisoformat(event["at"]), "La hora agendada ya pasó.")
        return
    if kind == "balance_adjust":
        require(p["customer"] in s["customers"], "Cliente no encontrado.")
        require(type(p["amount"]) is int and p["amount"] != 0, "Escribe un ajuste distinto de cero.")
        require(p["expected"] == s["balances"].get(p["customer"], 0), "La cuenta cambió. Actualiza.")
        require(p["reason"].strip(), "Escribe el motivo del ajuste.")
        return
    if kind == "reverse_payment":
        payment = next((x for x in s["payments"] if x["id"] == p["payment"]), None)
        require(payment and not payment.get("refund"), "Cobro no encontrado.")
        require(p["payment"] not in s["reversed_payments"], "Este cobro ya se corrigió.")
        require(p["reason"].strip(), "Escribe el motivo de corrección.")
        if payment["method"] == "Efectivo":
            cash_ready(s, payment["actor"], date)
            require(operating_cash(s, payment["actor"], date) >= payment["amount"], "No alcanza el efectivo de la caja original para revertirlo hoy.")
        return
    if kind == "sale":
        require(p["id"] not in s["orders"], "Este pedido ya existe.")
        require(0 < len(p["lines"]) <= 60, "El pedido necesita entre 1 y 60 renglones.")
        require(len({x["id"] for x in p["lines"]}) == len(p["lines"]), "Hay renglones duplicados.")
        int_amount(p["paid"])
        total = 0
        needs = defaultdict(int)
        for line in p["lines"]:
            if line.get("free_item"):
                require(line["product"].startswith("extra_") and 0 < len(line["name"].strip()) <= 150,
                        "Escribe la descripción del extra.")
                require(line["recipe"] == {} and line["category"] == "Agregar extra", "El extra libre no controla inventario.")
                int_amount(line["base_price"], True)
                product = dict(id=line["product"], name=line["name"], price=line["base_price"],
                               category="Agregar extra", recipe={}, active=True)
            else:
                require(line["product"] in s["products"], "Un producto ya no existe.")
                product = s["products"][line["product"]]
            require(product["active"] and product["price"] is not None, "Un producto está desactivado o sin precio.")
            require(line["base_price"] == product["price"], "Cambió un precio. Actualiza ese producto en el pedido.")
            require(line["recipe"] == product["recipe"], "Cambió el control de existencias de un producto. Agrégalo de nuevo.")
            if s.get("schema", 2) >= 3:
                require(line["location"] == "Puesto", "Agrega de nuevo el producto para usar el inventario unificado.")
                require(not auto_kitchen(product) or (line["kitchen"] and line["status"] == "Por preparar"), "La comida debe enviarse a cocina.")
                require(line["unit"] == line["base_price"] + modifier_price(line.get("options", {}), line.get("custom_extras", [])), "El precio de las opciones cambió. Personaliza el producto de nuevo.")
            int_amount(line["qty"], True)
            int_amount(line["unit"])
            require(line["unit"] >= line["base_price"], "El precio no puede ser menor al precio base.")
            require(line["location"] in LOCATIONS, "Ubicación inválida.")
            require(line["status"] in {"Entregado", "Por preparar"}, "Estado inicial inválido.")
            due = datetime.fromisoformat(line["due"])
            require(due.tzinfo is not None, "Falta zona horaria.")
            if line.get("scheduled"):
                require(due >= datetime.fromisoformat(event["at"]) - timedelta(minutes=2),
                        "La hora programada ya pasó; corrígela antes de guardar.")
                require(line["status"] == "Por preparar", "Una entrega futura no puede marcarse entregada.")
            total += line["unit"] * line["qty"]
            for asset, qty in line["recipe"].items():
                needs[(line["location"], asset)] += qty * line["qty"]
        require(total == p["total"], "El total del pedido no coincide.")
        require(p["paid"] <= total, "El pago excede el total.")
        customer = p["customer"]
        require(customer["id"] and customer["name"].strip(), "Falta cliente.")
        require(not customer.get("anonymous") or
                (p["paid"] == total and all(x["status"] == "Entregado" for x in p["lines"])),
                "Selecciona un cliente para dejar saldo o enviar productos a cocina.")
        for (location, asset), qty in needs.items():
            require(available(s, location, asset) >= qty,
                    f"No alcanza {s['assets'][asset]['name']} en {location}. Revisa la carga o reduce la cantidad.")
        if p["paid"]:
            require(p["method"] in METHODS, "Selecciona cómo se recibió el pago.")
            if p["method"] == "Efectivo":
                int_amount(p.get("received", p["paid"]))
                require(p.get("received", p["paid"]) >= p["paid"], "El efectivo recibido no alcanza para el cobro.")
                cash_ready(s, actor, date)
    elif kind in {"payment", "refund"}:
        require(p["customer"] in s["customers"], "Cliente no encontrado.")
        int_amount(p["amount"], True)
        require(p["method"] in METHODS, "Método de pago inválido.")
        balance = s["balances"].get(p["customer"], 0)
        limit = -balance if kind == "refund" else balance
        require(p["amount"] <= limit, "El saldo cambió o el importe lo excede. Actualiza y revisa la cuenta.")
        if p["method"] == "Efectivo":
            if kind == "payment":
                int_amount(p.get("received", p["amount"]))
                require(p.get("received", p["amount"]) >= p["amount"], "El efectivo recibido no alcanza para el abono.")
            cash_ready(s, actor, date)
            if kind == "refund":
                require(operating_cash(s, actor, date) >= p["amount"], "No hay suficiente efectivo registrado en tu caja.")
    elif kind in {"line_state", "cancel_line"}:
        order = s["orders"].get(p["order"])
        require(order is not None, "Pedido no encontrado.")
        line = next((x for x in order["lines"] if x["id"] == p["line"]), None)
        require(line is not None, "Producto no encontrado.")
        require(line["status"] == p["expected"], "El estado cambió desde otro teléfono. Actualiza la pantalla.")
        require(line["status"] != "Cancelado", "El producto ya está cancelado.")
        if kind == "line_state":
            transitions = {"Por preparar": ["Preparando", "Listo"], "Preparando": ["Listo"],
                           "Listo": ["En reparto", "Entregado"], "En reparto": ["Entregado"]}
            require(p["status"] in transitions.get(line["status"], []), "Cambio de estado no permitido.")
        else:
            require(bool(p["reason"].strip()), "Escribe el motivo de cancelación.")
            require(not order.get("legacy"), "Cancela los pedidos heredados en la revisión de transición; su importe no es reconstruible.")
    elif kind in {"product", "asset"}:
        require(p["id"] and p["name"].strip(), "Falta nombre.")
        if kind == "product":
            require(role == "admin" or p["id"] not in s["products"], "Solo admin puede modificar productos existentes.")
            require(not any(canon(x["name"]) == canon(p["name"]) and x["id"] != p["id"] for x in s["products"].values()), "Ya existe un producto con ese nombre.")
            if s.get("schema", 2) >= 3:
                require(p["recipe"] == controlled_recipe(p), "Solo se controlan tortas y vasos de café o té.")
            require(p["category"].strip(), "Falta categoría.")
            if p["price"] is not None:
                int_amount(p["price"])
            require(not p["active"] or p["price"] is not None, "Configura un precio antes de activar.")
            for asset, qty in p["recipe"].items():
                require(asset in s["assets"] or (s.get("schema", 2) >= 3 and is_torta(p) and asset == p["id"]), "Insumo no encontrado.")
                int_amount(qty, True)
    elif kind == "customer":
        require(p["name"].strip(), "Escribe el nombre del cliente.")
        require(not any(canon(x["name"]) == canon(p["name"]) and x["id"] != p["id"]
                        for x in s["customers"].values() if not x.get("anonymous")),
                "Ya existe un cliente con ese nombre; selecciónalo para evitar cuentas duplicadas.")
    elif kind.startswith("stock_"):
        require(bool(p["items"]), "No hay cantidades para registrar.")
        location = p.get("source", p.get("location"))
        require(location in LOCATIONS, "Ubicación inválida.")
        if s.get("schema", 2) >= 3:
            require(location == "Puesto" and kind != "stock_move", "Ahora todas las existencias están unificadas.")
        for asset, qty in p["items"].items():
            require(asset in s["assets"], "Insumo desconocido.")
            if s.get("schema", 2) >= 3 and role != "admin":
                require(asset != "vaso_caliente" and asset in s["products"] and is_torta(s["products"][asset]),
                        "Solo el administrador puede editar los vasos. Puedes editar las tortas.")
            if kind == "stock_adjust":
                require(type(qty) is int, "El ajuste debe ser entero.")
                require(available(s, location, asset) + qty >= 0, "El ajuste dejaría existencias negativas o sin reservas.")
            else:
                int_amount(qty)
            if kind in {"stock_loss", "stock_move"}:
                require(available(s, location, asset) >= qty, "No hay suficientes existencias libres.")
            if kind == "stock_count" and p.get("adjust"):
                require(role == "admin" or (s.get("schema", 2) >= 3 and asset in s["products"] and is_torta(s["products"][asset])), "Solo admin puede ajustar ese insumo.")
                require(qty >= reserved(s, location, asset), "El conteo es menor que lo reservado: resuelve esos pedidos antes de ajustar.")
        if kind == "stock_move":
            require(p["target"] in LOCATIONS and p["target"] != p["source"], "Elige ubicaciones diferentes.")
        require(p.get("reason", "").strip(), "Escribe una referencia o motivo.")
        if kind == "stock_count":
            require(p["expected"] == {a: s["stock"].get((location, a), 0) for a in p["items"]},
                    "Hubo movimientos durante el conteo. Actualiza y vuelve a contar al terminar la venta.")
    elif kind == "opening":
        int_amount(p["amount"])
        if s.get("schema", 2) >= 3:
            require(p.get("shared") is True, "Actualiza la app para registrar el fondo general.")
            require(not any(d == date for d, _ in s["openings"]), "El fondo general de hoy ya está registrado.")
            cash_ready(s, actor, date)
        else:
            require((date, actor) not in s["openings"], "Tu fondo inicial ya está registrado hoy.")
    elif kind in {"expense", "withdraw", "transfer"}:
        int_amount(p["amount"], True)
        require(p.get("reason", "").strip(), "Escribe el concepto.")
        if kind == "transfer":
            require(s.get("schema", 2) < 3, "La caja es general: no se necesitan traspasos entre usuarios.")
            require(p["target"] in users and p["target"] != actor, "Elige otro operador.")
        else:
            require(p["method"] in METHODS, "Método inválido.")
        if kind == "transfer" or p["method"] == "Efectivo":
            cash_ready(s, actor, date)
            require(operating_cash(s, actor, date) >= p["amount"], "El movimiento supera tu efectivo registrado.")
    elif kind == "transfer_accept":
        transfer = s["transfers"].get(p["transfer"])
        require(transfer and transfer["target"] == actor and transfer["status"] == "Pendiente", "Traspaso no disponible para ti.")
        cash_ready(s, actor, date)
    elif kind == "close":
        actor = p.get("owner", actor)
        require(actor in users or actor == "__puesto__", "Responsable no encontrado.")
        require(s.get("schema", 2) < 3 or actor == "__puesto__", "Haz el corte de la caja general.")
        cash_ready(s, actor, date)
        int_amount(p["counted"])
        require(p["expected"] == cash_total(s, actor, date), "Tu caja cambió mientras contabas. Actualiza antes de cerrar.")
        require(not any(t["status"] == "Pendiente" and (actor == "__puesto__" or t["sender"] == actor or t["target"] == actor)
                        for t in s["transfers"].values()), "Confirma los traspasos pendientes antes del corte.")
    elif kind == "reopen":
        require((date, p["owner"]) in s["closed"], "Esa caja no está cerrada hoy.")
        require(p["reason"].strip(), "Escribe el motivo de reapertura.")
    elif kind == "print_confirm":
        require(p["order"] in s["orders"] and p["type"] in {"venta", "cocina"}, "Ticket no encontrado.")
    else:
        raise RuleError("Operación desconocida.")


def event_rows(event):
    data = json_text(event["payload"])
    chunks = [data[i:i + 28000] for i in range(0, len(data), 28000)]
    digest = hashlib.sha256(data.encode()).hexdigest()
    require(len(chunks) <= 40, "El movimiento es demasiado grande para importarlo de una vez.")
    return [[event["id"], event["at"], event["actor"], event["kind"], str(i + 1),
             str(len(chunks)), chunk, digest] for i, chunk in enumerate(chunks)]


def decode_rows(rows):
    if not rows:
        return []
    if rows[0] != HEADERS:
        raise DataError("La estructura de FARO_V2_EVENTOS no coincide. No edites esa pestaña manualmente.")
    grouped, result = {}, []
    for row in rows[1:]:
        if not any(row):
            continue
        if len(row) != 8:
            raise DataError("Hay un evento incompleto; revisa el respaldo antes de seguir.")
        eid, at, actor, kind, part, total, data, checksum = row
        try:
            part, total = int(part), int(total)
            assert 1 <= part <= total <= 40
        except (ValueError, AssertionError):
            raise DataError("Partes inválidas en el registro.") from None
        meta = (at, actor, kind, total, checksum)
        if eid not in grouped:
            grouped[eid] = [meta, {}]
        old_meta, parts = grouped[eid]
        if meta != old_meta or (part in parts and parts[part] != data):
            raise DataError("Dos registros con el mismo folio contienen información diferente.")
        parts[part] = data  # Reintentos idénticos cuentan una sola vez.
    for eid, (meta, parts) in grouped.items():
        at, actor, kind, total, checksum = meta
        if set(parts) != set(range(1, total + 1)):
            raise DataError("Movimiento incompleto. No se calcularán saldos con información parcial.")
        text = "".join(parts[i] for i in range(1, total + 1))
        if hashlib.sha256(text.encode()).hexdigest() != checksum:
            raise DataError("Un movimiento fue modificado fuera de la aplicación.")
        try:
            payload = json.loads(text)
            datetime.fromisoformat(at)
        except (ValueError, TypeError):
            raise DataError("Datos de un movimiento inválidos.") from None
        result.append(dict(id=eid, at=at, actor=actor, kind=kind, payload=payload))
    return result


class Store:
    """Serializa escrituras entre sesiones de UNA instancia de Streamlit.

    La caché es compartida, dura 5 s y solo sirve para pantallas. Cada escritura
    lee el registro fresco bajo el mismo bloqueo y se confirma con un único
    appendCells atómico. Sheets no ofrece un bloqueo entre distintos servidores.
    """
    def __init__(self, book=None):
        self.book = book
        self.lock = threading.RLock()
        self.worksheet = None
        self.memory = []
        self.cached = None
        self.cached_at = 0.0
        self.login_attempts = {}
        self.uncertain = None

    def read(self, fresh=False):
        with self.lock:
            if self.cached is not None and not fresh and time.monotonic() - self.cached_at < 5:
                return copy.deepcopy(self.cached)
            if self.book is None:
                events = copy.deepcopy(self.memory)
            else:
                if self.worksheet is None:
                    try:
                        self.worksheet = self.book.worksheet(LOG_SHEET)
                    except gspread.WorksheetNotFound:
                        return initial_state()
                events = decode_rows(self.worksheet.get_all_values())
            state = replay(events)
            self.cached = state
            self.cached_at = time.monotonic()
            return copy.deepcopy(state)

    def ensure_sheet(self):
        if self.book is None or self.worksheet is not None:
            return
        try:
            self.worksheet = self.book.worksheet(LOG_SHEET)
        except gspread.WorksheetNotFound:
            sheet_id = uuid.uuid4().int % 2000000000
            self.book.batch_update({"requests": [
                {"addSheet": {"properties": {"sheetId": sheet_id, "title": LOG_SHEET,
                 "gridProperties": {"rowCount": 1000, "columnCount": 8, "frozenRowCount": 1}}}},
                {"appendCells": {"sheetId": sheet_id, "rows": [{"values": [
                    {"userEnteredValue": {"stringValue": h}} for h in HEADERS]}], "fields": "userEnteredValue"}}
            ]})
            self.worksheet = self.book.worksheet(LOG_SHEET)

    def check_event(self, event):
        """Consulta fresca sin escribir: verifica la referencia y su contenido."""
        with self.lock:
            state = self.read(fresh=True)
            existing = next((x for x in state["events"] if x["id"] == event["id"]), None)
            if not existing:
                return False
            require(json_text(existing) == json_text(event), "La referencia guardada contiene otro movimiento.")
            if self.uncertain and self.uncertain["id"] == event["id"]:
                self.uncertain = None
            return True

    def commit(self, event, role, users):
        with self.lock:
            state = self.read(fresh=True)
            existing = next((x for x in state["events"] if x["id"] == event["id"]), None)
            if existing:
                require(json_text(existing) == json_text(event), "El identificador corresponde a otro movimiento.")
                if self.uncertain and self.uncertain["id"] == event["id"]:
                    self.uncertain = None
                return state
            require(not self.uncertain or self.uncertain["id"] == event["id"],
                    "Hay un movimiento por confirmar. Resuélvelo antes de registrar otro.")
            validate_event(state, event, role, users)
            # Precalcular antes de escribir; un fallo del reductor no deja un evento imposible de leer.
            candidate = apply_event(copy.deepcopy(state), copy.deepcopy(event))
            if self.book is None:
                self.memory.append(copy.deepcopy(event))
            else:
                self.ensure_sheet()
                rows = event_rows(event)
                # RAW stringValue: nombres que empiezan con '=' nunca se convierten en fórmulas.
                body = {"requests": [{"appendCells": {"sheetId": self.worksheet.id,
                    "rows": [{"values": [{"userEnteredValue": {"stringValue": str(v)}} for v in row]} for row in rows],
                    "fields": "userEnteredValue"}}]}
                try:
                    self.uncertain = copy.deepcopy(event)
                    self.book.batch_update(body)
                except Exception:
                    self.cached = None  # Resultado incierto: el próximo intento consultará el mismo ID.
                    raise
                self.uncertain = None
            self.cached, self.cached_at = candidate, time.monotonic()
            return copy.deepcopy(candidate)


def refresh_store_runtime(store):
    """Refresh cached code without replacing the shared lock or pending write."""
    with store.lock:
        # cache_resource retains instances when only Store/reducers change.
        # Reuse its data/connection/uncertain event, but bind the current methods.
        if type(store) is not Store:
            store.__class__ = Store
        if getattr(store, "runtime_revision", None) != "3.4":
            store.cached = None
            store.cached_at = 0.0
            store.runtime_revision = "3.4"
    return store


@st.cache_resource
def get_store():
    if os.environ.get("FARO_DEMO") == "1":
        return Store()
    raw = st.secrets.get("google_credentials", st.secrets.get("gcp_service_account"))
    if not raw:
        raise RuleError("Falta google_credentials en los Secrets de Streamlit.")
    credentials = json.loads(raw, strict=False) if isinstance(raw, str) else dict(raw)
    client = gspread.service_account_from_dict(credentials)
    client.set_timeout(25)
    spreadsheet_id = st.secrets.get("spreadsheet_id", "")
    book = client.open_by_key(spreadsheet_id) if spreadsheet_id else client.open("Base_POS")
    return Store(book)


def configured_users(settings):
    users = {str(k): dict(v) for k, v in settings.get("usuarios", {}).items()}
    for user in users.values():
        user["rol"] = canon(user.get("rol", "operador"))
    # An explicit root admin_password is authoritative, even if usuarios.admin exists.
    if settings.get("admin_password"):
        users["admin"] = dict(users.get("admin", {}), nombre=users.get("admin", {}).get("nombre", "Administrador"),
                              pin=str(settings["admin_password"]), rol="admin")
    require(users and any(x.get("rol") == "admin" for x in users.values()),
            "Configura admin_password o un administrador en [usuarios] dentro de Secrets.")
    for username, user in users.items():
        require(user.get("rol") in {"admin", "operador"}, f"Revisa el rol de {username} en Secrets.")
        if user.get("rol") == "admin":
            require(len(str(user.get("pin", ""))) >= 6, f"La clave de {username} necesita al menos 6 caracteres.")
    return users


def credentials_config():
    if os.environ.get("FARO_DEMO") == "1":
        return {"demo": {"nombre": "Demostración", "pin": "solo-demo", "rol": "admin"},
                "cocina": {"nombre": "Cocina demo", "pin": "solo-demo", "rol": "operador"}}
    return configured_users(st.secrets)


def seed_catalog():
    """Precios del archivo recibido. Los productos nuevos quedan pendientes de precio."""
    products, assets = [], {}
    def add(cat, name, price, portable=False, stock=False, cup=None, active=True):
        pid = "p_" + hashlib.sha256((cat + "|" + name).encode()).hexdigest()[:16]
        recipe = {}
        if stock:
            assets[pid] = dict(id=pid, name=name, unit="pieza")
            recipe[pid] = 1
        if cup:
            assets[cup] = dict(id=cup, name="Vaso caliente" if cup == "vaso_caliente" else "Vaso frío", unit="pieza")
            recipe[cup] = 1
        products.append(dict(id=pid, name=name, category=cat, price=cents(price) if price is not None else None,
                             active=active, portable=portable, recipe=recipe))
    for flavor, price in {"Vainilla":25, "Avellana":25, "Clásico":25, "Crema irlandesa":30,
                          "Caramelo":30, "Canela":30, "Té":25}.items():
        name = "Café americano" if flavor == "Clásico" else ("Té" if flavor == "Té" else "Café " + flavor)
        add("Café caliente", name, price, True, cup="vaso_caliente")
    add("Café caliente", "Café con crema", None, True, cup="vaso_caliente", active=False)
    for name, price in {"Ensalada":75, "Sandwich":65, "Plato de Chilaquiles":55, "Torta de Chilaquiles":45}.items():
        add("Comida", name, price)
    for name, price in {"Pan de Dulce":25, "Telera":5}.items():
        add("Panadería", name, price, True, stock=True)
    for name in ["Torta de jamón", "Torta de queso de puerco", "Torta de milanesa de pollo", "Cuernito preparado"]:
        add("Tortas y cuernitos", name, None, True, stock=True, active=False)
    flavors = ["Fresa", "Taro", "Chai", "Matcha", "Rompope", "Red Velvet", "Pistache", "Galleta",
               "Mora", "Cereza", "Refresher Darks", "Cafe", "Moka", "Oreo", "Chocolate"]
    for cat, price, prefix in [("Bebidas Frías",45,"Bebida fría de "), ("Frappés",65,"Frappé de "), ("Esquimos",45,"Esquimo de ")]:
        for flavor in flavors:
            add(cat, prefix + flavor, price, cup="vaso_frio")
    for flavor in ["Fresa", "Mango", "Temporada"]:
        add("Chamoyadas", "Chamoyada de " + flavor, 65, cup="vaso_frio")
    for flavor in ["Fresa", "Cherry negra", "Guayaba", "Kiwi"]:
        add("Refreshers", "Refresher de " + flavor, 55, cup="vaso_frio")
    for name in ["Smoothie (define sabor)", "Muffin", "Galleta de panadería", "Strudel de manzana"]:
        cat = "Smoothies" if "Smoothie" in name else "Panadería"
        add(cat, name, None, cat == "Panadería", stock=cat == "Panadería", cup="vaso_frio" if cat == "Smoothies" else None, active=False)
    return products, list(assets.values())


def legacy_product_name(cat, base):
    if cat == "Café":
        return "Café caliente", "Café americano" if base in {"Clásico", "Café Clásico"} else ("Té" if base in {"Te", "Té"} else base if "Café" in base else "Café " + base)
    names = {"Frappés": "Frappé de ", "Esquimos": "Esquimo de ", "Bebidas Frías": "Bebida fría de ",
             "Chamoyadas": "Chamoyada de ", "Refreshers": "Refresher de "}
    return ("Comida" if cat == "Platillos" else "Tortas y cuernitos" if "Tortas" in cat else cat,
            (names[cat] + base) if cat in names and not canon(base).startswith(canon(names[cat])) else base)


def legacy_preview(book):
    products, assets_list = seed_catalog()
    products = {x["id"]: x for x in products}
    assets = {x["id"]: x for x in assets_list}
    customers, balances, stock, warnings, pending = {}, defaultdict(int), [], [], []
    def read_sheet(name):
        if book is None:
            return []
        try:
            return book.worksheet(name).get_all_values()
        except gspread.WorksheetNotFound:
            return []
    def client(name):
        name = " ".join(name.split())
        key = canon(name)
        cid = "c_" + hashlib.sha256(key.encode()).hexdigest()[:20]
        customers.setdefault(cid, dict(id=cid, name=name, reference="", anonymous=False))
        return cid
    seen_products = set()
    for n, row in enumerate(read_sheet("Inventario")[1:], 2):
        if not any(row):
            continue
        require(len(row) >= 4 and row[0].strip(), f"Inventario, fila {n}: faltan nombre, categoría o precio.")
        name, cat = row[0].strip(), row[2].strip()
        cat, name = legacy_product_name(cat, name)
        key = (canon(cat), canon(name))
        require(key not in seen_products, f"Inventario duplicado: {cat} / {name}. Corrígelo antes de importar.")
        seen_products.add(key)
        product = next((x for x in products.values() if (canon(x["category"]), canon(x["name"])) == key), None)
        if not product:
            pid = "p_" + hashlib.sha256((cat + "|" + name).encode()).hexdigest()[:16]
            portable = cat in {"Tortas y cuernitos", "Panadería", "Café caliente"}
            product = dict(id=pid, name=name, category=cat, portable=portable, recipe={})
            if cat in {"Tortas y cuernitos", "Panadería"}:
                product["recipe"] = {pid: 1}
                assets[pid] = dict(id=pid, name=name, unit="pieza")
            elif cat in {"Café caliente", "Bebidas Frías", "Frappés", "Esquimos", "Chamoyadas", "Refreshers", "Smoothies"}:
                product["recipe"] = {"vaso_caliente" if cat == "Café caliente" else "vaso_frio": 1}
            products[pid] = product
        price = cents(row[3])
        require(price >= 0, f"Precio negativo en Inventario, fila {n}.")
        product.update(price=price, active=len(row) < 5 or canon(row[4]) == "activo")
        if cat in {"Tortas y cuernitos", "Panadería"} and row[1].strip():
            try:
                qty = int(row[1])
            except ValueError:
                raise RuleError(f"Existencias inválidas en Inventario, fila {n}.") from None
            require(qty >= 0, f"Existencias negativas en Inventario, fila {n}. Cuenta y corrige primero.")
            stock.append(dict(location="Puesto", asset=product["id"], qty=qty))
    for n, row in enumerate(read_sheet("Deudas")[1:], 2):
        if not any(row):
            continue
        require(len(row) >= 3 and row[0].strip(), f"Deudas, fila {n}: faltan datos.")
        require(row[1] in {"Deuda", "Abono"}, f"Deudas, fila {n}: tipo desconocido {row[1]}.")
        amount = cents(row[2])
        require(amount >= 0, f"Deudas, fila {n}: importe negativo.")
        balances[client(row[0])] += amount if row[1] == "Deuda" else -amount
    for n, row in enumerate(read_sheet("Operaciones")[1:], 2):
        if not any(row):
            continue
        if row[0].strip() and row[0] != "Mostrador":
            client(row[0])
        if len(row) >= 9 and row[6] in {"Pendiente", "Preparando", "Listo"}:
            cid = client(row[0].strip() or f"Revisar pedido anterior fila {n}")
            try:
                due = datetime.strptime(row[8] + " " + row[5], "%d/%m/%Y %H:%M").replace(tzinfo=TZ)
            except ValueError:
                try:
                    due = datetime.strptime(row[8], "%d/%m/%Y").replace(tzinfo=TZ, hour=12)
                except ValueError:
                    raise RuleError(f"Operaciones fila {n}: corrige fecha/hora antes de importar.") from None
                warnings.append(f"Fila {n}: hora no válida; revisar antes de iniciar, se propone 12:00.")
            oid = "legacy_" + str(n)
            pending.append(dict(id=oid, folio="ANT-" + str(n), customer=customers[cid], at=now().isoformat(),
                actor="Importación", total=0, paid=0, method="", legacy=True, lines=[dict(id=oid, product="legacy",
                name=row[1], qty=1, unit=0, base_price=0, recipe={}, location="Puesto", kitchen=True,
                notes=row[3].replace("[IMP]", "").strip(), due=due.isoformat(), scheduled=row[4] != "Inmediato",
                status="Por preparar" if row[6] == "Pendiente" else row[6], consumed=True)]))
    warnings.append("Las existencias antiguas se asignan al Puesto. Cuenta vasos y pan; luego registra la carga al Carrito.")
    warnings.append("Los pedidos anteriores activos se importan solo para preparación/entrega. No generan otro cargo ni consumo; sus saldos vienen de Deudas.")
    return dict(products=list(products.values()), assets=list(assets.values()), customers=list(customers.values()),
                balances=dict(balances), stock=stock, legacy_orders=pending, warnings=warnings)


# ---------------------------------------------------------------------------
# Interfaz móvil. No se guardan contraseñas ni credenciales en los eventos.
# ---------------------------------------------------------------------------
CSS = """
<style>
.stApp {--primary-color:#155b46;--st-primary-color:#155b46;background:#f5f7f6;color:#183a31;}
.block-container {max-width:1100px;padding-top:3.5rem;padding-bottom:4rem;}
html, body, [data-testid="stMarkdownContainer"] p {font-size:17px;}
h1 {font-size:1.85rem!important;letter-spacing:-.04em;}
h2 {font-size:1.45rem!important;} h3 {font-size:1.2rem!important;}
.stButton button,.stDownloadButton button,.stLinkButton a,.stFormSubmitButton button,
[data-testid="stBaseButton-segmented_control"], [data-testid="stBaseButton-segmented_controlActive"] {
 min-height:58px!important;border-radius:12px!important;font-size:17px!important;
 font-weight:600!important;white-space:pre-wrap!important;padding:12px 16px!important;}
button p {font-size:17px!important;}
button:hover {border-color:#267b60!important;color:#155b46!important;}
button[kind="primary"]:hover {background:#104735!important;color:white!important;}
[aria-checked="true"], [aria-selected="true"] {accent-color:#155b46!important;}
[data-baseweb="tab-highlight"], [data-baseweb="slider"] [role="slider"] {background:#155b46!important;}
[data-testid="stAlert"] {background:#e4f0e9!important;color:#183a31!important;border-color:#80aa95!important;}
[data-testid="stAlert"] svg {color:#155b46!important;}
input {accent-color:#155b46;}
[data-baseweb="input"]:focus-within {border-color:#155b46!important;}

div[data-baseweb="input"],div[data-baseweb="select"] > div {min-height:52px;font-size:17px;}
button[kind="primary"] {background:#155b46;border-color:#155b46;color:white;}
[data-testid="stVerticalBlockBorderWrapper"] > div {border-radius:16px;}
[data-testid="stMetricValue"] {font-size:2rem;color:#155b46;}
.faro-account {background:#fff;border:1px solid #d9e3dd;border-left:5px solid #267b60;
 padding:20px;border-radius:14px;margin:8px 0 14px;}
.faro-account h3 {margin:0 0 5px;font-size:1.3rem!important;color:#163f32;}
.faro-account .amount {font-size:2.3rem;font-weight:750;color:#155b46;line-height:1.2;margin:8px 0;}
.faro-muted {color:#526b60;font-size:15px;}
[data-testid="stButtonGroup"] button {min-height:54px!important;border-radius:10px!important;padding:10px 16px!important;}
[data-testid="stButtonGroup"] {gap:8px;}
[data-testid="stBaseButton-segmented_controlActive"] {
 background:#e0eee7!important;border-color:#155b46!important;color:#155b46!important;}
[class*="st-key-stock_row_"] [data-testid="stHorizontalBlock"] {flex-wrap:nowrap!important;}
[class*="st-key-stock_row_"] [data-testid="stColumn"] {min-width:0!important;}
@media(max-width:640px) {
 .block-container {padding-left:1rem;padding-right:1rem;padding-top:3rem;}
 [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] .stButton) {flex-wrap:nowrap;gap:8px;}
 [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] .stButton) > [data-testid="stColumn"] {min-width:0;flex:1 1 0%;}
 .stButton button {padding:10px!important;}
}
</style>
"""


def choice(label, options, index=0, horizontal=False, key=None, label_visibility="visible", **kwargs):
    """Opciones táctiles nativas: sin círculos y sin selección vacía."""
    default = None if key and key in st.session_state else options[index]
    return st.segmented_control(label, options, default=default, required=True,
                                key=key, label_visibility=label_visibility, width="stretch",
                                persist_state=kwargs.pop("persist_state", "session" if key else None), **kwargs)


def account_card(customer, balance):
    name = html.escape(customer["name"])
    reference = html.escape(customer.get("reference", "") or "Sin referencia de ubicación")
    label = "Pendiente de pagar" if balance > 0 else "Saldo a favor" if balance < 0 else "Cuenta liquidada"
    st.markdown(f'<div class="faro-account"><h3>{name}</h3><div class="faro-muted">{reference}</div>'
                f'<div class="amount">{money(abs(balance))}</div><div>{label}</div></div>', unsafe_allow_html=True)


def cash_prompt(s, user, key):
    today = (day(), cash_owner(s, user["id"]))
    if (s.get("schema", 2) >= 3 or today in s["openings"]) and today not in s["closed"]:
        return True
    st.info("Abre tu caja del día antes de recibir efectivo; el fondo puede ser $0." if today not in s["openings"]
            else "Tu caja está cerrada. Solicita al administrador que la reabra antes de recibir efectivo.")
    if st.button("Ir a caja general", key=key, width="stretch"):
        st.session_state.nav_next = "Más"
        st.session_state.more_page = "Caja general"
        st.rerun()
    return False



def safe_state(store, fresh=False):
    try:
        return store.read(fresh)
    except DataError as exc:
        st.error(str(exc))
    except Exception as exc:
        logging.error("Faro lectura: %s", type(exc).__name__)
        st.error("No se pudo actualizar Google Sheets. No se mostrarán saldos como si fueran cero. Revisa la conexión y vuelve a intentar.")
    if st.button("Volver a consultar", key="retry_read"):
        st.rerun()
    st.stop()



def is_torta(product):
    return "chilaquiles" not in sort_key(product["name"]) and (
        "torta" in sort_key(product["name"]) or "torta" in sort_key(product.get("category", "")))


def controlled_recipe(product):
    name, category = sort_key(product["name"]), sort_key(product.get("category", ""))
    if is_torta(product):
        return {product.get("product", product["id"]): 1}
    if "cafe caliente" in category or category in {"cafe", "te", "tes"} or name == "te" or name.startswith("te de "):
        return {"vaso_caliente": 1}
    return {}


CATEGORY_ORDER = ["Café", "Tortas", "Comida", "Panadería", "Frappés", "Refresher", "Chamoyadas", "Esquimos"]


def category_label(value):
    key = sort_key(value).strip()
    for words, label in [("cafe", "Café"), ("torta", "Tortas"), ("comida", "Comida"),
                         ("panader", "Panadería"), ("frappe", "Frappés"), ("refresher", "Refresher"),
                         ("chamoyada", "Chamoyadas"), ("esquimo", "Esquimos")]:
        if words in key:
            return label
    return value


def ordered_categories(products):
    labels = {category_label(p["category"]) for p in products}
    return [c for c in CATEGORY_ORDER if c in labels] + sorted(labels - set(CATEGORY_ORDER), key=sort_key) + ["Agregar extra"]


def auto_kitchen(product):
    name = sort_key(product["name"])
    return any(word in name for word in ("chilaquiles", "ensalada", "sandwich", "sand wich", "sandwiche"))


def kitchen_upcoming(line, moment):
    return bool(line.get("scheduled") and line["status"] == "Por preparar" and
                datetime.fromisoformat(line["due"]) > moment + timedelta(minutes=15))


def modifier_price(options, extras):
    amount = 1000 if "Almendra" in options.get("milk", "") else 0
    amount += 1000 if options.get("pearls") else 0
    amount += 1000 if options.get("protein") == "Pechuga a la plancha (+$10)" else 0
    for extra in extras:
        require(extra["name"].strip(), "Escribe qué extra estás cobrando.")
        int_amount(extra["price"])
        amount += extra["price"]
    return amount


@st.cache_resource
def daily_memory_component():
    return st.components.v2.component("faro_daily_name", js="""
export default function(component) {
  const {data, setStateValue} = component;
  const key = 'faro-name-v3:' + window.location.pathname;
  let saved = null;
  try {
    if (data.clear) localStorage.removeItem(key);
    if (data.save) localStorage.setItem(key, JSON.stringify(data.save));
    saved = JSON.parse(localStorage.getItem(key) || 'null');
  } catch (_) { /* Private browsers can disable storage. Session still works. */ }
  if (!saved || saved.day !== data.day) saved = null;
  setStateValue('saved', saved);
}
""")


def worker_identity(name, users):
    name = " ".join(name.split())[:60]
    require(bool(name) and name != "/admin", "Escribe tu nombre.")
    key = next((k for k, v in users.items() if canon(name) in {canon(k), canon(v.get("nombre", k))}), None)
    key = key or "persona_" + hashlib.sha256(canon(name).encode()).hexdigest()[:16]
    users.setdefault(key, dict(nombre=name, rol="operador"))
    return dict(id=key, name=users[key].get("nombre", name), day=day())


def admin_access(store, users):
    with st.sidebar.expander("Administración"):
        if st.session_state.get("admin_auth"):
            st.success("Modo administrador activo")
            if st.button("Salir de administración", width="stretch"):
                st.session_state.pop("admin_auth", None)
                st.rerun()
        else:
            with st.form("admin_access"):
                command = st.text_input("Comando", value="/admin", key="admin_command")
                password = st.text_input("Contraseña de administrador", type="password", key="admin_password_input")
                submitted = st.form_submit_button("Entrar a administración", width="stretch")
            if submitted:
                with store.lock:
                    attempt_key = "admin-v31:" + st.session_state.setdefault("admin_attempt_id", uid())
                    attempts, until = store.login_attempts.get(attempt_key, (0, 0))
                    if until > time.monotonic():
                        st.error("Espera un minuto antes de intentar de nuevo.")
                    else:
                        admin = next((k for k, v in users.items() if v.get("rol") == "admin" and
                            hmac.compare_digest(password.encode(), str(v.get("pin", "")).encode())), None)
                        if canon(command) == "/admin" and admin:
                            store.login_attempts.pop(attempt_key, None)
                            st.session_state.admin_auth = dict(id=admin, fingerprint=hashlib.sha256(password.encode()).hexdigest(), day=day())
                            st.rerun()
                        else:
                            attempts += 1
                            store.login_attempts[attempt_key] = (attempts, time.monotonic() + 60 if attempts >= 5 else 0)
                            st.error("Escribe /admin en Comando." if canon(command) != "/admin" else
                                "La contraseña no coincide con la configurada en Secrets. No es el nombre con el que entraste.")
                            st.caption("Si tienes admin_password en la raíz de Secrets, usa esa contraseña. También se aceptan los PIN de usuarios con rol admin. No compartas aquí tu contraseña.")


def login(store, users):
    current = st.session_state.get("auth")
    if current and current.get("day") != day():
        st.session_state.pop("auth", None)
        st.session_state.pop("admin_auth", None)
        current = None
    memory = daily_memory_component()(data=dict(day=day(), save=current, clear=st.session_state.get("forget_name", False)),
        key="daily_name", on_saved_change=lambda: None)
    saved = memory.saved
    if not current and not st.session_state.get("forget_name") and isinstance(saved, dict) and saved.get("day") == day() and isinstance(saved.get("name"), str):
        current = worker_identity(saved["name"], users)
        st.session_state.auth = current
    if not current:
        st.title("☕ Faro Café")
        st.write("¿Quién trabaja hoy?")
        with st.form("login"):
            name = st.text_input("Tu nombre", max_chars=60, autocomplete="given-name")
            submitted = st.form_submit_button("Comenzar", type="primary", width="stretch")
        st.caption("Lo recordamos durante el día en este navegador. No necesitas contraseña para vender.")
        if submitted:
            try:
                st.session_state.auth = worker_identity(name, users)
                st.session_state.pop("forget_name", None)
                st.rerun()
            except RuleError as exc:
                st.error(str(exc))
        st.stop()
    users.setdefault(current["id"], dict(nombre=current["name"], rol="operador"))
    admin = st.session_state.get("admin_auth", {})
    expected = users.get(admin.get("id"), {})
    authorized = (admin.get("day") == day() and expected.get("rol") == "admin" and
        hmac.compare_digest(admin.get("fingerprint", ""), hashlib.sha256(str(expected.get("pin", "")).encode()).hexdigest()))
    if admin and not authorized:
        st.session_state.pop("admin_auth", None)
    admin_access(store, users)
    return dict(id=current["id"], name=current["name"], role="admin" if authorized else "operador")

def finish_action(event):
    if event["kind"] in {"stock_load", "stock_count"}:
        st.session_state.stock_form_version = st.session_state.get("stock_form_version", 0) + 1
    if event["kind"] == "sale":
        if st.session_state.get("auth", {}).get("id") == event["actor"]:
            st.session_state.cart = []
            st.session_state.pop("editing", None)
        st.session_state.last_order = event["payload"]["id"]
        st.session_state.checkout_version = st.session_state.get("checkout_version", 0) + 1
    if event["kind"] == "sale":
        st.session_state.sale_stage_next = "Productos"
    if event["kind"] in {"sale", "payment"} and event["payload"].get("method") == "Efectivo":
        p = event["payload"]
        amount = p["paid"] if event["kind"] == "sale" else p["amount"]
        if amount:
            st.session_state.change_notice = money(p.get("received", amount) - amount)
    if event["kind"] == "bootstrap":
        st.session_state.pop("legacy_preview", None)
    st.session_state.pop("outbox", None)
    st.session_state.pop("pending_error", None)
    st.session_state.pop("rejected_pending", None)
    st.session_state.notice = "Guardado correctamente"


def send(store, user, users, kind, payload):
    event = dict(id=uid(), at=now().isoformat(), actor=user["id"], kind=kind, payload=copy.deepcopy(payload))
    st.session_state.outbox = event
    try:
        store.commit(event, user["role"], users)
    except RuleError as exc:
        logging.warning("Faro rechazado %s: %s", event["id"], str(exc))
        st.session_state.pop("outbox", None)
        st.error(str(exc))
        return False
    except Exception as exc:
        if is_app_error(exc, RuleError):
            logging.warning("Faro rechazado %s: %s", event["id"], str(exc))
            st.session_state.pop("outbox", None)
            st.error(str(exc))
            return False
        logging.error("Faro escritura %s: %s", event["id"], type(exc).__name__)
        st.session_state.pending_error = connection_message(exc)
        st.rerun()
    finish_action(event)
    st.rerun()


def connection_message(exc):
    """Diagnóstico sin mostrar respuestas que puedan contener credenciales."""
    if is_app_error(exc, DataError) or is_app_error(exc, RuleError):
        return str(exc)
    code = getattr(getattr(exc, "response", None), "status_code", None)
    messages = {
        400: "Google rechazó el formato del movimiento (400). Conserva el comprobante para revisar este caso.",
        401: "Google no pudo validar la cuenta de servicio (401). Revisa las credenciales de la aplicación.",
        403: "Google no permite guardar (403). Revisa el permiso de editor de la cuenta de servicio y que la API esté habilitada.",
        404: "No se encontró la hoja configurada (404). Revisa el documento y sus permisos.",
        429: "Google alcanzó su límite temporal de solicitudes (429). Espera un minuto antes de consultar de nuevo.",
    }
    if code in messages:
        return messages[code]
    if isinstance(code, int) and code >= 500:
        return "Google está respondiendo con un error temporal. Consulta de nuevo en unos momentos."
    if isinstance(exc, (KeyError, TypeError, AttributeError, ValueError)):
        return "Error interno del programa (" + type(exc).__name__ + "). Conserva el comprobante; no es un diagnóstico de conexión."
    return "No se pudo confirmar el guardado (" + type(exc).__name__ + "). Conserva el comprobante para revisar la causa."


def restore_pending(store, user):
    with st.sidebar.expander("Recuperar un movimiento pendiente"):
        st.caption("Abre el comprobante de esta misma base si reiniciaste o actualizaste la aplicación.")
        file = st.file_uploader("Comprobante de recuperación", type=["json"], key="recovery_file")
        confirmed = st.checkbox("El comprobante corresponde a esta base", key="recovery_base")
        if st.button("Abrir comprobante", disabled=not file or not confirmed, width="stretch"):
            try:
                require(file.size <= 2_000_000, "El comprobante es demasiado grande.")
                event = json.loads(file.getvalue())
                require(isinstance(event, dict) and all(k in event for k in ("id", "at", "actor", "kind", "payload")), "Comprobante incompleto.")
                require(all(isinstance(event[k], str) and event[k] for k in ("id", "at", "actor", "kind")) and isinstance(event["payload"], dict), "Formato de comprobante inválido.")
                datetime.fromisoformat(event["at"])
                require(user["role"] == "admin" or user["id"] == event["actor"], "Solo el operador original o un administrador puede recuperarlo.")
                event = {k: event[k] for k in ("id", "at", "actor", "kind", "payload")}
                event_rows(event)
                with store.lock:
                    other = st.session_state.get("outbox") or store.uncertain
                    require(not other or json_text(other) == json_text(event), "Primero resuelve el movimiento que ya está pendiente.")
                    st.session_state.outbox = event
                st.rerun()
            except (ValueError, TypeError, RuleError) as exc:
                st.error(str(exc) if isinstance(exc, RuleError) else "El archivo no es un comprobante válido.")


def pending_action(store, user, users):
    event = st.session_state.get("outbox") or store.uncertain
    if not event:
        return
    st.title("Confirmar el último guardado")
    st.warning("Falta confirmar este movimiento. Conservamos su referencia para evitar duplicarlo.")
    labels = {"bootstrap": "Importación inicial", "sale": "Venta", "payment": "Cobro"}
    st.subheader(labels.get(event["kind"], "Movimiento"))
    st.caption("Referencia: " + event["id"][:8].upper() + " · operador: " + event["actor"])
    if st.session_state.get("pending_error"):
        st.error(st.session_state.pending_error)
    st.write("Primero consulta si Google ya lo guardó. Esta consulta no envía otro movimiento.")
    if user["id"] == event["actor"] or user["role"] == "admin":
        if st.button("Consultar si ya se guardó", type="primary", width="stretch"):
            try:
                with st.spinner("Consultando Google Sheets…"):
                    saved = store.check_event(event)
                if saved:
                    finish_action(event)
                    st.rerun()
                st.info("La consulta terminó: esta referencia aún no aparece guardada. Puedes reintentar el mismo movimiento.")
                st.session_state.pop("pending_error", None)
            except Exception as exc:
                st.session_state.pending_error = connection_message(exc)
                st.error(st.session_state.pending_error)
        if st.button("Reintentar el mismo movimiento", width="stretch"):
            try:
                role = users.get(event["actor"], {}).get("rol", "operador")
                with st.spinner("Verificando y guardando con la misma referencia…"):
                    store.commit(event, role, users)
            except RuleError as exc:
                st.session_state.pending_error = str(exc)
                st.session_state.rejected_pending = event["id"]
                st.error(str(exc))
            except Exception as exc:
                st.session_state.pending_error = connection_message(exc)
                if is_app_error(exc, RuleError):
                    st.session_state.rejected_pending = event["id"]
                st.error(st.session_state.pending_error)
            else:
                finish_action(event)
                st.rerun()
        if st.session_state.get("rejected_pending") == event["id"]:
            if st.button("Volver a revisar el movimiento rechazado", width="stretch"):
                try:
                    # Nunca libera un resultado incierto ni una referencia distinta.
                    with store.lock:
                        if store.check_event(event):
                            finish_action(event)
                            st.rerun()
                        require(not store.uncertain, "Todavía hay un envío pendiente de confirmación; conserva el comprobante.")
                        if event["kind"] == "sale":
                            require(user["id"] == event["actor"], "El operador original debe revisar su pedido.")
                            p = event["payload"]
                            st.session_state.cart = copy.deepcopy(p["lines"])
                            version = st.session_state.get("checkout_version", 0) + 1
                            st.session_state.checkout_version = version
                            st.session_state["sale_customer_" + str(version)] = p["customer"]["id"] if not p["customer"].get("anonymous") else ""
                            st.session_state["pay_mode_" + str(version)] = "Paga todo" if p["paid"] == p["total"] else "A cuenta / paga después" if not p["paid"] else "Abona una parte"
                            st.session_state["sale_abono_" + str(version)] = p["paid"] / 100
                            if p.get("method") in METHODS:
                                st.session_state["sale_method_" + str(version)] = p["method"]
                            if p.get("origin") in LOCATIONS:
                                st.session_state.sale_origin = p["origin"]
                            st.session_state.sale_stage_next = "Cobrar pedido"
                            st.session_state.nav_next = "Vender"
                        st.session_state.notice_error = "Movimiento no guardado. Revisa: " + st.session_state.get("pending_error", "la regla indicada")
                        st.session_state.pop("outbox", None)
                        st.session_state.pop("pending_error", None)
                        st.session_state.pop("rejected_pending", None)
                    st.rerun()
                except Exception as exc:
                    st.error(connection_message(exc))
    else:
        st.info("El operador original o el administrador puede resolverlo.")
    st.download_button("Guardar comprobante de recuperación", json_text(event), "movimiento_pendiente.json", "application/json", width="stretch")
    st.caption("Descarga el comprobante antes de actualizar o reiniciar. Después puedes abrirlo desde el menú lateral.")
    st.stop()


def setup_screen(store, user, users):
    st.title("Preparar Faro V2")
    if user["role"] != "admin":
        st.info("El administrador debe completar la preparación inicial.")
        st.stop()
    st.write("Se creará una pestaña nueva. Tus hojas Operaciones, Inventario, Deudas, Historial y Gastos se conservan.")
    st.warning("Haz el cambio cuando hayan terminado de usar la versión anterior. Después todos deben entrar a la misma aplicación nueva.")
    if st.button("Leer y revisar datos anteriores", width="stretch"):
        try:
            st.session_state.legacy_preview = legacy_preview(store.book)
        except RuleError as exc:
            st.error(str(exc))
        except Exception:
            st.error("No se pudieron leer las hojas. Revisa la conexión y permisos.")
    preview = st.session_state.get("legacy_preview")
    if preview:
        st.write(f"**{len(preview['products'])} productos · {len(preview['customers'])} clientes · {len(preview['legacy_orders'])} pedidos anteriores activos**")
        st.write("Saldo neto anterior: **" + money(sum(preview["balances"].values())) + "**")
        for warning in preview["warnings"]:
            st.info(warning)
        st.dataframe([dict(Cliente=x["name"], Saldo=money(preview["balances"].get(x["id"], 0))) for x in preview["customers"]], hide_index=True)
        with st.expander("Revisar productos y precios antes de importar"):
            st.dataframe([{"Producto": p["name"], "Categoría": p["category"], "Precio": money(p["price"]) if p["price"] is not None else "Por definir", "Activo": p["active"]} for p in preview["products"]], hide_index=True)
        with st.expander("Revisar pedidos anteriores activos"):
            for order in preview["legacy_orders"]:
                line = order["lines"][0]
                st.write(f"{order['folio']} · {order['customer']['name']} · {line['name']} · {line['due']} · {line['status']}")
        confirmed = st.checkbox("Revisé saldos, existencias y pedidos. Ya nadie captura en la versión anterior.")
        if st.button("Crear Faro V2 e importar una sola vez", type="primary", disabled=not confirmed, width="stretch"):
            send(store, user, users, "bootstrap", preview)
    st.stop()


def customer_picker(s, key, allow_anonymous=True):
    customers = sorted([c for c in s["customers"].values() if not c.get("anonymous")], key=lambda c: sort_key(c["name"]))
    by_id = {c["id"]: c for c in customers}
    options = ([""] if allow_anonymous else []) + list(by_id)
    if not options:
        st.info("Agrega primero un cliente.")
        return None
    if st.session_state.get(key) not in options:
        st.session_state.pop(key, None)
    choice = st.selectbox("Cliente · escribe para buscar", options,
                          format_func=lambda c: "Sin cliente / venta de mostrador" if not c else by_id[c]["name"] + (" · " + by_id[c].get("reference", "") if by_id[c].get("reference") else ""), key=key, persist_state="session")
    return by_id.get(choice)


def new_customer_form(store, s, user, users, prefix):
    with st.expander("＋ Nuevo cliente"):
        with st.form(prefix + "new_client", clear_on_submit=True):
            name = st.text_input("Nombre o referencia reconocible", key=prefix + "name")
            reference = st.text_input("Dónde encontrarlo", placeholder="Frente a los tacos, puesto azul…", key=prefix + "reference")
            if st.form_submit_button("Guardar cliente", width="stretch"):
                send(store, user, users, "customer", dict(id=uid(), name=name.strip(), reference=reference.strip(), anonymous=False))


def make_line(product, location, kitchen):
    return dict(id=uid(), product=product["id"], name=product["name"], category=product["category"],
                qty=1, base_price=product["price"], unit=product["price"], recipe=copy.deepcopy(product["recipe"]),
                location=location, kitchen=kitchen, status="Por preparar" if kitchen else "Entregado",
                due=now().isoformat(), scheduled=False, notes="", consumed=False)


def add_to_cart(line):
    # Comparación solo para agrupar unidades; nunca para asignar el total monetario.
    fields = ["product", "unit", "notes", "location", "kitchen", "scheduled"]
    match = next((x for x in st.session_state.cart if all(x[k] == line[k] for k in fields)
                  and not x["scheduled"] and x["recipe"] == line["recipe"]), None)
    if match:
        match["qty"] += line["qty"]
    else:
        st.session_state.cart.append(line)


def editor_screen(s):
    edit = st.session_state.editing
    line = copy.deepcopy(edit["line"])
    prefix = "modifier_" + edit.setdefault("form_id", uid()) + "_"
    old, options, descriptions = line.get("options", {}), {}, []
    st.subheader(line["name"])
    qty = int(st.number_input("Cantidad", min_value=1, max_value=100, value=line["qty"], step=1, key=prefix + "qty"))
    cat, name = line.get("category", ""), sort_key(line["name"])
    def select(field, label, values, default=None):
        previous = old.get(field, default or values[0])
        return choice(label, values, index=values.index(previous) if previous in values else 0,
                      key=prefix + field, persist_state=None)
    if cat in {"Frappés", "Esquimos", "Bebidas Frías", "Smoothies"}:
        options["milk"] = select("milk", "Tipo de leche", ["Entera", "Deslactosada", "Almendra (+$10)"])
        descriptions.append("Leche: " + options["milk"])
        options["adorn"] = select("adorn", "Adorno del vaso · incluido", ["Hershey's", "Caramelo", "Lechera", "Sin adorno"])
        descriptions.append("Vaso: " + options["adorn"])
        if cat == "Frappés":
            options["cream"] = select("cream", "Crema batida · incluida", ["Sí", "No"], "No" if old.get("no_cream") else "Sí")
            descriptions.append("Crema batida: " + options["cream"])
        options["sprinkles"] = select("sprinkles", "Chispas de chocolate · incluidas", ["Sí", "No"], "No" if old.get("no_sprinkles") else "Sí")
        descriptions.append("Chispas: " + options["sprinkles"])
    if cat == "Chamoyadas":
        options["stick"] = select("stick", "Banderilla · incluida", ["Sí", "No"], "No" if old.get("no_stick") else "Sí")
        options["gummies"] = select("gummies", "Gomitas · incluidas", ["Sí", "No"], "No" if old.get("no_gummies") else "Sí")
        descriptions.extend(["Banderilla: " + options["stick"], "Gomitas: " + options["gummies"]])
    if cat == "Refreshers":
        flavors = sorted({p["name"].split(" de ", 1)[-1] for p in s["products"].values()
                          if p["category"] == "Refreshers" and p["active"]}, key=sort_key)
        if not flavors:
            flavors = ["Fresa", "Cherry negra", "Guayaba", "Kiwi"]
        options["flavor"] = select("flavor", "Sabor", flavors, line["name"].split(" de ", 1)[-1])
        # Selecting another flavor selects its actual product and current base price.
        selected = next((p for p in s["products"].values() if p["category"] == cat and
            p["name"].split(" de ", 1)[-1] == options["flavor"] and p["active"]), None)
        if selected:
            line.update(product=selected["id"], name=selected["name"], base_price=selected["price"], recipe=copy.deepcopy(selected["recipe"]))
        descriptions.append("Sabor: " + options["flavor"])
    if cat in {"Chamoyadas", "Refreshers"}:
        options["pearls"] = select("pearls_choice", "Perlas explosivas", ["Sin perlas", "Con perlas (+$10)"],
            "Con perlas (+$10)" if old.get("pearls") else "Sin perlas") == "Con perlas (+$10)"
        descriptions.append("Con perlas explosivas (+$10)" if options["pearls"] else "Sin perlas")
    if "ensalada" in name or "sandwich" in name:
        options["protein"] = select("protein", "¿De qué lo preparamos?", ["Atún", "Pollo", "Pechuga a la plancha (+$10)"])
        descriptions.append(options["protein"])
    if "chilaquiles" in name:
        options["salsa"] = select("salsa", "Salsa", ["Verdes", "Rojos"])
        options["complete"] = select("complete", "Preparación", ["Con todo", "Con indicaciones"])
        descriptions.extend(["Salsa: " + options["salsa"], options["complete"]])
    note = st.text_input("Notas para preparar", value=line.get("free_note", ""), placeholder="Sin cebolla, salsa aparte…", max_chars=300, key=prefix + "note")
    custom = edit.setdefault("custom_extras", copy.deepcopy(line.get("custom_extras", [])))
    st.write("**Extras adicionales**")
    extra_rows = []
    for i, extra in enumerate(custom):
        with st.container(border=True):
            desc = st.text_input("Descripción del extra", value=extra["name"], key=prefix + f"extra_name_{i}", max_chars=100)
            price = cents(st.number_input("Precio del extra por unidad ($)", min_value=0.0, value=extra["price"] / 100, step=1.0, key=prefix + f"extra_price_{i}"))
            remove = st.checkbox("Quitar este extra", key=prefix + f"extra_remove_{i}")
            if not remove:
                extra_rows.append(dict(name=desc.strip(), price=price))
    if st.button("＋ Agregar extra con precio", key=prefix + "add_extra", width="stretch"):
        custom.append(dict(name="", price=0))
        st.rerun()
    later = st.toggle("Agendar para después", value=line["scheduled"], key=prefix + "later")
    due = datetime.fromisoformat(line["due"])
    date_input, hour_input = due.date(), due.time()
    if later:
        date_input = st.date_input("Fecha de entrega", value=max(due.date(), now().date()), min_value=now().date(), key=prefix + "date")
        hour_input = st.time_input("Hora de entrega", value=due.time().replace(second=0, microsecond=0), step=300, key=prefix + "time")
        st.caption("Aparece en Ahora 15 minutos antes de la entrega.")
    forced_kitchen = auto_kitchen(line) or later
    selected_kitchen = st.toggle("Enviar a cocina", value=forced_kitchen or line["kitchen"], disabled=forced_kitchen, key=prefix + "kitchen")
    valid_extras = all(e["name"] for e in extra_rows)
    surcharge = modifier_price(options, [e for e in extra_rows if e["name"]])
    unit = line["base_price"] + surcharge
    st.metric("Total de este producto", money(unit * qty))
    st.caption(f"{qty} × {money(unit)} · Base {money(line['base_price'])} + extras {money(surcharge)} por unidad")
    if not valid_extras:
        st.info("Describe el extra o marca Quitar este extra.")
    action = "Agregar al pedido" if edit["new"] else "Guardar cambios"
    if st.button(action + " · " + money(unit * qty), key=prefix + "save", type="primary", width="stretch", disabled=not valid_extras):
        dt = datetime.combine(date_input, hour_input, TZ) if later else now()
        if later and dt < now():
            st.error("Elige una hora futura.")
            return
        descriptions.extend(e["name"] + " (+" + money(e["price"]) + ")" for e in extra_rows)
        line.update(qty=qty, options=options, free_note=note, custom_extras=extra_rows,
            notes=" · ".join(descriptions + ([note] if note else [])), unit=unit,
            scheduled=later, due=dt.isoformat(), location="Puesto")
        line["kitchen"] = forced_kitchen or selected_kitchen
        line["status"] = "Por preparar" if line["kitchen"] else "Entregado"
        if edit["new"]:
            add_to_cart(line)
        else:
            st.session_state["line_kitchen_" + line["id"]] = line["kitchen"]
            st.session_state.cart = [line if x["id"] == line["id"] else x for x in st.session_state.cart]
        st.session_state.pop("editing")
        st.session_state.sale_stage_next = "Productos" if edit["new"] else "Cobrar pedido"
        st.rerun()
    if st.button("Volver sin cambios", key=prefix + "cancel", width="stretch"):
        st.session_state.pop("editing")
        st.session_state.sale_stage_next = "Productos" if edit["new"] else "Cobrar pedido"
        st.rerun()

def delivery_day(order, line):
    value = line['due'] if order.get('legacy') else order['at']
    return datetime.fromisoformat(value).astimezone(TZ).date().isoformat()


def free_item_screen():
    st.subheader("Agregar extra")
    with st.form("free_sale_item", clear_on_submit=False):
        name = st.text_input("¿Qué vas a cobrar?", placeholder="Ejemplo: extra de pollo, pedido especial…", max_chars=150)
        price = st.number_input("Precio por unidad ($)", min_value=0.0, step=1.0)
        qty = int(st.number_input("Cantidad", min_value=1, max_value=100, value=1, step=1))
        kitchen = st.toggle("Enviar este extra a cocina")
        if st.form_submit_button("Agregar al pedido", type="primary", width="stretch"):
            if not name.strip() or cents(price) <= 0:
                st.error("Escribe la descripción y un precio mayor a cero.")
                return
            product = dict(id="extra_" + uid(), name=name.strip(), category="Agregar extra", price=cents(price), recipe={})
            line = make_line(product, "Puesto", kitchen or auto_kitchen(product))
            line.update(free_item=True, qty=qty)
            add_to_cart(line)
            st.session_state.sale_stage_next = "Cobrar pedido"
            st.rerun()


KITCHEN_SOUND_JS = r"""
export default function(component) {
  const {data, parentElement} = component;
  const key = '__faroKitchenSound32';
  const state = window[key] || (window[key] = {context:null, enabled:false, previous:null, pending:false});
  const panel = parentElement.querySelector('#soundMenu');
  panel.open=!!state.menuOpen;
  panel.ontoggle=()=>{state.menuOpen=panel.open;};
  const play = parentElement.querySelector('#enableSound');
  const mute = parentElement.querySelector('#muteSound');
  const status = parentElement.querySelector('#soundStatus');
  function paint() {
    const running = state.enabled && state.context && state.context.state === 'running';
    status.textContent = running ? 'Sonido activado · mantén Cocina abierta y el volumen audible.' :
      (state.pending ? 'Hay pedidos nuevos. Activa el sonido para escucharlos.' : 'Sonido desactivado. Toca Activar y probar sonido.');
    mute.disabled = !state.enabled;
  }
  function ring() {
    if (!state.enabled || !state.context || state.context.state !== 'running') {
      state.pending = true; paint(); return;
    }
    const ctx=state.context;
    // Bell strike: fundamental plus inharmonic metallic partials, long decay.
    [0,0.65,1.30].forEach(offset => {
      [[880,0.32,1.6],[2425,0.13,1.0],[4755,0.055,0.65]].forEach(([frequency,peak,decay])=>{
        const osc=ctx.createOscillator(), gain=ctx.createGain(), start=ctx.currentTime+offset;
        osc.type='sine';osc.frequency.value=frequency;
        gain.gain.setValueAtTime(0,start);
        gain.gain.linearRampToValueAtTime(peak,start+0.004);
        gain.gain.exponentialRampToValueAtTime(0.001,start+decay);
        osc.connect(gain);gain.connect(ctx.destination);
        osc.onended=()=>{osc.disconnect();gain.disconnect();};
        osc.start(start);osc.stop(start+decay+0.02);
      });
    });
    state.pending=false;
  }
  play.onclick = async () => {
    try {
      const Audio=window.AudioContext || window.webkitAudioContext;
      if (!Audio) throw Error('unsupported');
      if (!state.context || state.context.state==='closed') state.context=new Audio();
      await state.context.resume();
      state.enabled=true;ring();paint();
    } catch (_) {state.enabled=false;status.textContent='No se pudo activar. Revisa el sonido de este navegador e inténtalo otra vez.';}
  };
  mute.onclick=()=>{state.enabled=false;paint();};
  const current=new Set(data.items || []);
  const changed=state.previous!==null && [...current].some(id=>!state.previous.has(id));
  state.previous=current;
  if(changed) ring();
  paint();
  return ()=>{play.onclick=null;mute.onclick=null;panel.ontoggle=null;};
}
"""


@st.cache_resource
def kitchen_sound_component():
    return st.components.v2.component("faro_kitchen_sound32", html='''
      <details id="soundMenu"><summary>🔔 Sonido de cocina</summary>
      <button id="enableSound">🔊 Activar y probar sonido</button>
      <button id="muteSound">Silenciar</button>
      <p id="soundStatus" role="status" aria-live="polite"></p></details>''', css='''
      summary {cursor:pointer;font:inherit;font-size:16px;padding:10px 0;color:var(--st-text-color,#155b46);}
      button {min-height:56px;padding:12px 18px;border-radius:12px;border:1px solid #155b46;
              font:inherit;font-size:17px;margin:4px;background:#155b46;color:white;cursor:pointer;}
      #muteSound {background:white;color:#155b46;} button:disabled{opacity:.5;}
      p {font:inherit;font-size:15px;color:var(--st-text-color);}''', js=KITCHEN_SOUND_JS)


def kitchen_notifications(s):
    tokens=[]
    for order in s["orders"].values():
        if order.get("legacy"):
            continue
        for line in order["lines"]:
            if line["kitchen"] and line["status"] in {"Por preparar", "Preparando"} and not kitchen_upcoming(line, now()):
                tokens.append(line["id"] + ":" + str(len(line.get("changes", []))))
    previous=st.session_state.get("kitchen_notification_tokens")
    if previous is not None and set(tokens)-set(previous):
        st.toast("Hay pedidos nuevos o modificados para preparar", icon="🔔")
    st.session_state.kitchen_notification_tokens=tokens
    kitchen_sound_component()(data=dict(items=sorted(tokens)), key="kitchen_sound")


def sale_screen(store, s, user, users):
    st.header("Nueva venta")
    st.session_state.setdefault("cart", [])
    if st.session_state.get("editing"):
        editor_screen(s)
        return
    if st.session_state.get("last_order") in s["orders"]:
        oid = st.session_state.last_order
        with st.expander("Último pedido · " + s["orders"][oid]["customer"]["name"]):
            st.write("Total: " + money(s["orders"][oid]["total"]))
            if st.button("Ver ticket", key="last_ticket", width="stretch"):
                st.session_state.ticket = dict(order=oid, type="venta")
                st.rerun()
    cart = st.session_state.cart
    total_now = sum(x["qty"] * x["unit"] for x in cart)
    count = sum(x["qty"] for x in cart)
    if st.session_state.get("sale_stage_next"):
        st.session_state.sale_stage = st.session_state.pop("sale_stage_next")
    stage = choice("Venta", ["Productos", "Cobrar pedido"], key="sale_stage",
                   format_func=lambda x: x if x == "Productos" else f"Pedido ({count}) · {money(total_now)}")
    origin, kitchen = "Puesto", False
    if stage == "Productos":
        products = [x for x in s["products"].values() if x["active"] and x["price"] is not None]
        search = st.text_input("Buscar producto o sabor", placeholder="Ejemplo: red velvet, milanesa…", key="product_search")
        if search.strip():
            query = sort_key(search.strip())
            products = [x for x in products if query in sort_key(x["name"] + " " + x["category"])]
        else:
            categories = ordered_categories(products)
            if categories:
                current = st.session_state.get("category", categories[0])
                if current not in categories:
                    st.session_state.pop("category", None)
                current = choice("Categoría", categories, key="category")
                if current == "Agregar extra":
                    free_item_screen()
                    if cart and st.button("Revisar y cobrar · " + money(total_now), type="primary", width="stretch"):
                        st.session_state.sale_stage_next = "Cobrar pedido"
                        st.rerun()
                    return
                products = [x for x in products if category_label(x["category"]) == current]
        if not products:
            st.info("No hay productos en esta selección. Prueba otra categoría o agrégalos en Más → Catálogo.")
        cols = st.columns(2)
        location = "Puesto" if kitchen else origin
        for i, product in enumerate(sorted(products, key=lambda x: sort_key(x["name"]))):
            stock = min((available(s, location, a) // qty for a, qty in product["recipe"].items()), default=None)
            label = product["name"] + "\n" + money(product["price"])
            if stock is not None:
                label += "\n" + ("Agotado" if stock <= 0 else f"Disponibles: {stock}")
            if cols[i % 2].button(label, key="prod_" + product["id"], width="stretch", disabled=stock is not None and stock <= 0):
                line = make_line(product, location, auto_kitchen(product))
                if product["category"] in {"Frappés", "Esquimos", "Bebidas Frías", "Chamoyadas", "Refreshers", "Smoothies"} or auto_kitchen(product):
                    st.session_state.editing = dict(line=line, new=True)
                else:
                    add_to_cart(line)
                    st.session_state.notice = product["name"] + " agregado al pedido"
                st.rerun()
        if cart and st.button("Revisar y cobrar · " + money(total_now), type="primary", width="stretch"):
            st.session_state.sale_stage_next = "Cobrar pedido"
            st.rerun()
        st.caption("Toca un producto. Si tiene opciones, elige sus extras antes de agregarlo. El precio se calcula automáticamente.")
        return
    cart = st.session_state.cart
    st.divider()
    if not cart:
        st.info("Tu pedido está vacío. Toca Productos para agregar algo.")
        return
    st.subheader("Tu pedido · " + money(sum(x["qty"] * x["unit"] for x in cart)))
    for line in cart:
        with st.container(border=True):
            st.write(f"**{line['qty']} × {line['name']} · {money(line['qty'] * line['unit'])}**")
            automatic = auto_kitchen(line) or line["scheduled"]
            line["kitchen"] = st.toggle("Enviar a cocina", value=automatic or line["kitchen"], disabled=automatic,
                                         key="line_kitchen_" + line["id"])
            if automatic:
                line["kitchen"] = True
                st.caption("Envío automático por ser comida o un producto agendado.")
            line["status"] = "Por preparar" if line["kitchen"] else "Entregado"
            st.caption(("Cocina" if line["kitchen"] else "Entrega aquí") +
                       (" · " + datetime.fromisoformat(line["due"]).strftime("%d/%m %H:%M") if line["scheduled"] else " · Ahora"))
            if line["notes"]:
                st.write(line["notes"])
            minus, plus = st.columns(2)
            if minus.button("− Quitar uno", key="minus_" + line["id"], width="stretch"):
                line["qty"] -= 1
                st.session_state.cart = [x for x in cart if x["qty"] > 0]
                st.rerun()
            if plus.button("＋ Agregar uno", key="plus_" + line["id"], width="stretch"):
                line["qty"] += 1
                st.rerun()
            if st.button("Personalizar / programar", key="edit_" + line["id"], width="stretch"):
                st.session_state.editing = dict(line=line, new=False)
                st.rerun()
    st.caption("Chilaquiles, torta de chilaquiles, ensaladas y sándwiches van automáticamente a cocina al guardar.")
    version = str(st.session_state.get("checkout_version", 0))
    customer = customer_picker(s, "sale_customer_" + version)
    new_customer_form(store, s, user, users, "sale_")
    if customer:
        balance = s["balances"].get(customer["id"], 0)
        st.caption("Saldo anterior: " + money(balance))
    total = sum(x["qty"] * x["unit"] for x in cart)
    pay_mode = choice("Cobro de este pedido", ["Paga todo", "A cuenta / paga después", "Abona una parte"], key="pay_mode_" + version)
    paid, method = 0, ""
    if pay_mode != "A cuenta / paga después":
        paid = total if pay_mode == "Paga todo" else cents(st.number_input("Abono de este pedido ($)", min_value=0.0, max_value=total / 100, step=5.0, key="sale_abono_" + version))
        method = choice("Forma de pago", METHODS, horizontal=True, key="sale_method_" + version)
        if method == "Efectivo":
            tender = received_cash(paid, "sale_cash_" + version)
        else:
            tender = paid
            st.caption("Confirma que el pago sí llegó antes de registrarlo.")
    else:
        tender = 0
    st.write("**Total: " + money(total) + " · Pendiente de este pedido: " + money(total - paid) + "**")
    missing_customer = customer is None and (paid < total or any(x["kitchen"] for x in cart))
    if missing_customer:
        st.info("Selecciona un cliente para identificar la deuda o el pedido de cocina.")
    cash_ok = cash_prompt(s, user, "sale_open_cash") if paid > 0 and method == "Efectivo" else True
    if st.button("Guardar pedido · " + money(total), type="primary", width="stretch", disabled=missing_customer or not cash_ok):
        if paid and method == "Efectivo" and tender < paid:
            st.error("El efectivo recibido es menor al importe que vas a cobrar.")
        elif pay_mode == "Abona una parte" and paid <= 0:
            st.error("Escribe un abono mayor a cero o selecciona A cuenta.")
        else:
            oid = uid()
            anon = dict(id="walkin_" + oid, name="Mostrador", reference="", anonymous=True)
            lines = copy.deepcopy(cart)
            for line in lines:
                if not line["scheduled"]:
                    line["due"] = now().isoformat()
            payload = dict(id=oid, folio=now().strftime("%d%m") + "-" + oid[:6].upper(),
                           customer=customer or anon, lines=lines, total=total, paid=paid, method=method, received=tender, origin=origin)
            send(store, user, users, "sale", payload)
    with st.expander("Descartar este pedido sin guardar"):
        if st.button("Confirmar: vaciar pedido", width="stretch"):
            st.session_state.cart = []
            st.session_state.checkout_version = int(version) + 1
            st.rerun()


def order_heading(order):
    st.subheader(order["customer"]["name"])
    if order["customer"].get("reference"):
        st.write("📍 " + order["customer"]["reference"])
    with st.expander("Detalles del pedido"):
        st.caption("Referencia: " + order["folio"] + " · Registró: " + order["actor"])
        if order.get("legacy"):
            st.caption("Pedido importado. Su importe ya está considerado en la cuenta del cliente.")


@st.fragment(run_every="10s")
def kitchen_screen(store, user, users):
    s = safe_state(store)
    st.header("👨‍🍳 Cocina")
    kitchen_notifications(s)
    st.caption("Última consulta: " + now().strftime("%H:%M:%S") + " · actualización cada 10 segundos mientras esta pantalla está abierta")
    mode = choice("Mostrar", ["Ahora", "Agendados"], horizontal=True, key="k_mode_v3")
    st.caption("Los agendados pasan a Ahora 15 minutos antes de su entrega.")
    entries = []
    for order in s["orders"].values():
        if order.get("legacy"):
            continue
        lines = [x for x in order["lines"] if x["kitchen"] and x["status"] in {"Por preparar", "Preparando"}]
        for line in lines:
            due = datetime.fromisoformat(line["due"])
            upcoming = kitchen_upcoming(line, now())
            if (mode == "Agendados" and upcoming) or (mode == "Ahora" and not upcoming):
                entries.append((due, order, line))
    entries.sort(key=lambda x: (x[1]["at"], x[2]["id"]) if mode == "Ahora" else (x[0].isoformat(), x[1]["at"]))
    if not entries:
        st.success("No hay productos pendientes en esta vista.")
    for due, order, line in entries:
        with st.container(border=True):
            order_heading(order)
            st.subheader(f"{line['qty']} × {line['name']}")
            if line["scheduled"]:
                text = "Entrega: " + due.strftime("%d/%m · %H:%M")
                if due < now():
                    st.error("Hora cumplida · " + text)
                else:
                    st.info(text)
            else:
                waiting = max(0, int((now() - datetime.fromisoformat(order["at"])).total_seconds() // 60))
                st.write(f"Ahora · {waiting} min desde el registro")
            if line["notes"]:
                st.warning(line["notes"])
            if line.get("changes"):
                st.error("Pedido modificado · " + line["changes"][-1]["reason"])
                with st.expander("Ver cambios para preparación"):
                    for change in line["changes"]:
                        st.write(users.get(change["actor"], {}).get("nombre", change["actor"]) + " · " + change["at"][11:16])
                        st.write("Antes: " + str(change["before"]["qty"]) + " · " + change["before"]["notes"])
                        st.write("Ahora: " + str(change["after"]["qty"]) + " · " + change["after"]["notes"])
            st.write("Estado: **" + line["status"] + "**")
            if st.button("✅ Marcar listo", key="k_" + line["id"], type="primary", width="stretch"):
                send(store, user, users, "line_state", dict(order=order["id"], line=line["id"], expected=line["status"], status="Listo"))
            if st.button("Ticket de cocina", key="kt_" + line["id"], width="stretch"):
                st.session_state.ticket = dict(order=order["id"], type="cocina")
                st.rerun()


@st.fragment(run_every="10s")
def deliveries_screen(store, user, users):
    s = safe_state(store)
    st.header("🚚 Entregas")
    source = choice("Pedidos", ["Actuales", "Anteriores"], key="delivery_source")
    found = False
    for order in sorted(s["orders"].values(), key=lambda o: o["at"]):
        if bool(order.get("legacy")) != (source == "Anteriores"):
            continue
        lines = [x for x in order["lines"] if x["status"] in {"Listo", "En reparto"}
                 and (source != "Anteriores" or delivery_day(order, x) == day())]
        if not lines:
            continue
        found = True
        with st.container(border=True):
            order_heading(order)
            balance = s["balances"].get(order["customer"]["id"], 0)
            st.write("Saldo total del cliente: **" + money(balance) + "**")
            for line in lines:
                st.write(f"**{line['qty']} × {line['name']} · {line['status']}**")
                if line["notes"]:
                    st.write(line["notes"])
                if line["status"] == "Listo" and st.button("Me lo llevo a entregar", key="take_" + line["id"], width="stretch"):
                    send(store, user, users, "line_state", dict(order=order["id"], line=line["id"], expected="Listo", status="En reparto"))
                if line["status"] == "En reparto":
                    st.caption("Lo lleva: " + line.get("updated_by", ""))
                if st.button("✅ Ya lo entregué", key="deliver_" + line["id"], type="primary", width="stretch"):
                    send(store, user, users, "line_state", dict(order=order["id"], line=line["id"], expected=line["status"], status="Entregado"))
            if balance > 0 and st.button("Cobrar a este cliente", key="collect_" + order["id"], width="stretch"):
                st.session_state.selected_debtor = order["customer"]["id"]
                st.session_state.nav_next = "Cobrar"
                st.rerun()
    if not found:
        st.info("No hay productos listos ni en reparto.")


DEBT_SEARCH_JS = r"""
export default function(component) {
  const {data,parentElement,setTriggerValue}=component;
  const state=window.__faroDebtSearch33 || (window.__faroDebtSearch33={query:'',scroll:0,returning:false});
  const field=parentElement.querySelector('#debtQuery');
  const list=parentElement.querySelector('#debtResults');
  const empty=parentElement.querySelector('#noDebts');
  const clear=parentElement.querySelector('#clearDebtQuery');
  const normalize=value=>String(value || '').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase().trim();
  if(state.returning){state.query="";}
  field.value=state.query;
  list.replaceChildren();
  const rows=(data.clients || []).map(client=>{
    const button=document.createElement('button');
    button.type='button';
    const title=document.createElement('span');
    title.textContent=client.name+' · '+client.status;
    button.appendChild(title);
    if(client.reference){const ref=document.createElement('small');ref.textContent=client.reference;button.appendChild(ref);}
    button.onclick=()=>{
      if(!state.query)state.scroll=list.scrollTop;
      state.returning=true;
      setTriggerValue('selected',client.id);
    };
    list.appendChild(button);
    return {button,text:normalize(client.name+' '+(client.reference || ''))};
  });
  function filter(){
    if(!state.query && field.value)state.scroll=list.scrollTop;
    state.query=field.value;
    const words=normalize(field.value).split(/\s+/).filter(Boolean);
    let matches=0;
    rows.forEach(row=>{row.button.hidden=!words.every(word=>row.text.includes(word));if(!row.button.hidden)matches++;});
    empty.hidden=matches>0;
    clear.disabled=!field.value;
  }
  field.oninput=()=>{filter();list.scrollTop=field.value?0:state.scroll;};
  clear.onclick=()=>{field.value='';filter();list.scrollTop=state.scroll;field.focus();};
  filter();
  let alive=true;
  const restore=()=>{if(alive){list.scrollTop=state.scroll || 0;}};
  if(state.returning){
    restore();
    requestAnimationFrame(restore);
    state.returning=false;
  }
  return ()=>{alive=false;field.oninput=null;clear.onclick=null;rows.forEach(row=>row.button.onclick=null);};
}
"""


@st.cache_resource
def debt_search_component():
    return st.components.v2.component("faro_debt_search33", html='''
      <label for="debtQuery">Buscar por nombre o ubicación</label>
      <div class="search"><input id="debtQuery" type="search" autocomplete="off" placeholder="Escribe y verás los resultados…"/>
      <button id="clearDebtQuery" type="button" aria-label="Limpiar búsqueda">Limpiar</button></div>
      <div id="debtResults"></div>
      <p id="noDebts" role="status" hidden>No hay cuentas que coincidan con esta búsqueda.</p>''',
      css='''
      #debtResults {max-height:65vh;overflow-y:auto;overscroll-behavior:contain;overflow-anchor:none;padding:2px;}
      :host {font-family:inherit;color:var(--st-text-color,#183c31);}
      label {display:block;font-size:17px;margin-bottom:8px;}
      .search {display:flex;gap:8px;margin-bottom:16px;}
      input {min-width:0;width:100%;box-sizing:border-box;min-height:56px;border:1px solid #bbc9c3;
             border-radius:12px;padding:12px;font:inherit;font-size:18px;background:var(--st-secondary-background-color,#f5f8f6);color:inherit;}
      button {min-height:58px;border:1px solid #cbd6d0;border-radius:12px;padding:14px;
              font:inherit;font-size:17px;background:var(--st-background-color,white);color:inherit;cursor:pointer;}
      button:focus-visible,input:focus-visible {outline:3px solid #267b60;outline-offset:2px;}
      #debtResults button {display:block;width:100%;text-align:left;margin-bottom:10px;}
      #debtResults button[hidden] {display:none;}
      small {display:block;font-size:15px;margin-top:5px;color:var(--st-text-color,#526b60);}
      button:disabled {opacity:.5;cursor:default;}
      ''',js=DEBT_SEARCH_JS)


def cash_suggestions(amount):
    rounded = ((amount // 1000) + 1) * 1000
    candidates = sorted({rounded, *[v * 100 for v in (20, 50, 100, 200, 300, 500, 1000)]})
    values = [v for v in candidates if v > amount][:3]
    while len(values) < 3:
        values.append(((values[-1] if values else amount) // 10000 + 1) * 10000)
    return values


def received_cash(amount, key):
    suggestions = cash_suggestions(amount)
    labels = ["Exacto", *[money(v) for v in suggestions], "Otra cantidad"]
    selected = choice("¿Con cuánto paga?", labels, key=key + "_option_" + str(amount))
    received = (amount if selected == "Exacto" else
                cents(st.number_input("Efectivo recibido ($)", min_value=0.0, step=10.0, key=key + "_manual_" + str(amount)))
                if selected == "Otra cantidad" else suggestions[labels.index(selected) - 1])
    st.metric("Cambio a entregar", money(max(0, received - amount)))
    if received < amount:
        st.warning("Falta dinero para este cobro. Si es un abono, ajusta primero el importe a cobrar.")
    return received


def payment_screen(store, s, user, users):
    st.header("Cuentas por cobrar")
    selected = st.session_state.get("selected_debtor")
    if selected in s["customers"]:
        if st.button("← Volver a deudores", width="stretch"):
            st.session_state.pop("selected_debtor", None)
            st.rerun()
        customer = s["customers"][selected]
        balance = s["balances"].get(selected, 0)
        account_card(customer, balance)
        prefix = "collect_" + selected + "_" + str(balance)
        if balance > 0:
            mode = choice("¿Cuánto paga?", ["Liquidar todo", "Abono parcial"], key=prefix + "mode")
            amount = balance
            if mode == "Abono parcial":
                amount = cents(st.number_input("Abono ($)", min_value=0.0, max_value=balance / 100,
                                               step=5.0, key=prefix + "amount"))
            method = choice("Forma de pago", METHODS, key=prefix + "method")
            tender = amount
            if method == "Efectivo":
                tender = received_cash(amount, prefix + "cash")
            else:
                st.info("Confirma que el pago llegó antes de registrarlo.")
            st.write("Después de este pago quedará debiendo **" + money(balance - amount) + "**")
            if tender < amount:
                st.warning("El efectivo recibido no alcanza para este pago.")
            cash_ok = cash_prompt(s, user, "collect_open_cash") if method == "Efectivo" else True
            if st.button("Confirmar cobro · " + money(amount), type="primary", width="stretch",
                         disabled=amount <= 0 or tender < amount or not cash_ok):
                send(store, user, users, "payment", dict(customer=selected, amount=amount, method=method, received=tender))
        elif balance == 0:
            st.success("Esta cuenta ya está liquidada.")
        else:
            st.info("El saldo a favor se compensará con sus próximas compras.")
            if user["role"] == "admin":
                with st.expander("Devolver saldo a favor"):
                    with st.form("refund_" + selected + str(balance)):
                        amount = st.number_input("Devolver ($)", min_value=0.0, max_value=-balance / 100, value=-balance / 100)
                        method = choice("Medio de devolución", METHODS)
                        if st.form_submit_button("Registrar devolución realizada", width="stretch"):
                            send(store, user, users, "refund", dict(customer=selected, amount=cents(amount), method=method))
        with st.expander("Ver compras, abonos y entregas"):
            entries = [x for x in s["history"] if x["customer"] == selected]
            for h in reversed(entries[-100:]):
                label = "Abono / ajuste a favor" if h["amount"] < 0 else "Cargo"
                st.write(f"**{label} · {money(abs(h['amount']))}**")
                st.caption(f"{datetime.fromisoformat(h['at']).strftime('%d/%m %H:%M')} · {h['detail']} · {h['actor']}")
            st.caption("Últimos 100 movimientos. El saldo incluye todo el historial. El detalle anterior a V2 sigue en la pestaña Deudas.")
            for order in s["orders"].values():
                if order["customer"]["id"] == selected:
                    for line in order["lines"]:
                        if line["status"] not in {"Entregado", "Cancelado"}:
                            st.info(f"{order['folio']} · {line['qty']} × {line['name']} · {line['status']}")
        with st.expander("Efectivo recibido y cambio entregado"):
            for payment in reversed(s["payments"]):
                if payment["customer"] == selected and payment["method"] == "Efectivo" and not payment.get("refund"):
                    st.write(f"{payment['at'][:16]} · {payment['actor']} · Cobrado: {money(payment['amount'])}")
                    if "received" in payment:
                        st.caption("Recibido: " + money(payment["received"]) + " · Cambio: " + money(payment.get("change", payment["received"] - payment["amount"])))
                    else:
                        st.caption("Movimiento anterior sin detalle de efectivo recibido.")
        with st.expander("Editar ubicación para identificar al cliente"):
            with st.form("reference_" + selected):
                reference = st.text_input("Referencia", value=customer.get("reference", ""))
                if st.form_submit_button("Guardar ubicación", width="stretch"):
                    send(store, user, users, "customer", dict(customer, reference=reference.strip()))
        return

    debtors = [c for c in s["customers"].values() if s["balances"].get(c["id"], 0) > 0]
    if user["role"] == "admin":
        total, count = st.columns(2)
        total.metric("Por cobrar", money(sum(s["balances"][c["id"]] for c in debtors)))
        count.metric("Personas con deuda", len(debtors))
    order = choice("Ordenar", ["Mayor deuda", "Nombre", "Ubicación"], key="debt_order")
    all_clients = st.toggle("Incluir cuentas liquidadas y saldos a favor", key="debt_all")
    clients = [c for c in s["customers"].values() if (not c.get("anonymous") or s["balances"].get(c["id"], 0) != 0)
               and (all_clients or s["balances"].get(c["id"], 0) > 0)]
    clients.sort(key=lambda c: ((-s["balances"].get(c["id"], 0) if order == "Mayor deuda" else
                                sort_key(c.get("reference", "")) if order == "Ubicación" else sort_key(c["name"])), sort_key(c["name"])))
    rows = []
    for customer in clients:
        balance = s["balances"].get(customer["id"], 0)
        status = money(balance) + " por cobrar" if balance > 0 else "Liquidado" if balance == 0 else money(-balance) + " a favor"
        rows.append(dict(id=customer["id"], name=customer["name"], reference=customer.get("reference", ""), status=status))
    selected = debt_search_component()(data=dict(clients=rows), key="debt_picker", on_selected_change=lambda: None).selected
    if selected and selected in {c["id"] for c in clients}:
        st.session_state.selected_debtor = selected
        st.rerun()
    new_customer_form(store, s, user, users, "pay_")


def inventory_screen(store, s, user, users):
    st.header("📦 Existencias actuales")
    st.write("Escribe cuántas piezas tienes en total. El número que guardes reemplaza el anterior; no se suma.")
    admin = user["role"] == "admin"
    names = {}
    for product in s["products"].values():
        if is_torta(product) and (product["price"] is not None or product["active"] or s["stock"].get(("Puesto", product["id"]), 0)):
            names[product["id"]] = product["name"]
    if admin:
        names["vaso_caliente"] = "Vasos de café y té"
    else:
        st.info("Los vasos los modifica el administrador.")
    st.caption("La torta de chilaquiles no necesita conteo de existencias.")
    if not names:
        st.info("No hay tortas configuradas. Puedes agregarlas en Catálogo.")
        return
    version = str(st.session_state.get("stock_form_version", 0)) + ("_admin" if admin else "_worker")
    snapshot = st.session_state.setdefault("inventory_snapshot_" + version,
        {a: s["stock"].get(("Puesto", a), 0) for a in names})
    for a in names:
        snapshot.setdefault(a, s["stock"].get(("Puesto", a), 0))
    with st.form("inventory_edit_" + version):
        quantities = {}
        for asset in sorted(names, key=lambda a: (a == "vaso_caliente", sort_key(names[a]))):
            with st.container(key="stock_row_" + asset):
                label, entry = st.columns([2, 1])
                label.write("**" + names[asset] + "**")
                quantities[asset] = int(entry.number_input("Existencias · " + names[asset], min_value=0,
                    value=max(0, snapshot[asset]), step=1, key="stock_actual_" + version + "_" + asset, label_visibility="collapsed"))
        if st.form_submit_button("Guardar existencias", type="primary", width="stretch"):
            changed = {a: q for a, q in quantities.items() if q != snapshot[a]}
            if not changed:
                st.info("No cambiaste ninguna cantidad.")
            else:
                send(store, user, users, "stock_count", dict(location="Puesto", items=changed,
                    expected={a: snapshot[a] for a in changed}, adjust=True, reason="Actualización de existencias físicas"))
    if st.button("Actualizar cantidades desde el sistema", width="stretch"):
        st.session_state.stock_form_version = st.session_state.get("stock_form_version", 0) + 1
        store.cached = None
        st.rerun()
    with st.expander("Productos apartados para pedidos"):
        st.caption("El conteo incluye las piezas apartadas. No puedes registrar menos piezas que las comprometidas en pedidos pendientes.")
        rows=[{"Producto":names[a],"Apartados":reserved(s,"Puesto",a),"Libres para vender":available(s,"Puesto",a)} for a in names]
        st.dataframe(rows, hide_index=True, width="stretch")
    if admin:
        with st.expander("Historial de cambios"):
            for event in reversed([e for e in s["events"] if e["kind"].startswith("stock_")][-30:]):
                st.write(event["at"][:16] + " · " + users.get(event["actor"], {}).get("nombre", event["actor"]) + " · " + event["payload"].get("reason", ""))
                st.write({names.get(a, s["assets"].get(a, {}).get("name", a)): q for a,q in event["payload"]["items"].items()})


def cash_screen(store, s, user, users):
    actor, date = user["id"], day()
    st.header("💵 Caja general")
    st.write("Una sola caja para todo el puesto · " + date)
    st.caption("Cada cobro conserva el nombre de quien lo recibió. El administrador puede consultar el desglose por usuario.")
    if not any(d == date for d, _ in s["openings"]) and (date, cash_owner(s, actor)) not in s["closed"]:
        with st.expander("Registrar fondo para dar cambio", expanded=False), st.form("opening_" + date):
            amount = st.number_input("Fondo de cambio del puesto hoy ($)", min_value=0.0, step=50.0)
            st.caption("No es una venta. Si empiezas sin cambio, registra $0. El dinero del día anterior que conserves también se incluye aquí.")
            if st.form_submit_button("Guardar fondo general", type="primary", width="stretch"):
                send(store, user, users, "opening", dict(amount=cents(amount), shared=True))
        if s.get("schema", 2) < 3:
            return
    expected = operating_cash(s, actor, date)
    st.metric("Fondo general registrado", money(opening_total(s, date)))
    st.metric("Efectivo esperado en el puesto", money(expected))
    closed = s["closed"].get((date, cash_owner(s, actor)))
    if closed:
        st.success("Caja cerrada · Contado: " + money(closed["counted"]) + " · Diferencia: " + money(closed["counted"] - closed["expected"]))
    for tid, transfer in s["transfers"].items():
        if transfer["status"] != "Pendiente":
            continue
        if transfer["target"] == actor:
            st.warning("Por confirmar: " + money(transfer["amount"]) + " de " + users.get(transfer["sender"], {}).get("nombre", transfer["sender"]))
            if st.button("Ya recibí ese dinero", key="accept_" + tid, width="stretch", disabled=bool(closed)):
                send(store, user, users, "transfer_accept", dict(transfer=tid))
        elif transfer["sender"] == actor:
            st.info("Entregaste " + money(transfer["amount"]) + " a " + transfer["target"] + "; falta que confirme.")
    if not closed:
        mode = choice("Registrar", (["Gasto", "Retiro", "Corte"] if user["role"] == "admin" else ["Gasto"]), horizontal=True)
        if mode == "Entrega de dinero":
            others = [x for x in users if x != actor]
            if others:
                with st.form("cash_transfer"):
                    target = st.selectbox("¿A quién le entregaste?", others, format_func=lambda x: users[x].get("nombre", x))
                    amount = st.number_input("Efectivo entregado ($)", min_value=0.0, step=50.0)
                    reason = st.text_input("Referencia", value="Entrega de efectivo")
                    if st.form_submit_button("Registrar entrega para que la confirme", type="primary", width="stretch"):
                        send(store, user, users, "transfer", dict(target=target, amount=cents(amount), reason=reason))
            else:
                st.info("Configura los usuarios del equipo para registrar traspasos.")
        elif mode in {"Gasto", "Retiro"}:
            with st.form("cash_out_" + mode):
                reason = st.text_input("Concepto")
                amount = st.number_input("Importe ($)", min_value=0.0, step=10.0)
                method = choice("Pagado con", METHODS, horizontal=True)
                st.caption("Solo los movimientos en efectivo se restan de la caja general. Una compra aquí no suma inventario; registra también su entrada.")
                if st.form_submit_button("Registrar " + mode.lower(), type="primary", width="stretch"):
                    send(store, user, users, "expense" if mode == "Gasto" else "withdraw", dict(amount=cents(amount), reason=reason, method=method))
        else:
            with st.form("cash_close_" + str(expected)):
                counted = st.number_input("Efectivo que contaste físicamente ($)", min_value=0.0, step=10.0)
                reason = st.text_input("Observaciones del corte")
                st.write("Esperado: **" + money(expected) + "**")
                st.caption("El corte conserva pedidos, cuentas por cobrar e historial. Confirma tus traspasos primero.")
                if st.form_submit_button("Guardar corte general", type="primary", width="stretch"):
                    send(store, user, users, "close", dict(owner=cash_owner(s, actor), expected=expected, counted=cents(counted), reason=reason))
    with st.expander("Mis movimientos de efectivo de hoy", expanded=True):
        for row in s["cash"]:
            if row["owner"] == actor and row["date"] == date:
                st.write(f"{row['at'][11:16]} · {row['detail']} · **{money(row['amount'])}**")


def export_csv(rows):
    if not rows:
        return ""
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(rows[0]))
    writer.writeheader()
    for row in rows:
        # No ejecutar fórmulas al abrir el CSV en Excel.
        writer.writerow({k: "'" + v if isinstance(v, str) and v[:1] in {"=", "+", "-", "@"} else v for k, v in row.items()})
    return "\ufeff" + output.getvalue()


def reports_screen(store, s, user, users):
    st.header("📊 Resumen y cortes")
    admin_close_screen(store, s, user, users)
    st.metric("Vasos disponibles", available(s, "Puesto", "vaso_caliente"))
    report_date = st.date_input("Día a consultar", value=now().date()).isoformat()
    sales = [o for o in s["orders"].values() if o["at"][:10] == report_date and not o.get("legacy")]
    payments = [p for p in s["payments"] if p["at"][:10] == report_date]
    st.metric("Ventas registradas ese día, netas de cancelaciones", money(sum(o["total"] for o in sales)))
    st.caption("Las cancelaciones corrigen el total del pedido original. Su fecha y autor se conservan en el historial.")
    for method in METHODS:
        amount = sum((-1 if p.get("refund") else 1) * p["amount"] for p in payments if p["method"] == method)
        st.write("**Cobrado neto en " + method.lower() + ": " + money(amount) + "**")
    st.write("Cuentas por cobrar actuales: **" + money(sum(max(0, b) for b in s["balances"].values())) + "**")
    st.caption("Cobros incluye abonos de compras anteriores. Cuentas por cobrar es el saldo actual, no un saldo histórico a esa fecha.")
    expenses = [e for e in s["events"] if e["kind"] == "expense" and e["at"][:10] == report_date]
    st.write("Gastos del día: **" + money(sum(e["payload"]["amount"] for e in expenses)) + "**")
    st.metric("Fondo general del día", money(opening_total(s, report_date)))
    st.metric("Efectivo esperado del puesto", money(cash_total(s, "__puesto__", report_date)))
    close = s["closed"].get((report_date, "__puesto__"))
    if close:
        st.write("Contado: **" + money(close["counted"]) + "** · Diferencia: **" + money(close["counted"] - close["expected"]) + "**")
    owners = set(users) | {p["actor"] for p in payments} | {o["actor"] for o in sales}
    cash_rows = []
    for owner in sorted(owners):
        row = {"Responsable": users.get(owner, {}).get("nombre", owner),
               "Ventas": money(sum(o["total"] for o in sales if o["actor"] == owner))}
        for method in METHODS:
            row["Cobros " + method] = money(sum((-1 if p.get("refund") else 1) * p["amount"]
                for p in payments if p["actor"] == owner and p["method"] == method))
        cash_rows.append(row)
    st.subheader("Cobros por usuario")
    st.caption("Importes netos, descontando devoluciones y correcciones. El fondo general no se cuenta como venta ni como cobro de un usuario.")
    st.dataframe(cash_rows, hide_index=True, width="stretch")
    transit = sum(t["amount"] for t in s["transfers"].values() if t["status"] == "Pendiente")
    st.write("Entregas de dinero pendientes de confirmar, actuales: **" + money(transit) + "**")
    quantities = defaultdict(lambda: [0, 0])
    for order in sales:
        for line in order["lines"]:
            if line["status"] != "Cancelado":
                key = (line["product"], line["name"])
                quantities[key][0] += line["qty"]
                quantities[key][1] += line["qty"] * line["unit"]
    st.subheader("Productos vendidos")
    st.dataframe([{"Producto": name, "Unidades": q, "Venta": money(amount)} for (_, name), (q, amount) in quantities.items()], hide_index=True)
    details = []
    for o in sales:
        for line in o["lines"]:
            details.append(dict(Folio=o["folio"], Fecha=o["at"], Cliente=o["customer"]["name"],
                                Operador=o["actor"], Producto=line["name"], Cantidad=line["qty"],
                                Precio_unitario=line["unit"] / 100, Importe=line["qty"] * line["unit"] / 100,
                                Estado=line["status"], Entrega=line["due"], Notas=line["notes"]))
    st.download_button("Descargar productos del día (CSV)", export_csv(details), "ventas_" + report_date + ".csv", "text/csv", width="stretch")
    st.download_button("Descargar respaldo completo (JSON)", json.dumps(s["events"], ensure_ascii=False, indent=2),
                       "respaldo_faro_" + day() + ".json", "application/json", width="stretch")
    closed_today = [u for d, u in s["closed"] if d == day() and (u in users or u == "__puesto__")]
    if closed_today:
        with st.expander("Reabrir una caja de hoy"):
            with st.form("reopen"):
                owner = st.selectbox("Caja", closed_today, format_func=lambda x: "Caja general" if x == "__puesto__" else x)
                reason = st.text_input("Motivo")
                if st.form_submit_button("Reabrir conservando el corte anterior"):
                    send(store, user, users, "reopen", dict(owner=owner, reason=reason))
    with st.expander("Cortes anteriores y reaperturas"):
        for event in reversed([e for e in s["events"] if e["kind"] in {"close", "reopen"}][-50:]):
            p = event["payload"]
            if event["kind"] == "close":
                st.write(f"{event['at'][:16]} · {event['actor']} · contado {money(p['counted'])} · diferencia {money(p['counted'] - p['expected'])}")
            else:
                st.write(f"{event['at'][:16]} · reapertura de {p['owner']} · {p['reason']}")


def catalog_screen(store, s, user, users):
    st.header("Productos")
    admin = user["role"] == "admin"
    options = [""] + (sorted(s["products"], key=lambda pid: sort_key(s["products"][pid]["name"])) if admin else [])
    pid = st.selectbox("Producto", options, format_func=lambda x: "＋ Crear nuevo producto" if not x else s["products"][x]["name"])
    product = s["products"].get(pid, dict(id=uid(), name="", category="Comida", price=0, active=True, portable=True))
    if not admin:
        st.caption("Puedes crear productos. Para cambiar uno existente, entra a administración.")
    with st.form("catalog_v3_" + (pid or "new")):
        name = st.text_input("Nombre completo", value=product["name"])
        categories = sorted({p["category"] for p in s["products"].values()} | {"Comida", "Tortas y cuernitos", "Café caliente", "Frappés", "Chamoyadas", "Refreshers", "Otros"})
        category = st.selectbox("Categoría", categories, index=categories.index(product["category"]) if product["category"] in categories else 0)
        price = st.number_input("Precio base ($)", min_value=0.0, value=(product["price"] or 0) / 100, step=1.0)
        active = st.toggle("Disponible para venta", value=product["active"])
        st.caption("Las tortas controlan piezas. Café caliente y té usan el mismo inventario de vasos. Los demás productos no requieren existencias.")
        if st.form_submit_button("Guardar producto", type="primary", width="stretch"):
            result = dict(id=product["id"], name=name.strip(), category=category, price=cents(price), active=active, portable=True)
            result["recipe"] = controlled_recipe(result)
            send(store, user, users, "product", result)

def corrections_screen(store, s, user, users):
    st.header("Corregir cuentas")
    customer = customer_picker(s, "admin_customer", allow_anonymous=False)
    if not customer:
        return
    cid = customer["id"]
    balance = s["balances"].get(cid, 0)
    st.metric("Saldo actual", money(balance))
    with st.expander("Ajustar saldo con motivo"):
        with st.form("adjust_balance_" + cid + str(balance)):
            direction = choice("Tipo de ajuste", ["Disminuir deuda", "Aumentar deuda"])
            amount = cents(st.number_input("Importe del ajuste ($)", min_value=0.0, step=1.0))
            reason = st.text_input("Motivo del ajuste")
            st.caption("Queda un movimiento nuevo con el motivo. El historial anterior se conserva.")
            if st.form_submit_button("Registrar ajuste", type="primary", width="stretch"):
                send(store, user, users, "balance_adjust", dict(customer=cid, amount=amount if direction == "Aumentar deuda" else -amount, expected=balance, reason=reason))
    payments = [p for p in s["payments"] if p["customer"] == cid and not p.get("refund") and p["id"] not in s["reversed_payments"]]
    with st.expander("Corregir un cobro registrado por error"):
        if not payments:
            st.info("No hay cobros reversibles registrados en esta versión.")
        else:
            by_id = {p["id"]: p for p in payments}
            selected = st.selectbox("Cobro", list(by_id), format_func=lambda i: by_id[i]["at"][:16] + " · " + money(by_id[i]["amount"]) + " · " + by_id[i]["method"])
            with st.form("reverse_" + selected):
                reason = st.text_input("Motivo de la corrección")
                confirmed = st.checkbox("Este cobro fue un error y debe volver a la deuda")
                st.caption("Si fue efectivo, se resta hoy de la caja del responsable original. El cobro anterior permanece en el historial.")
                if st.form_submit_button("Revertir cobro", disabled=not confirmed, width="stretch"):
                    send(store, user, users, "reverse_payment", dict(payment=selected, reason=reason))
    with st.expander("Historial de la cuenta", expanded=True):
        for row in reversed([x for x in s["history"] if x["customer"] == cid]):
            st.write(row["at"][:16] + " · " + row["detail"] + " · " + money(row["amount"]) + " · " + users.get(row["actor"], {}).get("nombre", row["actor"]))


def edit_sent_line(store, s, user, users, order, line):
    if line["status"] in {"Entregado", "Cancelado"}:
        return
    with st.expander("Modificar: " + line["name"]):
        fingerprint = hashlib.sha256(json_text(line).encode()).hexdigest()
        with st.form("edit_sent_" + line["id"] + fingerprint):
            qty = int(st.number_input("Cantidad", min_value=1, value=line["qty"], step=1, disabled=bool(line.get("consumed"))))
            unit = cents(st.number_input("Precio por unidad con extras ($)", min_value=line["base_price"] / 100, value=line["unit"] / 100, step=1.0))
            notes = st.text_area("Indicaciones completas para cocina", value=line["notes"], max_chars=1500)
            scheduled = st.toggle("Agendado", value=line["scheduled"])
            due = datetime.fromisoformat(line["due"])
            date_value = st.date_input("Fecha", value=max(due.date(), now().date()), min_value=now().date())
            time_value = st.time_input("Hora", value=due.time().replace(second=0, microsecond=0), step=300)
            reason = st.text_input("Motivo del cambio")
            st.caption("Se conserva la versión anterior y cocina verá un aviso. Si ya se prepara, la cantidad no se puede cambiar aquí.")
            if st.form_submit_button("Guardar modificación", type="primary", width="stretch"):
                send(store, user, users, "edit_line", dict(order=order["id"], line=line["id"], expected=fingerprint,
                    qty=qty, unit=unit, notes=notes, scheduled=scheduled,
                    due=datetime.combine(date_value, time_value, TZ).isoformat() if scheduled else now().isoformat(), reason=reason))


def admin_close_screen(store, s, user, users):
    st.subheader("Hacer corte de caja")
    owner = "__puesto__"
    if (day(), owner) in s["closed"]:
        st.info("La caja general de hoy ya tiene corte. Puedes reabrirla abajo si es necesario.")
        return
    st.caption("Corte único del puesto. Los cobros siguen desglosados por usuario.")
    expected = cash_total(s, owner, day())
    st.metric("Efectivo esperado", money(expected))
    with st.form("admin_close_" + owner + str(expected)):
        counted = cents(st.number_input("Efectivo contado ($)", min_value=0.0, step=10.0))
        reason = st.text_input("Observaciones")
        if st.form_submit_button("Guardar corte", type="primary", width="stretch"):
            send(store, user, users, "close", dict(owner=owner, expected=expected, counted=counted, reason=reason))


def ticket_text(order, kind):
    width = 32
    lines = ["=" * width, "FARO CAFE".center(width), "=" * width,
             "Pedido: " + order["folio"], "Registro: " + datetime.fromisoformat(order["at"]).strftime("%d/%m %H:%M"),
             "Cliente: " + order["customer"]["name"]]
    if order["customer"].get("reference"):
        lines.append("Lugar: " + order["customer"]["reference"])
    lines.extend(["Operador: " + order["actor"], "-" * width])
    selected = [x for x in order["lines"] if x["status"] != "Cancelado" and (kind != "cocina" or x["kitchen"])]
    for line in selected:
        lines.append(f"{line['qty']} x {line['name']}")
        if kind == "venta":
            lines.append(f" {money(line['unit'])} c/u = {money(line['unit'] * line['qty'])}")
        if line["notes"]:
            lines.append("NOTAS: " + line["notes"])
        lines.append("Entrega: " + datetime.fromisoformat(line["due"]).strftime("%d/%m %H:%M") if line["scheduled"] else "Entrega: ahora")
        lines.append("Estado: " + line["status"])
        lines.append("")
    if kind == "venta":
        lines.append("TOTAL PEDIDO: " + money(order["total"]))
        if order.get("legacy"):
            lines.append("Pedido anterior: consultar cuenta")
        else:
            lines.append("Pago inicial: " + money(order["paid"]))
            if order.get("method") == "Efectivo" and order.get("paid") and "received" in order:
                lines.append("Efectivo recibido: " + money(order["received"]))
                lines.append("Cambio: " + money(order["received"] - order["paid"]))
            lines.append("Abonos posteriores: ver cuenta")
    else:
        lines.append("COMANDA DE COCINA")
    lines.extend(["=" * width, "Gracias por tu compra", "", "", ""])
    # Ajustar todas las líneas; no truncar nombres, cantidades ni notas.
    return "\n".join("\n".join(textwrap.wrap(line, width=width, replace_whitespace=False)) if line else "" for line in lines)


def ticket_screen(store, s, user, users):
    ticket = st.session_state.get("ticket")
    if not ticket:
        return
    order = s["orders"].get(ticket["order"])
    if not order:
        st.session_state.pop("ticket", None)
        return
    st.subheader("🖨️ Ticket · " + order["folio"])
    text = ticket_text(order, ticket["type"])
    printed = s["prints"].get((order["id"], ticket["type"]))
    if printed:
        st.info("Hay una impresión confirmada: " + printed[:16] + ". Puedes reimprimir si lo necesitas.")
    st.code(text, language=None)
    url = "rawbt:" + urllib.parse.quote(text, safe="")
    # Sin auto-redirección: no dispara impresiones al cambiar de pantalla.
    st.markdown(f'<a href="{url}" style="display:block;background:#16654e;color:white;text-align:center;padding:20px;border-radius:14px;text-decoration:none;font-size:20px">Abrir RawBT e imprimir</a>', unsafe_allow_html=True)
    st.caption("Requiere RawBT e impresora compatible configurada en el teléfono. No se considera impreso hasta que tú lo confirmes.")
    st.download_button("Descargar ticket TXT", text, "ticket_" + order["folio"] + ".txt", "text/plain", width="stretch")
    if st.button("Confirmo que salió el ticket", type="primary", width="stretch"):
        send(store, user, users, "print_confirm", dict(order=order["id"], type=ticket["type"]))
    if st.button("Cerrar ticket", width="stretch"):
        st.session_state.pop("ticket", None)
        st.rerun()
    st.divider()


def history_screen(store, s, user, users):
    st.header("🧾 Pedidos e historial")
    search = st.text_input("Buscar folio, cliente o producto", key="order_search")
    active_only = st.checkbox("Solo pedidos sin terminar", value=True)
    matches = []
    for o in sorted(s["orders"].values(), key=lambda x: x["at"], reverse=True):
        if active_only and not any(x["status"] not in {"Entregado", "Cancelado"} for x in o["lines"]):
            continue
        if sort_key(search) not in sort_key(o["folio"] + " " + o["customer"]["name"] + " " + " ".join(x["name"] for x in o["lines"])):
            continue
        matches.append(o)
    st.caption(f"{len(matches)} pedidos encontrados. Se muestran hasta 50; usa el buscador para localizar uno anterior.")
    for order in matches[:50]:
        with st.expander(order["folio"] + " · " + order["customer"]["name"] + " · " + money(order["total"])):
            order_heading(order)
            for line in order["lines"]:
                st.write(f"{line['qty']} × {line['name']} · {line['status']} · {money(line['qty'] * line['unit'])}")
                if line["notes"]:
                    st.write(line["notes"])
                if user["role"] == "admin" and line["status"] != "Cancelado" and not order.get("legacy"):
                    edit_sent_line(store, s, user, users, order, line)
                    with st.expander("Cancelar: " + line["name"]):
                        with st.form("cancel_" + line["id"]):
                            reason = st.text_input("Motivo")
                            restock = st.checkbox("La mercancía está intacta y regresa a existencias", value=False)
                            st.caption("Se cancela todo este renglón. El importe reduce la cuenta; si ya pagó, queda saldo a favor y puedes devolverlo desde Cobrar.")
                            if st.form_submit_button("Confirmar cancelación"):
                                send(store, user, users, "cancel_line", dict(order=order["id"], line=line["id"], expected=line["status"], reason=reason, restock=restock))
            if st.button("Ver / reimprimir ticket", key="ht_" + order["id"], width="stretch"):
                st.session_state.ticket = dict(order=order["id"], type="venta")
                st.rerun()


def main():
    st.set_page_config(page_title="Faro Café · Caja y pedidos", page_icon="☕", layout="centered", initial_sidebar_state="collapsed")
    st.markdown(CSS, unsafe_allow_html=True)
    try:
        users = credentials_config()
        store = refresh_store_runtime(get_store())
    except RuleError as exc:
        st.error(str(exc))
        st.info("Consulta LEEME.md y secrets_ejemplo.toml. Las credenciales van en Streamlit → Settings → Secrets, nunca en GitHub.")
        st.stop()
    except Exception as exc:
        logging.error("Faro configuración: %s", type(exc).__name__)
        st.error("No se pudo conectar. Revisa Secrets, permisos de la cuenta de servicio y el nombre Base_POS o spreadsheet_id.")
        st.stop()
    if os.environ.get("FARO_DEMO") == "1":
        st.warning("DEMOSTRACIÓN · Datos en memoria. Entra con tu nombre. Admin: /admin y clave solo-demo.")
    user = login(store, users)
    restore_pending(store, user)
    pending_action(store, user, users)
    s = safe_state(store)
    for event in s["events"]:
        users.setdefault(event["actor"], dict(nombre=s["workers"].get(event["actor"], event["actor"]), rol="operador"))
    with st.sidebar:
        st.write("**" + user["name"] + "** · " + user["role"])
        st.caption("Nombre recordado por hoy. Administración: abre el panel de arriba y escribe /admin.")
        if st.button("Cambiar de persona", width="stretch"):
            if st.session_state.get("cart"):
                st.warning("Vacía o guarda el pedido antes de salir.")
            else:
                st.session_state.clear()
                st.session_state.forget_name = True
                st.rerun()
    if not s["initialized"]:
        setup_screen(store, user, users)
    if s.get("schema", 2) < 3:
        send(store, user, users, "upgrade_v3", {})
        st.stop()
    if s.get("inventory_revision", 0) < 31:
        with store.lock:
            s = safe_state(store, fresh=True)
            if s.get("inventory_revision", 0) < 31:
                send(store, user, users, "inventory_v31", {})
                st.stop()
    if s.get("inventory_revision", 0) < 32:
        with store.lock:
            s = safe_state(store, fresh=True)
            if s.get("inventory_revision", 0) < 32:
                send(store, user, users, "inventory_v32", {})
                st.stop()
    if s["workers"].get(user["id"]) != user["name"]:
        send(store, user, users, "worker", dict(name=user["name"]))
        st.stop()
    st.title("☕ Faro Café")
    st.caption(user["name"] + " · " + now().strftime("%d/%m/%Y"))
    if st.session_state.get("notice"):
        st.success(st.session_state.pop("notice"))
    if st.session_state.get("notice_error"):
        st.error(st.session_state.pop("notice_error"))
    if st.session_state.get("change_notice"):
        st.success("Cambio a entregar: " + st.session_state.pop("change_notice"))
    if st.session_state.get("nav_next"):
        st.session_state.nav = st.session_state.pop("nav_next")
    nav = choice("Sección", ["Vender", "Cobrar", "Cocina", "Entregas", "Más"], horizontal=True, key="nav", label_visibility="collapsed")
    if st.sidebar.button("↻ Actualizar datos", width="stretch"):
        store.cached = None
        st.rerun()
    ticket_screen(store, s, user, users)
    if nav == "Vender":
        sale_screen(store, s, user, users)
    elif nav == "Cocina":
        kitchen_screen(store, user, users)
    elif nav == "Entregas":
        deliveries_screen(store, user, users)
    elif nav == "Cobrar":
        payment_screen(store, s, user, users)
    else:
        options = ["Caja general", "Inventario", "Pedidos", "Catálogo"]
        if user["role"] == "admin":
            options += ["Resumen y cortes", "Corregir cuentas"]
        page = st.selectbox("Abrir", options, key="more_page")
        {"Caja general": cash_screen, "Inventario": inventory_screen, "Pedidos": history_screen,
         "Resumen y cortes": reports_screen, "Catálogo": catalog_screen, "Corregir cuentas": corrections_screen}[page](store, s, user, users)


if __name__ == "__main__":
    main()
