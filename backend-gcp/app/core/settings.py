"""
Configuración centralizada de la aplicación
Maneja variables de entorno y configuraciones
"""
from pydantic_settings import BaseSettings
from typing import List, Optional
import os
from pathlib import Path

class Settings(BaseSettings):
    """
    Configuración principal de la aplicación
    """
    
    # === Google Cloud Configuration ===
    google_application_credentials: str
    google_cloud_project: str = "dogwood-vision-459716-k3"
    firestore_database: str = "raspberrypi001"
    
    # === FastAPI Configuration ===
    environment: str = "development"
    debug: bool = True
    host: str = "0.0.0.0"
    port: int = 8000
    
    # === CORS Configuration ===
    cors_origins: List[str] = [
        "http://localhost:3000",
        "http://localhost:8501", 
        "http://127.0.0.1:8501"
    ]
    
    # === Logging Configuration ===
    log_level: str = "INFO"
    log_format: str = "detailed"
    
    # === WebSocket Configuration ===
    websocket_timeout: int = 300
    max_connections: int = 100
    
    # === Firestore Collections ===
    detections_collection: str = "detections"
    raspberries_collection: str = "raspberries"
    stats_collection: str = "stats"
    
    # === Development/Production flags ===
    use_firestore_emulator: bool = False
    firestore_emulator_host: str = "localhost:8080"
    
    # === App Metadata ===
    app_name: str = "Backend EPP Detection"
    app_version: str = "1.0.0"
    app_description: str = "Backend para sistema de detección de EPP - Comunicación WebSocket con Raspberry Pi"
    
    class Config:
        # Buscar archivo .env en el directorio raíz del proyecto
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
        
        # Permitir campos extra del .env
        extra = "allow"
    
    def setup_google_credentials(self):
        """
        Configura las credenciales de Google Cloud
        """
        if self.use_firestore_emulator:
            # En modo emulador, no necesitamos credenciales reales
            self.setup_firestore_emulator()
            return True
            
        if self.google_application_credentials:
            # Convertir a ruta absoluta si es relativa
            creds_path = Path(self.google_application_credentials)
            if not creds_path.is_absolute():
                # Si es relativa, buscar desde el directorio del proyecto
                project_root = Path(__file__).parent.parent.parent
                creds_path = project_root / self.google_application_credentials
            
            if creds_path.exists():
                os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = str(creds_path)
                return True
            else:
                if self.is_development():
                    print(f"Warning: Archivo de credenciales no encontrado: {creds_path}")
                    print("Usando modo mock para desarrollo")
                    return False
                else:
                    raise FileNotFoundError(f"Archivo de credenciales no encontrado: {creds_path}")
        return False
    
    def setup_firestore_emulator(self):
        """
        Configura el emulador de Firestore si está habilitado
        """
        if self.use_firestore_emulator:
            os.environ['FIRESTORE_EMULATOR_HOST'] = self.firestore_emulator_host
            return True
        return False
    
    def get_cors_origins(self) -> List[str]:
        """
        Obtiene los orígenes CORS permitidos
        """
        if self.environment == "production":
            # En producción, ser más restrictivo
            return [origin for origin in self.cors_origins if not origin.startswith("http://localhost")]
        return self.cors_origins
    
    def is_development(self) -> bool:
        """
        Verifica si estamos en modo desarrollo
        """
        return self.environment.lower() in ["development", "dev", "local"]
    
    def is_production(self) -> bool:
        """
        Verifica si estamos en modo producción
        """
        return self.environment.lower() in ["production", "prod"]

# Instancia global de configuración
settings = Settings()

# Configurar credenciales automáticamente al importar
try:
    settings.setup_google_credentials()
    if settings.use_firestore_emulator:
        settings.setup_firestore_emulator()
except Exception as e:
    print(f"Warning: Error configurando credenciales: {e}")