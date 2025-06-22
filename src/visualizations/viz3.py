from dash import html, dcc
import pandas as pd
import geopandas as gpd
import plotly.graph_objects as go
import json
from shapely.geometry import Point
import numpy as np
import os
from data_manager import data_manager

_cached_figure = None
_cached_data = None

def crime_hover_template(crime_type):
    return (
        f"<b>{crime_type}</b><br>" +
        "District: %{customdata[1]}<br>" +
        "PDQ: %{customdata[0]}<br>" +
        "Crime Count: %{customdata[2]}<extra></extra>"
    )

def base_hover_template():
    return "District: %{location}<extra></extra>"

def _get_montreal_json_path():
    """Trouve le chemin correct vers le fichier montreal.json"""
    possible_paths = [
        "src/data/montreal.json",
        "data/montreal.json",
        "../data/montreal.json",
        os.path.join(os.path.dirname(__file__), "data", "montreal.json"),
        os.path.join(os.path.dirname(__file__), "..", "data", "montreal.json")
    ]
    
    for path in possible_paths:
        if os.path.exists(path):
            return path
    
    # Chemin par défaut
    return "src/data/montreal.json"

def load_and_process_data():
    """Load and process data once, then cache it - OPTIMISÉ avec data_manager"""
    global _cached_data
    
    if _cached_data is not None:
        return _cached_data
    
    print("Loading data for the first time using optimized data manager...")
    
    CRIME_TRANSLATION = {
        "Vol De Véhicule À Moteur": "Motor Vehicle Theft",
        "Méfait": "Mischief",
        "Vol Dans / Sur Véhicule À Moteur": "Theft From/In Motor Vehicle",
        "Introduction": "Breaking And Entering",
        "Vols Qualifiés": "Robbery",
        "Infractions Entrainant La Mort": "Offences Causing Death"
    }
    
    # Chargement du fichier GeoJSON
    montreal_json_path = _get_montreal_json_path()
    with open(montreal_json_path) as f:
        montreal_geo = json.load(f)

    gdf_districts = gpd.read_file(montreal_json_path)
    
    # OPTIMISATION: Utilisation du gestionnaire de données centralisé au lieu de pd.read_csv
    df = data_manager.get_data_for_viz3()

    df.rename(columns={
        "CATEGORIE": "CrimeType",
        "LONGITUDE": "Longitude",
        "LATITUDE": "Latitude",
        "PDQ": "PDQ"
    }, inplace=True)

    df = df.dropna(subset=["Longitude", "Latitude"]).copy()
    df["CrimeType"] = df["CrimeType"].str.strip().str.lower().str.title()
    df["CrimeType"] = df["CrimeType"].map(CRIME_TRANSLATION).fillna(df["CrimeType"])
    
    df["PDQ"] = df["PDQ"].astype(str)

    df = df[(df["Latitude"] > 45.40) & (df["Latitude"] < 45.70) &
            (df["Longitude"] > -73.95) & (df["Longitude"] < -73.45)]

    df["geometry"] = df.apply(lambda row: Point(row["Longitude"], row["Latitude"]), axis=1)
    gdf_crimes = gpd.GeoDataFrame(df, geometry="geometry", crs=gdf_districts.crs)

    gdf_joined = gpd.sjoin(gdf_crimes, gdf_districts, how="left", predicate="within")
    gdf_joined["District"] = gdf_joined["NOM"]
    
    _cached_data = {
        'montreal_geo': montreal_geo,
        'gdf_joined': gdf_joined
    }
    
    print(f"Data processed and cached: {len(gdf_joined)} crime records")
    return _cached_data

