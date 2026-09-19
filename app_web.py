import streamlit as st
import gspread
import json
import pandas as pd
import re
import urllib.parse
from datetime import datetime, timedelta, timezone

# ==========================================
# 1. CONFIGURACIÓN VISUAL Y CSS ERGONÓMICO
# ==========================================
st.set_page_config(page_title="POS Faro Café", layout="wide", initial_sidebar_state="collapsed")

st.markdown("""
<style>
    html, body, [class*="css"], .stMarkdown, p, span, label, div { font-size: 19px !important; }
    h1 { font-size: 32px !important; font-weight: 800 !important; }
    h2 { font-size: 26px !important; font-weight: 700 !important; }
    h3 { font-size: 22px !important; font-weight: 700 !important; }
    
    .prod-container div.stButton > button { 
        min-height: 110px !important; border-radius: 20px !important; border: 2px solid #005A9E !important; 
        font-weight: 700 !important; background-color: #FFFFFF !important; color: #002244 !important; 
        font-size: 21px !important; box-shadow: 0 4px 10px rgba(0,0,0,0.1) !important;
        white-space: pre-wrap !important; line-height: 1.25 !important;
    }
    .prod-container div.stButton > button:hover { border-color: #003B66 !important; background-color: #F0F7FF !important; }
    .prod-container div.stButton > button:active { background-color: #CCE5FF !important; transform: scale(0.95) !important; }

    div[role="radiogroup"] { gap: 12px !important; flex-wrap: wrap; }
    div[role="radiogroup"] label {
        background-color: #F8FBFF !important; border: 2px solid #005A9E !important; border-radius: 20px !important;
        padding: 10px 20px !important; font-weight: 700 !important; cursor: pointer !important;
        display: flex !important; justify-content: center !important; align-items: center !important;
    }
    div[role="radiogroup"] label input, div[role="radiogroup"] label div[data-testid="stMarkdownContainer"] pre, div[role="radiogroup"] .st-b7, div[role="radiogroup"] .st-b8, div[role="radiogroup"] svg { display: none !important; }
    div[role="radiogroup"] label[data-baseweb="radio"][aria-checked="true"] { background-color: #005A9E !important; }
    div[role="radiogroup"] label[data-baseweb="radio"][aria-checked="true"] div { color: #FFFFFF !important; }

    div.stButton > button[kind="primary"] { 
        background-color: #005A9E !important; color: white !important; border: none !important; font-size: 22px !important;
        min-height: 85px !important; font-weight: 800 !important; border-radius: 20px !important; box-shadow: 0 4px 8px rgba(0,0,0,0.2) !important;
    }
    .btn-imprimir {
        display: block; width: 100%; background-color: #28A745; color: white !important; text-align: center;
        padding: 20px; font-size: 24px; font-weight: 900; border-radius: 20px; text-decoration: none;
        box-shadow: 0 4px 10px rgba(0,0,0,0.2); margin-bottom: 20px;
    }
    .btn-imprimir:hover { background-color: #218838; }

    .cat-container div.stButton > button { min-height: 65px !important; border-radius: 15px !important; font-size: 20px !important; font-weight: 700 !important; box-shadow: 0 2px 5px rgba(0,0,0,0.05) !important; }
    .card { background-color: #FFFFFF; padding: 20px; border-radius: 20px; margin-bottom: 20px; box-shadow: 0 4px 12px rgba(0,0,0,0.08); border: 2px solid #E1E4E8; }
    .card-urgente { border: 4px solid #D93025; background-color: #FFF2F2; }
    .card-programado { border: 4px solid #005A9E; background-color: #F0F7FF; }
    .card-personalizar { border: 4px solid #FF8C00; background-color: #FFFDF0; border-radius: 20px; padding: 24px; margin-bottom: 20px; }
    .card-borrador { background-color: #F8FBFF; border: 3px dashed #005A9E; padding: 20px; border-radius: 20px; margin-bottom: 20px; }
    .sticky-header { position: sticky; top: 0; background-color: white; z-index: 999; padding: 10px 0 15px 0; border-bottom: 2px solid #E1E4E8; }
</style>
""", unsafe_allow_html=True)

zona_mx = timezone(timedelta(hours=-6))
hoy_obj = datetime.now(zona_mx).date()
hoy_str = hoy_obj.strftime("%d/%m/%Y")
semana_str = f"{(hoy_obj - timedelta(days=hoy_obj.weekday())).strftime('%d/%m')} al {(hoy_obj - timedelta(days=hoy_obj.weekday()) + timedelta(days=6)).strftime('%d/%m')}"

# ==========================================
# 2. CONEXIÓN Y LECTURA
# ==========================================
@st.cache_resource
def conectar():
    try:
        c = json.loads(st.secrets["google_credentials"], strict=False)
        return gspread.service_account_from_dict(c).open("Base_POS")
    except Exception as e: return None
sh = conectar()

@st.cache_data(ttl=600)
def leer():
    if not sh: return [], [], [], [], []
    try: return sh.worksheet("Operaciones").get_all_values(), sh.worksheet("Inventario").get_all_values(), sh.worksheet("Deudas").get_all_values(), sh.worksheet("Historial").get_all_values(), (sh.worksheet("Gastos").get_all_values() if "Gastos" in [w.title for w in sh.worksheets()] else [])
    except: return [], [], [], [], []

ops, inv, deu, hist, gas = leer()

if len(ops) > 1:
    ops_to_update = [{"row": i, "val": "Preparando" if f[2] == "Cocina" else "Listo"} for i, f in enumerate(ops[1:], start=2) if len(f) > 8 and f[6] == "Pendiente" and f[8] == hoy_str]
    if ops_to_update:
        try:
            ws_ops = sh.worksheet("Operaciones")
            for u in ops_to_update: ws_ops.update_cell(u["row"], 7, u["val"])
            st.cache_data.clear(); ops, inv, deu, hist, gas = leer()
        except: pass

