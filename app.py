import streamlit as st
import plotly.graph_objects as go
import pandas as pd
from datetime import datetime, date, time, timedelta
import heapq
import json
import os
import h3
import requests
from geopy.geocoders import Nominatim

# ==========================================
# 1. PERSISTÊNCIA DE DADOS
# ==========================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BOOKINGS_FILE = os.path.join(BASE_DIR, "data", "bookings_rio.json")
CUSTOM_MAP_FILE = os.path.join(BASE_DIR, "data", "custom_map.json")

def load_json(file_path, default_val):
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except: return default_val
    return default_val

def save_json(file_path, data):
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_bookings():
    data = load_json(BOOKINGS_FILE, [])
    for b in data:
        b["start_time"] = datetime.fromisoformat(b["start_time"])
        b["end_time"] = datetime.fromisoformat(b["end_time"])
    return data

def save_bookings(bookings):
    ser = []
    for b in bookings:
        c = b.copy()
        c["start_time"] = b["start_time"].isoformat()
        c["end_time"] = b["end_time"].isoformat()
        ser.append(c)
    save_json(BOOKINGS_FILE, ser)

if "bookings" not in st.session_state: st.session_state.bookings = load_bookings()
if "void_cells" not in st.session_state: st.session_state.void_cells = set(load_json(CUSTOM_MAP_FILE, []))

# ==========================================
# 0. CAMADA DE COMPATIBILIDADE H3
# ==========================================
def h3_lat_lng_to_cell(lat: float, lng: float, res: int) -> str:
    if hasattr(h3, 'latlng_to_cell'): return h3.latlng_to_cell(lat, lng, res)
    elif hasattr(h3, 'lat_lng_to_cell'): return h3.lat_lng_to_cell(lat, lng, res)
    elif hasattr(h3, 'geo_to_h3'): return h3.geo_to_h3(lat, lng, res)
    return ""

def h3_cell_to_lat_lng(cell: str):
    if hasattr(h3, 'cell_to_latlng'): return h3.cell_to_latlng(cell)
    elif hasattr(h3, 'h3_to_geo'): return h3.h3_to_geo(cell)
    return None

def h3_grid_disk(cell: str, k: int) -> set:
    if hasattr(h3, 'grid_disk'): return set(h3.grid_disk(cell, k))
    elif hasattr(h3, 'k_ring'): return set(h3.k_ring(cell, k))
    return {cell}

def h3_grid_ring(cell: str, k: int) -> set:
    if hasattr(h3, 'grid_ring'): return set(h3.grid_ring(cell, k))
    elif hasattr(h3, 'hex_ring'): return set(h3.hex_ring(cell, k)) if k > 0 else {cell}
    return {cell}

def h3_grid_distance(cell_a: str, cell_b: str) -> int:
    if hasattr(h3, 'grid_distance'): return h3.grid_distance(cell_a, cell_b)
    elif hasattr(h3, 'h3_distance'): return h3.h3_distance(cell_a, cell_b)
    return 1

def h3_cell_to_boundary(cell: str):
    if hasattr(h3, 'cell_to_boundary'): return h3.cell_to_boundary(cell)
    elif hasattr(h3, 'h3_to_geo_boundary'): return h3.h3_to_geo_boundary(cell)
    return []

geolocator = Nominatim(user_agent="simulador_vacinacao_rio_v8")

@st.cache_data(show_spinner=False)
def geocode_endereco_ou_cep(texto: str):
    texto_limpo = texto.replace("-", "").strip()
    if texto_limpo.isdigit() and len(texto_limpo) == 8:
        try:
            resp = requests.get(f"https://viacep.com.br/ws/{texto_limpo}/json/", timeout=5)
            if resp.status_code == 200 and "erro" not in resp.json():
                d = resp.json()
                rua, bairro = d.get('logradouro', ''), d.get('bairro', '')
                loc = geolocator.geocode(f"{rua}, {bairro}, Rio de Janeiro", timeout=10)
                if loc: return loc.latitude, loc.longitude, f"{rua} - {bairro}"
                loc_bairro = geolocator.geocode(f"{bairro}, Rio de Janeiro", timeout=10)
                if loc_bairro: return loc_bairro.latitude, loc_bairro.longitude, f"{bairro}"
        except: pass

    try:
        loc = geolocator.geocode(f"{texto}, Rio de Janeiro, Brasil", timeout=10)
        if loc: return loc.latitude, loc.longitude, loc.address.split(",")[0]
    except: pass
    return None, None, None

