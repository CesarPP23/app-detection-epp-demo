"""
Cliente WebSocket para comunicación con la nube
Este módulo maneja la conexión WebSocket con el backend en Google Cloud
"""
import asyncio
import websockets
import json
import time
import threading
from typing import Dict, Optional, Callable
from utils.logger import get_app_logger

class WebSocketClient:
    """
    Cliente WebSocket para comunicación con el backend en la nube
    """

    def __init__(self, websocket_config: dict, queue_manager):
        """
        Inicializa el cliente WebSocket

        Args:
            websocket_config: Configuración WebSocket desde settings.py
            queue_manager: Instancia del manejador de cola
        """
        self.logger = get_app_logger()
        self.config = websocket_config
        self.queue_manager = queue_manager

        # Estado de conexión
        self.websocket = None
        self.is_connected = False
        self.is_running = False
        self.reconnect_attempts = 0

        # Callbacks para eventos
        self.on_message_callback = None
        self.on_connect_callback = None
        self.on_disconnect_callback = None

        # Hilo para manejar la conexión
        self.connection_thread = None
        self.send_queue_thread = None

    def set_callbacks(self, 
                     on_message: Optional[Callable] = None,
                     on_connect: Optional[Callable] = None,
                     on_disconnect: Optional[Callable] = None):
        """
        Establece callbacks para eventos de WebSocket

        Args:
            on_message: Callback cuando se recibe un mensaje
            on_connect: Callback cuando se conecta
            on_disconnect: Callback cuando se desconecta
        """
        self.on_message_callback = on_message
        self.on_connect_callback = on_connect
        self.on_disconnect_callback = on_disconnect

    def start(self):
        """Inicia el cliente WebSocket"""
        if self.is_running:
            self.logger.warning("Cliente WebSocket ya está ejecutándose")
            return

        self.is_running = True

        # Iniciar hilo de conexión
        self.connection_thread = threading.Thread(
            target=self._run_connection_loop, 
            daemon=True
        )
        self.connection_thread.start()

        # Iniciar hilo para enviar cola pendiente
        self.send_queue_thread = threading.Thread(
            target=self._run_queue_sender, 
            daemon=True
        )
        self.send_queue_thread.start()

        self.logger.info("Cliente WebSocket iniciado")

    def stop(self):
        """Detiene el cliente WebSocket"""
        self.is_running = False

        if self.connection_thread:
            self.connection_thread.join(timeout=5)

        if self.send_queue_thread:
            self.send_queue_thread.join(timeout=5)

        self.logger.info("Cliente WebSocket detenido")

    def _run_connection_loop(self):
        """Loop principal de conexión (ejecuta en hilo separado)"""
        while self.is_running:
            try:
                # Crear nuevo loop de eventos para este hilo
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

                # Ejecutar conexión WebSocket
                loop.run_until_complete(self._connect_and_listen())

            except Exception as e:
                self.logger.error(f"Error en loop de conexión: {e}")

            finally:
                # Limpiar loop
                try:
                    loop.close()
                except:
                    pass

                # Esperar antes de reconectar
                if self.is_running:
                    time.sleep(self.config['reconnect_interval'])

    async def _connect_and_listen(self):
        """Conecta al WebSocket y escucha mensajes"""
        try:
            self.logger.info(f"Conectando a {self.config['server_url']}")

            # Conectar al WebSocket
            async with websockets.connect(
                self.config['server_url'],
                ping_interval=self.config['ping_interval']
            ) as websocket:

                self.websocket = websocket
                self.is_connected = True
                self.reconnect_attempts = 0

                self.logger.info("Conectado al servidor WebSocket")

                # Llamar callback de conexión
                if self.on_connect_callback:
                    self.on_connect_callback()

                # Escuchar mensajes
                await self._listen_for_messages()

        except websockets.exceptions.ConnectionClosed:
            self.logger.warning("Conexión WebSocket cerrada")
        except Exception as e:
            self.logger.error(f"Error de conexión WebSocket: {e}")
        finally:
            self.is_connected = False
            self.websocket = None

            # Llamar callback de desconexión
            if self.on_disconnect_callback:
                self.on_disconnect_callback()

            # Incrementar contador de reconexión
            self.reconnect_attempts += 1

            if self.reconnect_attempts >= self.config['max_reconnect_attempts']:
                self.logger.error("Máximo número de reconexiones alcanzado")
                self.is_running = False

    async def _listen_for_messages(self):
        """Escucha mensajes del servidor"""
        try:
            async for message in self.websocket:
                try:
                    # Parsear mensaje JSON
                    data = json.loads(message)
                    self.logger.debug(f"Mensaje recibido: {data}")

                    # Llamar callback de mensaje
                    if self.on_message_callback:
                        self.on_message_callback(data)

                except json.JSONDecodeError:
                    self.logger.error(f"Mensaje JSON inválido: {message}")
                except Exception as e:
                    self.logger.error(f"Error procesando mensaje: {e}")

        except websockets.exceptions.ConnectionClosed:
            self.logger.info("Conexión cerrada por el servidor")
        except Exception as e:
            self.logger.error(f"Error escuchando mensajes: {e}")

    def send_detection_data(self, detection_data: Dict) -> bool:
        """
        Envía datos de detección al servidor

        Args:
            detection_data: Datos de detección a enviar

        Returns:
            True si se envió exitosamente, False si se agregó a la cola
        """
        if self.is_connected and self.websocket:
            try:
                # ✅ ESTRUCTURA CORRECTA - igual que test_connection.py
                message = {
                    "type": "detection",
                    "raspberry_id": "raspberrypi001",  # ← Agregar aquí también por compatibilidad
                    "data": {
                        "raspberry_id": "raspberrypi001",  # ← CLAVE: debe estar dentro de "data"
                        "timestamp": detection_data.get("timestamp", time.time()),
                        "total_detections": detection_data.get("total_detections", 0),
                        "detections": detection_data.get("detections", []),
                        "compliance_status": detection_data.get("compliance_status", {}),
                        # Agregar campos adicionales del detection_result
                        "total_persons": len([d for d in detection_data.get("detections", []) if d.get("class_name") == "person"]),
                        "persons_with_epp": 0,  # Calcular según lógica
                        "compliance_percentage": detection_data.get("compliance_status", {}).get("compliance_percentage", 0)
                    }
                }

                # ✅ ENVÍO SÍNCRONO más confiable
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                
                async def send_message():
                    await self.websocket.send(json.dumps(message))
                
                loop.run_until_complete(send_message())
                loop.close()

                self.logger.info(f"✅ Datos enviados: Compliance {message['data']['compliance_percentage']}%")
                return True

            except Exception as e:
                self.logger.error(f"❌ Error enviando datos: {e}")

        # Si no hay conexión, agregar a la cola
        self.queue_manager.add_to_queue(detection_data)
        self.logger.debug("📦 Datos agregados a la cola (sin conexión)")
        return False
    def send_status_update(self, status_data: Dict) -> bool:
        """
        Envía actualización de estado al servidor

        Args:
            status_data: Datos de estado a enviar

        Returns:
            True si se envió exitosamente
        """
        if not (self.is_connected and self.websocket):
            return False

        try:
            message = {
                "type": "status",
                "timestamp": time.time(),
                "data": status_data
            }

            asyncio.run_coroutine_threadsafe(
                self.websocket.send(json.dumps(message)),
                self.websocket.loop if hasattr(self.websocket, 'loop') else asyncio.get_event_loop()
            )

            self.logger.debug("Estado enviado")
            return True

        except Exception as e:
            self.logger.error(f"Error enviando estado: {e}")
            return False

    def _run_queue_sender(self):
        """
        Hilo para enviar datos de la cola cuando hay conexión
        """
        while self.is_running:
            try:
                if self.is_connected:
                    # Obtener lote de la cola
                    batch = self.queue_manager.get_next_batch(5)

                    if batch:
                        sent_ids = []
                        failed_ids = []

                        for item in batch:
                            success = self.send_detection_data(item['detection_data'])

                            if success:
                                sent_ids.append(item['queue_id'])
                            else:
                                failed_ids.append(item['queue_id'])

                        # Marcar elementos como enviados o fallidos
                        if sent_ids:
                            self.queue_manager.mark_as_sent(sent_ids)
                            self.logger.info(f"📤 Enviados {len(sent_ids)} elementos de la cola")

                        if failed_ids:
                            self.queue_manager.mark_as_failed(failed_ids)

                # ✅ CORREGIR: usar reconnect_interval en lugar de retry_interval
                time.sleep(self.config.get('reconnect_interval', 10))

            except Exception as e:
                self.logger.error(f"❌ Error en sender de cola: {e}")
                time.sleep(5)

    def get_connection_status(self) -> Dict:
        """
        Obtiene el estado de la conexión

        Returns:
            Diccionario con estado de conexión
        """
        return {
            "is_connected": self.is_connected,
            "is_running": self.is_running,
            "reconnect_attempts": self.reconnect_attempts,
            "server_url": self.config['server_url'],
            "queue_size": self.queue_manager.get_queue_size()
        }

# Función auxiliar para probar la conexión WebSocket
def test_websocket_connection(websocket_config: dict) -> bool:
    """
    Prueba la conexión WebSocket

    Args:
        websocket_config: Configuración WebSocket

    Returns:
        True si puede conectar, False en caso contrario
    """
    try:
        async def test_connect():
            try:
                async with websockets.connect(
                    websocket_config['server_url'],
                    timeout=5
                ) as websocket:
                    # Enviar mensaje de prueba
                    test_message = {"type": "test", "timestamp": time.time()}
                    await websocket.send(json.dumps(test_message))
                    return True
            except:
                return False

        # Ejecutar prueba
        return asyncio.run(test_connect())

    except Exception as e:
        print(f"Error probando WebSocket: {e}")
        return False