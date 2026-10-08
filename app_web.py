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
ADMIN_TYPES = {"bootstrap", "product", "asset", "stock_adjust", "cancel_line", "refund", "reopen"}


class RuleError(Exception):
    """El movimiento no cumple una regla de negocio; no se escribió."""


class DataError(Exception):
    """Datos incompletos o alterados: detener, nunca reemplazar por ceros."""


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
                prints={}, events=[], initialized=False, history=[])


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
    kind, p = event["kind"], event["payload"]
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
    elif kind in {"product", "asset", "customer"}:
        s[{"product": "products", "asset": "assets", "customer": "customers"}[kind]][p["id"]] = p
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
            s["payments"].append(payment)
            add_balance(s, payment["customer"], -payment["amount"], event, "Cobro " + order["folio"])
            if payment["method"] == "Efectivo":
                add_cash(s, event, actor, payment["amount"], "Cobro " + order["folio"])
    elif kind in {"payment", "refund"}:
        sign = 1 if kind == "refund" else -1
        add_balance(s, p["customer"], sign * p["amount"], event,
                    "Devolución al cliente" if sign == 1 else "Abono · " + p["method"])
        s["payments"].append(dict(**p, at=event["at"], actor=actor, id=event["id"], refund=sign == 1))
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
        s["openings"][(date, actor)] = p["amount"]
        add_cash(s, event, actor, p["amount"], "Fondo inicial")
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
        s["closed"][(date, actor)] = dict(**p, at=event["at"], actor=actor)
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
    return sum(x["amount"] for x in s["cash"] if x["owner"] == owner and x["date"] == date)


