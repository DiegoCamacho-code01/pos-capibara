import streamlit as st
import gspread
from datetime import datetime

# --- CONFIGURACIÓN DE PÁGINA Y CSS (MODO APP) ---
st.set_page_config(page_title="POS Capibara", layout="centered")

st.markdown("""
<style>
    div.stButton > button {
        height: 70px; border-radius: 12px; border: 2px solid #0073E6; 
        font-weight: bold; background-color: #E6F2FF; color: #002244;
    }
    div.stButton > button:active { background-color: #99CCFF; transform: scale(0.95); }
    div.stButton > button[kind="primary"] { background-color: #0073E6; color: white; border: none; }
    .st-tabs { font-size: 18px; font-weight: bold; }
</style>
""", unsafe_allow_html=True)

# --- CONEXIÓN A GOOGLE SHEETS ---
# Esto conectará mágicamente tu sistema con la nube
import streamlit as st
import gspread
import json
from datetime import datetime

# --- CONFIGURACIÓN DE PÁGINA Y CSS (MODO APP) ---
st.set_page_config(page_title="POS Capibara", layout="centered")

st.markdown("""
<style>
    div.stButton > button {
        height: 70px; border-radius: 12px; border: 2px solid #0073E6; 
        font-weight: bold; background-color: #E6F2FF; color: #002244;
    }
    div.stButton > button:active { background-color: #99CCFF; transform: scale(0.95); }
    div.stButton > button[kind="primary"] { background-color: #0073E6; color: white; border: none; }
    .st-tabs { font-size: 18px; font-weight: bold; }
</style>
""", unsafe_allow_html=True)

# --- CONEXIÓN A GOOGLE SHEETS (MODO NUBE SECRETA) ---
@st.cache_resource
def conectar_sheets():
    try:
        # Leemos la llave desde la bóveda invisible de Streamlit
        credenciales = json.loads(st.secrets["google_credentials"])
        gc = gspread.service_account_from_dict(credenciales)
        sh = gc.open("Base_POS")
        return sh
    except Exception as e:
        st.error(f"⚠️ Error de conexión: {e}")
        return None

sh = conectar_sheets()

# (El resto de tu código hacia abajo se queda exactamente igual)
# --- MEMORIA DEL CARRITO TEMPORAL ---
if 'ticket' not in st.session_state:
    st.session_state.ticket = []

# --- EL MENÚ CAPIBARA OFICIAL ---
MENU = {
    "☕ Café (Directo)": {"Americano Caliente": 30, "Americano Frio": 35, "Mocha Caliente": 35, "Mocha Frio": 40, "Latte Vainilla Cal.": 40, "Latte Vainilla Frio": 45, "Latte Fresa Cal.": 40, "Latte Fresa Frio": 45, "Cafe c/Leche Cal.": 35, "Cafe c/Leche Frio": 40},
    "🍞 Pan (Directo)": {"Concha": 15, "Oreja": 12, "Mantecada": 14},
    "🥤 Frappés (Puesto)": {"Fresa": 65, "Taro": 65, "Chai": 65, "Matcha": 65, "Rompope": 65, "Red Velvet": 65, "Pistache": 65, "Galleta": 65, "Mora": 65, "Cereza": 65},
    "🥛 Esquimos (Puesto)": {"Fresa": 45, "Mocha": 45, "Cafe": 45, "Nuez": 45, "Chocolate": 45, "Rompope": 45, "Vainilla": 45},
    "🍧 Chamoyadas (Puesto)": {"Fresa": 65, "Mango": 65, "Temporada": 65, "Refresher Darks": 65, "+ Bolitas Explosivas": 10},
    "🥪 Tortas (Cocina)": {"Cubana": 65, "Milanesa de Res": 40, "Milanesa de Pollo": 40, "Salchicha": 35, "Huevo": 35, "Huevo c/ Jamón": 35, "Chilaquiles": 40},
    "🥗 Platillos (Cocina)": {"Ensalada": 65, "Sandwich": 65, "Plato de Chilaquiles": 55}
}

# --- LAS 5 PESTAÑAS DE OPERACIÓN ---
tab1, tab2, tab3, tab4, tab5 = st.tabs(["🛒 Carrito", "🥤 Puesto", "✅ Listos", "🍳 Cocina", "📓 Pagos"])

