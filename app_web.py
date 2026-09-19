import streamlit as st
import gspread
import json
import pandas as pd
import re
from datetime import datetime, timedelta, timezone

# ==========================================
# 1. CONFIGURACIÓN VISUAL Y CSS ERGONÓMICO (Bordes 20px, Sin Puntos)
# ==========================================
st.set_page_config(page_title="POS Faro Café", layout="wide", initial_sidebar_state="collapsed")

st.markdown("""
<style>
    /* Escalado global y bordes ultra redondeados */
    html, body, [class*="css"], .stMarkdown, p, span, label, div {
        font-size: 19px !important;
    }
    h1 { font-size: 32px !important; font-weight: 800 !important; }
    h2 { font-size: 26px !important; font-weight: 700 !important; }
    h3 { font-size: 22px !important; font-weight: 700 !important; }
    
    /* Botones de producto (Gigantes, redondeados) */
    .prod-container div.stButton > button { 
        min-height: 110px !important; 
        border-radius: 20px !important; 
        border: 2px solid #005A9E !important; 
        font-weight: 700 !important; 
        background-color: #FFFFFF !important; 
        color: #002244 !important; 
        font-size: 21px !important; 
        box-shadow: 0 4px 10px rgba(0,0,0,0.1) !important;
        white-space: pre-wrap !important;
        line-height: 1.25 !important;
    }
    
    .prod-container div.stButton > button:hover { 
        border-color: #003B66 !important; 
        background-color: #F0F7FF !important; 
    }
    
    .prod-container div.stButton > button:active { 
        background-color: #CCE5FF !important; 
        transform: scale(0.95) !important; 
    }

    /* Transformar Radios en Botones Pastilla (Ocultar el punto rojo) */
    div[role="radiogroup"] {
        gap: 12px !important;
        flex-wrap: wrap;
    }
    div[role="radiogroup"] label {
        background-color: #F8FBFF !important;
        border: 2px solid #005A9E !important;
        border-radius: 20px !important;
        padding: 10px 20px !important;
        font-weight: 700 !important;
        cursor: pointer !important;
        display: flex !important;
        justify-content: center !important;
        align-items: center !important;
    }
    /* Ocultar el círculo nativo */
    div[role="radiogroup"] label input, 
    div[role="radiogroup"] label div[data-testid="stMarkdownContainer"] pre {
        display: none !important;
    }
    div[role="radiogroup"] .st-b7, div[role="radiogroup"] .st-b8, div[role="radiogroup"] svg {
        display: none !important;
    }
    /* Efecto de seleccionado para el radiogroup */
    div[role="radiogroup"] label[data-baseweb="radio"][aria-checked="true"] {
        background-color: #005A9E !important;
    }
    div[role="radiogroup"] label[data-baseweb="radio"][aria-checked="true"] div {
        color: #FFFFFF !important;
    }

    /* Botones primarios (Acciones grandes) */
    div.stButton > button[kind="primary"] { 
        background-color: #005A9E !important; 
        color: white !important; 
        border: none !important; 
        font-size: 22px !important;
        min-height: 85px !important;
        font-weight: 800 !important;
        border-radius: 20px !important;
        box-shadow: 0 4px 8px rgba(0,0,0,0.2) !important;
    }

    /* Estilo de botones de Categoría */
    .cat-container div.stButton > button {
        min-height: 65px !important;
        border-radius: 15px !important;
        font-size: 20px !important;
        font-weight: 700 !important;
        box-shadow: 0 2px 5px rgba(0,0,0,0.05) !important;
    }

    /* Tarjetas de cocina, personalización y borrador (20px radius) */
    .card { 
        background-color: #FFFFFF; 
        padding: 20px; 
        border-radius: 20px; 
        margin-bottom: 20px; 
        box-shadow: 0 4px 12px rgba(0,0,0,0.08); 
        border: 2px solid #E1E4E8;
    }
    .card-urgente { 
        border: 4px solid #D93025; 
        background-color: #FFF2F2; 
    }
    .card-programado {
        border: 4px solid #005A9E; 
        background-color: #F0F7FF;
    }
    .card-personalizar {
        border: 4px solid #FF8C00;
        background-color: #FFFDF0;
        border-radius: 20px;
        padding: 24px;
        margin-bottom: 20px;
    }
    .card-borrador {
        background-color: #F8FBFF;
        border: 3px dashed #005A9E;
        padding: 20px;
        border-radius: 20px;
        margin-bottom: 20px;
    }

    /* Cabecera fija de categorías */
    .sticky-header { 
        position: sticky; 
        top: 0; 
        background-color: white; 
        z-index: 999; 
        padding: 10px 0 15px 0; 
        border-bottom: 2px solid #E1E4E8; 
    }
</style>
""", unsafe_allow_html=True)

zona_mx = timezone(timedelta(hours=-6))
hoy_obj = datetime.now(zona_mx).date()
hoy_str = hoy_obj.strftime("%d/%m/%Y")

inicio_semana = hoy_obj - timedelta(days=hoy_obj.weekday())
fin_semana = inicio_semana + timedelta(days=6)
semana_str = f"{inicio_semana.strftime('%d/%m')} al {fin_semana.strftime('%d/%m')}"

# ==========================================
# 2. CONEXIÓN Y LECTURA
# ==========================================
@st.cache_resource
def conectar():
    try:
        c = json.loads(st.secrets["google_credentials"], strict=False)
        return gspread.service_account_from_dict(c).open("Base_POS")
    except Exception as e: 
        st.error(f"Error de conexión: {e}")
        return None
sh = conectar()

@st.cache_data(ttl=600)
def leer():
    if not sh: return [], [], [], [], []
    try: 
        ops = sh.worksheet("Operaciones").get_all_values()
        inv = sh.worksheet("Inventario").get_all_values()
        deu = sh.worksheet("Deudas").get_all_values()
        hist = sh.worksheet("Historial").get_all_values()
        try: gas = sh.worksheet("Gastos").get_all_values()
        except: gas = []
        return ops, inv, deu, hist, gas
    except Exception as e: 
        st.error(f"Error de lectura: {e}")
        return [], [], [], [], []

