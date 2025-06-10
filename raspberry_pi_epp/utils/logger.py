"""
Sistema de logging para el Raspberry Pi
Este módulo configura los logs del sistema para registrar eventos importantes
"""
import logging
import logging.handlers
import os
from pathlib import Path

def setup_logger(name: str, log_file: Path, level: str = "INFO"):
    """
    Configura un logger con rotación de archivos

    Args:
        name: Nombre del logger
        log_file: Ruta del archivo de log
        level: Nivel de logging (DEBUG, INFO, WARNING, ERROR)

    Returns:
        Logger configurado
    """
    # Crear directorio de logs si no existe
    log_file.parent.mkdir(parents=True, exist_ok=True)

    # Configurar el logger
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper()))

    # Evitar duplicar handlers si ya existe
    if logger.handlers:
        return logger

    # Formato de los mensajes de log
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Handler para archivo con rotación (cuando el archivo sea muy grande)
    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=10*1024*1024,  # 10MB
        backupCount=5,  # Mantener 5 archivos de respaldo
        encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Handler para consola (para ver logs en tiempo real)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger

# Logger principal de la aplicación
def get_app_logger():
    """Obtiene el logger principal de la aplicación"""
    from config.settings import LOGGING_CONFIG

    return setup_logger(
        name="raspberry_pi_epp",
        log_file=LOGGING_CONFIG["file_path"],
        level=LOGGING_CONFIG["level"]
    )

# Función para registrar eventos de detección
def log_detection_event(logger, detection_data):
    """
    Registra un evento de detección en los logs

    Args:
        logger: Logger a usar
        detection_data: Datos de la detección
    """
    logger.info(f"Detección: {detection_data}")

# Función para registrar errores de conexión
def log_connection_error(logger, error_msg):
    """
    Registra errores de conexión

    Args:
        logger: Logger a usar
        error_msg: Mensaje de error
    """
    logger.error(f"Error de conexión: {error_msg}")

# Función para registrar estado del sistema
def log_system_status(logger, cpu_usage, memory_usage, temperature=None):
    """
    Registra el estado del sistema

    Args:
        logger: Logger a usar
        cpu_usage: Uso de CPU en porcentaje
        memory_usage: Uso de memoria en porcentaje
        temperature: Temperatura del sistema (opcional)
    """
    status_msg = f"Sistema - CPU: {cpu_usage}%, RAM: {memory_usage}%"
    if temperature:
        status_msg += f", Temp: {temperature}°C"

    logger.info(status_msg)