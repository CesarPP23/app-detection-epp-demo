import base64
import threading
import time
from datetime import datetime, timezone

import flet as ft
import requests

from core.config import Config
from core.firestore_client import get_device_state, get_recent_detections, set_device_state

REFRESH_SECONDS = 7
LIVE_POLL_SECONDS = 2
MAX_UI_FPS = 8
_JPEG_SOI = b"\xff\xd8"
_JPEG_EOI = b"\xff\xd9"

# ---------------------------------------------------------------------------
# Paleta (skill dataviz, valores validados para superficie oscura).
# Un solo hue para todo lo que sea "magnitud" (barras, tendencia) — no se usa
# un color por clase porque no son series distintas, son la misma métrica
# (conteo) comparada entre categorías: ver choosing-a-form.md, "Compare
# magnitude -> color job: sequential (un hue)".
COLOR_BLUE = "#3987e5"
COLOR_SURFACE = "#1a1a19"
COLOR_PAGE = "#0d0d0d"
TEXT_PRIMARY = "#ffffff"
TEXT_SECONDARY = "#c3c2b7"
TEXT_MUTED = "#898781"
GRIDLINE = "#2c2c2a"
STATUS_GOOD = "#0ca30c"
STATUS_WARNING = "#fab219"
STATUS_CRITICAL = "#d03b3b"


def mjpeg_frames(url: str, chunk_size: int = 8192, timeout: int = 10):
    """Lee un stream MJPEG (multipart/x-mixed-replace) y va entregando cada
    JPEG completo (busca directamente los marcadores SOI/EOI del propio
    JPEG, ignorando los headers de multipart — más simple y robusto que
    parsear el boundary). No depende de ninguna librería nueva de GCP."""
    resp = requests.get(url, stream=True, timeout=timeout)
    resp.raise_for_status()
    buf = b""
    try:
        for chunk in resp.iter_content(chunk_size=chunk_size):
            if not chunk:
                continue
            buf += chunk
            while True:
                start = buf.find(_JPEG_SOI)
                if start == -1:
                    buf = b""
                    break
                end = buf.find(_JPEG_EOI, start + 2)
                if end == -1:
                    buf = buf[start:]
                    break
                yield buf[start:end + 2]
                buf = buf[end + 2:]
    finally:
        resp.close()


# ---------------------------------------------------------------------------
# Lector de MJPEG ÚNICO para todo el proceso (no por sesión/pestaña).
# Motivo: cada pestaña del dashboard que abre una sesión Flet nueva, si cada
# una abriera su propia conexión al MJPEG de Cloud Run, multiplicaría los
# requests facturables contra orchestrator-service sin necesidad (y además
# generaba el "video congelado" visto en pruebas: varios hilos escribían
# frames desordenados sobre el mismo control). Con un solo lector global,
# sin importar cuántas pestañas/sesiones estén abiertas, solo hay UNA
# conexión real al stream; cada sesión solo lee la última imagen en memoria.
_shared_lock = threading.Lock()
_shared_frame_b64: dict = {"value": None}
_shared_reader_started = {"value": False}


def _global_mjpeg_reader():
    last_state_check = 0.0
    state = "stopped"
    while True:
        now = time.monotonic()
        if now - last_state_check > 2.0:
            try:
                state = get_device_state()
            except Exception:
                state = "stopped"
            last_state_check = now

        if state != "streaming":
            with _shared_lock:
                _shared_frame_b64["value"] = None
            time.sleep(1)
            continue

        try:
            last_check = time.monotonic()
            for frame in mjpeg_frames(Config.VIDEO_STREAM_URL):
                with _shared_lock:
                    _shared_frame_b64["value"] = base64.b64encode(frame).decode("ascii")
                if time.monotonic() - last_check > 2.0:
                    try:
                        state = get_device_state()
                    except Exception:
                        state = "stopped"
                    last_check = time.monotonic()
                    last_state_check = last_check
                    if state != "streaming":
                        break
        except Exception:
            time.sleep(2)


