# detector.py
from datetime import datetime

def process_frame_and_get_detections(frame, model):
    """
    Procesa un solo frame con el modelo YOLO y devuelve un diccionario
    con las detecciones y el timestamp.
    """
    results = model(frame, verbose=False)

    detections_list = []
    for det in results[0].boxes.data:
        det = det.tolist()
        detections_list.append({
            "clase_id": int(det[5]),
            "nombre_clase": model.names[int(det[5])],
            "confianza": float(det[4]),
            "bounding_box": {
                "x1": int(det[0]),
                "y1": int(det[1]),
                "x2": int(det[2]),
                "y2": int(det[3]),
            }
        })

    timestamp_actual = datetime.now().isoformat()
    
    return {
        "timestamp": timestamp_actual,
        "detections": detections_list
    }