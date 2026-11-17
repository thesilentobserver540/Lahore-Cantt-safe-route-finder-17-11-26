import math
from pathlib import Path
import folium
import networkx as nx
import osmnx as ox
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium
from pyproj import Transformer
from shapely.geometry import LineString, Point

st.set_page_config(page_title='Lahore Routing Map', layout='wide')
HOME = [31.5204, 74.3587]
KEY = 'routing_map'

# ===== Helper Functions (from routing_core) =====
def edge_line(graph, u, v, data):
    return data.get('geometry') or LineString([
        (graph.nodes[u]['x'], graph.nodes[u]['y']),
        (graph.nodes[v]['x'], graph.nodes[v]['y'])])

def offset_line(coords, distance_m=8):
    if len(coords) < 2:
        return coords
    offset_coords = []
    for i in range(len(coords) - 1):
        lat1, lon1 = coords[i]
        lat2, lon2 = coords[i + 1]
        d_lon = lon2 - lon1
        d_lat = lat2 - lat1
        length = math.sqrt(d_lon**2 + d_lat**2)
        if length == 0:
            offset_coords.append((lat1, lon1))
            continue
        offset_lat = (-d_lon / length) * (distance_m / 111320)
        offset_lon = (d_lat / length) * (distance_m / (111320 * math.cos(math.radians(lat1))))
        offset_coords.append((lat1 + offset_lat, lon1 + offset_lon))
    offset_coords.append(coords[-1])
    return offset_coords


def snap_click(graph, lat, lon, tolerance=50):
    if graph.number_of_edges() == 0:
        raise ValueError('Active graph mein koi road edge nahi bachi.')
    forward = Transformer.from_crs('EPSG:4326', graph.graph['crs'], always_xy=True)
    inverse = Transformer.from_crs(graph.graph['crs'], 'EPSG:4326', always_xy=True)
    x, y = forward.transform(lon, lat)
    (u, v, key), distance = ox.distance.nearest_edges(graph, X=x, Y=y, return_dist=True)
    if float(distance) > tolerance:
        raise ValueError(f'Active road {distance:.0f} m door hai; limit {tolerance} m hai.')
    line = edge_line(graph, u, v, graph.edges[u, v, key])
    snapped = line.interpolate(line.project(Point(x, y)))
    snap_lon, snap_lat = inverse.transform(snapped.x, snapped.y)
    node = min((u, v), key=lambda n: math.hypot(
        graph.nodes[n]['x'] - snapped.x, graph.nodes[n]['y'] - snapped.y))
    return {'raw': [lat, lon], 'snapped': [snap_lat, snap_lon],
            'node': node, 'edge': (u, v, key), 'distance_m': float(distance)}

def route_coordinates(graph, route, weight):
    inverse = Transformer.from_crs(graph.graph['crs'], 'EPSG:4326', always_xy=True)
    result, length = [], 0.0
    for u, v in zip(route[:-1], route[1:]):
        data = min(graph[u][v].values(), key=lambda d: d[weight])
        coords = list(edge_line(graph, u, v, data).coords)
        start = graph.nodes[u]
        if math.hypot(coords[-1][0]-start['x'], coords[-1][1]-start['y']) < math.hypot(coords[0][0]-start['x'], coords[0][1]-start['y']):
            coords.reverse()
        result.extend((lat, lon) for lon, lat in (inverse.transform(x, y) for x, y in coords))
        length += float(data['length'])
    return result, length

# ===== Main App =====
DEFAULTS = dict(map_center=HOME, map_zoom=14, pick_mode=None,
                start_point=None, end_point=None, pending_click=None,
                last_seen_click=None, notice=None)
for key, value in DEFAULTS.items():
    if key not in st.session_state:
        st.session_state[key] = value
ss = st.session_state

@st.cache_resource(show_spinner=False)
def load_real_graph():
    try:
        graph = ox.load_graphml("lahore_cantt.graphml")
        return ox.project_graph(graph)
    except Exception as e:
        st.error(f"Graph file load nahi hui: {e}")
        return None


def capture_map_event():
    data = ss.get(KEY) or {}
    click = data.get('last_clicked')
    if click is not None:
        fingerprint = (float(click['lat']), float(click['lng']))
        if fingerprint != ss.last_seen_click:
            ss.last_seen_click = fingerprint
            if ss.pick_mode:
                ss.pending_click = (ss.pick_mode, *fingerprint)

def arm(mode):
    click = (ss.get(KEY) or {}).get('last_clicked')
    if click:
        ss.last_seen_click = (float(click['lat']), float(click['lng']))
    ss.pending_click = None
    ss.pick_mode = mode
    ss.notice = None
def clear_points():
    ss.start_point = None
    ss.end_point = None
    ss.pick_mode = None
    ss.pending_click = None
    ss.notice = ("info", "Start and End points cleared.")

st.markdown("""
<style>
iframe[title="streamlit_folium.st_folium"] {
    min-height: 650px;
}
</style>
""", unsafe_allow_html=True)