def reduce_points_strategy_1(gdf_joined, max_points_per_district=3):
    """Strategy 1: Top crimes by type per district (most representative)"""
    reduced_data = []
    
    for district in gdf_joined['District'].dropna().unique():
        district_data = gdf_joined[gdf_joined['District'] == district]
        crime_counts = district_data['CrimeType'].value_counts()
        

        for crime_type in crime_counts.head(max_points_per_district).index:
            crime_subset = district_data[district_data['CrimeType'] == crime_type]
            representative = crime_subset.iloc[len(crime_subset)//2].copy()
            representative['crime_count'] = crime_counts[crime_type]
            reduced_data.append(representative)
    
    return pd.DataFrame(reduced_data)

def reduce_points_strategy_3(gdf_joined, max_points_per_district=2):
    """Strategy 3: Crime hotspots - most frequent locations"""
    reduced_data = []
    
    for district in gdf_joined['District'].dropna().unique():
        district_data = gdf_joined[gdf_joined['District'] == district]
        district_data = district_data.copy()
        district_data['lat_rounded'] = district_data['Latitude'].round(3)
        district_data['lon_rounded'] = district_data['Longitude'].round(3)
        district_data['location_key'] = district_data['lat_rounded'].astype(str) + "_" + district_data['lon_rounded'].astype(str)
        
        location_counts = district_data['location_key'].value_counts()
        
        for location_key in location_counts.head(max_points_per_district).index:
            location_data = district_data[district_data['location_key'] == location_key]
            most_common_crime = location_data['CrimeType'].mode().iloc[0]
            representative = location_data[location_data['CrimeType'] == most_common_crime].iloc[0].copy()
            representative['crime_count'] = location_counts[location_key]
            reduced_data.append(representative)
    
    return pd.DataFrame(reduced_data)

def create_map_figure(strategy="strategy_1", max_points=3):
    """Create the map figure using cached data with reduced points"""
    print(f"Creating map with {strategy}, max {max_points} points per district...")
    
    data = load_and_process_data()
    montreal_geo = data['montreal_geo']
    gdf_joined = data['gdf_joined']
    
    if strategy == "strategy_1":
        reduced_gdf = reduce_points_strategy_1(gdf_joined, max_points)
        title_suffix = f"Top {max_points} Crime Types per District"
    else: 
        reduced_gdf = reduce_points_strategy_3(gdf_joined, max_points)
        title_suffix = f"Crime Hotspots ({max_points} per district)"
    
    print(f"Reduced from {len(gdf_joined)} to {len(reduced_gdf)} points")
    
    COLOR_MAP = {
        "Motor Vehicle Theft": "#626ff5",
        "Mischief": "#E74C3C",
        "Theft From/In Motor Vehicle": "#1ABC9C",
        "Breaking And Entering": "#9B59B6",
        "Robbery": "#F39C12",
        "Offences Causing Death": "#00BCD4"
    }

    fig = go.Figure()
    neighborhoods = [feature["properties"]["NOM"] for feature in montreal_geo["features"]]
    z_vals = [1] * len(neighborhoods)

    fig.add_choroplethmapbox(
        geojson=montreal_geo,
        locations=neighborhoods,
        z=z_vals,
        featureidkey="properties.NOM",
        colorscale=[[0, "lightgrey"], [1, "lightgrey"]],
        showscale=False,
        marker_opacity=0.2,
        marker_line=dict(width=1.5, color="black"),
        hovertemplate=base_hover_template()
    )

    for crime_type, color in COLOR_MAP.items():
        crime_data = reduced_gdf[reduced_gdf["CrimeType"] == crime_type].copy()
        
        if not crime_data.empty:
            marker_sizes = []
            for _, row in crime_data.iterrows():
                base_size = 8
                if 'crime_count' in row and pd.notna(row['crime_count']):
                    size = min(20, base_size + (row['crime_count'] / 10))
                else:
                    size = base_size
                marker_sizes.append(size)

            fig.add_trace(go.Scattermapbox(
                lat=crime_data["Latitude"],
                lon=crime_data["Longitude"],
                mode="markers",
                marker=dict(
                    size=marker_sizes,
                    color=color,
                    opacity=0.8
                ),
                name=crime_type,
                customdata=crime_data[["PDQ", "District", "crime_count"]],
                hovertemplate=crime_hover_template(crime_type)
            ))

    # Enhanced layout with larger map
    fig.update_layout(
        mapbox_style="white-bg",
        mapbox_zoom=8.5,  # Increased zoom for better detail
        mapbox_center={"lat": 45.55, "lon": -73.6},
        mapbox_bounds={"west": -74.1, "east": -73.3, "south": 45.35, "north": 45.75},  
        height=700,  # Increased from 500 to 700
        margin=dict(t=60, r=10, l=10, b=10),  # Reduced margins
        legend=dict(
            orientation="v",
            yanchor="top",
            y=1,
            xanchor="left",
            x=1.02,
            bgcolor="rgba(255,255,255,0.8)",
            bordercolor="Black",
            borderwidth=1
        ),
        title=dict(
            text=f"Montreal Crime Map - {title_suffix}",
            x=0.5,
            font=dict(size=18),  # Larger title
            pad=dict(t=20)
        )
    )
    
    return fig

def layout():
    return html.Div([
        # Header section with reduced padding
        html.Div([
            html.H2("Montreal Crime Data Explorer", 
                   style={'textAlign': 'center', 'marginBottom': '10px', 'color': '#2c3e50'}),
            html.P("Interactive map showing crime distribution across Montreal districts",
                   style={'textAlign': 'center', 'marginBottom': '20px', 'color': '#7f8c8d'})
        ]),
        
        # Controls section - more compact
        html.Div([
            html.Div([
                html.Label("Visualization Strategy:", style={'fontWeight': 'bold', 'marginBottom': '5px'}),
                dcc.Dropdown(
                    id='strategy-dropdown',
                    options=[
                        {'label': 'Top crimes per district', 'value': 'strategy_1'},
                        {'label': 'Crime hotspots', 'value': 'strategy_3'}
                    ],
                    value='strategy_1',
                    style={'width': '100%'}
                )
            ], style={'width': '48%', 'display': 'inline-block', 'verticalAlign': 'top'}),
            
            html.Div([
                html.Label("Maximum points per district:", style={'fontWeight': 'bold', 'marginBottom': '5px'}),
                dcc.Slider(
                    id='max-points-slider',
                    min=1, max=5, step=1, value=3,
                    marks={i: str(i) for i in range(1, 6)},
                    tooltip={"placement": "bottom", "always_visible": True}
                )
            ], style={'width': '48%', 'float': 'right', 'display': 'inline-block'})
            
        ], style={'margin': '20px 0', 'padding': '15px', 'backgroundColor': '#f8f9fa', 'borderRadius': '5px'}),
        
        # Map section - full width with minimal margins
        html.Div([
            dcc.Graph(
                id='reduced-crime-map', 
                figure=create_map_figure(),
                style={'height': '700px'}  # Explicit height
            )
        ], style={'width': '100%', 'margin': '0'})
        
    ], style={
        'fontFamily': 'Arial, sans-serif',
        'maxWidth': '1400px',  # Increased max width
        'margin': '0 auto',
        'padding': '20px'
    })


def clear_cache():
    """Vide le cache local ET le cache du data_manager"""
    global _cached_figure, _cached_data
    _cached_figure = None
    _cached_data = None
    # Vider aussi le cache du data_manager si nécessaire
    data_manager.clear_cache()


from dash import callback, Input, Output

@callback(
    Output('reduced-crime-map', 'figure'),
    [Input('strategy-dropdown', 'value'),
     Input('max-points-slider', 'value')]
)
def update_map(strategy, max_points):
    return create_map_figure(strategy, max_points)

