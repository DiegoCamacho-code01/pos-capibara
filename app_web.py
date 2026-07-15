import streamlit as st
import gspread
import json
import pandas as pd
from datetime import datetime, timedelta, timezone

# ==========================================
# 1. CONFIGURACIÓN VISUAL Y CSS
# ==========================================
st.set_page_config(page_title="POS Capibara", layout="wide", initial_sidebar_state="collapsed")

st.markdown("""
<style>
    /* Botones Táctiles Gigantes */
    div.stButton > button { height: 75px; border-radius: 12px; border: 2px solid #0073E6; font-weight: bold; background-color: #E6F2FF; color: #002244; font-size: 16px;}
    div.stButton > button:active { background-color: #99CCFF; transform: scale(0.95); }
    div.stButton > button[kind="primary"] { background-color: #0073E6; color: white; border: none; }
    
    /* ALERTA AMARILLA PARA STOCK BAJO */
    .btn-alerta > button { background-color: #FFD700 !important; color: #000 !important; border: 2px solid #B8860B !important; }
    
    /* Barra Pegajosa (Menú) */
    .sticky-header { position: sticky; top: 0; background-color: white; z-index: 999; padding: 10px 0; border-bottom: 2px solid #f0f2f6; }
    
    /* Reemplazo de círculos por cuadros táctiles */
    div[role="radiogroup"] { flex-wrap: wrap; gap: 5px; }
    div[role="radiogroup"] > label > div:first-child { display: none !important; }
    div[role="radiogroup"] > label { border: 2px solid #0073E6; padding: 10px 20px !important; border-radius: 12px !important; cursor: pointer; transition: 0.2s; }
    div[role="radiogroup"] > label[data-checked="true"] { background-color: #0073E6 !important; color: white !important; }
    
    /* Tarjetas Visuales (Cocina y Agendados) */
    .card { background-color: #f8f9fa; padding: 15px; border-radius: 10px; border-left: 5px solid #0073E6; margin-bottom: 10px; }
    .card-urgente { border-left: 5px solid #ff4b4b; }
</style>
""", unsafe_allow_html=True)

zona_mx = timezone(timedelta(hours=-6))
hoy_obj = datetime.now(zona_mx).date()
hoy_str = hoy_obj.strftime("%d/%m/%Y")

# ==========================================
# 2. CONEXIÓN A BASE DE DATOS Y CACHÉ
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

@st.cache_data(ttl=2)
def leer():
    if not sh: return [], [], [], []
    try: 
        return (sh.worksheet("Operaciones").get_all_values(), 
                sh.worksheet("Inventario").get_all_values(), 
                sh.worksheet("Deudas").get_all_values(),
                sh.worksheet("Historial").get_all_values())
    except Exception as e: 
        st.error(f"Error de lectura: {e}")
        return [], [], [], []

ops, inv, deu, hist = leer()

# ==========================================
# 3. MEMORIA DE CAJERO Y ESTADOS GLOBALES
# ==========================================
if 'cajero' not in st.session_state:
    if st.query_params.get("u", ""):
        st.session_state.cajero = st.query_params.get("u", "")
    else:
        st.title("👋 Bienvenido al Sistema")
        st.write("Registra tu nombre para iniciar el turno.")
        st.info("💡 **Tip:** Después de entrar, agrega esta página a la pantalla de inicio de tu celular. Así guardará tu nombre y no te lo volverá a pedir jamás.")
        nom = st.text_input("NOMBRE DEL CAJERO:")
        if st.button("Entrar al Sistema", type="primary"):
            if nom.strip():
                st.query_params["u"] = nom.strip()
                st.session_state.cajero = nom.strip()
                st.rerun()
        st.stop()

if 'cart' not in st.session_state: st.session_state.cart = []
if 'puesto_cart' not in st.session_state: st.session_state.puesto_cart = []
if 'admin_mode' not in st.session_state: st.session_state.admin_mode = False
if 'cat_apagadas' not in st.session_state: st.session_state.cat_apagadas = []