# ==========================================
# 3. MEMORIA DE ESTADOS Y UTILIDADES
# ==========================================
if 'cajero' not in st.session_state:
    if st.query_params.get("u", ""): st.session_state.cajero = st.query_params.get("u", "")
    else:
        st.title("Control de Acceso")
        nom = st.text_input("Nombre del Operador/Cajero:")
        if st.button("Iniciar Turno", type="primary") and nom.strip():
            st.query_params["u"] = nom.strip(); st.session_state.cajero = nom.strip(); st.rerun()
        st.stop()

for s in ['cart', 'puesto_cart', 'cat_apagadas']: 
    if s not in st.session_state: st.session_state[s] = []
if 'admin_mode' not in st.session_state: st.session_state.admin_mode = False
if 'borrador_voz' not in st.session_state: st.session_state.borrador_voz = None
if 'prod_edit' not in st.session_state: st.session_state.prod_edit = None
if 'ticket_imprimir' not in st.session_state: st.session_state.ticket_imprimir = None
if 'cat_activa' not in st.session_state: st.session_state.cat_activa = "Café"
if 'cat_puesto_activa' not in st.session_state: st.session_state.cat_puesto_activa = "Café"

# Función generadora de texto para la Zebra
def generar_texto_ticket(cliente, pago, items, total, tipo="venta"):
    t = "================================\n"
    t += "           FARO CAFE\n"
    t += "================================\n"
    t += f"FECHA: {hoy_str}   HORA: {datetime.now(zona_mx).strftime('%H:%M')}\n"
    t += f"CLIENTE: {cliente.upper()}\n"
    if tipo == "venta": t += f"COBRO: {pago.upper()}\n"
    t += "--------------------------------\n\n"
    
    for i in items:
        # PRODUCTO GIGANTE Y DESTACADO
        t += f"== {i['cant']}x {i['prod'].upper()} ==\n"
        # Notas resaltadas
        if i['notas']:
            t += f"   >> EXTRAS: {i['notas']}\n"
        if i.get('tiempo') == "🕒 Prog.":
            t += f"   >> ENTREGAR A LAS: {i['hora']} HRS\n"
        t += "\n"
        
    t += "--------------------------------\n"
    if tipo == "venta":
        t += f"TOTAL: $ {total:.2f}\n"
    else:
        t += "TICKET DE PRODUCCION - COCINA\n"
    t += "================================\n\n\n"
    return t

# ==========================================
# 4. CONSTRUCCIÓN DINÁMICA DEL MENÚ
# ==========================================
MENU_BASE = {
    "Café": {"Vainilla": 25.0, "Avellana": 25.0, "Clásico": 25.0, "Crema irlandesa": 30.0, "Caramelo": 30.0, "Canela": 30.0, "Te": 25.0},
    "Frappés": {"Fresa": 65.0, "Taro": 65.0, "Chai": 65.0, "Matcha": 65.0, "Rompope": 65.0, "Red Velvet": 65.0, "Pistache": 65.0, "Galleta": 65.0, "Mora": 65.0, "Cereza": 65.0, "Refresher Darks": 65.0, "Cafe": 65.0, "Moka": 65.0, "Oreo": 65.0, "Chocolate": 65.0},
    "Bebidas Frías": {"Fresa": 45.0, "Taro": 45.0, "Chai": 45.0, "Matcha": 45.0, "Rompope": 45.0, "Red Velvet": 45.0, "Pistache": 45.0, "Galleta": 45.0, "Mora": 45.0, "Cereza": 45.0, "Refresher Darks": 45.0, "Cafe": 45.0, "Moka": 45.0, "Oreo": 45.0, "Chocolate": 45.0},
    "Esquimos": {"Fresa": 45.0, "Taro": 45.0, "Chai": 45.0, "Matcha": 45.0, "Rompope": 45.0, "Red Velvet": 45.0, "Pistache": 45.0, "Galleta": 45.0, "Mora": 45.0, "Cereza": 45.0, "Refresher Darks": 45.0, "Cafe": 45.0, "Moka": 45.0, "Oreo": 45.0, "Chocolate": 45.0},
    "Chamoyadas": {"Fresa": 65.0, "Mango": 65.0, "Temporada": 65.0},
    "Refreshers": {"Fresa": 50.0, "Mango": 50.0, "Mora": 50.0, "Limón": 50.0},
    "Platillos": {"Ensalada": 65.0, "Sandwich": 65.0, "Plato de Chilaquiles": 50.0, "Torta de Chilaquiles": 65.0},
    "Tortas": {},
    "Panadería": {"Pan de Dulce": 25.0, "Telera": 5.0}
}

def armar_nombre(cat, base):
    if cat == "Café": return f"Café {base}" if "Café" not in base else base
    if cat == "Frappés": return f"Frappé de {base}"
    if cat == "Esquimos": return f"Esquimo de {base}"
    if cat == "Bebidas Frías": return f"Bebida Fría de {base}"
    if cat == "Chamoyadas": return f"Chamoyada de {base}"
    if cat == "Refreshers": return f"Refresher de {base}"
    return base

MENU = {k: v.copy() for k, v in MENU_BASE.items()}
dict_inv, fila_producto_map = {}, {}

if len(inv) > 1:
    for row_idx, row in enumerate(inv[1:], start=2):
        if len(row) >= 4 and (row[4].strip().lower() if len(row) >= 5 else "activo") == "activo":
            prod, cat, precio_str = row[0].strip(), row[2].strip() if len(row) > 2 else "General", row[3].strip() if len(row) > 3 else "0"
            fila_producto_map[prod] = row_idx
            if "Tortas" in cat: dict_inv[prod] = int(row[1].strip() if len(row) > 1 else 0)
            else: dict_inv[prod] = None
            if cat not in MENU: MENU[cat] = {}
            try: MENU[cat][prod] = float(precio_str)
            except: MENU[cat][prod] = 0.0

if not MENU.get("Tortas") and "Tortas" in MENU: del MENU["Tortas"]

