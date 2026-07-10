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
    "🥪 Tortas (Carrito - Directo)": {"Jamón": 25, "Queso de puerco": 25, "Milanesa de Res": 40, "Milanesa de Pollo": 40, "Salchicha": 35, "Huevo": 35, "Huevo c/ Jamón": 35, "Cochinita": 40, "Frijoles con queso": 25},
    "🥗 Platillos (Cocina)": {"Ensalada": 65, "Sandwich": 65, "Plato de Chilaquiles": 50, "Torta de Chilaquiles": 40},
    "🏪 Cafetería Completa (Puesto)": {"Americano Caliente": 30, "Americano Frio": 35, "Moka Caliente": 35, "Moka Frio": 40, "Latte Vainilla Cal.": 40, "Latte Vainilla Frio": 45, "Latte Fresa Cal.": 40, "Latte Fresa Frio": 45, "Cafe c/Leche Cal.": 35, "Cafe c/Leche Frio": 40},
    "🍞 Pan (Carrito - Directo)": {"Pan Dulce": 25, "Pan Telera": 5},
    "🥤 Frappés (Puesto - Opcional)": {"Fresa": 65, "Taro": 65, "Chai": 65, "Matcha": 65, "Rompope": 65, "Red Velvet": 65, "Pistache": 65, "Galleta": 65, "Mora": 65, "Cereza": 65, "Refresher Darks": 65, "Cafe": 65, "Moka": 65, "Oreo": 65, "Chocolate": 65},
    "🥛 Esquimos (Puesto - Opcional)": {"Fresa": 45, "Taro": 45, "Chai": 45, "Matcha": 45, "Rompope": 45, "Red Velvet": 45, "Pistache": 45, "Galleta": 45, "Mora": 45, "Cereza": 45, "Refresher Darks": 45, "Cafe": 45, "Moka": 45, "Oreo": 45, "Chocolate": 45},
    "🍧 Chamoyadas (Puesto - Opcional)": {"Fresa": 65, "Mango": 65, "Temporada": 65}
    
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
    if not sh or len(datos_ops_global) <= 1:
        st.info("Todo tranquilo por aquí. 😎")
        return
    
    try:
        ws_ops_local = sh.worksheet("Operaciones")
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
                            ws_ops_local.update_cell(i, 7, nuevo_estado)
                            st.rerun()
                st.divider()
        if mostrados == 0: st.info("No hay tickets pendientes para hoy en esta área.")
    except Exception as e:
        st.error(f"Error al actualizar monitor: {e}")

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

    # --- APARTADO EXTRA EN EL CARRITO ---
    with st.expander(" ➕ AGREGAR IMPORTE / EXTRA PERSONALIZADO"):
        col_motivo, col_monto = st.columns([3, 1])
        with col_motivo:
            motivo_extra = st.text_input("Motivo del extra (ej. Ingrediente extra, Envío, Ajuste):", key=f"motivo_ext_{f_actual}")
        with col_monto:
            monto_extra = st.number_input("Precio ($):", value=0.0, step=5.0, key=f"monto_ext_{f_actual}")
        
        if st.button("➕ Añadir Extra al Ticket", use_container_width=True, key=f"btn_ext_{f_actual}"):
            if motivo_extra:
                st.session_state.ticket_carrito.append({
                    "producto": f"Extra: {motivo_extra}",
                    "producto_puro": "Extra",
                    "precio": monto_extra,
                    "destino": "Directo",
                    "notas": ""
                })
                st.toast(f"✅ Extra agregado: {motivo_extra}")
                st.rerun()
            else:
                st.warning("Por favor escribe el motivo del cargo extra.")

    st.divider()
    if len(st.session_state.ticket_carrito) > 0:
        st.subheader("🧾 Tu Ticket:")
        total = 0
        for index, item in enumerate(st.session_state.ticket_carrito):
            c1, c2, c3 = st.columns([3, 1, 2])
            with c1: st.write(f"▪️ {item['producto']}")
            with c2: st.write(f"${item['precio']}")
            with c3:
                item['notas'] = st.text_input("Notas:", key=f"nota_c_{f_actual}_{index}", label_visibility="collapsed", placeholder="Detalles...")
                
                # --- AQUÍ ESTÁ EL AJUSTE EXACTO ---
                # Ahora tanto las Opcionales como las de Puesto se mandan a Cocina
                if item['destino'] == "Puesto_Opcional" or item['destino'] == "Puesto":
                    preparar = st.checkbox("Enviar a Cocina", key=f"prep_c_{f_actual}_{index}")
                    item['destino_final'] = "Cocina" if preparar else "Directo"
                else:
                    item['destino_final'] = item['destino']
            total += item['precio']
            
        st.write(f"### 💰 Total: ${total}")
        pago = st.radio("💵 Estado del Pago:", ["Pagado", "Pendiente"], horizontal=True, key=f"pag_{f_actual}")
        
        col_borrar, col_cobrar = st.columns(2)
        with col_borrar:
            if st.button("🗑️ Borrar Ticket", use_container_width=True):
                st.session_state.ticket_carrito = []
                st.rerun()
        with col_cobrar:
            if st.button("ENVIAR PEDIDO", type="primary", use_container_width=True):
                if sh:
                    try:
                        hora_registro = str(datetime.now(zona_mx).strftime("%d/%m/%Y %H:%M"))
                        hora_final = hora_registro if tiempo == "Ahora" else f"{fecha_final_entrega} {hora_entrega}"
                        detalles_ticket = []
                        
                        for item in st.session_state.ticket_carrito:
                            destino_real = item['destino_final']
                            estado = "Entregado" if destino_real == "Directo" else "Preparando"
                            ws_ops.append_row([cliente, item['producto'], destino_real, item['notas'], tiempo, hora_final, estado, total if item == st.session_state.ticket_carrito[0] else 0, fecha_final_entrega])
                            detalles_ticket.append(item['producto'])
                        
                        if pago == "Pendiente":
                            resumen_productos = ", ".join(detalles_ticket)
                            sh.worksheet("Deudas").append_row([cliente, "Deuda", total, resumen_productos, hora_final])
                            
                        if datos_inv_global:
                            for idx, row in enumerate(datos_inv_global, start=2):
                                p_nombre = row['Producto']
                                p_stock = int(row['Stock'])
                                comprados = sum(1 for p in st.session_state.ticket_carrito if p['producto_puro'] == p_nombre)
                                if comprados > 0:
                                    sh.worksheet("Inventario").update_cell(idx, 2, p_stock - comprados)

                        st.session_state.ticket_carrito = []
                        st.session_state.folio += 1 
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
                            
    # --- APARTADO EXTRA EN EL PUESTO ---
    with st.expander(" ➕ AGREGAR IMPORTE / EXTRA PERSONALIZADO"):
        col_motivo_p, col_monto_p = st.columns([3, 1])
        with col_motivo_p:
            motivo_extra_p = st.text_input("Motivo del extra (ej. Carga extra, Vaso especial):", key="motivo_ext_p")
        with col_monto_p:
            monto_extra_p = st.number_input("Precio ($):", value=0.0, step=5.0, key="monto_ext_p")
        
        if st.button("➕ Añadir Extra al Puesto", use_container_width=True, key="btn_ext_p"):
            if motivo_extra_p:
                st.session_state.ticket_puesto.append({
                    "producto": f"Extra: {motivo_extra_p}",
                    "precio": monto_extra_p,
                    "destino": "Directo",
                    "notas": ""
                })
                st.toast(f"✅ Extra agregado: {motivo_extra_p}")
                st.rerun()
            else:
                st.warning("Por favor escribe el motivo del cargo extra.")

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
            if st.button("COBRAR Y ENTREGAR", type="primary", use_container_width=True, key="enviar_p"):
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
    st.header("✅ LISTOS PARA ENTREGA HOY")
    mostrar_pedidos(None, "Listo", "Entregar al Cliente", "Entregado")

# ==========================================
# PESTAÑA 6: AGENDA FUTURA
# ==========================================
with tab6:
    st.header("📅 Pedidos Agendados")
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
    st.header(" Pagos y Deudas")
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

# ==========================================
# PESTAÑA 7: INVENTARIO DIARIO
# ==========================================
with tab7:
    st.header("📦 Control de Inventario")
    if sh and datos_inv_global:
        st.write("Actualiza las cantidades con las que empiezas el día:")
        with st.form("form_inventario"):
            nuevos_valores = {}
            for i, fila in enumerate(datos_inv_global, start=2):
                producto = fila["Producto"]
                stock = int(fila["Stock"])
                nuevos_valores[i] = st.number_input(producto, value=stock, min_value=0)
            
            if st.form_submit_button("💾 Guardar Inventario", type="primary"):
                for idx, val in nuevos_valores.items():
                    sh.worksheet("Inventario").update_cell(idx, 2, val)
                st.success("¡Inventario actualizado correctamente!")
                st.rerun()
    else:
        st.warning("⚠️ No se encontró la hoja 'Inventario' en el Excel, o está vacía.")