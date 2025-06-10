import asyncio
import websockets
import json
import time
from config.settings import WEBSOCKET_CONFIG, DEVICE_ID

async def test_full_system():
    uri = WEBSOCKET_CONFIG["server_url"]
    
    print(f"🔌 Conectando a {uri}...")
    
    try:
        async with websockets.connect(uri) as websocket:
            print("✅ ¡Conectado al backend!")
            
            # 1. Enviar estado del dispositivo
            status_message = {
                "type": "status",
                "data": {
                    "device_id": DEVICE_ID,
                    "status": "online",
                    "ip_address": "192.168.1.100",
                    "version": "1.0.0",
                    "model_loaded": True,
                    "camera_source": "prueba2.mp4",
                    "timestamp": time.time()
                }
            }
            
            print("📤 Enviando estado del dispositivo...")
            await websocket.send(json.dumps(status_message))
            response = await websocket.recv()
            print(f"📥 Estado: {json.loads(response)}")
            
            # 2. Enviar detección con EPP
            detection_message = {
                "type": "detection",
                "data": {
                    "raspberry_id": DEVICE_ID,
                    "timestamp": time.time(),
                    "total_detections": 4,
                    "detections": [
                        {
                            "class_name": "person",
                            "confidence": 0.95,
                            "bbox": [100, 150, 200, 300]
                        },
                        {
                            "class_name": "mascarilla",
                            "confidence": 0.88,
                            "bbox": [120, 160, 180, 200]
                        },
                        {
                            "class_name": "bata",
                            "confidence": 0.82,
                            "bbox": [110, 180, 190, 280]
                        },
                        {
                            "class_name": "guantes",
                            "confidence": 0.79,
                            "bbox": [130, 250, 170, 290]
                        }
                    ],
                    "compliance_status": {
                        "is_compliant": False,
                        "compliance_percentage": 75.0,
                        "missing_epp": ["cofia"],
                        "detected_epp": ["mascarilla", "bata", "guantes"]
                    }
                }
            }
            
            print("📤 Enviando detección EPP...")
            await websocket.send(json.dumps(detection_message))
            response = await websocket.recv()
            detection_response = json.loads(response)
            print(f"📥 Detección guardada:")
            print(f"   Doc ID: {detection_response.get('doc_id')}")
            print(f"   Compliance: {detection_response.get('compliance')}%")
            print(f"   Status: {detection_response.get('status')}")
            
            print("🎉 ¡Sistema completo funcionando!")
            
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    asyncio.run(test_full_system())