# ==========================================
# 2. MALHA DENSA E CORREDORES DE TRÂNSITO
# ==========================================
H3_RESOLUTION = 9
BASE_STEP_TIME_MINS = 1.5
INTRA_CELL_TIME_MINS = 3.0

CORREDORES_VIARIOS = [
    {"p1": (-22.8870, -43.2250), "p2": (-22.8550, -43.3800), "mult": 1.5, "raio": 1},
    {"p1": (-22.8620, -43.2500), "p2": (-22.9800, -43.3400), "mult": 1.4, "raio": 1},
    {"p1": (-22.8050, -43.2400), "p2": (-22.8980, -43.2100), "mult": 1.4, "raio": 1},
    {"p1": (-22.9250, -43.2080), "p2": (-22.9600, -43.1850), "mult": 1.5, "raio": 1},
    {"p1": (-23.0000, -43.3200), "p2": (-23.0250, -43.4600), "mult": 1.3, "raio": 1},
    {"p1": (-22.8950, -43.2800), "p2": (-22.9240, -43.2320), "mult": 1.3, "raio": 1},
]

CONGESTED_ZONES = {}
for corr in CORREDORES_VIARIOS:
    lat1, lng1 = corr["p1"]
    lat2, lng2 = corr["p2"]
    steps = 25
    for i in range(steps + 1):
        interp_lat = lat1 + (lat2 - lat1) * (i / steps)
        interp_lng = lng1 + (lng2 - lng1) * (i / steps)
        c = h3_lat_lng_to_cell(interp_lat, interp_lng, H3_RESOLUTION)
        if c:
            for viz in h3_grid_disk(c, corr["raio"]):
                CONGESTED_ZONES[viz] = max(CONGESTED_ZONES.get(viz, 1.0), corr["mult"])

def gerar_retangulo_rio_continuo(res: int) -> set:
    malha = set()
    lat_min, lat_max = -23.05, -22.78
    lng_min, lng_max = -43.60, -43.10
    step = 0.0015
    current_lat = lat_min
    while current_lat <= lat_max:
        current_lng = lng_min
        while current_lng <= lng_max:
            cell = h3_lat_lng_to_cell(current_lat, current_lng, res)
            if cell: malha.add(cell)
            current_lng += step
        current_lat += step
    return malha

MALHA_TOTAL = gerar_retangulo_rio_continuo(H3_RESOLUTION)
MALHA_ATIVA = MALHA_TOTAL - st.session_state.void_cells
VEHICLE_COLORS = ["#FF4B4B", "#9B59B6", "#2ECC71", "#F1C40F", "#E67E22"]

# ==========================================
# 3. MOTOR A* DE ROTEAMENTO (COM MAPEAMENTO DE CAMINHO)
# ==========================================
def get_time_multiplier(dep_time: datetime) -> float:
    t = dep_time.time()
    if time(7, 0) <= t < time(10, 0): return 1.6
    elif time(16, 30) <= t <= time(19, 30): return 1.7
    elif time(10, 0) <= t < time(16, 30): return 1.15
    return 1.0

