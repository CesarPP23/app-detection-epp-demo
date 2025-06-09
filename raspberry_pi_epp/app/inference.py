import torch
import cv2
import numpy as np
from ultralytics import YOLO
from typing import List, Dict, Optional, Tuple
import time
from utils.logger import get_app_logger

class YOLOInference:
    """
    Clase para manejar la inferencia del modelo YOLOv8s
    """

    def __init__(self, model_config: dict):
        """
        Inicializa el modelo de inferencia

        Args:
            model_config: Configuración del modelo desde settings.py
        """
        self.logger = get_app_logger()
        self.config = model_config
        self.model = None
        self.device = model_config['device']

        # Cargar el modelo
        self._load_model()

    def _load_model(self):
        """Carga el modelo YOLOv8s"""
        try:
            self.logger.info(f"Cargando modelo desde: {self.config['model_path']}")

            # Cargar modelo YOLO
            self.model = YOLO(str(self.config['model_path']))

            # Configurar dispositivo (CPU o GPU)
            if self.device == 'cuda' and torch.cuda.is_available():
                self.logger.info("Usando GPU para inferencia")
            else:
                self.device = 'cpu'
                self.logger.info("Usando CPU para inferencia")

            # Hacer una inferencia de prueba para "calentar" el modelo
            dummy_image = np.zeros((640, 640, 3), dtype=np.uint8)
            _ = self.model(dummy_image, device=self.device, verbose=False)

            self.logger.info("Modelo cargado exitosamente")

        except Exception as e:
            self.logger.error(f"Error cargando modelo: {e}")
            raise

    def detect_epp(self, frame: np.ndarray) -> Dict:
        """
        Detecta EPP en un frame

        Args:
            frame: Frame de video como array numpy

        Returns:
            Diccionario con resultados de detección
        """
        if self.model is None:
            self.logger.error("Modelo no cargado")
            return self._empty_detection_result()

        try:
            start_time = time.time()

            # Ejecutar inferencia
            results = self.model(
                frame,
                device=self.device,
                conf=self.config['confidence_threshold'],
                verbose=False
            )

            # Procesar resultados
            detection_result = self._process_results(results[0], frame.shape)

            # Calcular tiempo de inferencia
            inference_time = time.time() - start_time
            detection_result['inference_time'] = round(inference_time, 3)

            self.logger.debug(f"Inferencia completada en {inference_time:.3f}s")

            return detection_result

        except Exception as e:
            self.logger.error(f"Error en inferencia: {e}")
            return self._empty_detection_result()

    def _process_results(self, result, frame_shape: Tuple[int, int, int]) -> Dict:
        """
        Procesa los resultados de YOLO y los convierte a formato estándar

        Args:
            result: Resultado de YOLO
            frame_shape: Forma del frame (height, width, channels)

        Returns:
            Diccionario con detecciones procesadas
        """
        detections = []
        epp_summary = {
            "guantes": False,
            "cofia": False,
            "mascarilla": False,
            "bata": False
        }

        # Verificar si hay detecciones
        if result.boxes is not None and len(result.boxes) > 0:
            boxes = result.boxes.xyxy.cpu().np()  # Coordenadas x1,y1,x2,y2
            confidences = result.boxes.conf.cpu().np()  # Confianzas
            classes = result.boxes.cls.cpu().np()  # Clases

            for i, (box, conf, cls) in enumerate(zip(boxes, confidences, classes)):
                cls_id = int(cls)

                # Verificar si la clase está en nuestras clases de interés
                if cls_id in self.config['class_names']:
                    class_name = self.config['class_names'][cls_id]

                    # Crear detección
                    detection = {
                        "id": i,
                        "class_id": cls_id,
                        "class_name": class_name,
                        "confidence": round(float(conf), 3),
                        "bbox": {
                            "x1": int(box[0]),
                            "y1": int(box[1]),
                            "x2": int(box[2]),
                            "y2": int(box[3])
                        }
                    }

                    detections.append(detection)

                    # Actualizar resumen de EPP
                    if class_name in epp_summary:
                        epp_summary[class_name] = True

        return {
            "timestamp": time.time(),
            "frame_shape": frame_shape,
            "total_detections": len(detections),
            "detections": detections,
            "epp_summary": epp_summary,
            "compliance_status": self._check_compliance(epp_summary)
        }

    def _check_compliance(self, epp_summary: Dict[str, bool]) -> Dict:
        """
        Verifica el cumplimiento de EPP

        Args:
            epp_summary: Resumen de EPP detectado

        Returns:
            Estado de cumplimiento
        """
        required_epp = ["guantes", "cofia", "mascarilla", "bata"]
        missing_epp = [epp for epp in required_epp if not epp_summary.get(epp, False)]

        is_compliant = len(missing_epp) == 0

        return {
            "is_compliant": is_compliant,
            "missing_epp": missing_epp,
            "compliance_percentage": round((len(required_epp) - len(missing_epp)) / len(required_epp) * 100, 1)
        }

    def _empty_detection_result(self) -> Dict:
        """Retorna un resultado vacío en caso de error"""
        return {
            "timestamp": time.time(),
            "frame_shape": None,
            "total_detections": 0,
            "detections": [],
            "epp_summary": {
                "guantes": False,
                "cofia": False,
                "mascarilla": False,
                "bata": False
            },
            "compliance_status": {
                "is_compliant": False,
                "missing_epp": ["guantes", "cofia", "mascarilla", "bata"],
                "compliance_percentage": 0.0
            },
            "inference_time": 0.0,
            "error": True
        }

    def draw_detections(self, frame: np.ndarray, detection_result: Dict) -> np.ndarray:
        """
        Dibuja las detecciones en el frame

        Args:
            frame: Frame original
            detection_result: Resultado de detección

        Returns:
            Frame con detecciones dibujadas
        """
        if not detection_result.get('detections'):
            return frame

        # Copiar frame para no modificar el original
        annotated_frame = frame.copy()

        # Colores para cada clase de EPP
        colors = {
            "guantes": (0, 255, 0),    # Verde
            "cofia": (255, 0, 0),      # Azul
            "mascarilla": (0, 0, 255), # Rojo
            "bata": (255, 255, 0)      # Cian
        }

        for detection in detection_result['detections']:
            bbox = detection['bbox']
            class_name = detection['class_name']
            confidence = detection['confidence']

            # Obtener color
            color = colors.get(class_name, (255, 255, 255))

            # Dibujar rectángulo
            cv2.rectangle(
                annotated_frame,
                (bbox['x1'], bbox['y1']),
                (bbox['x2'], bbox['y2']),
                color,
                2
            )

            # Dibujar etiqueta
            label = f"{class_name}: {confidence:.2f}"
            label_size = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 2)[0]

            # Fondo para el texto
            cv2.rectangle(
                annotated_frame,
                (bbox['x1'], bbox['y1'] - label_size[1] - 10),
                (bbox['x1'] + label_size[0], bbox['y1']),
                color,
                -1
            )

            # Texto
            cv2.putText(
                annotated_frame,
                label,
                (bbox['x1'], bbox['y1'] - 5),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (0, 0, 0),
                2
            )

        # Agregar información de cumplimiento
        compliance = detection_result['compliance_status']
        status_text = f"Cumplimiento: {compliance['compliance_percentage']}%"
        status_color = (0, 255, 0) if compliance['is_compliant'] else (0, 0, 255)

        cv2.putText(
            annotated_frame,
            status_text,
            (10, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            status_color,
            2
        )

        return annotated_frame

    def get_model_info(self) -> Dict:
        """
        Obtiene información sobre el modelo

        Returns:
            Información del modelo
        """
        if self.model is None:
            return {"status": "No cargado"}

        return {
            "status": "Cargado",
            "device": self.device,
            "confidence_threshold": self.config['confidence_threshold'],
            "classes": self.config['class_names']
        }

# Función auxiliar para probar el modelo
def test_inference(model_config: dict, test_image_path: Optional[str] = None) -> bool:
    """
    Prueba la inferencia del modelo

    Args:
        model_config: Configuración del modelo
        test_image_path: Ruta de imagen de prueba (opcional)

    Returns:
        True si la inferencia funciona, False en caso contrario
    """
    try:
        inference = YOLOInference(model_config)

        # Crear imagen de prueba si no se proporciona una
        if test_image_path is None:
            test_image = np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)
        else:
            test_image = cv2.imread(test_image_path)

        result = inference.detect_epp(test_image)
        return not result.get('error', False)

    except Exception as e:
        print(f"Error probando inferencia: {e}")
        return False
