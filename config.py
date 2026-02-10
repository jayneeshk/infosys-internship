from pymongo import MongoClient

# Local MongoDB configuration
MONGO_URI = "mongodb://127.0.0.1:27017/"
MONGO_DB_NAME = "aadhaar_fraud_db"

try:
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    client.server_info()  # Validate connection
    db = client[MONGO_DB_NAME]
    users_collection = db["users"]
    print("Connected to local MongoDB server")
except Exception as e:
    raise RuntimeError(f"MongoDB connection failed: {e}")