# ==========================================
# 4. CONSTRUCCIÓN DEL MENÚ (Fijo + Inventario)
# ==========================================
MENU_BASE = {
    "☕ Café": {"Cafe Vainilla": 25, "Cafe Avellana": 25, "Café Clásico": 25},
    "🥤 Frappés": {"Fresa": 65, "Taro": 65, "Chai": 65, "Matcha": 65, "Rompope": 65, "Red Velvet": 65, "Pistache": 65, "Galleta": 65, "Mora": 65, "Cereza": 65, "Refresher Darks": 65, "Cafe": 65, "Moka": 65, "Oreo": 65, "Chocolate": 65},
    "🧊 Bebidas Frías": {"Fresa": 45, "Taro": 45, "Chai": 45, "Matcha": 45, "Rompope": 45, "Red Velvet": 45, "Pistache": 45, "Galleta": 45, "Mora": 45, "Cereza": 45, "Refresher Darks": 45, "Cafe": 45, "Moka": 45, "Oreo": 45, "Chocolate": 45},
    "🥛 Esquimos": {"Fresa": 45, "Taro": 45, "Chai": 45, "Matcha": 45, "Rompope": 45, "Red Velvet": 45, "Pistache": 45, "Galleta": 45, "Mora": 45, "Cereza": 45, "Refresher Darks": 45, "Cafe": 45, "Moka": 45, "Oreo": 45, "Chocolate": 45},
    "🍧 Chamoyadas": {"Fresa": 65, "Mango": 65, "Temporada": 65},
    "🥗 Platillos": {"Ensalada": 65, "Sandwich": 65, "Plato de Chilaquiles": 50, "Torta de Chilaquiles": 65},
    "🍞 Pan": {"Pan de Dulce": 25, "Telera": 5}
}

MENU = MENU_BASE.copy()
dict_inv = {}

if len(inv) > 1:
    for row in inv[1:]:
        if len(row) >= 5 and str(row[4]).strip().lower() == "activo":
            prod, stock_str, cat, precio_str = row[0], row[1], row[2], row[3]
            try: dict_inv[prod] = int(stock_str)
            except: dict_inv[prod] = 0
            
            if cat not in MENU: MENU[cat] = {}
            try: MENU[cat][prod] = float(precio_str)
            except: MENU[cat][prod] = 0.0

for c_apagada in st.session_state.cat_apagadas:
    if c_apagada in MENU: del MENU[c_apagada]

# ==========================================
# 5. MENÚ LATERAL Y PESTAÑAS
# ==========================================
with st.sidebar:
    st.write(f"👤 **Cajero Activo:** {st.session_state.cajero}")
    st.divider()
    pin = st.text_input("PIN Admin (Oculto):", type="password")
    if pin == "1234":
        st.session_state.admin_mode = True
        st.success("✅ Modo Admin Activado")
    else:
        st.session_state.admin_mode = False

pestanas = ["🛒 Carrito", "🏪 Puesto", "🍳 Cocina", "✅ Listos", "📅 Agendados", "📓 Pagos", "📦 Inventario"]
if st.session_state.admin_mode: pestanas.append("⚙️ Admin")
tabs = st.tabs(pestanas)

