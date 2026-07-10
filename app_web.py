import streamlit as st
import gspread
import json
from datetime import datetime, timedelta, timezone

# --- CONFIGURACIÓN DE PÁGINA Y CSS ---
st.set_page_config(page_title="POS Capibara", layout="wide")

st.markdown("""
<style>
    div.stButton > button { height: 70px; border-radius: 12px; border: 2px solid #0073E6; font-weight: bold; background-color: #E6F2FF; color: #002244; }
    div.stButton > button:active { background-color: #99CCFF; transform: scale(0.95); }
    div.stButton > button[kind="primary"] { background-color: #0073E6; color: white; border: none; }
    .st-tabs { font-size: 18px; font-weight: bold; }
    .streamlit-expanderHeader { font-size: 18px !important; font-weight: bold !important; background-color: #262730; border-radius: 8px; }
</style>
""", unsafe_allow_html=True)

# --- ZONA HORARIA MÉXICO ---
zona_mx = timezone(timedelta(hours=-6))
hoy_obj = datetime.now(zona_mx).date()
hoy_str = hoy_obj.strftime("%d/%m/%Y")

# --- CONEXIÓN A GOOGLE SHEETS ---
@st.cache_resource
def conectar_sheets():
    try:
        credenciales = json.loads(st.secrets["google_credentials"], strict=False)
        gc = gspread.service_account_from_dict(credenciales)
        return gc.open("Base_POS")
    except Exception as e:
        return None

sh = conectar_sheets()

# --- MEMORIA RAM Y SISTEMA DE FOLIOS ---
if 'ticket_carrito' not in st.session_state: st.session_state.ticket_carrito = []
if 'ticket_puesto' not in st.session_state: st.session_state.ticket_puesto = []
if 'folio' not in st.session_state: st.session_state.folio = 1

# --- GENERADOR DE HORAS ---
def obtener_horas_disponibles():
    ahora = datetime.now(zona_mx)
    minutos_extra = 30 - (ahora.minute % 30)
    siguiente_hora = ahora + timedelta(minutes=minutos_extra)
    horas = []
    for _ in range(24): 
        horas.append(siguiente_hora.strftime("%H:%M"))
        siguiente_hora += timedelta(minutes=30)
    return horas

# --- EL MENÚ ACTUALIZADO ---
MENU = {
    "☕ Café (Carrito - Directo)": {"Cafe Vainilla": 25, "Cafe Avellana": 25, "Café": 25},
    "🏪 Cafetería Completa (Puesto)": {"Americano Caliente": 30, "Americano Frio": 35, "Moka Caliente": 35, "Moka Frio": 40, "Latte Vainilla Cal.": 40, "Latte Vainilla Frio": 45, "Latte Fresa Cal.": 40, "Latte Fresa Frio": 45, "Cafe c/Leche Cal.": 35, "Cafe c/Leche Frio": 40},
    "🍞 Pan (Carrito - Directo)": {"Pan Dulce": 25, "Pan Telera": 5},
    "🥤 Frappés (Puesto - Opcional)": {"Fresa": 65, "Taro": 65, "Chai": 65, "Matcha": 65, "Rompope": 65, "Red Velvet": 65, "Pistache": 65, "Galleta": 65, "Mora": 65, "Cereza": 65, "Refresher Darks": 65, "Cafe": 65, "Moka": 65, "Oreo": 65, "Chocolate": 65},
    "🥛 Esquimos (Puesto - Opcional)": {"Fresa": 45, "Taro": 45, "Chai": 45, "Matcha": 45, "Rompope": 45, "Red Velvet": 45, "Pistache": 45, "Galleta": 45, "Mora": 45, "Cereza": 45, "Refresher Darks": 45, "Cafe": 45, "Moka": 45, "Oreo": 45, "Chocolate": 45},
    "🍧 Chamoyadas (Puesto - Opcional)": {"Fresa": 65, "Mango": 65, "Temporada": 65},
    "🥪 Tortas (Carrito - Directo)": {"Jamon": 25, "Queso de puerco": 25, "Milanesa de Res": 40, "Milanesa de Pollo": 40, "Salchicha": 35, "Huevo": 35, "Huevo c/ Jamón": 35, "Cochinita": 40, "frijoles con queso": 25},
    "🥗 Platillos (Cocina)": {"Ensalada": 65, "Sandwich": 65, "Plato de Chilaquiles": 50, "Torta de Chilaquiles": 40}
}

# --- LECTURA ÚNICA DE DATOS ---
datos_ops_global = []
datos_inv_global = []
dict_inventario = {}

if sh:
    try:
        ws_ops = sh.worksheet("Operaciones")
        datos_ops_global = ws_ops.get_all_values()
        
        ws_inv = sh.worksheet("Inventario")
        datos_inv_global = ws_inv.get_all_records()
        for fila in datos_inv_global:
            dict_inventario[fila["Producto"]] = int(fila["Stock"])
    except:
        pass

