"""
Modelos de datos para el sistema EPP
"""
from .detection import DetectionResult, Detection, BoundingBox, EPPSummary, ComplianceStatus

__all__ = [
    "DetectionResult",
    "Detection", 
    "BoundingBox",
    "EPPSummary",
    "ComplianceStatus"
]