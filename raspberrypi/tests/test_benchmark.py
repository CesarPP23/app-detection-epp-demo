"""tools/benchmark.py (Fase 7): solo la parte pura (estadísticas, tabla, regla de
selección). No importa ultralytics/torch/ncnn: usa un `Runner` falso.
"""

from tools import benchmark as bm


class FakeRunner:
    """Cuenta llamadas y tarda `delay_ms` por frame (simulado, sin reloj real)."""

    def __init__(self):
        self.calls = 0

    def predict(self, frame):
        self.calls += 1


def test_latency_stats_vacio():
    s = bm.latency_stats([])
    assert s == bm.LatencyStats(0.0, 0.0, 0.0)


def test_latency_stats_percentiles():
    # 1..100 ms: p50=50.5, p95=95.05 (numpy linear interpolation), fps = 1000/mean
    s = bm.latency_stats([float(i) for i in range(1, 101)])
    assert s.p50_ms == 50.5
    assert round(s.p95_ms, 2) == 95.05
    assert s.fps > 0


def test_run_timed_cuenta_warmup_y_medicion():
    runner = FakeRunner()
    frames = [object() for _ in range(15)]
    lat = bm.run_timed(runner, frames, warmup=10, iters=5)
    assert runner.calls == 15
    assert len(lat) == 5
    assert all(ms >= 0 for ms in lat)


def test_run_timed_exige_suficientes_frames():
    runner = FakeRunner()
    try:
        bm.run_timed(runner, [object()] * 5, warmup=10, iters=5)
        raise AssertionError("debía fallar: faltan frames")
    except ValueError:
        pass


def test_render_table_incluye_error_y_mapa():
    results = [
        bm.BenchResult("pt", 640, 10.0, 15.0, 66.0, map5095=0.80),
        bm.BenchResult("ncnn", 416, error="torch no disponible"),
    ]
    out = bm.render_table(results)
    assert "pt@640" in out
    assert "0.800" in out
    assert "ncnn@416" in out
    assert "ERROR" in out
    assert "torch no disponible" in out


def test_select_combo_sin_map_usa_supuesto_del_plan():
    results = [
        bm.BenchResult("pt", 640, 10.0, 15.0, 66.0),
        bm.BenchResult("ncnn", 416, 4.0, 6.0, 166.0),
    ]
    chosen, reason = bm.select_combo(results)
    assert chosen == "ncnn@416"
    assert "supuesto" in reason


def test_select_combo_con_map_elige_la_mas_rapida_que_cumple():
    # baseline pt@640 con mAP 0.80; ncnn@416 pierde 1 pt (0.79) y cumple p95 < 0.7/5=0.14s;
    # ncnn@320 es más rápida pero pierde 3 pts (no cumple max_map_drop=2.0).
    results = [
        bm.BenchResult("pt", 640, 20.0, 30.0, 33.0, map5095=0.80),
        bm.BenchResult("ncnn", 416, 8.0, 12.0, 125.0, map5095=0.79),
        bm.BenchResult("ncnn", 320, 5.0, 7.0, 200.0, map5095=0.77),
    ]
    chosen, reason = bm.select_combo(results, baseline="pt@640", max_map_drop=2.0, inference_fps=5.0)
    assert chosen == "ncnn@416"
    assert "más rápida" in reason


def test_select_combo_ninguna_cumple_cae_al_supuesto():
    results = [
        bm.BenchResult("pt", 640, 500.0, 800.0, 2.0, map5095=0.80),
        bm.BenchResult("ncnn", 416, 400.0, 600.0, 2.5, map5095=0.60),  # pierde 20 pts
    ]
    chosen, reason = bm.select_combo(results, baseline="pt@640", max_map_drop=2.0, inference_fps=5.0)
    assert chosen == "ncnn@416"
    assert "ninguna" in reason.lower() or "ninguna" in reason.lower()


def test_cpu_temperature_no_lanza():
    value, note = bm.cpu_temperature_celsius()
    assert isinstance(note, str)
    assert value is None or isinstance(value, float)