def calculate_travel_time_mins(cell_a: str, cell_b: str, departure_time: datetime) -> float:
    if cell_a in st.session_state.void_cells or cell_b in st.session_state.void_cells: return float('inf')
    if cell_a == cell_b: return INTRA_CELL_TIME_MINS * get_time_multiplier(departure_time) * CONGESTED_ZONES.get(cell_a, 1.0)

    pq = [(0.0, 0.0, cell_a)]
    g_score = {cell_a: 0.0}
    
    while pq:
        _, current_cost, current = heapq.heappop(pq)
        if current == cell_b: return current_cost
        if current_cost > g_score.get(current, float('inf')): continue

        current_time = departure_time + timedelta(minutes=current_cost)
            
        for neighbor in h3_grid_ring(current, 1):
            if neighbor in st.session_state.void_cells or neighbor not in MALHA_TOTAL: continue
            segment_mult = (CONGESTED_ZONES.get(current, 1.0) + CONGESTED_ZONES.get(neighbor, 1.0)) / 2.0
            step_cost = BASE_STEP_TIME_MINS * get_time_multiplier(current_time) * segment_mult
            new_cost = current_cost + step_cost
            
            if new_cost < g_score.get(neighbor, float('inf')):
                g_score[neighbor] = new_cost
                heapq.heappush(pq, (new_cost + h3_grid_distance(neighbor, cell_b)*BASE_STEP_TIME_MINS, new_cost, neighbor))
    return float('inf')

def get_a_star_path(cell_a: str, cell_b: str, departure_time: datetime):
    if cell_a in st.session_state.void_cells or cell_b in st.session_state.void_cells: 
        return float('inf'), []
    if cell_a == cell_b: 
        return INTRA_CELL_TIME_MINS * get_time_multiplier(departure_time) * CONGESTED_ZONES.get(cell_a, 1.0), [cell_a]

    pq = [(0.0, 0.0, cell_a)]
    g_score = {cell_a: 0.0}
    came_from = {}
    
    while pq:
        _, current_cost, current = heapq.heappop(pq)
        
        if current == cell_b:
            path = []
            curr = current
            while curr in came_from:
                path.append(curr)
                curr = came_from[curr]
            path.append(cell_a)
            path.reverse()
            return current_cost, path

        if current_cost > g_score.get(current, float('inf')): continue

        current_time = departure_time + timedelta(minutes=current_cost)
            
        for neighbor in h3_grid_ring(current, 1):
            if neighbor in st.session_state.void_cells or neighbor not in MALHA_TOTAL: continue
            segment_mult = (CONGESTED_ZONES.get(current, 1.0) + CONGESTED_ZONES.get(neighbor, 1.0)) / 2.0
            step_cost = BASE_STEP_TIME_MINS * get_time_multiplier(current_time) * segment_mult
            new_cost = current_cost + step_cost
            
            if new_cost < g_score.get(neighbor, float('inf')):
                came_from[neighbor] = current
                g_score[neighbor] = new_cost
                heapq.heappush(pq, (new_cost + h3_grid_distance(neighbor, cell_b)*BASE_STEP_TIME_MINS, new_cost, neighbor))
                
    return float('inf'), []

def is_slot_feasible_for_vehicle(v_bookings, c_cell, c_start, dur):
    if c_cell in st.session_state.void_cells: return False
    c_end = c_start + timedelta(minutes=dur)
    sb = sorted(v_bookings, key=lambda x: x["start_time"])
    prev_b = next_b = None
    for b in sb:
        if max(b["start_time"], c_start) < min(b["end_time"], c_end): return False
        if b["end_time"] <= c_start: prev_b = b
        elif b["start_time"] >= c_end and not next_b: next_b = b

    if prev_b:
        t_ab = calculate_travel_time_mins(prev_b["cell_id"], c_cell, prev_b["end_time"])
        if prev_b["end_time"] + timedelta(minutes=t_ab) > c_start: return False
    if next_b:
        t_bc = calculate_travel_time_mins(c_cell, next_b["cell_id"], c_end)
        if c_end + timedelta(minutes=t_bc) > next_b["start_time"]: return False
    return True

# ==========================================
# 4. INTERFACE STREAMLIT
# ==========================================
st.set_page_config(page_title="Sistema Logístico H3 - Rio de Janeiro", layout="wide")
st.title("🏖️ App Operacional de Vacinação (Planeamento D-1)")

if "data_operacao" not in st.session_state:
    st.session_state.data_operacao = date.today()

