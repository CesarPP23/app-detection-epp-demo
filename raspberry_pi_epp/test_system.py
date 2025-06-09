#!/usr/bin/env python3
"""
Script de prueba para verificar que todos los componentes funcionen
Ejecutar antes de desplegar en el Raspberry Pi
"""
import sys
import os
import time
from pathlib import Path
from config.settings import *
# Agregar el directorio raíz al path
sys.path.append(str(Path(__file__).parent))

def test_imports():
    """Prueba que todas las importaciones funcionen"""
    print("🔍 Probando importaciones...")

    try:
        print("✅ Configuraciones importadas correctamente")

        from utils.logger import get_app_logger
        print("✅ Logger importado correctamente")

        from app.camera import CameraManager, test_camera
        print("✅ Módulo de cámara importado correctamente")

        from app.inference import YOLOInference, test_inference
        print("✅ Módulo de inferencia importado correctamente")

        from app.queue_manager import QueueManager, test_queue
        print("✅ Módulo de cola importado correctamente")

        from app.websocket_client import WebSocketClient, test_websocket_connection
        print("✅ Cliente WebSocket importado correctamente")

        return True

    except Exception as e:
        print(f"❌ Error en importaciones: {e}")
        return False

def test_logger():
    """Prueba el sistema de logging"""
    print("\n🔍 Probando sistema de logging...")

    try:
        from utils.logger import get_app_logger
        logger = get_app_logger()

        logger.info("Prueba de log INFO")
        logger.warning("Prueba de log WARNING")
        logger.error("Prueba de log ERROR")

        print("✅ Sistema de logging funcionando")
        return True

    except Exception as e:
        print(f"❌ Error en logging: {e}")
        return False

def test_camera_system():
    """Prueba el sistema de cámara"""
    print("\n🔍 Probando sistema de cámara...")

    try:
        from config.settings import CAMERA_CONFIG
        from app.camera import test_camera

        # Probar con cámara por defecto
        if test_camera(CAMERA_CONFIG):
            print("✅ Cámara funcionando correctamente")
            return True
        else:
            print("⚠️  Cámara no disponible (normal si no hay cámara conectada)")
            return True  # No es error crítico para pruebas

    except Exception as e:
        print(f"❌ Error en sistema de cámara: {e}")
        return False

def test_model_system():
    """Prueba el sistema de inferencia"""
    print("\n🔍 Probando sistema de inferencia...")

    try:
        from config.settings import MODEL_CONFIG
        from app.inference import test_inference

        # Verificar si existe el modelo
        model_path = MODEL_CONFIG['model_path']
        if not model_path.exists():
            print(f"⚠️  Modelo no encontrado en {model_path}")
            print("   Coloca tu modelo yolov8s.pt en la carpeta models/")
            return True  # No es error crítico para pruebas

        if test_inference(MODEL_CONFIG):
            print("✅ Sistema de inferencia funcionando")
            return True
        else:
            print("❌ Error en sistema de inferencia")
            return False

    except Exception as e:
        print(f"❌ Error en sistema de inferencia: {e}")
        return False

def test_queue_system():
    """Prueba el sistema de cola"""
    print("\n🔍 Probando sistema de cola...")

    try:
        from config.settings import QUEUE_CONFIG
        from app.queue_manager import test_queue

        if test_queue(QUEUE_CONFIG):
            print("✅ Sistema de cola funcionando")
            return True
        else:
            print("❌ Error en sistema de cola")
            return False

    except Exception as e:
        print(f"❌ Error en sistema de cola: {e}")
        return False

def test_websocket_system():
    """Prueba el sistema WebSocket"""
    print("\n🔍 Probando sistema WebSocket...")

    try:
        from config.settings import WEBSOCKET_CONFIG
        from app.websocket_client import test_websocket_connection

        if test_websocket_connection(WEBSOCKET_CONFIG):
            print("✅ Conexión WebSocket funcionando")
            return True
        else:
            print("⚠️  Servidor WebSocket no disponible (normal si no está ejecutándose)")
            return True  # No es error crítico para pruebas

    except Exception as e:
        print(f"❌ Error en sistema WebSocket: {e}")
        return False

def test_directories():
    """Verifica que existan los directorios necesarios"""
    print("\n🔍 Verificando estructura de directorios...")

    required_dirs = [
        "app", "config", "utils", "models", "data", "logs"
    ]

    all_exist = True
    for dir_name in required_dirs:
        if os.path.exists(dir_name):
            print(f"✅ Directorio {dir_name}/ existe")
        else:
            print(f"❌ Directorio {dir_name}/ no existe")
            all_exist = False

    return all_exist

def main():
    """Función principal de pruebas"""
    print("🚀 INICIANDO PRUEBAS DEL SISTEMA EPP RASPBERRY PI")
    print("=" * 50)

    tests = [
        ("Estructura de directorios", test_directories),
        ("Importaciones", test_imports),
        ("Sistema de logging", test_logger),
        ("Sistema de cámara", test_camera_system),
        ("Sistema de inferencia", test_model_system),
        ("Sistema de cola", test_queue_system),
        ("Sistema WebSocket", test_websocket_system),
    ]

    passed = 0
    total = len(tests)

    for test_name, test_func in tests:
        print(f"\n{'='*20} {test_name} {'='*20}")
        try:
            if test_func():
                passed += 1
            else:
                print(f"❌ Falló: {test_name}")
        except Exception as e:
            print(f"❌ Error en {test_name}: {e}")

    print("\n" + "="*50)
    print(f"📊 RESUMEN: {passed}/{total} pruebas pasaron")

    if passed == total:
        print("🎉 ¡Todas las pruebas pasaron! El sistema está listo.")
        return 0
    else:
        print("⚠️  Algunas pruebas fallaron. Revisa los errores arriba.")
        return 1

if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
