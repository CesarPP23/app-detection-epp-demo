# stream_manager.py
"""
Gestiona la conexión de streaming hacia el orchestrator-service (Cloud Run)
de forma evento-driven: se abre sola cuando hay actividad de detección
confirmada, y se cierra sola tras un periodo de inactividad. Así el
Cloud Run del lado de streaming solo recibe tráfico real durante eventos
reales, no queda una conexión abierta por horas "por si acaso".
"""
import asyncio
import websockets


class StreamManager:
    def __init__(self, control_url: str, video_url: str, uplink_token: str, logger, on_state_change=None):
        self.control_url = control_url
        self.video_url = video_url
        self.uplink_token = uplink_token
        self._logger = logger
        self._on_state_change = on_state_change  # async callback(new_state: str)
        self._control_ws = None
        self._video_ws = None
        self._control_listener_task = None
        self.active = False
        self._starting = False

    async def start(self, reason: str):
        if self.active or self._starting:
            return
        self._starting = True
        try:
            headers = {"Authorization": self.uplink_token}
            self._control_ws = await websockets.connect(self.control_url, additional_headers=headers)
            self._video_ws = await websockets.connect(self.video_url, additional_headers=headers)
            self._control_listener_task = asyncio.create_task(self._listen_control())
            self.active = True
            self._logger.info(f"Streaming ACTIVADO (motivo: {reason}).")
            if self._on_state_change:
                await self._on_state_change("streaming")
        except Exception as e:
            self._logger.error(f"No se pudo activar streaming: {e}", exc_info=True)
            await self._cleanup()
        finally:
            self._starting = False

    async def send_frame(self, jpeg_bytes: bytes):
        if not self.active or self._video_ws is None:
            return
        try:
            await self._video_ws.send(jpeg_bytes)
        except (websockets.exceptions.ConnectionClosed, Exception) as e:
            self._logger.warning(f"Se perdió la conexión de video durante streaming: {e}")
            await self.stop(reason="conexión de video perdida")

    async def _listen_control(self):
        try:
            async for _message in self._control_ws:
                pass  # reservado para futuros comandos (ej. STOP_STREAM remoto)
        except (websockets.exceptions.ConnectionClosed, asyncio.CancelledError):
            pass

    async def stop(self, reason: str, next_state: str = "running"):
        if not self.active:
            return
        self._logger.info(f"Streaming DESACTIVADO (motivo: {reason}).")
        await self._cleanup()
        if self._on_state_change:
            await self._on_state_change(next_state)

    async def _cleanup(self):
        self.active = False
        if self._control_listener_task and not self._control_listener_task.done():
            self._control_listener_task.cancel()
        for ws in (self._control_ws, self._video_ws):
            if ws is not None:
                try:
                    await ws.close()
                except Exception:
                    pass
        self._control_ws = None
        self._video_ws = None
        self._control_listener_task = None
