import bcrypt

def generate_hash():
    password = "251203".encode()  # bcrypt yêu cầu dạng bytes
    salt = bcrypt.gensalt(rounds=10)
    hashed = bcrypt.hashpw(password, salt)

    print("Password:", password.decode())
    print("Hash:", hashed.decode())

generate_hash()