with st.sidebar:
    st.header("⚙️ Painel de Gestão Operacional")
    st.session_state.data_operacao = st.date_input("🗓️ Data da Operação (Visão Gestor / Rotas):", value=st.session_state.data_operacao)
    num_vehicles = st.slider("Frota (Veículos / Enfermeiros)", 1, 5, 3)
    
    st.markdown("---")
    st.caption("Ações de Manutenção:")
    if st.button("🗑️ Limpar Todos os Agendamentos"):
        st.session_state.bookings = []
        save_bookings([])
        st.rerun()

tab_agendar, tab_editar, tab_rotas, tab_mapa_rotas = st.tabs([
    "📋 Agendamento", 
    "🗺️ Edição do Mapa", 
    "🚗 Tabela de Roteirização",
    "🗺️ Esboço Visual de Rotas"
])

# --- ABA 1: AGENDAMENTO DO CLIENTE ---
with tab_agendar:
    st.subheader("Novo Agendamento (Portal do Cliente)")
    col_form, col_preview = st.columns([1, 1.2])
    
    with col_form:
        client_name = st.text_input("Nome do Paciente", "Paciente Exemplo")
        input_end = st.text_input("CEP ou Endereço:", placeholder="Ex: 20960-005 (Méier / Rocha)")
        
        data_atendimento_cliente = st.date_input("🗓️ Data do Atendimento Desejado:", value=date.today(), min_value=date.today())
        
        if "search_result" not in st.session_state: st.session_state.search_result = None

        if st.button("🔍 Buscar Endereço", type="secondary"):
            with st.spinner("Buscando GPS..."):
                lat, lng, end_encontrado = geocode_endereco_ou_cep(input_end)
                if lat:
                    st.session_state.search_result = {"lat": lat, "lng": lng, "address": end_encontrado}
                else: st.error("Endereço não encontrado.")

        if st.session_state.search_result:
            loc = st.session_state.search_result
            st.info(f"📍 Endereço Localizado: **{loc['address']}**")
            st.caption("💡 **Ajuste Fino:** Se o pino no mapa ao lado não estiver na sua casa, basta **clicar em qualquer ponto do mapa** para reposicioná-lo!")
            
            final_cell = h3_lat_lng_to_cell(loc["lat"], loc["lng"], H3_RESOLUTION)
            
            if final_cell in st.session_state.void_cells:
                st.error("🚫 O ponto selecionado está numa área bloqueada para atendimento (Vazio Geográfico).")
            else:
                n_vac = st.number_input("Quantidade de Vacinas", 1, 5, 2)
                dur = 10.0 + (n_vac * 5.0)

                slots = [datetime.combine(data_atendimento_cliente, time(6, 0)) + timedelta(minutes=10 * i) for i in range(72)]
                avail = []
                day_b = [b for b in st.session_state.bookings if b["start_time"].date() == data_atendimento_cliente]

                for s in slots:
                    f_vids = [v for v in range(1, num_vehicles + 1) if is_slot_feasible_for_vehicle([b for b in day_b if b["vehicle_id"] == v], final_cell, s, dur)]
                    if f_vids: avail.append((s, f_vids))

                if avail:
                    labels = [f"{slot.strftime('%I:%M %p')} ({len(v_ids)} vaga(s))" for slot, v_ids in avail]
                    sel_lbl = st.selectbox("Selecione o Horário Desejado", labels)
                    sel_slot, sel_vids = avail[labels.index(sel_lbl)]

                    if st.button("✅ Confirmar Agendamento", type="primary"):
                        st.session_state.bookings.append({
                            "client": client_name, "bairro": loc["address"], "cell_id": final_cell,
                            "lat": loc["lat"], "lng": loc["lng"],
                            "start_time": sel_slot, "end_time": sel_slot + timedelta(minutes=dur),
                            "vaccines": n_vac, "vehicle_id": sel_vids[0]
                        })
                        save_bookings(st.session_state.bookings)
                        st.session_state.search_result = None
                        st.success(f"Agendamento confirmado para {sel_slot.strftime('%d/%m/%Y às %I:%M %p')}!")
                        st.rerun()
                else: st.error("Sem viaturas disponíveis para este local na data escolhida.")

    with col_preview:
        if st.session_state.search_result:
            loc = st.session_state.search_result
            grid_lats, grid_lons = [], []
            step = 0.0008
            for dlat in [i * step for i in range(-12, 13)]:
                for dlng in [j * step for j in range(-12, 13)]:
                    grid_lats.append(loc["lat"] + dlat)
                    grid_lons.append(loc["lng"] + dlng)

            MapClass = go.Scattermap if hasattr(go, "Scattermap") else go.Scattermapbox
            
            trace_click_target = MapClass(
                lat=grid_lats, lon=grid_lons, mode='markers',
                marker=dict(size=12, color='rgba(0,120,255,0.08)', opacity=0.1),
                hoverinfo='text', hovertext="Clique aqui para mover o pino para esta posição",
                showlegend=False
            )
            
            trace_pin = MapClass(
                lat=[loc["lat"]], lon=[loc["lng"]], mode='markers+text',
                text=["📍 Ponto do Atendimento"], textposition="top center",
                marker=dict(size=20, color="#FF4B4B"), showlegend=False
            )

            fig_prev = go.Figure([trace_click_target, trace_pin])
            layout_cfg = dict(map_style="open-street-map", map=dict(center=dict(lat=loc["lat"], lon=loc["lng"]), zoom=15)) if hasattr(go, "Scattermap") else dict(mapbox_style="open-street-map", mapbox=dict(center=dict(lat=loc["lat"], lon=loc["lng"]), zoom=15))
            fig_prev.update_layout(margin=dict(l=0, r=0, t=0, b=0), height=450, **layout_cfg)

            prev_event = st.plotly_chart(fig_prev, use_container_width=True, on_select="rerun", selection_mode="points", key="preview_map_pin")

            if prev_event and "selection" in prev_event and "points" in prev_event["selection"]:
                pts = prev_event["selection"]["points"]
                if pts:
                    clicked_pt = pts[0]
                    new_lat = clicked_pt.get("lat", clicked_pt.get("y"))
                    new_lng = clicked_pt.get("lon", clicked_pt.get("x"))
                    if new_lat and new_lng and (abs(new_lat - loc["lat"]) > 0.00001 or abs(new_lng - loc["lng"]) > 0.00001):
                        st.session_state.search_result["lat"] = new_lat
                        st.session_state.search_result["lng"] = new_lng
                        st.toast("📍 Pino movido com sucesso!", icon="📍")
                        st.rerun()

