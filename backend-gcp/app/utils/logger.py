"""
Sistema de logging centralizado
Configura logs consistentes en toda la aplicación
"""
import logging
import sys
from typing import Optional
from datetime import datetime

def get_logger(name: Optional[str] = None) -> logging.Logger:
    """
    Configura y retorna un logger con formato consistente
    
    Args:
        name: Nombre del logger (generalmente __name__ del módulo)
        
    Returns:
        Logger configurado
    """
    
    logger = logging.getLogger(name or __name__)
    
    # Evitar duplicar handlers si ya está configurado
    if not logger.handlers:
        # Crear handler para consola
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        
        # Formato detallado del log
        formatter = logging.Formatter(
            fmt='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        console_handler.setFormatter(formatter)
        
        # Agregar handler al logger
        logger.addHandler(console_handler)
        logger.setLevel(logging.INFO)
        
        # Evitar propagación a loggers padre
        logger.propagate = False
    
    return logger

def log_detection_received(raspberry_id: str, compliance: float, total_detections: int):
    """
    Log especializado para detecciones recibidas
    
    Args:
        raspberry_id: ID del Raspberry Pi
        compliance: Porcentaje de cumplimiento
        total_detections: Número de detecciones
    """
    logger = get_logger("detection")
    logger.info(
        f"DETECCIÓN | Pi: {raspberry_id} | "
        f"Objetos: {total_detections} | "
        f"Cumplimiento: {compliance:.1f}%"
    )

def log_websocket_event(event: str, raspberry_id: str = None, details: str = None):
    """
    Log especializado para eventos WebSocket
    
    Args:
        event: Tipo de evento (connect, disconnect, error, etc.)
        raspberry_id: ID del Raspberry Pi (opcional)
        details: Detalles adicionales (opcional)
    """
    logger = get_logger("websocket")
    message = f"WEBSOCKET | {event.upper()}"
    
    if raspberry_id:
        message += f" | Pi: {raspberry_id}"
    
    if details:
        message += f" | {details}"
    
    logger.info(message)

def log_firestore_operation(operation: str, doc_id: str = None, error: str = None):
    """
    Log especializado para operaciones de Firestore
    
    Args:
        operation: Tipo de operación (save, read, delete, etc.)
        doc_id: ID del documento (opcional)
        error: Mensaje de error (opcional)
    """
    logger = get_logger("firestore")
    
    if error:
        logger.error(f"FIRESTORE | {operation.upper()} | ERROR: {error}")
    else:
        message = f"FIRESTORE | {operation.upper()}"
        if doc_id:
            message += f" | Doc: {doc_id}"
        logger.info(message)