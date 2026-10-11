"""Errores de la API con formato uniforme: {"error": {"code": "...", "message": "..."}}."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def error_response(status: int, code: str, message: str, headers: dict | None = None) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status, headers=headers)


_HTTP_DEFAULTS = {
    400: ("solicitud_invalida", "La solicitud no es válida."),
    401: ("no_autenticado", "Inicia sesión para continuar."),
    403: ("prohibido", "No tienes permiso para hacer esto."),
    404: ("no_encontrado", "No se encontró lo que buscas."),
    405: ("metodo_no_permitido", "Método no permitido."),
    429: ("demasiados_intentos", "Demasiados intentos. Espera un momento."),
}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        return error_response(exc.status, exc.code, exc.message)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException):
        code, message = _HTTP_DEFAULTS.get(exc.status_code, ("error", "Ocurrió un error."))
        return error_response(exc.status_code, code, message, getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError):
        return error_response(422, "solicitud_invalida", "Los datos enviados no son válidos.")