# ==========================================
# PESTAÑA 1: CARRITO (Principal)
# ==========================================
with tabs[0]:
    st.markdown('<div class="sticky-header">', unsafe_allow_html=True)
    cat_seleccionada = st.radio("Navegación:", list(MENU.keys()), horizontal=True, label_visibility="collapsed")
    st.markdown('</div>', unsafe_allow_html=True)
    
    st.write(f"### {cat_seleccionada}")
    cols = st.columns(2)
    for i, (n, p) in enumerate(MENU[cat_seleccionada].items()):
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
            if st.button(btn_txt, use_container_width=True, disabled=agotado, key=f"c_{n}"):
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
                item['notas'] = st.text_input("Notas", key=f"cn_{idx}", label_visibility="collapsed", placeholder="Notas...")
                if "Chilaquiles" in item['prod'] and "Torta" not in item['prod']: 
                    item['pan'] = st.checkbox("🥖 ¿Incluir Pan Telera?", key=f"cpan_{idx}")
            total += item['precio']
            
        st.write(f"### 💰 Total: ${total}")
        st.divider()
        
        # Casilla Única de Cliente
        c_nom, c_pago = st.columns([1.5, 1])
        with c_nom: 
            cliente = st.text_input("👤 Cliente (Empieza a escribir para registrar o autocompletar):", placeholder="Ej. Carlos")
        with c_pago: 
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
                if not cliente.strip() and pago == "Pendiente / Fiado" and not st.session_state.admin_mode: 
                    st.warning("⚠️ Escribe un nombre para fiar.")
                else:
                    nom_final = cliente.strip() if cliente.strip() else "Mostrador"
                    panes = sum(1 for i in st.session_state.cart if "pan telera" in i['prod'].lower() or "torta de chilaquiles" in i['prod'].lower() or i['pan'])
                    h_real = datetime.now(zona_mx).strftime("%H:%M") if hora_fin == "Ahora" else f"{fecha_fin} {hora_fin}"
                    
                    for i in st.session_state.cart:
                        dest = "Cocina" if any(x in i['prod'] for x in ["Platillo", "Frappé", "Esquimo", "Chamoyada", "Bebidas Frías", "Cafetería"]) else "Directo"
                        est = "Pendiente" if dia_tipo != "Hoy" or tiempo != "Ahora" else ("Preparando" if dest == "Cocina" else "Entregado")
                        # AQUÍ SE GUARDA EL NOMBRE DEL CAJERO
                        sh.worksheet("Operaciones").append_row([nom_final, i['prod'], dest, i['notas'], tiempo, h_real, est, total if i == st.session_state.cart[0] else 0, fecha_fin, st.session_state.cajero])
                    
                    if pago == "Pendiente / Fiado": 
                        sh.worksheet("Deudas").append_row([nom_final, "Deuda", total, "Ticket", h_real])
                    
                    if len(inv) > 1:
                        for idx, row in enumerate(inv[1:], start=2):
                            p_nom = row[0]
                            try: p_stock = int(row[1])
                            except: p_stock = 0
                            
                            comprados = sum(1 for p in st.session_state.cart if p['prod'] == p_nom)
                            if comprados > 0: sh.worksheet("Inventario").update_cell(idx, 2, p_stock - comprados)
                            
                            if p_nom == "Pan Telera" and panes > 0:
                                sh.worksheet("Inventario").update_cell(idx, 2, p_stock - panes)
                                
                    st.session_state.cart = []
                    leer.clear()
                    st.success("¡Pedido Enviado!")
                    st.rerun()

# ==========================================
# PESTAÑA 2: CAJA PUESTO (Bebidas Rápidas)
# ==========================================
with tabs[1]:
    st.header("🏪 Caja Puesto (Solo Bebidas)")
    categorias_puesto = [c for c in MENU.keys() if any(x in c for x in ["Café", "Frappés", "Bebidas", "Esquimos", "Chamoyadas"])]
    
    if categorias_puesto:
        cat_p = st.radio("Navegación Puesto:", categorias_puesto, horizontal=True, label_visibility="collapsed")
        
        cols_p = st.columns(2)
        for i, (n, p) in enumerate(MENU[cat_p].items()):
            with cols_p[i%2]:
                if st.button(f"{n}\n${p}", use_container_width=True, key=f"p_{n}"):
                    st.session_state.puesto_cart.append({"prod": n, "precio": p, "notas": ""})
                    st.rerun()
                    
        if st.session_state.puesto_cart:
            st.divider()
            total_p = 0
            for idx, item in enumerate(st.session_state.puesto_cart):
                c1, c2, c3 = st.columns([3, 1, 2])
                with c1: st.write(f"▪️ {item['prod']}")
                with c2: st.write(f"${item['precio']}")
                with c3: item['notas'] = st.text_input("Notas", key=f"pn_{idx}", label_visibility="collapsed")
                total_p += item['precio']
                
            st.write(f"### 💰 Total Puesto: ${total_p}")
            
            c_nom_p, c_pag_p = st.columns([1.5, 1])
            with c_nom_p: cliente_p = st.text_input("👤 Cliente:", key="cli_p")
            with c_pag_p: pago_p = st.radio("💵 Pago", ["Pagado", "Pendiente / Fiado"], index=0, horizontal=True, key="pag_p")
            
            c_bp, c_ep = st.columns(2)
            with c_bp:
                if st.button("🗑️ Borrar", key="b_puesto", use_container_width=True): 
                    st.session_state.puesto_cart = []
                    st.rerun()
            with c_ep:
                if st.button("COBRAR Y ENTREGAR", type="primary", use_container_width=True):
                    nom_final_p = cliente_p.strip() if cliente_p.strip() else "Mostrador"
                    h_real_p = datetime.now(zona_mx).strftime("%H:%M")
                    
                    for i in st.session_state.puesto_cart:
                        sh.worksheet("Operaciones").append_row([nom_final_p, i['prod'], "Directo", i['notas'], "Ahora", h_real_p, "Entregado", total_p if i == st.session_state.puesto_cart[0] else 0, hoy_str, st.session_state.cajero])
                    
                    if pago_p == "Pendiente / Fiado": 
                        sh.worksheet("Deudas").append_row([nom_final_p, "Deuda", total_p, "Ticket Puesto", h_real_p])
                    
                    st.session_state.puesto_cart = []
                    leer.clear()
                    st.success("¡Cobro Registrado!")
                    st.rerun()

