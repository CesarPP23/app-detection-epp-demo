"""
Cliente de Firestore para operaciones de base de datos
Maneja todas las interacciones con Google Cloud Firestore
"""
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from app.utils.logger import get_logger, log_firestore_operation
from app.models.detection import DetectionResult
from app.core.settings import settings
from typing import List, Dict, Optional, Any
import os
from datetime import datetime, timedelta
import time

class FirestoreClient:
    """
    Cliente para interactuar con Google Cloud Firestore
    """
        
    def __init__(self):
        """Inicializa el cliente de Firestore"""
        # Inicializar logger PRIMERO
        self.logger = get_logger(__name__)
        
        try:
            # Las credenciales ya están configuradas en settings.py
            self.logger.info(f"Inicializando Firestore para proyecto: {settings.google_cloud_project}")
            self.logger.info(f"Base de datos: {settings.firestore_database}")
            
            # Inicializar cliente Firestore con base de datos específica
            self.db = firestore.Client(
                project=settings.google_cloud_project,
                database=settings.firestore_database  # ← AGREGAR ESTA LÍNEA
            )
            self.logger.info(f"Cliente Firestore inicializado correctamente para BD: {settings.firestore_database}")
            
            # Nombres de colecciones desde settings
            self.DETECTIONS_COLLECTION = settings.detections_collection
            self.RASPBERRIES_COLLECTION = settings.raspberries_collection
            self.STATS_COLLECTION = settings.stats_collection
            
        except Exception as e:
            self.logger.error(f"Error inicializando Firestore: {e}")
            # En lugar de fallar, crear un cliente mock para desarrollo
            if settings.is_development():
                self.db = None
                self.logger.warning("Firestore no disponible - usando modo mock para desarrollo")
            else:
                raise e

    async def save_detection(self, detection_data: DetectionResult) -> str:
        """
        Guarda una detección en Firestore
        """
        try:
            if not self.db:
                # Modo mock para desarrollo sin Firestore
                mock_id = f"mock_{int(time.time())}"
                self.logger.info(f"MOCK: Detección guardada con ID {mock_id}")
                return mock_id
            
            # Convertir a diccionario
            data = detection_data.dict()
            
            # Agregar metadatos del servidor
            data['server_timestamp'] = firestore.SERVER_TIMESTAMP
            data['created_at'] = datetime.utcnow().isoformat()
            data['date'] = datetime.fromtimestamp(detection_data.timestamp).strftime('%Y-%m-%d')
            data['hour'] = datetime.fromtimestamp(detection_data.timestamp).hour
            
            # Guardar en colección 'detections'
            doc_ref = self.db.collection(self.DETECTIONS_COLLECTION).document()
            doc_ref.set(data)
            
            # Log de operación exitosa
            log_firestore_operation("save", doc_ref.id)
            
            return doc_ref.id
            
        except Exception as e:
            log_firestore_operation("save", error=str(e))
            # En lugar de fallar, retornar ID mock
            mock_id = f"error_{int(time.time())}"
            self.logger.warning(f"Error guardando en Firestore, usando ID mock: {mock_id}")
            return mock_id

    async def get_connection_status(self) -> Dict:
        """
        Verifica el estado de conexión con Firestore
        """
        try:
            if not self.db:
                return {
                    "status": "mock_mode",
                    "message": "Firestore no disponible - usando modo mock",
                    "timestamp": datetime.utcnow().isoformat()
                }
            
            # Hacer una consulta simple para verificar conectividad
            # Intentar listar colecciones para verificar acceso
            collections = list(self.db.collections(page_size=1))
            
            return {
                "status": "connected",
                "project": settings.google_cloud_project,
                "database": settings.firestore_database,  # ← AGREGAR ESTA LÍNEA
                "collections_accessible": True,
                "timestamp": datetime.utcnow().isoformat()
            }
        except Exception as e:
            return {
                "status": "error",
                "project": settings.google_cloud_project,
                "database": settings.firestore_database,  # ← AGREGAR ESTA LÍNEA
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat()
            }
    async def get_raspberry_stats(self, raspberry_id: str, hours: int = 24) -> Dict:
        """
        Obtiene estadísticas de un Raspberry Pi
        """
        try:
            if not self.db:
                # Retornar stats mock
                return {
                    'raspberry_id': raspberry_id,
                    'period_hours': hours,
                    'total_detections': 10,
                    'compliant_detections': 8,
                    'compliance_rate_percent': 80.0,
                    'average_compliance_percent': 85.5,
                    'generated_at': datetime.utcnow().isoformat()
                }
            
            # Calcular timestamp de inicio
            start_time = time.time() - (hours * 60 * 60)
            
            # Consultar detecciones en el período
            query = self.db.collection(self.DETECTIONS_COLLECTION)\
                .where(filter=FieldFilter("raspberry_id", "==", raspberry_id))\
                .where(filter=FieldFilter("timestamp", ">=", start_time))
            
            docs = list(query.stream())
            
            # Calcular estadísticas
            total_detections = len(docs)
            compliant_count = 0
            total_objects = 0
            compliance_sum = 0
            
            for doc in docs:
                data = doc.to_dict()
                if data.get('compliance_status', {}).get('is_compliant', False):
                    compliant_count += 1
                
                total_objects += data.get('total_detections', 0)
                compliance_sum += data.get('compliance_status', {}).get('compliance_percentage', 0)
            
            # Calcular promedios
            compliance_rate = (compliant_count / total_detections * 100) if total_detections > 0 else 0
            avg_compliance = (compliance_sum / total_detections) if total_detections > 0 else 0
            avg_objects_per_detection = (total_objects / total_detections) if total_detections > 0 else 0
            
            # Obtener última detección
            last_detection = None
            if docs:
                last_doc = max(docs, key=lambda x: x.to_dict().get('timestamp', 0))
                last_detection = last_doc.to_dict().get('timestamp')
            
            stats = {
                'raspberry_id': raspberry_id,
                'period_hours': hours,
                'total_detections': total_detections,
                'compliant_detections': compliant_count,
                'compliance_rate_percent': round(compliance_rate, 2),
                'average_compliance_percent': round(avg_compliance, 2),
                'total_objects_detected': total_objects,
                'average_objects_per_detection': round(avg_objects_per_detection, 2),
                'last_detection_timestamp': last_detection,
                'last_detection_ago_minutes': round((time.time() - last_detection) / 60, 1) if last_detection else None,
                'generated_at': datetime.utcnow().isoformat()
            }
            
            log_firestore_operation("stats", details=f"{raspberry_id} - {total_detections} detecciones")
            return stats
            
        except Exception as e:
            log_firestore_operation("stats", error=str(e))
            # Retornar stats básicos en caso de error
            return {
                'raspberry_id': raspberry_id,
                'period_hours': hours,
                'total_detections': 0,
                'error': str(e),
                'generated_at': datetime.utcnow().isoformat()
            }

    async def get_daily_summary(self, date: str = None) -> Dict:
        """
        Obtiene resumen diario de todas las detecciones
        """
        try:
            if not self.db:
                return {
                    'date': date or datetime.now().strftime('%Y-%m-%d'),
                    'total_detections': 5,
                    'total_compliant': 4,
                    'overall_compliance_rate': 80.0,
                    'generated_at': datetime.utcnow().isoformat()
                }
            
            if not date:
                date = datetime.now().strftime('%Y-%m-%d')
            
            # Consultar detecciones del día
            query = self.db.collection(self.DETECTIONS_COLLECTION)\
                .where(filter=FieldFilter("date", "==", date))
            
            docs = list(query.stream())
            
            # Agrupar por Raspberry Pi
            raspberry_stats = {}
            total_detections = len(docs)
            total_compliant = 0
            
            for doc in docs:
                data = doc.to_dict()
                pi_id = data.get('raspberry_id', 'unknown')
                
                if pi_id not in raspberry_stats:
                    raspberry_stats[pi_id] = {
                        'detections': 0,
                        'compliant': 0,
                        'total_objects': 0
                    }
                
                raspberry_stats[pi_id]['detections'] += 1
                raspberry_stats[pi_id]['total_objects'] += data.get('total_detections', 0)
                
                if data.get('compliance_status', {}).get('is_compliant', False):
                    raspberry_stats[pi_id]['compliant'] += 1
                    total_compliant += 1
            
            # Calcular porcentajes
            for pi_id in raspberry_stats:
                stats = raspberry_stats[pi_id]
                stats['compliance_rate'] = (stats['compliant'] / stats['detections'] * 100) if stats['detections'] > 0 else 0
            
            summary = {
                'date': date,
                'total_detections': total_detections,
                'total_compliant': total_compliant,
                'overall_compliance_rate': (total_compliant / total_detections * 100) if total_detections > 0 else 0,
                'raspberry_stats': raspberry_stats,
                'generated_at': datetime.utcnow().isoformat()
            }
            
            log_firestore_operation("daily_summary", details=f"{date} - {total_detections} detecciones")
            return summary
            
        except Exception as e:
            log_firestore_operation("daily_summary", error=str(e))
            return {
                'date': date or datetime.now().strftime('%Y-%m-%d'),
                'error': str(e),
                'generated_at': datetime.utcnow().isoformat()
            }

    async def register_raspberry(self, raspberry_id: str, info: Dict) -> str:
        """
        Registra o actualiza información de un Raspberry Pi
        """
        try:
            if not self.db:
                self.logger.info(f"MOCK: Raspberry {raspberry_id} registrado")
                return raspberry_id
            
            data = {
                **info,
                'raspberry_id': raspberry_id,
                'last_seen': firestore.SERVER_TIMESTAMP,
                'updated_at': datetime.utcnow().isoformat()
            }
            
            # Usar el raspberry_id como ID del documento
            doc_ref = self.db.collection(self.RASPBERRIES_COLLECTION).document(raspberry_id)
            doc_ref.set(data, merge=True)  # merge=True para actualizar campos existentes
            
            log_firestore_operation("register", raspberry_id)
            return raspberry_id
            
        except Exception as e:
            log_firestore_operation("register", error=str(e))
            return raspberry_id

    def get_connection_status(self) -> Dict:
        """
        Verifica el estado de conexión con Firestore
        """
        try:
            if not self.db:
                return {
                    "status": "mock_mode",
                    "message": "Firestore no disponible - usando modo mock",
                    "timestamp": datetime.utcnow().isoformat()
                }
            
            # Hacer una consulta simple para verificar conectividad
            self.db.collection(self.DETECTIONS_COLLECTION).limit(1).get()
            return {
                "status": "connected",
                "project": "dogwood-vision-459716-k3",
                "timestamp": datetime.utcnow().isoformat()
            }
        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat()
            }

# Instancia global del cliente
firestore_client = FirestoreClient()