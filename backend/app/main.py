from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import asyncio
import json
from typing import Optional, List
from datetime import datetime, timedelta

from .websocket_manager import connection_manager
from .services.firestore_service import FirestoreService
from .services.storage_service import StorageService
from .services.alert_service import AlertService
from .models.detection import DetectionData, DeviceStatus, VideoStreamRequest
from .utils.logger import get_logger
from config.settings import FASTAPI_CONFIG, VIDEO_CONFIG

logger = get_logger(__name__)

# Servicios globales
firestore_service = FirestoreService()
storage_service = StorageService()
alert_service = AlertService()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Maneja el ciclo de vida de la aplicación"""
    logger.info("=== INICIANDO BACKEND EPP CLOUD ===")
    
    # Iniciar tareas de limpieza periódica
    cleanup_task = asyncio.create_task(periodic_cleanup())
    
    yield
    
    # Cancelar tareas al cerrar
    cleanup_task.cancel()
    logger.info("=== BACKEND EPP CLOUD CERRADO ===")

app = FastAPI(
    title="EPP Detection Cloud Backend",
    description="Backend en la nube para sistema de detección de EPP",
    version="1.0.0",
    lifespan=lifespan
)

# Configurar CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configurar según necesidades
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

async def periodic_cleanup():
    """Tarea periódica de limpieza"""
    while True:
        try:
            await storage_service.cleanup_expired_clips()
            await asyncio.sleep(VIDEO_CONFIG["cleanup_interval"])
        except Exception as e:
            logger.error(f"Error en limpieza periódica: {e}")
            await asyncio.sleep(60)

# === ENDPOINTS REST ===

@app.get("/")
async def root():
    return {
        "message": "EPP Detection Cloud Backend",
        "version": "1.0.0",
        "status": "running",
        "connected_devices": len(connection_manager.device_connections),
        "connected_clients": len(connection_manager.client_connections)
    }

@app.get("/devices")
async def get_connected_devices():
    """Lista dispositivos conectados"""
    devices = []
    for device_id in connection_manager.device_connections.keys():
        status = await firestore_service.get_device_status(device_id)
        devices.append({
            "device_id": device_id,
            "connected": True,
            "last_status": status
        })
    
    return {"devices": devices}

@app.get("/devices/{device_id}/status")
async def get_device_status(device_id: str):
    """Obtiene estado de un dispositivo"""
    status = await firestore_service.get_device_status(device_id)
    if not status:
        raise HTTPException(status_code=404, detail="Dispositivo no encontrado")
    
    return {
        "device_id": device_id,
        "connected": device_id in connection_manager.device_connections,
        "status": status
    }

@app.post("/devices/{device_id}/command")
async def send_device_command(device_id: str, command: dict):
    """Envía comando a un dispositivo"""
    if device_id not in connection_manager.device_connections:
        raise HTTPException(status_code=404, detail="Dispositivo no conectado")
    
    await connection_manager.send_to_device(device_id, command)
    return {"message": "Comando enviado", "device_id": device_id}

@app.get("/detections")
async def get_detections_history(
    device_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 100
):
    """Obtiene historial de detecciones"""
    start_dt = datetime.fromisoformat(start_date) if start_date else None
    end_dt = datetime.fromisoformat(end_date) if end_date else None
    
    detections = await firestore_service.get_detections_history(
        device_id=device_id,
        start_date=start_dt,
        end_date=end_dt,
        limit=limit
    )
    
    return {"detections": detections}

@app.get("/alerts")
async def get_active_alerts():
    """Obtiene alertas activas"""
    alerts = await firestore_service.get_active_alerts()
    return {"alerts": alerts}

@app.get("/devices/{device_id}/video-clips")
async def get_device_video_clips(device_id: str, limit: int = 20):
    """Lista clips de video de un dispositivo"""
    clips = await storage_service.list_device_clips(device_id, limit)
    return {"device_id": device_id, "clips": clips}

@app.get("/video-clips/{filename}/url")
async def get_video_clip_url(filename: str, expiration_minutes: int = 60):
    """Genera URL para acceder a un clip de video"""
    try:
        url = await storage_service.get_video_clip_url(filename, expiration_minutes)
        return {"url": url, "expires_in_minutes": expiration_minutes}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Clip de video no encontrado")

# === WEBSOCKET ENDPOINTS ===

@app.websocket("/ws/device/{device_id}")
async def websocket_device_endpoint(websocket: WebSocket, device_id: str):
    """WebSocket para dispositivos Raspberry Pi"""
    await connection_manager.connect_device(websocket, device_id)
    
    try:
        while True:
            data = await websocket.receive_text()
            message_data = json.loads(data)
            await connection_manager.handle_device_message(device_id, message_data)
            
    except WebSocketDisconnect:
        await connection_manager.disconnect_device(device_id)
    except Exception as e:
        logger.error(f"Error en WebSocket del dispositivo {device_id}: {e}")
        await connection_manager.disconnect_device(device_id)

@app.websocket("/ws/client")
async def websocket_client_endpoint(websocket: WebSocket):
    """WebSocket para clientes (Streamlit, etc.)"""
    client_id = await connection_manager.connect_client(websocket)
    
    try:
        while True:
            data = await websocket.receive_text()
            message_data = json.loads(data)
            await connection_manager.handle_client_message(client_id, message_data)
            
    except WebSocketDisconnect:
        await connection_manager.disconnect_client(client_id)
    except Exception as e:
        logger.error(f"Error en WebSocket del cliente {client_id}: {e}")
        await connection_manager.disconnect_client(client_id)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=FASTAPI_CONFIG["host"],
        port=FASTAPI_CONFIG["port"],
        reload=FASTAPI_CONFIG["debug"]
    )