def _ensure_global_reader_started():
    with _shared_lock:
        if not _shared_reader_started["value"]:
            _shared_reader_started["value"] = True
            threading.Thread(target=_global_mjpeg_reader, daemon=True).start()


STATE_COLORS = {
    "streaming": (ft.Colors.RED, "En vivo"),
    "running": (STATUS_GOOD, "Activo (sin streaming)"),
    "stopped": (ft.Colors.GREY, "Inactivo"),
}


def _fmt_dt(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, datetime):
        return value.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    return str(value)


def _fmt_ago(value) -> str:
    if not isinstance(value, datetime):
        return "-"
    now = datetime.now(timezone.utc)
    delta = now - value.astimezone(timezone.utc)
    secs = int(delta.total_seconds())
    if secs < 0:
        secs = 0
    if secs < 60:
        return f"hace {secs}s"
    if secs < 3600:
        return f"hace {secs // 60}m"
    if secs < 86400:
        return f"hace {secs // 3600}h"
    return f"hace {secs // 86400}d"


def _fmt_duration(total_seconds: float) -> str:
    secs = int(total_seconds)
    if secs < 60:
        return f"{secs}s"
    m, s = divmod(secs, 60)
    if m < 60:
        return f"{m}m {s}s"
    h, m = divmod(m, 60)
    return f"{h}h {m}m"


def _confidence_status_color(avg_conf: float) -> str:
    pct = avg_conf * 100
    if pct >= 80:
        return STATUS_GOOD
    if pct >= 50:
        return STATUS_WARNING
    return STATUS_CRITICAL


def compute_kpis(events: list[dict]) -> dict:
    total = len(events)
    total_seconds = sum(e.get("duration_seconds", 0) or 0 for e in events)
    avg_conf = (
        sum(e.get("confianza_promedio", 0) or 0 for e in events) / total if total else 0.0
    )
    counts: dict[str, int] = {}
    for e in events:
        cls = e.get("nombre_clase")
        if cls:
            counts[cls] = counts.get(cls, 0) + 1
    top_class = max(counts.items(), key=lambda kv: kv[1]) if counts else ("—", 0)
    return {
        "total": total,
        "total_seconds": total_seconds,
        "avg_conf": avg_conf,
        "top_class": top_class,
        "class_counts": counts,
    }


