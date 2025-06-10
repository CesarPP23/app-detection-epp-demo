import cv2
import logging

logger = logging.getLogger(__name__)

class Camera:
    def __init__(self, source=0, width=None, height=None, fps=30):
        self.source = source
        self.width = width
        self.height = height
        self.fps = fps
        self.video_capture = None
        self.is_running = False

    def initialize(self):
        try:
            # Modificación: Usar un archivo de video en lugar de la webcam
            self.video_capture = cv2.VideoCapture(self.source)  # self.source ahora es la ruta al video
            if not self.video_capture.isOpened():
                raise Exception(f"No se pudo abrir el video desde: {self.source}")

            self.video_capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            self.video_capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            self.video_capture.set(cv2.CAP_PROP_FPS, self.fps)

            self.is_running = True
            logger.info(f"Cámara configurada: {self.width}x{self.height} @ {self.fps}fps")
        except Exception as e:
            logger.error(f"Error al inicializar la cámara: {e}")
            self.video_capture = None

    def start(self):
        logger.info("Captura de video iniciada")
        while self.is_running and self.video_capture is not None:
            ret, frame = self.video_capture.read()
            if not ret:
                logger.warning("Fin del video. Reiniciando desde el principio.")
                self.video_capture.set(cv2.CAP_PROP_POS_FRAMES, 0)  # Reiniciar el video
                continue
            yield frame

    def stop(self):
        self.is_running = False
        if self.video_capture is not None:
            self.video_capture.release()
            logger.info("Captura de video detenida")

    def release(self):
        if self.video_capture is not None:
            self.video_capture.release()
            logger.info("Recursos de cámara liberados")