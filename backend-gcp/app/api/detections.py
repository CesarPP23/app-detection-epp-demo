"""
API REST para manejo de detecciones EPP
Endpoints SOLO para que Streamlit consulte datos históricos
NO para recibir datos del Raspberry Pi (eso es por WebSocket)
"""
from fastapi import APIRouter, Query
from app.models.detection import DetectionResult
from app.database.firestore_client import firestore_client
from app.utils.logger import get_logger
from typing import Optional, List
from datetime import datetime, timedelta
import time

router = APIRouter()
logger = get_logger(__name__)

@router.get("/")
async def get_detections(
    raspberry_id: Optional[str] = Query(None, description="Filtrar por ID de Raspberry Pi"),
    limit: int = Query(100, ge=1, le=1000, description="Número máximo de resultados"),
    start_date: Optional[str] = Query(None, description="Fecha inicio (timestamp o YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="Fecha fin (timestamp o YYYY-MM-DD)"),
    only_non_compliant: bool = Query(False, description="Solo detecciones no conformes")
):
    """
    Obtiene detecciones históricas para Streamlit
    SOLO para consultas, NO para recibir datos del Raspberry Pi
    """
    try:
        # Convertir fechas si es necesario
        start_timestamp = None
        end_timestamp = None
        
        if start_date:
            try:
                # Intentar como timestamp primero
                start_timestamp = str(float(start_date))
            except ValueError:
                # Si falla, intentar como fecha YYYY-MM-DD
                try:
                    dt = datetime.strptime(start_date, '%Y-%m-%d')
                    start_timestamp = str(dt.timestamp())
                except ValueError:
                    return {
                        "status": "error",
                        "message": "Formato de start_date inválido. Use timestamp o YYYY-MM-DD"
                    }
        
        if end_date:
            try:
                end_timestamp = str(float(end_date))
            except ValueError:
                try:
                    dt = datetime.strptime(end_date, '%Y-%m-%d')
                    # Agregar 23:59:59 para incluir todo el día
                    dt = dt.replace(hour=23, minute=59, second=59)
                    end_timestamp = str(dt.timestamp())
                except ValueError:
                    return {
                        "status": "error",
                        "message": "Formato de end_date inválido. Use timestamp o YYYY-MM-DD"
                    }
        
        # Obtener detecciones
        detections = await firestore_client.get_detections(
            raspberry_id=raspberry_id,
            limit=limit,
            start_date=start_timestamp,
            end_date=end_timestamp,
            only_non_compliant=only_non_compliant
        )
        
        # Agregar metadatos útiles para Streamlit
        for detection in detections:
            if 'timestamp' in detection:
                detection['formatted_date'] = datetime.fromtimestamp(
                    detection['timestamp']
                ).strftime('%Y-%m-%d %H:%M:%S')
        
        return {
            "status": "success",
            "count": len(detections),
            "filters": {
                "raspberry_id": raspberry_id,
                "start_date": start_date,
                "end_date": end_date,
                "only_non_compliant": only_non_compliant,
                "limit": limit
            },
            "detections": detections
        }
        
    except Exception as e:
        logger.error(f"Error obteniendo detecciones: {e}")
        return {
            "status": "error",
            "message": f"Error interno: {str(e)}"
        }

@router.get("/stats/{raspberry_id}")
async def get_raspberry_stats(
    raspberry_id: str,
    hours: int = Query(24, ge=1, le=168, description="Horas hacia atrás para estadísticas")
):
    """
    Obtiene estadísticas detalladas de un Raspberry Pi
    SOLO para consultas de Streamlit
    """
    try:
        stats = await firestore_client.get_raspberry_stats(raspberry_id, hours)
        
        return {
            "status": "success",
            "stats": stats
        }
        
    except Exception as e:
        logger.error(f"Error obteniendo estadísticas de {raspberry_id}: {e}")
        return {
            "status": "error",
            "message": f"Error interno: {str(e)}"
        }

@router.get("/summary/daily")
async def get_daily_summary(
    date: Optional[str] = Query(None, description="Fecha en formato YYYY-MM-DD (por defecto hoy)")
):
    """
    Obtiene resumen diario de todas las detecciones
    SOLO para consultas de Streamlit
    """
    try:
        summary = await firestore_client.get_daily_summary(date)
        
        return {
            "status": "success",
            "summary": summary
        }
        
    except Exception as e:
        logger.error(f"Error obteniendo resumen diario: {e}")
        return {
            "status": "error",
            "message": f"Error interno: {str(e)}"
        }

@router.get("/compliance/report")
async def get_compliance_report(
    raspberry_id: Optional[str] = Query(None, description="Filtrar por Raspberry Pi"),
    days: int = Query(7, ge=1, le=30, description="Días hacia atrás para el reporte")
):
    """
    Genera reporte de cumplimiento EPP
    SOLO para consultas de Streamlit
    """
    try:
        # Calcular rango de fechas
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)
        
        # Obtener detecciones del período
        detections = await firestore_client.get_detections(
            raspberry_id=raspberry_id,
            start_date=str(start_date.timestamp()),
            end_date=str(end_date.timestamp()),
            limit=10000  # Límite alto para análisis completo
        )
        
        # Analizar cumplimiento
        total_detections = len(detections)
        compliant_detections = sum(1 for d in detections if d.get('compliance_status', {}).get('is_compliant', False))
        
        # Agrupar por día
        daily_stats = {}
        epp_missing_count = {}
        
        for detection in detections:
            # Agrupar por día
            timestamp = detection.get('timestamp', 0)
            day = datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d')
            
            if day not in daily_stats:
                daily_stats[day] = {'total': 0, 'compliant': 0}
            
            daily_stats[day]['total'] += 1
            if detection.get('compliance_status', {}).get('is_compliant', False):
                daily_stats[day]['compliant'] += 1
            
            # Contar EPP faltantes
            missing_epp = detection.get('compliance_status', {}).get('missing_epp', [])
            for epp in missing_epp:
                epp_missing_count[epp] = epp_missing_count.get(epp, 0) + 1
        
        # Calcular porcentajes diarios
        for day in daily_stats:
            stats = daily_stats[day]
            stats['compliance_rate'] = (stats['compliant'] / stats['total'] * 100) if stats['total'] > 0 else 0
        
        report = {
            "period": {
                "start_date": start_date.strftime('%Y-%m-%d'),
                "end_date": end_date.strftime('%Y-%m-%d'),
                "days": days
            },
            "overall": {
                "total_detections": total_detections,
                "compliant_detections": compliant_detections,
                "compliance_rate": (compliant_detections / total_detections * 100) if total_detections > 0 else 0
            },
            "daily_breakdown": daily_stats,
            "most_missing_epp": dict(sorted(epp_missing_count.items(), key=lambda x: x[1], reverse=True)),
            "raspberry_id": raspberry_id,
            "generated_at": datetime.utcnow().isoformat()
        }
        
        return {
            "status": "success",
            "report": report
        }
        
    except Exception as e:
        logger.error(f"Error generando reporte de cumplimiento: {e}")
        return {
            "status": "error",
            "message": f"Error interno: {str(e)}"
        }