ops, inv, deu, hist, gas = leer()

# ==========================================
# 3. MEMORIA DE ESTADOS
# ==========================================
if 'cajero' not in st.session_state:
    if st.query_params.get("u", ""):
        st.session_state.cajero = st.query_params.get("u", "")
    else:
        st.title("Control de Acceso")
        st.write("Identificación requerida para el registro de operaciones.")
        st.info("Nota: Guarda esta página en tu pantalla de inicio después de acceder.")
        nom = st.text_input("Nombre del Operador/Cajero:")
        if st.button("Iniciar Turno", type="primary"):
            if nom.strip():
                st.query_params["u"] = nom.strip()
                st.session_state.cajero = nom.strip()
                st.rerun()
        st.stop()

if 'cart' not in st.session_state: st.session_state.cart = []
if 'puesto_cart' not in st.session_state: st.session_state.puesto_cart = []
if 'admin_mode' not in st.session_state: st.session_state.admin_mode = False
if 'cat_apagadas' not in st.session_state: st.session_state.cat_apagadas = []
if 'borrador_voz' not in st.session_state: st.session_state.borrador_voz = None
if 'prod_edit' not in st.session_state: st.session_state.prod_edit = None
if 'cat_activa' not in st.session_state: st.session_state.cat_activa = "Café"
if 'cat_puesto_activa' not in st.session_state: st.session_state.cat_puesto_activa = "Café"

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
dict_inv = {}
fila_producto_map = {}

if len(inv) > 1:
    for row_idx, row in enumerate(inv[1:], start=2):
        if len(row) >= 4:
            prod = row[0].strip()
            stock_str = row[1].strip() if len(row) > 1 else ""
            cat = row[2].strip() if len(row) > 2 else "General"
            precio_str = row[3].strip() if len(row) > 3 else "0"
            activo = row[4].strip().lower() if len(row) >= 5 else "activo"

            fila_producto_map[prod] = row_idx
            if activo == "activo":
                if "Tortas" in cat:
                    try: dict_inv[prod] = int(stock_str)
                    except: dict_inv[prod] = 0
                else: dict_inv[prod] = None

                if cat not in MENU: MENU[cat] = {}
                try: MENU[cat][prod] = float(precio_str)
                except: MENU[cat][prod] = 0.0

if not MENU.get("Tortas") and "Tortas" in MENU: del MENU["Tortas"]
for c_apagada in st.session_state.cat_apagadas:
    if c_apagada in MENU: del MENU[c_apagada]

clientes_historicos = []
if len(ops) > 1: clientes_historicos.extend([f[0] for f in ops[1:] if len(f)>0 and f[0].strip() not in ["", "Mostrador"]])
if len(deu) > 1: clientes_historicos.extend([f[0] for f in deu[1:] if len(f)>0 and f[0].strip() not in ["", "Mostrador"]])
clientes_unicos = sorted(list(set(clientes_historicos)))

TODOS_LOS_PRODUCTOS = {}
for cat_k, p_dict in MENU.items():
    for p_k, p_v in p_dict.items(): TODOS_LOS_PRODUCTOS[p_k] = {"precio": p_v, "cat": cat_k}
LISTA_NOMBRES_PRODUCTOS = sorted(list(TODOS_LOS_PRODUCTOS.keys()))

if st.session_state.cat_activa not in MENU and len(MENU) > 0: st.session_state.cat_activa = list(MENU.keys())[0]

