import streamlit as st
import gspread
import json
import pandas as pd
from datetime import datetime, timedelta, timezone

# ==========================================
# 1. CONFIGURACIÓN VISUAL Y CSS
# ==========================================
st.set_page_config(page_title="POS Sistema", layout="wide", initial_sidebar_state="collapsed")

st.markdown("""
<style>
    div.stButton > button { height: 75px; border-radius: 8px; border: 1px solid #005A9E; font-weight: 600; background-color: #FFFFFF; color: #002244; font-size: 16px; box-shadow: 0 2px 4px rgba(0,0,0,0.05);}
    div.stButton > button:hover { border-color: #005A9E; background-color: #F3F9FF; }
    div.stButton > button:active { background-color: #CCE5FF; transform: scale(0.98); }
    div.stButton > button[kind="primary"] { background-color: #005A9E; color: white; border: none; }
    .btn-alerta > button { background-color: #FFF3CD !important; color: #856404 !important; border: 1px solid #FFEEBA !important; }
    div.stButton > button:disabled { background-color: #E9ECEF !important; color: #6C757D !important; border: 1px solid #DEE2E6 !important; opacity: 1; }
    .sticky-header { position: sticky; top: 0; background-color: white; z-index: 999; padding: 15px 0; border-bottom: 1px solid #E1E4E8; margin-bottom: 20px;}
    div[role="radiogroup"].st-emotion-cache-1n76uvr { flex-wrap: wrap; gap: 8px; } 
    .card { background-color: #FFFFFF; padding: 15px; border-radius: 8px; border-left: 5px solid #005A9E; margin-bottom: 15px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); border-right: 1px solid #E1E4E8; border-top: 1px solid #E1E4E8; border-bottom: 1px solid #E1E4E8;}
    .card-urgente { border-left: 5px solid #D93025; background-color: #FEF7F7; }
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
        st.info("Nota: Guarda esta página en tu pantalla de inicio después de acceder para no repetir este paso.")
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

# ==========================================
# 4. CONSTRUCCIÓN DEL MENÚ Y CLIENTES
# ==========================================
MENU_BASE = {
    "Café": {"Cafe Vainilla": 25, "Cafe Avellana": 25, "Café Clásico": 25},
    "Frappés": {"Fresa": 65, "Taro": 65, "Chai": 65, "Matcha": 65, "Rompope": 65, "Red Velvet": 65, "Pistache": 65, "Galleta": 65, "Mora": 65, "Cereza": 65, "Refresher Darks": 65, "Cafe": 65, "Moka": 65, "Oreo": 65, "Chocolate": 65},
    "Bebidas Frías": {"Fresa": 45, "Taro": 45, "Chai": 45, "Matcha": 45, "Rompope": 45, "Red Velvet": 45, "Pistache": 45, "Galleta": 45, "Mora": 45, "Cereza": 45, "Refresher Darks": 45, "Cafe": 45, "Moka": 45, "Oreo": 45, "Chocolate": 45},
    "Esquimos": {"Fresa": 45, "Taro": 45, "Chai": 45, "Matcha": 45, "Rompope": 45, "Red Velvet": 45, "Pistache": 45, "Galleta": 45, "Mora": 45, "Cereza": 45, "Refresher Darks": 45, "Cafe": 45, "Moka": 45, "Oreo": 45, "Chocolate": 45},
    "Chamoyadas": {"Fresa": 65, "Mango": 65, "Temporada": 65},
    "Platillos": {"Ensalada": 65, "Sandwich": 65, "Plato de Chilaquiles": 50, "Torta de Chilaquiles": 65},
    "Tortas": {},
    "Panadería": {"Pan de Dulce": 25, "Telera": 5}
}

MENU = MENU_BASE.copy()
dict_inv = {}

if len(inv) > 1:
    for row in inv[1:]:
        if len(row) >= 5 and str(row[4]).strip().lower() == "activo":
            prod, stock_str, cat, precio_str = row[0], row[1], row[2], row[3]
            
            if "Tortas" in cat:
                try: dict_inv[prod] = int(stock_str)
                except: dict_inv[prod] = 0
            else:
                dict_inv[prod] = None
            
            if cat not in MENU: MENU[cat] = {}
            try: MENU[cat][prod] = float(precio_str)
            except: MENU[cat][prod] = 0.0

if not MENU["Tortas"]:
    del MENU["Tortas"]

for c_apagada in st.session_state.cat_apagadas:
    if c_apagada in MENU: del MENU[c_apagada]

clientes_historicos = []
if len(ops) > 1:
    clientes_historicos.extend([f[0] for f in ops[1:] if len(f)>0 and f[0].strip() not in ["", "Mostrador"]])
if len(deu) > 1:
    clientes_historicos.extend([f[0] for f in deu[1:] if len(f)>0 and f[0].strip() not in ["", "Mostrador"]])
clientes_unicos = sorted(list(set(clientes_historicos)))

# ==========================================
# 5. MENÚ LATERAL Y PESTAÑAS
# ==========================================
with st.sidebar:
    st.write(f"**Operador:** {st.session_state.cajero}")
    st.divider()
    pin = st.text_input("Credencial Admin:", type="password")
    if pin == "1234":
        st.session_state.admin_mode = True
        st.success("Modo Administrador Autorizado")
    else:
        st.session_state.admin_mode = False

pestanas = ["Carrito", "Caja Rápida", "Cocina", "Entregas", "Agendados", "Pagos", "Inventario"]
if st.session_state.admin_mode: 
    pestanas.append("Administración")
    pestanas.append("Gastos (Nuevo)")
tabs = st.tabs(pestanas)

# ==========================================
# PESTAÑA 1: CARRITO (Principal)
# ==========================================
with tabs[0]:
    st.markdown('<div class="sticky-header">', unsafe_allow_html=True)
    cat_seleccionada = st.selectbox("Categorías del Menú:", list(MENU.keys()))
    st.markdown('</div>', unsafe_allow_html=True)
    
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
                btn_txt += f"\n(Últimos {stock})"
                alerta = True
            else: btn_txt += f"\n(Stock: {stock})"
            
        with cols[i%2]:
            if alerta: st.markdown('<div class="btn-alerta">', unsafe_allow_html=True)
            if st.button(btn_txt, use_container_width=True, disabled=agotado, key=f"c_{n}"):
                st.session_state.cart.append({"prod": n, "precio": p, "notas": "", "pan": False, "cat": cat_seleccionada})
                st.rerun()
            if alerta: st.markdown('</div>', unsafe_allow_html=True)
            
    with st.expander("Añadir Cargo Extra Manual"):
        c_mot, c_mon = st.columns([3,1])
        with c_mot: mot = st.text_input("Concepto:")
        with c_mon: mon = st.number_input("$", step=5.0)
        if st.button("Aplicar Extra", use_container_width=True):
            if mot:
                st.session_state.cart.append({"prod": f"Extra: {mot}", "precio": mon, "notas": "", "pan": False, "cat": "Extra"})
                st.rerun()
    
    if st.session_state.cart:
        st.divider()
        st.subheader("Resumen de Orden")
        total = 0
        for idx, item in enumerate(st.session_state.cart):
            c1, c2, c3, c4 = st.columns([2.5, 1, 2, 2.5])
            with c1: st.write(f"▪ {item['prod']}")
            with c2: st.write(f"${item['precio']}")
            with c3:
                item['notas'] = st.text_input("Notas", key=f"cn_{idx}", label_visibility="collapsed", placeholder="Notas u observaciones...")
                if "Chilaquiles" in item['prod'] and "Torta" not in item['prod']: 
                    item['pan'] = st.checkbox("Incluir Telera", key=f"cpan_{idx}")
            with c4:
                # LÓGICA DE COCINA OPTIMIZADA
                if item.get('cat') == "Platillos":
                    item['dest'] = "Cocina"
                    st.markdown("<div style='padding-top:10px; color:#005A9E; font-weight:600;'>👨‍🍳 A cocina</div>", unsafe_allow_html=True)
                else:
                    a_cocina = st.checkbox("A cocina", key=f"dest_{idx}")
                    item['dest'] = "Cocina" if a_cocina else "Entrega Directa"
                
            total += item['precio']
            
        st.write(f"### Total a Cobrar: ${total}")
        st.divider()
        
        st.write("**Datos del Cliente y Orden**")
        
        c_cli, c_pago = st.columns([2, 1])
        with c_cli:
            # LÓGICA DE CLIENTE EN BLANCO
            opcion_cliente = st.selectbox("Buscar cliente registrado:", [""] + clientes_unicos)
            if opcion_cliente == "":
                cliente = st.text_input("Nombre del cliente:", placeholder="Escriba aquí el nombre...")
            else:
                cliente = opcion_cliente
                
        with c_pago:
            pago = st.radio("Estado de Pago:", ["Pagado", "Pendiente"], index=0, horizontal=True)

        c_dest, c_dia = st.columns(2)
        with c_dest:
            tiempo = st.radio("Horario:", ["Inmediato", "Definir Hora"], horizontal=True)
            if tiempo == "Definir Hora": hora_fin = st.time_input("Especificar Hora:", step=1800).strftime("%H:%M")
            else: hora_fin = "Ahora"
            
        with c_dia:
            dia_tipo = st.radio("Día de Entrega:", ["Hoy", "Mañana", "Programado"], horizontal=True)
            if dia_tipo == "Programado": fecha_fin = st.date_input("Día en Calendario:").strftime("%d/%m/%Y")
            elif dia_tipo == "Mañana": fecha_fin = (hoy_obj + timedelta(days=1)).strftime("%d/%m/%Y")
            else: fecha_fin = hoy_str
            
        st.write("")
        c_borrar, c_enviar = st.columns(2)
        with c_borrar:
            if st.button("Descartar Orden", use_container_width=True): 
                st.session_state.cart = []
                st.rerun()
        with c_enviar:
            if st.button("PROCESAR ORDEN", type="primary", use_container_width=True):
                if not cliente.strip() and pago == "Pendiente" and not st.session_state.admin_mode: 
                    st.warning("Se requiere registrar un nombre para generar deuda.")
                else:
                    nom_final = cliente.strip() if cliente.strip() else "Mostrador"
                    panes = sum(1 for i in st.session_state.cart if "telera" in i['prod'].lower() or "torta de chilaquiles" in i['prod'].lower() or i['pan'])
                    h_real = datetime.now(zona_mx).strftime("%H:%M") if hora_fin == "Ahora" else f"{fecha_fin} {hora_fin}"
                    
                    for i in st.session_state.cart:
                        dest = i['dest']
                        est = "Pendiente" if dia_tipo != "Hoy" or tiempo != "Inmediato" else ("Preparando" if dest == "Cocina" else "Entregado")
                        sh.worksheet("Operaciones").append_row([nom_final, i['prod'], dest, i['notas'], tiempo, h_real, est, total if i == st.session_state.cart[0] else 0, fecha_fin, st.session_state.cajero])
                    
                    if pago == "Pendiente": 
                        resumen_productos = ", ".join([i['prod'] for i in st.session_state.cart])
                        sh.worksheet("Deudas").append_row([nom_final, "Deuda", total, resumen_productos, hoy_str])
                    
                    if len(inv) > 1:
                        for idx, row in enumerate(inv[1:], start=2):
                            p_nom = row[0]
                            try: p_stock = int(row[1])
                            except: p_stock = 0
                            
                            comprados = sum(1 for p in st.session_state.cart if p['prod'] == p_nom)
                            if comprados > 0: sh.worksheet("Inventario").update_cell(idx, 2, p_stock - comprados)
                            
                            if p_nom == "Telera" and panes > 0:
                                sh.worksheet("Inventario").update_cell(idx, 2, p_stock - panes)
                                
                    st.session_state.cart = []
                    leer.clear()
                    st.success("Orden procesada con éxito.")
                    st.rerun()

# ==========================================
# PESTAÑA 2: CAJA PUESTO (Bebidas Rápidas)
# ==========================================
with tabs[1]:
    st.header("Caja Rápida (Bebidas)")
    categorias_puesto = [c for c in MENU.keys() if any(x in c for x in ["Café", "Frappés", "Bebidas", "Esquimos", "Chamoyadas"])]
    
    if categorias_puesto:
        cat_p = st.selectbox("Categorías Rápidas:", categorias_puesto)
        
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
                with c1: st.write(f"▪ {item['prod']}")
                with c2: st.write(f"${item['precio']}")
                with c3: item['notas'] = st.text_input("Notas", key=f"pn_{idx}", label_visibility="collapsed")
                total_p += item['precio']
                
            st.write(f"### Total Rápido: ${total_p}")
            
            c_nom_p, c_pag_p = st.columns([1.5, 1])
            with c_nom_p: 
                opc_p = st.selectbox("Buscar cliente:", [""] + clientes_unicos, key="sb_puesto")
                if opc_p == "":
                    cliente_p = st.text_input("Nombre del cliente:", key="txt_puesto", placeholder="Escriba el nombre...")
                else: 
                    cliente_p = opc_p
            with c_pag_p: 
                pago_p = st.radio("Estado:", ["Pagado", "Pendiente"], index=0, horizontal=True, key="pag_p")
            
            st.write("")
            c_bp, c_ep = st.columns(2)
            with c_bp:
                if st.button("Descartar", key="b_puesto", use_container_width=True): 
                    st.session_state.puesto_cart = []
                    st.rerun()
            with c_ep:
                if st.button("COBRAR Y ENTREGAR", type="primary", use_container_width=True):
                    nom_final_p = cliente_p.strip() if cliente_p.strip() else "Mostrador"
                    h_real_p = datetime.now(zona_mx).strftime("%H:%M")
                    
                    for i in st.session_state.puesto_cart:
                        sh.worksheet("Operaciones").append_row([nom_final_p, i['prod'], "Entrega Directa", i['notas'], "Inmediato", h_real_p, "Entregado", total_p if i == st.session_state.puesto_cart[0] else 0, hoy_str, st.session_state.cajero])
                    
                    if pago_p == "Pendiente": 
                        resumen_productos = ", ".join([i['prod'] for i in st.session_state.puesto_cart])
                        sh.worksheet("Deudas").append_row([nom_final_p, "Deuda", total_p, resumen_productos, hoy_str])
                    
                    st.session_state.puesto_cart = []
                    leer.clear()
                    st.success("Transacción registrada.")
                    st.rerun()

# ==========================================
# PESTAÑA 3: COCINA
# ==========================================
with tabs[2]:
    st.header("Monitor de Producción")
    st.markdown("""<audio autoplay="true"><source src="https://www.soundjay.com/misc/sounds/bell-ringing-05.mp3" type="audio/mpeg"></audio>""", unsafe_allow_html=True)
    
    if len(ops) > 1:
        pedidos_cocina = [ (i, f) for i, f in enumerate(ops[1:], start=2) if len(f) > 8 and f[8] == hoy_str and f[6] == "Preparando" and f[2] == "Cocina" ]
        pedidos_cocina.sort(key=lambda x: (x[1][4] != "Inmediato", x[1][5])) 
        
        if pedidos_cocina:
            for i, f in pedidos_cocina:
                urgente = "card-urgente" if f[4] == "Inmediato" else ""
                st.markdown(f'<div class="card {urgente}"><h4>{f[1]}</h4><p><b>{f[0]}</b> | Hora: {f[5]}</p><p style="color:#D93025; font-size:14px;">Observaciones: {f[3]}</p></div>', unsafe_allow_html=True)
                
                c_listo, c_canc = st.columns(2)
                with c_listo:
                    if st.button(f"Marcar como Listo", key=f"l_{i}", use_container_width=True):
                        sh.worksheet("Operaciones").update_cell(i, 7, "Listo")
                        leer.clear()
                        st.rerun()
                with c_canc:
                    if st.session_state.admin_mode:
                        if st.button("Forzar Cancelación", key=f"fc_{i}", use_container_width=True):
                            sh.worksheet("Operaciones").delete_rows(i)
                            leer.clear()
                            st.rerun()
        else:
            st.info("Sin órdenes en producción.")

# ==========================================
# PESTAÑA 4: LISTOS PARA ENTREGA
# ==========================================
with tabs[3]:
    st.header("Entregas Pendientes")
    if len(ops) > 1:
        hay_listos = False
        for i, f in enumerate(ops[1:], start=2):
            if len(f) > 8 and f[8] == hoy_str and f[6] == "Listo":
                hay_listos = True
                st.success(f"**{f[1]}** | Para: {f[0]}")
                if st.button("Entregado a cliente", key=f"ent_{i}"):
                    sh.worksheet("Operaciones").update_cell(i, 7, "Entregado")
                    leer.clear()
                    st.rerun()
        if not hay_listos: st.write("No hay órdenes esperando entrega.")

# ==========================================
# PESTAÑA 5: AGENDADOS
# ==========================================
with tabs[4]:
    st.header("Programación de Órdenes")
    if len(ops) > 1:
        futuros = 0
        for i, f in enumerate(ops[1:], start=2):
            if len(f) > 8 and f[6] not in ["Entregado", "Listo"]:
                try:
                    fecha_pedido = datetime.strptime(f[8], "%d/%m/%Y").date()
                except:
                    fecha_pedido = hoy_obj 

                if fecha_pedido < hoy_obj:
                    continue
                
                if fecha_pedido > hoy_obj:
                    futuros += 1
                    st.info(f"**{f[1]}** - Fecha: {f[8]} | Cliente: {f[0]}")
                    
                    if st.session_state.admin_mode:
                        with st.expander("Modificar Orden (Solo Admin)"):
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
                        if st.button("Pasar a cocina hoy", key=f"ae_coc_{i}"):
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
# PESTAÑA 6: PAGOS
# ==========================================
with tabs[5]:
    st.header("Control de Crédito")
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
                with st.expander(f"{c} - Saldo Pendiente: ${round(info['tot'], 2)}", expanded=(busqueda!="Todos los saldos")):
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
                        if st.button("Registrar Ingreso", key=f"p_{c}", use_container_width=True):
                            sh.worksheet("Deudas").append_row([c, "Abono", abono, "Pago", hoy_str])
                            leer.clear()
                            st.rerun()

# ==========================================
# PESTAÑA 7: INVENTARIO
# ==========================================
with tabs[6]:
    st.header("Gestión de Inventario")
    
    with st.expander("Alta de Nuevo Producto"):
        with st.form("add_inv"):
            n_prod = st.text_input("Nombre comercial:")
            n_cat = st.selectbox("Clasificación de menú:", list(MENU_BASE.keys()))
            n_pre = st.number_input("Precio Unitario ($):", step=5.0)
            
            if "Tortas" in n_cat:
                st.info("Para este producto perecedero, defina el inventario inicial:")
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
                    st.warning("Especifique un nombre válido.")

    st.divider()
    st.write("Conteo físico y existencias operativas (Solo para productos con límite):")
    
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
                st.info("No hay productos con conteo físico activo en este momento.")
                
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
        st.header("Panel Administrativo")
        
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
            cortes_recientes = cortes[-6:] 
            df_cortes = pd.DataFrame(cortes_recientes)
            st.bar_chart(df_cortes.set_index("Fecha"))
        else:
            st.info("Aún no hay cortes de caja registrados en el historial.")
            
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
                    else:
                        filas_mantener.append(f)
                
                if tickets_borrados > 0:
                    ws_hist.append_row([hoy_str, "Cierre Diario", f"Ingreso Total: ${ventas_hoy}", f"Transacciones: {tickets_borrados}"])
                    ws_ops.clear()
                    ws_ops.update("A1", filas_mantener)
                    leer.clear()
                    st.success(f"Corte exitoso procesado por: ${ventas_hoy}.")
                    st.rerun()
                else:
                    st.info("No existen operaciones de hoy aptas para cierre.")
            except Exception as e:
                st.error(f"Falla de ejecución: {e}")
                
        st.divider()
        
        st.subheader("Editor Base de Datos (Avanzado)")
        if len(ops) > 0:
            df_ops = pd.DataFrame(ops[1:], columns=ops[0])
            edited_df = st.data_editor(df_ops, num_rows="dynamic", use_container_width=True)
            if st.button("Forzar Sincronización de Tabla"):
                edited_df = edited_df.fillna("")
                datos_nuevos_ops = [edited_df.columns.values.tolist()] + edited_df.values.tolist()
                ws_o = sh.worksheet("Operaciones")
                ws_o.clear()
                ws_o.update("A1", datos_nuevos_ops)
                leer.clear()
                st.success("Sincronización maestra completada.")
                st.rerun()

# ==========================================
# PESTAÑA 9: GASTOS (NUEVO - SOLO ADMIN)
# ==========================================
if st.session_state.admin_mode:
    with tabs[8]:
        st.header("Control de Compras e Insumos")
        
        with st.form("form_gastos"):
            st.write("Registrar nuevo gasto operativo:")
            g_conc = st.text_input("Concepto / Insumo (Ej. Vasos, Polvo Taro, Café):")
            
            c_cant, c_uni, c_precio = st.columns(3)
            with c_cant: g_cant = st.number_input("Cantidad:", min_value=0.0, step=0.5, value=1.0)
            with c_uni: g_uni = st.selectbox("Unidad:", ["Pieza(s)", "Kg", "Litro(s)", "Paquete(s)"])
            with c_precio: g_precio = st.number_input("Precio Unitario ($):", min_value=0.0, step=10.0, value=0.0)
            
            total_gasto = g_cant * g_precio
            st.info(f"**Total calculado del gasto:** ${total_gasto}")
            
            if st.form_submit_button("Registrar Gasto en Base de Datos"):
                if g_conc.strip() and total_gasto > 0:
                    try:
                        sh.worksheet("Gastos").append_row([hoy_str, g_conc, g_cant, g_uni, g_precio, total_gasto, semana_str])
                        leer.clear()
                        st.success(f"Gasto de ${total_gasto} registrado correctamente.")
                        st.rerun()
                    except Exception as e:
                        st.error("Por favor, asegúrate de haber creado la pestaña 'Gastos' en Google Sheets.")
                else:
                    st.warning("Escribe el concepto y asegúrate de que el precio sea mayor a cero.")
                    
        st.divider()
        
        st.subheader("Historial de Gastos por Semana")
        gas_validos = [fila[:7] for fila in gas if len(fila) >= 7 and fila[0] != "Fecha"]
        
        if len(gas_validos) > 0:
            df_gas = pd.DataFrame(gas_validos, columns=["Fecha", "Concepto", "Cantidad", "Unidad", "Precio_Unitario", "Total", "Semana"])
            df_gas['Total'] = pd.to_numeric(df_gas['Total'], errors='coerce').fillna(0)
            
            semanas_unicas = df_gas['Semana'].unique()
            
            for sem in reversed(semanas_unicas): 
                df_semana = df_gas[df_gas['Semana'] == sem]
                total_semana = df_semana['Total'].sum()
                
                with st.expander(f"Semana: {sem} --- Total Gastado: ${total_semana}"):
                    for index, row in df_semana.iterrows():
                        st.write(f"- {row['Fecha']}: **{row['Concepto']}** ({row['Cantidad']} {row['Unidad']}) ➔ **${row['Total']}**")
        else:
            st.info("No hay registros de gastos aún.")