# training — entrenamiento, evaluación y robustez del modelo de EPP

Scripts de la tesis para reproducir el modelo `best.pt` (YOLOv8n, 3 clases) y su evaluación:

| Script | Uso |
|---|---|
| `prepare_dataset.py` | Re-partición del dataset de Roboflow (aumentadas solo en entrenamiento) y verificación de integridad |
| `train.py` | Transferencia de aprendizaje con YOLOv8n, YOLOv8s y YOLO11n |
| `evaluate.py` | Métricas sobre el conjunto de prueba (y de los modelos exportados NCNN/ONNX) |
| `export.py` | Exportación a NCNN 640 y ONNX; copia a `../edge_pose/models/` |
| `robustness.py` | Particiones por escena y por cámara, 3 semillas y bootstrap de 1000 réplicas |
| `pipeline.py` | Reentrenamiento completo con un dataset nuevo (`--src <zip de Roboflow> --name epp_v4`) |

El dataset, las corridas y los pesos no se versionan (`.gitignore`): contienen fotografías de operarios.
