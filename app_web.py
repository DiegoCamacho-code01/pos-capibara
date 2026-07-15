import streamlit as st
import gspread
import json
from datetime import datetime, timedelta, timezone

# --- CONFIGURACIÓN DE PÁGINA Y CSS ---
st.set_page_config(page_title="POS Capibara", layout="wide", initial_sidebar_state="collapsed")

st.markdown("""
<style>
    div.stButton > button { height: 75px; border-radius: 12px; border: 2px solid #0073E6; font-weight: bold; background-color: #E6F2FF; color: #002244; font-size: 16px;}
    div.stButton > button:active { background-color: #99CCFF; transform: scale(0.95); }
    div.stButton > button[kind="primary"] { background-color: #0073E6; color: white; border: none; }
    
    /* Botón Amarillo para Poco Stock */
    .btn-alerta > button { background-color: #FFD700 !important; color: #000 !important; border: 2px solid #B8860B !important; }
    
    /* Barra Pegajosa */
    .sticky-header { position: sticky; top: 0; background-color: white; z-index: 999; padding: 10px 0; border-bottom: 2px solid #f0f2f6; }
    
    /* Cuadros Táctiles (reemplazo de círculos) */
    div[role="radiogroup"] { flex-wrap: wrap; gap: 5px; }
    div[role="radiogroup"] > label > div:first-child { display: none !important; }
    div[role="radiogroup"] > label { border: 2px solid #0073E6; padding: 10px 20px !important; border-radius: 12px !important; cursor: pointer; transition: 0.2s; }
    div[role="radiogroup"] > label[data-checked="true"] { background-color: #0073E6 !important; color: white !important; }
    
    /* Tarjetas Interactivas */
    .card { background-color: #f8f9fa; padding: 15px; border-radius: 10px; border-left: 5px solid #0073E6; margin-bottom: 10px; }
    .card-urgente { border-left: 5px solid #ff4b4b; }
</style>
""", unsafe_allow_html=True)

zona_mx = timezone(timedelta(hours=-6))
hoy_obj = datetime.now(zona_mx).date()
hoy_str = hoy_obj.strftime("%d/%m/%Y")

# --- CONEXIÓN Y LECTURA CACHÉ ---
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
    try: return (sh.worksheet("Operaciones").get_all_values(), sh.worksheet("Inventario").get_all_records(), sh.worksheet("Deudas").get_all_records())
    except: return [], [], []

ops, inv, deu = leer()

# --- PANTALLA DE REGISTRO INICIAL (MEMORIA DE USUARIO) ---
if 'cajero' not in st.session_state:
    # Intenta leer si el usuario ya guardó el enlace con su nombre
    nombre_guardado = st.query_params.get("u", "")
    if nombre_guardado:
        st.session_state.cajero = nombre_guardado
    else:
        st.title("👋 Bienvenido")
        st.write("Por favor, identifícate para iniciar el turno.")
        nom = st.text_input("NOMBRE:")
        if st.button("Entrar", type="primary"):
            if nom.strip():
                st.query_params["u"] = nom.strip() # Ancla el nombre al enlace
                st.session_state.cajero = nom.strip()
                st.rerun()
            else:
                st.warning("El nombre es obligatorio.")
        st.stop() # Detiene la app aquí hasta que pongan el nombre

# --- MENÚ BASE Y DINÁMICO (Desde Inventario) ---
MENU = {
    "☕ Café": {"Cafe Vainilla": 25, "Cafe Avellana": 25, "Café Clásico": 25},
    "🥤 Frappés": {"Fresa": 65, "Taro": 65, "Oreo": 65},
    "🧊 Bebidas Frías": {"Fresa": 45, "Taro": 45, "Oreo": 45},
    "🥪 Tortas": {"Milanesa de Res": 40, "Milanesa de Pollo": 40},
    "🥗 Platillos": {"Plato de Chilaquiles": 50, "Torta de Chilaquiles": 40}
}
dict_inv = {}
categorias_apagadas = st.session_state.get('cat_apagadas', [])

for f in inv:
    if str(f.get("Estado", "")).strip().lower() == "activo":
        cat = f.get("Categoria", "Otros")
        prod = f["Producto"]
        precio = float(f.get("Precio", 0))
        if cat not in MENU: MENU[cat] = {}
        MENU[cat][prod] = precio
        dict_inv[prod] = int(f.get("Stock", 1000))

# Quitar categorías apagadas por el Admin
for c_apagada in categorias_apagadas:
    if c_apagada in MENU: del MENU[c_apagada]

# --- VARIABLES DE SESIÓN ---
if 'cart' not in st.session_state: st.session_state.cart = []
if 'admin_mode' not in st.session_state: st.session_state.admin_mode = False

