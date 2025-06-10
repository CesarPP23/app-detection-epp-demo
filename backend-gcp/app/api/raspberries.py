"""
API REST para manejo de Raspberry Pi devices
Endpoints SOLO para consultas de Streamlit sobre dispositivos
La comunicación real con Raspberry Pi es por WebSocket
"""
from fastapi import APIRouter, Query
from app.database.firestore_client import firestore_client
from app.utils.logger import get_logger, log_websocket_event
from typing import Dict, List, Optional
from datetime import datetime
import time

router = APIRouter()
logger = get_logger(__name__)

# Registro en memoria de Raspberry Pis conectados via WebSocket
# Esto se actualiza desde el WebSocket endpoint en main.py
connected_raspberries: Dict[str, Dict] = {}

@router.get("/")
async def get_raspberries():
    """
    Lista todos los Raspberry Pi registrados
    SOLO para consultas de Streamlit
    """
    try:
        return {
            "status": "success",
            "count": len(connected_raspberries),
            "raspberries": connected_raspberries,
            "timestamp": datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Error obteniendo lista de Raspberry Pi: {e}")
        return {
            "status": "error",
            "message": f"Error interno: {str(e)}"
        }

@router.get("/{raspberry_id}")
async def get_raspberry_info(raspberry_id: str):
    """
    Obtiene información detallada de un Raspberry Pi específico
    SOLO para consultas de Streamlit
    """
    try:
        # Buscar en memoria primero
        device_info = connected_raspberries.get(raspberry_id)
        
        if not device_info:
            return {
                "status": "error",
                "message": f"Raspberry Pi {raspberry_id} no encontrado o no conectado"
            }
        
        # Obtener estadísticas recientes
        stats = await firestore_client.get_raspberry_stats(raspberry_id, hours=24)
        
        # Combinar información
        complete_info = {
            **device_info,
            "recent_stats": stats,
            "is_online": True,  # Si está en memoria, asumimos que está online
            "retrieved_at": datetime.utcnow().isoformat()
        }
        
        return {
            "status": "success",
            "raspberry_info": complete_info
        }
        
    except Exception as e:
        logger.error(f"Error obteniendo info de {raspberry_id}: {e}")
        return {
            "status": "error",
            "message": f"Error interno: {str(e)}"
        }

@router.get("/status/summary")
async def get_status_summary():
    """
    Obtiene resumen del estado de todos los Raspberry Pi
    SOLO para consultas de Streamlit
    """
    try:
        current_time = datetime.utcnow()
        online_count = 0
        offline_count = 0
        
        for raspberry_id, info in connected_raspberries.items():
            # Considerar offline si no se ha visto en los últimos 5 minutos
            last_seen = datetime.fromisoformat(info.get("last_seen", "1970-01-01T00:00:00"))
            minutes_since_seen = (current_time - last_seen).total_seconds() / 60
            
            if minutes_since_seen <= 5:
                online_count += 1
                connected_raspberries[raspberry_id]["status"] = "online"
            else:
                offline_count += 1
                connected_raspberries[raspberry_id]["status"] = "offline"
        
        return {
            "status": "success",
            "summary": {
                "total_devices": len(connected_raspberries),
                "online": online_count,
                "offline": offline_count,
                "last_updated": current_time.isoformat()
            },
            "devices": connected_raspberries
        }
        
    except Exception as e:
        logger.error(f"Error obteniendo resumen de estado: {e}")
        return {
            "status": "error",
            "message": f"Error interno: {str(e)}"
        }

# Funciones auxiliares para que main.py actualice el registro
def register_raspberry_connection(raspberry_id: str, device_info: Dict):
    """
    Registra una conexión WebSocket de Raspberry Pi
    Llamada desde main.py cuando se conecta un dispositivo
    """
    connected_raspberries[raspberry_id] = {
        **device_info,
        "connected_at": datetime.utcnow().isoformat(),
        "last_seen": datetime.utcnow().isoformat(),
        "status": "online"
    }
    logger.info(f"Raspberry Pi registrado: {raspberry_id}")

def update_raspberry_heartbeat(raspberry_id: str):
    """
    Actualiza el heartbeat de un Raspberry Pi
    Llamada desde main.py cuando se recibe actividad
    """
    if raspberry_id in connected_raspberries:
        connected_raspberries[raspberry_id]["last_seen"] = datetime.utcnow().isoformat()
        connected_raspberries[raspberry_id]["status"] = "online"

def unregister_raspberry_connection(raspberry_id: str):
    """
    Desregistra una conexión WebSocket de Raspberry Pi
    Llamada desde main.py cuando se desconecta un dispositivo
    """
    if raspberry_id in connected_raspberries:
        connected_raspberries.pop(raspberry_id)
        logger.info(f"Raspberry Pi desconectado: {raspberry_id}")