# ==========================================
# 5. MOTOR DE RECONOCIMIENTO (VOZ)
# ==========================================
def procesar_voz_pedido(texto_in):
    t = texto_in.lower().strip()
    pago = "Pendiente" if any(x in t for x in ["pendiente", "a cuenta", "debe", "deuda", "despues", "luego", "apunta", "anot"]) else "Pagado"
    
    cliente = ""
    for c in clientes_unicos:
        if c.lower() in t:
            cliente = c
            break
    if not cliente:
        m_cli = re.search(r'(?:para|a nombre de)\s+([a-záéíóúñ]+)', t)
        if m_cli:
            cand = m_cli.group(1).capitalize()
            if cand.lower() not in ["las", "la", "el", "los", "hoy", "llevar", "cocina", "mañana"]: cliente = cand

    tiempo = "⚡ Ahora"
    hora_fin = "Ahora"
    m_hora = re.search(r'(?:a las|para las)\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm|de la tarde|de la mañana)?', t)
    if m_hora:
        tiempo = "🕒 Prog."
        h = int(m_hora.group(1))
        m = m_hora.group(2) if m_hora.group(2) else "00"
        ampm = m_hora.group(3)
        if ampm in ["pm", "de la tarde"] and h < 12: h += 12
        hora_fin = f"{h:02d}:{m}"

    dia_tipo = "Mañana" if "mañana" in t else "Hoy"
    items_extraidos = []
    num_map = {"un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5}
    
    notas_auto = []
    if "sin azucar" in t or "sin azúcar" in t: notas_auto.append("Sin azúcar")
    if "deslactosada" in t: notas_auto.append("Leche deslactosada")
    if "almendra" in t: notas_auto.append("Leche de Almendra")
    txt_notas = ", ".join(notas_auto)

    for p_nom, p_info in TODOS_LOS_PRODUCTOS.items():
        kw = p_nom.lower()
        if len(kw) >= 3 and re.search(r'\b' + re.escape(kw) + r'\b', t):
            cant = 1
            m_cant = re.search(r'(\d+|un|uno|una|dos|tres|cuatro|cinco)\s+(?:de\s+)?(?:cafes?\s+|bebidas?\s+)?' + re.escape(kw), t)
            if m_cant:
                val = m_cant.group(1)
                cant = int(val) if val.isdigit() else num_map.get(val, 1)

            dest = "Cocina" if p_info["cat"] == "Platillos" else "Entrega Directa"
            precio_calc = p_info["precio"]
            if "almendra" in t and p_info["cat"] in ["Frappés", "Esquimos", "Bebidas Frías", "Café"]: precio_calc += 10
            
            items_extraidos.append({
                "prod": armar_nombre(p_info["cat"], p_nom),
                "cant": cant,
                "precio": precio_calc,
                "notas": txt_notas,
                "dest": dest,
                "cat": p_info["cat"]
            })

    return {"cliente": cliente, "pago": pago, "tiempo": tiempo, "hora_fin": hora_fin, "dia_tipo": dia_tipo, "items": items_extraidos, "texto_origen": texto_in}

# ==========================================
# 6. MENÚ LATERAL Y PESTAÑAS
# ==========================================
with st.sidebar:
    st.write(f"**Operador:** {st.session_state.cajero}")
    st.divider()
    pin = st.text_input("Credencial Admin:", type="password")
    if pin == "1234":
        st.session_state.admin_mode = True
        st.success("Modo Administrador Autorizado")
    else: st.session_state.admin_mode = False

pestanas = ["🛒 Carrito", "⚡ Caja Rápida", "👨‍🍳 Cocina", "🚚 Entregas", "📅 Agendados", "💳 Pagos", "📦 Inventario"]
if st.session_state.admin_mode: 
    pestanas.append("⚙️ Administración")
    pestanas.append("💸 Gastos (Nuevo)")
tabs = st.tabs(pestanas)

# ==========================================
# PESTAÑA 1: CARRITO PRINCIPAL (POP-UP y VOZ)
# ==========================================
with tabs[0]:
    # --- MÓDULO DE VOZ ---
    with st.expander("🎙️ TOMAR ORDEN POR VOZ / DICTADO RÁPIDO", expanded=False):
        st.write("Presiona el micrófono del teclado de tu celular y dicta la orden:")
        col_v1, col_v2 = st.columns([3, 1])
        with col_v1: texto_voz = st.text_input("Dictado:", placeholder="Habla o escribe aquí...", label_visibility="collapsed", key="in_voz")
        with col_v2:
            if st.button("⚡ Interpretar", use_container_width=True):
                if texto_voz.strip():
                    st.session_state.borrador_voz = procesar_voz_pedido(texto_voz)
                    st.rerun()

    # --- BORRADOR VOZ ---
    if st.session_state.borrador_voz:
        b = st.session_state.borrador_voz
        st.markdown('<div class="card-borrador">', unsafe_allow_html=True)
        st.markdown("### 📝 Confirmación de Orden (Revisa y Edita)")
        st.info(f"Escuchado: *\"{b.get('texto_origen', '')}\"*")

        c_b1, c_b2 = st.columns(2)
        with c_b1:
            cli_actual = b["cliente"]
            opc_idx = ([""] + clientes_unicos).index(cli_actual) if cli_actual in clientes_unicos else 0
            sel_cli = st.selectbox("Cliente:", [""] + clientes_unicos, index=opc_idx, key="bv_cli_sel")
            b["cliente"] = st.text_input("Nombre Cliente:", value=(sel_cli if sel_cli else b["cliente"]), key="bv_cli_txt")
        with c_b2:
            pago_idx = 1 if b["pago"] == "Pendiente" else 0
            b["pago"] = st.radio("Cobro:", ["Pagado", "Pendiente"], index=pago_idx, horizontal=True, key="bv_pago")

        c_b3, c_b4 = st.columns(2)
        with c_b3:
            t_idx = 1 if b["tiempo"] == "🕒 Prog." else 0
            b["tiempo"] = st.radio("Tiempo Gral:", ["⚡ Ahora", "🕒 Prog."], index=t_idx, horizontal=True, key="bv_tiempo")
            if b["tiempo"] == "🕒 Prog.": b["hora_fin"] = st.text_input("Hora entrega:", value=b["hora_fin"], key="bv_hora")
            else: b["hora_fin"] = "Ahora"
        with c_b4:
            d_idx = 1 if b["dia_tipo"] == "Mañana" else 0
            b["dia_tipo"] = st.radio("Día:", ["Hoy", "Mañana"], index=d_idx, horizontal=True, key="bv_dia")

        st.write("---")
        st.write("**Productos detectados:**")
        tot_borrador = 0
        eliminar_idx = None
        for idx, item in enumerate(b["items"]):
            cb1, cb2, cb3, cb4 = st.columns([3, 1, 2, 1])
            with cb1: st.write(f"**{item['prod']}**")
            with cb2: item["cant"] = st.number_input("Cant:", min_value=1, value=item.get("cant", 1), key=f"bc_{idx}")
            with cb3: item["notas"] = st.text_input("Notas:", value=item.get("notas", ""), key=f"bn_{idx}")
            with cb4:
                st.write("")
                if st.button("🗑️", key=f"bdel_{idx}"): eliminar_idx = idx
            tot_borrador += (item["precio"] * item["cant"])

        if eliminar_idx is not None:
            b["items"].pop(eliminar_idx)
            st.rerun()

        st.write(f"### Total Orden Voz: ${tot_borrador}")
        col_b_desc, col_b_proc = st.columns(2)
        with col_b_desc:
            if st.button("❌ Descartar Borrador", use_container_width=True):
                st.session_state.borrador_voz = None
                st.rerun()
        with col_b_proc:
            if st.button("✅ PROCESAR ORDEN", type="primary", use_container_width=True):
                if not b["items"]: st.warning("No hay productos.")
                elif not b["cliente"].strip() and b["pago"] == "Pendiente": st.warning("Falta nombre para deuda.")
                else:
                    nom_f = b["cliente"].strip() if b["cliente"].strip() else "Mostrador"
                    fecha_f = (hoy_obj + timedelta(days=1)).strftime("%d/%m/%Y") if b["dia_tipo"] == "Mañana" else hoy_str
                    h_r = datetime.now(zona_mx).strftime("%H:%M") if b["hora_fin"] == "Ahora" else f"{fecha_f} {b['hora_fin']}"

                    for i in b["items"]:
                        for _ in range(i["cant"]):
                            est = "Pendiente" if b["dia_tipo"] != "Hoy" or b["tiempo"] != "⚡ Ahora" else ("Preparando" if i["dest"] == "Cocina" else "Entregado")
                            sh.worksheet("Operaciones").append_row([nom_f, i['prod'], i["dest"], i['notas'], b["tiempo"], h_r, est, tot_borrador if (i == b["items"][0] and _ == 0) else 0, fecha_f, st.session_state.cajero])

                    if b["pago"] == "Pendiente":
                        sh.worksheet("Deudas").append_row([nom_f, "Deuda", tot_borrador, ", ".join([f"{i['cant']}x {i['prod']}" for i in b["items"]]), hoy_str])

                    st.session_state.borrador_voz = None
                    leer.clear()
                    st.success("¡Procesado exitosamente!")
                    st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

    # --- POP-UP OPCIÓN A (PERSONALIZACIÓN) ---
    if st.session_state.prod_edit:
        pe = st.session_state.prod_edit
        st.markdown(f'<div class="card-personalizar"><h2>⚙️ {armar_nombre(pe["cat"], pe["nombre"])}</h2>', unsafe_allow_html=True)
        
        if st.button("⚡ AGREGAR CLÁSICO (Saltar Preguntas)", type="primary", use_container_width=True, key="btn_saltar"):
            st.session_state.cart.append({"prod": armar_nombre(pe["cat"], pe["nombre"]), "precio": pe["precio"], "notas": "Clásico", "pan": False, "cat": pe["cat"], "tiempo": "⚡ Ahora", "hora": ""})
            st.session_state.prod_edit = None
            st.rerun()
            
        st.write("---")
        st.write("**O Cambiar Opciones:**")
        
        c_p1, c_p2 = st.columns(2)
        notas_extra = []
        precio_final = pe["precio"]
        
        if pe["cat"] in ["Frappés", "Esquimos", "Bebidas Frías", "Café"]:
            with c_p1:
                leche = st.radio("Leche:", ["Entera", "Deslactosada", "Almendra (+$10)"], key="mod_leche")
                if leche == "Almendra (+$10)": 
                    precio_final += 10
                    notas_extra.append("Almendra")
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
            if perlas == "Con Perlas (+$10)": 
                precio_final += 10
                notas_extra.append("Con Perlas Explosivas")
            
        elif pe["cat"] == "Refreshers":
            perlas = st.radio("Extra:", ["Normal", "Perlas Explosivas (+$10)"], key="mod_perlas")
            if perlas == "Perlas Explosivas (+$10)":
                precio_final += 10
                notas_extra.append("Con Perlas Explosivas")
                
        st.write("")
        if st.button(f"✅ AGREGAR PERSONALIZADO (${precio_final})", use_container_width=True, key="btn_pers"):
            str_notas = ", ".join(notas_extra)
            st.session_state.cart.append({"prod": armar_nombre(pe["cat"], pe["nombre"]), "precio": precio_final, "notas": str_notas, "pan": False, "cat": pe["cat"], "tiempo": "⚡ Ahora", "hora": ""})
            st.session_state.prod_edit = None
            st.rerun()
            
        if st.button("❌ Cancelar", key="btn_canc_mod"):
            st.session_state.prod_edit = None
            st.rerun()
            
        st.markdown('</div>', unsafe_allow_html=True)
        st.stop() 

    # --- CATEGORÍAS (BOTONES) ---
    st.markdown('<div class="sticky-header"><div class="cat-container">', unsafe_allow_html=True)
    lista_cats = list(MENU.keys())
    cols_por_fila = 3
    for fila_i in range(0, len(lista_cats), cols_por_fila):
        bloque = lista_cats[fila_i:fila_i + cols_por_fila]
        col_c = st.columns(cols_por_fila)
        for idx_col, cat_n in enumerate(bloque):
            with col_c[idx_col]:
                if st.button(cat_n, key=f"btn_cat_{cat_n}", use_container_width=True, type="primary" if st.session_state.cat_activa == cat_n else "secondary"):
                    st.session_state.cat_activa = cat_n
                    st.rerun()
    st.markdown('</div></div>', unsafe_allow_html=True)

    # --- PRODUCTOS GRID ---
    st.markdown('<div class="prod-container">', unsafe_allow_html=True)
    c_prod = st.columns(2)
    for i, (n, p) in enumerate(MENU[st.session_state.cat_activa].items()):
        stock = dict_inv.get(n, None)
        btn_txt = f"{n}\n${p}"
        agotado = False
        if stock is not None: 
            if stock <= 0: btn_txt += "\n(AGOTADO)"; agotado = True
            else: btn_txt += f"\n(Quedan {stock})"
            
        with c_prod[i%2]:
            if st.button(btn_txt, use_container_width=True, disabled=agotado, key=f"p_{st.session_state.cat_activa}_{n}"):
                if st.session_state.cat_activa in ["Frappés", "Esquimos", "Chamoyadas", "Bebidas Frías", "Café", "Refreshers"]:
                    st.session_state.prod_edit = {"nombre": n, "cat": st.session_state.cat_activa, "precio": p}
                else:
                    st.session_state.cart.append({"prod": armar_nombre(st.session_state.cat_activa, n), "precio": p, "notas": "", "pan": False, "cat": st.session_state.cat_activa, "tiempo": "⚡ Ahora", "hora": ""})
                st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

    with st.expander("Añadir Cargo Extra Manual"):
        c_mot, c_mon = st.columns([3,1])
        with c_mot: mot = st.text_input("Concepto (Extra):")
        with c_mon: mon = st.number_input("$", step=5.0)
        if st.button("Aplicar Extra", use_container_width=True):
            if mot:
                st.session_state.cart.append({"prod": f"Extra: {mot}", "precio": mon, "notas": "", "pan": False, "cat": "Extra", "tiempo": "⚡ Ahora", "hora": ""})
                st.rerun()

    # --- RESUMEN DE ORDEN MANUAL ---
    if st.session_state.cart:
        st.divider()
        st.subheader("🧾 Ticket / Resumen de Orden")
        total = 0
        
        for idx, item in enumerate(st.session_state.cart):
            st.markdown(f"**▪ {item['prod']} (${item['precio']})**")
            
            c1, c2, c3 = st.columns([1.5, 1, 1])
            with c1: 
                item['notas'] = st.text_input("Notas", value=item['notas'], key=f"cn_{idx}", label_visibility="collapsed", placeholder="Sin notas")
            with c2:
                item['tiempo'] = st.radio("Horario", ["⚡ Ahora", "🕒 Prog."], index=0 if item.get('tiempo', "⚡ Ahora") == "⚡ Ahora" else 1, horizontal=True, key=f"tr_{idx}", label_visibility="collapsed")
            with c3:
                if item['tiempo'] == "🕒 Prog.": item['hora'] = st.time_input("Hora", key=f"th_{idx}", label_visibility="collapsed").strftime("%H:%M")
                else: item['hora'] = "Ahora"
                    
            if item.get('cat') == "Platillos": 
                item['dest'] = "Cocina"
                if "Chilaquiles" in item['prod'] and "Torta" not in item['prod']: item['pan'] = st.checkbox("Incluir Telera", key=f"cpan_{idx}")
            else: item['dest'] = "Cocina" if st.checkbox("Mandar a Cocina", value=True, key=f"cd_{idx}") else "Entrega"
            
            total += item['precio']
            st.write("---")
            
        st.write(f"### Total a Cobrar: ${total}")
        
        st.write("**Datos del Cliente y Cobro:**")
        c_cli, c_pago = st.columns([2, 1])
        with c_cli:
            opc = st.selectbox("Buscar cliente:", [""] + clientes_unicos)
            cliente = st.text_input("Nombre / Referencia:", value=opc, placeholder="Escriba aquí...")
        with c_pago:
            pago = st.radio("Estado de Pago:", ["Pagado", "Pendiente"], index=0, horizontal=True)

        st.write("")
        c_borrar, c_enviar = st.columns(2)
        with c_borrar:
            if st.button("❌ Descartar Orden", use_container_width=True): 
                st.session_state.cart = []
                st.rerun()
        with c_enviar:
            if st.button("🚀 ENVIAR ORDEN", type="primary", use_container_width=True):
                if not cliente.strip() and pago == "Pendiente" and not st.session_state.admin_mode: 
                    st.warning("Se requiere nombre para registrar deuda.")
                else:
                    nom_final = cliente.strip() if cliente.strip() else "Mostrador"
                    panes = sum(1 for i in st.session_state.cart if "telera" in i['prod'].lower() or "torta de chilaquiles" in i['prod'].lower() or i.get('pan', False))
                    
                    for i in st.session_state.cart:
                        t_est = "Preparando" if i['dest'] == "Cocina" else "Entregado"
                        t_bd = "Inmediato" if i['tiempo'] == "⚡ Ahora" else "Definir Hora"
                        h_bd = datetime.now(zona_mx).strftime("%H:%M") if t_bd == "Inmediato" else i['hora']
                        
                        sh.worksheet("Operaciones").append_row([nom_final, i['prod'], i['dest'], i['notas'], t_bd, h_bd, t_est, total if i == st.session_state.cart[0] else 0, hoy_str, st.session_state.cajero])
                    
                    if pago == "Pendiente": 
                        resumen_productos = ", ".join([i['prod'] for i in st.session_state.cart])
                        sh.worksheet("Deudas").append_row([nom_final, "Deuda", total, resumen_productos, hoy_str])
                    
                    if len(inv) > 1:
                        for idx, row in enumerate(inv[1:], start=2):
                            p_nom = row[0]
                            try: p_stock = int(row[1])
                            except: p_stock = 0
                            
                            comprados = sum(1 for p in st.session_state.cart if p_nom in p['prod'])
                            if comprados > 0: sh.worksheet("Inventario").update_cell(idx, 2, p_stock - comprados)
                            if p_nom == "Telera" and panes > 0: sh.worksheet("Inventario").update_cell(idx, 2, p_stock - panes)
                                
                    st.session_state.cart = []
                    leer.clear()
                    st.success("¡Orden Procesada y Registrada!")
                    st.rerun()

# ==========================================
# PESTAÑA 2: CAJA RÁPIDA (Bebidas Directas)
# ==========================================
with tabs[1]:
    st.header("⚡ Caja Rápida (Sin Cocina)")
    categorias_puesto = [c for c in MENU.keys() if any(x in c for x in ["Café", "Frappés", "Bebidas", "Esquimos", "Chamoyadas", "Refreshers"])]
    
    if st.session_state.cat_puesto_activa not in categorias_puesto and categorias_puesto:
        st.session_state.cat_puesto_activa = categorias_puesto[0]

    if categorias_puesto:
        st.markdown('<div class="cat-container">', unsafe_allow_html=True)
        cols_p_cat = st.columns(3)
        for idx_cp, cat_p_nom in enumerate(categorias_puesto):
            with cols_p_cat[idx_cp % 3]:
                if st.button(cat_p_nom, key=f"btn_cp_{cat_p_nom}", use_container_width=True, type="primary" if st.session_state.cat_puesto_activa == cat_p_nom else "secondary"):
                    st.session_state.cat_puesto_activa = cat_p_nom
                    st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)
        
        st.markdown('<div class="prod-container">', unsafe_allow_html=True)
        cat_p = st.session_state.cat_puesto_activa
        cols_p = st.columns(2)
        for i, (n, p) in enumerate(MENU[cat_p].items()):
            with cols_p[i%2]:
                if st.button(f"{n}\n${p}", use_container_width=True, key=f"puesto_{cat_p}_{n}"):
                    st.session_state.puesto_cart.append({"prod": armar_nombre(cat_p, n), "precio": p, "notas": ""})
                    st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)
                    
        if st.session_state.puesto_cart:
            st.divider()
            total_p = 0
            for idx, item in enumerate(st.session_state.puesto_cart):
                c1, c2, c3 = st.columns([3, 1, 2])
                with c1: st.write(f"▪ {item['prod']}")
                with c2: st.write(f"${item['precio']}")
                with c3: item['notas'] = st.text_input("Notas", key=f"pn_{idx}", label_visibility="collapsed", placeholder="Opcional")
                total_p += item['precio']
                
            st.write(f"### Total Rápido: ${total_p}")
            
            c_nom_p, c_pag_p = st.columns([1.5, 1])
            with c_nom_p: 
                cliente_p = st.text_input("Nombre cliente:", placeholder="Para llevar...")
            with c_pag_p: 
                pago_p = st.radio("Cobro:", ["Pagado", "Pendiente"], index=0, horizontal=True, key="pag_p")
            
            st.write("")
            c_bp, c_ep = st.columns(2)
            with c_bp:
                if st.button("❌ Descartar", key="b_puesto", use_container_width=True): 
                    st.session_state.puesto_cart = []
                    st.rerun()
            with c_ep:
                if st.button("🚀 COBRAR Y ENTREGAR", type="primary", use_container_width=True):
                    nom_final_p = cliente_p.strip() if cliente_p.strip() else "Mostrador"
                    h_real_p = datetime.now(zona_mx).strftime("%H:%M")
                    
                    for i in st.session_state.puesto_cart:
                        sh.worksheet("Operaciones").append_row([nom_final_p, i['prod'], "Entrega Directa", i['notas'], "Inmediato", h_real_p, "Entregado", total_p if i == st.session_state.puesto_cart[0] else 0, hoy_str, st.session_state.cajero])
                    
                    if pago_p == "Pendiente": 
                        resumen_productos = ", ".join([i['prod'] for i in st.session_state.puesto_cart])
                        sh.worksheet("Deudas").append_row([nom_final_p, "Deuda", total_p, resumen_productos, hoy_str])
                    
                    st.session_state.puesto_cart = []
                    leer.clear()
                    st.success("Transacción registrada rápido.")
                    st.rerun()

