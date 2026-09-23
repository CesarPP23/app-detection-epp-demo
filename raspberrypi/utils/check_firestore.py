from google.cloud import firestore
from google.api_core import exceptions

# --- ID de la Base de Datos ---
# Lo obtenemos de tu captura de pantalla.
DATABASE_ID = "(default)"

try:
    # --- MODIFICACIÓN CLAVE ---
    # Le decimos explícitamente a qué base de datos conectarse.
    db = firestore.Client(database=DATABASE_ID)
    
    doc_ref = db.collection("device_status").document("raspberry-pi-01")
    
    print(f"Buscando documento en la base de datos '{DATABASE_ID}'...")
    print("Ruta: device_status/raspberry-pi-01")
    
    doc = doc_ref.get()
    
    if doc.exists:
        print("\n✅ ¡Éxito! Documento encontrado.")
        print(f"   -> Datos: {doc.to_dict()}")
    else:
        print("\n❌ Documento NO encontrado.")
        print("   -> La conexión y autenticación son correctas, pero el documento no se encontró en esta ruta.")

except exceptions:
    print("\n❌ ERROR DE AUTENTICACIÓN:")
    print("   -> No se encontraron las credenciales. Revisa la variable de entorno.")
except Exception as e:
    print(f"\n❌ Ocurrió un error inesperado: {e}")