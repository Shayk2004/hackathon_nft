from flask import Flask, request, render_template, redirect, url_for, flash, session, jsonify
import json
import os
import uuid
import math
from solders.keypair import Keypair
import base58
from cryptography.fernet import Fernet
import subprocess
import re
import logging
import random
import threading

logging.basicConfig(
    filename='log.txt',
    level=logging.INFO,
    format='%(asctime)s:%(levelname)s:%(message)s'
)

app = Flask(__name__)
app.secret_key = os.urandom(24)
app.config['UPLOAD_FOLDER'] = 'uploads'

USERS_FILE = "users.json" # I love you json databases
NFTS_FILE = "nfts.json"

encryption_key = Fernet.generate_key()
cipher_suite = Fernet(encryption_key)

jobs = {}

def process_nft_job(qid, file_path, nft_name, description, recipient_pubkey, username, lat, long):
    try:
        jobs[qid] = {"status": "running", "message": "Minting started", "mint_address": None}

        mint_command = ['/home/ubuntu/.nvm/versions/node/v22.14.0/bin/node', 'mintNft.js', file_path, nft_name, 'MYNFT', description]
        logging.info("Thread %s: Running mint command: %s", qid, mint_command)
        mint_output = subprocess.check_output(mint_command, stderr=subprocess.STDOUT)
        mint_output_str = mint_output.decode()
        logging.info("Thread %s: Mint Output: %s", qid, mint_output_str)
        
        match = re.search(r'NFT created with address:\s*(\S+)', mint_output_str)
        if match:
            mint_address = match.group(1)
            jobs[qid]["mint_address"] = mint_address
            logging.info("Thread %s: Mint address extracted: %s", qid, mint_address)
        else:
            jobs[qid]["status"] = "error"
            jobs[qid]["message"] = "Minting succeeded but mint address not found in output"
            return

        # Send command.
        send_command = ['/home/ubuntu/.nvm/versions/node/v22.14.0/bin/node', 'sendNft.js', mint_address, recipient_pubkey]
        logging.info("Thread %s: Running send command: %s", qid, send_command)
        send_output = subprocess.check_output(send_command, stderr=subprocess.STDOUT)
        logging.info("Thread %s: Send Output: %s", qid, send_output.decode())

        # Update job status to completed.
        jobs[qid]["status"] = "completed"
        jobs[qid]["message"] = "NFT minted and sent successfully!"

        nft_id = uuid.uuid5()
        nfts[nft_id] = {"id": nft_id, "name": nft_name, "description": description, "latitude": lat, "longitude": long, "image_path": file_path, "rarity": 0}
        save_nfts(nfts)
        
        users[username]['nft_names'].append(nft_name)
        users[username]['descriptions'].append(description)
        users[username]['image_paths'].append(file_path)
        users[username]['latitude'].append(lat)
        users[username]['longitude'].append(long)
        users[username]["nft_ids"].append(nft_id)
        save_users(users)

        
        logging.info("Thread %s: User data updated successfully", qid)

    except subprocess.CalledProcessError as e:
        error_msg = e.output.decode()
        logging.error("Thread %s: Error in NFT processing: %s", qid, error_msg)
        jobs[qid] = {"status": "error", "message": error_msg}

    except Exception as e:
        logging.error("Thread %s: Unhandled exception: %s", qid, str(e))
        jobs[qid] = {"status": "error", "message": str(e)}


@app.route('/upload', methods=['POST'])
def upload():
    try:
        logging.info("upload route: Received upload request")
        mint_address = None  # To store the mint address if available

        # Retrieve fields from the form.
        username = request.form.get('username')
        nft_name = request.form.get('name')
        description = request.form.get('description')
        lat = request.form.get('latitude')
        long = request.form.get('longitude')
        logging.info("upload route: Retrieved form fields for username, nft_name, description, lat, and long")

        if 'image' not in request.files:
            logging.info("upload route: No image file provided")
            return jsonify({"success": False, "message": "No image file provided"}), 400

        file = request.files['image']
        if file.filename == '':
            logging.info("upload route: No selected image file")
            return jsonify({"success": False, "message": "No selected image file"}), 400

        # Secure the filename and define the upload path.
        upload_folder = app.config['UPLOAD_FOLDER']
        logging.info("upload route: Filename secured and upload folder set")

        # Create the directory if it doesn't exist.
        if not os.path.exists(upload_folder):
            logging.info("upload route: Upload folder not found; creating directory")
            os.makedirs(upload_folder)

        file_path = os.path.join(upload_folder, file.filename)
        file.save(file_path)
        logging.info("upload route: File saved at %s", file_path)

        image = file_path
        # Retrieve recipient public key from stored user info.
        recipient_pubkey = users[username]["solana_public_key"]
        logging.info("upload route: Retrieved recipient public key from user data")

        if not username or not nft_name or not description or not image or not recipient_pubkey:
            logging.info("upload route: Missing required fields for minting NFT")
            return jsonify({"success": False, "message": "error minting NFT."}), 400

        # Generate a unique job ID (qid) for this task.
        qid = str(uuid.uuid4())
        # Initialize the job status.
        jobs[qid] = {"status": "pending", "message": "Job queued", "mint_address": None}

        # Start a new thread to process the NFT creation and sending.
        thread = threading.Thread(
            target=process_nft_job,
            args=(qid, file_path, nft_name, description, recipient_pubkey, username, lat, long)
        )
        thread.start()
        logging.info("upload route: Job %s started in background thread", qid)

        # Return the qid immediately so the client can poll for status.
        return jsonify({"success": True, "qid": qid}), 200

    except Exception as e:
        logging.error("upload route: Unhandled exception: %s", str(e))
        return jsonify({"success": False, "message": "An internal error occurred."}), 500