# ==========================================
# PESTAÑA 3: COCINA
# ==========================================
with tabs[2]:
    st.header("👨‍🍳 Monitor de Producción")
    st.markdown("""<audio autoplay="true"><source src="https://www.soundjay.com/misc/sounds/bell-ringing-05.mp3" type="audio/mpeg"></audio>""", unsafe_allow_html=True)
    
    if len(ops) > 1:
        pedidos_cocina = [ (i, f) for i, f in enumerate(ops[1:], start=2) if len(f) > 8 and f[8] == hoy_str and f[6] == "Preparando" and f[2] == "Cocina" ]
        pedidos_cocina.sort(key=lambda x: (x[1][4] != "Inmediato", x[1][5])) 
        
        if pedidos_cocina:
            for i, f in pedidos_cocina:
                urgente = f[4] == "Inmediato"
                css_clase = "card-urgente" if urgente else "card-programado"
                
                st.markdown(f'<div class="card {css_clase}">', unsafe_allow_html=True)
                if urgente:
                    st.markdown("<h2 style='color:#D93025; font-weight:900;'>🔥 ENTREGAR AHORA (INMEDIATO)</h2>", unsafe_allow_html=True)
                else:
                    st.markdown(f"<h2 style='color:#005A9E; font-weight:800;'>🕒 PREPARAR PARA: {f[5]} HRS</h2>", unsafe_allow_html=True)
                    
                st.markdown(f"<h3>{f[1]}</h3><p><b>Cliente:</b> {f[0]}</p><p style='font-size:22px; font-weight:bold;'>Extras/Notas: <span style='color:#D93025;'><i>{f[3]}</i></span></p>", unsafe_allow_html=True)
                
                c_listo, c_canc = st.columns([3,1])
                with c_listo:
                    if st.button(f"✅ MARCAR COMO LISTO", key=f"l_{i}", use_container_width=True, type="primary"):
                        sh.worksheet("Operaciones").update_cell(i, 7, "Listo")
                        leer.clear()
                        st.rerun()
                with c_canc:
                    if st.session_state.admin_mode:
                        if st.button("🗑️ Cancelar", key=f"fc_{i}", use_container_width=True):
                            sh.worksheet("Operaciones").delete_rows(i)
                            leer.clear()
                            st.rerun()
                st.markdown('</div>', unsafe_allow_html=True)
        else:
            st.info("Cocina despejada. Sin órdenes activas.")