clientes_unicos = sorted(list(set([f[0] for f in ops[1:] if len(f)>0 and f[0] not in ["", "Mostrador"]] + [f[0] for f in deu[1:] if len(f)>0 and f[0] not in ["", "Mostrador"]])))
TODOS_LOS_PRODUCTOS = {p_k: {"precio": p_v, "cat": cat_k} for cat_k, p_dict in MENU.items() for p_k, p_v in p_dict.items()}
LISTA_NOMBRES_PRODUCTOS = sorted(list(TODOS_LOS_PRODUCTOS.keys()))
if st.session_state.cat_activa not in MENU and len(MENU) > 0: st.session_state.cat_activa = list(MENU.keys())[0]

# ==========================================
# 6. SIDEBAR Y TABS
# ==========================================
with st.sidebar:
    st.write(f"**Operador:** {st.session_state.cajero}")
    if st.text_input("Credencial Admin:", type="password") == "1234": st.session_state.admin_mode = True
    else: st.session_state.admin_mode = False

pestanas = ["🛒 Carrito", "⚡ Caja Rápida", "👨‍🍳 Cocina", "🚚 Entregas", "📅 Agendados", "💳 Pagos", "📦 Inventario"]
if st.session_state.admin_mode: pestanas.extend(["⚙️ Admin", "💸 Gastos"])
tabs = st.tabs(pestanas)

# ==========================================
# VISOR GLOBAL DE IMPRESIÓN (Aparece arriba si hay ticket pendiente)
# ==========================================
if st.session_state.ticket_imprimir:
    url_impresion = "rawbt:" + urllib.parse.quote(st.session_state.ticket_imprimir)
    st.markdown(f'<a href="{url_impresion}" target="_blank" class="btn-imprimir">🖨️ TOCAR AQUÍ PARA IMPRIMIR TICKET EN LA ZEBRA</a>', unsafe_allow_html=True)
    if st.button("✅ Ocultar Botón de Impresión", use_container_width=True):
        st.session_state.ticket_imprimir = None
        st.rerun()
    st.divider()