# --- FUNCIÓN MAESTRA DE MONITORES ---
def mostrar_pedidos(destino_filtro, estado_actual, texto_boton, nuevo_estado):
    if len(datos_ops_global) <= 1:
        st.info("Todo tranquilo por aquí. 😎")
        return
    
    mostrados = 0
    for i, fila in enumerate(datos_ops_global[1:], start=2):
        if len(fila) < 7: continue
        
        cliente, producto, destino, notas, tiempo, hora, estado = fila[0], fila[1], fila[2], fila[3], fila[4], fila[5], fila[6]
        fecha_fila = fila[8] if len(fila) > 8 else hoy_str
        
        if fecha_fila != hoy_str: continue
        
        mostrar = False
        if destino_filtro: 
            if destino == destino_filtro and estado == estado_actual: mostrar = True
        else: 
            if estado == estado_actual: mostrar = True

        if mostrar:
            mostrados += 1
            with st.container():
                col_info, col_btn = st.columns([3, 2])
                with col_info:
                    st.markdown(f"### 🛒 {producto}")
                    st.write(f"👤 **Cliente:** {cliente} | 🕒 **Hora:** {hora}")
                    if notas: st.warning(f"📝 **Notas:** {notes}")
                with col_btn:
                    st.write("")
                    if st.button(f"{texto_boton} ✅", key=f"btn_{i}_{estado}"):
                        ws_ops.update_cell(i, 7, nuevo_estado)
                        st.rerun()
            st.divider()
    if mostrados == 0: st.info("No hay tickets pendientes para hoy en esta área.")

# --- LAS 7 PESTAÑAS DE OPERACIÓN ---
tab1, tab2, tab3, tab4, tab6, tab5, tab7 = st.tabs(["🛒 Carrito", "🥤 Puesto", "✅ Listos", "🍳 Cocina", "📅 Agendados", "📓 Pagos", "📦 Inventario"])

# ==========================================
# PESTAÑA 1: EL CARRITO PRINCIPAL
# ==========================================
with tab1:
    st.title("🛒 Carrito Principal")
    f_actual = st.session_state.folio 
    
    col_nom, col_dia, col_hora = st.columns([2, 1.5, 1.5])
    with col_nom: cliente = st.text_input("👤 Cliente (Obligatorio para deudas):", value="Mostrador", key=f"cli_{f_actual}")
    with col_dia: dia_pedido = st.radio("📅 Día:", ["Hoy", "Mañana", "Otro"], horizontal=True, key=f"dia_{f_actual}")
    with col_hora:
        tiempo = st.radio("⏱️ Horario:", ["Ahora", "Más tarde"], horizontal=True, key=f"tie_{f_actual}")
        hora_entrega = st.selectbox("¿A qué hora?", obtener_horas_disponibles(), key=f"hor_{f_actual}") if tiempo == "Más tarde" else ""
    
    if dia_pedido == "Mañana": fecha_final_entrega = (hoy_obj + timedelta(days=1)).strftime("%d/%m/%Y")
    elif dia_pedido == "Otro":
        fecha_calendario = st.date_input("Selecciona fecha:", hoy_obj + timedelta(days=2), key=f"cal_{f_actual}")
        fecha_final_entrega = fecha_calendario.strftime("%d/%m/%Y")
    else: fecha_final_entrega = hoy_str
            
    st.write("### 📌 Menú:")
    for categoria, productos in MENU.items():
        with st.expander(categoria):
            col1, col2 = st.columns(2) 
            for i, (nombre, precio) in enumerate(productos.items()):
                cat_corta = categoria.split(" (")[0][2:].strip()
                nombre_completo = f"{cat_corta} {nombre}" if "Cafetería" in categoria or "Frappés" in categoria or "Esquimos" in categoria or "Chamoyadas" in categoria else nombre
                
                if "(Cocina)" in categoria: destino = "Cocina"
                elif "(Puesto - Opcional)" in categoria: destino = "Puesto_Opcional"
                elif "(Puesto)" in categoria: destino = "Puesto"
                else: destino = "Directo"
                
                stock_actual = dict_inventario.get(nombre, None)
                texto_boton = f"{nombre}\n${precio}"
                esta_agotado = False
                
                if stock_actual is not None:
                    texto_boton += f"\n(Quedan {stock_actual})"
                    if stock_actual <= 0:
                        texto_boton = f"{nombre}\n(AGOTADO)"
                        esta_agotado = True
                
                target_col = col1 if i % 2 == 0 else col2
                with target_col:
                    if st.button(texto_boton, use_container_width=True, disabled=esta_agotado, key=f"btn_c_{nombre}_{categoria}"):
                        st.session_state.ticket_carrito.append({
                            "producto": nombre_completo, 
                            "producto_puro": nombre, 
                            "precio": precio, 
                            "destino": destino, 
                            "notas": ""
                        })
                        st.toast(f"✅ Agregado: {nombre_completo}")

    st.divider()
    if len(st.session_state.ticket_carrito) > 0:
        st.subheader("🧾 Tu Ticket:")
        total = 0
        for index, item in enumerate(st.session_state.ticket_carrito):
            c1, c2, c3 = st.columns([3, 1, 2])
            with c1: st.write(f"▪️ {item['producto']}")
            with c2: st