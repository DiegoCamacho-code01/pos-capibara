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

# --- MEMORIA RAM ---
if 'ticket_carrito' not in st.session_state: st.session_state.ticket_carrito = []
if 'ticket_puesto' not in st.session_state: st.session_state.ticket_puesto = []

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

# --- EL MENÚ ---
MENU = {
    "☕ Café Básico (Carrito - Directo)": {"Americano Caliente": 30, "Americano Frio": 35, "Café de Olla": 25},
    "🏪 Cafetería Completa (Puesto)": {"Mocha Caliente": 35, "Mocha Frio": 40, "Latte Vainilla Cal.": 40, "Latte Vainilla Frio": 45, "Latte Fresa Cal.": 40, "Latte Fresa Frio": 45, "Cafe c/Leche Cal.": 35, "Cafe c/Leche Frio": 40},
    "🍞 Pan (Carrito - Directo)": {"Concha": 15, "Oreja": 12, "Mantecada": 14},
    "🥤 Frappés (Puesto - Opcional)": {"Fresa": 65, "Taro": 65, "Chai": 65, "Matcha": 65, "Rompope": 65, "Red Velvet": 65, "Pistache": 65, "Galleta": 65, "Mora": 65, "Cereza": 65},
    "🥛 Esquimos (Puesto - Opcional)": {"Fresa": 45, "Mocha": 45, "Cafe": 45, "Nuez": 45, "Chocolate": 45, "Rompope": 45, "Vainilla": 45},
    "🍧 Chamoyadas (Puesto - Opcional)": {"Fresa": 65, "Mango": 65, "Temporada": 65, "Refresher Darks": 65, "+ Bolitas Explosivas": 10},
    "🥪 Tortas (Carrito - Directo)": {"Cubana": 65, "Milanesa de Res": 40, "Milanesa de Pollo": 40, "Salchicha": 35, "Huevo": 35, "Huevo c/ Jamón": 35},
    "🥗 Platillos (Cocina)": {"Ensalada": 65, "Sandwich": 65, "Plato de Chilaquiles": 55, "Torta de Chilaquiles": 40}
}

# --- LECTURA ÚNICA DE DATOS (EVITA EL ERROR 429 DE GOOGLE) ---
datos_ops_global = []
if sh:
    try:
        ws_ops = sh.worksheet("Operaciones")
        datos_ops_global = ws_ops.get_all_values()
    except:
        pass

# --- FUNCIÓN MAESTRA DE MONITORES (Usa la lectura única) ---
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
                    if notas: st.warning(f"📝 **Notas:** {notas}")
                with col_btn:
                    st.write("")
                    if st.button(f"{texto_boton} ✅", key=f"btn_{i}_{estado}"):
                        ws_ops.update_cell(i, 7, nuevo_estado)
                        st.rerun()
            st.divider()
    if mostrados == 0: st.info("No hay tickets pendientes para hoy en esta área.")

# --- LAS 6 PESTAÑAS DE OPERACIÓN ---
tab1, tab2, tab3, tab4, tab6, tab5 = st.tabs(["🛒 Carrito", "🥤 Puesto", "✅ Listos", "🍳 Cocina", "📅 Agendados", "📓 Pagos"])