# ==========================================
# PESTAÑA 1: CARRITO
# ==========================================
with tabs[0]:
    # POP-UP DE PERSONALIZACIÓN
    if st.session_state.prod_edit:
        pe = st.session_state.prod_edit
        st.markdown(f'<div class="card-personalizar"><h2>⚙️ {armar_nombre(pe["cat"], pe["nombre"])}</h2>', unsafe_allow_html=True)
        if st.button("⚡ AGREGAR CLÁSICO (Saltar)", type="primary", use_container_width=True):
            st.session_state.cart.append({"prod": armar_nombre(pe["cat"], pe["nombre"]), "precio": pe["precio"], "notas": "Clásico", "pan": False, "cat": pe["cat"], "tiempo": "⚡ Ahora", "hora": "", "cant": 1})
            st.session_state.prod_edit = None; st.rerun()
            
        c_p1, c_p2 = st.columns(2)
        notas_extra, precio_final = [], pe["precio"]
        
        if pe["cat"] in ["Frappés", "Esquimos", "Bebidas Frías"]:
            with c_p1:
                leche = st.radio("Leche:", ["Entera", "Deslactosada", "Almendra (+$10)"], key="mod_leche")
                if leche == "Almendra (+$10)": precio_final += 10; notas_extra.append("Almendra")
                elif leche == "Deslactosada": notas_extra.append("Deslactosada")
                chispas = st.radio("Chispas:", ["Con chispas", "Sin chispas"], key="mod_chispas")
                if chispas == "Sin chispas": notas_extra.append("Sin chispas")
            with c_p2:
                if pe["cat"] == "Frappés":
                    crema = st.radio("Crema Batida:", ["Con crema", "Sin crema"], key="mod_crema")
                    if crema == "Sin crema": notas_extra.append("Sin crema")
                adorno = st.selectbox("Vaso Adornado:", ["Sin Adorno", "Lechera", "Hershey's", "Caramelo"], key="mod_adorno")
                if adorno != "Sin Adorno": notas_extra.append(f"Vaso: {adorno}")
                
        elif pe["cat"] == "Chamoyadas":
            with c_p1: banderilla = st.radio("Banderilla:", ["Con banderilla", "Sin banderilla"], key="mod_band")
            with c_p2: gomitas = st.radio("Gomitas:", ["Con gomitas", "Sin gomitas"], key="mod_gom")
            perlas = st.radio("Perlas Explosivas:", ["Sin Perlas", "Con Perlas (+$10)"], key="mod_perl_cham")
            if banderilla == "Sin banderilla": notas_extra.append("Sin banderilla")
            if gomitas == "Sin gomitas": notas_extra.append("Sin gomitas")
            if perlas == "Con Perlas (+$10)": precio_final += 10; notas_extra.append("Con Perlas Explosivas")
            
        elif pe["cat"] == "Refreshers":
            perlas = st.radio("Extra:", ["Normal", "Perlas Explosivas (+$10)"], key="mod_perlas")
            if perlas == "Perlas Explosivas (+$10)": precio_final += 10; notas_extra.append("Con Perlas Explosivas")
                
        st.write("")
        if st.button(f"✅ AGREGAR PERSONALIZADO (${precio_final})", use_container_width=True):
            st.session_state.cart.append({"prod": armar_nombre(pe["cat"], pe["nombre"]), "precio": precio_final, "notas": ", ".join(notas_extra), "pan": False, "cat": pe["cat"], "tiempo": "⚡ Ahora", "hora": "", "cant": 1})
            st.session_state.prod_edit = None; st.rerun()
            
        if st.button("❌ Cancelar"): st.session_state.prod_edit = None; st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)
        st.stop() 

    # CATEGORÍAS
    st.markdown('<div class="sticky-header"><div class="cat-container">', unsafe_allow_html=True)
    lista_cats = list(MENU.keys())
    for fila_i in range(0, len(lista_cats), 3):
        col_c = st.columns(3)
        for idx_col, cat_n in enumerate(lista_cats[fila_i:fila_i+3]):
            with col_c[idx_col]:
                if st.button(cat_n, key=f"btn_cat_{cat_n}", use_container_width=True, type="primary" if st.session_state.cat_activa == cat_n else "secondary"):
                    st.session_state.cat_activa = cat_n; st.rerun()
    st.markdown('</div></div>', unsafe_allow_html=True)

    # PRODUCTOS
    st.markdown('<div class="prod-container">', unsafe_allow_html=True)
    c_prod = st.columns(2)
    for i, (n, p) in enumerate(MENU[st.session_state.cat_activa].items()):
        stock, btn_txt, agotado = dict_inv.get(n, None), f"{n}\n${p}", False
        if stock is not None: 
            if stock <= 0: btn_txt += "\n(AGOTADO)"; agotado = True
            else: btn_txt += f"\n(Quedan {stock})"
            
        with c_prod[i%2]:
            if st.button(btn_txt, use_container_width=True, disabled=agotado, key=f"p_{st.session_state.cat_activa}_{n}"):
                if st.session_state.cat_activa in ["Frappés", "Esquimos", "Chamoyadas", "Bebidas Frías", "Refreshers"]:
                    st.session_state.prod_edit = {"nombre": n, "cat": st.session_state.cat_activa, "precio": p}
                else:
                    st.session_state.cart.append({"prod": armar_nombre(st.session_state.cat_activa, n), "precio": p, "notas": "", "pan": False, "cat": st.session_state.cat_activa, "tiempo": "⚡ Ahora", "hora": "", "cant": 1})
                st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

    with st.expander("Añadir Cargo Extra Manual"):
        c_mot, c_mon = st.columns([3,1])
        with c_mot: mot = st.text_input("Concepto (Extra):")
        with c_mon: mon = st.number_input("$", step=5.0)
        if st.button("Aplicar Extra", use_container_width=True) and mot:
            st.session_state.cart.append({"prod": f"Extra: {mot}", "precio": mon, "notas": "", "pan": False, "cat": "Extra", "tiempo": "⚡ Ahora", "hora": "", "cant": 1}); st.rerun()

    # RESUMEN Y COBRO
    if st.session_state.cart:
        st.divider()
        st.subheader("🧾 Ticket de Compra")
        total = 0
        for idx, item in enumerate(st.session_state.cart):
            st.markdown(f"**▪ {item['prod']} (${item['precio']})**")
            c1, c2, c3 = st.columns([1.5, 1, 1])
            with c1: 
                if "Chilaquiles" in item['prod']:
                    cs1, cs2 = st.columns(2)
                    with cs1: item['salsa'] = st.selectbox("Salsa", ["Verdes", "Rojos"], key=f"salsa_{idx}", label_visibility="collapsed")
                    with cs2: item['pan'] = st.checkbox("Con Telera", key=f"cpan_{idx}")
                else: item['notas'] = st.text_input("Notas", value=item['notas'], key=f"cn_{idx}", label_visibility="collapsed", placeholder="Notas")
            with c2: item['tiempo'] = st.radio("Horario", ["⚡ Ahora", "🕒 Prog."], index=0 if item.get('tiempo', "⚡ Ahora") == "⚡ Ahora" else 1, horizontal=True, key=f"tr_{idx}", label_visibility="collapsed")
            with c3:
                if item['tiempo'] == "🕒 Prog.": item['hora'] = st.time_input("Hora", key=f"th_{idx}", label_visibility="collapsed").strftime("%H:%M")
                else: item['hora'] = "Ahora"
                    
            if item.get('cat') == "Platillos": 
                item['dest'] = "Cocina"
                st.markdown("<p style='color:#D93025; font-size:14px; margin:0;'>👨‍🍳 Va a cocina directo</p>", unsafe_allow_html=True)
            else: item['dest'] = "Cocina" if st.checkbox("Mandar a Cocina", value=False, key=f"cd_{idx}") else "Entrega Directa"
            total += item['precio']; st.write("---")
            
        st.write(f"### Total a Cobrar: ${total}")
        c_cli, c_pago = st.columns([2, 1])
        with c_cli:
            opc = st.selectbox("Buscar cliente:", [""] + clientes_unicos)
            cliente = st.text_input("Nombre / Referencia:", value=opc, placeholder="Escriba aquí...")
        with c_pago: pago = st.radio("Estado de Pago:", ["Pagado", "Pendiente"], index=0, horizontal=True)

        if st.button("❌ Descartar Orden", use_container_width=True): st.session_state.cart = []; st.rerun()
        
        c_e1, c_e2 = st.columns(2)
        with c_e1:
            if st.button("🚀 GUARDAR Y ENVIAR", type="primary", use_container_width=True):
                if not cliente.strip() and pago == "Pendiente" and not st.session_state.admin_mode: st.warning("Nombre obligatorio para deuda.")
                else:
                    nom_final = cliente.strip() if cliente.strip() else "Mostrador"
                    panes = sum(1 for i in st.session_state.cart if "telera" in i['prod'].lower() or i.get('pan', False))
                    for i in st.session_state.cart:
                        t_est = "Preparando" if i['dest'] == "Cocina" else "Entregado"
                        t_bd = "Inmediato" if i['tiempo'] == "⚡ Ahora" else "Definir Hora"
                        h_bd = datetime.now(zona_mx).strftime("%H:%M") if t_bd == "Inmediato" else i['hora']
                        notas_final = i.get('notas', '')
                        if "Chilaquiles" in i['prod']: notas_final = f"Salsa: {i.get('salsa', 'Verdes')} | Pan: {'Sí (Telera)' if i.get('pan') else 'No'} | " + notas_final
                        sh.worksheet("Operaciones").append_row([nom_final, i['prod'], i['dest'], notas_final, t_bd, h_bd, t_est, total if i == st.session_state.cart[0] else 0, hoy_str, st.session_state.cajero])
                    if pago == "Pendiente": sh.worksheet("Deudas").append_row([nom_final, "Deuda", total, ", ".join([i['prod'] for i in st.session_state.cart]), hoy_str])
                    if len(inv) > 1:
                        for idx, row in enumerate(inv[1:], start=2):
                            p_nom = row[0]
                            try: p_stock = int(row[1])
                            except: p_stock = 0
                            comprados = sum(1 for p in st.session_state.cart if p_nom in p['prod'])
                            if comprados > 0: sh.worksheet("Inventario").update_cell(idx, 2, p_stock - comprados)
                            if p_nom == "Telera" and panes > 0: sh.worksheet("Inventario").update_cell(idx, 2, p_stock - panes)
                    st.session_state.cart = []; leer.clear(); st.success("¡Registrado!"); st.rerun()
        
        with c_e2:
            if st.button("🖨️ GUARDAR E IMPRIMIR TICKET", type="primary", use_container_width=True):
                if not cliente.strip() and pago == "Pendiente" and not st.session_state.admin_mode: st.warning("Nombre obligatorio para deuda.")
                else:
                    nom_final = cliente.strip() if cliente.strip() else "Mostrador"
                    panes = sum(1 for i in st.session_state.cart if "telera" in i['prod'].lower() or i.get('pan', False))
                    ticket_items = []
                    for i in st.session_state.cart:
                        t_est = "Preparando" if i['dest'] == "Cocina" else "Entregado"
                        t_bd = "Inmediato" if i['tiempo'] == "⚡ Ahora" else "Definir Hora"
                        h_bd = datetime.now(zona_mx).strftime("%H:%M") if t_bd == "Inmediato" else i['hora']
                        notas_final = i.get('notas', '')
                        if "Chilaquiles" in i['prod']: notas_final = f"Salsa: {i.get('salsa', 'Verdes')} | Pan: {'Sí (Telera)' if i.get('pan') else 'No'} | " + notas_final
                        sh.worksheet("Operaciones").append_row([nom_final, i['prod'], i['dest'], notas_final, t_bd, h_bd, t_est, total if i == st.session_state.cart[0] else 0, hoy_str, st.session_state.cajero])
                        ticket_items.append({"prod": i['prod'], "cant": 1, "notas": notas_final, "tiempo": i['tiempo'], "hora": h_bd})
                    
                    if pago == "Pendiente": sh.worksheet("Deudas").append_row([nom_final, "Deuda", total, ", ".join([i['prod'] for i in st.session_state.cart]), hoy_str])
                    if len(inv) > 1:
                        for idx, row in enumerate(inv[1:], start=2):
                            p_nom = row[0]
                            try: p_stock = int(row[1])
                            except: p_stock = 0
                            comprados = sum(1 for p in st.session_state.cart if p_nom in p['prod'])
                            if comprados > 0: sh.worksheet("Inventario").update_cell(idx, 2, p_stock - comprados)
                            if p_nom == "Telera" and panes > 0: sh.worksheet("Inventario").update_cell(idx, 2, p_stock - panes)
                    
                    # Generar Ticket para RawBT
                    st.session_state.ticket_imprimir = generar_texto_ticket(nom_final, pago, ticket_items, total, "venta")
                    st.session_state.cart = []; leer.clear(); st.rerun()