# --- ABA 2: EDIÇÃO DO MAPA ---
with tab_editar:
    st.subheader(f"Sala de Comando (Operação do dia {st.session_state.data_operacao.strftime('%d/%m/%Y')})")
    
    col_t1, col_t2, col_t3 = st.columns(3)
    with col_t1:
        modo_edicao = st.radio("Ferramenta de Laço:", ["🔴 Forçar Apagar (Borracha)", "🟢 Forçar Ativar (Pincel)", "🔄 Alternar (Inteligente)"])
    with col_t2:
        raio_apagar = st.slider("Raio do Pincel", 0, 4, 1)
        mostrar_apagados = st.toggle("👁️ Mostrar áreas apagadas em cinza", value=True)
    with col_t3:
        st.caption(f"Hexágonos Removidos: **{len(st.session_state.void_cells)}**")
        if st.button("🔄 Restaurar Todos os Hexágonos"):
            st.session_state.void_cells = set()
            save_json(CUSTOM_MAP_FILE, [])
            st.rerun()

    traces = []
    MapClass = go.Scattermap if hasattr(go, "Scattermap") else go.Scattermapbox

    red_l, red_g, yel_l, yel_g, blu_l, blu_g = [], [], [], [], [], []
    for cell in MALHA_ATIVA:
        bnd = h3_cell_to_boundary(cell)
        if bnd:
            ls = [p[0] for p in bnd] + [bnd[0][0], None]
            gs = [p[1] for p in bnd] + [bnd[0][1], None]
            mult = CONGESTED_ZONES.get(cell, 1.0)
            if mult >= 1.4: red_l.extend(ls); red_g.extend(gs)
            elif mult >= 1.2: yel_l.extend(ls); yel_g.extend(gs)
            else: blu_l.extend(ls); blu_g.extend(gs)

    traces.append(MapClass(lat=red_l, lon=red_g, mode='lines', fill='toself', fillcolor='rgba(255,0,0,0.35)', line=dict(color='rgba(255,0,0,0.5)', width=1), name="Trânsito Severo", hoverinfo='skip'))
    traces.append(MapClass(lat=yel_l, lon=yel_g, mode='lines', fill='toself', fillcolor='rgba(255,165,0,0.35)', line=dict(color='rgba(255,165,0,0.5)', width=1), name="Trânsito Moderado", hoverinfo='skip'))
    traces.append(MapClass(lat=blu_l, lon=blu_g, mode='lines', fill='toself', fillcolor='rgba(28,131,225,0.12)', line=dict(color='rgba(28,131,225,0.35)', width=1), name="Tráfego Livre", hoverinfo='skip'))

    if mostrar_apagados:
        void_l, void_g = [], []
        for cell in st.session_state.void_cells:
            if cell not in MALHA_TOTAL: continue
            bnd = h3_cell_to_boundary(cell)
            if bnd:
                ls = [p[0] for p in bnd] + [bnd[0][0], None]
                gs = [p[1] for p in bnd] + [bnd[0][1], None]
                void_l.extend(ls); void_g.extend(gs)
        if void_l: traces.append(MapClass(lat=void_l, lon=void_g, mode='lines', fill='toself', fillcolor='rgba(100,100,100,0.5)', line=dict(color='rgba(50,50,50,0.8)', width=1), name="Vazios (Apagados)", hoverinfo='skip'))

    pts_lat, pts_lng, pts_cells, pts_hover = [], [], [], []
    for cell in MALHA_TOTAL:
        coords = h3_cell_to_lat_lng(cell)
        if coords:
            pts_lat.append(coords[0]); pts_lng.append(coords[1]); pts_cells.append(cell)
            if cell in st.session_state.void_cells: pts_hover.append(f"Hexágono: {cell}<br>🔴 VAZIO")
            else: pts_hover.append(f"Hexágono: {cell}<br>🟢 ATIVO ({CONGESTED_ZONES.get(cell, 1.0)}x)")

    traces.append(MapClass(
        lat=pts_lat, lon=pts_lng, mode='markers', marker=dict(size=14, color='rgba(0,0,0,0.01)', opacity=0.01),
        customdata=pts_cells, hoverinfo='text', hovertext=pts_hover, showlegend=False
    ))

    dia_b = [b for b in st.session_state.bookings if b["start_time"].date() == st.session_state.data_operacao]
    m_lats, m_lngs, m_texts, m_colors = [], [], [], []
    for b in dia_b:
        m_lats.append(b["lat"]); m_lngs.append(b["lng"])
        m_texts.append(f"<b>{b['client']}</b><br>V{b['vehicle_id']}")
        m_colors.append(VEHICLE_COLORS[(b["vehicle_id"] - 1) % len(VEHICLE_COLORS)])

    if m_lats:
        traces.append(MapClass(lat=m_lats, lon=m_lngs, mode='markers+text', text=m_texts, textposition="top center", marker=dict(size=16, color=m_colors), showlegend=False))

    fig = go.Figure(traces)
    layout_cfg = dict(map_style="open-street-map", map=dict(center=dict(lat=-22.92, lon=-43.30), zoom=10), dragmode="lasso") if hasattr(go, "Scattermap") else dict(mapbox_style="open-street-map", mapbox=dict(center=dict(lat=-22.92, lon=-43.30), zoom=10), dragmode="lasso")
    fig.update_layout(margin=dict(l=0, r=0, t=0, b=0), height=550, legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01), **layout_cfg)

    event = st.plotly_chart(fig, use_container_width=True, on_select="rerun", selection_mode=["box", "lasso", "points"], key="editor_map")

    if event and "selection" in event and "points" in event["selection"]:
        selected_points = event["selection"]["points"]
        if selected_points:
            base_cells = [pts_cells[pt["point_index"]] for pt in selected_points if pt["point_index"] < len(pts_cells)]
            if base_cells:
                target_cells = set()
                for bc in base_cells: target_cells.update(h3_grid_disk(bc, raio_apagar))
                
                if "🔴" in modo_edicao: st.session_state.void_cells.update(target_cells)
                elif "🟢" in modo_edicao: st.session_state.void_cells.difference_update(target_cells)
                else:
                    if base_cells[0] in st.session_state.void_cells: st.session_state.void_cells.difference_update(target_cells)
                    else: st.session_state.void_cells.update(target_cells)
                
                save_json(CUSTOM_MAP_FILE, list(st.session_state.void_cells))
                st.rerun()

