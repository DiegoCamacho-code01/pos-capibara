import streamlit as st
import gspread
import json
import pandas as pd
from datetime import datetime, timedelta, timezone

# --- CONFIGURACIÓN DE PÁGINA Y CSS ---
st.set_page_config(page_title="POS Capibara", layout="wide", initial_sidebar_state="collapsed")

st.markdown("""
<style>
    div.stButton > button { height: 75px; border-radius: 12px; border: 2px solid #0073E6; font-weight: bold; background-color: #E6F2FF; color: #002244; font-size: 16px;}
    div.stButton > button:active { background-color: #99CCFF; transform: scale(0.95); }
    div.stButton > button[kind="primary"] { background-color: #0073E6; color: white; border: none; }
    .btn-alerta > button { background-color: #FFD700 !important; color: #000 !important; border: 2px solid #B8860B !important; }
    .sticky-header { position: sticky; top: 0; background-color: white; z-index: 999; padding: 10px 0; border-bottom: 2px solid #f0f2f6; }
    div[role="radiogroup"] { flex-wrap: wrap; gap: 5px; }
    div[role="radiogroup"] > label > div:first-child { display: none !important; }
    div[role="radiogroup"] > label { border: 2px solid #0073E6; padding: 10px 20px !important; border-radius: 12px !important; cursor: pointer; transition: 0.2s; }
    div[role="radiogroup"] > label[data-checked="true"] { background-color: #0073E6 !important; color: white !important; }
    .card { background-color: #f8f9fa; padding: 15px; border-radius: 10px; border-left: 5px solid #0073E6; margin-bottom: 10px; }
    .card-urgente { border-left: 5px solid #ff4b4b; }
</style>
""", unsafe_allow_html=True)

zona_mx = timezone(timedelta(hours=-6))
hoy_obj = datetime.now(zona_mx).date()
hoy_str = hoy_obj.strftime("%d/%m/%Y")

# --- CONEXIÓN Y LECTURA A PRUEBA DE FALLOS ---
@st.cache_resource
def conectar():
    try:
        c = json.loads(st.secrets["google_credentials"], strict=False)
        return gspread.service_account_from_dict(c).open("Base_POS")
    except: return None
sh = conectar()

@st.cache_data(ttl=2)
def leer():
    if not sh: return [], [], []
    try: 
        # Usamos get_all_values() para evitar errores si faltan columnas
        return (sh.worksheet("Operaciones").get_all_values(), 
                sh.worksheet("Inventario").get_all_values(), 
                sh.worksheet("Deudas").get_all_values())
    except Exception as e: 
        st.error(f"Error de lectura: {e}")
        return [], [], []

ops, inv, deu = leer()

# --- MEMORIA DE USUARIO ---
if 'cajero' not in st.session_state:
    if st.query_params.get("u", ""):
        st.session_state.cajero = st.query_params.get("u", "")
    else:
        st.title("👋 Bienvenido")
        nom = st.text_input("NOMBRE:")
        if st.button("Entrar", type="primary"):
            if nom.strip():
                st.query_params["u"] = nom.strip()
                st.session_state.cajero = nom.strip()
                st.rerun()
        st.stop()

# --- MENÚ FIJO (Sin inventario) ---
MENU = {
    "☕ Café": {"Cafe Vainilla": 25, "Cafe Avellana": 25, "Café Clásico": 25},
    "🥤 Frappés": {"Fresa": 65, "Taro": 65, "Oreo": 65},
    "🧊 Bebidas Frías": {"Fresa": 45, "Taro": 45, "Oreo": 45},
    "🥗 Platillos": {"Plato de Chilaquiles": 50}
}

# --- PROCESAR INVENTARIO (Tortas y agregados) ---
dict_inv = {}
if len(inv) > 1:
    for row in inv[1:]:
        if len(row) >= 4:
            prod, stock, cat, precio = row[0], row[1], row[2], row[3]
            try: dict_inv[prod] = int(stock)
            except: dict_inv[prod] = 0
            
            if cat not in MENU: MENU[cat] = {}
            try: MENU[cat][prod] = float(precio)
            except: MENU[cat][prod] = 0.0

if 'cart' not in st.session_state: st.session_state.cart = []
if 'admin_mode' not in st.session_state: st.session_state.admin_mode = False