# ==========================================
# PESTAÑA 4: ENTREGAS
# ==========================================
with tabs[3]:
    st.header("🚚 Entregas Pendientes")
    if len(ops) > 1:
        hay_listos = False
        for i, f in enumerate(ops[1:], start=2):
            if len(f) > 8 and f[8] == hoy_str and f[6] == "Listo":
                hay_listos = True
                st.success(f"**{f[1]}** | Para: {f[0]} | Notas: {f[3]}")
                if st.button("🚀 Entregado al cliente", key=f"ent_{i}", type="primary"):
                    sh.worksheet("Operaciones").update_cell(i, 7, "Entregado")
                    leer.clear()
                    st.rerun()
        if not hay_listos: st.write("No hay órdenes esperando entrega.")

# ==========================================
# PESTAÑA 5: AGENDADOS
# ==========================================
with tabs[4]:
    st.header("📅 Programación de Órdenes a Futuro")
    if len(ops) > 1:
        futuros = 0
        for i, f in enumerate(ops[1:], start=2):
            if len(f) > 8 and f[6] not in ["Entregado", "Listo"]:
                try: fecha_pedido = datetime.strptime(f[8], "%d/%m/%Y").date()
                except: fecha_pedido = hoy_obj 

                if fecha_pedido > hoy_obj:
                    futuros += 1
                    st.info(f"**{f[1]}** - Fecha: {f[8]} | Hora: {f[5]} | Cliente: {f[0]}")
                    
                    if st.session_state.admin_mode:
                        with st.expander("Modificar Orden (Admin)"):
                            n_cli = st.text_input("Cliente", value=f[0], key=f"ac_{i}")
                            n_fec = st.text_input("Fecha (DD/MM/YYYY)", value=f[8], key=f"af_{i}")
                            n_not = st.text_input("Notas", value=f[3], key=f"an_{i}")
                            c_g, c_b = st.columns(2)
                            with c_g:
                                if st.button("Guardar Cambios", key=f"ag_{i}"):
                                    sh.worksheet("Operaciones").update_cell(i, 1, n_cli)
                                    sh.worksheet("Operaciones").update_cell(i, 9, n_fec)
                                    sh.worksheet("Operaciones").update_cell(i, 4, n_not)
                                    leer.clear()
                                    st.rerun()
                            with c_b:
                                if st.button("Eliminar Registro", key=f"ab_{i}"):
                                    sh.worksheet("Operaciones").delete_rows(i)
                                    leer.clear()
                                    st.rerun()
                    
                    c_p1, c_p2 = st.columns(2)
                    with c_p1:
                        if st.button("Pasar a cocina HOY", key=f"ae_coc_{i}"):
                            sh.worksheet("Operaciones").update_cell(i, 7, "Preparando")
                            sh.worksheet("Operaciones").update_cell(i, 9, hoy_str)
                            leer.clear()
                            st.rerun()
                    with c_p2:
                        if st.button("Entregado", key=f"ae_ent_{i}"):
                            sh.worksheet("Operaciones").update_cell(i, 7, "Entregado")
                            leer.clear()
                            st.rerun()
                            
        if futuros == 0: st.success("Sin órdenes programadas a futuro.")

