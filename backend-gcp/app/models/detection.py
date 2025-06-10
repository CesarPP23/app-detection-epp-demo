"""
Modelos de datos para detecciones EPP
Define la estructura de datos que se intercambia entre Raspberry Pi y backend
"""
from pydantic import BaseModel, Field
from typing import List, Dict, Optional
from datetime import datetime

class BoundingBox(BaseModel):
    """
    Coordenadas de la caja delimitadora de una detección
    """
    x1: int = Field(..., description="Coordenada X superior izquierda")
    y1: int = Field(..., description="Coordenada Y superior izquierda") 
    x2: int = Field(..., description="Coordenada X inferior derecha")
    y2: int = Field(..., description="Coordenada Y inferior derecha")

class Detection(BaseModel):
    """
    Información de una detección individual de objeto EPP
    """
    id: int = Field(..., description="ID único de la detección")
    class_id: int = Field(..., description="ID de la clase detectada")
    class_name: str = Field(..., description="Nombre de la clase (ej: mascarilla, guantes)")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confianza de la detección (0-1)")
    bbox: BoundingBox = Field(..., description="Coordenadas de la caja delimitadora")

class EPPSummary(BaseModel):
    """
    Resumen de EPP detectado en el frame
    """
    guantes: bool = Field(default=False, description="¿Se detectaron guantes?")
    cofia: bool = Field(default=False, description="¿Se detectó cofia?")
    mascarilla: bool = Field(default=False, description="¿Se detectó mascarilla?")
    bata: bool = Field(default=False, description="¿Se detectó bata?")

class ComplianceStatus(BaseModel):
    """
    Estado de cumplimiento de EPP
    """
    is_compliant: bool = Field(..., description="¿Cumple con todos los EPP requeridos?")
    missing_epp: List[str] = Field(default=[], description="Lista de EPP faltantes")
    compliance_percentage: float = Field(..., ge=0.0, le=100.0, description="Porcentaje de cumplimiento")

class DetectionResult(BaseModel):
    """
    Resultado completo de una detección desde Raspberry Pi
    Este es el modelo principal que se envía desde el Pi al backend
    """
    raspberry_id: str = Field(..., description="ID único del Raspberry Pi")
    timestamp: float = Field(..., description="Timestamp Unix de la detección")
    frame_shape: Optional[List[int]] = Field(default=None, description="Dimensiones del frame [height, width, channels]")
    total_detections: int = Field(..., ge=0, description="Número total de objetos detectados")
    detections: List[Detection] = Field(default=[], description="Lista de detecciones individuales")
    epp_summary: EPPSummary = Field(..., description="Resumen de EPP detectado")
    compliance_status: ComplianceStatus = Field(..., description="Estado de cumplimiento")
    inference_time: float = Field(default=0.0, ge=0.0, description="Tiempo de inferencia en segundos")

    class Config:
        """Configuración del modelo"""
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }
        schema_extra = {
            "example": {
                "raspberry_id": "raspberrypi001",
                "timestamp": 1703123456.789,
                "frame_shape": [480, 640, 3],
                "total_detections": 3,
                "detections": [
                    {
                        "id": 1,
                        "class_id": 0,
                        "class_name": "mascarilla",
                        "confidence": 0.95,
                        "bbox": {"x1": 100, "y1": 50, "x2": 200, "y2": 150}
                    }
                ],
                "epp_summary": {
                    "guantes": True,
                    "cofia": False,
                    "mascarilla": True,
                    "bata": True
                },
                "compliance_status": {
                    "is_compliant": False,
                    "missing_epp": ["cofia"],
                    "compliance_percentage": 75.0
                },
                "inference_time": 0.123
            }
        }