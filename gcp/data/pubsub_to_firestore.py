import base64
import json
from google.cloud import firestore

# Inicializa el cliente de Firestore
db = firestore.Client()

def pubsub_to_firestore(event, context):
    """
    Cloud Function disparada por mensajes de Pub/Sub.
    Guarda el JSON recibido en Firestore.
    """
    try:
        # Decodificar el mensaje de Pub/Sub
        if "data" in event:
            message = base64.b64decode(event["data"]).decode("utf-8")

            # Intentar parsear como JSON
            try:
                payload = json.loads(message)
            except json.JSONDecodeError:
                # Si no es un JSON válido, lo guarda como texto
                payload = {"raw_message": message}
        else:
            payload = {"raw_event": str(event)}

        # Guardar en Firestore (colección genérica iot_data, ID automático)
        doc_ref = db.collection("iot_data").document()
        doc_ref.set(payload)

        print(f"✅ Insertado en Firestore con ID: {doc_ref.id}")

    except Exception as e:
        print(f"❌ Error procesando mensaje: {e}")