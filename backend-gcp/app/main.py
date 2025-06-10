from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import json
import time
from app.core.settings import settings
from app.database.firestore_client import firestore_client
from app.utils.logger import get_logger

# Crear aplicación FastAPI
app = FastAPI(
    title="Backend EPP Detection",
    description="Backend para sistema de detección de EPP con Raspberry Pi",
    version="1.0.0"
)

# Configurar CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Permitir todos los orígenes para debug
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Logger
logger = get_logger(__name__)

@app.get("/")
async def root():
    """Endpoint raíz"""
    return {
        "message": "Backend EPP Detection - WebSocket Ready",
        "status": "running",
        "environment": settings.environment,
        "project": settings.google_cloud_project,
        "websocket_endpoint": "/ws",
        "version": "1.0.0"
    }

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    firestore_status = firestore_client.get_connection_status()
    
    return {
        "status": "healthy",
        "websocket": "ready",
        "firestore": firestore_status,
        "environment": settings.environment
    }

# ← ESTE ES EL ENDPOINT QUE NECESITAS
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    Endpoint WebSocket para comunicación con Raspberry Pi
    """
    await websocket.accept()
    logger.info("Raspberry Pi conectado via WebSocket")
    
    try:
        while True:
            # Recibir mensaje
            data = await websocket.receive_text()
            message = json.loads(data)
            
            logger.info(f"Mensaje WebSocket recibido: {message.get('type', 'unknown')}")
            
            # Procesar según el tipo de mensaje
            if message.get("type") == "status":
                response = await handle_status_message(message.get("data", {}))
                
            elif message.get("type") == "detection":
                response = await handle_detection_message(message.get("data", {}))
                
            elif message.get("type") == "test":
                response = {
                    "status": "success",
                    "message": "Conexión WebSocket funcionando correctamente",
                    "server_time": time.time()
                }
                
            else:
                response = {
                    "status": "error",
                    "message": f"Tipo de mensaje no reconocido: {message.get('type')}"
                }
            
            # Enviar respuesta
            await websocket.send_text(json.dumps(response))
            
    except WebSocketDisconnect:
        logger.info("Raspberry Pi desconectado")
    except Exception as e:
        logger.error(f"Error en WebSocket: {e}")

async def handle_status_message(data: dict) -> dict:
    """
    Maneja mensajes de estado del Raspberry Pi
    """
    try:
        device_id = data.get("device_id", "unknown")
        
        # Guardar estado en Firestore
        status_doc = {
            "device_id": device_id,
            "status": data.get("status", "unknown"),
            "ip_address": data.get("ip_address"),
            "version": data.get("version"),
            "model_loaded": data.get("model_loaded", False),
            "camera_source": data.get("camera_source"),
            "timestamp": time.time(),
            "last_seen": time.time()
        }
        
        # Guardar en Firestore
        doc_ref = firestore_client.db.collection("raspberries").document(device_id)
        doc_ref.set(status_doc, merge=True)
        
        logger.info(f"Estado del Raspberry Pi {device_id} actualizado")
        
        return {
            "status": "received",
            "message": "Estado actualizado correctamente",
            "device_id": device_id
        }
        
    except Exception as e:
        logger.error(f"Error procesando estado: {e}")
        return {
            "status": "error",
            "message": str(e)
        }

async def handle_detection_message(data: dict) -> dict:
    """
    Maneja mensajes de detección del Raspberry Pi
    """
    try:
        raspberry_id = data.get("raspberry_id", "unknown")
        
        # Crear documento de detección
        detection_doc = {
            "raspberry_id": raspberry_id,
            "timestamp": data.get("timestamp", time.time()),
            "total_detections": data.get("total_detections", 0),
            "detections": data.get("detections", []),
            "compliance_status": data.get("compliance_status", {}),
            "processed_at": time.time()
        }
        
        # Guardar en Firestore
        doc_ref = firestore_client.db.collection("detections").add(detection_doc)
        doc_id = doc_ref[1].id
        
        compliance = data.get("compliance_status", {}).get("compliance_percentage", 0)
        logger.info(f"Detección guardada: {compliance}% cumplimiento")
        
        return {
            "status": "success",
            "doc_id": doc_id,
            "message": "Detección guardada en Firestore",
            "compliance": compliance,
            "timestamp": time.time()
        }
        
    except Exception as e:
        logger.error(f"Error procesando detección: {e}")
        return {
            "status": "error",
            "message": str(e)
        }
# Agregar estos endpoints después del endpoint /health

@app.get("/firestore/status")
async def firestore_detailed_status():
    """Verificar estado detallado de Firestore"""
    try:
        # Probar escribir y leer un documento de prueba
        test_doc = {
            "test": True,
            "timestamp": time.time(),
            "message": "Prueba de conectividad"
        }
        
        # Escribir documento de prueba
        doc_ref = firestore_client.db.collection("test").document("connectivity_test")
        doc_ref.set(test_doc)
        
        # Leer documento de prueba
        doc = doc_ref.get()
        
        if doc.exists:
            return {
                "status": "success",
                "message": "Firestore funcionando correctamente",
                "test_data": doc.to_dict(),
                "collections_available": True
            }
        else:
            return {
                "status": "error",
                "message": "No se pudo leer el documento de prueba"
            }
            
    except Exception as e:
        return {
            "status": "error",
            "message": f"Error de Firestore: {str(e)}"
        }

@app.get("/data/raspberries")
async def get_raspberries():
    """Ver datos de Raspberry Pi registrados"""
    try:
        docs = firestore_client.db.collection("raspberries").limit(10).stream()
        
        raspberries = []
        for doc in docs:
            data = doc.to_dict()
            data['id'] = doc.id
            raspberries.append(data)
        
        return {
            "status": "success",
            "count": len(raspberries),
            "raspberries": raspberries
        }
        
    except Exception as e:
        return {
            "status": "error",
            "message": str(e)
        }

@app.get("/data/detections")
async def get_detections():
    """Ver últimas detecciones"""
    try:
        docs = firestore_client.db.collection("detections").order_by("timestamp", direction="DESCENDING").limit(10).stream()
        
        detections = []
        for doc in docs:
            data = doc.to_dict()
            data['id'] = doc.id
            detections.append(data)
        
        return {
            "status": "success",
            "count": len(detections),
            "detections": detections
        }
        
    except Exception as e:
        return {
            "status": "error",
            "message": str(e)
        }

@app.get("/data/stats")
async def get_stats():
    """Estadísticas generales"""
    try:
        # Contar documentos en cada colección
        raspberries_count = len(list(firestore_client.db.collection("raspberries").stream()))
        detections_count = len(list(firestore_client.db.collection("detections").stream()))
        
        # Última detección
        last_detection = None
        last_detection_docs = list(firestore_client.db.collection("detections").order_by("timestamp", direction="DESCENDING").limit(1).stream())
        
        if last_detection_docs:
            last_detection = last_detection_docs[0].to_dict()
        
        return {
            "status": "success",
            "collections": {
                "raspberries": raspberries_count,
                "detections": detections_count
            },
            "last_detection": last_detection,
            "timestamp": time.time()
        }
        
    except Exception as e:
        return {
            "status": "error",
            "message": str(e)
        }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)