def cash_ready(s, actor, date):
    require((date, actor) in s["openings"], "Primero registra tu fondo inicial en Más → Mi caja; puede ser $0.")
    require((date, actor) not in s["closed"], "Tu caja está cerrada. Un administrador debe reabrirla.")


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
    if kind == "sale":
        require(p["id"] not in s["orders"], "Este pedido ya existe.")
        require(0 < len(p["lines"]) <= 60, "El pedido necesita entre 1 y 60 renglones.")
        require(len({x["id"] for x in p["lines"]}) == len(p["lines"]), "Hay renglones duplicados.")
        int_amount(p["paid"])
        total = 0
        needs = defaultdict(int)
        for line in p["lines"]:
            require(line["product"] in s["products"], "Un producto ya no existe.")
            product = s["products"][line["product"]]
            require(product["active"] and product["price"] is not None, "Un producto está desactivado o sin precio.")
            require(line["base_price"] == product["price"], "Cambió un precio. Actualiza ese producto en el pedido.")
            require(line["recipe"] == product["recipe"], "Cambió el control de existencias de un producto. Agrégalo de nuevo.")
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
                require(p.get("received", p["amount"]) >= p["amount"], "El efectivo recibido no alcanza para el abono.")
            cash_ready(s, actor, date)
            if kind == "refund":
                require(cash_total(s, actor, date) >= p["amount"], "No hay suficiente efectivo registrado en tu caja.")
    elif kind in {"line_state", "cancel_line"}:
        order = s["orders"].get(p["order"])
        require(order is not None, "Pedido no encontrado.")
        line = next((x for x in order["lines"] if x["id"] == p["line"]), None)
        require(line is not None, "Producto no encontrado.")
        require(line["status"] == p["expected"], "El estado cambió desde otro teléfono. Actualiza la pantalla.")
        require(line["status"] != "Cancelado", "El producto ya está cancelado.")
        if kind == "line_state":
            transitions = {"Por preparar": ["Preparando"], "Preparando": ["Listo"],
                           "Listo": ["En reparto", "Entregado"], "En reparto": ["Entregado"]}
            require(p["status"] in transitions.get(line["status"], []), "Cambio de estado no permitido.")
        else:
            require(bool(p["reason"].strip()), "Escribe el motivo de cancelación.")
            require(not order.get("legacy"), "Cancela los pedidos heredados en la revisión de transición; su importe no es reconstruible.")
    elif kind in {"product", "asset"}:
        require(p["id"] and p["name"].strip(), "Falta nombre.")
        if kind == "product":
            require(p["category"].strip(), "Falta categoría.")
            if p["price"] is not None:
                int_amount(p["price"])
            require(not p["active"] or p["price"] is not None, "Configura un precio antes de activar.")
            for asset, qty in p["recipe"].items():
                require(asset in s["assets"], "Insumo no encontrado.")
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
        for asset, qty in p["items"].items():
            require(asset in s["assets"], "Insumo desconocido.")
            if kind == "stock_adjust":
                require(type(qty) is int, "El ajuste debe ser entero.")
                require(available(s, location, asset) + qty >= 0, "El ajuste dejaría existencias negativas o sin reservas.")
            else:
                int_amount(qty)
            if kind in {"stock_loss", "stock_move"}:
                require(available(s, location, asset) >= qty, "No hay suficientes existencias libres.")
            if kind == "stock_count" and p.get("adjust"):
                require(role == "admin", "Solo el administrador puede ajustar con un conteo.")
                require(qty >= reserved(s, location, asset), "El conteo es menor que lo reservado: resuelve esos pedidos antes de ajustar.")
        if kind == "stock_move":
            require(p["target"] in LOCATIONS and p["target"] != p["source"], "Elige ubicaciones diferentes.")
        require(p.get("reason", "").strip(), "Escribe una referencia o motivo.")
        if kind == "stock_count":
            require(p["expected"] == {a: s["stock"].get((location, a), 0) for a in p["items"]},
                    "Hubo movimientos durante el conteo. Actualiza y vuelve a contar al terminar la venta.")
    elif kind == "opening":
        int_amount(p["amount"])
        require((date, actor) not in s["openings"], "Tu fondo inicial ya está registrado hoy.")
    elif kind in {"expense", "withdraw", "transfer"}:
        int_amount(p["amount"], True)
        require(p.get("reason", "").strip(), "Escribe el concepto.")
        if kind == "transfer":
            require(p["target"] in users and p["target"] != actor, "Elige otro operador.")
        else:
            require(p["method"] in METHODS, "Método inválido.")
        if kind == "transfer" or p["method"] == "Efectivo":
            cash_ready(s, actor, date)
            require(cash_total(s, actor, date) >= p["amount"], "El movimiento supera tu efectivo registrado.")
    elif kind == "transfer_accept":
        transfer = s["transfers"].get(p["transfer"])
        require(transfer and transfer["target"] == actor and transfer["status"] == "Pendiente", "Traspaso no disponible para ti.")
        cash_ready(s, actor, date)
    elif kind == "close":
        cash_ready(s, actor, date)
        int_amount(p["counted"])
        require(p["expected"] == cash_total(s, actor, date), "Tu caja cambió mientras contabas. Actualiza antes de cerrar.")
        require(not any(t["status"] == "Pendiente" and (t["sender"] == actor or t["target"] == actor)
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


def credentials_config():
    if os.environ.get("FARO_DEMO") == "1":
        return {"demo": {"nombre": "Demostración", "pin": "solo-demo", "rol": "admin"},
                "cocina": {"nombre": "Cocina demo", "pin": "solo-demo", "rol": "operador"}}
    users = {str(k): dict(v) for k, v in st.secrets.get("usuarios", {}).items()}
    if st.secrets.get("admin_password"):
        users.setdefault("admin", {"nombre": "Administrador", "pin": str(st.secrets["admin_password"]), "rol": "admin"})
    require(users and any(x.get("rol") == "admin" for x in users.values()),
            "Configura admin_password o un administrador en [usuarios] dentro de Secrets.")
    for username, user in users.items():
        require(user.get("rol") in {"admin", "operador"}, f"Revisa el rol de {username} en Secrets.")
        require(len(str(user.get("pin", ""))) >= 6, f"La clave de {username} necesita al menos 6 caracteres.")
    return users


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
.stApp {background:#f5f7f6;color:#183a31;}
.block-container {max-width:1100px;padding-top:3.5rem;padding-bottom:4rem;}
html, body, [data-testid="stMarkdownContainer"] p {font-size:17px;}
h1 {font-size:1.85rem!important;letter-spacing:-.04em;}
h2 {font-size:1.45rem!important;} h3 {font-size:1.2rem!important;}
.stButton button,.stDownloadButton button,.stLinkButton a,.stFormSubmitButton button,
[data-testid="stBaseButton-segmented_control"], [data-testid="stBaseButton-segmented_controlActive"] {
 min-height:58px!important;border-radius:12px!important;font-size:17px!important;
 font-weight:600!important;white-space:pre-wrap!important;padding:12px 16px!important;}
button p {font-size:17px!important;}
div[data-baseweb="input"],div[data-baseweb="select"] > div {min-height:52px;font-size:17px;}
button[kind="primary"] {background:#155b46;border-color:#155b46;color:white;}
[data-testid="stVerticalBlockBorderWrapper"] > div {border-radius:16px;}
[data-testid="stMetricValue"] {font-size:2rem;color:#155b46;}
.faro-account {background:#fff;border:1px solid #d9e3dd;border-left:5px solid #be7c22;
 padding:20px;border-radius:14px;margin:8px 0 14px;}
.faro-account h3 {margin:0 0 5px;font-size:1.3rem!important;color:#163f32;}
.faro-account .amount {font-size:2.3rem;font-weight:750;color:#875516;line-height:1.2;margin:8px 0;}
.faro-muted {color:#526b60;font-size:15px;}
[data-testid="stButtonGroup"] button {min-height:54px!important;border-radius:10px!important;padding:10px 16px!important;}
[data-testid="stButtonGroup"] {gap:8px;}
[data-testid="stBaseButton-segmented_controlActive"] {
 background:#e0eee7!important;border-color:#155b46!important;color:#155b46!important;}
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
    today = (day(), user["id"])
    if today in s["openings"] and today not in s["closed"]:
        return True
    st.info("Abre tu caja del día antes de recibir efectivo; el fondo puede ser $0." if today not in s["openings"]
            else "Tu caja está cerrada. Solicita al administrador que la reabra antes de recibir efectivo.")
    if st.button("Ir a mi caja", key=key, width="stretch"):
        st.session_state.nav_next = "Más"
        st.session_state.more_page = "Mi caja"
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


def login(store, users):
    current = st.session_state.get("auth")
    if current:
        expected = users.get(current["id"])
        fingerprint = hashlib.sha256(str(expected.get("pin", "")).encode()).hexdigest() if expected else ""
        if expected and hmac.compare_digest(current["fingerprint"], fingerprint):
            return dict(id=current["id"], name=expected.get("nombre", current["id"]), role=expected["rol"])
        st.session_state.clear()
    st.title("☕ Faro Café")
    st.write("Entra con tu usuario para registrar tus ventas y cobros.")
    with st.form("login"):
        user = st.text_input("Usuario", autocomplete="username").strip()
        pin = st.text_input("Clave personal", type="password", autocomplete="current-password")
        submitted = st.form_submit_button("Entrar", type="primary", width="stretch")
    if submitted:
        with store.lock:
            attempts, blocked_until = store.login_attempts.get(user, (0, 0))
            if blocked_until > time.monotonic():
                st.error("Espera un minuto antes de volver a intentar.")
            elif user in users and hmac.compare_digest(pin.encode(), str(users[user].get("pin", "")).encode()):
                store.login_attempts.pop(user, None)
                st.session_state.auth = dict(id=user, fingerprint=hashlib.sha256(pin.encode()).hexdigest())
                st.rerun()
            else:
                attempts += 1
                store.login_attempts[user] = (attempts, time.monotonic() + 60 if attempts >= 5 else 0)
                st.error("Usuario o clave incorrectos.")
    st.stop()


def finish_action(event):
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
    st.session_state.notice = "Guardado correctamente · " + event["id"][:8].upper()


def send(store, user, users, kind, payload):
    event = dict(id=uid(), at=now().isoformat(), actor=user["id"], kind=kind, payload=copy.deepcopy(payload))
    st.session_state.outbox = event
    try:
        store.commit(event, user["role"], users)
    except RuleError as exc:
        st.session_state.pop("outbox", None)
        st.error(str(exc))
        return False
    except Exception as exc:
        logging.error("Faro escritura %s: %s", event["id"], type(exc).__name__)
        st.session_state.pending_error = connection_message(exc)
        st.rerun()
    finish_action(event)
    st.rerun()


def connection_message(exc):
    """Diagnóstico sin mostrar respuestas que puedan contener credenciales."""
    if isinstance(exc, (DataError, RuleError)):
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
    return "No se pudo completar la conexión con Google Sheets. El guardado todavía no está confirmado."


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
                if not store.uncertain:
                    st.session_state.pop("outbox", None)
                    st.session_state.notice = "No se guardó: " + str(exc)
                    st.rerun()
                st.error(str(exc))
            except Exception as exc:
                st.session_state.pending_error = connection_message(exc)
                st.error(st.session_state.pending_error)
            else:
                finish_action(event)
                st.rerun()
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
    token = line["id"]
    prefix = "modifier_" + edit.setdefault("form_id", uid()) + "_"
    st.subheader(line["name"])
    with st.container(border=True):
        qty = st.number_input("Cantidad", min_value=1, max_value=100, value=line["qty"], step=1, key=prefix + "qty")
        # Cada editor tiene claves independientes: no hereda opciones de otro producto.
        cat = line.get("category", "")
        extras, extra_price = [], 0
        old = line.get("options", {})
        options = {}
        if cat in {"Frappés", "Esquimos", "Bebidas Frías", "Smoothies"}:
            milks = ["Entera", "Deslactosada", "Almendra (+$10)"]
            options["milk"] = choice("Leche", milks, index=milks.index(old.get("milk", "Entera")), key=prefix + "milk", persist_state=None)
            if options["milk"] != "Entera":
                extras.append(options["milk"].replace(" (+$10)", ""))
            if "Almendra" in options["milk"]:
                extra_price += 1000
            options["no_sprinkles"] = st.checkbox("Sin chispas", value=old.get("no_sprinkles", False), key=prefix + "no_sprinkles")
            if options["no_sprinkles"]:
                extras.append("Sin chispas")
            if cat == "Frappés":
                options["no_cream"] = st.checkbox("Sin crema batida", value=old.get("no_cream", False), key=prefix + "no_cream")
                if options["no_cream"]:
                    extras.append("Sin crema batida")
            adorns = ["Sin adorno", "Lechera", "Hershey's", "Caramelo"]
            options["adorn"] = st.selectbox("Vaso adornado", adorns, index=adorns.index(old.get("adorn", "Sin adorno")), key=prefix + "adorn")
            if options["adorn"] != "Sin adorno":
                extras.append("Vaso: " + options["adorn"])
        if cat in {"Chamoyadas", "Refreshers"}:
            options["pearls"] = st.checkbox("Perlas explosivas (+$10)", value=old.get("pearls", False), key=prefix + "pearls")
            if options["pearls"]:
                extra_price += 1000
                extras.append("Perlas explosivas")
            if cat == "Chamoyadas":
                options["no_gummies"] = st.checkbox("Sin gomitas", value=old.get("no_gummies", False), key=prefix + "no_gummies")
                options["no_stick"] = st.checkbox("Sin banderilla", value=old.get("no_stick", False), key=prefix + "no_stick")
                if options["no_gummies"]:
                    extras.append("Sin gomitas")
                if options["no_stick"]:
                    extras.append("Sin banderilla")
        if "chilaquiles" in canon(line["name"]):
            options["salsa"] = choice("Salsa", ["Verdes", "Rojos"], index=0 if old.get("salsa", "Verdes") == "Verdes" else 1, key=prefix + "salsa", persist_state=None)
            extras.append("Salsa: " + options["salsa"])
            st.caption("Si lleva telera adicional, agrégala como producto: se cobra y descuenta del inventario.")
        note = st.text_input("Indicaciones", value=line.get("free_note", ""), max_chars=300, key=prefix + "note")
        later = st.toggle("Programar para después", value=line["scheduled"], key=prefix + "later")
        due = datetime.fromisoformat(line["due"])
        date_input, hour_input = due.date(), due.time()
        if later:
            date_input = st.date_input("Fecha de entrega", value=max(due.date(), now().date()), min_value=now().date(), key=prefix + "date")
            hour_input = st.time_input("Hora de entrega", value=due.time().replace(second=0, microsecond=0), step=300, key=prefix + "time")
            st.caption("El pedido se enviará a cocina con esta hora de entrega.")
        unit = line["base_price"] + extra_price
        st.metric("Total de este producto", money(unit * int(qty)))
        st.caption(f"{int(qty)} × {money(unit)} · Base {money(line['base_price'])} + extras {money(extra_price)} por unidad")
        if extras:
            st.write(" · ".join(extras))
        action = "Agregar al pedido" if edit["new"] else "Guardar cambios"
        if st.button(action + " · " + money(unit * int(qty)), type="primary", width="stretch", key=prefix + "save"):
            line.update(qty=int(qty), options=options, free_note=note, notes=" · ".join(extras + ([note] if note else [])),
                        unit=line["base_price"] + extra_price, scheduled=later)
            if later:
                dt = datetime.combine(date_input, hour_input, TZ)
                if dt < now() - timedelta(minutes=1):
                    st.error("Elige una hora futura.")
                    return
                line.update(due=dt.isoformat(), location="Puesto", kitchen=True, status="Por preparar")
            else:
                line["due"] = now().isoformat()
            if edit["new"]:
                add_to_cart(line)
            else:
                st.session_state.cart = [line if x["id"] == token else x for x in st.session_state.cart]
            st.session_state.pop("editing")
            st.session_state.sale_stage_next = "Productos" if edit["new"] else "Cobrar pedido"
            st.rerun()
    if st.button("Volver sin cambios", width="stretch", key=prefix + "cancel"):
        st.session_state.pop("editing")
        st.session_state.sale_stage_next = "Productos" if edit["new"] else "Cobrar pedido"
        st.rerun()


def sale_screen(store, s, user, users):
    st.header("Nueva venta")
    st.session_state.setdefault("cart", [])
    if st.session_state.get("editing"):
        editor_screen(s)
        return
    if st.session_state.get("last_order") in s["orders"]:
        oid = st.session_state.last_order
        with st.expander("Último pedido · " + s["orders"][oid]["folio"]):
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
    with st.expander("Punto de venta y preparación"):
        origin = choice("Estoy vendiendo desde", LOCATIONS, horizontal=True, key="sale_origin")
        mode = choice("¿De dónde sale el producto?", ["Lo entrego aquí", "Pedir a cocina"], horizontal=True, key="sale_mode")
    st.caption(origin + " · " + mode)
    kitchen = mode == "Pedir a cocina"
    if stage == "Productos":
        products = [x for x in s["products"].values() if x["active"] and x["price"] is not None and (kitchen or x["portable"])]
        search = st.text_input("Buscar producto o sabor", placeholder="Ejemplo: red velvet, milanesa…", key="product_search")
        if search.strip():
            query = sort_key(search.strip())
            products = [x for x in products if query in sort_key(x["name"] + " " + x["category"])]
        else:
            categories = sorted({x["category"] for x in products}, key=sort_key)
            if categories:
                current = st.session_state.get("category", categories[0])
                if current not in categories:
                    st.session_state.pop("category", None)
                current = choice("Categoría", categories, key="category")
                products = [x for x in products if x["category"] == current]
        if not products:
            st.info("No hay productos en esta selección. Revisa el catálogo o cambia a Pedir a cocina.")
        cols = st.columns(2)
        location = "Puesto" if kitchen else origin
        for i, product in enumerate(sorted(products, key=lambda x: sort_key(x["name"]))):
            stock = min((available(s, location, a) // qty for a, qty in product["recipe"].items()), default=None)
            label = product["name"] + "\n" + money(product["price"])
            if stock is not None:
                label += "\n" + ("Agotado" if stock <= 0 else f"Disponibles: {stock}")
            if cols[i % 2].button(label, key="prod_" + product["id"], width="stretch", disabled=stock is not None and stock <= 0):
                line = make_line(product, location, kitchen)
                if product["category"] in {"Frappés", "Esquimos", "Bebidas Frías", "Chamoyadas", "Refreshers", "Smoothies"} or "chilaquiles" in canon(product["name"]):
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
            st.caption(("Cocina" if line["kitchen"] else "Entrega aquí · " + line["location"]) +
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
            change = st.toggle("Calcular cambio", key="sale_change_" + version)
            tender = cents(st.number_input("Efectivo recibido ($)", min_value=0.0, step=10.0, key="sale_tender_" + version)) if change else paid
            st.caption("Pago exacto" if not change else "Cambio: " + money(max(0, tender - paid)))
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
    st.caption("Última consulta: " + now().strftime("%H:%M:%S") + " · actualización cada 10 segundos mientras esta pantalla está abierta")
    lead = st.select_slider("Anticipación para preparar", options=[5, 10, 15, 20, 30, 45, 60], value=15)
    mode = choice("Mostrar", ["Ahora", "Programados", "Todo"], horizontal=True, key="k_mode")
    older = sum(1 for o in s["orders"].values() if o.get("legacy") and any(
        x["kitchen"] and x["status"] in {"Por preparar", "Preparando"} for x in o["lines"]))
    source = choice("Pedidos", ["Actuales", "Anteriores"], key="k_source",
                    format_func=lambda x: f"Anteriores ({older})" if x == "Anteriores" else x)
    if source == "Anteriores":
        st.caption("Pedidos importados conservados para revisión. Esta vista no cambia saldos ni marca entregas automáticamente.")
    entries = []
    for order in s["orders"].values():
        if bool(order.get("legacy")) != (source == "Anteriores"):
            continue
        lines = [x for x in order["lines"] if x["kitchen"] and x["status"] in {"Por preparar", "Preparando"}]
        for line in lines:
            due = datetime.fromisoformat(line["due"])
            upcoming = due > now() + timedelta(minutes=lead) and line["status"] == "Por preparar"
            if mode == "Todo" or (mode == "Programados" and upcoming) or (mode == "Ahora" and not upcoming):
                entries.append((due, order, line))
    entries.sort(key=lambda x: (x[0], x[1]["at"]))
    previous = st.session_state.get("known_kitchen")
    ids = {line["id"] for _, _, line in entries}
    if previous is not None and ids - previous:
        st.toast("Llegaron productos nuevos a esta vista de cocina", icon="🔔")
    st.session_state.known_kitchen = ids
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
            st.write("Estado: **" + line["status"] + "**")
            next_state = "Preparando" if line["status"] == "Por preparar" else "Listo"
            if st.button("Aceptar y preparar" if next_state == "Preparando" else "✅ Marcar listo", key="k_" + line["id"], type="primary", width="stretch"):
                send(store, user, users, "line_state", dict(order=order["id"], line=line["id"], expected=line["status"], status=next_state))
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
        lines = [x for x in order["lines"] if x["status"] in {"Listo", "En reparto"}]
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
                change = st.toggle("Calcular cambio", key=prefix + "change")
                if change:
                    tender = cents(st.number_input("Efectivo recibido ($)", min_value=0.0, step=10.0, key=prefix + "tender"))
                    st.metric("Cambio a entregar", money(max(0, tender - amount)))
                else:
                    st.caption("Pago exacto · activa Calcular cambio si te entregan más.")
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
        with st.expander("Editar ubicación para identificar al cliente"):
            with st.form("reference_" + selected):
                reference = st.text_input("Referencia", value=customer.get("reference", ""))
                if st.form_submit_button("Guardar ubicación", width="stretch"):
                    send(store, user, users, "customer", dict(customer, reference=reference.strip()))
        return

    debtors = [c for c in s["customers"].values() if s["balances"].get(c["id"], 0) > 0]
    total, count = st.columns(2)
    total.metric("Por cobrar", money(sum(s["balances"][c["id"]] for c in debtors)))
    count.metric("Personas con deuda", len(debtors))
    search = st.text_input("Buscar por nombre o ubicación", placeholder="Ejemplo: Brenda, puesto azul…", key="debt_search")
    order = choice("Ordenar", ["Mayor deuda", "Nombre", "Ubicación"], key="debt_order")
    all_clients = st.toggle("Incluir cuentas liquidadas y saldos a favor", key="debt_all")
    clients = [c for c in s["customers"].values() if (not c.get("anonymous") or s["balances"].get(c["id"], 0) != 0)
               and (all_clients or s["balances"].get(c["id"], 0) > 0)
               and sort_key(search.strip()) in sort_key(c["name"] + " " + c.get("reference", ""))]
    clients.sort(key=lambda c: ((-s["balances"].get(c["id"], 0) if order == "Mayor deuda" else
                                sort_key(c.get("reference", "")) if order == "Ubicación" else sort_key(c["name"])), sort_key(c["name"])))
    filters = (search, order, all_clients)
    if st.session_state.get("debt_filters") != filters:
        st.session_state.debt_filters = filters
        st.session_state.debt_page = 0
    pages = max(1, (len(clients) + 11) // 12)
    page = min(st.session_state.get("debt_page", 0), pages - 1)
    st.caption(f"{len(clients)} cuentas · página {page + 1} de {pages} · toca una para cobrar")
    for c in clients[page * 12:(page + 1) * 12]:
        balance = s["balances"].get(c["id"], 0)
        status = money(balance) + " por cobrar" if balance > 0 else "Liquidado" if balance == 0 else money(-balance) + " a favor"
        label = c["name"] + "  ·  " + status
        if c.get("reference"):
            label += "\n" + c["reference"]
        if st.button(label, key="debtor_" + c["id"], width="stretch"):
            st.session_state.selected_debtor = c["id"]
            st.rerun()
    if pages > 1:
        prev, nxt = st.columns(2)
        if prev.button("← Anterior", disabled=page == 0, width="stretch"):
            st.session_state.debt_page = page - 1
            st.rerun()
        if nxt.button("Siguiente →", disabled=page + 1 >= pages, width="stretch"):
            st.session_state.debt_page = page + 1
            st.rerun()
    if not clients:
        st.info("No hay cuentas que coincidan con esta búsqueda.")
    new_customer_form(store, s, user, users, "pay_")


def inventory_screen(store, s, user, users):
    st.header("📦 Inventario y carga del carrito")
    location = choice("Ver existencias de", LOCATIONS, horizontal=True)
    rows = []
    for asset in sorted(s["assets"].values(), key=lambda a: sort_key(a["name"])):
        a = asset["id"]
        rows.append({"Producto / insumo": asset["name"], "Físico esperado": s["stock"].get((location, a), 0),
                     "Reservado": reserved(s, location, a), "Libre para vender": available(s, location, a)})
    st.dataframe(rows, hide_index=True, width="stretch")
    st.caption("Una orden de cocina reserva existencias; al empezar a prepararla se consumen. Las ventas de entrega inmediata se descuentan al guardar.")
    mode = choice("Movimiento", ["Entrada de mercancía", "Cargar / regresar carrito", "Merma o cortesía", "Conteo físico"], key="stock_mode")
    source = location
    target = "Puesto" if source == "Carrito" else "Carrito"
    if mode == "Cargar / regresar carrito":
        st.info("Mover de " + source + " a " + target + ". No es una compra ni una venta.")
    names = {a["id"]: a["name"] for a in s["assets"].values()}
    chosen = st.multiselect("Productos o insumos que vas a registrar", sorted(names, key=lambda a: sort_key(names[a])), format_func=lambda a: names[a])
    with st.form("stock_operation_" + mode + source):
        quantities = {}
        for asset in chosen:
            quantities[asset] = int(st.number_input(names[asset], min_value=0, value=0, step=1, key="sq_" + mode + source + asset))
        reason = st.text_input("Referencia / motivo", placeholder="Carga de la mañana, compra de vasos, derrame…")
        adjust = False
        if mode == "Conteo físico":
            st.caption("Cuenta cuando no estén vendiendo. El conteo deja evidencia de faltantes y sobrantes.")
            if user["role"] == "admin":
                adjust = st.checkbox("Usar el conteo como nuevo saldo físico; conservar la diferencia registrada")
        if st.form_submit_button("Registrar movimiento", type="primary", width="stretch"):
            if not quantities:
                st.error("Selecciona por lo menos un producto o insumo.")
            elif mode == "Conteo físico":
                send(store, user, users, "stock_count", dict(location=source, items=quantities, reason=reason,
                     expected={a: s["stock"].get((source, a), 0) for a in chosen}, adjust=adjust))
            else:
                quantities = {a: q for a, q in quantities.items() if q > 0}
                if mode == "Cargar / regresar carrito":
                    send(store, user, users, "stock_move", dict(source=source, target=target, items=quantities, reason=reason))
                else:
                    kind = "stock_load" if mode == "Entrada de mercancía" else "stock_loss"
                    send(store, user, users, kind, dict(location=source, items=quantities, reason=reason))
    with st.expander("Últimos conteos y diferencias"):
        for count in reversed(s["counts"][-15:]):
            st.write(f"**{count['at'][:16]} · {count['location']} · {count['actor']}**")
            st.caption(count["reason"] + (" · Saldo ajustado" if count["adjust"] else " · Solo registro"))
            st.dataframe([{"Producto": names.get(x["asset"], x["asset"]), "Esperado": x["expected"],
                           "Contado": x["counted"], "Diferencia": x["difference"]} for x in count["rows"]], hide_index=True)


def cash_screen(store, s, user, users):
    actor, date = user["id"], day()
    st.header("💵 Mi caja")
    st.write("Responsable: **" + user["name"] + "** · " + date)
    if (date, actor) not in s["openings"]:
        with st.form("opening_" + date):
            amount = st.number_input("Cambio con el que empiezas hoy ($)", min_value=0.0, step=50.0)
            st.caption("No es una venta. Si empiezas sin cambio, registra $0. El dinero del día anterior que conserves también se incluye aquí.")
            if st.form_submit_button("Iniciar mi caja", type="primary", width="stretch"):
                send(store, user, users, "opening", dict(amount=cents(amount)))
        return
    expected = cash_total(s, actor, date)
    st.metric("Efectivo que deberías tener", money(expected))
    closed = s["closed"].get((date, actor))
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
        mode = choice("Registrar", ["Entrega de dinero", "Gasto", "Retiro", "Corte"], horizontal=True)
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
                st.caption("Solo los movimientos en efectivo se restan de tu caja. Una compra aquí no suma inventario; registra también su entrada.")
                if st.form_submit_button("Registrar " + mode.lower(), type="primary", width="stretch"):
                    send(store, user, users, "expense" if mode == "Gasto" else "withdraw", dict(amount=cents(amount), reason=reason, method=method))
        else:
            with st.form("cash_close_" + str(expected)):
                counted = st.number_input("Efectivo que contaste físicamente ($)", min_value=0.0, step=10.0)
                reason = st.text_input("Observaciones del corte")
                st.write("Esperado: **" + money(expected) + "**")
                st.caption("El corte conserva pedidos, cuentas por cobrar e historial. Confirma tus traspasos primero.")
                if st.form_submit_button("Guardar corte de mi caja", type="primary", width="stretch"):
                    send(store, user, users, "close", dict(expected=expected, counted=cents(counted), reason=reason))
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
    owners = set(users) | {x["owner"] for x in s["cash"] if x["date"] == report_date}
    cash_rows = []
    for owner in sorted(owners):
        close = s["closed"].get((report_date, owner))
        cash_rows.append({"Responsable": users.get(owner, {}).get("nombre", owner),
                         "Fondo inicial": money(s["openings"].get((report_date, owner), 0)),
                         "Efectivo esperado": money(cash_total(s, owner, report_date)),
                         "Contado": money(close["counted"]) if close else "Sin corte",
                         "Diferencia": money(close["counted"] - close["expected"]) if close else ""})
    st.subheader("Dinero por responsable")
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
    closed_today = [u for d, u in s["closed"] if d == day() and u in users]
    if closed_today:
        with st.expander("Reabrir una caja de hoy"):
            with st.form("reopen"):
                owner = st.selectbox("Responsable", closed_today)
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
    st.header("⚙️ Catálogo")
    st.caption("Los nuevos productos sin precio están desactivados. Actívalos cuando confirmes precio y presentación.")
    pending = [p["name"] for p in s["products"].values() if p["price"] is None]
    if pending:
        st.info("Por configurar: " + ", ".join(pending))
    with st.expander("Agregar insumo controlado: vasos por tamaño, tortas, pan…"):
        with st.form("asset"):
            asset_name = st.text_input("Nombre del insumo", placeholder="Vaso caliente 12 oz")
            if st.form_submit_button("Crear insumo"):
                send(store, user, users, "asset", dict(id=uid(), name=asset_name.strip(), unit="pieza"))
    options = [""] + sorted(s["products"], key=lambda pid: sort_key(s["products"][pid]["category"] + " " + s["products"][pid]["name"]))
    pid = st.selectbox("Producto a editar", options, format_func=lambda x: "＋ Crear nuevo producto" if not x else s["products"][x]["category"] + " · " + s["products"][x]["name"])
    product = s["products"].get(pid, dict(id=uid(), name="", category="", price=0, portable=False, active=False, recipe={}))
    with st.form("product_" + (pid or "new")):
        name = st.text_input("Nombre completo", value=product["name"], placeholder="Frappé de Red Velvet")
        category = st.text_input("Categoría", value=product["category"], placeholder="Frappés")
        price = st.number_input("Precio ($)", min_value=0.0, value=(product["price"] or 0) / 100, step=1.0)
        active = st.checkbox("Disponible para venta", value=product["active"])
        portable = st.checkbox("Se puede entregar directamente sin preparación en cocina", value=product["portable"])
        st.write("Insumos por unidad vendida")
        st.caption("1 vaso por bebida, 1 pieza por torta; 0 significa que no consume ese insumo. Para tamaños distintos crea productos separados.")
        recipe = {}
        for asset in sorted(s["assets"].values(), key=lambda a: sort_key(a["name"])):
            qty = int(st.number_input(asset["name"], min_value=0, max_value=100, value=product["recipe"].get(asset["id"], 0), step=1, key="recipe_" + (pid or "new") + asset["id"]))
            if qty:
                recipe[asset["id"]] = qty
        if st.form_submit_button("Guardar producto", type="primary", width="stretch"):
            send(store, user, users, "product", dict(id=product["id"], name=name.strip(), category=category.strip(),
                                                  price=cents(price), active=active, portable=portable, recipe=recipe))


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
        store = get_store()
    except RuleError as exc:
        st.error(str(exc))
        st.info("Consulta LEEME.md y secrets_ejemplo.toml. Las credenciales van en Streamlit → Settings → Secrets, nunca en GitHub.")
        st.stop()
    except Exception as exc:
        logging.error("Faro configuración: %s", type(exc).__name__)
        st.error("No se pudo conectar. Revisa Secrets, permisos de la cuenta de servicio y el nombre Base_POS o spreadsheet_id.")
        st.stop()
    if os.environ.get("FARO_DEMO") == "1":
        st.warning("DEMOSTRACIÓN · Los datos están en memoria y se pierden al reiniciar. Usuario demo / clave solo-demo.")
    user = login(store, users)
    restore_pending(store, user)
    pending_action(store, user, users)
    s = safe_state(store)
    with st.sidebar:
        st.write("**" + user["name"] + "** · " + user["role"])
        st.caption("Cada persona debe usar su propia cuenta.")
        if st.button("Salir de mi cuenta", width="stretch"):
            if st.session_state.get("cart"):
                st.warning("Vacía o guarda el pedido antes de salir.")
            else:
                st.session_state.clear()
                st.rerun()
    if not s["initialized"]:
        setup_screen(store, user, users)
    st.title("☕ Faro Café")
    st.caption(user["name"] + " · " + now().strftime("%d/%m/%Y"))
    if st.session_state.get("notice"):
        st.success(st.session_state.pop("notice"))
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
        options = ["Mi caja", "Inventario", "Pedidos"]
        if user["role"] == "admin":
            options += ["Resumen y cortes", "Catálogo"]
        page = st.selectbox("Abrir", options, key="more_page")
        {"Mi caja": cash_screen, "Inventario": inventory_screen, "Pedidos": history_screen,
         "Resumen y cortes": reports_screen, "Catálogo": catalog_screen}[page](store, s, user, users)


if __name__ == "__main__":
    main()
