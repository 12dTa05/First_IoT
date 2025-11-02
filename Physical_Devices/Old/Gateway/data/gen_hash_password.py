import hashlib
import json
import sys
from datetime import datetime

# Salt phải giống với device
DEVICE_SALT = "passkey_01_salt_2025"

def generate_password_hash(password):
    """Generate FULL SHA-256 hash (64 characters) of salted password"""
    salted = DEVICE_SALT + password
    return hashlib.sha256(salted.encode()).hexdigest()

def verify_password(password, stored_hash):
    """Verify password against stored hash"""
    generated_hash = generate_password_hash(password)
    return generated_hash == stored_hash

def create_password_entry(password, owner, description="", active=True, expires_at=None):
    """Create a complete password entry for database"""
    password_hash = generate_password_hash(password)
    
    entry = {
        "hash": password_hash,
        "active": active,
        "owner": owner,
        "description": description,
        "created_at": datetime.now().isoformat() + "Z",
        "last_used": None,
        "expires_at": expires_at
    }
    
    return entry

def interactive_mode():
    """Interactive mode to generate password entries"""
    print("=" * 60)
    print("PASSWORD HASH GENERATOR FOR IOT GATEWAY (IMPROVED)")
    print("Using FULL SHA-256 hash (64 characters)")
    print("=" * 60)
    print()
    
    passwords = {}
    
    while True:
        print("\n--- New Password Entry ---")
        
        user_id = input("Enter user ID (e.g., user_001) [or 'q' to quit]: ").strip()
        if user_id.lower() == 'q':
            break
        
        if not user_id:
            print("Error: User ID cannot be empty")
            continue
        
        password = input("Enter password: ").strip()
        if not password:
            print("Error: Password cannot be empty")
            continue
        
        # Password strength check
        if len(password) < 6:
            print("Warning: Password is too short (minimum 6 characters recommended)")
            confirm = input("Continue anyway? (y/n): ").strip().lower()
            if confirm != 'y':
                continue
        
        owner = input("Enter owner name: ").strip()
        if not owner:
            owner = "Unknown User"
        
        description = input("Enter description (optional): ").strip()
        
        active_input = input("Active? (y/n) [default: y]: ").strip().lower()
        active = active_input != 'n'
        
        expires_input = input("Set expiration date? (y/n) [default: n]: ").strip().lower()
        expires_at = None
        if expires_input == 'y':
            expires_at = input("Enter expiration date (YYYY-MM-DDTHH:MM:SSZ): ").strip()
        
        # Generate entry
        entry = create_password_entry(password, owner, description, active, expires_at)
        passwords[user_id] = entry
        
        # Show generated hash
        print("\n--- Generated Entry ---")
        print(f"User ID: {user_id}")
        print(f"Password: {password}")
        print(f"Hash (FULL 64 chars): {entry['hash']}")
        print(f"Owner: {entry['owner']}")
        print(f"Active: {entry['active']}")
        if expires_at:
            print(f"Expires: {expires_at}")
        print("-" * 40)
        
        # Verify
        if verify_password(password, entry['hash']):
            print("✓ Verification successful!")
        else:
            print("✗ Verification failed!")
    
    # Save to file
    if passwords:
        save_choice = input("\nSave to file? (y/n): ").strip().lower()
        if save_choice == 'y':
            filename = input("Enter filename [default: passwords.json]: ").strip()
            if not filename:
                filename = "passwords.json"
            
            output = {
                "passwords": passwords,
                "generated_at": datetime.now().isoformat() + "Z",
                "total_entries": len(passwords),
                "salt_used": DEVICE_SALT,
                "hash_algorithm": "SHA-256",
                "hash_length": 64
            }
            
            try:
                with open(filename, 'w') as f:
                    json.dump(output, f, indent=2)
                print(f"\n✓ Saved to {filename}")
            except Exception as e:
                print(f"\n✗ Error saving file: {e}")
    
    print("\nDone!")

def quick_generate(password):
    """Quick generate hash for a single password"""
    password_hash = generate_password_hash(password)
    print(f"Salt: {DEVICE_SALT}")
    print(f"Password: {password}")
    print(f"Full Hash (64 chars): {password_hash}")
    print(f"\nVerification: {verify_password(password, password_hash)}")
    return password_hash

def batch_generate():
    """Generate hashes for test passwords with FULL hash"""
    print("Generating FULL hashes (64 characters) for test passwords...")
    print("=" * 60)
    
    test_passwords = {
        "user_001": {
            "password": "123456",
            "owner": "Test User 1",
            "description": "Test password 123456"
        },
        "user_002": {
            "password": "333333",
            "owner": "Test User 2",
            "description": "Test password 333333"
        },
        "admin": {
            "password": "admin123",
            "owner": "Administrator",
            "description": "Admin password"
        },
        "guest": {
            "password": "guest2024",
            "owner": "Guest",
            "description": "Guest access password",
            "expires_at": "2025-12-31T23:59:59Z"
        }
    }
    
    passwords_db = {}
    
    for user_id, info in test_passwords.items():
        password = info["password"]
        entry = create_password_entry(
            password, 
            info["owner"], 
            info["description"], 
            True,
            info.get("expires_at")
        )
        passwords_db[user_id] = entry
        
        print(f"\n{user_id}:")
        print(f"  Password: {password}")
        print(f"  Hash (FULL): {entry['hash']}")
        print(f"  Owner: {entry['owner']}")
    
    # Save to file
    output = {
        "passwords": passwords_db,
        "generated_at": datetime.now().isoformat() + "Z",
        "total_entries": len(passwords_db),
        "salt_used": DEVICE_SALT,
        "hash_algorithm": "SHA-256",
        "hash_length": 64,
        "note": "These are TEST passwords only. Change them in production!"
    }
    
    filename = "test_passwords.json"
    try:
        with open(filename, 'w') as f:
            json.dump(output, f, indent=2)
        print(f"\n✓ Saved to {filename}")
    except Exception as e:
        print(f"\n✗ Error saving file: {e}")
    
    print("\n" + "=" * 60)
    print("\nIMPORTANT: These hashes are FULL 64 characters")
    print("The gateway now uses the complete hash for authentication")
    print("This provides maximum security against brute force attacks")

def main():
    if len(sys.argv) > 1:
        if sys.argv[1] == "--batch":
            batch_generate()
        elif sys.argv[1] == "--quick":
            if len(sys.argv) > 2:
                quick_generate(sys.argv[2])
            else:
                print("Usage: python generate_password_hash.py --quick <password>")
        elif sys.argv[1] == "--help":
            print("Password Hash Generator (IMPROVED VERSION)")
            print("\nUsage:")
            print("  python generate_password_hash.py              # Interactive mode")
            print("  python generate_password_hash.py --batch      # Generate test passwords")
            print("  python generate_password_hash.py --quick <pw> # Quick hash generation")
            print("  python generate_password_hash.py --help       # Show this help")
            print("\nFeatures:")
            print("  - Uses FULL 64-character SHA-256 hash")
            print("  - Salted passwords for additional security")
            print("  - Password expiration support")
            print("  - Automatic verification")
        else:
            quick_generate(sys.argv[1])
    else:
        interactive_mode()

if __name__ == "__main__":
    main()