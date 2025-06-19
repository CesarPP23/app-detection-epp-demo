# Sistema de Monitoreo Inteligente con Raspberry Pi y Google Cloud

## Objetivo Principal

Desarrollar un sistema robusto y eficiente para la captura, procesamiento y visualización de video en tiempo real, utilizando un dispositivo de borde (Raspberry Pi) y un backend escalable en Google Cloud Platform (GCP). El sistema permite monitoreo remoto, análisis inteligente de imágenes y visualización de datos históricos a través de una interfaz web amigable.

## Arquitectura General

La solución está compuesta por tres grandes bloques:

- **Borde (Edge): Raspberry Pi**
  - Dispositivo físico encargado de capturar imágenes y video mediante una cámara conectada.
  - Ejecuta un microservicio en Python (FastAPI) que permite monitorear el estado del dispositivo y recibir comandos desde la nube.
  - Se comunica de forma segura y bidireccional con el backend principal en GCP mediante WebSocket, enviando imágenes/video y recibiendo instrucciones (por ejemplo, activar/desactivar streaming).

- **Backend Principal: Google Cloud Platform (GCP)**
  - Servidor central implementado con FastAPI, desplegado en una máquina virtual con GPU (Google Compute Engine).
  - Recibe los datos del Raspberry Pi, ejecuta modelos de inferencia (por ejemplo, YOLOv8s para detección y etiquetado de imágenes) y almacena los resultados en Firestore (NoSQL) y archivos en Cloud Storage.
  - Expone endpoints para control, visualización en tiempo real (stream MJPEG) y consulta de datos históricos.
  - Gestiona la autenticación y la lógica de negocio.

- **Interfaz de Usuario: Streamlit**
  - Panel web interactivo para visualizar datos históricos, estadísticas y video en tiempo real.
  - Se comunica exclusivamente con el backend en GCP para todas las operaciones (no hay conexión directa con el Raspberry Pi).

## Componentes Clave

### Raspberry Pi (Edge Device)
- **Hardware:** Raspberry Pi 4/5, cámara USB o módulo oficial.
- **Software:** Python, FastAPI (microservicio local), Docker (despliegue recomendado).
- **Funciones:**
  - Captura y preprocesamiento de imágenes/video.
  - Comunicación bidireccional con el backend mediante WebSocket.
  - Exposición de un endpoint local para monitoreo del dispositivo.

### Backend FastAPI en GCP
- **Infraestructura:** Google Compute Engine (con GPU), Firestore, Cloud Storage.
- **Software:** FastAPI, modelos de inferencia (YOLOv8s).
- **Funciones:**
  - Recepción y procesamiento de datos del Raspberry Pi.
  - Ejecución de inferencia y almacenamiento de resultados.
  - Exposición de endpoints para control, visualización y consulta de datos.
  - Comunicación en tiempo real con el Raspberry Pi mediante WebSocket.

## Comunicación

Toda la comunicación entre el Raspberry Pi y el backend principal se realiza mediante WebSocket seguro (WSS), permitiendo el envío eficiente de datos y comandos en tiempo real.