# ==========================================
# PESTAÑA 2: CAJA RÁPIDA (Bebidas Directas)
# ==========================================
with tabs[1]:
    st.header("⚡ Caja Rápida")
    categorias_puesto = [c for c in MENU.keys() if any(x in c for x in ["Café", "Frappés", "Bebidas", "Esquimos", "Chamoyadas", "Refreshers"])]
    if st.session_state.cat_puesto_activa not in categorias_puesto and categorias_puesto: st.session_state.cat_puesto_activa = categorias_puesto[0]

    if categorias_puesto:
        st.markdown('<div class="cat-container">', unsafe_allow_html=True)
        cols_p_cat = st.columns(3)
        for idx_cp, cat_p_nom in enumerate(categorias_puesto):
            with cols_p_cat[idx_cp % 3]:
                if st.button(cat_p_nom, key=f"btn_cp_{cat_p_nom}", use_container_width=True, type="primary" if st.session_state.cat_puesto_activa == cat_p_nom else "secondary"):
                    st.session_state.cat_puesto_activa = cat_p_nom; st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)
        
        st.markdown('<div class="prod-container">', unsafe_allow_html=True)
        cat_p = st.session_state.cat_puesto_activa
        cols_p = st.columns(2)
        for i, (n, p) in enumerate(MENU[cat_p].items()):
            with cols_p[i%2]:
                if st.button(f"{n}\n${p}", use_container_width=True, key=f"puesto_{cat_p}_{n}"):
                    st.session_state.puesto_cart.append({"prod": armar_nombre(cat_p, n), "precio": p, "notas": "", "cant": 1}); st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)
                    
        if st.session_state.puesto_cart:
            st.divider()
            total_p = 0
            for idx, item in enumerate(st.session_state.puesto_cart):
                c1, c2, c3 = st.columns([3, 1, 2])
                with c1: st.write(f"▪ {item['prod']}")
                with c2: st.write(f"${item['precio']}")
                with c3: item['notas'] = st.text_input("Notas", key=f"pn_{idx}", label_visibility="collapsed")
                total_p += item['precio']
                
            st.write(f"### Total Rápido: ${total_p}")
            c_nom_p, c_pag_p = st.columns([1.5, 1])
            with c_nom_p: cliente_p = st.text_input("Nombre cliente:", placeholder="Para llevar...")
            with c_pag_p: pago_p = st.radio("Cobro:", ["Pagado", "Pendiente"], index=0, horizontal=True, key="pag_p")
            
            c_bp, c_ep1, c_ep2 = st.columns([1, 1, 1])
            with c_bp:
                if st.button("❌ Descartar", key="b_puesto", use_container_width=True): st.session_state.puesto_cart = []; st.rerun()
            with c_ep1:
                if st.button("🚀 GUARDAR", type="primary", use_container_width=True):
                    nom_final_p = cliente_p.strip() if cliente_p.strip() else "Mostrador"
                    for i in st.session_state.puesto_cart:
                        sh.worksheet("Operaciones").append_row([nom_final_p, i['prod'], "Entrega Directa", i['notas'], "Inmediato", datetime.now(zona_mx).strftime("%H:%M"), "Entregado", total_p if i == st.session_state.puesto_cart[0] else 0, hoy_str, st.session_state.cajero])
                    if pago_p == "Pendiente": sh.worksheet("Deudas").append_row([nom_final_p, "Deuda", total_p, ", ".join([i['prod'] for i in st.session_state.puesto_cart]), hoy_str])
                    st.session_state.puesto_cart = []; leer.clear(); st.rerun()
            with c_ep2:
                if st.button("🖨️ IMPRIMIR", type="primary", use_container_width=True):
                    nom_final_p = cliente_p.strip() if cliente_p.strip() else "Mostrador"
                    for i in st.session_state.puesto_cart:
                        sh.worksheet("Operaciones").append_row([nom_final_p, i['prod'], "Entrega Directa", i['notas'], "Inmediato", datetime.now(zona_mx).strftime("%H:%M"), "Entregado", total_p if i == st.session_state.puesto_cart[0] else 0, hoy_str, st.session_state.cajero])
                    if pago_p == "Pendiente": sh.worksheet("Deudas").append_row([nom_final_p, "Deuda", total_p, ", ".join([i['prod'] for i in st.session_state.puesto_cart]), hoy_str])
                    st.session_state.ticket_imprimir = generar_texto_ticket(nom_final_p, pago_p, st.session_state.puesto_cart, total_p, "venta")
                    st.session_state.puesto_cart = []; leer.clear(); st.rerun()

