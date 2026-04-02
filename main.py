#!/usr/bin/env python3
"""
Raspberry Pi Car GPS Tracker & Web Interface
============================================
Prerequisites:
1. Ensure gpsd is running and receiving data:
   sudo apt-get install gpsd gpsd-clients python3-gps
2. Install Flask for the web server:
   pip3 install flask

Run the script:
   python3 pi_gps_tracker.py

Access the web interface on any device on your network:
   http://<raspberry-pi-ip>:5000
"""

import sqlite3
import threading
import time
import json
import socket
from datetime import datetime
from flask import Flask, jsonify, render_template_string

# --- CONFIGURATION ---
DB_FILE = 'gps_history.db'
WEB_PORT = 5000
LOG_INTERVAL_SECONDS = 5  # Minimum time between logging points to DB

app = Flask(__name__)

# --- DATABASE SETUP ---
def init_db():
    """Initializes the SQLite database and creates the necessary tables."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS location_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            latitude REAL,
            longitude REAL,
            speed REAL,     -- Speed in m/s
            altitude REAL,  -- Altitude in meters
            track REAL      -- Course over ground, degrees from true north
        )
    ''')
    conn.commit()
    conn.close()

def log_to_db(lat, lon, speed, alt, track):
    """Inserts a new GPS point into the database."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO location_history (latitude, longitude, speed, altitude, track)
        VALUES (?, ?, ?, ?, ?)
    ''', (lat, lon, speed, alt, track))
    conn.commit()
    conn.close()

# --- BACKGROUND GPS LOGGER THREAD ---
def gps_logger_thread():
    """Runs continuously in the background, listening to gpsd via socket and logging data."""
    print("Starting GPS logging thread...")
    
    while True:
        try:
            # Connect directly to the local gpsd socket (bypassing buggy gps library)
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect(('127.0.0.1', 2947))
            
            # Command gpsd to start sending JSON location data
            sock.sendall(b'?WATCH={"enable":true,"json":true}\n')
            
            # Create a file-like object to read stream lines easily
            f = sock.makefile('r', encoding='utf-8')
            last_log_time = 0
            
            for line in f:
                try:
                    report = json.loads(line)
                    
                    # TPV (Time-Position-Velocity) contains the location data
                    if report.get('class') == 'TPV':
                        lat = report.get('lat')
                        lon = report.get('lon')
                        
                        if lat is not None and lon is not None:
                            current_time = time.time()
                            
                            # Only log every LOG_INTERVAL_SECONDS to prevent database bloat
                            if current_time - last_log_time >= LOG_INTERVAL_SECONDS:
                                speed = report.get('speed', 0.0) # m/s
                                alt = report.get('alt', 0.0)
                                track = report.get('track', 0.0)
                                
                                log_to_db(lat, lon, speed, alt, track)
                                last_log_time = current_time
                except json.JSONDecodeError:
                    continue # Skip incomplete/malformed chunks
                    
        except Exception as e:
            print(f"GPS Connection Error: {e}. Retrying in 5 seconds...")
            time.sleep(5)
        finally:
            try:
                sock.close()
            except:
                pass

# --- WEB SERVER ROUTES ---
@app.route('/')
def index():
    """Serves the main HTML page with the Leaflet map."""
    return render_template_string(HTML_TEMPLATE)

@app.route('/api/current')
def get_current_location():
    """Returns the most recent GPS point from the database."""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('''
        SELECT timestamp, latitude, longitude, speed, altitude, track 
        FROM location_history 
        ORDER BY id DESC LIMIT 1
    ''')
    row = cursor.fetchone()
    conn.close()
    
    if row:
        return jsonify(dict(row))
    return jsonify({"error": "No GPS data available yet"}), 404

@app.route('/api/history')
def get_history():
    """Returns the last 2000 points for drawing the route history."""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    # Limit to last 2000 points to keep browser performance smooth
    cursor.execute('''
        SELECT timestamp, latitude, longitude, speed 
        FROM location_history 
        ORDER BY id DESC LIMIT 2000
    ''')
    rows = cursor.fetchall()
    conn.close()
    
    # Reverse to chronological order for the polyline
    history = [dict(row) for row in reversed(rows)]
    return jsonify(history)

# --- HTML/JS TEMPLATE ---
# Embedded here to keep everything in a single, easily deployable file.
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Pi Car GPS Tracker</title>
    
    <!-- Leaflet CSS -->
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    
    <style>
        body, html { margin: 0; padding: 0; height: 100%; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }
        #app-container { display: flex; flex-direction: column; height: 100vh; }
        
        #header { 
            background: #1e293b; 
            color: white; 
            padding: 15px 20px; 
            display: flex; 
            justify-content: space-between; 
            align-items: center; 
            box-shadow: 0 2px 10px rgba(0,0,0,0.2);
            z-index: 1000;
        }
        
        #header h1 { margin: 0; font-size: 1.2rem; }
        
        #stats { display: flex; gap: 20px; font-size: 0.9rem; }
        .stat-box { background: #334155; padding: 5px 10px; border-radius: 6px; }
        .stat-value { font-weight: bold; color: #38bdf8; }
        
        #map { flex-grow: 1; width: 100%; }
        
        /* Custom map marker pulse effect */
        .gps-marker {
            background-color: #3b82f6;
            width: 16px;
            height: 16px;
            border-radius: 50%;
            border: 3px solid white;
            box-shadow: 0 0 10px rgba(0,0,0,0.5);
        }
    </style>
</head>
<body>
    <div id="app-container">
        <div id="header">
            <h1>📍 Pi Dash Tracker</h1>
            <div id="stats">
                <div class="stat-box">Speed: <span id="speed" class="stat-value">0</span> mph</div>
                <div class="stat-box">Alt: <span id="alt" class="stat-value">0</span> ft</div>
                <div class="stat-box">Updated: <span id="time" class="stat-value">Waiting...</span></div>
            </div>
        </div>
        <div id="map"></div>
    </div>

    <!-- Leaflet JS -->
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    
    <script>
        // Initialize Map
        const map = L.map('map').setView([0, 0], 2);
        
        // Add OpenStreetMap tiles
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            attribution: '&copy; OpenStreetMap contributors',
            maxZoom: 19
        }).addTo(map);

        // Map Layers
        let currentMarker = null;
        let routePolyline = L.polyline([], { color: '#ef4444', weight: 4, opacity: 0.8 }).addTo(map);
        let isFirstLoad = true;

        // Custom icon for current location
        const markerIcon = L.divIcon({
            className: 'gps-marker',
            iconSize: [16, 16],
            iconAnchor: [8, 8]
        });

        // Convert m/s to mph
        const msToMph = (ms) => (ms * 2.23694).toFixed(1);
        // Convert meters to feet
        const mToFt = (m) => (m * 3.28084).toFixed(0);

        async function loadHistory() {
            try {
                const res = await fetch('/api/history');
                const history = await res.json();
                
                if (history.length > 0) {
                    const latlngs = history.map(pt => [pt.latitude, pt.longitude]);
                    routePolyline.setLatLngs(latlngs);
                    
                    if (isFirstLoad) {
                        map.fitBounds(routePolyline.getBounds(), { padding: [50, 50] });
                    }
                }
            } catch (err) {
                console.error("Failed to load history:", err);
            }
        }

        async function updateCurrentLocation() {
            try {
                const res = await fetch('/api/current');
                if (!res.ok) return;
                
                const data = await res.json();
                const latlng = [data.latitude, data.longitude];
                
                // Update UI Stats
                document.getElementById('speed').innerText = msToMph(data.speed);
                document.getElementById('alt').innerText = mToFt(data.altitude);
                document.getElementById('time').innerText = new Date(data.timestamp).toLocaleTimeString();

                // Update Marker
                if (!currentMarker) {
                    currentMarker = L.marker(latlng, { icon: markerIcon }).addTo(map);
                    if (isFirstLoad && routePolyline.isEmpty()) {
                        map.setView(latlng, 15);
                    }
                } else {
                    currentMarker.setLatLng(latlng);
                }

                // Add to polyline
                routePolyline.addLatLng(latlng);
                
                // Optionally keep map centered on car if it's moving
                // map.panTo(latlng);

                isFirstLoad = false;
            } catch (err) {
                console.error("Failed to fetch current location:", err);
            }
        }

        // Boot up
        loadHistory().then(() => {
            updateCurrentLocation();
            // Poll for new location every 3 seconds
            setInterval(updateCurrentLocation, 3000);
            // Refresh history occasionally in case of long drives to sync up db pruning etc
            setInterval(loadHistory, 60000); 
        });
    </script>
</body>
</html>
"""

if __name__ == '__main__':
    # 1. Initialize DB
    init_db()
    
    # 2. Start GPS Background Thread
    # Set as daemon so it automatically dies when the web server is stopped
    gps_thread = threading.Thread(target=gps_logger_thread, daemon=True)
    gps_thread.start()
    
    # 3. Start Web Server
    print(f"Starting Web Server on port {WEB_PORT}...")
    # host='0.0.0.0' allows access from other devices on the same network
    app.run(host='0.0.0.0', port=WEB_PORT, debug=False, threaded=True)