# ==========================================
# PESTAÑA 3: COCINA (Sonido y Cancelación)
# ==========================================
with tabs[2]:
    st.header("🍳 Monitor de Cocina")
    st.markdown("""<audio autoplay="true"><source src="https://www.soundjay.com/misc/sounds/bell-ringing-05.mp3" type="audio/mpeg"></audio>""", unsafe_allow_html=True)
    
    if len(ops) > 1:
        pedidos_cocina = [ (i, f) for i, f in enumerate(ops[1:], start=2) if len(f) > 8 and f[8] == hoy_str and f[6] == "Preparando" and f[2] == "Cocina" ]
        pedidos_cocina.sort(key=lambda x: (x[1][4] != "Ahora", x[1][5])) 
        
        if pedidos_cocina:
            for i, f in pedidos_cocina:
                urgente = "card-urgente" if f[4] == "Ahora" else ""
                st.markdown(f'<div class="card {urgente}"><h3>🛒 {f[1]}</h3><p>👤 <b>{f[0]}</b> | 🕒 {f[5]}</p><p style="color:red;">📝 Notas: {f[3]}</p></div>', unsafe_allow_html=True)
                
                c_listo, c_canc = st.columns(2)
                with c_listo:
                    if st.button(f"✅ Listo", key=f"l_{i}", use_container_width=True):
                        sh.worksheet("Operaciones").update_cell(i, 7, "Listo")
                        leer.clear()
                        st.rerun()
                with c_canc:
                    if st.session_state.admin_mode:
                        if st.button("🗑️ Forzar Cancelación", key=f"fc_{i}", use_container_width=True):
                            sh.worksheet("Operaciones").delete_rows(i)
                            leer.clear()
                            st.rerun()
        else:
            st.info("Sin pedidos en cocina por el momento.")

# ==========================================
# PESTAÑA 4: LISTOS PARA ENTREGA
# ==========================================
with tabs[3]:
    st.header("✅ Pedidos Listos")
    if len(ops) > 1:
        hay_listos = False
        for i, f in enumerate(ops[1:], start=2):
            if len(f) > 8 and f[8] == hoy_str and f[6] == "Listo":
                hay_listos = True
                st.success(f"🛒 **{f[1]}** | 👤 {f[0]}")
                if st.button("✅ Entregar al Cliente", key=f"ent_{i}"):
                    sh.worksheet("Operaciones").update_cell(i, 7, "Entregado")
                    leer.clear()
                    st.rerun()
        if not hay_listos: st.write("No hay pedidos esperando entrega.")