# --- MENÚ LATERAL ---
with st.sidebar:
    st.write(f"👤 **Cajero:** {st.session_state.cajero}")
    st.divider()
    if st.text_input("PIN Admin:", type="password") == "1234":
        st.session_state.admin_mode = True
        st.success("Modo Dios Activado")
    else:
        st.session_state.admin_mode = False

pestanas = ["🛒 Carrito", "🍳 Cocina", "📅 Agendados", "📓 Pagos", "📦 Inventario"]
if st.session_state.admin_mode: pestanas.append("⚙️ Admin (Ventas)")
tabs = st.tabs(pestanas)

# ==========================================
# 1. CARRITO (Cliente 1-paso y Pago Pendiente)
# ==========================================
with tabs[0]:
    st.markdown('<div class="sticky-header">', unsafe_allow_html=True)
    cat = st.radio("Navegación:", list(MENU.keys()), horizontal=True, label_visibility="collapsed")
    st.markdown('</div>', unsafe_allow_html=True)
    
    cols = st.columns(2)
    for i, (n, p) in enumerate(MENU[cat].items()):
        # Solo muestra inventario si el producto existe en la hoja de Inventario
        stock = dict_inv.get(n, None)
        btn_txt = f"{n}\n${p}"
        agotado, alerta = False, False
        
        if stock is not None: 
            if stock <= 0: 
                btn_txt += "\n(AGOTADO)"
                agotado = True
            elif stock <= 2: 
                btn_txt += f"\n(¡ÚLTIMOS {stock}!)"
                alerta = True
            else: btn_txt += f"\n(Quedan {stock})"
            
        with cols[i%2]:
            if alerta: st.markdown('<div class="btn-alerta">', unsafe_allow_html=True)
            if st.button(btn_txt, use_container_width=True, disabled=agotado, key=f"b_{n}"):
                st.session_state.cart.append({"prod": n, "precio": p, "notas": "", "pan": False})
                st.rerun()
            if alerta: st.markdown('</div>', unsafe_allow_html=True)
            
    with st.expander("✨ ➕ Agregar Extra Manual"):
        c_mot, c_mon = st.columns([3,1])
        with c_mot: mot = st.text_input("Motivo:")
        with c_mon: mon = st.number_input("$", step=5.0)
        if st.button("Añadir Extra", use_container_width=True):
            if mot:
                st.session_state.cart.append({"prod": f"Extra: {mot}", "precio": mon, "notas": "", "pan": False})
                st.rerun()
    
    if st.session_state.cart:
        st.divider()
        st.subheader("🧾 Tu Ticket")
        total = 0
        for idx, item in enumerate(st.session_state.cart):
            c1, c2, c3 = st.columns([3, 1, 2])
            with c1: st.write(f"▪️ {item['prod']}")
            with c2: st.write(f"${item['precio']}")
            with c3:
                item['notas'] = st.text_input("Notas", key=f"n_{idx}", label_visibility="collapsed", placeholder="Notas...")
                if "Chilaquiles" in item['prod'] and "Torta" not in item['prod']: 
                    item['pan'] = st.checkbox("🥖 ¿Incluir Pan Telera?", key=f"pan_{idx}")
            total += item['precio']
            
        st.write(f"### 💰 Total: ${total}")
        st.divider()
        
        c_nom, c_pago = st.columns([1.5, 1])
        with c_nom: 
            cliente = st.text_input("👤 Nombre del Cliente (Escribe para registrar):")
        with c_pago: 
            # Default en índice 1 ("Pendiente / Fiado")
            pago = st.radio("💵 Pago", ["Pagado", "Pendiente / Fiado"], index=1, horizontal=True)

        c_dia, c_hora = st.columns(2)
        with c_dia:
            dia_tipo = st.radio("📅 Día:", ["Hoy", "Mañana", "Otro"], horizontal=True)
            if dia_tipo == "Otro": fecha_fin = st.date_input("Día en Calendario:").strftime("%d/%m/%Y")
            elif dia_tipo == "Mañana": fecha_fin = (hoy_obj + timedelta(days=1)).strftime("%d/%m/%Y")
            else: fecha_fin = hoy_str
            
        with c_hora:
            tiempo = st.radio("⏱️ Horario:", ["Ahora", "Más tarde"], horizontal=True)
            if tiempo == "Más tarde": hora_fin = st.time_input("Hora en Reloj:", step=1800).strftime("%H:%M")
            else: hora_fin = "Ahora"
        
        c_borrar, c_enviar = st.columns(2)
        with c_borrar:
            if st.button("🗑️ Borrar", use_container_width=True): 
                st.session_state.cart = []
                st.rerun()
        with c_enviar:
            if st.button("🚀 ENVIAR PEDIDO", type="primary", use_container_width=True):
                if not cliente.strip() and pago == "Pendiente / Fiado": 
                    st.warning("⚠️ Escribe un nombre para fiar.")
                else:
                    nom_final = cliente.strip() if cliente.strip() else "Mostrador"
                    panes = sum(1 for i in st.session_state.cart if "pan telera" in i['prod'].lower() or "torta de chilaquiles" in i['prod'].lower() or i['pan'])
                    h_real = datetime.now(zona_mx).strftime("%H:%M") if hora_fin == "Ahora" else f"{fecha_fin} {hora_fin}"
                    
                    for i in st.session_state.cart:
                        dest = "Cocina" if any(x in i['prod'] for x in ["Platillo", "Frappé", "Esquimo", "Chamoyada"]) else "Directo"
                        est = "Pendiente" if dia_tipo != "Hoy" or tiempo != "Ahora" else ("Preparando" if dest == "Cocina" else "Entregado")
                        sh.worksheet("Operaciones").append_row([nom_final, i['prod'], dest, i['notas'], tiempo, h_real, est, total if i == st.session_state.cart[0] else 0, fecha_fin, st.session_state.cajero])
                    
                    if pago == "Pendiente / Fiado": 
                        sh.worksheet("Deudas").append_row([nom_final, "Deuda", total, "Ticket", h_real])
                    
                    if panes > 0 and len(inv) > 1:
                        for idx, row in enumerate(inv[1:], start=2):
                            if len(row) > 1 and row[0] == "Pan Telera": 
                                try: sh.worksheet("Inventario").update_cell(idx, 2, int(row[1]) - panes)
                                except: pass
                                
                    st.session_state.cart = []
                    leer.clear()
                    st.success("¡Pedido Enviado!")
                    st.rerun()

