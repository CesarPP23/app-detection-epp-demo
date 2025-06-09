"""
Aplicación principal del Raspberry Pi
Este es el punto de entrada principal que coordina todos los componentes
"""
import asyncio
import time
import threading
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
import cv2
import psutil
import os
import io
# Importar nuestros módulos
from config.settings import *
from utils.logger import get_app_logger
from app.camera import CameraManager
from app.inference import YOLOInference
from app.queue_manager import QueueManager
from app.websocket_client import WebSocketClient

# Variables globales para los componentes
camera_manager = None
yolo_inference = None
queue_manager = None
websocket_client = None
logger = None

# Estado del sistema
system_state = {
    "is_monitoring": False,
    "last_detection": None,
    "alerts_active": False,
    "start_time": time.time()
}

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Maneja el ciclo de vida de la aplicación
    Se ejecuta al iniciar y al cerrar la aplicación
    """
    global camera_manager, yolo_inference, queue_manager, websocket_client, logger

    # INICIO DE LA APLICACIÓN
    logger = get_app_logger()
    logger.info("=== INICIANDO SISTEMA EPP RASPBERRY PI ===")

    try:
        # 1. Inicializar manejador de cola
        logger.info("Inicializando cola persistente...")
        queue_manager = QueueManager(QUEUE_CONFIG)

        # 2. Inicializar cámara
        logger.info("Inicializando cámara...")
        camera_manager = CameraManager(CAMERA_CONFIG)
        camera_manager.start_capture()

        # 3. Inicializar modelo YOLO
        logger.info("Cargando modelo YOLO...")
        yolo_inference = YOLOInference(MODEL_CONFIG)

        # 4. Inicializar cliente WebSocket
        logger.info("Inicializando cliente WebSocket...")
        websocket_client = WebSocketClient(WEBSOCKET_CONFIG, queue_manager)

        # Configurar callbacks del WebSocket
        websocket_client.set_callbacks(
            on_connect=on_websocket_connect,
            on_disconnect=on_websocket_disconnect,
            on_message=on_websocket_message
        )

        # Iniciar cliente WebSocket
        websocket_client.start()

        # 5. Iniciar monitoreo automático
        start_monitoring()

        logger.info("=== SISTEMA INICIADO CORRECTAMENTE ===")

        yield  # Aquí la aplicación está ejecutándose

    except Exception as e:
        logger.error(f"Error durante la inicialización: {e}")
        raise

    # CIERRE DE LA APLICACIÓN
    logger.info("=== CERRANDO SISTEMA ===")

    try:
        # Detener monitoreo
        stop_monitoring()

        # Cerrar componentes
        if websocket_client:
            websocket_client.stop()

        if camera_manager:
            camera_manager.release()

        logger.info("Sistema cerrado correctamente")

    except Exception as e:
        logger.error(f"Error durante el cierre: {e}")

# Crear aplicación FastAPI
app = FastAPI(
    title="Raspberry Pi EPP Detection System",
    description="Sistema de detección de EPP en tiempo real",
    version="1.0.0",
    lifespan=lifespan
)

# Callbacks del WebSocket
def on_websocket_connect():
    """Callback cuando se conecta al WebSocket"""
    logger.info("Conectado al servidor en la nube")

    # Enviar estado inicial
    status_data = get_system_status()
    websocket_client.send_status_update(status_data)

def on_websocket_disconnect():
    """Callback cuando se desconecta del WebSocket"""
    logger.warning("Desconectado del servidor en la nube")

def on_websocket_message(message_data):
    """
    Callback cuando se recibe un mensaje del WebSocket

    Args:
        message_data: Datos del mensaje recibido
    """
    try:
        message_type = message_data.get("type")

        if message_type == "start_monitoring":
            start_monitoring()
            logger.info("Monitoreo iniciado por comando remoto")

        elif message_type == "stop_monitoring":
            stop_monitoring()
            logger.info("Monitoreo detenido por comando remoto")

        elif message_type == "get_status":
            status = get_system_status()
            websocket_client.send_status_update(status)

        elif message_type == "update_config":
            # Actualizar configuración (implementar según necesidades)
            logger.info("Solicitud de actualización de configuración recibida")

    except Exception as e:
        logger.error(f"Error procesando mensaje WebSocket: {e}")

# Hilo de monitoreo principal
monitoring_thread = None
monitoring_active = False

def monitoring_loop():
    """
    Loop principal de monitoreo
    Se ejecuta en un hilo separado
    """
    global monitoring_active, system_state

    logger.info("Iniciando loop de monitoreo")
    last_alert_time = 0

    while monitoring_active:
        try:
            # Obtener frame de la cámara
            frame = camera_manager.get_frame_for_inference()

            if frame is None:
                time.sleep(0.1)
                continue

            # Ejecutar inferencia
            detection_result = yolo_inference.detect_epp(frame)

            # Actualizar estado del sistema
            system_state["last_detection"] = detection_result

            # Verificar cumplimiento de EPP
            compliance = detection_result["compliance_status"]

            # Enviar datos al servidor (o cola si no hay conexión)
            websocket_client.send_detection_data(detection_result)

            # Verificar si necesita enviar alerta
            current_time = time.time()
            if not compliance["is_compliant"]:
                if current_time - last_alert_time > ALERT_CONFIG["missing_epp_threshold"]:
                    send_alert(detection_result)
                    last_alert_time = current_time

            # Log de detección
            if detection_result["total_detections"] > 0:
                logger.info(f"Detecciones: {detection_result['total_detections']}, "
                           f"Cumplimiento: {compliance['compliance_percentage']}%")

            # Controlar FPS del monitoreo (no necesariamente igual al de la cámara)
            time.sleep(1.0 / 10)  # 10 FPS para el monitoreo

        except Exception as e:
            logger.error(f"Error en loop de monitoreo: {e}")
            time.sleep(1)

    logger.info("Loop de monitoreo detenido")

def start_monitoring():
    """Inicia el monitoreo automático"""
    global monitoring_thread, monitoring_active, system_state

    if monitoring_active:
        logger.warning("El monitoreo ya está activo")
        return

    monitoring_active = True
    system_state["is_monitoring"] = True

    monitoring_thread = threading.Thread(target=monitoring_loop, daemon=True)
    monitoring_thread.start()

    logger.info("Monitoreo automático iniciado")

def stop_monitoring():
    """Detiene el monitoreo automático"""
    global monitoring_active, system_state

    monitoring_active = False
    system_state["is_monitoring"] = False

    if monitoring_thread:
        monitoring_thread.join(timeout=5)

    logger.info("Monitoreo automático detenido")

def send_alert(detection_result):
    """
    Envía una alerta por incumplimiento de EPP

    Args:
        detection_result: Resultado de detección que causó la alerta
    """
    alert_data = {
        "type": "epp_violation",
        "timestamp": time.time(),
        "device_id": DEVICE_ID,
        "missing_epp": detection_result["compliance_status"]["missing_epp"],
        "compliance_percentage": detection_result["compliance_status"]["compliance_percentage"]
    }

    # Enviar alerta al servidor
    websocket_client.send_detection_data(alert_data)

    logger.warning(f"ALERTA EPP: Falta {', '.join(alert_data['missing_epp'])}")

def get_system_status():
    """
    Obtiene el estado completo del sistema

    Returns:
        Diccionario con estado del sistema
    """
    # Información del sistema
    cpu_percent = psutil.cpu_percent(interval=1)
    memory = psutil.virtual_memory()

    # Temperatura (si está disponible)
    temperature = None
    try:
        if os.path.exists('/sys/class/thermal/thermal_zone0/temp'):
            with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                temperature = int(f.read()) / 1000.0
    except:
        pass

    # Información mejorada de la cámara
    camera_info = None
    if camera_manager:
        camera_info = camera_manager.get_camera_info()
        # Agregar información adicional sobre dimensiones
        if camera_info.get("status") == "Activa":
            original_dims = camera_manager.get_original_dimensions()
            processed_dims = camera_manager.get_frame_dimensions()
            camera_info.update({
                "original_dimensions": f"{original_dims[0]}x{original_dims[1]}",
                "processed_dimensions": f"{processed_dims[0]}x{processed_dims[1]}",
                "scale_factor": camera_manager.scale_factor
            })

    return {
        "device_id": DEVICE_ID,
        "timestamp": time.time(),
        "uptime": time.time() - system_state["start_time"],
        "monitoring_active": system_state["is_monitoring"],
        "camera_status": camera_info,
        "model_status": yolo_inference.get_model_info() if yolo_inference else None,
        "websocket_status": websocket_client.get_connection_status() if websocket_client else None,
        "queue_stats": queue_manager.get_queue_stats() if queue_manager else None,
        "system_resources": {
            "cpu_percent": cpu_percent,
            "memory_percent": memory.percent,
            "memory_available_mb": memory.available // (1024*1024),
            "temperature_celsius": temperature
        },
        "last_detection": system_state["last_detection"]
    }

# === ENDPOINTS DE LA API ===

@app.get("/")
async def root():
    """Endpoint raíz"""
    return {
        "message": "Sistema EPP Raspberry Pi",
        "version": "1.0.0",
        "status": "running"
    }

@app.get("/status")
async def get_status():
    """Obtiene el estado completo del sistema"""
    try:
        status = get_system_status()
        return JSONResponse(content=status)
    except Exception as e:
        logger.error(f"Error obteniendo estado: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/monitoring/start")
async def start_monitoring_endpoint():
    """Inicia el monitoreo"""
    try:
        start_monitoring()
        return {"message": "Monitoreo iniciado", "status": "success"}
    except Exception as e:
        logger.error(f"Error iniciando monitoreo: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/monitoring/stop")
async def stop_monitoring_endpoint():
    """Detiene el monitoreo"""
    try:
        stop_monitoring()
        return {"message": "Monitoreo detenido", "status": "success"}
    except Exception as e:
        logger.error(f"Error deteniendo monitoreo: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/camera/frame")
async def get_current_frame():
    """Obtiene el frame actual de la cámara"""
    try:
        if not camera_manager or not camera_manager.is_camera_available():
            raise HTTPException(status_code=503, detail="Cámara no disponible")

        frame = camera_manager.get_frame()
        if frame is None:
            raise HTTPException(status_code=503, detail="No hay frame disponible")

        # Ejecutar detección en el frame
        detection_result = yolo_inference.detect_epp(frame)

        # Dibujar detecciones
        annotated_frame = yolo_inference.draw_detections(frame, detection_result)

        # Convertir a JPEG
        _, buffer = cv2.imencode('.jpg', annotated_frame)

        return StreamingResponse(
            io.BytesIO(buffer.tobytes()),
            media_type="image/jpeg"
        )

    except Exception as e:
        logger.error(f"Error obteniendo frame: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/detection/latest")
async def get_latest_detection():
    """Obtiene la última detección realizada"""
    try:
        if system_state["last_detection"] is None:
            return {"message": "No hay detecciones disponibles"}

        return JSONResponse(content=system_state["last_detection"])

    except Exception as e:
        logger.error(f"Error obteniendo detección: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Punto de entrada para ejecutar con uvicorn
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=FASTAPI_CONFIG["host"],
        port=FASTAPI_CONFIG["port"],
        reload=FASTAPI_CONFIG["debug"]
    )