# ==========================================
# PESTAÑA 5: AGENDADOS (Admin edita todo)
# ==========================================
with tabs[4]:
    st.header("📅 Pedidos Agendados / Futuros")
    if len(ops) > 1:
        futuros = 0
        for i, f in enumerate(ops[1:], start=2):
            if len(f) > 8 and f[8] != hoy_str and f[6] not in ["Entregado", "Listo"]:
                futuros += 1
                st.info(f"🛒 {f[1]} - 📅 {f[8]} | 👤 {f[0]}")
                
                if st.session_state.admin_mode:
                    with st.expander("✏️ Editar Pedido"):
                        n_cli = st.text_input("Cliente", value=f[0], key=f"ac_{i}")
                        n_fec = st.text_input("Fecha (DD/MM/YYYY)", value=f[8], key=f"af_{i}")
                        n_not = st.text_input("Notas", value=f[3], key=f"an_{i}")
                        c_g, c_b = st.columns(2)
                        with c_g:
                            if st.button("💾 Guardar Edición", key=f"ag_{i}"):
                                sh.worksheet("Operaciones").update_cell(i, 1, n_cli)
                                sh.worksheet("Operaciones").update_cell(i, 9, n_fec)
                                sh.worksheet("Operaciones").update_cell(i, 4, n_not)
                                leer.clear()
                                st.rerun()
                        with c_b:
                            if st.button("🗑️ Borrar Pedido", key=f"ab_{i}"):
                                sh.worksheet("Operaciones").delete_rows(i)
                                leer.clear()
                                st.rerun()
                else:
                    if st.button("✅ Marcar Entregado", key=f"ae_{i}"):
                        sh.worksheet("Operaciones").update_cell(i, 7, "Entregado")
                        leer.clear()
                        st.rerun()
        if futuros == 0: st.success("No hay pedidos a futuro.")

# ==========================================
# PESTAÑA 6: PAGOS (Condonar Deudas)
# ==========================================
with tabs[5]:
    st.header("📓 Libreta de Pagos")
    if len(deu) > 1:
        lista_nombres = sorted(list(set(f[0] for f in deu[1:] if len(f) > 0 and f[0] != "")))
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
                            
                    if st.session_state.admin_mode:
                        if st.button("🚨 CONDONAR DEUDA (Limpiar Error)", key=f"cond_{c}", type="primary"):
                            sh.worksheet("Deudas").append_row([c, "Abono", info['tot'], "Condonado por Admin", hoy_str])
                            leer.clear()
                            st.rerun()

# ==========================================
# PESTAÑA 7: INVENTARIO (Control y Añadir)
# ==========================================
with tabs[6]:
    st.header("📦 Control de Inventario y Menú")
    
    with st.expander("➕ Añadir Nuevo Producto (Aparecerá en el Menú)"):
        with st.form("add_inv"):
            n_prod = st.text_input("Nombre del Producto:")
            
            # Puedes elegir cualquier categoría del menú para agregar tu producto
            n_cat = st.selectbox("Categoría a la que pertenece:", list(MENU.keys()))
            n_pre = st.number_input("Precio ($):", step=5.0)
            
            # Solo pide inventario físico si estás en la sección de Tortas
            if "Tortas" in n_cat:
                st.info("🥪 Has elegido Tortas. Por favor, ingresa el stock inicial:")
                n_stk = st.number_input("Stock Inicial (Aparecerá en el contador):", min_value=0, value=10)
            else:
                n_stk = "" # Los demás productos no llevan conteo estricto de stock aquí
                
            if st.form_submit_button("Guardar Producto en el Menú"):
                if n_prod.strip():
                    sh.worksheet("Inventario").append_row([n_prod, n_stk, n_cat, n_pre, "Activo"])
                    leer.clear()
                    st.success(f"¡{n_prod} agregado exitosamente a {n_cat}!")
                    st.rerun()
                else:
                    st.warning("El nombre es obligatorio.")

    st.divider()
    st.write("Actualiza el stock físico del día (Las celdas amarillas te avisarán solas en el menú):")
    
    # Vista limpia de aplicación para actualizar stock (como antes)
    if len(inv) > 1:
        with st.form("inv_form"):
            cols = st.columns(3)
            nv = {}
            contador = 0
            for i, f in enumerate(inv[1:], start=2):
                if len(f) >= 5 and str(f[4]).strip().lower() == "activo" and f[1] != "":
                    try: val = int(f[1])
                    except: val = 0
                    
                    with cols[contador % 3]:
                        nv[i] = st.number_input(f[0], value=val, min_value=0, key=f"ui_inv_{i}")
                    contador += 1
                    
            if st.form_submit_button("💾 Guardar Cambios Físicos"):
                for i, v in nv.items(): 
                    sh.worksheet("Inventario").update_cell(i, 2, v)
                leer.clear()
                st.success("Inventario actualizado correctamente.")
                st.rerun()