# ==========================================
# PESTAÑA 6: PAGOS (Control de Crédito)
# ==========================================
with tabs[5]:
    st.header("💳 Control de Crédito y Deudas")
    if len(deu) > 1:
        lista_nombres = sorted(list(set(f[0] for f in deu[1:] if len(f) > 0 and f[0] != "")))
        busqueda = st.selectbox("Buscar Cliente:", ["Todos los saldos"] + lista_nombres)
        
        clientes_resumen = {}
        for i, f in enumerate(deu[1:], start=2):
            if len(f) >= 3:
                c, tipo = f[0], f[1]
                try: m = float(f[2])
                except: m = 0.0
                if c not in clientes_resumen: clientes_resumen[c] = {"tot": 0, "hist": []}
                if tipo == "Deuda": clientes_resumen[c]["tot"] += m
                else: clientes_resumen[c]["tot"] -= m
                clientes_resumen[c]["hist"].append({"idx": i, "data": f})
            
        for c, info in clientes_resumen.items():
            if (busqueda == "Todos los saldos" or c == busqueda) and round(info["tot"], 2) > 0:
                with st.expander(f"🔴 {c} - Saldo Pendiente: ${round(info['tot'], 2)}", expanded=(busqueda!="Todos los saldos")):
                    for h in info["hist"]:
                        fila = h["data"]
                        det = fila[3] if len(fila)>3 else ""
                        fec_original = fila[4] if len(fila)>4 else ""
                        try: fec_corta = datetime.strptime(fec_original, "%d/%m/%Y").strftime("%d/%m")
                        except: fec_corta = fec_original
                        st.write(f"{'[-] Cargo' if fila[1]=='Deuda' else '[+] Abono'} **${fila[2]}** ({fec_corta}) - *{det}*")
                    
                    st.divider()
                    c_ab, c_btn = st.columns(2)
                    with c_ab: 
                        abono = st.number_input(f"Monto a abonar:", min_value=0.0, max_value=float(info['tot']), value=float(info['tot']), key=f"n_{c}")
                    with c_btn:
                        st.write("")
                        if st.button("Registrar Pago", key=f"p_{c}", type="primary", use_container_width=True):
                            sh.worksheet("Deudas").append_row([c, "Abono", abono, "Pago", hoy_str])
                            leer.clear()
                            st.rerun()

