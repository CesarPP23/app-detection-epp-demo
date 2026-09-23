import asyncio
import logging
import os
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException
from fastapi.responses import StreamingResponse

# --- Configuración ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
# El token se lee de una variable de entorno para mayor seguridad.
UPLINK_TOKEN = os.environ.get("UPLINK_TOKEN")

class DeviceManager:
    """Gestiona las conexiones de control y los últimos frames de cada dispositivo."""
    def __init__(self):
        self.control_connections: dict[str, WebSocket] = {}
        self.latest_frames: dict[str, bytes] = {}
        self.lock = asyncio.Lock()

    async def connect_control(self, device_id: str, websocket: WebSocket):
        async with self.lock:
            self.control_connections[device_id] = websocket

    async def disconnect_control(self, device_id: str):
        async with self.lock:
            self.control_connections.pop(device_id, None)

    async def send_command(self, device_id: str, action: str):
        async with self.lock:
            connection = self.control_connections.get(device_id)
            if connection:
                await connection.send_json({"action": action})
                return True
            return False

    async def set_frame(self, device_id: str, frame: bytes):
        async with self.lock:
            self.latest_frames[device_id] = frame
    
    async def get_frame(self, device_id: str) -> bytes | None:
        async with self.lock:
            return self.latest_frames.get(device_id)

app = FastAPI(title="Streaming Orchestrator")
manager = DeviceManager()

@app.websocket("/ws/control/{device_id}")
async def websocket_control(websocket: WebSocket, device_id: str):
    """Endpoint para la conexión de control persistente de los dispositivos."""
    auth_token = websocket.headers.get("Authorization")
    if auth_token != UPLINK_TOKEN:
        logging.warning(f"Intento de conexión de '{device_id}' rechazado por token inválido.")
        await websocket.close(code=1008)
        return

    await websocket.accept()
    await manager.connect_control(device_id, websocket)
    logging.info(f"Dispositivo '{device_id}' conectado al canal de control.")
    try:
        while True:
            # Mantenemos la conexión abierta para poder enviar comandos
            await websocket.receive_text() 
    except WebSocketDisconnect:
        await manager.disconnect_control(device_id)
        logging.info(f"Dispositivo '{device_id}' desconectado del canal de control.")

@app.websocket("/ws/video/{device_id}")
async def websocket_video(websocket: WebSocket, device_id: str):
    """Endpoint para recibir el stream de video desde un dispositivo."""
    auth_token = websocket.headers.get("Authorization")
    if auth_token != UPLINK_TOKEN:
        logging.warning(f"Intento de conexión de video de '{device_id}' rechazado.")
        await websocket.close(code=1008)
        return

    await websocket.accept()
    logging.info(f"Dispositivo '{device_id}' conectado al canal de video.")
    try:
        while True:
            frame_bytes = await websocket.receive_bytes()
            await manager.set_frame(device_id, frame_bytes)
    except WebSocketDisconnect:
        logging.warning(f"Dispositivo '{device_id}' desconectado del canal de video.")

@app.get("/request_stream/{device_id}/{action}")
async def request_stream(device_id: str, action: str):
    """Endpoint HTTP para que el dashboard inicie o detenga el streaming."""
    action_upper = action.upper()
    if action_upper not in ["START_STREAM", "STOP_STREAM"]:
        raise HTTPException(status_code=400, detail="Acción inválida. Usa 'start_stream' o 'stop_stream'.")
    
    success = await manager.send_command(device_id, action_upper)
    if success:
        return {"status": "comando enviado", "device_id": device_id, "action": action}
    else:
        raise HTTPException(status_code=404, detail=f"Dispositivo '{device_id}' no conectado o no a la escucha.")

async def viewer_stream_generator(device_id: str):
    """Generador que emite frames de un dispositivo específico a los espectadores."""
    logging.info(f"Nuevo espectador para el dispositivo '{device_id}'.")
    try:
        while True:
            frame = await manager.get_frame(device_id)
            if frame:
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
            # Controla la tasa de refresco para los espectadores
            await asyncio.sleep(0.03)
    except asyncio.CancelledError:
        logging.info(f"Espectador del dispositivo '{device_id}' desconectado.")

@app.get("/video_stream/{device_id}")
async def video_stream_for_viewers(device_id: str):
    """Endpoint HTTP para que los navegadores vean el streaming."""
    return StreamingResponse(viewer_stream_generator(device_id), media_type="multipart/x-mixed-replace; boundary=frame")

@app.get("/")
def root():
    return {"status": "Orquestador de streaming activo."}

@app.get("/gastos")
async def solicitar_gastos_totales():
    return {"gastos": 123.45}