# --- MENÚ LATERAL (ACCESO ADMIN) ---
with st.sidebar:
    st.write(f"👤 **Cajero:** {st.session_state.cajero}")
    st.divider()
    pin = st.text_input("PIN Admin:", type="password")
    if pin == "1234":
        st.session_state.admin_mode = True
        st.success("Modo Dios Activado")
    else:
        st.session_state.admin_mode = False

# --- PESTAÑAS ---
pestanas = ["🛒 Carrito", "🍳 Cocina", "📅 Agendados", "📓 Pagos", "📦 Inventario"]
if st.session_state.admin_mode: pestanas.append("⚙️ Admin")
tabs = st.tabs(pestanas)

# ==========================================
# 1. CARRITO UNIFICADO Y RÁPIDO
# ==========================================
with tabs[0]:
    st.markdown('<div class="sticky-header">', unsafe_allow_html=True)
    cat = st.radio("Navegación:", list(MENU.keys()), horizontal=True, label_visibility="collapsed")
    st.markdown('</div>', unsafe_allow_html=True)
    
    cols = st.columns(2)
    for i, (n, p) in enumerate(MENU[cat].items()):
        stock = dict_inv.get(n, 1000)
        btn_txt = f"{n}\n${p}"
        agotado = stock <= 0
        alerta = False
        
        if stock < 1000: # Si es un producto inventariado
            if agotado: btn_txt += "\n(AGOTADO)"
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
        for idx, item in enumerate(st.session_state.cart):
            st.write(f"▪️ {item['prod']} - ${item['precio']}")
            item['notas'] = st.text_input("Notas", key=f"n_{idx}", label_visibility="collapsed", placeholder="Notas...")
            if "Chilaquiles" in item['prod'] and "Torta" not in item['prod']: 
                item['pan'] = st.checkbox("🥖 ¿Incluir Pan Telera?", key=f"pan_{idx}")
        
        total = sum(i['precio'] for i in st.session_state.cart)
        st.write(f"### 💰 Total: ${total}")
        
        st.divider()
        c_nom, c_pago = st.columns([1.5, 1])
        with c_nom: 
            # Buscador Inteligente Nativo
            lista_clientes = ["Mostrador"] + sorted(list(set(f["Cliente"] for f in deu if f["Cliente"] != "Mostrador"))) + ["✨ NUEVO"]
            cliente_sel = st.selectbox("👤 Cliente (Escribe para buscar):", lista_clientes)
            cliente = st.text_input("Nombre Nuevo:") if cliente_sel == "✨ NUEVO" else cliente_sel
        with c_pago: 
            pago = st.radio("💵 Pago", ["Pagado", "Fiado"], horizontal=True)

        c_dia, c_hora = st.columns(2)
        with c_dia:
            dia_tipo = st.radio("📅 Día:", ["Hoy", "Mañana", "Otro"], horizontal=True)
            if dia_tipo == "Otro": fecha_fin = st.date_input("Selecciona Día en Calendario:").strftime("%d/%m/%Y")
            elif dia_tipo == "Mañana": fecha_fin = (hoy_obj + timedelta(days=1)).strftime("%d/%m/%Y")
            else: fecha_fin = hoy_str
            
        with c_hora:
            tiempo = st.radio("⏱️ Horario:", ["Ahora", "Más tarde"], horizontal=True)
            if tiempo == "Más tarde": hora_fin = st.time_input("Selecciona Hora en Reloj:", step=1800).strftime("%H:%M")
            else: hora_fin = "Ahora"
        
        c_borrar, c_enviar = st.columns(2)
        with c_borrar:
            if st.button("🗑️ Borrar", use_container_width=True): 
                st.session_state.cart = []
                st.rerun()
        with c_enviar:
            if st.button("🚀 ENVIAR PEDIDO", type="primary", use_container_width=True):
                # El Admin puede fiar a mostrador, los empleados no.
                if pago == "Fiado" and cliente.strip() in ["", "Mostrador"] and not st.session_state.admin_mode: 
                    st.warning("⚠️ Nombre obligatorio para fiar.")
                else:
                    panes = sum(1 for i in st.session_state.cart if "pan telera" in i['prod'].lower() or "torta de chilaquiles" in i['prod'].lower() or i['pan'])
                    h_real = datetime.now(zona_mx).strftime("%H:%M") if hora_fin == "Ahora" else f"{fecha_fin} {hora_fin}"
                    
                    for i in st.session_state.cart:
                        dest = "Cocina" if any(x in i['prod'] for x in ["Platillo", "Frappé", "Esquimo", "Chamoyada"]) else "Directo"
                        est = "Pendiente" if dia_tipo != "Hoy" or tiempo != "Ahora" else ("Preparando" if dest == "Cocina" else "Entregado")
                        sh.worksheet("Operaciones").append_row([cliente, i['prod'], dest, i['notas'], tiempo, h_real, est, total if i == st.session_state.cart[0] else 0, fecha_fin, st.session_state.cajero])
                    
                    if pago == "Fiado": 
                        sh.worksheet("Deudas").append_row([cliente, "Deuda", total, "Ticket", h_real])
                    
                    if panes > 0:
                        for idx, row in enumerate(inv, start=2):
                            if row["Producto"] == "Pan Telera": sh.worksheet("Inventario").update_cell(idx, 2, int(row["Stock"]) - panes)
                            
                    st.session_state.cart = []
                    leer.clear()
                    st.success("¡Pedido Enviado!")
                    st.rerun()