# ==========================================
# PESTAÑA 7: INVENTARIO Y GESTOR DE PRECIOS
# ==========================================
with tabs[6]:
    st.header("📦 Gestión de Inventario y Precios")
    
    with st.expander("💲 MODIFICAR PRECIOS DEL MENÚ (Guarda automático en la Nube)", expanded=True):
        st.write("Cambia el precio de cualquier producto y se actualizará en todos los botones:")
        cp_sel, cp_val = st.columns([2, 1])
        with cp_sel:
            prod_a_editar = st.selectbox("Seleccionar Producto:", LISTA_NOMBRES_PRODUCTOS, key="edit_p_sel")
        with cp_val:
            precio_actual = TODOS_LOS_PRODUCTOS[prod_a_editar]["precio"]
            nuevo_precio = st.number_input("Precio ($):", value=float(precio_actual), step=1.0, min_value=0.0, key="edit_p_val")

        if st.button("💾 Guardar Nuevo Precio", type="primary", use_container_width=True):
            try:
                ws_inv = sh.worksheet("Inventario")
                if prod_a_editar in fila_producto_map:
                    fila_target = fila_producto_map[prod_a_editar]
                    ws_inv.update_cell(fila_target, 4, nuevo_precio)
                else:
                    cat_prod = TODOS_LOS_PRODUCTOS[prod_a_editar]["cat"]
                    ws_inv.append_row([prod_a_editar, "", cat_prod, nuevo_precio, "Activo"])
                leer.clear()
                st.success(f"¡Precio de {prod_a_editar} actualizado a ${nuevo_precio}!")
                st.rerun()
            except Exception as e:
                st.error(f"Error al guardar precio: {e}")

    st.divider()

    with st.expander("Alta de Nuevo Producto"):
        with st.form("add_inv"):
            n_prod = st.text_input("Nombre comercial:")
            n_cat = st.selectbox("Clasificación de menú:", list(MENU_BASE.keys()))
            n_pre = st.number_input("Precio Unitario ($):", step=5.0)
            
            if "Tortas" in n_cat:
                st.info("Para este producto, defina el inventario inicial:")
                n_stk = st.number_input("Cantidad Física:", min_value=0, value=10)
            else:
                n_stk = "" 
                
            if st.form_submit_button("Guardar en Base de Datos"):
                if n_prod.strip():
                    sh.worksheet("Inventario").append_row([n_prod, n_stk, n_cat, n_pre, "Activo"])
                    leer.clear()
                    st.success(f"Registro exitoso de {n_prod}.")
                    st.rerun()
                else:
                    st.warning("Especifique un nombre.")

    st.divider()
    st.write("Conteo físico y existencias (Productos controlados):")
    
    if len(inv) > 1:
        with st.form("inv_form"):
            cols = st.columns(4)
            nv = {}
            contador = 0
            for i, f in enumerate(inv[1:], start=2):
                if len(f) >= 5 and str(f[4]).strip().lower() == "activo" and "Tortas" in f[2]:
                    try: val = int(f[1])
                    except: val = 0
                    
                    with cols[contador % 4]:
                        nv[i] = st.number_input(f[0], value=val, min_value=0, key=f"ui_inv_{i}")
                    contador += 1
                    
            if contador == 0:
                st.info("No hay productos con conteo físico activo.")
                
            if st.form_submit_button("Actualizar Conteos"):
                for i, v in nv.items(): 
                    sh.worksheet("Inventario").update_cell(i, 2, v)
                leer.clear()
                st.success("Existencias guardadas en servidor.")
                st.rerun()