# ==========================================
# PESTAÑA 1: EL CARRITO PRINCIPAL
# ==========================================
with tab1:
    st.title("🛒 Carrito Principal")
    
    col_nom, col_dia, col_hora = st.columns([2, 1.5, 1.5])
    with col_nom: cliente = st.text_input("👤 Cliente (Obligatorio para deudas):", value="Mostrador")
    with col_dia: dia_pedido = st.radio("📅 Día:", ["Hoy", "Mañana", "Otro"], horizontal=True)
    with col_hora:
        tiempo = st.radio("⏱️ Horario:", ["Ahora", "Más tarde"], horizontal=True)
        hora_entrega = st.selectbox("¿A qué hora?", obtener_horas_disponibles()) if tiempo == "Más tarde" else ""
    
    if dia_pedido == "Mañana": fecha_final_entrega = (hoy_obj + timedelta(days=1)).strftime("%d/%m/%Y")
    elif dia_pedido == "Otro":
        fecha_calendario = st.date_input("Selecciona fecha:", hoy_obj + timedelta(days=2))
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
                
                target_col = col1 if i % 2 == 0 else col2
                with target_col:
                    if st.button(f"{nombre}\n${precio}", use_container_width=True, key=f"btn_c_{nombre}_{categoria}"):
                        st.session_state.ticket_carrito.append({"producto": nombre_completo, "precio": precio, "destino": destino, "notas": ""})
                        st.toast(f"✅ Agregado: {nombre_completo}")

    st.divider()
    if len(st.session_state.ticket_carrito) > 0:
        st.subheader("🧾 Tu Ticket:")
        total = 0
        for index, item in enumerate(st.session_state.ticket_carrito):
            c1, c2, c3 = st.columns([3, 1, 2])
            with c1: st.write(f"▪️ {item['producto']}")
            with c2: st.write(f"${item['precio']}")
            with c3:
                item['notas'] = st.text_input("Notas:", key=f"nota_c_{index}", label_visibility="collapsed", placeholder="Detalles...")
                if item['destino'] == "Puesto_Opcional":
                    preparar = st.checkbox("Enviar a Puesto", key=f"prep_c_{index}")
                    item['destino_final'] = "Puesto" if preparar else "Directo"
                else:
                    item['destino_final'] = item['destino']
            total += item['precio']
            
        st.write(f"### 💰 Total: ${total}")
        pago = st.radio("💵 Estado del Pago:", ["Pagado Completo", "Pendiente / Fiado"], horizontal=True)
        
        col_borrar, col_cobrar = st.columns(2)
        with col_borrar:
            if st.button("🗑️ Borrar Ticket", use_container_width=True):
                st.session_state.ticket_carrito = []
                st.rerun()
        with col_cobrar:
            if st.button("🚀 ENVIAR PEDIDO", type="primary", use_container_width=True):
                if sh:
                    try:
                        hora_registro = str(datetime.now(zona_mx).strftime("%H:%M:%S"))
                        hora_final = hora_registro if tiempo == "Ahora" else str(hora_entrega)
                        detalles_ticket = []
                        
                        for item in st.session_state.ticket_carrito:
                            destino_real = item['destino_final']
                            estado = "Entregado" if destino_real == "Directo" else "Preparando"
                            ws_ops.append_row([cliente, item['producto'], destino_real, item['notas'], tiempo, hora_final, estado, total if item == st.session_state.ticket_carrito[0] else 0, fecha_final_entrega])
                            detalles_ticket.append(item['producto'])
                        
                        if pago == "Pendiente / Fiado":
                            resumen_productos = ", ".join(detalles_ticket)
                            sh.worksheet("Deudas").append_row([cliente, "Deuda", total, resumen_productos, hora_final])
                            
                        st.session_state.ticket_carrito = []
                        st.success("¡Pedido registrado con éxito!")
                        st.rerun()
                    except Exception as e: st.error(f"Error al escribir en Excel: {e}")

# ==========================================
# PESTAÑA 2: CAJA PUESTO (Solo Bebidas)
# ==========================================
with tab2:
    st.title("🥤 Caja Puesto")
    col_nom_p, col_hora_p = st.columns(2)
    with col_nom_p: cliente_p = st.text_input("👤 Cliente:", value="Mostrador", key="cli_p_ind")
    with col_hora_p:
        tiempo_p = st.radio("⏱️ Horario:", ["Ahora", "Más tarde"], horizontal=True, key="tie_p_ind")
        hora_entrega_p = st.selectbox("¿A qué hora?", obtener_horas_disponibles(), key="hor_p_ind") if tiempo_p == "Más tarde" else ""

    categorias_puesto = ["🏪 Cafetería Completa (Puesto)", "🥤 Frappés (Puesto - Opcional)", "🥛 Esquimos (Puesto - Opcional)", "🍧 Chamoyadas (Puesto - Opcional)"]
    st.write("### 📌 Menú de Bebidas:")
    for categoria in categorias_puesto:
        if categoria in MENU:
            with st.expander(categoria):
                col1, col2 = st.columns(2) 
                for i, (nombre, precio) in enumerate(MENU[categoria].items()):
                    cat_corta = categoria.split(" (")[0][2:].strip()
                    nombre_completo = f"{cat_corta} {nombre}"
                    target_col = col1 if i % 2 == 0 else col2
                    with target_col:
                        if st.button(f"{nombre}\n${precio}", use_container_width=True, key=f"btn_p_{nombre}_{categoria}"):
                            st.session_state.ticket_puesto.append({"producto": nombre_completo, "precio": precio, "destino": "Directo", "notas": ""})
                            st.toast(f"✅ Agregado: {nombre_completo}")
                            
    st.divider()
    if len(st.session_state.ticket_puesto) > 0:
        st.subheader("🧾 Ticket del Puesto:")
        total_p = 0
        for index, item in enumerate(st.session_state.ticket_puesto):
            c1, c2, c3 = st.columns([3, 1, 2])
            with c1: st.write(f"▪️ {item['producto']}")
            with c2: st.write(f"${item['precio']}")
            with c3: item['notas'] = st.text_input("Notas:", key=f"nota_p_{index}", label_visibility="collapsed", placeholder="Detalles...")
            total_p += item['precio']
            
        st.write(f"### 💰 Total: ${total_p}")
        pago_p = st.radio("💵 Estado del Pago:", ["Pagado Completo", "Pendiente / Fiado"], horizontal=True, key="pag_pue")
        
        col_borrar_p, col_cobrar_p = st.columns(2)
        with col_borrar_p:
            if st.button("🗑️ Borrar Ticket", use_container_width=True, key="borrar_p"):
                st.session_state.ticket_puesto = []
                st.rerun()
        with col_cobrar_p:
            if st.button("🚀 COBRAR Y ENTREGAR", type="primary", use_container_width=True, key="enviar_p"):
                if sh:
                    try:
                        hora_registro = str(datetime.now(zona_mx).strftime("%H:%M:%S"))
                        hora_final = hora_registro if tiempo_p == "Ahora" else str(hora_entrega_p)
                        detalles_ticket = []
                        
                        for item in st.session_state.ticket_puesto:
                            ws_ops.append_row([cliente_p, item['producto'], "Directo", item['notas'], tiempo_p, hora_final, "Entregado", total_p if item == st.session_state.ticket_puesto[0] else 0, hoy_str])
                            detalles_ticket.append(item['producto'])
                        
                        if pago_p == "Pendiente / Fiado":
                            resumen_productos = ", ".join(detalles_ticket)
                            sh.worksheet("Deudas").append_row([cliente_p, "Deuda", total_p, resumen_productos, hora_final])
                            
                        st.session_state.ticket_puesto = []
                        st.success("¡Cobro registrado con éxito!")
                        st.rerun()
                    except Exception as e: st.error(f"Error al escribir: {e}")

