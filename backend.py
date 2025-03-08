from fastapi import FastAPI, WebSocket, WebSocketDisconnect, BackgroundTasks, HTTPException
import asyncio
import uuid
import logging
import json
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict, Any
from datetime import datetime

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Adjust for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Store image generation results and status
image_results = {}  # request_id -> result dict

# Active WebSocket connections
active_websockets = {}  # request_id -> WebSocket

@app.websocket("/ws/{user_id}/{request_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: str, request_id: str):
    await websocket.accept()
    logger.info(f"🟢 WebSocket connection accepted for {user_id} - {request_id}")
    
    # Store WebSocket connection
    active_websockets[request_id] = websocket
    
    try:
        # Check if we already have a result for this request_id
        if request_id in image_results:
            result = image_results[request_id]
            
            # Send the result immediately
            if result["status"] == "completed":
                logger.info(f"✅ Sending cached result for {user_id} - {request_id}")
                await websocket.send_json({
                    "request_id": request_id,
                    "image_url": result["image_url"],
                    "status": "completed",
                    "timestamp": datetime.now().isoformat()
                })
            elif result["status"] == "error":
                logger.info(f"⚠️ Sending cached error for {user_id} - {request_id}")
                await websocket.send_json({
                    "request_id": request_id,
                    "status": "error",
                    "message": result.get("error", "Unknown error"),
                    "timestamp": datetime.now().isoformat()
                })
        
        # Keep connection open and handle messages
        while True:
            data = await websocket.receive_text()
            
            # Handle ping/pong for keepalive
            if data == "ping":
                await websocket.send_text("pong")
            elif data == "pong":
                pass  # Do nothing, just acknowledge
            # Try to parse as JSON message
            elif data.startswith("{"):
                try:
                    message = json.loads(data)
                    
                    # Handle acknowledgments
                    if message.get("type") == "acknowledgment":
                        logger.info(f"👍 Client acknowledged receipt for {user_id} - {request_id}")
                        
                        # We could remove the WebSocket connection here as it's done its job
                        # But we'll keep it open for a bit longer for any additional messages
                        
                    # Handle ready signals
                    elif message.get("type") == "ready":
                        logger.info(f"📱 Client ready signal received for {user_id} - {request_id}")
                except json.JSONDecodeError:
                    logger.warning(f"⚠️ Received invalid JSON from client: {user_id} - {request_id}")
            
    except WebSocketDisconnect:
        logger.info(f"🔴 WebSocket disconnected for {user_id} - {request_id}")
    except Exception as e:
        logger.error(f"❌ WebSocket error for {user_id} - {request_id}: {str(e)}")
    finally:
        # Clean up the connection
        if request_id in active_websockets:
            del active_websockets[request_id]

@app.post("/generate")
async def generate_request(data: dict, background_tasks: BackgroundTasks):
    user_id = data.get("user_id")
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id is required")
    
    request_id = str(uuid.uuid4())  # Generate a unique request ID
    
    # Create a record for this generation task
    image_results[request_id] = {
        "status": "processing",
        "created_at": datetime.now().isoformat(),
        "user_id": user_id
    }
    
    # Start image generation in background
    background_tasks.add_task(generate_image, user_id, request_id)
    
    logger.info(f"🚀 Started image generation for {user_id} - {request_id}")
    return {"message": "Image generation started!", "request_id": request_id}

async def generate_image(user_id: str, request_id: str):
    """
    Simulates image generation and attempts to send results through WebSocket.
    """
    try:
        # Simulate image generation delay
        await asyncio.sleep(10)
        
        # Create dummy image URL
        image_url = f"https://dummyimage.com/600x400/000/fff&text=Generated+Image+{request_id}"
        
        # Update the result
        image_results[request_id] = {
            "status": "completed",
            "image_url": image_url,
            "completed_at": datetime.now().isoformat(),
            "user_id": user_id
        }
        
        # Attempt to send the result if a WebSocket connection exists
        if request_id in active_websockets:
            websocket = active_websockets[request_id]
            try:
                logger.info(f"🔔 Attempting to send result to client for {user_id} - {request_id}")
                await websocket.send_json({
                    "request_id": request_id,
                    "image_url": image_url,
                    "status": "completed",
                    "timestamp": datetime.now().isoformat()
                })
                logger.info(f"✅ Result sent successfully to {user_id} - {request_id}")
            except Exception as e:
                logger.error(f"❌ Error sending result to client for {user_id} - {request_id}: {str(e)}")
        else:
            logger.warning(f"⚠️ No active WebSocket found for {user_id} - {request_id}")
    except Exception as e:
        logger.error(f"❌ Error in generate_image for {user_id} - {request_id}: {str(e)}")
        
        # Update the result with error
        image_results[request_id] = {
            "status": "error",
            "error": str(e),
            "completed_at": datetime.now().isoformat(),
            "user_id": user_id
        }
        
        # Try to send error to client
        if request_id in active_websockets:
            websocket = active_websockets[request_id]
            try:
                await websocket.send_json({
                    "request_id": request_id,
                    "status": "error",
                    "message": str(e),
                    "timestamp": datetime.now().isoformat()
                })
            except Exception:
                pass  # Can't send error message, but result is stored

@app.get("/status/{user_id}/{request_id}")
async def get_status(user_id: str, request_id: str):
    """
    Endpoint to check generation status without WebSocket
    """
    if request_id not in image_results:
        raise HTTPException(status_code=404, detail="Request not found")
    
    result = image_results[request_id]
    
    # Verify user_id matches (optional security check)
    if result.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="Not authorized to access this request")
    
    return {
        "request_id": request_id,
        "status": result["status"],
        "created_at": result.get("created_at"),
        "completed_at": result.get("completed_at"),
        "result": result.get("image_url") if result["status"] == "completed" else None,
        "error": result.get("error") if result["status"] == "error" else None
    }

# Cleanup task to periodically remove old results
@app.on_event("startup")
async def startup_event():
    background_tasks = BackgroundTasks()
    background_tasks.add_task(cleanup_old_results)

async def cleanup_old_results():
    """
    Periodically clean up old results
    """
    while True:
        try:
            # Keep results for 30 minutes
            cutoff = datetime.now()
            keys_to_remove = []
            
            for request_id, result in image_results.items():
                if "completed_at" in result:
                    completed_time = datetime.fromisoformat(result["completed_at"])
                    if (cutoff - completed_time).total_seconds() > 1800:  # 30 minutes
                        keys_to_remove.append(request_id)
            
            # Remove old entries
            for key in keys_to_remove:
                del image_results[key]
                logger.info(f"🧹 Cleaned up old result: {key}")
                
        except Exception as e:
            logger.error(f"❌ Error in cleanup task: {str(e)}")
        
        # Run every 5 minutes
        await asyncio.sleep(300)