st.title("🚚 Lahore Delivery Route Planner")


st.caption(
    "Plan delivery routes across Lahore using road conditions "
    "such as traffic, road closures and flooding."
)

base = load_real_graph()
if base is None:
    st.stop()

map_style = st.sidebar.radio(
    "🗺️ Map Style",
    ["OpenStreetMap", "Satellite"]
)
st.sidebar.header("🛣️ Road Conditions")

road_conditions = [
    'Road Construction',
    'Flooded Road',
    'Road Closure',
    'Traffic Congestion'
]

for condition in road_conditions:
    st.sidebar.checkbox(condition)


graph = base

if ss.pending_click:
    mode, lat, lon = ss.pending_click
    ss.pending_click = None
    try:
        ss[f'{mode}_point'] = snap_click(graph, lat, lon)
        ss.notice = ('success', f'{mode.title()} select ho gaya.')
        ss.pick_mode = 'end' if mode == 'start' else None
    except ValueError as exc:
        ss.notice = ('error', str(exc))

st.sidebar.header("📦 Delivery Points")
place = st.sidebar.text_input("🔎 Search place")
if st.sidebar.button("Add place as point", use_container_width=True) and place:
    try:
        lat, lon = ox.geocode(f"{place}, Lahore, Pakistan")
        if ss.start_point is None:
            mode = 'start'
        elif ss.end_point is None:
            mode = 'end'
        else:
            ss.start_point = None
            ss.end_point = None
            mode = 'start'
        ss[f'{mode}_point'] = snap_click(graph, lat, lon)
        ss.notice = ('success', f'{mode.title()} select ho gaya.')
    except ValueError as exc:
        ss.notice = ('error', str(exc))
    except Exception:
        ss.notice = ('error', 'Jagah nahi mili ya internet ka masla hai.')
st.sidebar.button(
    "🔄 Clear Selected Points",
    on_click=clear_points,
    use_container_width=True
)
left, right = st.sidebar.columns(2)
left.button('📍 Set Start', on_click=arm, args=('start',), use_container_width=True)
right.button('🎯 Set End', on_click=arm, args=('end',), use_container_width=True)
for mode in ('start', 'end'):
    point = ss[f'{mode}_point']
    st.sidebar.write(f"**{mode.title()}:** {point['node'] if point else 'Not selected'}")
if ss.pick_mode:
    st.sidebar.warning(
        f"Now click on the map to select your {ss.pick_mode} point."
    )
else:
    st.sidebar.info(
        "How to use:\n"
        "1. Click Set Start.\n"
        "2. Select your starting point on the map.\n"
        "3. Click Set End.\n"
        "4. Select your destination on the map."
    )
if ss.notice:
    level, text = ss.notice
    getattr(st, level)(text)

m = folium.Map(location=HOME, zoom_start=14, tiles=None, max_zoom=18, min_zoom=12, max_bounds=True, zoom_snap=1)

folium.TileLayer(
    tiles='https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}',
    attr='Google Satellite',
    name='Satellite',
    show=(map_style == "Satellite"),
    keep_buffer=4
).add_to(m)

folium.TileLayer(
    tiles='OpenStreetMap',
    name='OpenStreetMap',
    show=(map_style == "OpenStreetMap")
).add_to(m)

overlay = folium.FeatureGroup(name='Routing overlays')


for mode, color in [('start', 'green'), ('end', 'red')]:
    point = ss[f'{mode}_point']
    if point:
        folium.Marker(point['snapped'], tooltip=f'{mode.title()} (road snap)',
                      bubbling_mouse_events=True,
                      icon=folium.Icon(color=color)).add_to(overlay)
        folium.CircleMarker(point['raw'], radius=4, color=color, fill=True,
                            interactive=False).add_to(overlay)

if ss.start_point and ss.end_point:
    start = ss.start_point['node']
    end = ss.end_point['node']

    if start == end:
        st.info('Choose two different road locations.')

    else:
        with st.spinner("Calculating route..."):
            try:
                route = nx.shortest_path(
                    graph,
                    start,
                    end,
                    weight='length'
                )

                coords, metres = route_coordinates(
                    graph,
                    route,
                    'length'
                )

                folium.PolyLine(
                    coords,
                    color='blue',
                    weight=5,
                    opacity=0.9,
                    interactive=False
                ).add_to(overlay)

                st.success(
                    f"Shortest route: {metres / 1000:.2f} km"
                )

            except (nx.NetworkXNoPath, nx.NodeNotFound):
                st.warning(
                    'No route found. Choose different locations.'
                )
        


map_data = st_folium(
    m,
    feature_group_to_add=overlay,
    width=1200,
    height=650,
    key=KEY,
    returned_objects=["last_clicked"],
    on_change=capture_map_event
)
if map_data and map_data.get("center") and map_data.get("zoom"):
    ss.map_center = [map_data["center"]["lat"], map_data["center"]["lng"]]
    ss.map_zoom = map_data["zoom"]


            
        
                