@app.route('/job_status/<qid>', methods=['GET'])
def job_status(qid):
    # Return the status of a job given its qid.
    if qid in jobs:
        return jsonify({"qid": qid, "status": jobs[qid]})
    else:
        return jsonify({"error": "Job ID not found"}), 404

def save_users(users):
    """Saves the current users dictionary to users.json file."""
    with open(USERS_FILE, "w") as f:
        json.dump(users, f, indent=4)

def load_users():
    """Loads users from users.json. If missing or invalid, resets it."""
    if not os.path.exists(USERS_FILE):  # If file doesn't exist, create it
        save_users({})

    try:
        with open(USERS_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        save_users({})
        return {}

# Load users into memory
users = load_users()

def save_nfts(nfts):
    with open(NFTS_FILE, "w") as f:
        json.dump(nfts, f, indent=4)

def load_nfts():
    if not os.path.exists(NFTS_FILE):  # If file doesn't exist, create it
        save_nfts({})

    try:
        with open(NFTS_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        save_nfts({})
        return {}

nfts = load_nfts()

# Function to create a Solana wallet
def create_solana_wallet():
    keypair = Keypair()
    secret_key_b58 = base58.b58encode(bytes(keypair)).decode("utf-8")
    return {
        "public_key": str(keypair.pubkey()),
        "secret_key": secret_key_b58
    }

# Signup Route
# Signup Route - Now includes Solana wallet generation
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
        # Generate Solana wallet
        wallet = create_solana_wallet()

        logging.info("here 5: Encrypting private key")
        # Encrypt private key before storing
        encrypted_private_key = cipher_suite.encrypt(wallet["secret_key"].encode()).decode()

        logging.info("here 6: Storing user info with wallet details")
        # Store user info with wallet details
        users[username] = {
            "password": password,
            "solana_public_key": wallet["public_key"],
            "solana_private_key": encrypted_private_key,  # Encrypted for security
            'nft_names': [],
            'descriptions': [],
            'image_paths': [],
            'latitude': [],
            'longitude': [],
            'nft_ids': []
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

# Haversine formula to calculate distance between two lat/lon points (miles)
def haversine(lat1, lon1, lat2, lon2):
    """Calculates the distance (in miles) between two lat/lon points."""
    R = 3958.8  # Earth's radius in miles
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))

@app.route('/nearby_points', methods=['GET'])
def nearby_points():
    lat = request.args.get("latitude", type=float)
    lon = request.args.get("longitude", type=float)
    
    if lat is None or lon is None:
        return jsonify({"success": False, "message": "Latitude and longitude are required."}), 400

    RADIUS_MILES = 10  # You can adjust this radius

    nearby_nfts = []
    for nft_id, nft_data in nfts.items():
        nft_lat = float(nft_data["latitude"])
        nft_lon = float(nft_data["longitude"])

        distance = haversine(lat, lon, nft_lat, nft_lon)
        if distance <= RADIUS_MILES:
            nearby_nfts.append(nfts[nft_id])

    return jsonify({"success": True, "nearby_nfts": nearby_nfts, "count": len(nearby_nfts)}), 200

@app.route('/get_wallet', methods=['POST'])
def get_wallet():
    username = request.form.get('username')

    if not username or username not in users:
        return jsonify({"success": False, "message": "User not found."}), 404

    decrypted_key = cipher_suite.decrypt(users[username]["solana_private_key"].encode()).decode()

    return jsonify({
        "solana_public_key": users[username]["solana_public_key"],
        "solana_private_key": decrypted_key  # Be careful exposing this!
    })


@app.route('/get_belonging', methods=['GET'])
def get_belongings():
    username = request.form.get('username')
    return jsonify({
        "nft_names": users[username]['nft_names'],
        "descriptions": users[username]['descriptions'],
        "image_paths": users[username]['image_paths']
    })

@app.route('/samaira')
def samaira():
    return "Hi Samaira!"

if __name__ == '__main__':
    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        os.makedirs(app.config['UPLOAD_FOLDER'])
    app.run(debug=True)