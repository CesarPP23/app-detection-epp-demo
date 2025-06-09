import cv2
import numpy as np
import threading
import time
from typing import Optional, Tuple
from utils.logger import get_app_logger

class CameraManager:
    """
    Clase para manejar la captura de video desde la cámara
    """

    def __init__(self, camera_config: dict):
        """
        Inicializa el manejador de cámara

        Args:
            camera_config: Configuración de la cámara desde settings.py
        """
        self.logger = get_app_logger()
        self.config = camera_config
        self.cap = None
        self.is_running = False
        self.current_frame = None
        self.frame_lock = threading.Lock()  # Para acceso seguro al frame
        self.capture_thread = None

        # Inicializar la cámara
        self._initialize_camera()

    def _initialize_camera(self):
        """Inicializa la conexión con la cámara"""
        try:
            self.logger.info(f"Inicializando cámara en fuente: {self.config['source']}")

            # Crear objeto VideoCapture
            self.cap = cv2.VideoCapture(self.config['source'])

            if not self.cap.isOpened():
                raise Exception(f"No se pudo abrir la cámara en {self.config['source']}")

            # Configurar propiedades de la cámara
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.config['width'])
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config['height'])
            self.cap.set(cv2.CAP_PROP_FPS, self.config['fps'])

            # Verificar configuración
            actual_width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            actual_height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            actual_fps = int(self.cap.get(cv2.CAP_PROP_FPS))

            self.logger.info(f"Cámara configurada: {actual_width}x{actual_height} @ {actual_fps}fps")

        except Exception as e:
            self.logger.error(f"Error inicializando cámara: {e}")
            raise

    def start_capture(self):
        """Inicia la captura de video en un hilo separado"""
        if self.is_running:
            self.logger.warning("La captura ya está en ejecución")
            return

        self.is_running = True
        self.capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self.capture_thread.start()
        self.logger.info("Captura de video iniciada")

    def stop_capture(self):
        """Detiene la captura de video"""
        self.is_running = False
        if self.capture_thread:
            self.capture_thread.join(timeout=5)
        self.logger.info("Captura de video detenida")

    def _capture_loop(self):
        """
        Loop principal de captura de video
        Se ejecuta en un hilo separado para no bloquear la aplicación
        """
        frame_time = 1.0 / self.config['fps']  # Tiempo entre frames

        while self.is_running:
            start_time = time.time()

            try:
                # Capturar frame
                ret, frame = self.cap.read()

                if not ret:
                    self.logger.error("No se pudo capturar frame de la cámara")
                    time.sleep(0.1)
                    continue

                # Guardar frame de forma segura (thread-safe)
                with self.frame_lock:
                    self.current_frame = frame.copy()

                # Controlar FPS
                elapsed = time.time() - start_time
                sleep_time = max(0, frame_time - elapsed)
                if sleep_time > 0:
                    time.sleep(sleep_time)

            except Exception as e:
                self.logger.error(f"Error en captura de video: {e}")
                time.sleep(1)

    def get_frame(self) -> Optional[np.ndarray]:
        """
        Obtiene el frame más reciente capturado

        Returns:
            Frame como array de numpy o None si no hay frame disponible
        """
        with self.frame_lock:
            if self.current_frame is not None:
                return self.current_frame.copy()
            return None

    def get_frame_for_inference(self) -> Optional[np.ndarray]:
        """
        Obtiene un frame preparado para inferencia
        (puede incluir preprocesamiento adicional)

        Returns:
            Frame procesado o None
        """
        frame = self.get_frame()
        if frame is None:
            return None

        # Aquí puedes agregar preprocesamiento si es necesario
        # Por ejemplo: redimensionar, normalizar, etc.

        return frame

    def is_camera_available(self) -> bool:
        """
        Verifica si la cámara está disponible y funcionando

        Returns:
            True si la cámara está disponible, False en caso contrario
        """
        return self.cap is not None and self.cap.isOpened() and self.is_running

    def get_camera_info(self) -> dict:
        """
        Obtiene información sobre la cámara

        Returns:
            Diccionario con información de la cámara
        """
        if not self.cap:
            return {"status": "No inicializada"}

        return {
            "status": "Activa" if self.is_running else "Inactiva",
            "width": int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
            "height": int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            "fps": int(self.cap.get(cv2.CAP_PROP_FPS)),
            "is_opened": self.cap.isOpened()
        }

    def release(self):
        """Libera los recursos de la cámara"""
        self.stop_capture()
        if self.cap:
            self.cap.release()
            self.logger.info("Recursos de cámara liberados")

    def __del__(self):
        """Destructor para asegurar liberación de recursos"""
        self.release()

# Función auxiliar para probar la cámara
def test_camera(camera_config: dict) -> bool:
    """
    Prueba si la cámara funciona correctamente

    Args:
        camera_config: Configuración de la cámara

    Returns:
        True si la cámara funciona, False en caso contrario
    """
    try:
        camera = CameraManager(camera_config)
        camera.start_capture()
        time.sleep(2)  # Esperar un poco

        frame = camera.get_frame()
        camera.release()

        return frame is not None

    except Exception as e:
        print(f"Error probando cámara: {e}")
        return False