# ==========================================
# PESTAÑA 3: COCINA CON TEMPORIZADOR Y TICKET
# ==========================================
with tabs[2]:
    st.header("👨‍🍳 Monitor de Producción")
    st.markdown("""<audio autoplay="true"><source src="https://www.soundjay.com/misc/sounds/bell-ringing-05.mp3" type="audio/mpeg"></audio>""", unsafe_allow_html=True)
    
    if len(ops) > 1:
        pedidos_cocina = [ (i, f) for i, f in enumerate(ops[1:], start=2) if len(f) > 8 and f[8] == hoy_str and f[6] == "Preparando" and f[2] == "Cocina" ]
        pedidos_cocina.sort(key=lambda x: (x[1][4] != "Inmediato", x[1][5])) 
        
        if pedidos_cocina:
            # BOTON GIGANTE PARA IMPRIMIR COMANDAS DE COCINA
            if st.button("🖨️ IMPRIMIR ESTAS COMANDAS EN LA ZEBRA", type="primary", use_container_width=True):
                items_cocina = []
                for _, f in pedidos_cocina: items_cocina.append({"prod": f[1], "cant": 1, "notas": f[3], "tiempo": ("🕒 Prog." if f[4]!="Inmediato" else "⚡ Ahora"), "hora": f[5]})
                st.session_state.ticket_imprimir = generar_texto_ticket("COCINA", "N/A", items_cocina, 0, "cocina")
                st.rerun()
                
            for i, f in pedidos_cocina:
                urgente = False
                if f[4] == "Inmediato": urgente = True
                else:
                    try: 
                        dt_pedido = datetime.combine(hoy_obj, datetime.strptime(f[5], "%H:%M").time()).replace(tzinfo=zona_mx)
                        if (dt_pedido - datetime.now(zona_mx)) <= timedelta(minutes=15): urgente = True
                    except: pass
                    
                css_clase = "card-urgente" if urgente else "card-programado"
                
                st.markdown(f'<div class="card {css_clase}">', unsafe_allow_html=True)
                if urgente: st.markdown("<h2 style='color:#D93025; font-weight:900;'>🔥 ENTREGAR AHORA (INMEDIATO)</h2>", unsafe_allow_html=True)
                else: st.markdown(f"<h2 style='color:#005A9E; font-weight:800;'>🕒 PREPARAR PARA: {f[5]} HRS</h2>", unsafe_allow_html=True)
                    
                st.markdown(f"<h3>{f[1]}</h3><p><b>Cliente:</b> {f[0]}</p><p style='font-size:22px; font-weight:bold;'>Extras/Notas: <span style='color:#D93025;'><i>{f[3]}</i></span></p>", unsafe_allow_html=True)
                
                c_listo, c_canc = st.columns([3,1])
                with c_listo:
                    if st.button(f"✅ MARCAR COMO LISTO", key=f"l_{i}", use_container_width=True, type="primary"):
                        sh.worksheet("Operaciones").update_cell(i, 7, "Listo"); leer.clear(); st.rerun()
                with c_canc:
                    if st.session_state.admin_mode:
                        if st.button("🗑️ Cancelar", key=f"fc_{i}", use_container_width=True):
                            sh.worksheet("Operaciones").delete_rows(i); leer.clear(); st.rerun()
                st.markdown('</div>', unsafe_allow_html=True)
        else:
            st.info("Cocina despejada.")

# ==========================================
# PESTAÑA 4: ENTREGAS
# ==========================================
with tabs[3]:
    st.header("🚚 Entregas Pendientes")
    hay_listos = False
    if len(ops) > 1:
        for i, f in enumerate(ops[1:], start=2):
            if len(f) > 8 and f[8] == hoy_str and f[6] == "Listo":
                hay_listos = True
                st.success(f"**{f[1]}** | Para: {f[0]} | Notas: {f[3]}")
                if st.button("🚀 Entregado al cliente", key=f"ent_{i}", type="primary"):
                    sh.worksheet("Operaciones").update_cell(i, 7, "Entregado"); leer.clear(); st.rerun()
    if not hay_listos: st.write("No hay órdenes esperando.")

