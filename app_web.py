import streamlit as st
import gspread
import json
from datetime import datetime

# --- CONFIGURACIÓN DE PÁGINA Y CSS ---
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
    .streamlit-expanderHeader {
        font-size: 18px !important;
        font-weight: bold !important;
        background-color: #262730;
        border-radius: 8px;
    }
</style>
""", unsafe_allow_html=True)

# --- CONEXIÓN A GOOGLE SHEETS ---
@st.cache_resource
def conectar_sheets():
    try:
        credenciales = json.loads(st.secrets["google_credentials"], strict=False)
        gc = gspread.service_account_from_dict(credenciales)
        sh = gc.open("Base_POS")
        return sh
    except Exception as e:
        st.error(f"⚠️ Error de conexión: {e}")
        return None

sh = conectar_sheets()

# --- MEMORIA DEL CARRITO TEMPORAL ---
if 'ticket' not in st.session_state:
    st.session_state.ticket = []

# --- EL MENÚ CAPIBARA REESTRUCTURADO ---
MENU = {
    "☕ Café Básico (Carrito - Directo)": {
        "Americano Caliente": 30, "Americano Frio": 35, "Café de Olla": 25
    },
    "🏪 Cafetería Completa (Puesto)": {
        "Mocha Caliente": 35, "Mocha Frio": 40, "Latte Vainilla Cal.": 40, "Latte Vainilla Frio": 45, 
        "Latte Fresa Cal.": 40, "Latte Fresa Frio": 45, "Cafe c/Leche Cal.": 35, "Cafe c/Leche Frio": 40
    },
    "🍞 Pan (Carrito - Directo)": {
        "Concha": 15, "Oreja": 12, "Mantecada": 14
    },
    "🥤 Frappés (Puesto)": {
        "Fresa": 65, "Taro": 65, "Chai": 65, "Matcha": 65, "Rompope": 65, "Red Velvet": 65, "Pistache": 65, "Galleta": 65, "Mora": 65, "Cereza": 65
    },
    "🥛 Esquimos (Puesto)": {
        "Fresa": 45, "Mocha": 45, "Cafe": 45, "Nuez": 45, "Chocolate": 45, "Rompope": 45, "Vainilla": 45
    },
    "🍧 Chamoyadas (Puesto)": {
        "Fresa": 65, "Mango": 65, "Temporada": 65, "Refresher Darks": 65, "+ Bolitas Explosivas": 10
    },
    "🥪 Tortas (Cocina)": {
        "Cubana": 65, "Milanesa de Res": 40, "Milanesa de Pollo": 40, "Salchicha": 35, "Huevo": 35, "Huevo c/ Jamón": 35, "Chilaquiles": 40
    },
    "🥗 Platillos (Cocina)": {
        "Ensalada": 65, "Sandwich": 65, "Plato de Chilaquiles": 55
    }
}

# --- FUNCIÓN MAESTRA DE MONITORES ---
def mostrar_pedidos(destino_filtro, estado_actual, texto_boton, nuevo_estado):
    if not sh: return
    try:
        ws_ops = sh.worksheet("Operaciones")
        datos = ws_ops.get_all_values() # Trae todo como listas
        
        if len(datos) <= 1:
            st.info("Todo tranquilo por aquí. 😎")
            return
            
        mostrados = 0
        # Empezamos desde la fila 2 (índice 1 en la lista) porque la 1 son los encabezados
        for i, fila in enumerate(datos[1:], start=2):
            if len(fila) < 7: continue # Por si hay filas en blanco
            
            cliente, producto, destino, notas, tiempo, hora, estado = fila[0], fila[1], fila[2], fila[3], fila[4], fila[5], fila[6]
            
            # Filtro lógico
            mostrar = False
            if destino_filtro: # Si estamos en Cocina o Puesto
                if destino == destino_filtro and estado == estado_actual:
                    mostrar = True
            else: # Si estamos en la pestaña "Listos" (no importa de dónde venga, solo que esté listo)
                if estado == estado_actual:
                    mostrar = True

            if mostrar:
                mostrados += 1
                with st.container():
                    col_info, col_btn = st.columns([3, 2])
                    with col_info:
                        st.markdown(f"### 🛒 {producto}")
                        st.write(f"👤 **Cliente:** {cliente} | 🕒 **Hora:** {hora}")
                        if notas:
                            st.warning(f"📝 **Notas:** {notas}")
                    with col_btn:
                        st.write("") # Espaciador
                        if st.button(f"{texto_boton} ✅", key=f"btn_{i}_{estado}"):
                            # 7 es el número de la columna G (Estado) en Sheets
                            ws_ops.update_cell(i, 7, nuevo_estado)
                            st.rerun()
                st.divider()
                
        if mostrados == 0:
            st.info("No hay tickets pendientes en esta área. ☕")
            
    except Exception as e:
        st.error(f"Faltan los encabezados en el Excel o hubo un error: {e}")

# --- LAS 5 PESTAÑAS DE OPERACIÓN ---
tab1, tab2, tab3, tab4, tab5 = st.tabs(["🛒 Carrito", "🥤 Puesto", "✅ Listos", "🍳 Cocina", "📓 Pagos"])

# ==========================================
# PESTAÑA 1: EL CARRITO
# ==========================================
with tab1:
    st.title("🛒 Punto de Venta")
    if not sh: st.warning("⚠️ Sin conexión a Google Sheets.")
        
    col_nom, col_hora = st.columns(2)
    with col_nom: cliente = st.text_input("👤 Cliente (Opcional):", value="Mostrador")
    with col_hora:
        tiempo = st.radio("⏱️ ¿Cuándo lo quiere?", ["Ahora", "Más tarde"], horizontal=True)
        hora_entrega = st.time_input("¿A qué hora?") if tiempo == "Más tarde" else ""
            
    st.write("### 📌 Selecciona los productos:")
    for categoria, productos in MENU.items():
        with st.expander(categoria):
            col1, col2 = st.columns(2) 
            for i, (nombre, precio) in enumerate(productos.items()):
                destino = "Cocina" if "(Cocina)" in categoria else "Puesto" if "(Puesto)" in categoria else "Directo"
                target_col = col1 if i % 2 == 0 else col2
                with target_col:
                    if st.button(f"{nombre}\n${precio}", use_container_width=True, key=f"btn_{nombre}_{categoria}"):
                        st.session_state.ticket.append({"producto": nombre, "precio": precio, "destino": destino, "notas": ""})
                        st.toast(f"✅ Agregado: {nombre}")

    st.divider()
    if len(st.session_state.ticket) > 0:
        st.subheader("🧾 Tu Ticket:")
        total = 0
        for index, item in enumerate(st.session_state.ticket):
            c1, c2, c3 = st.columns([3, 1, 2])
            with c1: st.write(f"▪️ {item['producto']}")
            with c2: st.write(f"${item['precio']}")
            with c3:
                if item['destino'] == "Cocina": item['notas'] = st.text_input("Detalles (ej. Sin cebolla):", key=f"nota_{index}")
            total += item['precio']
            
        st.write(f"### 💰 Total: ${total}")
        pago = st.radio("💵 Estado del Pago:", ["Pagado Completo", "Pendiente / Fiado"], horizontal=True)
        
        col_borrar, col_cobrar = st.columns(2)
        with col_borrar:
            if st.button("🗑️ Borrar Ticket", use_container_width=True):
                st.session_state.ticket = []
                st.rerun()
        with col_cobrar:
            if st.button("🚀 ENVIAR PEDIDO", type="primary", use_container_width=True):
                if sh:
                    try:
                        ws_ops = sh.worksheet("Operaciones")
                        hora_registro = str(datetime.now().strftime("%H:%M:%S"))
                        hora_final = hora_registro if tiempo == "Ahora" else str(hora_entrega)
                        
                        for item in st.session_state.ticket:
                            estado = "Entregado" if item['destino'] == "Directo" else "Preparando"
                            ws_ops.append_row([
                                cliente, item['producto'], item['destino'], item['notas'], 
                                tiempo, hora_final, estado, total if item == st.session_state.ticket[0] else 0
                            ])
                        
                        if pago == "Pendiente / Fiado":
                            sh.worksheet("Deudas").append_row([cliente, total, hora_registro])
                            
                        st.session_state.ticket = []
                        st.success("¡Pedido registrado en la nube con éxito!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error al escribir en Excel: {e}")

# ==========================================
# MONITORES DE COCINA Y PUESTO
# ==========================================
with tab2:
    st.header("🥤 PUESTO (Frappés y Cafetería)")
    # Muestra lo que va al Puesto y está Preparando. Botón cambia a Listo.
    mostrar_pedidos("Puesto", "Preparando", "Marcar Terminado", "Listo")

with tab4:
    st.header("🍳 COCINA (Comida Caliente)")
    # Muestra lo que va a Cocina y está Preparando. Botón cambia a Listo.
    mostrar_pedidos("Cocina", "Preparando", "Marcar Terminado", "Listo")

with tab3:
    st.header("✅ LISTOS PARA ENTREGA")
    # Muestra todo lo que diga Listo. Botón cambia a Entregado.
    mostrar_pedidos(None, "Listo", "Entregar al Cliente", "Entregado")

with tab5:
    st.header("📓 Libreta de Pagos y Deudas")
    if sh:
        try:
            st.dataframe(sh.worksheet("Deudas").get_all_records())
        except:
            st.write("Aún no hay datos u ocurrió un error al leer Deudas.")