# ==========================================
# MONITORES DE COCINA Y LISTOS
# ==========================================
with tab4:
    st.header("🍳 COCINA (Platillos)")
    mostrar_pedidos("Cocina", "Preparando", "Marcar Terminado", "Listo")

with tab3:
    st.header("✅ LISTOS PARA HOY")
    mostrar_pedidos(None, "Listo", "Entregar al Cliente", "Entregado")

# ==========================================
# PESTAÑA 6: AGENDA FUTURA
# ==========================================
with tab6:
    st.header("📅 Pedidos Futuros Agendados")
    if len(datos_ops_global) > 1:
        futuros = 0
        for i, fila in enumerate(datos_ops_global[1:], start=2):
            if len(fila) > 8:
                fecha_str = fila[8]
                if fecha_str != hoy_str and fila[6] != "Entregado":
                    futuros += 1
                    st.info(f"📅 **Para el: {fecha_str}** | 🕒 {fila[5]}\n\n👤 {fila[0]} ordenó: **{fila[1]}**")
        if futuros == 0: st.success("No hay pedidos pendientes para los próximos días.")
    else:
        st.write("Sin datos.")

# ==========================================
# PESTAÑA 5: SISTEMA DE PAGOS
# ==========================================
with tab5:
    st.header("📓 Libreta de Pagos y Deudas")
    if sh:
        try:
            ws_deudas = sh.worksheet("Deudas")
            datos_deudas = ws_deudas.get_all_records()
            clientes = {}
            for fila in datos_deudas:
                c = fila.get("Cliente", "Desconocido")
                if c not in clientes: clientes[c] = {"total": 0, "historial": []}
                monto = float(fila.get("Monto", 0))
                if fila.get("Tipo") == "Deuda": clientes[c]["total"] += monto
                else: clientes[c]["total"] -= monto
                clientes[c]["historial"].append(fila)
            
            hay_deudores = False
            for c, info in clientes.items():
                saldo = round(info["total"], 2)
                if saldo > 0:
                    hay_deudores = True
                    with st.expander(f"👤 {c} - Debe: ${saldo}"):
                        st.write("**Historial de consumo:**")
                        for h in info["historial"]:
                            tipo = "🔴 Ticket" if h["Tipo"] == "Deuda" else "🟢 Abono"
                            st.write(f"{tipo}: **${h['Monto']}** ({h['Fecha']}) - *{h['Detalles']}*")
                        st.divider()
                        col_abono, col_btn = st.columns(2)
                        with col_abono:
                            abono_input = st.number_input(f"Abonar cantidad para {c}", min_value=0.0, max_value=float(saldo), value=float(saldo), key=f"num_{c}")
                        with col_btn:
                            st.write("")
                            st.write("")
                            if st.button("Registrar Pago", key=f"pagar_{c}", use_container_width=True):
                                if abono_input > 0:
                                    detalle_abono = "Liquidación total" if abono_input == saldo else "Abono parcial"
                                    hora_abono = str(datetime.now(zona_mx).strftime("%H:%M:%S"))
                                    ws_deudas.append_row([c, "Abono", abono_input, detalle_abono, hora_abono])
                                    st.success(f"Pago de ${abono_input} registrado.")
                                    st.rerun()
            if not hay_deudores: st.success("¡Todo está pagado! No hay deudas activas.")
        except Exception as e: st.error(f"Configura los encabezados en la hoja 'Deudas' primero.")