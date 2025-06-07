import os
from datetime import datetime
import cv2
import requests
import time
from config import SERVER_URL, CAPTURE_INTERVAL, DEBUG

PENDING_DIR = r'C:\Users\cesar\app-detection-epp\client\pending_frames'
os.makedirs(PENDING_DIR, exist_ok=True)

def send_frame(image_path):
    with open(image_path, 'rb') as f:
        try:
            response = requests.post(SERVER_URL, files={'image': f}, timeout=5)
            if response.status_code == 200:
                print(f"Enviado {image_path} con éxito")
                return True
            else:
                print(f"Error en el servidor: {response.status_code}")
                return False
        except requests.exceptions.RequestException as e:
            print(f"Error de conexión: {e}")
            return False

def send_pending_frames():
    pending_files = sorted(os.listdir(PENDING_DIR))
    for filename in pending_files:
        filepath = os.path.join(PENDING_DIR, filename)
        if send_frame(filepath):
            os.remove(filepath)
            print(f"Eliminado archivo enviado localmente: {filename}")

def capture_and_send():
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("Error al abrir la cámara")
        return

    while True:
        ret, frame = cap.read()
        if not ret:
            print("Error al capturar frame")
            break

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        temp_filename = f"{timestamp}.jpg"
        cv2.imwrite(temp_filename, frame)

        # Primero intenta enviar los pendientes
        send_pending_frames()

        # Luego intenta enviar el frame actual
        if not send_frame(temp_filename):
            os.rename(temp_filename, os.path.join(PENDING_DIR, temp_filename))
            print(f"Guardado localmente: {temp_filename}")
        else:
            os.remove(temp_filename)

        time.sleep(CAPTURE_INTERVAL)

    cap.release()

if __name__ == "__main__":
    capture_and_send()
