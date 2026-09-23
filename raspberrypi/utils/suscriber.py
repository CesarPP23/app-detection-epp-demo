import time
from google.cloud import pubsub_v1

# --- Configuración ---
# Reemplaza con tus IDs
GCP_PROJECT_ID = "epp-detection-v1"
SUBSCRIPTION_ID = "yolo-detections-debugger"

# Esta función se ejecutará por cada mensaje recibido
def callback(message: pubsub_v1.subscriber.message.Message) -> None:
    """Imprime el mensaje recibido y envía el acuse de recibo."""

    print(f"Recibido nuevo mensaje:")
    # Imprimimos el contenido del mensaje (el JSON)
    print(message.data.decode("utf-8"))

    # Enviamos el "acuse de recibo" para que Pub/Sub no vuelva a enviar este mensaje.
    message.ack()
    print("-" * 20) # Separador para ver claramente cada mensaje

# Inicializa el cliente de suscriptor
subscriber = pubsub_v1.SubscriberClient()
# Crea la ruta completa a la suscripción
subscription_path = subscriber.subscription_path(GCP_PROJECT_ID, SUBSCRIPTION_ID)

# Abre la suscripción y le dice que use nuestra función 'callback'
streaming_pull_future = subscriber.subscribe(subscription_path, callback=callback)
print(f"Escuchando mensajes en la suscripción '{SUBSCRIPTION_ID}'... Presiona Ctrl+C para detener.")

# Mantiene el script corriendo para que pueda seguir escuchando
try:
    streaming_pull_future.result()
except KeyboardInterrupt:
    streaming_pull_future.cancel()
    print("Suscripción detenida.")