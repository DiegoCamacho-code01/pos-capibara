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
        # Aquí está la corrección clave: strict=False
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
tab1, tab2, tab3, tab4, tab5 = st.tabs(["🛒 Carrito", "🥤 Puesto", "✅ Listos",