# --- ABA 3: TABELA DE ROTEIRIZAÇÃO ---
with tab_rotas:
    st.markdown(f"### 🚗 Rotas dos Enfermeiros ({st.session_state.data_operacao.strftime('%d/%m/%Y')})")
    day_bookings = [b for b in st.session_state.bookings if b["start_time"].date() == st.session_state.data_operacao]

    if not day_bookings:
        st.info("Nenhum agendamento encontrado para a data selecionada na barra lateral.")
    else:
        vehicle_tabs = st.tabs([f"🚗 Veículo {v}" for v in range(1, num_vehicles + 1)])
        
        for v_idx, v_tab in enumerate(vehicle_tabs):
            v_id = v_idx + 1
            with v_tab:
                v_books = sorted([b for b in day_bookings if b["vehicle_id"] == v_id], key=lambda x: x["start_time"])
                
                if not v_books:
                    st.write(f"O Veículo {v_id} não possui pacientes agendados nesta data.")
                else:
                    table_data = []
                    prev_cell = None
                    prev_end = None

                    for seq, b in enumerate(v_books):
                        perm_mins = int((b["end_time"] - b["start_time"]).total_seconds() / 60)
                        
                        if prev_cell is None:
                            desloc_texto = "0 mins (Início de Turno)"
                        else:
                            tt = calculate_travel_time_mins(prev_cell, b["cell_id"], prev_end)
                            desloc_texto = "Inviável (Sem rota)" if tt == float('inf') else f"{int(tt)} mins"

                        bairro_nome = b["bairro"].split('-')[0].strip() if '-' in b["bairro"] else b["bairro"]

                        table_data.append({
                            "Seq": seq + 1,
                            "Paciente": b["client"],
                            "Bairro / Região": bairro_nome,
                            "Célula H3": b["cell_id"],
                            "Início": b["start_time"].strftime("%I:%M %p"),
                            "Fim": b["end_time"].strftime("%I:%M %p"),
                            "Permanência": f"{perm_mins} mins",
                            "Deslocamento Anterior": desloc_texto
                        })

                        prev_cell = b["cell_id"]
                        prev_end = b["end_time"]

                    df = pd.DataFrame(table_data)
                    st.dataframe(df, use_container_width=True, hide_index=True)