# ==========================================
# PESTAÑA 1: EL CARRITO (TOMA DE PEDIDOS)
# ==========================================
with tab1:
    st.title("🛒 Punto de Venta")
    
    # DATOS DEL PEDIDO (Arriba para no estorbar)
    col_nom, col_hora = st.columns(2)
    with col_nom:
        cliente = st.text_input("👤 Cliente (Opcional):", value="Mostrador")
    with col_hora:
        tiempo = st.radio("⏱️ ¿Cuándo lo quiere?", ["Ahora", "Más tarde"], horizontal=True)
        hora_entrega = ""
        if tiempo == "Más tarde":
            hora_entrega = st.time_input("¿A qué hora?")
            
    # SELECCIÓN DE MENÚ EN BLOQUES
    categoria = st.selectbox("📌 Categoría:", list(MENU.keys()))
    col1, col2 = st.columns(2) 
    
    for i, (nombre, precio) in enumerate(MENU[categoria].items()):
        # Ruteo interno automático
        destino = "Cocina" if "(Cocina)" in categoria else "Puesto" if "(Puesto)" in categoria else "Directo"
        
        # Distribuir botones en 2 columnas
        target_col = col1 if i % 2 == 0 else col2
        with target_col:
            if st.button(f"{nombre}\n${precio}", use_container_width=True, key=f"btn_{nombre}"):
                st.session_state.ticket.append({"producto": nombre, "precio": precio, "destino": destino, "notas": ""})
                st.toast(f"✅ Agregado: {nombre}")

    st.divider()
    
    # EL TICKET Y COBRO
    if len(st.session_state.ticket) > 0:
        st.subheader("🧾 Tu Ticket:")
        total = 0
        
        # Mostrar carrito y permitir agregar notas a la comida
        for index, item in enumerate(st.session_state.ticket):
            c1, c2, c3 = st.columns([3, 1, 2])
            with c1: st.write(f"▪️ {item['producto']}")
            with c2: st.write(f"${item['precio']}")
            with c3:
                # Solo pedir notas si va a cocina
                if item['destino'] == "Cocina":
                    item['notas'] = st.text_input("Detalles (ej. Sin cebolla):", key=f"nota_{index}")
            total += item['precio']
            
        st.write(f"### 💰 Total: ${total}")
        
        # ESTADO DE PAGO Y ENVIAR
        pago = st.radio("💵 Estado del Pago:", ["Pagado Completo", "Pendiente / Fiado"], horizontal=True)
        
        col_borrar, col_cobrar = st.columns(2)
        with col_borrar:
            if st.button("🗑️ Borrar Ticket", use_container_width=True):
                st.session_state.ticket = []
                st.rerun()
        with col_cobrar:
            if st.button("🚀 ENVIAR PEDIDO", type="primary", use_container_width=True):
                if sh:
                    ws_ops = sh.worksheet("Operaciones")
                    hora_registro = str(datetime.now().strftime("%H:%M:%S"))
                    hora_final = hora_registro if tiempo == "Ahora" else str(hora_entrega)
                    
                    # Registrar cada producto en Sheets
                    for item in st.session_state.ticket:
                        # Si es directo (Pan/Café), ya se asume entregado. Si no, va a Preparación.
                        estado = "Entregado" if item['destino'] == "Directo" else "Preparando"
                        
                        ws_ops.append_row([
                            cliente, item['producto'], item['destino'], item['notas'], 
                            tiempo, hora_final, estado, total if item == st.session_state.ticket[0] else 0 # Solo anota el total 1 vez
                        ])
                    
                    # Si es fiado, va a la hoja de deudas
                    if pago == "Pendiente / Fiado":
                        ws_deudas = sh.worksheet("Deudas")
                        ws_deudas.append_row([cliente, total, hora_registro])
                        
                    st.session_state.ticket = []
                    st.success("¡Pedido registrado en la nube con éxito!")
                    st.rerun()

# ==========================================
# PESTAÑA 2, 3 y 4: MONITORES DE TRABAJO
# ==========================================
# Función para leer las operaciones pendientes desde el Excel
def cargar_pendientes():
    if not sh: return []
    try:
        registros = sh.worksheet("Operaciones").get_all_records()
        return registros
    except:
        return []

registros_hoy = cargar_pendientes()

with tab2:
    st.header("🥤 PUESTO (Frappés y Esquimos)")
    st.info("Aquí solo aparecen las bebidas preparadas.")
    # (Aquí programaremos la lectura en vivo del Excel en el siguiente paso)
    st.write("Conectado y listo para recibir comandas...")

with tab4:
    st.header("🍳 COCINA (Comida Caliente)")
    st.info("Aquí aparecen Tortas, Chilaquiles y Ensaladas con sus notas.")
    st.write("Conectado y esperando tickets...")

with tab3:
    st.header("✅ LISTOS PARA ENTREGA")
    st.success("Cuando Cocina o Puesto terminen, los pedidos saltarán aquí.")
    st.write("Esperando pedidos terminados...")

# ==========================================
# PESTAÑA 5: GESTIÓN FINANCIERA Y DEUDAS
# ==========================================
with tab5:
    st.header("📓 Libreta de Pagos y Deudas")
    if sh:
        try:
            deudas_data = sh.worksheet("Deudas").get_all_records()
            st.dataframe(deudas_data) # Muestra el Excel en formato tabla bonita
        except:
            st.write("No hay deudas registradas aún.")
            
    st.write("---")
    st.write("*(Módulo de abonos en construcción para el próximo paso)*")