# ==========================================
# 2. COCINA
# ==========================================
with tabs[1]:
    st.header("🍳 Monitor de Cocina")
    st.markdown("""<audio autoplay="true"><source src="https://www.soundjay.com/misc/sounds/bell-ringing-05.mp3" type="audio/mpeg"></audio>""", unsafe_allow_html=True)
    
    if len(ops) > 1:
        pedidos_cocina = [ (i, f) for i, f in enumerate(ops[1:], start=2) if len(f) > 8 and f[8] == hoy_str and f[6] == "Preparando" and f[2] == "Cocina" ]
        pedidos_cocina.sort(key=lambda x: (x[1][4] != "Ahora", x[1][5]))
        
        for i, f in pedidos_cocina:
            urgente = "card-urgente" if f[4] == "Ahora" else ""
            st.markdown(f'<div class="card {urgente}"><h3>🛒 {f[1]}</h3><p>👤 <b>{f[0]}</b> | 🕒 {f[5]}</p><p style="color:red;">📝 Notas: {f[3]}</p></div>', unsafe_allow_html=True)
            if st.button(f"✅ Listo", key=f"l_{i}", use_container_width=True):
                sh.worksheet("Operaciones").update_cell(i, 7, "Entregado")
                leer.clear()
                st.rerun()

# ==========================================
# 3. AGENDADOS
# ==========================================
with tabs[2]:
    st.header("📅 Pedidos Agendados")
    if len(ops) > 1:
        for i, f in enumerate(ops[1:], start=2):
            if len(f) > 8 and f[8] != hoy_str and f[6] not in ["Entregado", "Listo"]:
                st.info(f"🛒 {f[1]} - 📅 {f[8]} | 👤 {f[0]}")
                if st.button("✅ Marcar Entregado", key=f"ae_{i}"):
                    sh.worksheet("Operaciones").update_cell(i, 7, "Entregado")
                    leer.clear()
                    st.rerun()

