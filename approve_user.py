import asyncio
import os
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

# Load the .env file explicitly
load_dotenv()

async def approve():
    # Connect to MongoDB
    url = os.getenv("MONGODB_URL")
    if not url:
        print("Error: MONGODB_URL not found in .env")
        return

    print("Connecting to MongoDB...")
    client = AsyncIOMotorClient(url)
    db = client["welthwest"]
    
    target_email = "kunalkumar9457.kk@gmail.com"
    
    # Check if user exists
    user = await db.users.find_one({"email": target_email})
    if not user:
        print(f"User {target_email} not found in the database.")
    else:
        # Update user
        result = await db.users.update_one(
            {"email": target_email},
            {"$set": {"approved": True}}
        )
        print(f"Successfully approved user: {target_email} (Modified count: {result.modified_count})")
        
    client.close()

if __name__ == "__main__":
    asyncio.run(approve())