# ==========================================
# 2. COCINA (Con sonido y cancelación de Admin)
# ==========================================
with tabs[1]:
    st.header("🍳 Monitor de Cocina")
    # Alerta sonora simple HTML
    st.markdown("""<audio autoplay="true"><source src="https://www.soundjay.com/misc/sounds/bell-ringing-05.mp3" type="audio/mpeg"></audio>""", unsafe_allow_html=True)
    
    pedidos_cocina = [ (i, f) for i, f in enumerate(ops[1:], start=2) if len(f) > 8 and f[8] == hoy_str and f[6] == "Preparando" and f[2] == "Cocina" ]
    pedidos_cocina.sort(key=lambda x: (x[1][4] != "Ahora", x[1][5])) # Orden de prioridad
    
    for i, f in pedidos_cocina:
        urgente = "card-urgente" if f[4] == "Ahora" else ""
        st.markdown(f'<div class="card {urgente}"><h3>🛒 {f[1]}</h3><p>👤 <b>{f[0]}</b> | 🕒 {f[5]}</p><p style="color:red;">📝 Notas: {f[3]}</p></div>', unsafe_allow_html=True)
        
        c_listo, c_canc = st.columns(2)
        with c_listo:
            if st.button(f"✅ Listo", key=f"l_{i}", use_container_width=True):
                sh.worksheet("Operaciones").update_cell(i, 7, "Entregado")
                leer.clear()
                st.rerun()
        with c_canc:
            if st.session_state.admin_mode:
                if st.button("🗑️ Forzar Cancelación", key=f"c_{i}", use_container_width=True):
                    sh.worksheet("Operaciones").delete_rows(i)
                    leer.clear()
                    st.rerun()

# ==========================================
# 3. AGENDADOS (Con Edición Total para Admin)
# ==========================================
with tabs[2]:
    st.header("📅 Pedidos Agendados")
    for i, f in enumerate(ops[1:], start=2):
        if len(f) > 8 and f[8] != hoy_str and f[6] not in ["Entregado", "Listo"]:
            st.info(f"🛒 {f[1]} - 📅 {f[8]} | 👤 {f[0]}")
            
            if st.session_state.admin_mode:
                with st.expander("✏️ Editar Pedido (Admin)"):
                    n_cli = st.text_input("Cliente", value=f[0], key=f"ac_{i}")
                    n_fec = st.text_input("Fecha (DD/MM/YYYY)", value=f[8], key=f"af_{i}")
                    n_not = st.text_input("Notas", value=f[3], key=f"an_{i}")
                    c_g, c_b = st.columns(2)
                    with c_g:
                        if st.button("💾 Guardar", key=f"ag_{i}"):
                            sh.worksheet("Operaciones").update_cell(i, 1, n_cli)
                            sh.worksheet("Operaciones").update_cell(i, 9, n_fec)
                            sh.worksheet("Operaciones").update_cell(i, 4, n_not)
                            leer.clear()
                            st.rerun()
                    with c_b:
                        if st.button("🗑️ Borrar", key=f"ab_{i}"):
                            sh.worksheet("Operaciones").delete_rows(i)
                            leer.clear()
                            st.rerun()
            else:
                if st.button("✅ Marcar Entregado", key=f"ae_{i}"):
                    sh.worksheet("Operaciones").update_cell(i, 7, "Entregado")
                    leer.clear()
                    st.rerun()