# ==========================================
# 4. PAGOS
# ==========================================
with tabs[3]:
    st.header("📓 Libreta de Pagos")
    if len(deu) > 1:
        lista_nombres = sorted(list(set(f[0] for f in deu[1:] if len(f) > 0)))
        busqueda = st.selectbox("🔍 Buscar Cliente:", ["Todos"] + lista_nombres)
        
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
            if (busqueda == "Todos" or c == busqueda) and round(info["tot"], 2) > 0:
                with st.expander(f"👤 {c} - Debe: ${round(info['tot'], 2)}", expanded=(busqueda!="Todos")):
                    for h in info["hist"]:
                        fila = h["data"]
                        det = fila[3] if len(fila)>3 else ""
                        fec = fila[4] if len(fila)>4 else ""
                        st.write(f"{'🔴' if fila[1]=='Deuda' else '🟢'} **${fila[2]}** ({fec}) - *{det}*")
                    
                    st.divider()
                    c_ab, c_btn = st.columns(2)
                    with c_ab: 
                        abono = st.number_input(f"Abonar:", min_value=0.0, max_value=float(info['tot']), value=float(info['tot']), key=f"n_{c}")
                    with c_btn:
                        st.write("")
                        if st.button("Registrar Pago", key=f"p_{c}", use_container_width=True):
                            sh.worksheet("Deudas").append_row([c, "Abono", abono, "Pago", hoy_str])
                            leer.clear()
                            st.rerun()

# ==========================================
# 5. INVENTARIO (Control y Añadir Producto)
# ==========================================
with tabs[4]:
    st.header("📦 Inventario Exclusivo (Tortas/Pan)")
    
    with st.expander("➕ Añadir Producto a Inventario"):
        with st.form("add_inv"):
            n_prod = st.text_input("Nombre (Ej. Torta Cubana):")
            n_cat = st.selectbox("Categoría:", ["🥪 Tortas", "🍞 Pan", "🥗 Platillos"])
            n_pre = st.number_input("Precio ($):", step=5.0)
            n_stk = st.number_input("Stock de hoy:", min_value=0)
            if st.form_submit_button("Guardar en Menú e Inventario"):
                if n_prod.strip():
                    sh.worksheet("Inventario").append_row([n_prod, n_stk, n_cat, n_pre, "Activo"])
                    leer.clear()
                    st.success(f"{n_prod} agregado con éxito.")
                    st.rerun()
                else:
                    st.warning("Escribe el nombre.")

    st.divider()
    st.write("Actualiza el stock físico del día:")
    if len(inv) > 1:
        with st.form("inv_form"):
            nv = {}
            for i, f in enumerate(inv[1:], start=2):
                if len(f) >= 2:
                    try: val = int(f[1])
                    except: val = 0
                    nv[i] = st.number_input(f[0], value=val, min_value=0)
            if st.form_submit_button("💾 Guardar Inventario"):
                for i, v in nv.items(): sh.worksheet("Inventario").update_cell(i, 2, v)
                leer.clear()
                st.rerun()

# ==========================================
# 6. MODO DIOS (Excel Integrado para Admin)
# ==========================================
if st.session_state.admin_mode:
    with tabs[5]:
        st.error("🔒 ZONA ADMIN: EDITOR MAESTRO")
        st.write("Aquí puedes editar, borrar o cambiar estados de CUALQUIER ticket. Modifica la tabla y dale a Guardar.")
        
        if len(ops) > 0:
            df_ops = pd.DataFrame(ops[1:], columns=ops[0])
            # Data Editor interactivo de Pandas
            edited_df = st.data_editor(df_ops, num_rows="dynamic", use_container_width=True)
            
            if st.button("💾 Guardar Cambios en la Base de Datos", type="primary"):
                # Gspread no acepta valores nulos (NaN), los rellenamos
                edited_df = edited_df.fillna("")
                datos_nuevos = [edited_df.columns.values.tolist()] + edited_df.values.tolist()
                ws = sh.worksheet("Operaciones")
                ws.clear()
                ws.update("A1", datos_nuevos)
                leer.clear()
                st.success("¡Base de datos actualizada con éxito!")
                st.rerun()