# ==========================================
# PESTAÑA 8: MODO ADMIN
# ==========================================
if st.session_state.admin_mode:
    with tabs[7]:
        st.header("⚙️ Panel Administrativo")
        
        st.subheader("Ingresos Recientes (Últimos 6 cortes)")
        cortes = []
        if len(hist) > 1:
            for row in hist:
                if len(row) > 2 and ("Corte" in row[1] or "Cierre" in row[1]):
                    try: 
                        monto_str = row[2].split("$")[1].replace(",", "").strip()
                        cortes.append({"Fecha": row[0], "Ingreso": float(monto_str)})
                    except: pass
                    
        if cortes:
            df_cortes = pd.DataFrame(cortes[-6:])
            st.bar_chart(df_cortes.set_index("Fecha"))
        else:
            st.info("Aún no hay cortes registrados.")
            
        st.divider()
        st.subheader("Cierre de Turno de Hoy")
        if st.button("EJECUTAR CORTE DE CAJA HOY", type="primary"):
            try:
                ws_ops = sh.worksheet("Operaciones")
                ws_hist = sh.worksheet("Historial")
                
                filas_mantener = [ops[0]] 
                ventas_hoy = 0
                tickets_borrados = 0
                
                for f in ops[1:]:
                    if len(f) > 8 and f[8] == hoy_str and f[6] == "Entregado":
                        try: ventas_hoy += float(f[7]) if f[7] else 0.0
                        except: pass
                        tickets_borrados += 1
                    else: filas_mantener.append(f)
                
                if tickets_borrados > 0:
                    ws_hist.append_row([hoy_str, "Cierre Diario", f"Ingreso Total: ${ventas_hoy}", f"Transacciones: {tickets_borrados}"])
                    ws_ops.clear()
                    ws_ops.update("A1", filas_mantener)
                    leer.clear()
                    st.success(f"Corte exitoso procesado por: ${ventas_hoy}.")
                    st.rerun()
                else: st.info("No existen operaciones de hoy aptas para cierre.")
            except Exception as e: st.error(f"Falla: {e}")
                
        st.divider()
        st.subheader("Editor Base de Datos (Avanzado)")
        if len(ops) > 0:
            df_ops = pd.DataFrame(ops[1:], columns=ops[0])
            edited_df = st.data_editor(df_ops, num_rows="dynamic", use_container_width=True)
            if st.button("Forzar Sincronización de Tabla"):
                datos_nuevos_ops = [edited_df.fillna("").columns.values.tolist()] + edited_df.fillna("").values.tolist()
                ws_o = sh.worksheet("Operaciones")
                ws_o.clear()
                ws_o.update("A1", datos_nuevos_ops)
                leer.clear()
                st.success("Sincronización completada.")
                st.rerun()

# ==========================================
# PESTAÑA 9: GASTOS (SOLO ADMIN)
# ==========================================
if st.session_state.admin_mode:
    with tabs[8]:
        st.header("💸 Control de Compras e Insumos")
        
        with st.form("form_gastos"):
            g_conc = st.text_input("Concepto (Ej. Vasos, Leche, Café):")
            c_cant, c_uni, c_precio = st.columns(3)
            with c_cant: g_cant = st.number_input("Cantidad:", min_value=0.0, step=0.5, value=1.0)
            with c_uni: g_uni = st.selectbox("Unidad:", ["Pieza(s)", "Kg", "Litro(s)", "Paquete(s)"])
            with c_precio: g_precio = st.number_input("Precio Unitario ($):", min_value=0.0, step=10.0, value=0.0)
            
            total_gasto = g_cant * g_precio
            st.info(f"**Total del gasto:** ${total_gasto}")
            
            if st.form_submit_button("Registrar Gasto"):
                if g_conc.strip() and total_gasto > 0:
                    try:
                        sh.worksheet("Gastos").append_row([hoy_str, g_conc, g_cant, g_uni, g_precio, total_gasto, semana_str])
                        leer.clear()
                        st.success(f"Gasto de ${total_gasto} registrado.")
                        st.rerun()
                    except: st.error("Asegúrate de tener la pestaña 'Gastos' en Google Sheets.")
                else: st.warning("Escribe el concepto y el precio.")
                    
        st.divider()
        st.subheader("Historial de Gastos por Semana")
        gas_validos = [fila[:7] for fila in gas if len(fila) >= 7 and fila[0] != "Fecha"]
        
        if len(gas_validos) > 0:
            df_gas = pd.DataFrame(gas_validos, columns=["Fecha", "Concepto", "Cantidad", "Unidad", "Precio_Unitario", "Total", "Semana"])
            df_gas['Total'] = pd.to_numeric(df_gas['Total'], errors='coerce').fillna(0)
            
            for sem in reversed(df_gas['Semana'].unique()): 
                df_semana = df_gas[df_gas['Semana'] == sem]
                total_semana = df_semana['Total'].sum()
                with st.expander(f"Semana: {sem} --- Total: ${total_semana}"):
                    for _, row in df_semana.iterrows():
                        st.write(f"- {row['Fecha']}: **{row['Concepto']}** ({row['Cantidad']} {row['Unidad']}) ➔ **${row['Total']}**")
        else:
            st.info("No hay registros de gastos aún.")
