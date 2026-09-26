import streamlit as st
import streamlit.components.v1 as components
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

# Formato ancho 58mm (Max 32 caracteres por renglón)
def generar_texto_ticket(cliente, pago, items, total, tipo="venta"):
    t = "================================\n"
    t += "           FARO CAFE\n"
    t += "================================\n"
    t += f"Fec: {hoy_str}   Hor: {datetime.now(zona_mx).strftime('%H:%M')}\n"
    t += f"Cli: {cliente.upper()[:27]}\n"
    if tipo == "venta": t += f"Cobro: {pago.upper()}\n"
    t += "--------------------------------\n\n"
    
    for i in items:
        t += f"{i['cant']}x {i['prod'].upper()}\n"
        if i.get('notas'):
            t += f"  > EXT: {i['notas'].replace(' [IMP]', '')}\n"
        if i.get('tiempo') == "🕒 Prog.":
            t += f"  > ENTREGA: {i.get('hora', '')} HRS\n"
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
    "Platillos": {"Ensalada": 75.0, "Sandwich": 65.0, "Plato de Chilaquiles": 55.0, "Torta de Chilaquiles": 45.0}, 
    "Tortas": {},
    "Panadería": {"Pan de Dulce": 25.0, "Telera": 5.0},
    "Bebidas Frías": {"Fresa": 45.0, "Taro": 45.0, "Chai": 45.0, "Matcha": 45.0, "Rompope": 45.0, "Red Velvet": 45.0, "Pistache": 45.0, "Galleta": 45.0, "Mora": 45.0, "Cereza": 45.0, "Refresher Darks": 45.0, "Cafe": 45.0, "Moka": 45.0, "Oreo": 45.0, "Chocolate": 45.0},
    "Frappés": {"Fresa": 65.0, "Taro": 65.0, "Chai": 65.0, "Matcha": 65.0, "Rompope": 65.0, "Red Velvet": 65.0, "Pistache": 65.0, "Galleta": 65.0, "Mora": 65.0, "Cereza": 65.0, "Refresher Darks": 65.0, "Cafe": 65.0, "Moka": 65.0, "Oreo": 65.0, "Chocolate": 65.0},
    "Esquimos": {"Fresa": 45.0, "Taro": 45.0, "Chai": 45.0, "Matcha": 45.0, "Rompope": 45.0, "Red Velvet": 45.0, "Pistache": 45.0, "Galleta": 45.0, "Mora": 45.0, "Cereza": 45.0, "Refresher Darks": 45.0, "Cafe": 45.0, "Moka": 45.0, "Oreo": 45.0, "Chocolate": 45.0},
    "Chamoyadas": {"Fresa": 65.0, "Mango": 65.0, "Temporada": 65.0},
    "Refreshers": {"Fresa": 55.0, "Cherry negra": 55.0, "Guayaba": 55.0, "Kiwi": 55.0},
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
# VISOR GLOBAL DE IMPRESIÓN CON AUTO-REDIRECT
# ==========================================
if st.session_state.ticket_imprimir:
    url_impresion = "rawbt:" + urllib.parse.quote(st.session_state.ticket_imprimir)
    st.markdown(f'<a href="{url_impresion}" target="_blank" class="btn-imprimir">🖨️ TOCAR AQUÍ PARA IMPRIMIR TICKET EN LA ZEBRA</a>', unsafe_allow_html=True)
    
    components.html(f'''
        <script>
            window.parent.location.href = "{url_impresion}";
        </script>
    ''', height=0)

    if st.button("✅ Ya se imprimió / Ocultar botón", use_container_width=True):
        st.session_state.ticket_imprimir = None
        st.rerun()
    st.divider()

# ==========================================
# PESTAÑA 1: CARRITO
# ==========================================
with tabs[0]:
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
                else: item['notas'] = st.text_input("Notas", value=item.get('notas', ''), key=f"cn_{idx}", label_visibility="collapsed", placeholder="Notas")
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
        with c_pago:
            pago = st.selectbox("Método de Pago:", ["Efectivo", "Transferencia", "Terminal", "A Cuenta (Deuda)"])

        c_fin1, c_fin2 = st.columns(2)
        with c_fin1:
            if st.button("✅ Registrar Pedido", type="primary", use_container_width=True):
                nombre_c = cliente.strip() if cliente.strip() else "Mostrador"
                ahora = datetime.now(zona_mx)
                nuevas_filas = []
                for it in st.session_state.cart:
                    nuevas_filas.append([
                        nombre_c, it['prod'], it.get('dest', 'Entrega Directa'),
                        it['precio'], pago, st.session_state.cajero,
                        "Pendiente", it.get('tiempo', '⚡ Ahora'), hoy_str,
                        ahora.strftime("%H:%M"), it.get('notas', '')
                    ])
                try:
                    if sh:
                        ws_ops = sh.worksheet("Operaciones")
                        ws_ops.append_rows(nuevas_filas)
                        if pago == "A Cuenta (Deuda)":
                            sh.worksheet("Deudas").append_row([nombre_c, total, hoy_str, "Pendiente"])
                except Exception as err:
                    st.error(f"Error al guardar: {err}")
                
                st.session_state.ticket_imprimir = generar_texto_ticket(nombre_c, pago, st.session_state.cart, total, tipo="venta")
                st.session_state.cart = []
                st.cache_data.clear()
                st.rerun()

        with c_fin2:
            if st.button("🗑️ Vaciar Carrito", use_container_width=True):
                st.session_state.cart = []
                st.rerun()

