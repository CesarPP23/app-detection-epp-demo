import cv2
import requests
import numpy as np
import time

BACKEND_URL = "http://localhost:8000/latest-image"  # Cambia esto si el servidor está en otro host
REFRESH_INTERVAL = 2  # segundos

def fetch_latest_image():
    try:
        response = requests.get(BACKEND_URL)
        if response.status_code == 200:
            img_array = np.frombuffer(response.content, dtype=np.uint8)
            image = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
            return image
        else:
            print(f"[ERROR] Código de respuesta: {response.status_code}")
            return None
    except requests.exceptions.RequestException as e:
        print(f"[ERROR] No se pudo conectar al backend: {e}")
        return None

def main():
    while True:
        image = fetch_latest_image()
        if image is not None:
            cv2.imshow("Última detección", image)
        else:
            img_placeholder = np.zeros((480, 640, 3), dtype=np.uint8)
            cv2.putText(img_placeholder, "Esperando imagen...", (50, 240),
                        cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
            cv2.imshow("Última detección", img_placeholder)

        if cv2.waitKey(REFRESH_INTERVAL * 1000) & 0xFF == ord('q'):
            break

    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()
