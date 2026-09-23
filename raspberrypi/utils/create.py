from google.cloud import firestore

# --- Configuración ---
COLLECTION_ID = "device_status"
DOCUMENT_ID = "raspberry-pi-01"
DATABASE_ID = "(default)"
DATA_TO_CREATE = {"state": "running"}

try:
    # Inicializa el cliente, especificando la base de datos
    db = firestore.Client(database=DATABASE_ID)

    # Crea la referencia al documento
    doc_ref = db.collection(COLLECTION_ID).document(DOCUMENT_ID)

    # Usa .set() para crear el documento (o sobrescribirlo si ya existe)
    print(f"Creando/actualizando documento: {DOCUMENT_ID}...")
    doc_ref.set(DATA_TO_CREATE)

    print("\n✅ ¡Éxito! El documento ha sido creado/actualizado en Firestore.")
    print(f"   -> Colección: {COLLECTION_ID}")
    print(f"   -> Documento: {DOCUMENT_ID}")
    print(f"   -> Datos: {DATA_TO_CREATE}")

except Exception as e:
    print(f"\n❌ Ocurrió un error: {e}")
    print("   -> Asegúrate de que tu variable de entorno GOOGLE_APPLICATION_CREDENTIALS esté bien configurada.")