# ==========================================
# PESTAÑA 2: CAJA RÁPIDA (BLINDADA CONTRA ERRORES)
# ==========================================
with tabs[1]:
    st.subheader("⚡ Venta Rápida por Cuadrícula")
    
    lista_rapida = []
    for cat_k, p_dict in MENU.items():
        for prod_k, prec_v in p_dict.items():
            lista_rapida.append({"nombre": armar_nombre(cat_k, prod_k), "precio": prec_v, "cat": cat_k})

    # 1. Asegurar estado de sesión limpio (previene StreamlitValueBelowMinError)
    for ctd, p_info in enumerate(lista_rapida):
        key_w = f"cr_qty_{ctd}_{p_info['nombre']}"
        if key_w not in st.session_state or st.session_state[key_w] is None:
            st.session_state[key_w] = 0
        elif not isinstance(st.session_state[key_w], int) or st.session_state[key_w] < 0:
            st.session_state[key_w] = 0

    # 2. Renderizado de 4 columnas
    cols = st.columns(4)
    nv = {}
    for ctd, p_info in enumerate(lista_rapida):
        key_w = f"cr_qty_{ctd}_{p_info['nombre']}"
        with cols[ctd % 4]:
            nv[p_info['nombre']] = st.number_input(
                label=f"{p_info['nombre']} (${p_info['precio']:.0f})",
                min_value=0,
                step=1,
                key=key_w
            )

    # Cálculo y procesamiento
    pedido_rapido = []
    tot_rapido = 0
    for p_info in lista_rapida:
        cant_sel = nv.get(p_info['nombre'], 0)
        if cant_sel > 0:
            tot_rapido += cant_sel * p_info['precio']
            pedido_rapido.append({
                "prod": p_info['nombre'], "precio": p_info['precio'] * cant_sel,
                "cant": cant_sel, "notas": "", "tiempo": "⚡ Ahora",
                "dest": "Cocina" if p_info['cat'] == "Platillos" else "Entrega Directa"
            })

    if pedido_rapido:
        st.divider()
        st.write(f"### Total Venta Rápida: ${tot_rapido}")
        cr_c1, cr_c2 = st.columns([2, 1])
        with cr_c1:
            cr_cli = st.text_input("Cliente:", value="Mostrador", key="cr_cliente_txt")
        with cr_c2:
            cr_pago = st.selectbox("Forma de Pago:", ["Efectivo", "Transferencia", "Terminal", "A Cuenta (Deuda)"], key="cr_pago_sel")

        if st.button("⚡ Registrar y Cobrar Rápido", type="primary", use_container_width=True):
            nombre_cr = cr_cli.strip() if cr_cli.strip() else "Mostrador"
            ahora = datetime.now(zona_mx)
            filas_cr = []
            for item_r in pedido_rapido:
                filas_cr.append([
                    nombre_cr, item_r['prod'], item_r['dest'],
                    item_r['precio'], cr_pago, st.session_state.cajero,
                    "Pendiente", "⚡ Ahora", hoy_str, ahora.strftime("%H:%M"), ""
                ])
            try:
                if sh:
                    sh.worksheet("Operaciones").append_rows(filas_cr)
                    if cr_pago == "A Cuenta (Deuda)":
                        sh.worksheet("Deudas").append_row([nombre_cr, tot_rapido, hoy_str, "Pendiente"])
            except Exception as err:
                st.error(f"Error al guardar: {err}")

            st.session_state.ticket_imprimir = generar_texto_ticket(nombre_cr, cr_pago, pedido_rapido, tot_rapido, tipo="venta")
            # Reset cantidades
            for ctd, p_info in enumerate(lista_rapida):
                st.session_state[f"cr_qty_{ctd}_{p_info['nombre']}"] = 0
            st.cache_data.clear()
            st.rerun()

