import os
import subprocess
import re
import json
from flask import Flask, request, render_template, redirect, url_for, flash, jsonify
from solders.keypair import Keypair
import base58
from cryptography.fernet import Fernet

app = Flask(__name__)
app.secret_key = os.urandom(24)
app.config['UPLOAD_FOLDER'] = 'uploads'

USERS_FILE = "users.json"

# Generate encryption key (Run once and store securely)
encryption_key = Fernet.generate_key()
cipher_suite = Fernet(encryption_key)

# Function to save users to JSON file
def save_users(users):
    """Saves the current users dictionary to users.json file."""
    with open(USERS_FILE, "w") as f:
        json.dump(users, f, indent=4)

# Function to load users from JSON file (creates new file if missing or corrupted)
def load_users():
    """Loads users from users.json. If missing or invalid, resets it."""
    if not os.path.exists(USERS_FILE):  # If file doesn't exist, create it
        save_users({})
    
    try:
        with open(USERS_FILE, "r") as f:
            return json.load(f)  # Load users from JSON
    except (json.JSONDecodeError, FileNotFoundError):  # Handle empty or corrupted JSON
        print("Warning: users.json was empty or corrupted. Resetting it.")
        save_users({})  # Reset to empty JSON
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

# Signup Route - Now includes Solana wallet generation
@app.route('/signup', methods=['POST'])
def signup():
    username = request.form.get('username')
    password = request.form.get('password')

    if not username or not password:
        return jsonify({"success": False, "message": "Username and password are required."}), 400

    if username in users:
        return jsonify({"success": False, "message": "Username already exists."}), 400
    else:
        # Generate Solana wallet
        wallet = create_solana_wallet()

        # Encrypt private key before storing
        encrypted_private_key = cipher_suite.encrypt(wallet["secret_key"].encode()).decode()

        # Store user info with wallet details
        users[username] = {
            "password": password,
            "solana_public_key": wallet["public_key"],
            "solana_private_key": encrypted_private_key  # Encrypted for security
        }
        
        save_users(users)
        return jsonify({
            "success": True,
            "message": "Signup successful.",
            "solana_public_key": wallet["public_key"]
        }), 201

# Login Route
@app.route('/login', methods=['POST'])
def login():
    username = request.form.get('username')
    password = request.form.get('password')

    if not username or not password:
        return jsonify({"success": False, "message": "Username and password are required."}), 400
    if username in users and users[username]["password"] == password:
        return jsonify({"success": True, "message": "Login successful."}), 200
    else:
        return jsonify({"success": False, "message": "Invalid username or password."}), 401

# Retrieve Wallet Details (Securely)
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

# Global dictionary to store details of the upload.
user_images = {}

@app.route('/', methods=['GET', 'POST'])
def upload():
    mint_address = None  # To store the mint address if available
    if request.method == 'POST':
        # Retrieve fields from the form.
        username = request.form.get('username')
        nft_name = request.form.get('name')
        description = request.form.get('description')
        recipient_pubkey = request.form.get('recipient')
        image = request.files.get('image')
        
        if not username or not nft_name or not description or not image or not recipient_pubkey:
            flash("Username, NFT name, description, recipient public key, and image are all required!")
            return redirect(request.url)
        
        # Save the uploaded image.
        filename = image.filename
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        image.save(file_path)
        
        # Update the global dictionary.
        user_images[username] = {
            'name': nft_name,
            'description': description,
            'path': file_path,
            'recipient': recipient_pubkey
        }
        
        # Call the Node.js mint script.
        mint_command = ['node', 'mintNft.js', file_path, nft_name, 'MYNFT', description]
        try:
            mint_output = subprocess.check_output(mint_command, stderr=subprocess.STDOUT)
            mint_output_str = mint_output.decode()
            print("Mint Output:\n", mint_output_str)
            
            # Extract the mint address from the output using regex.
            match = re.search(r'NFT created with address:\s*(\S+)', mint_output_str)
            if match:
                mint_address = match.group(1)
            else:
                flash("Minting succeeded but mint address not found in output.")
        except subprocess.CalledProcessError as e:
            print("Error minting NFT:", e.output.decode())
            flash("Error minting NFT. Check the server logs.")
            return redirect(request.url)
        
        # Call the Node.js send script with mint address and recipient pubkey as parameters.
        send_command = ['node', 'sendNft.js', mint_address, recipient_pubkey]
        try:
            send_output = subprocess.check_output(send_command, stderr=subprocess.STDOUT)
            print("Send Output:\n", send_output.decode())
        except subprocess.CalledProcessError as e:
            print("Error sending NFT:", e.output.decode())
            flash("Error sending NFT. Check the server logs.")
            return redirect(request.url)
        
        flash("NFT minted and sent successfully!")
        if mint_address:
            flash(f"NFT Mint Address: {mint_address}")
        
        return redirect(url_for('upload'))
    
    return render_template('upload.html', user_images=user_images, mint_address=mint_address)

@app.route('/samaira')
def samaira():
    return "Hi Samaira!"

if __name__ == '__main__':
    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        os.makedirs(app.config['UPLOAD_FOLDER'])
    app.run(debug=True)