# ==========================================
# PESTAÑA 5: AGENDADOS (FORMULARIO Y SECCIONES)
# ==========================================
with tabs[4]:
    st.header("📅 Agenda de Órdenes")
    
    with st.expander("📝 Registrar Nueva Orden Futura"):
        with st.form("form_agenda"):
            a_cli = st.text_input("Cliente:")
            a_prod = st.selectbox("Producto:", LISTA_NOMBRES_PRODUCTOS)
            a_cant = st.number_input("Cantidad:", min_value=1, value=1)
            c_fd, c_fh = st.columns(2)
            with c_fd: a_fecha = st.date_input("Día de Entrega:", min_value=hoy_obj)
            with c_fh: a_hora = st.time_input("Hora de Entrega:")
            
            c_nd, c_cp = st.columns(2)
            with c_nd: a_notas = st.text_input("Notas Especiales:")
            with c_cp: a_dest = st.selectbox("Preparar en:", ["Cocina", "Entrega Directa"], index=0 if TODOS_LOS_PRODUCTOS[a_prod]["cat"] == "Platillos" else 1)
            
            a_pago = st.radio("Cobro:", ["Pagado", "Pendiente"], horizontal=True)
            
            if st.form_submit_button("Agendar Pedido", type="primary"):
                if not a_cli.strip() and a_pago == "Pendiente": st.warning("Falta nombre.")
                else:
                    n_f = a_cli.strip() if a_cli.strip() else "Mostrador"
                    f_str = a_fecha.strftime("%d/%m/%Y")
                    h_str = a_hora.strftime("%H:%M")
                    tot_a = TODOS_LOS_PRODUCTOS[a_prod]["precio"] * a_cant
                    
                    for _ in range(a_cant):
                        est_a = "Pendiente" if a_fecha > hoy_obj else ("Preparando" if a_dest == "Cocina" else "Listo")
                        sh.worksheet("Operaciones").append_row([n_f, armar_nombre(TODOS_LOS_PRODUCTOS[a_prod]["cat"], a_prod), a_dest, a_notas, "Definir Hora", h_str, est_a, tot_a if _ == 0 else 0, f_str, st.session_state.cajero])
                    
                    if a_pago == "Pendiente": sh.worksheet("Deudas").append_row([n_f, "Deuda", tot_a, f"{a_cant}x {a_prod}", hoy_str])
                    leer.clear(); st.success("¡Agendado exitosamente!"); st.rerun()

    st.divider()
    hoy_list, manana_list, futuro_list = [], [], []
    
    if len(ops) > 1:
        for i, f in enumerate(ops[1:], start=2):
            if len(f) > 8 and f[6] == "Pendiente":
                try: fp = datetime.strptime(f[8], "%d/%m/%Y").date()
                except: fp = hoy_obj 
                
                if fp == hoy_obj: hoy_list.append((i, f))
                elif fp == hoy_obj + timedelta(days=1): manana_list.append((i, f))
                elif fp > hoy_obj + timedelta(days=1): futuro_list.append((i, f))
                
        def mostrar_agendados(lista, titulo):
            if lista:
                st.markdown(f"### {titulo}")
                for i, f in lista:
                    st.info(f"**{f[1]}** | Fecha: {f[8]} | Hora: {f[5]} | Cliente: {f[0]}")
                    if st.session_state.admin_mode:
                        with st.expander(f"Modificar {f[1]} (Admin)"):
                            n_cli = st.text_input("Cliente", value=f[0], key=f"ac_{i}")
                            n_fec = st.text_input("Fecha (DD/MM/YYYY)", value=f[8], key=f"af_{i}")
                            if st.button("Guardar Cambios", key=f"ag_{i}"):
                                sh.worksheet("Operaciones").update_cell(i, 1, n_cli); sh.worksheet("Operaciones").update_cell(i, 9, n_fec); leer.clear(); st.rerun()
                            if st.button("Eliminar Registro", key=f"ab_{i}"):
                                sh.worksheet("Operaciones").delete_rows(i); leer.clear(); st.rerun()

        mostrar_agendados(hoy_list, "🔥 Para Hoy (Pendientes de pasar a cocina)")
        mostrar_agendados(manana_list, "⏳ Para Mañana")
        mostrar_agendados(futuro_list, "📅 Futuros")

# ==========================================
# PESTAÑA 6: PAGOS CON HISTORIAL LIMPIO
# ==========================================
with tabs[5]:
    st.header("💳 Control de Crédito")
    if len(deu) > 1:
        lista_nombres = sorted(list(set(f[0] for f in deu[1:] if len(f) > 0 and f[0] != "")))
        busqueda = st.selectbox("Buscar Cliente:", ["Todos los saldos"] + lista_nombres)
        
        c_res = {}
        for i, f in enumerate(deu[1:], start=2):
            if len(f) >= 3:
                c, tipo = f[0], f[1]
                try: m = float(f[2])
                except: m = 0.0
                if c not in c_res: c_res[c] = {"tot": 0, "hist": []}
                if tipo == "Deuda": c_res[c]["tot"] += m
                else: c_res[c]["tot"] -= m
                c_res[c]["hist"].append({"idx": i, "data": f})
            
        for c, info in c_res.items():
            if (busqueda == "Todos los saldos" or c == busqueda) and round(info["tot"], 2) > 0:
                with st.expander(f"{c} - Saldo Pendiente: ${round(info['tot'], 2)}", expanded=(busqueda!="Todos los saldos")):
                    
                    last_abono_idx = 0
                    for idx_h, h in enumerate(info["hist"]):
                        if h["data"][1] == "Abono": last_abono_idx = idx_h
                    hist_mostrar = info["hist"][last_abono_idx:]
                    
                    for h in hist_mostrar:
                        fila, det = h["data"], h["data"][3] if len(h["data"])>3 else ""
                        try: fec_corta = datetime.strptime(fila[4], "%d/%m/%Y").strftime("%d/%m") if len(fila)>4 else ""
                        except: fec_corta = ""
                        st.write(f"{'[-] Cargo' if fila[1]=='Deuda' else '[+] Abono'} **${fila[2]}** ({fec_corta}) - *{det}*")
                    
                    st.divider()
                    c_ab, c_btn = st.columns(2)
                    with c_ab: abono = st.number_input(f"Abonar:", min_value=0.0, max_value=float(info['tot']), value=float(info['tot']), key=f"n_{c}")
                    with c_btn:
                        if st.button("Registrar Pago", key=f"p_{c}", type="primary", use_container_width=True):
                            sh.worksheet("Deudas").append_row([c, "Abono", abono, "Pago", hoy_str]); leer.clear(); st.rerun()

