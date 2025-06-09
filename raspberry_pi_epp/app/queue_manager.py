"""
Módulo para manejo de cola persistente
Este módulo maneja la cola de metadatos cuando no hay conexión a internet
"""
import sqlite3
import json
import time
import threading
from typing import Dict, List, Optional
from pathlib import Path
from utils.logger import get_app_logger

class QueueManager:
    """
    Clase para manejar la cola persistente de metadatos
    Usa SQLite para almacenar datos cuando no hay conexión
    """

    def __init__(self, queue_config: dict):
        """
        Inicializa el manejador de cola

        Args:
            queue_config: Configuración de la cola desde settings.py
        """
        self.logger = get_app_logger()
        self.config = queue_config
        self.db_path = queue_config['db_path']
        self.max_queue_size = queue_config['max_queue_size']
        self.lock = threading.Lock()  # Para acceso seguro a la base de datos

        # Crear directorio si no existe
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Inicializar base de datos
        self._initialize_database()

    def _initialize_database(self):
        """Inicializa la base de datos SQLite"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                # Crear tabla para la cola si no existe
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS detection_queue (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp REAL NOT NULL,
                        device_id TEXT NOT NULL,
                        detection_data TEXT NOT NULL,
                        retry_count INTEGER DEFAULT 0,
                        created_at REAL DEFAULT (julianday('now'))
                    )
                """)

                # Crear índice para mejorar rendimiento
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_timestamp 
                    ON detection_queue(timestamp)
                """)

                conn.commit()

            self.logger.info(f"Base de datos de cola inicializada: {self.db_path}")

        except Exception as e:
            self.logger.error(f"Error inicializando base de datos: {e}")
            raise

    def add_to_queue(self, detection_data: Dict, device_id: str = "raspberry_pi_001") -> bool:
        """
        Agrega datos de detección a la cola

        Args:
            detection_data: Datos de detección a almacenar
            device_id: ID del dispositivo

        Returns:
            True si se agregó exitosamente, False en caso contrario
        """
        try:
            with self.lock:
                # Verificar tamaño de la cola
                if self.get_queue_size() >= self.max_queue_size:
                    self.logger.warning("Cola llena, eliminando elementos más antiguos")
                    self._cleanup_old_entries()

                # Preparar datos
                timestamp = detection_data.get('timestamp', time.time())
                data_json = json.dumps(detection_data)

                # Insertar en base de datos
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        INSERT INTO detection_queue 
                        (timestamp, device_id, detection_data, retry_count)
                        VALUES (?, ?, ?, 0)
                    """, (timestamp, device_id, data_json))
                    conn.commit()

                self.logger.debug(f"Datos agregados a la cola: {device_id}")
                return True

        except Exception as e:
            self.logger.error(f"Error agregando a la cola: {e}")
            return False

    def get_next_batch(self, batch_size: int = 10) -> List[Dict]:
        """
        Obtiene el siguiente lote de datos para enviar

        Args:
            batch_size: Número máximo de elementos a obtener

        Returns:
            Lista de elementos de la cola
        """
        try:
            with self.lock:
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT id, timestamp, device_id, detection_data, retry_count
                        FROM detection_queue
                        ORDER BY timestamp ASC
                        LIMIT ?
                    """, (batch_size,))

                    rows = cursor.fetchall()

                    # Convertir a lista de diccionarios
                    batch = []
                    for row in rows:
                        item = {
                            'queue_id': row[0],
                            'timestamp': row[1],
                            'device_id': row[2],
                            'detection_data': json.loads(row[3]),
                            'retry_count': row[4]
                        }
                        batch.append(item)

                    return batch

        except Exception as e:
            self.logger.error(f"Error obteniendo lote de la cola: {e}")
            return []

    def mark_as_sent(self, queue_ids: List[int]) -> bool:
        """
        Marca elementos como enviados exitosamente (los elimina de la cola)

        Args:
            queue_ids: Lista de IDs de cola a eliminar

        Returns:
            True si se eliminaron exitosamente
        """
        try:
            with self.lock:
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()

                    # Eliminar elementos enviados
                    placeholders = ','.join('?' * len(queue_ids))
                    cursor.execute(f"""
                        DELETE FROM detection_queue 
                        WHERE id IN ({placeholders})
                    """, queue_ids)

                    deleted_count = cursor.rowcount
                    conn.commit()

                    self.logger.debug(f"Eliminados {deleted_count} elementos de la cola")
                    return True

        except Exception as e:
            self.logger.error(f"Error marcando como enviados: {e}")
            return False

    def get_queue_size(self) -> int:
        """
        Obtiene el tamaño actual de la cola

        Returns:
            Número de elementos en la cola
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT COUNT(*) FROM detection_queue')
                return cursor.fetchone()[0]

        except Exception as e:
            self.logger.error(f"Error obteniendo tamaño de cola: {e}")
            return 0

    def clear_queue(self) -> bool:
        """
        Limpia completamente la cola (usar con cuidado)

        Returns:
            True si se limpió exitosamente
        """
        try:
            with self.lock:
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute('DELETE FROM detection_queue')
                    conn.commit()

                self.logger.warning("Cola completamente limpiada")
                return True

        except Exception as e:
            self.logger.error(f"Error limpiando cola: {e}")
            return False

    def _cleanup_old_entries(self, keep_count: Optional[int] = None):
        """Limpia entradas antiguas de la cola"""
        if keep_count is None:
            keep_count = int(self.max_queue_size * 0.8)

        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    DELETE FROM detection_queue 
                    WHERE id NOT IN (
                        SELECT id FROM detection_queue 
                        ORDER BY timestamp DESC 
                        LIMIT ?
                    )
                """, (keep_count,))

                deleted_count = cursor.rowcount
                conn.commit()

                self.logger.info(f"Limpieza: eliminadas {deleted_count} entradas antiguas")

        except Exception as e:
            self.logger.error(f"Error en limpieza de cola: {e}")
