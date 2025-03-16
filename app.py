from flask import Flask, render_template, jsonify, request, session, redirect, url_for
from flask_socketio import SocketIO, emit
import folium
import random
import time
import threading
import sqlite3
import json
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = 'your-secret-key-here'
socketio = SocketIO(app)

def init_db():
    db_path = 'users.db'
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users 
                 (username TEXT PRIMARY KEY, password TEXT, avatar TEXT, 
                  team TEXT, claims INTEGER DEFAULT 0, steals INTEGER DEFAULT 0, 
                  user_points INTEGER DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS rivalries 
                 (player1 TEXT, player2 TEXT, steal_count INTEGER DEFAULT 0, 
                  PRIMARY KEY (player1, player2))''')
    c.execute('''CREATE TABLE IF NOT EXISTS zones 
                 (zone_id TEXT PRIMARY KEY, owner TEXT, team TEXT, points INTEGER DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS game_state 
                 (key TEXT PRIMARY KEY, value TEXT)''')
    
    c.execute("PRAGMA table_info(users)")
    columns = [col[1] for col in c.fetchall()]
    if 'user_points' not in columns:
        print("Adding user_points column to users table")
        c.execute('ALTER TABLE users ADD COLUMN user_points INTEGER DEFAULT 0')
    
    for zone_id in zones.keys():
        c.execute('INSERT OR IGNORE INTO zones (zone_id, owner, team, points) VALUES (?, ?, ?, ?)',
                  (zone_id, None, None, 0))
    
    c.execute('INSERT OR IGNORE INTO game_state (key, value) VALUES (?, ?)', ('weather', 'Clear'))
    c.execute('INSERT OR IGNORE INTO game_state (key, value) VALUES (?, ?)', ('red_upgraded', '0'))
    c.execute('INSERT OR IGNORE INTO game_state (key, value) VALUES (?, ?)', ('blue_upgraded', '0'))
    
    c.execute('SELECT zone_id, owner, team, points FROM zones')
    rows = c.fetchall()
    print("Loading zones from database:")
    for row in rows:
        zone_id, owner, team, points = row
        print(f"Zone {zone_id}: owner={owner}, team={team}, points={points}")
        if zone_id in zones:
            zones[zone_id]["owner"] = owner
            zones[zone_id]["team"] = team
            zones[zone_id]["points"] = points

    c.execute('SELECT key, value FROM game_state WHERE key = "weather"')
    weather_row = c.fetchone()
    global current_weather
    current_weather = weather_row[1] if weather_row else 'Clear'

    c.execute('SELECT key, value FROM game_state WHERE key IN ("red_upgraded", "blue_upgraded")')
    for key, value in c.fetchall():
        if key == "red_upgraded":
            global red_upgraded
            red_upgraded = int(value)
        elif key == "blue_upgraded":
            global blue_upgraded
            blue_upgraded = int(value)

    conn.commit()
    conn.close()
    print("Database initialized")

map_center = [34.4283, -119.7629]
zones = {
    "zone_1": {"coords": [[34.435, -119.83], [34.435, -119.82], [34.44, -119.82], [34.44, -119.83]], "owner": None, "team": None, "points": 0, "value": 1, "capturing": None, "capture_start": None, "boost": False, "defense": 0, "powerup": None, "stealth": False},
    "zone_2": {"coords": [[34.413, -119.85], [34.413, -119.84], [34.42, -119.84], [34.42, -119.85]], "owner": None, "team": None, "points": 0, "value": 1, "capturing": None, "capture_start": None, "boost": False, "defense": 0, "powerup": None, "stealth": False},
    "zone_3": {"coords": [[34.44, -119.81], [34.44, -119.80], [34.45, -119.80], [34.45, -119.81]], "owner": None, "team": None, "points": 0, "value": 1, "capturing": None, "capture_start": None, "boost": False, "defense": 0, "powerup": None, "stealth": False},
    "zone_4": {"coords": [[34.43, -119.89], [34.43, -119.88], [34.44, -119.88], [34.44, -119.89]], "owner": None, "team": None, "points": 0, "value": 1, "capturing": None, "capture_start": None, "boost": False, "defense": 0, "powerup": None, "stealth": False},
    "zone_5": {"coords": [[34.420, -119.70], [34.420, -119.69], [34.425, -119.69], [34.425, -119.70]], "owner": None, "team": None, "points": 0, "value": 2, "capturing": None, "capture_start": None, "boost": False, "defense": 0, "powerup": None, "stealth": False, "golden": True},
    "zone_6": {"coords": [[34.409, -119.69], [34.409, -119.68], [34.414, -119.68], [34.414, -119.69]], "owner": None, "team": None, "points": 0, "value": 2, "capturing": None, "capture_start": None, "boost": False, "defense": 0, "powerup": None, "stealth": False},
    "zone_7": {"coords": [[34.425, -119.67], [34.425, -119.66], [34.43, -119.66], [34.43, -119.67]], "owner": None, "team": None, "points": 0, "value": 2, "capturing": None, "capture_start": None, "boost": False, "defense": 0, "powerup": None, "stealth": False},
    "zone_8": {"coords": [[34.415, -119.71], [34.415, -119.70], [34.42, -119.70], [34.42, -119.71]], "owner": None, "team": None, "points": 0, "value": 1, "capturing": None, "capture_start": None, "boost": False, "defense": 0, "powerup": None, "stealth": False}
}

adjacent_zones = {
    "zone_1": ["zone_3"],
    "zone_2": ["zone_4"],
    "zone_3": ["zone_1"],
    "zone_4": ["zone_2"],
    "zone_5": ["zone_6", "zone_7", "zone_8"],
    "zone_6": ["zone_5", "zone_7", "zone_8"],
    "zone_7": ["zone_5", "zone_6", "zone_8"],
    "zone_8": ["zone_5", "zone_6", "zone_7"]
}

event_active = False
event_type = None
powerup_types = ["speed", "shield"]
current_weather = 'Clear'
red_upgraded = 0
blue_upgraded = 0
rare_zone = None
hazard_zones = []

init_db()

def update_points():
    global red_upgraded, blue_upgraded
    print("Starting update_points thread")
    while True:
        team_scores = {"Red": 0, "Blue": 0}
        team_adjacency_bonus = {"Red": 0, "Blue": 0}
        seen_pairs = set()

        for zone_id, zone in zones.items():
            if zone["owner"]:
                base_points = zone["value"]
                multiplier = 2 if event_active and event_type == "double" else 1
                if zone.get("golden", False):
                    multiplier *= 2
                if zone_id == rare_zone:
                    multiplier *= 3
                points = base_points * multiplier
                if current_weather == "Fog" and blue_upgraded:
                    points *= 2 if zone["team"] == "Blue" else 1
                if current_weather == "Sunny":
                    points += 1
                if zone_id in hazard_zones:
                    points -= 2
                    if points < 0:
                        points = 0
                zone["points"] += points
                if zone["team"]:
                    team_scores[zone["team"]] += points

                    for adj_zone_id in adjacent_zones.get(zone_id, []):
                        adj_zone = zones.get(adj_zone_id)
                        if adj_zone["team"] == zone["team"]:
                            pair = tuple(sorted([zone_id, adj_zone_id]))
                            if pair not in seen_pairs:
                                seen_pairs.add(pair)
                                team_adjacency_bonus[zone["team"]] += 1

        for team in team_scores:
            if team_adjacency_bonus[team] > 0:
                bonus_points = int(team_scores[team] * 0.5 * team_adjacency_bonus[team])
                team_scores[team] += bonus_points
                socketio.emit('team_bonus', {
                    'team': 'Red Surfers' if team == 'Red' else 'Blue Anglers',
                    'bonus': bonus_points,
                    'pairs': team_adjacency_bonus[team]
                })

        red_zones = sum(1 for z in zones.values() if z["team"] == "Red")
        blue_zones = sum(1 for z in zones.values() if z["team"] == "Blue")
        if red_zones >= 3 and not red_upgraded:
            red_upgraded = 1
            conn = sqlite3.connect('users.db')
            c = conn.cursor()
            c.execute('UPDATE game_state SET value = "1" WHERE key = "red_upgraded"')
            conn.commit()
            conn.close()
            socketio.emit('ticker_update', {'message': "Red Surfers upgraded: Weather bonuses unlocked!"})
        if blue_zones >= 3 and not blue_upgraded:
            blue_upgraded = 1
            conn = sqlite3.connect('users.db')
            c = conn.cursor()
            c.execute('UPDATE game_state SET value = "1" WHERE key = "blue_upgraded"')
            conn.commit()
            conn.close()
            socketio.emit('ticker_update', {'message': "Blue Anglers upgraded: Weather bonuses unlocked!"})

        leader = 'Red Surfers' if team_scores["Red"] > team_scores["Blue"] else 'Blue Anglers' if team_scores["Blue"] > team_scores["Red"] else 'Tied'
        socketio.emit('ticker_update', {
            'red_score': team_scores["Red"],
            'blue_score': team_scores["Blue"],
            'leader': leader,
            'message': f"Red Surfers: {team_scores['Red']} | Blue Anglers: {team_scores['Blue']} | {leader} in the lead! | Weather: {current_weather}"
        })
        print(f"Ticker update: Red={team_scores['Red']}, Blue={team_scores['Blue']}")
        time.sleep(10)

def turf_event():
    global event_active, event_type, rare_zone
    print("Starting turf_event thread")
    time.sleep(10)
    while True:
        time.sleep(300)
        event = random.choice(["double", "reset", "rare"])
        event_type = event
        if event == "double":
            event_active = True
            print("Double Points event started")
            socketio.emit('event_update', {'type': 'double', 'message': 'Double Points for 1 minute!'})
            time.sleep(60)
            event_active = False
            socketio.emit('event_update', {'type': 'double_end', 'message': 'Double Points ended'})
        elif event == "reset":
            print("Resetting zones")
            for zone in zones.values():
                zone["owner"] = None
                zone["team"] = None
                zone["capturing"] = None
                zone["capture_start"] = None
                zone["defense"] = 0
                zone["powerup"] = None
                zone["points"] = 0
                zone["stealth"] = False
            conn = sqlite3.connect('users.db')
            c = conn.cursor()
            c.execute('UPDATE zones SET owner = NULL, team = NULL, points = 0')
            conn.commit()
            conn.close()
            socketio.emit('event_update', {'type': 'reset', 'message': 'Turf Reset - All zones up for grabs!'})
        elif event == "rare":
            rare_zone = random.choice(list(zones.keys()))
            print(f"Rare Zone event: {rare_zone}")
            socketio.emit('event_update', {'type': 'rare', 'message': f"Rare Zone {rare_zone} - 3x points for 2 minutes!'"})
            time.sleep(120)
            rare_zone = None
            socketio.emit('event_update', {'type': 'rare_end', 'message': 'Rare Zone event ended'})
        socketio.emit('update_map', zones)

def spawn_boosts():
    print("Starting spawn_boosts thread")
    time.sleep(10)
    while True:
        sleep_time = 60 if current_weather == "Sunny" else 120
        time.sleep(sleep_time)
        zone_id = random.choice(list(zones.keys()))
        zones[zone_id]["boost"] = True
        socketio.emit('update_map', zones)
        time.sleep(30)
        zones[zone_id]["boost"] = False
        socketio.emit('update_map', zones)

def update_defense():
    print("Starting update_defense thread")
    time.sleep(10)
    while True:
        for zone in zones.values():
            if zone["owner"] and zone["defense"] < 5:
                zone["defense"] += 1 if current_weather != "Storm" or not blue_upgraded else 2
        socketio.emit('update_map', zones)
        time.sleep(60)

def spawn_powerups():
    print("Starting spawn_powerups thread")
    time.sleep(10)
    while True:
        time.sleep(90 if current_weather != "Fog" else 135)
        zone_id = random.choice(list(zones.keys()))
        if not zones[zone_id]["powerup"]:
            zones[zone_id]["powerup"] = random.choice(powerup_types)
            socketio.emit('update_map', zones)
            time.sleep(30)
            if zones[zone_id]["powerup"]:
                zones[zone_id]["powerup"] = None
                socketio.emit('update_map', zones)

def weather_event():
    global current_weather, hazard_zones
    print("Starting weather_event thread")
    time.sleep(10)
    while True:
        time.sleep(180)
        current_weather = random.choice(["Storm", "Fog", "Sunny"])
        conn = sqlite3.connect('users.db')
        c = conn.cursor()
        c.execute('UPDATE game_state SET value = ? WHERE key = "weather"', (current_weather,))
        conn.commit()
        conn.close()
        if current_weather == "Storm":
            hazard_zones = random.sample(list(zones.keys()), 2)
            print(f"Storm: Hazard zones {hazard_zones}")
            socketio.emit('weather_update', {'weather': current_weather, 'hazard_zones': hazard_zones})
            socketio.emit('ticker_update', {'message': f"Storm hits! Hazard zones: {', '.join(hazard_zones)}"})
        else:
            hazard_zones = []
            socketio.emit('weather_update', {'weather': current_weather, 'hazard_zones': []})
            socketio.emit('ticker_update', {'message': f"Weather Alert: {current_weather} conditions now in effect!"})

threading.Thread(target=update_points, daemon=True).start()
threading.Thread(target=turf_event, daemon=True).start()
threading.Thread(target=spawn_boosts, daemon=True).start()
threading.Thread(target=update_defense, daemon=True).start()
threading.Thread(target=spawn_powerups, daemon=True).start()
threading.Thread(target=weather_event, daemon=True).start()

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        conn = sqlite3.connect('users.db')
        c = conn.cursor()
        c.execute('SELECT password, avatar, team FROM users WHERE username = ?', (username,))
        result = c.fetchone()
        conn.close()
        if result and check_password_hash(result[0], password):
            session['username'] = username
            session['avatar'] = result[1]
            session['team'] = result[2]
            session['powerup'] = None
            session['powerup_expiry'] = 0
            session['double_claim'] = False
            session['stealth_expiry'] = 0
            return redirect(url_for('show_map'))
        return render_template('login.html', error="Invalid credentials")
    return render_template('login.html', error=None)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        avatar = request.form.get('avatar')
        team = request.form.get('team')
        if not all([username, password, avatar, team]):
            return render_template('register.html', error="All fields are required")
        conn = sqlite3.connect('users.db')
        c = conn.cursor()
        try:
            hashed_pass = generate_password_hash(password)
            c.execute('INSERT INTO users (username, password, avatar, team, claims, steals, user_points) VALUES (?, ?, ?, ?, ?, ?, ?)',
                      (username, hashed_pass, avatar, team, 0, 0, 0))
            conn.commit()
            session['username'] = username
            session['avatar'] = avatar
            session['team'] = team
            session['powerup'] = None
            session['powerup_expiry'] = 0
            session['double_claim'] = False
            session['stealth_expiry'] = 0
            return redirect(url_for('show_map'))
        except sqlite3.IntegrityError:
            conn.close()
            return render_template('register.html', error="Username already taken")
        finally:
            conn.close()
    return render_template('register.html', error=None)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/')
def show_map():
    if 'username' not in session:
        return redirect(url_for('login'))
    m = folium.Map(location=map_center, zoom_start=13)
    zone_data = {zone_id: f"{zone_id}" for zone_id in zones.keys()}
    
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    try:
        c.execute('SELECT username, user_points FROM users WHERE username = ?', (session['username'],))
        user_points = c.fetchone()[1]
    except sqlite3.OperationalError:
        print("user_points column missing, defaulting to 0")
        user_points = 0
    c.execute('SELECT username, avatar FROM users')
    users = {row[0]: row[1] for row in c.fetchall()}
    conn.close()

    # Debug: Print all zone coordinates and add polygons
    print("Rendering zones:")
    for zone_id, data in zones.items():
        print(f"Zone {zone_id} coords: {data['coords']}")
        team_color = 'red' if data["team"] == 'Red' else 'blue' if data["team"] == 'Blue' else 'gray'
        if data["stealth"] and data["owner"] != session['username']:
            team_color = 'gray'
        color = 'gold' if data.get("golden", False) else 'purple' if zone_id == rare_zone else team_color
        print(f"Adding zone {zone_id} with color {color}")
        
        try:
            polygon = folium.Polygon(
                locations=data["coords"],
                color=color,
                fill=True,
                fill_color=color,
                fill_opacity=0.5 + (data["defense"] * 0.1),
                weight=2,
                popup=f"{zone_id} ({'Golden' if data.get('golden', False) else 'Normal'})"
            )
            polygon.add_to(m)
            # Explicitly set zone_id as a custom attribute
            polygon.options['__zone_id'] = zone_id
            print(f"Successfully added {zone_id} to map")
        except Exception as e:
            print(f"Error adding zone {zone_id}: {str(e)}")

    # Add a test polygon to verify rendering
    test_polygon = folium.Polygon(
        locations=[[34.428, -119.763], [34.428, -119.762], [34.429, -119.762], [34.429, -119.763]],
        color='green',
        fill=True,
        fill_color='green',
        fill_opacity=0.5,
        weight=2,
        popup="Test Polygon"
    )
    test_polygon.add_to(m)
    test_polygon.options['__zone_id'] = 'test_zone'
    print("Added test polygon at map center")

    print(f"Rendering map for {session['username']} with {user_points} points")
    map_html = m._repr_html_()
    print(f"Map HTML length: {len(map_html)}")
    print(f"Map HTML snippet: {map_html[:500]}")  # First 500 chars for inspection
    
    return render_template("map.html", map=map_html, username=session['username'], 
                          avatar=session['avatar'], team=session['team'], zone_data=zone_data, 
                          zones=zones, users=users, user_points=user_points)

@app.route('/upgrade', methods=['POST'])
def upgrade():
    if 'username' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    upgrade_type = request.json.get('upgrade')
    costs = {'double_claim': 50, 'stealth_mode': 75, 'defense_boost': 100}
    if upgrade_type not in costs:
        return jsonify({'error': 'Invalid upgrade'}), 400
    
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    try:
        c.execute('SELECT user_points FROM users WHERE username = ?', (session['username'],))
        points = c.fetchone()[0]
    except sqlite3.OperationalError:
        print("user_points column missing in upgrade, defaulting to 0")
        points = 0
    
    if points < costs[upgrade_type]:
        conn.close()
        return jsonify({'error': 'Not enough points'}), 400
    
    c.execute('UPDATE users SET user_points = user_points - ? WHERE username = ?', 
              (costs[upgrade_type], session['username']))
    conn.commit()
    conn.close()

    if upgrade_type == 'double_claim':
        session['double_claim'] = True
    elif upgrade_type == 'stealth_mode':
        session['stealth_expiry'] = time.time() + 120
    elif upgrade_type == 'defense_boost':
        session['defense_boost'] = True
    
    print(f"{session['username']} bought {upgrade_type} for {costs[upgrade_type]} points")
    return jsonify({'success': True, 'points': points - costs[upgrade_type]})

@socketio.on('collect_powerup')
def handle_powerup_collection(data):
    player = data.get('player')
    zone_id = data["zone"]
    if zones[zone_id]["powerup"] and session.get('username') == player:
        if not session.get('powerup'):
            session['powerup'] = zones[zone_id]["powerup"]
            session['powerup_expiry'] = time.time() + 30
            zones[zone_id]["powerup"] = None
            emit('powerup_collected', {'player': player, 'powerup': session['powerup']}, to=request.sid)
            socketio.emit('update_map', zones)

@socketio.on('start_capture')
def handle_capture(data):
    player = data.get('player')
    if not player:
        print("Capture failed: No player provided")
        return
    zone_id = data["zone"]
    current_time = time.time()

    zone = zones[zone_id]
    if zone["owner"] and session.get('powerup') == "shield" and session.get('powerup_expiry', 0) > current_time and zone["team"] == session['team']:
        print(f"Capture blocked: Zone {zone_id} shielded")
        return
    
    defense_bonus = zone["defense"] * 2 if zone["owner"] else 0
    base_time = 5 if zone["boost"] else (10 if not zone["owner"] else 20 + defense_bonus)
    if zone.get("golden", False):
        base_time *= 2
    if current_weather == "Storm":
        base_time *= 1.5
    if session.get('powerup') == "speed" and session.get('powerup_expiry', 0) > current_time:
        base_time /= 2
    if current_weather == "Sunny" and red_upgraded and session['team'] == "Red":
        base_time *= 0.5
    
    print(f"Starting capture for Zone {zone_id} by {player} - Base time: {base_time}s")
    
    if zone["capturing"] and zone["capturing"] != player:
        zone["capture_start"] = current_time
        emit('capture_update', {'zone': zone_id, 'player': player, 'time_left': base_time, 'contested': True, 'base_time': base_time}, broadcast=True)
    elif not zone["capturing"]:
        zone["capturing"] = player
        zone["capture_start"] = current_time
        emit('capture_update', {'zone': zone_id, 'player': player, 'time_left': base_time, 'contested': False, 'base_time': base_time}, broadcast=True)
    else:
        print(f"Capture ignored: Zone {zone_id} already capturing by {player}")
        return
    
    start_time = zone["capture_start"]
    for _ in range(int(base_time * 2)):
        if zone["capturing"] != player:
            print(f"Capture interrupted: Zone {zone_id} by {zone['capturing']}")
            emit('capture_progress', {'zone': zone_id, 'time_left': 0, 'base_time': base_time}, broadcast=True)
            return
        elapsed = time.time() - start_time
        time_left = base_time - elapsed
        print(f"Capture progress: Zone {zone_id}, Time left: {time_left:.1f}s")
        emit('capture_progress', {'zone': zone_id, 'player': player, 'time_left': max(0, time_left), 'base_time': base_time}, broadcast=True)
        if time_left <= 0:
            break
        time.sleep(0.5)
    
    if zone["capturing"] == player:
        conn = sqlite3.connect('users.db')
        c = conn.cursor()
        c.execute('SELECT team FROM users WHERE username = ?', (player,))
        team = c.fetchone()[0]
        current_owner = zone["owner"]
        zone["owner"] = player
        zone["team"] = team
        zone["capturing"] = None
        zone["capture_start"] = None
        zone["defense"] = 0
        if session.get('defense_boost', False):
            zone["defense"] = 2
            session['defense_boost'] = False
        if zone["boost"]:
            zone["boost"] = False
        if session.get('stealth_expiry', 0) > current_time:
            zone["stealth"] = True
            threading.Thread(target=clear_stealth, args=(zone_id,), daemon=True).start()

        points_earned = 10 if not current_owner else 20
        c.execute('UPDATE users SET claims = claims + 1, user_points = user_points + ? WHERE username = ?', 
                  (points_earned, player))
        if current_owner and current_owner != player:
            c.execute('UPDATE users SET steals = steals + 1 WHERE username = ?', (player,))
            c.execute('INSERT OR REPLACE INTO rivalries (player1, player2, steal_count) VALUES (?, ?, COALESCE((SELECT steal_count FROM rivalries WHERE player1 = ? AND player2 = ?) + 1, 1))',
                      (player, current_owner, player, current_owner))
        c.execute('UPDATE zones SET owner = ?, team = ?, points = ? WHERE zone_id = ?',
                  (zone["owner"], zone["team"], zone["points"], zone_id))
        conn.commit()
        conn.close()

        print(f"Capture complete: Zone {zone_id} by {player}")
        emit('capture_progress', {'zone': zone_id, 'time_left': 0, 'base_time': base_time}, broadcast=True)
        socketio.emit('update_map', zones)
        if session.get('powerup') and session.get('powerup_expiry', 0) > current_time:
            session['powerup'] = None
            session['powerup_expiry'] = 0
            emit('powerup_collected', {'player': player, 'powerup': None}, to=request.sid)
        if session.get('double_claim', False):
            zone["points"] += zone["value"] * 2
            session['double_claim'] = False
            socketio.emit('ticker_update', {'message': f"{player} doubled points on {zone_id}!"})
        socketio.emit('ticker_update', {'message': f"{player} captured {zone_id} for {team} Surfers/Anglers!"})

def clear_stealth(zone_id):
    time.sleep(120)
    zones[zone_id]["stealth"] = False
    socketio.emit('update_map', zones)

@app.route('/leaderboard')
def leaderboard():
    try:
        conn = sqlite3.connect('users.db')
        c = conn.cursor()
        scores = {}
        team_scores = {"Red": 0, "Blue": 0}
        for zone_id, zone in zones.items():
            if zone["owner"]:
                points = zone["points"]
                scores[zone["owner"]] = scores.get(zone["owner"], 0) + points
                if zone["team"]:
                    team_scores[zone["team"]] += points
        c.execute('SELECT username, team, claims, steals, user_points FROM users')
        users_data = c.fetchall()
        c.execute('SELECT player1, player2, steal_count FROM rivalries')
        rivalries = c.fetchall()
        conn.close()
        stats = [{'username': row[0], 'team': 'Red Surfers' if row[1] == 'Red' else 'Blue Anglers', 
                  'score': scores.get(row[0], 0), 'claims': row[2], 'steals': row[3], 'points': row[4]} for row in users_data]
        team_scores_display = {"Red Surfers": team_scores["Red"], "Blue Anglers": team_scores["Blue"]}
        rivalry_data = [{'player1': r[0], 'player2': r[1], 'steal_count': r[2]} for r in rivalries]
        return jsonify({'stats': stats, 'rivalries': rivalry_data, 'team_scores': team_scores_display})
    except Exception as e:
        print(f"Leaderboard error: {str(e)}")
        return jsonify({'error': str(e)}), 500

if __name__ == "__main__":
    socketio.run(app, debug=True, host='0.0.0.0', port=5000)