# ==========================================
# 4. PAGOS (Condonar y Edición Total Admin)
# ==========================================
with tabs[3]:
    st.header("📓 Libreta de Pagos")
    busqueda = st.selectbox("🔍 Buscar Cliente:", ["Todos"] + sorted(list(set(f["Cliente"] for f in deu))))
    
    clientes_resumen = {}
    for i, f in enumerate(deu, start=2):
        c = f["Cliente"]
        if c not in clientes_resumen: clientes_resumen[c] = {"tot": 0, "hist": []}
        m = float(f["Monto"])
        if f["Tipo"] == "Deuda": clientes_resumen[c]["tot"] += m
        else: clientes_resumen[c]["tot"] -= m
        clientes_resumen[c]["hist"].append({"idx": i, "data": f})
        
    for c, info in clientes_resumen.items():
        if (busqueda == "Todos" or c == busqueda) and round(info["tot"], 2) > 0:
            with st.expander(f"👤 {c} - Debe: ${round(info['tot'], 2)}", expanded=(busqueda!="Todos")):
                
                # Historial Editable para Admin
                for h in info["hist"]:
                    fila = h["data"]
                    idx = h["idx"]
                    st.write(f"{'🔴' if fila['Tipo']=='Deuda' else '🟢'} **${fila['Monto']}** ({fila['Fecha']}) - *{fila['Detalles']}*")
                    if st.session_state.admin_mode:
                        if st.button(f"🗑️ Borrar Registro {idx}", key=f"bp_{idx}"):
                            sh.worksheet("Deudas").delete_rows(idx)
                            leer.clear()
                            st.rerun()
                
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
                    if st.button("🚨 CONDONAR DEUDA (Perdonar)", key=f"cond_{c}"):
                        sh.worksheet("Deudas").append_row([c, "Abono", info['tot'], "Condonado por Admin", hoy_str])
                        leer.clear()
                        st.rerun()

# ==========================================
# 5. INVENTARIO 
# ==========================================
with tabs[4]:
    st.header("📦 Inventario")
    with st.form("inv_form"):
        st.write("Actualiza el stock físico:")
        nv = {}
        for i, f in enumerate(inv, start=2):
            nv[i] = st.number_input(f["Producto"], value=int(f["Stock"]), min_value=0)
        if st.form_submit_button("💾 Guardar Todo"):
            for i, v in nv.items(): sh.worksheet("Inventario").update_cell(i, 2, v)
            leer.clear()
            st.rerun()

# ==========================================
# 6. MODO ADMIN (El Panel de Control)
# ==========================================
if st.session_state.admin_mode:
    with tabs[5]:
        st.error("🔒 PANEL DE CONTROL ADMINISTRATIVO")
        
        # --- 1. BOTÓN DE PÁNICO (Apagar Categorías) ---
        st.subheader("🔌 Botón de Pánico (Menú)")
        st.write("Si apagas una categoría, desaparecerá del celular de todos inmediatamente.")
        cat_apagadas_temp = []
        for cat_base in MENU.keys():
            encendida = st.toggle(cat_base, value=(cat_base not in categorias_apagadas))
            if not encendida: cat_apagadas_temp.append(cat_base)
        
        if st.button("Aplicar Cambios al Menú"):
            st.session_state.cat_apagadas = cat_apagadas_temp
            st.success("Menú actualizado.")
            st.rerun()
            
        st.divider()
        
        # --- 2. CREAR NUEVO PRODUCTO ---
        st.subheader("📝 Añadir Nuevo Producto")
        with st.form("add_prod"):
            n_cat = st.selectbox("Categoría:", list(MENU.keys()))
            n_prod = st.text_input("Nombre:")
            n_pre = st.number_input("Precio:", step=5.0)
            n_stock = st.number_input("Stock Inicial (Pon 1000 si no quieres controlar inventario):", value=1000)
            if st.form_submit_button("Guardar en Excel"):
                sh.worksheet("Inventario").append_row([n_prod, n_stock, n_cat, n_pre, "Activo"])
                leer.clear()
                st.success("Producto creado.")
                st.rerun()
                
        st.divider()
        
        # --- 3. CORTE DE CAJA MAESTRO ---
        st.subheader("🌙 Corte de Caja y Limpieza")
        st.warning("⚠️ Esto sumará los tickets, guardará el resumen en 'Historial' y BORRARÁ los tickets Entregados de hoy para limpiar la memoria.")
        
        if st.button("🚨 EJECUTAR CORTE DE DÍA", type="primary"):
            try:
                ws_ops = sh.worksheet("Operaciones")
                ws_hist = sh.worksheet("Historial")
                
                filas_mantener = [ops[0]] # Encabezados
                ventas_hoy = 0
                tickets_borrados = 0
                
                for f in ops[1:]:
                    if len(f) > 8 and f[8] == hoy_str and f[6] == "Entregado":
                        ventas_hoy += float(f[7]) if f[7] else 0
                        tickets_borrados += 1
                    else:
                        filas_mantener.append(f)
                
                if tickets_borrados > 0:
                    # Guarda el resumen
                    ws_hist.append_row([hoy_str, "Corte de Caja", f"Venta Total: ${ventas_hoy}", f"Tickets: {tickets_borrados}"])
                    # Limpia Operaciones
                    ws_ops.clear()
                    ws_ops.update("A1", filas_mantener)
                    leer.clear()
                    st.success(f"Corte exitoso: ${ventas_hoy}. Excel limpio.")
                    st.rerun()
                else:
                    st.info("No hay tickets completados de hoy para cortar.")
            except Exception as e:
                st.error(f"Error: {e}")