# ==========================================
# PESTAÑA 3: COCINA
# ==========================================
with tabs[2]:
    st.subheader("👨‍🍳 Monitor de Cocina")
    pendientes_cocina = [f for f in ops[1:] if len(f) > 8 and f[2] == "Cocina" and f[6] in ["Pendiente", "Preparando"] and f[8] == hoy_str]
    if not pendientes_cocina:
        st.info("No hay pedidos pendientes en cocina.")
    else:
        for idx_c, p in enumerate(pendientes_cocina):
            urgente = p[7] == "⚡ Ahora"
            clase_card = "card-urgente" if urgente else "card-programado"
            st.markdown(f'''
            <div class="card {clase_card}">
                <b>{p[1]}</b> | Cantidad: 1<br>
                Cliente: <b>{p[0]}</b> | Entrega: <b>{p[7]}</b> ({p[9]})<br>
                Notas: <i>{p[10] if len(p) > 10 else ""}</i>
            </div>
            ''', unsafe_allow_html=True)
            if st.button("Listo para entrega", key=f"btn_cocina_{idx_c}"):
                try:
                    ws_ops = sh.worksheet("Operaciones")
                    for row_i, f in enumerate(ops[1:], start=2):
                        if f == p:
                            ws_ops.update_cell(row_i, 7, "Listo")
                            break
                    st.cache_data.clear()
                    st.rerun()
                except Exception as err:
                    st.error(f"Error: {err}")

# ==========================================
# PESTAÑA 4: ENTREGAS
# ==========================================
with tabs[3]:
    st.subheader("🚚 Monitor de Entregas")
    listos_entrega = [f for f in ops[1:] if len(f) > 8 and f[6] == "Listo" and f[8] == hoy_str]
    if not listos_entrega:
        st.info("No hay pedidos esperando entrega.")
    else:
        for idx_e, p in enumerate(listos_entrega):
            st.markdown(f'''
            <div class="card">
                <b>{p[1]}</b> para <b>{p[0]}</b><br>
                Estado: <b>{p[6]}</b> | Hora: {p[9]}
            </div>
            ''', unsafe_allow_html=True)
            if st.button("Marcar como Entregado", key=f"btn_ent_{idx_e}"):
                try:
                    ws_ops = sh.worksheet("Operaciones")
                    for row_i, f in enumerate(ops[1:], start=2):
                        if f == p:
                            ws_ops.update_cell(row_i, 7, "Entregado")
                            break
                    st.cache_data.clear()
                    st.rerun()
                except Exception as err:
                    st.error(f"Error: {err}")

# ==========================================
# PESTAÑA 5: AGENDADOS
# ==========================================
with tabs[4]:
    st.subheader("📅 Pedidos Programados")
    prog = [f for f in ops[1:] if len(f) > 8 and f[7] != "⚡ Ahora" and f[8] == hoy_str]
    if not prog:
        st.info("No hay pedidos agendados para hoy.")
    else:
        for p in prog:
            st.markdown(f'''
            <div class="card card-programado">
                <b>{p[0]}</b> - {p[1]}<br>
                Hora programada: <b>{p[9]}</b> | Estado: {p[6]}
            </div>
            ''', unsafe_allow_html=True)

# ==========================================
# PESTAÑA 6: PAGOS Y DEUDAS
# ==========================================
with tabs[5]:
    st.subheader("💳 Cuentas Pendientes")
    if len(deu) > 1:
        df_deu = pd.DataFrame(deu[1:], columns=deu[0])
        st.dataframe(df_deu, use_container_width=True)
    else:
        st.info("No hay cuentas pendientes registradas.")

# ==========================================
# PESTAÑA 7: INVENTARIO
# ==========================================
with tabs[6]:
    st.subheader("📦 Control de Inventario")
    if len(inv) > 1:
        df_inv = pd.DataFrame(inv[1:], columns=inv[0])
        st.dataframe(df_inv, use_container_width=True)
    else:
        st.info("No hay datos de inventario disponibles.")

# ==========================================
# PESTAÑA 8 Y 9: ADMIN & GASTOS (SI ESTÁ ACTIVO)
# ==========================================
if st.session_state.admin_mode:
    with tabs[7]:
        st.subheader("⚙️ Panel de Administración")
        st.write("Corte de caja del día:")
        ventas_hoy = [float(f[3]) for f in ops[1:] if len(f) > 8 and f[8] == hoy_str and f[4] != "A Cuenta (Deuda)"]
        st.metric("Total Vendido Hoy", f"${sum(ventas_hoy):.2f}")
        if st.button("Borrar Caché y Recargar"):
            st.cache_data.clear()
            st.rerun()
            
    with tabs[8]:
        st.subheader("💸 Registro de Gastos")
        g_con = st.text_input("Concepto del Gasto:")
        g_mon = st.number_input("Monto ($):", min_value=0.0, step=10.0)
        if st.button("Guardar Gasto") and g_con:
            try:
                if sh and "Gastos" in [w.title for w in sh.worksheets()]:
                    sh.worksheet("Gastos").append_row([hoy_str, g_con, g_mon, st.session_state.cajero])
                    st.success("Gasto registrado")
                    st.cache_data.clear()
                    st.rerun()
            except Exception as e:
                st.error(f"Error: {e}")