# --- ABA 4: ESBOÇO VISUAL DE ROTAS (CORRIGIDO SEM 'LINE' NO MARKER) ---
with tab_mapa_rotas:
    st.markdown(f"### 🗺️ Trajetos Espaciais das Viaturas ({st.session_state.data_operacao.strftime('%d/%m/%Y')})")
    
    mostrar_malha_rotas = st.toggle("⬢ Mostrar Malha Hexagonal H3 sobre o Mapa", value=False, key="toggle_malha_rotas")
    
    day_bookings = [b for b in st.session_state.bookings if b["start_time"].date() == st.session_state.data_operacao]
    
    if not day_bookings:
        st.info("Nenhum agendamento para desenhar rotas nesta data.")
    else:
        MapClass = go.Scattermap if hasattr(go, "Scattermap") else go.Scattermapbox
        route_traces = []

        if mostrar_malha_rotas:
            red_l, red_g, yel_l, yel_g, blu_l, blu_g = [], [], [], [], [], []
            for cell in MALHA_ATIVA:
                bnd = h3_cell_to_boundary(cell)
                if bnd:
                    ls = [p[0] for p in bnd] + [bnd[0][0], None]
                    gs = [p[1] for p in bnd] + [bnd[0][1], None]
                    mult = CONGESTED_ZONES.get(cell, 1.0)
                    if mult >= 1.4: red_l.extend(ls); red_g.extend(gs)
                    elif mult >= 1.2: yel_l.extend(ls); yel_g.extend(gs)
                    else: blu_l.extend(ls); blu_g.extend(gs)

            route_traces.append(MapClass(lat=red_l, lon=red_g, mode='lines', fill='toself', fillcolor='rgba(255,0,0,0.22)', line=dict(color='rgba(255,0,0,0.35)', width=1), name="Trânsito Severo", hoverinfo='skip'))
            route_traces.append(MapClass(lat=yel_l, lon=yel_g, mode='lines', fill='toself', fillcolor='rgba(255,165,0,0.22)', line=dict(color='rgba(255,165,0,0.35)', width=1), name="Trânsito Moderado", hoverinfo='skip'))
            route_traces.append(MapClass(lat=blu_l, lon=blu_g, mode='lines', fill='toself', fillcolor='rgba(28,131,225,0.08)', line=dict(color='rgba(28,131,225,0.25)', width=1), name="Tráfego Livre", hoverinfo='skip'))

        # DESENHA OS TRAJETOS FÍSICOS REAIS
        for v in range(1, num_vehicles + 1):
            v_books = sorted([b for b in day_bookings if b["vehicle_id"] == v], key=lambda x: x["start_time"])
            if not v_books: continue
            
            v_color = VEHICLE_COLORS[(v - 1) % len(VEHICLE_COLORS)]
            
            # 1. LINHA DE TRAJETO A*
            if len(v_books) > 1:
                path_lats, path_lons = [], []
                for i in range(len(v_books) - 1):
                    start_b = v_books[i]
                    end_b = v_books[i+1]
                    
                    _, path_cells = get_a_star_path(start_b["cell_id"], end_b["cell_id"], start_b["end_time"])
                    
                    if path_cells:
                        for cell in path_cells:
                            coords = h3_cell_to_lat_lng(cell)
                            if coords:
                                path_lats.append(coords[0])
                                path_lons.append(coords[1])
                        path_lats.append(None)
                        path_lons.append(None)
                
                if path_lats:
                    route_traces.append(MapClass(
                        lat=path_lats, lon=path_lons,
                        mode='lines', line=dict(width=5, color=v_color),
                        hoverinfo='skip', showlegend=False
                    ))

            # 2. PINOS DOS PACIENTES (CORRIGIDO: Sem 'line' dentro de marker)
            lats = [b["lat"] for b in v_books]
            lons = [b["lng"] for b in v_books]
            texts = [f"<b>{i+1}. {b['client']}</b><br>Horário: {b['start_time'].strftime('%H:%M')}" for i, b in enumerate(v_books)]
            
            route_traces.append(MapClass(
                lat=lats, lon=lons,
                mode='markers+text',
                marker=dict(size=18, color=v_color),
                text=[f"{i+1}" for i in range(len(v_books))],
                textposition="top center",
                hoverinfo='text', hovertext=texts,
                name=f"Pacientes V{v}"
            ))

        fig_routes = go.Figure(route_traces)
        layout_cfg = dict(map_style="open-street-map", map=dict(center=dict(lat=-22.92, lon=-43.30), zoom=11)) if hasattr(go, "Scattermap") else dict(mapbox_style="open-street-map", mapbox=dict(center=dict(lat=-22.92, lon=-43.30), zoom=11))
        fig_routes.update_layout(margin=dict(l=0, r=0, t=0, b=0), height=580, legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01), **layout_cfg)
        st.plotly_chart(fig_routes, use_container_width=True, key="routes_map")