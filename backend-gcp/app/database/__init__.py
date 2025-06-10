"""
Módulo de base de datos
"""
from .firestore_client import FirestoreClient, firestore_client

__all__ = ["FirestoreClient", "firestore_client"]