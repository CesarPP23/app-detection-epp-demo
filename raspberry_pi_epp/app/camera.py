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
        
        # Configuración de redimensionado
        self.scale_factor = self.config['scale_factor'] # Por defecto 1/3 del tamaño original
        self.target_width = None
        self.target_height = None
        
        # Configuración de FPS
        self.target_fps = self.config['fps']  # FPS objetivo (debe estar en settings.py)

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

            # Obtener dimensiones originales del video/cámara
            original_width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            original_height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            original_fps = int(self.cap.get(cv2.CAP_PROP_FPS))  # Obtener FPS original

            # Calcular dimensiones objetivo basadas en el factor de escala
            self.target_width = int(original_width * self.scale_factor)
            self.target_height = int(original_height * self.scale_factor)
            
            # Asegurar que las dimensiones sean pares (mejor para codecs de video)
            self.target_width = self.target_width if self.target_width % 2 == 0 else self.target_width - 1
            self.target_height = self.target_height if self.target_height % 2 == 0 else self.target_height - 1

            # Configurar FPS
            self.cap.set(cv2.CAP_PROP_FPS, self.target_fps)
            
            # Verificar que se haya configurado correctamente
            actual_fps = int(self.cap.get(cv2.CAP_PROP_FPS))
            if actual_fps != self.target_fps:
                self.logger.warning(f"No se pudo configurar FPS a {self.target_fps}.  FPS actual: {actual_fps}")
            else:
                self.logger.info(f"FPS configurado correctamente a {self.target_fps}")

            self.logger.info(f"Video original: {original_width}x{original_height} @ {original_fps}fps")
            self.logger.info(f"Video redimensionado: {self.target_width}x{self.target_height} (factor: {self.scale_factor:.2f})")
            self.logger.info(f"FPS objetivo: {self.target_fps}")

        except Exception as e:
            self.logger.error(f"Error inicializando cámara: {e}")
            raise

    def _resize_frame(self, frame: np.ndarray) -> np.ndarray:
        """
        Redimensiona el frame según el factor de escala configurado
        
        Args:
            frame: Frame original
            
        Returns:
            Frame redimensionado
        """
        if self.scale_factor == 1.0:
            return frame
            
        # Usar interpolación INTER_AREA para reducir tamaño (mejor calidad)
        # Usar INTER_LINEAR para aumentar tamaño
        interpolation = cv2.INTER_AREA if self.scale_factor < 1.0 else cv2.INTER_LINEAR
        
        resized_frame = cv2.resize(
            frame, 
            (self.target_width, self.target_height), 
            interpolation=interpolation
        )
        
        return resized_frame

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
        frame_time = 1.0 / self.target_fps  # Tiempo entre frames

        while self.is_running:
            start_time = time.time()

            try:
                # Capturar frame
                ret, frame = self.cap.read()

                if not ret:
                    # Si es un video, reiniciar desde el principio
                    if isinstance(self.config['source'], str) and self.config['source'].endswith(('.mp4', '.avi', '.mov', '.mkv')):
                        self.logger.info("Fin del video. Reiniciando desde el principio.")
                        self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        continue
                    else:
                        self.logger.error("No se pudo capturar frame de la cámara")
                        time.sleep(0.1)
                        continue

                # Redimensionar frame
                resized_frame = self._resize_frame(frame)

                # Guardar frame de forma segura (thread-safe)
                with self.frame_lock:
                    self.current_frame = resized_frame.copy()

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
        Obtiene el frame más reciente capturado (ya redimensionado)

        Returns:
            Frame como array de numpy o None si no hay frame disponible
        """
        with self.frame_lock:
            if self.current_frame is not None:
                return self.current_frame.copy()
            return None

    def get_original_frame(self) -> Optional[np.ndarray]:
        """
        Obtiene un frame en tamaño original (sin redimensionar)
        Útil si necesitas el frame completo para algún procesamiento específico

        Returns:
            Frame original o None
        """
        if not self.cap or not self.cap.isOpened():
            return None
            
        ret, frame = self.cap.read()
        if ret:
            return frame
        return None

    def get_frame_for_inference(self) -> Optional[np.ndarray]:
        """
        Obtiene un frame preparado para inferencia
        (ya viene redimensionado según la configuración)

        Returns:
            Frame procesado o None
        """
        frame = self.get_frame()
        if frame is None:
            return None

        # Aquí puedes agregar preprocesamiento adicional si es necesario
        # Por ejemplo: normalizar, cambiar formato de color, etc.

        return frame

    def set_scale_factor(self, scale_factor: float):
        """
        Cambia el factor de escala dinámicamente
        
        Args:
            scale_factor: Nuevo factor de escala (ej: 0.5 = 50% del tamaño original)
        """
        if scale_factor <= 0:
            self.logger.warning("El factor de escala debe ser mayor que 0")
            return
            
        self.scale_factor = scale_factor
        
        # Recalcular dimensiones objetivo
        if self.cap and self.cap.isOpened():
            original_width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            original_height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            
            self.target_width = int(original_width * self.scale_factor)
            self.target_height = int(original_height * self.scale_factor)
            
            # Asegurar dimensiones pares
            self.target_width = self.target_width if self.target_width % 2 == 0 else self.target_width - 1
            self.target_height = self.target_height if self.target_height % 2 == 0 else self.target_height - 1
            
            self.logger.info(f"Factor de escala actualizado: {scale_factor:.2f}")
            self.logger.info(f"Nuevas dimensiones: {self.target_width}x{self.target_height}")

    def get_frame_dimensions(self) -> Tuple[int, int]:
        """
        Obtiene las dimensiones actuales de los frames procesados
        
        Returns:
            Tupla (width, height) de los frames redimensionados
        """
        return (self.target_width, self.target_height)

    def get_original_dimensions(self) -> Tuple[int, int]:
        """
        Obtiene las dimensiones originales del video/cámara
        
        Returns:
            Tupla (width, height) original
        """
        if self.cap and self.cap.isOpened():
            width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            return (width, height)
        return (0, 0)

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

        original_width, original_height = self.get_original_dimensions()
        
        return {
            "status": "Activa" if self.is_running else "Inactiva",
            "original_width": original_width,
            "original_height": original_height,
            "processed_width": self.target_width,
            "processed_height": self.target_height,
            "scale_factor": self.scale_factor,
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