def main(page: ft.Page):
    page.title = "EPP Detection · Dashboard"
    page.theme_mode = ft.ThemeMode.DARK
    page.padding = 0
    page.bgcolor = COLOR_PAGE

    stop_event = threading.Event()
    last_events: dict = {"value": []}

    # =======================================================================
    # ---------- Estado en vivo (compartido: chip del header + tab) ----------
    # =======================================================================
    header_dot = ft.Container(width=10, height=10, border_radius=5, bgcolor=ft.Colors.GREY)
    header_label = ft.Text("Inactivo", size=13, color=TEXT_SECONDARY)
    header_chip = ft.Container(
        content=ft.Row([header_dot, header_label], spacing=8),
        padding=ft.padding.symmetric(horizontal=14, vertical=8),
        bgcolor=COLOR_SURFACE,
        border_radius=20,
    )

    # =======================================================================
    # ---------------------------- Dashboard --------------------------------
    # =======================================================================
    kpi_events = ft.Text("0", size=26, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY)
    kpi_duration = ft.Text("0s", size=26, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY)
    kpi_confidence = ft.Text("—", size=26, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY)
    kpi_top_class = ft.Text("—", size=20, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY)
    kpi_top_class_sub = ft.Text("", size=12, color=TEXT_MUTED)
    kpi_confidence_icon = ft.Icon(ft.Icons.VERIFIED, color=STATUS_GOOD, size=22)
    kpi_confidence_box = ft.Container(
        width=40, height=40, border_radius=10, alignment=ft.alignment.center,
        bgcolor=ft.Colors.with_opacity(0.15, STATUS_GOOD), content=kpi_confidence_icon,
    )

    def stat_tile(icon: str, label: str, value_control, accent: str, sub_control=None) -> ft.Container:
        col_children = [ft.Text(label, size=12, color=TEXT_MUTED), value_control]
        if sub_control is not None:
            col_children.append(sub_control)
        return ft.Container(
            content=ft.Row(
                [
                    ft.Container(
                        content=ft.Icon(icon, color=accent, size=22),
                        width=40, height=40, border_radius=10,
                        bgcolor=ft.Colors.with_opacity(0.15, accent),
                        alignment=ft.alignment.center,
                    ),
                    ft.Column(col_children, spacing=2, tight=True),
                ],
                spacing=12,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=COLOR_SURFACE,
            border_radius=14,
            padding=16,
            expand=True,
        )

    kpi_confidence_tile = ft.Container(
        content=ft.Row(
            [kpi_confidence_box, ft.Column(
                [ft.Text("Confianza promedio", size=12, color=TEXT_MUTED), kpi_confidence],
                spacing=2, tight=True,
            )],
            spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        bgcolor=COLOR_SURFACE, border_radius=14, padding=16, expand=True,
    )

    kpi_row = ft.Row(
        [
            stat_tile(ft.Icons.FACT_CHECK, "Eventos confirmados", kpi_events, COLOR_BLUE),
            stat_tile(ft.Icons.SCHEDULE, "Tiempo con EPP detectado", kpi_duration, COLOR_BLUE),
            kpi_confidence_tile,
            stat_tile(ft.Icons.LEADERBOARD, "Ítem más detectado", kpi_top_class, COLOR_BLUE, kpi_top_class_sub),
        ],
        spacing=12,
    )

    trend_bars_row = ft.Row(spacing=4, alignment=ft.MainAxisAlignment.END,
                             vertical_alignment=ft.CrossAxisAlignment.END)
    trend_card = ft.Container(
        content=ft.Column(
            [
                ft.Text("Actividad por hora (últimas 12h)", size=13, weight=ft.FontWeight.W_600, color=TEXT_SECONDARY),
                ft.Container(content=trend_bars_row, height=90, padding=ft.padding.only(top=8)),
                ft.Row(
                    [ft.Text("-12h", size=11, color=TEXT_MUTED), ft.Text("ahora", size=11, color=TEXT_MUTED)],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
            ],
            spacing=4,
        ),
        bgcolor=COLOR_SURFACE, border_radius=14, padding=18, expand=2,
    )

    top_classes_col = ft.Column(spacing=10)
    top_classes_card = ft.Container(
        content=ft.Column(
            [
                ft.Text("Ítems más detectados", size=13, weight=ft.FontWeight.W_600, color=TEXT_SECONDARY),
                ft.Container(content=top_classes_col, padding=ft.padding.only(top=8)),
            ],
            spacing=4,
        ),
        bgcolor=COLOR_SURFACE, border_radius=14, padding=18, expand=1,
    )

    table = ft.DataTable(
        column_spacing=24,
        heading_row_height=36,
        data_row_max_height=36,
        columns=[
            ft.DataColumn(ft.Text("Clase", size=12, color=TEXT_MUTED)),
            ft.DataColumn(ft.Text("Inicio", size=12, color=TEXT_MUTED)),
            ft.DataColumn(ft.Text("Duración", size=12, color=TEXT_MUTED)),
            ft.DataColumn(ft.Text("Confianza (prom/máx)", size=12, color=TEXT_MUTED)),
            ft.DataColumn(ft.Text("Frames", size=12, color=TEXT_MUTED)),
        ],
        rows=[],
    )
    empty_state = ft.Container(
        content=ft.Text(
            "Todavía no hay eventos confirmados. Corre el simulador de la Pi para generar datos.",
            color=TEXT_MUTED, italic=True,
        ),
        padding=24, visible=False,
    )

    def build_trend_bars(events: list[dict], hours: int = 12):
        now = datetime.now(timezone.utc)
        buckets = [0] * hours
        for e in events:
            ts = e.get("end_ts")
            if not isinstance(ts, datetime):
                continue
            delta_h = int((now - ts.astimezone(timezone.utc)).total_seconds() // 3600)
            if 0 <= delta_h < hours:
                buckets[hours - 1 - delta_h] += 1
        max_val = max(buckets) or 1
        bars = []
        for v in buckets:
            h = 6 + int((v / max_val) * 70) if v else 3
            bars.append(
                ft.Container(
                    width=14, height=h,
                    bgcolor=COLOR_BLUE if v else GRIDLINE,
                    border_radius=ft.border_radius.only(top_left=4, top_right=4),
                    tooltip=f"{v} evento(s)",
                )
            )
        trend_bars_row.controls = bars

    def build_top_classes(class_counts: dict[str, int], top_n: int = 5):
        ranked = sorted(class_counts.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
        if not ranked:
            top_classes_col.controls = [ft.Text("Sin datos aún", color=TEXT_MUTED, italic=True, size=12)]
            return
        max_val = ranked[0][1]
        rows = []
        for cls, cnt in ranked:
            rows.append(
                ft.Row(
                    [
                        ft.Container(width=100, content=ft.Text(cls, size=12, color=TEXT_SECONDARY, no_wrap=True)),
                        ft.Container(
                            content=ft.Container(
                                width=max(6, int((cnt / max_val) * 140)), height=14,
                                bgcolor=COLOR_BLUE, border_radius=4,
                            ),
                            width=140, alignment=ft.alignment.center_left,
                        ),
                        ft.Text(str(cnt), size=12, color=TEXT_MUTED),
                    ],
                    spacing=10,
                )
            )
        top_classes_col.controls = rows

    def refresh_dashboard():
        try:
            events = get_recent_detections(limit=200)
        except Exception:
            return
        last_events["value"] = events

        k = compute_kpis(events)
        kpi_events.value = str(k["total"])
        kpi_duration.value = _fmt_duration(k["total_seconds"])
        if k["total"]:
            pct = k["avg_conf"] * 100
            kpi_confidence.value = f"{pct:.0f}%"
            color = _confidence_status_color(k["avg_conf"])
            kpi_confidence_icon.color = color
            kpi_confidence_box.bgcolor = ft.Colors.with_opacity(0.15, color)
        else:
            kpi_confidence.value = "—"
        top_cls, top_cnt = k["top_class"]
        kpi_top_class.value = top_cls
        kpi_top_class_sub.value = f"{top_cnt} evento(s)" if top_cnt else ""

        build_trend_bars(events)
        build_top_classes(k["class_counts"])

        table.rows.clear()
        for e in events[:50]:
            table.rows.append(
                ft.DataRow(
                    cells=[
                        ft.DataCell(ft.Text(str(e.get("nombre_clase", "-")), size=12)),
                        ft.DataCell(ft.Text(_fmt_dt(e.get("start_ts")), size=12)),
                        ft.DataCell(ft.Text(f"{e.get('duration_seconds', 0):.1f}s", size=12)),
                        ft.DataCell(ft.Text(
                            f"{e.get('confianza_promedio', 0):.2f} / {e.get('confianza_max', 0):.2f}", size=12,
                        )),
                        ft.DataCell(ft.Text(str(e.get("frame_count", "-")), size=12)),
                    ]
                )
            )
        empty_state.visible = len(events) == 0
        page.update()

    # =======================================================================
    # ------------------------------ En vivo ---------------------------------
    # =======================================================================
    # Nota técnica: el motor web de Flet (Flutter Web) no puede mostrar un
    # stream MJPEG (multipart/x-mixed-replace) embebido directo en un
    # ft.Image(src=url) — Flutter espera un único payload de imagen completo
    # y se queda "cargando" para siempre (y ft.WebView tampoco renderiza nada
    # en el target web). Solución: el backend Python de esta app lee el
    # MJPEG, extrae cada JPEG individual y lo empuja al ft.Image como base64
    # — el video queda embebido en la misma página, sin pestañas nuevas.
    #
    # Bug corregido: un ft.Image necesita `src` o `src_base64` para no
    # lanzar el error "Image must have either src or src_base64 specified".
    # Antes se montaba `live_image` (con src_base64=None) apenas cambiaba el
    # estado a streaming, antes de que llegara el primer frame real. Ahora
    # se muestra un estado "Conectando…" separado y solo se monta
    # `live_image` cuando ya hay al menos un frame real en memoria.
    live_dot = ft.Container(width=12, height=12, border_radius=6, bgcolor=ft.Colors.GREY)
    live_label = ft.Text("Inactivo", size=16, weight=ft.FontWeight.W_500)
    device_id_text = ft.Text(f"Dispositivo: {Config.DEVICE_ID}", size=12, color=TEXT_MUTED)
    toggle_btn = ft.FilledButton("Iniciar streaming", icon=ft.Icons.PLAY_ARROW)
    refresh_btn = ft.OutlinedButton("Actualizar ahora", icon=ft.Icons.REFRESH)
    open_stream_btn = ft.OutlinedButton(
        "Abrir en pestaña nueva", icon=ft.Icons.OPEN_IN_NEW,
        url=Config.VIDEO_STREAM_URL, url_target=ft.UrlTarget.BLANK, visible=False,
    )

    video_icon = ft.Icon(ft.Icons.VIDEOCAM_OFF, size=48, color=TEXT_MUTED)
    video_text = ft.Text("Sin streaming activo", color=TEXT_MUTED)
    idle_content = ft.Column([video_icon, video_text], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=12)

    connecting_content = ft.Column(
        [ft.ProgressRing(width=32, height=32, color=COLOR_BLUE), ft.Text("Conectando al stream…", color=TEXT_SECONDARY)],
        horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=12,
    )

    live_image = ft.Image(width=720, height=480, fit=ft.ImageFit.CONTAIN, border_radius=12)

    video_placeholder = ft.Container(
        content=idle_content, width=720, height=480, alignment=ft.alignment.center,
        bgcolor=COLOR_SURFACE, border_radius=12,
    )

    last_class_caption = ft.Text("", size=13, color=TEXT_SECONDARY)

    current_state = {"value": "stopped", "has_frame": False}

    def apply_state_to_ui(state: str):
        color, label = STATE_COLORS.get(state, (ft.Colors.GREY, state))
        live_dot.bgcolor = color
        live_label.value = label
        header_dot.bgcolor = color
        header_label.value = label
        is_streaming = state == "streaming"
        if not is_streaming:
            video_placeholder.content = idle_content
            video_placeholder.bgcolor = COLOR_SURFACE
            current_state["has_frame"] = False
        elif not current_state["has_frame"]:
            video_placeholder.content = connecting_content
            video_placeholder.bgcolor = COLOR_SURFACE
        open_stream_btn.visible = is_streaming
        toggle_btn.text = "Detener streaming" if is_streaming else "Iniciar streaming"
        toggle_btn.icon = ft.Icons.STOP if is_streaming else ft.Icons.PLAY_ARROW
        current_state["value"] = state

        events = last_events["value"]
        if events:
            top = events[0]
            last_class_caption.value = (
                f"Última detección: {top.get('nombre_clase', '—')} "
                f"(confianza {top.get('confianza_promedio', 0):.0%}, {_fmt_ago(top.get('end_ts'))})"
            )
        else:
            last_class_caption.value = "Sin detecciones registradas todavía."

    def refresh_live_state():
        try:
            state = get_device_state()
        except Exception:
            state = current_state["value"]
        apply_state_to_ui(state)
        page.update()

    def on_toggle_click(e):
        target = "stopped" if current_state["value"] == "streaming" else "streaming"
        toggle_btn.disabled = True
        page.update()
        try:
            set_device_state(target)
        finally:
            toggle_btn.disabled = False
            refresh_live_state()

    def on_refresh_click(e):
        refresh_dashboard()
        refresh_live_state()

    toggle_btn.on_click = on_toggle_click
    refresh_btn.on_click = on_refresh_click

    dashboard_view = ft.Column(
        [
            kpi_row,
            ft.Row([trend_card, top_classes_card], spacing=12),
            ft.Container(height=8),
            ft.Text("Historial detallado", size=13, weight=ft.FontWeight.W_600, color=TEXT_SECONDARY),
            empty_state,
            ft.Container(
                content=ft.Column([table], scroll=ft.ScrollMode.AUTO),
                bgcolor=COLOR_SURFACE, border_radius=14, padding=8,
            ),
        ],
        spacing=16,
        expand=True,
        scroll=ft.ScrollMode.AUTO,
    )

    live_view = ft.Column(
        [
            ft.Row([live_dot, live_label], spacing=8),
            device_id_text,
            ft.Row([toggle_btn, refresh_btn], spacing=8),
            video_placeholder,
            last_class_caption,
            open_stream_btn,
        ],
        spacing=14,
        expand=True,
    )

    tabs = ft.Tabs(
        selected_index=0,
        tabs=[
            ft.Tab(text="Dashboard", icon=ft.Icons.DASHBOARD, content=ft.Container(dashboard_view, padding=20)),
            ft.Tab(text="En vivo", icon=ft.Icons.VIDEOCAM, content=ft.Container(live_view, padding=20)),
        ],
        expand=True,
    )

    page.appbar = ft.AppBar(
        title=ft.Text("EPP Detection"),
        center_title=False,
        bgcolor=COLOR_SURFACE,
        actions=[header_chip, ft.Container(width=12)],
    )
    page.add(tabs)

    refresh_dashboard()
    refresh_live_state()

    def background_refresh_loop():
        elapsed = 0
        while not stop_event.is_set():
            time.sleep(LIVE_POLL_SECONDS)
            if stop_event.is_set():
                break
            elapsed += LIVE_POLL_SECONDS
            try:
                refresh_live_state()
                if elapsed >= REFRESH_SECONDS:
                    elapsed = 0
                    refresh_dashboard()
            except Exception:
                pass

    def live_video_loop():
        """Por-sesión: solo lee el último frame ya descargado por el lector
        global (memoria local, sin red) y lo empuja al ft.Image de ESTA
        pestaña. La primera vez que llega un frame real, recién ahí se monta
        el control Image (nunca antes, para no disparar el error de Flet de
        Image sin src)."""
        last_pushed = None
        interval = 1.0 / MAX_UI_FPS
        while not stop_event.is_set():
            with _shared_lock:
                b64 = _shared_frame_b64["value"]
            if b64 and b64 != last_pushed:
                live_image.src_base64 = b64
                if not current_state["has_frame"]:
                    current_state["has_frame"] = True
                    video_placeholder.content = live_image
                    video_placeholder.bgcolor = ft.Colors.BLACK
                    page.update()
                else:
                    try:
                        live_image.update()
                    except Exception:
                        pass
                last_pushed = b64
            elif b64 is None and last_pushed is not None:
                last_pushed = None
            time.sleep(interval)

    _ensure_global_reader_started()
    worker = threading.Thread(target=background_refresh_loop, daemon=True)
    worker.start()
    video_worker = threading.Thread(target=live_video_loop, daemon=True)
    video_worker.start()

    def on_disconnect(_e):
        stop_event.set()

    if hasattr(page, "on_disconnect"):
        page.on_disconnect = on_disconnect


if __name__ == "__main__":
    ft.app(target=main, view=ft.AppView.WEB_BROWSER, port=8765)