# ==========================================
# PESTAÑA 8: MODO ADMIN (Panel General)
# ==========================================
if st.session_state.admin_mode:
    with tabs[7]:
        st.header("⚙️ Panel de Administración")
        
        # --- NUEVO: RESUMEN DE VENTAS POR CAJERO ---
        st.subheader("👥 Ventas por Cajero (Hoy)")
        ventas_cajeros = {}
        for f in ops[1:]:
            # Verifica que el ticket sea de hoy, esté entregado y tenga registro de cajero
            if len(f) > 9 and f[8] == hoy_str and f[6] == "Entregado":
                cajero = f[9]
                try: monto = float(f[7]) if f[7] else 0.0
                except: monto = 0.0
                ventas_cajeros[cajero] = ventas_cajeros.get(cajero, 0.0) + monto
                
        if ventas_cajeros:
            for caj, monto in ventas_cajeros.items():
                st.info(f"🧑‍💼 **Cajero: {caj}** ➔ Dinero cobrado: **${monto}**")
        else:
            st.write("Aún no hay ventas registradas finalizadas en el día de hoy.")
            
        st.divider()
        
        st.subheader("🔌 Apagar Categorías")
        st.write("Si apagas una categoría aquí, desaparecerá del celular de todos inmediatamente.")
        cat_apagadas_temp = []
        for cat_base in MENU.keys():
            encendida = st.toggle(cat_base, value=(cat_base not in st.session_state.cat_apagadas))
            if not encendida: cat_apagadas_temp.append(cat_base)
        
        if st.button("Aplicar Cambios al Menú Visual"):
            st.session_state.cat_apagadas = cat_apagadas_temp
            st.success("Menú actualizado en todos los dispositivos.")
            st.rerun()
            
        st.divider()
        
        st.subheader("🌙 Corte de Caja y Limpieza del Excel")
        st.warning("⚠️ Esto sumará los tickets, guardará el resumen en 'Historial' y BORRARÁ todos los tickets Entregados de hoy.")
        
        if st.button("🚨 EJECUTAR CORTE DE DÍA", type="primary"):
            try:
                ws_ops = sh.worksheet("Operaciones")
                ws_hist = sh.worksheet("Historial")
                
                filas_mantener = [ops[0]] 
                ventas_hoy = 0
                tickets_borrados = 0
                
                for f in ops[1:]:
                    if len(f) > 8 and f[8] == hoy_str and f[6] == "Entregado":
                        try: ventas_hoy += float(f[7])
                        except: pass
                        tickets_borrados += 1
                    else:
                        filas_mantener.append(f)
                
                if tickets_borrados > 0:
                    ws_hist.append_row([hoy_str, "Corte de Caja", f"Venta Total: ${ventas_hoy}", f"Tickets Procesados: {tickets_borrados}"])
                    ws_ops.clear()
                    ws_ops.update("A1", filas_mantener)
                    leer.clear()
                    st.success(f"Corte exitoso: ${ventas_hoy}. Excel liberado.")
                    st.rerun()
                else:
                    st.info("No hay tickets completados de hoy listos para archivar.")
            except Exception as e:
                st.error(f"Error al hacer corte: {e}")
                
        st.divider()
        
        st.subheader("🛠️ Editor Maestro (Operaciones)")
        st.write("Modifica nombres, precios, o borra estados directamente. Toca 'Guardar' para aplicar al Excel.")
        
        if len(ops) > 0:
            df_ops = pd.DataFrame(ops[1:], columns=ops[0])
            edited_df = st.data_editor(df_ops, num_rows="dynamic", use_container_width=True)
            
            if st.button("💾 Inyectar Cambios a Google Sheets"):
                edited_df = edited_df.fillna("")
                datos_nuevos_ops = [edited_df.columns.values.tolist()] + edited_df.values.tolist()
                ws_o = sh.worksheet("Operaciones")
                ws_o.clear()
                ws_o.update("A1", datos_nuevos_ops)
                leer.clear()
                st.success("¡Base de datos de operaciones sobreescrita con éxito!")
                st.rerun()