# ==========================================
# PESTAÑA 7: INVENTARIO Y PRECIOS
# ==========================================
with tabs[6]:
    st.header("📦 Inventario y Precios")
    with st.expander("💲 MODIFICAR PRECIOS DEL MENÚ (En la Nube)", expanded=True):
        cp_sel, cp_val = st.columns([2, 1])
        with cp_sel: prod_ed = st.selectbox("Producto:", LISTA_NOMBRES_PRODUCTOS, key="edit_p_sel")
        with cp_val: n_pre = st.number_input("Precio ($):", value=float(TODOS_LOS_PRODUCTOS[prod_ed]["precio"]), step=1.0, key="edit_p_val")

        if st.button("💾 Guardar Precio", type="primary", use_container_width=True):
            try:
                if prod_ed in fila_producto_map: sh.worksheet("Inventario").update_cell(fila_producto_map[prod_ed], 4, n_pre)
                else: sh.worksheet("Inventario").append_row([prod_ed, "", TODOS_LOS_PRODUCTOS[prod_ed]["cat"], n_pre, "Activo"])
                leer.clear(); st.success(f"¡Actualizado a ${n_pre}!"); st.rerun()
            except: st.error("Error al guardar.")

    st.divider()
    with st.expander("Alta de Nuevo Producto"):
        with st.form("add_inv"):
            n_p = st.text_input("Nombre:")
            n_c = st.selectbox("Categoría:", list(MENU_BASE.keys()))
            n_pr = st.number_input("Precio ($):", step=5.0)
            n_s = st.number_input("Stock Inicial:", min_value=0, value=10) if "Tortas" in n_c else ""
            if st.form_submit_button("Guardar"):
                if n_p.strip(): sh.worksheet("Inventario").append_row([n_p, n_s, n_c, n_pr, "Activo"]); leer.clear(); st.rerun()
                else: st.warning("Falta nombre.")

    st.divider()
    if len(inv) > 1:
        with st.form("inv_form"):
            st.write("Conteo físico (Productos controlados):")
            cols, nv, ctd = st.columns(4), {}, 0
            for i, f in enumerate(inv[1:], start=2):
                if len(f) >= 5 and (f[4].strip().lower() if len(f)>4 else "activo") == "activo" and "Tortas" in f[2]:
                    try: val = int(f[1])
                    except: val = 0
                    with cols[ctd % 4]: nv[i] = st.number_input(f[0], value=val, min_value=0, key=f"ui_inv_{i}")
                    ctd += 1
            if ctd == 0: st.info("No hay productos con límite.")
            if st.form_submit_button("Actualizar Conteos"):
                for i, v in nv.items(): sh.worksheet("Inventario").update_cell(i, 2, v)
                leer.clear(); st.rerun()

# ==========================================
# PESTAÑA 8: MODO ADMIN
# ==========================================
if st.session_state.admin_mode:
    with tabs[7]:
        st.header("⚙️ Admin")
        cortes = [{"Fecha": r[0], "Ingreso": float(r[2].split("$")[1].replace(",", "").strip())} for r in hist if len(r)>2 and ("Corte" in r[1] or "Cierre" in r[1])] if len(hist)>1 else []
        if cortes: st.bar_chart(pd.DataFrame(cortes[-6:]).set_index("Fecha"))
        else: st.info("No hay cortes.")
            
        st.divider()
        if st.button("EJECUTAR CORTE DE CAJA HOY", type="primary"):
            try:
                f_man, v_hoy, tkts = [ops[0]], 0, 0
                for f in ops[1:]:
                    if len(f) > 8 and f[8] == hoy_str and f[6] == "Entregado":
                        try: v_hoy += float(f[7])
                        except: pass
                        tkts += 1
                    else: f_man.append(f)
                if tkts > 0:
                    sh.worksheet("Historial").append_row([hoy_str, "Cierre Diario", f"Ingreso Total: ${v_hoy}", f"Transacciones: {tkts}"])
                    sh.worksheet("Operaciones").clear(); sh.worksheet("Operaciones").update("A1", f_man)
                    leer.clear(); st.success(f"Corte procesado: ${v_hoy}"); st.rerun()
                else: st.info("Sin operaciones para cierre.")
            except Exception as e: st.error(f"Falla: {e}")
                
        st.divider()
        if len(ops) > 0:
            edited_df = st.data_editor(pd.DataFrame(ops[1:], columns=ops[0]), num_rows="dynamic", use_container_width=True)
            if st.button("Forzar Sincronización DB"):
                sh.worksheet("Operaciones").clear(); sh.worksheet("Operaciones").update("A1", [edited_df.fillna("").columns.values.tolist()] + edited_df.fillna("").values.tolist())
                leer.clear(); st.rerun()

# ==========================================
# PESTAÑA 9: GASTOS
# ==========================================
if st.session_state.admin_mode:
    with tabs[8]:
        st.header("💸 Gastos")
        with st.form("form_gastos"):
            g_conc = st.text_input("Concepto:")
            cc, cu, cp = st.columns(3)
            with cc: g_cant = st.number_input("Cantidad:", min_value=0.0, step=0.5, value=1.0)
            with cu: g_uni = st.selectbox("Unidad:", ["Pieza(s)", "Kg", "Litro(s)", "Paquete(s)"])
            with cp: g_precio = st.number_input("Precio Uni ($):", min_value=0.0, step=10.0, value=0.0)
            tot_g = g_cant * g_precio
            if st.form_submit_button("Registrar Gasto"):
                if g_conc.strip() and tot_g > 0:
                    try: sh.worksheet("Gastos").append_row([hoy_str, g_conc, g_cant, g_uni, g_precio, tot_g, semana_str]); leer.clear(); st.rerun()
                    except: st.error("Falta pestaña 'Gastos'.")
                else: st.warning("Datos inválidos.")
                    
        g_val = [f[:7] for f in gas if len(f)>=7 and f[0]!="Fecha"]
        if g_val:
            df_g = pd.DataFrame(g_val, columns=["Fecha", "Concepto", "Cantidad", "Unidad", "Precio_Unitario", "Total", "Semana"])
            df_g['Total'] = pd.to_numeric(df_g['Total'], errors='coerce').fillna(0)
            for sem in reversed(df_g['Semana'].unique()): 
                df_s = df_g[df_g['Semana'] == sem]
                with st.expander(f"Semana: {sem} --- Total: ${df_s['Total'].sum()}"):
                    for _, r in df_s.iterrows(): st.write(f"- {r['Fecha']}: **{r['Concepto']}** ({r['Cantidad']} {r['Unidad']}) ➔ **${r['Total']}**")
