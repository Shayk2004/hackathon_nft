from flask import Flask, request, render_template, redirect, url_for, flash, session, jsonify
import json
import os
import uuid
import math
import re
import logging
import subprocess
import base58
from solders.keypair import Keypair
from cryptography.fernet import Fernet
import requests

logging.basicConfig(
    filename='log.txt',
    level=logging.INFO,  # Use INFO for your custom markers; errors will be logged as ERROR
    format='%(asctime)s:%(levelname)s:%(message)s'
)

app = Flask(__name__)
app.secret_key = os.urandom(24)  # Required for session handling
app.config['UPLOAD_FOLDER'] = 'uploads'

USERS_FILE = "users.json"
POINTS_FILE = "points.json"

# Generate encryption key (Run once and store securely)
encryption_key = Fernet.generate_key()
cipher_suite = Fernet(encryption_key)

# Function to save users to JSON file
def save_users(users):
    """Saves the current users dictionary to users.json file."""
    with open(USERS_FILE, "w") as f:
        json.dump(users, f, indent=4)

# Function to load users from JSON file
def load_users():
    """Loads users from users.json. If missing or invalid, resets it."""
    if not os.path.exists(USERS_FILE):
        save_users({})
    try:
        with open(USERS_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        print("⚠️ Warning: users.json was empty or corrupted. Resetting it.")
        save_users({})
        return {}

# Load users into memory
users = load_users()

# Function to create a Solana wallet
def create_solana_wallet():
    keypair = Keypair()
    secret_key_b58 = base58.b58encode(bytes(keypair)).decode("utf-8")
    return {
        "public_key": str(keypair.pubkey()),
        "secret_key": secret_key_b58
    }

# --- Population Density Functions Integration Start ---

def get_osm_administrative_areas(lat, lon):
    """
    Queries OpenStreetMap (Overpass API) to retrieve all administrative areas
    that contain the given latitude and longitude and have a Wikidata tag.
    """
    overpass_url = "http://overpass-api.de/api/interpreter"
    overpass_query = f"""
    [out:json];
    is_in({lat},{lon})->.a;
    area.a["boundary"="administrative"]["wikidata"]["name"];
    out body;
    """
    try:
        response = requests.get(overpass_url, params={"data": overpass_query})
        response.raise_for_status()
        data = response.json()
        areas = []
        if "elements" in data and len(data["elements"]) > 0:
            for element in data["elements"]:
                if "tags" in element and "wikidata" in element["tags"]:
                    try:
                        admin_level = int(element["tags"].get("admin_level", 99))
                    except ValueError:
                        admin_level = 99
                    areas.append({
                        "name": element["tags"].get("name"),
                        "wikidata_id": element["tags"].get("wikidata"),
                        "admin_level": admin_level
                    })
            # Sort areas in descending order of admin_level (smaller area first)
            sorted_areas = sorted(areas, key=lambda x: x["admin_level"], reverse=True)
            return sorted_areas
        return []
    except requests.exceptions.RequestException as e:
        logging.error(f"OSM request error: {str(e)}")
        return []

def get_population_and_area_wikidata(wikidata_id):
    """
    Queries Wikidata to fetch the population and area for a given Wikidata ID.
    Uses a UNION query to try retrieving the population from the truthy property or statement.
    """
    wikidata_url = "https://query.wikidata.org/sparql"
    sparql_query = f"""
    SELECT ?population ?area WHERE {{
      {{
        wd:{wikidata_id} wdt:P1082 ?population .
      }}
      UNION
      {{
        wd:{wikidata_id} p:P1082 ?popStatement .
        ?popStatement ps:P1082 ?population .
      }}
      OPTIONAL {{ wd:{wikidata_id} wdt:P2046 ?area . }}
    }} LIMIT 1
    """
    headers = {"Accept": "application/json"}
    try:
        response = requests.get(wikidata_url, params={"query": sparql_query, "format": "json"}, headers=headers)
        response.raise_for_status()
        data = response.json()
        if ("results" in data and "bindings" in data["results"] and 
            len(data["results"]["bindings"]) > 0):
            result = data["results"]["bindings"][0]
            try:
                population = int(result["population"]["value"])
            except (KeyError, ValueError):
                return {"error": "Population data is not valid."}
            area = None
            if "area" in result:
                try:
                    area = float(result["area"]["value"])
                except ValueError:
                    area = None
            if area:
                density = population / area
                return {
                    "population": population,
                    "area_km2": area,
                    "population_density": density
                }
            else:
                return {
                    "population": population,
                    "area_km2": "Unknown",
                    "population_density": "Cannot calculate (no area data)"
                }
        return {"error": "No population or area data found for this Wikidata ID."}
    except requests.exceptions.RequestException as e:
        return {"error": f"Request failed: {str(e)}"}

def search_alternative_wikidata_ids(area_name):
    """
    Searches Wikidata for alternative entities matching the given area name.
    """
    search_url = "https://www.wikidata.org/w/api.php"
    params = {
        "action": "wbsearchentities",
        "search": area_name,
        "language": "en",
        "format": "json"
    }
    try:
        response = requests.get(search_url, params=params)
        response.raise_for_status()
        data = response.json()
        results = data.get("search", [])
        return [r["id"] for r in results]
    except requests.exceptions.RequestException as e:
        logging.error(f"Wikidata search error: {str(e)}")
        return []

def get_population_density(lat, lon):
    """
    Combines OSM and Wikidata methods to compute population density for a given location.
    """
    areas = get_osm_administrative_areas(lat, lon)
    if not areas:
        return {"error": "No administrative areas found for this location."}
    for area in areas:
        wikidata_id = area["wikidata_id"]
        logging.info(f"Trying area '{area['name']}' with Wikidata ID: {wikidata_id}")
        population_data = get_population_and_area_wikidata(wikidata_id)
        if "error" not in population_data:
            return {
                "location": area["name"],
                "admin_level": area["admin_level"],
                "population": population_data["population"],
                "area_km2": population_data["area_km2"],
                "population_density": population_data["population_density"]
            }
        else:
            logging.info(f"Area '{area['name']}' (Wikidata ID: {wikidata_id}) did not return valid data.")
            alternatives = search_alternative_wikidata_ids(area["name"])
            for alt_id in alternatives:
                if alt_id == wikidata_id:
                    continue
                logging.info(f"Trying alternative Wikidata ID: {alt_id} for area '{area['name']}'")
                alt_population_data = get_population_and_area_wikidata(alt_id)
                if "error" not in alt_population_data:
                    return {
                        "location": area["name"],
                        "admin_level": area["admin_level"],
                        "population": alt_population_data["population"],
                        "area_km2": alt_population_data["area_km2"],
                        "population_density": alt_population_data["population_density"],
                        "wikidata_id": alt_id
                    }
            logging.info(f"No alternative Wikidata IDs for '{area['name']}' returned valid data. Trying a larger area...")
    return {"error": "Could not fetch population or area data from Wikidata for any administrative area."}

def determine_rarity(population_density):
    """
    Determines rarity based on population density.
    Rarity rules:
      - 0 <= density < 25: rarity 1
      - 25 <= density < 100: rarity 2
      - 100 <= density < 500: rarity 3
      - density >= 500: rarity 4
    """
    if not isinstance(population_density, (int, float)):
        return None
    if population_density < 25:
        return 1
    elif population_density < 100:
        return 2
    elif population_density < 500:
        return 3
    else:
        return 4

# --- Population Density Functions Integration End ---

# Signup Route - Now includes Solana wallet generation and adds an empty rarity list.
@app.route('/signup', methods=['POST'])
def signup():
    try:
        logging.info("here 1: Entered signup route")
        username = request.form.get('username')
        password = request.form.get('password')
        if not username or not password:
            logging.info("here 2: Missing username or password")
            return jsonify({"success": False, "message": "Username and password are required."}), 400
        if username in users:
            logging.info("here 3: Username already exists")
            return jsonify({"success": False, "message": "Username already exists."}), 400
        logging.info("here 4: Generating Solana wallet")
        wallet = create_solana_wallet()
        logging.info("here 5: Encrypting private key")
        encrypted_private_key = cipher_suite.encrypt(wallet["secret_key"].encode()).decode()
        logging.info("here 6: Storing user info with wallet details")
        users[username] = {
            "password": password,
            "solana_public_key": wallet["public_key"],
            "solana_private_key": encrypted_private_key,
            'nft_names': [],
            'descriptions': [],
            'image_paths': [],
            'latitude': [],
            'longitude': [],
            'rarity': []  # New array to store rarity values for each NFT.
        }
        logging.info("here 7: Saving users")
        save_users(users)
        logging.info("here 8: Signup successful")
        return jsonify({
            "success": True,
            "message": "Signup successful.",
            "solana_public_key": wallet["public_key"],
            "solana_private_key": wallet["secret_key"]
        }), 201
    except Exception as e:
        logging.error(f"An error occurred: {str(e)}")
        return jsonify({"success": False, "message": "An internal error occurred."}), 500

# Login Route
@app.route('/login', methods=['POST'])
def login():
    username = request.form.get('username')
    password = request.form.get('password')
    if not username or not password:
        return jsonify({"success": False, "message": "Username and password are required."}), 400
    if users.get(username) == password:
        return jsonify({"success": True, 
                        "message": "Login successful.",
                        "solana_public_key": users[username]["solana_public_key"]}), 200
    else:
        return jsonify({"success": False, "message": "Invalid username or password."}), 401

# Logout Route
@app.route('/logout')
def logout():
    session.pop('username', None)
    flash("Logged out successfully!", "info")
    return redirect(url_for('login'))

# Function to save points to JSON file
def save_points(points):
    """Saves points to points.json file."""
    with open(POINTS_FILE, "w") as f:
        json.dump(points, f, indent=4)

# Function to load points from JSON file
def load_points():
    """Loads points from points.json. If missing or invalid, resets it."""
    if not os.path.exists(POINTS_FILE):
        save_points([])
    try:
        with open(POINTS_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        print("⚠️ Warning: points.json was empty or corrupted. Resetting it.")
        save_points([])
        return []

# Load points into memory
points = load_points()

# Haversine formula to calculate distance between two lat/lon points (miles)
def haversine(lat1, lon1, lat2, lon2):
    """Calculates the distance (in miles) between two lat/lon points."""
    R = 3958.8  # Earth's radius in miles
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = math.sin(dlat / 2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2)**2
    return 2 * R * math.asin(math.sqrt(a))

# API endpoint to add a new point
@app.route('/add_point', methods=['POST'])
def add_point():
    global points
    data = request.get_json()
    lat, lon = data.get("latitude"), data.get("longitude")
    if lat is None or lon is None:
        return jsonify({"success": False, "message": "Latitude and longitude are required."}), 400
    if points is None:
        points = []
    new_point = {"uuid": str(uuid.uuid4()), "latitude": lat, "longitude": lon}
    points.append(new_point)
    save_points(points)
    return jsonify({"success": True, "message": "Point added successfully.", "point": new_point}), 201

# API endpoint to find nearby points within 100 miles
@app.route('/nearby_points', methods=['GET'])
def nearby_points():
    lat = request.args.get("latitude", type=float)
    lon = request.args.get("longitude", type=float)
    if lat is None or lon is None:
        return jsonify({"success": False, "message": "Latitude and longitude are required."}), 400
    if points is None:
        return jsonify({"success": True, "nearby_points": [], "count": 0})
    nearby = [point for point in points if haversine(lat, lon, point["latitude"], point["longitude"]) <= 100]
    return jsonify({"success": True, "nearby_points": nearby, "count": len(nearby)})

# Retrieve Wallet Details (Securely)
@app.route('/get_wallet', methods=['POST'])
def get_wallet():
    username = request.form.get('username')
    if not username or username not in users:
        return jsonify({"success": False, "message": "User not found."}), 404
    decrypted_key = cipher_suite.decrypt(users[username]["solana_private_key"].encode()).decode()
    return jsonify({
        "solana_public_key": users[username]["solana_public_key"],
        "solana_private_key": decrypted_key
    })

@app.route('/get_belonging', methods=['GET'])
def get_belongings():
    username = request.form.get('username')
    return jsonify({
        "nft_names": users[username]['nft_names'],
        "descriptions": users[username]['descriptions'],
        "image_paths": users[username]['image_paths']
    })

# Updated upload endpoint integrating population density and rarity calculation.
@app.route('/upload', methods=['POST'])
def upload():
    try:
        logging.info("upload route: Received upload request")
        mint_address = None
        username = request.form.get('username')
        nft_name = request.form.get('name')
        description = request.form.get('description')
        lat = request.form.get('latitude')
        long_val = request.form.get('longitude')
        logging.info("upload route: Retrieved form fields for username, nft_name, description, lat, and long")
        if 'image' not in request.files:
            logging.info("upload route: No image file provided")
            return jsonify({"success": False, "message": "No image file provided"}), 400
        file = request.files['image']
        if file.filename == '':
            logging.info("upload route: No selected image file")
            return jsonify({"success": False, "message": "No selected image file"}), 400
        upload_folder = app.config['UPLOAD_FOLDER']
        logging.info("upload route: Filename secured and upload folder set")
        if not os.path.exists(upload_folder):
            logging.info("upload route: Upload folder not found; creating directory")
            os.makedirs(upload_folder)
        file_path = os.path.join(upload_folder, file.filename)
        file.save(file_path)
        logging.info("upload route: File saved at %s", file_path)
        image = file_path
        recipient_pubkey = users[username]["solana_public_key"]
        logging.info("upload route: Retrieved recipient public key from user data")
        if not username or not nft_name or not description or not image or not recipient_pubkey:
            logging.info("upload route: Missing required fields for minting NFT")
            return jsonify({"success": False, "message": "error minting NFT."}), 400
        mint_command = ['node', 'mintNft.js', file_path, nft_name, 'MYNFT', description]
        logging.info("upload route: Running mint command: %s", mint_command)
        try:
            mint_output = subprocess.check_output(mint_command, stderr=subprocess.STDOUT)
            mint_output_str = mint_output.decode()
            logging.info("upload route: Mint Output: %s", mint_output_str)
            match = re.search(r'NFT created with address:\s*(\S+)', mint_output_str)
            if match:
                mint_address = match.group(1)
                logging.info("upload route: Mint address extracted: %s", mint_address)
            else:
                logging.info("upload route: Minting succeeded but mint address not found in output")
                flash("Minting succeeded but mint address not found in output.")
        except subprocess.CalledProcessError as e:
            logging.error("upload route: Error minting NFT: %s", e.output.decode())
            flash("Error minting NFT. Check the server logs.")
            return jsonify({"success": False, "message": "error minting NFT."}), 400
        send_command = ['node', 'sendNft.js', mint_address, recipient_pubkey]
        logging.info("upload route: Running send command: %s", send_command)
        try:
            send_output = subprocess.check_output(send_command, stderr=subprocess.STDOUT)
            logging.info("upload route: Send Output: %s", send_output.decode())
        except subprocess.CalledProcessError as e:
            logging.error("upload route: Error sending NFT: %s", e.output.decode())
            flash("Error sending NFT. Check the server logs.")
            return jsonify({"success": False, "message": "error sending NFT."}), 400
        flash("NFT minted and sent successfully!")
        if mint_address:
            flash(f"NFT Mint Address: {mint_address}")
        # Calculate population density and determine rarity
        try:
            lat_val = float(lat)
            lon_val = float(long_val)
            pop_density_data = get_population_density(lat_val, lon_val)
            rarity = None
            if "error" not in pop_density_data and isinstance(pop_density_data.get("population_density"), (int, float)):
                density = pop_density_data["population_density"]
                rarity = determine_rarity(density)
                logging.info("upload route: Calculated rarity: %s based on density: %s", rarity, density)
            else:
                logging.info("upload route: Population density data error: %s", pop_density_data.get("error"))
        except Exception as ex:
            logging.error("upload route: Error calculating population density: %s", str(ex))
            rarity = None
        # Update user data with new NFT info including rarity.
        logging.info("upload route: Updating user data with NFT details")
        users[username]['nft_names'].append(nft_name)
        users[username]['descriptions'].append(description)
        users[username]['image_paths'].append(image)
        users[username]['latitude'].append(lat)
        users[username]['longitude'].append(long_val)
        users[username]['rarity'].append(rarity)
        save_users(users)
        logging.info("upload route: User data saved successfully")
        return jsonify({"success": True, "message": "NFT minted and sent successfully!"}), 200
    except Exception as e:
        logging.error("upload route: Unhandled exception: %s", str(e))
        return jsonify({"success": False, "message": "An internal error occurred."}), 500

@app.route('/samaira')
def samaira():
    return "Hi Samaira!"

if __name__ == '__main__':
    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        os.makedirs(app.config['UPLOAD_FOLDER'])
    app.run(debug=True)
