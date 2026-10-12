# Modelo de visión — EPP Monitor

**Para quién es:** quien reentrene, exporte o despliegue el modelo de detección de EPP, y
quien necesite entender qué benchmark real existe (y cuál no) para elegir el formato/tamaño
de inferencia en la Raspberry Pi.
**Última verificación:** 2026-10-11.

---

## 1. Resumen ejecutivo

- **Combinación elegida para producción: `ncnn@416`.** No es el resultado de un benchmark
  real en esta máquina (ver §4): es el **supuesto por defecto que indica el plan**
  (`docs/PLAN_REMEDIACION.md` §11) cuando no hay dataset de validación ni se puede medir
  latencia real. Coincide con `MODEL_IMGSZ=416` (default en `epp_edge/config.py`), así que
  no hace falta cambiar configuración.
- **No se obtuvo ningún número real de latencia/FPS** para las 6 combinaciones
  `{pt, ncnn} × {320, 416, 640}` en esta laptop Windows: Smart App Control bloquea las DLL
  de `torch`, y `ultralytics` (y por tanto cualquier export o inferencia `pt`/`ncnn` vía
  Ultralytics) depende de `torch` para importar. El detalle completo, con el comando y el
  error exacto, está en §4.
- **No hay dataset de validación local** (`data.yaml`): no se reporta `mAP50-95`, ni medido
  ni estimado, para ninguna combinación. Ver §4.3.
- Las clases, el SHA-256 y los metadatos del checkpoint (§2) **sí son reales**: se leyeron
  directamente del archivo `torch/best.pt` con una técnica alternativa que no necesita
  `torch` (ver §3), precisamente para no depender del bloqueo de §4.

## 2. El checkpoint (`torch/best.pt`)

`torch/best.pt` está fuera de git (carpeta `torch/` ignorada localmente). No se sube a
ningún sitio como parte de este trabajo; la distribución de pesos (GitHub Release o
almacenamiento privado) es una decisión pendiente del usuario, según el plan.

| Campo | Valor |
|---|---|
| Archivo | `torch/best.pt` |
| Tamaño | 6 256 355 bytes (≈ 6.2 MB) |
| SHA-256 | `bdef864a815c35d95396a362998f80405a1203fe873cd8d9b53bc4ad5892223a` |
| Arquitectura | YOLOv8n (`yolov8n.pt` como base, según `train_args.model` del checkpoint) |
| Clases (`model.names`) | `{0: "Gorra", 1: "Guante", 2: "Mascarilla"}` (`nc=3`) |
| `imgsz` de entrenamiento | 640 (`train_args.imgsz`) |
| Épocas | 100 (`train_args.epochs`) |
| Fecha de entrenamiento | 2026-09-13 (hora local del equipo de entrenamiento, UTC-05:00) |
| Versión de Ultralytics usada al entrenar | 8.4.150 (campo `version` del checkpoint) |
| Versión de Ultralytics pineada para inferencia | 8.4.175 (`raspberrypi/requirements.txt`) |
| Dataset de entrenamiento (ruta original, informativa) | `training/data/epp_v3/data.yaml` (no existe en esta laptop; ver §4.3) |

Notas:

- El modelo detecta **presencia** de 3 ítems de EPP (gorra, guante, mascarilla), no
  personas. Como señala el plan (§11), el indicador de cumplimiento real necesitaría además
  la clase `person` (o clases de ausencia) y tracking por persona
  (`model.track(persist=True, tracker="bytetrack.yaml")`); eso queda para el reentrenamiento
  futuro de la tesis, documentado aquí pero **no implementado** en este trabajo.
- El desfase de versión de Ultralytics (8.4.150 al entrenar vs. 8.4.175 pineado para
  inferencia) es menor y no debería romper la carga del checkpoint, pero no se pudo
  confirmar cargándolo de verdad en esta máquina (§4). Si al exportar en la Pi/Linux
  apareciera un error de compatibilidad, reentrenar o reexportar con la misma versión 8.4.175
  es la primera cosa a probar.
- `edge/inference.py::compute_model_version()` ya calcula el SHA-256 (primeros 12
  caracteres) del archivo de modelo activo en el borde y lo expone como
  `edge.model_version` en cada evento; no se duplicó esa lógica aquí.

## 3. Cómo se leyeron las clases y metadatos sin `torch`

`ultralytics.YOLO("torch/best.pt")` (la forma "normal", la que usa `export_model.py` y
`inference.py::load_yolo`) no funciona en esta laptop porque `import torch` falla (§4). Para
poder documentar las clases reales sin inventarlas ni dejarlas en blanco, se leyó
`torch/best.pt` como lo que es a bajo nivel —un ZIP con un pickle de PyTorch
(`best/data.pkl`)— usando un `pickle.Unpickler` con `find_class`/`persistent_load`
sobrescritos para devolver objetos "stub" genéricos en vez de reconstruir tensores reales.
Esto permite recorrer el diccionario del checkpoint (`names`, `nc`, `yaml`, `train_args`,
`version`, `date`, …) sin ejecutar ni un solo kilobyte de los pesos reales.

Esta técnica es **solo diagnóstico**, usada una vez para escribir este documento; no forma
parte de `tools/export_model.py` ni de `tools/benchmark.py`, que siguen usando
`ultralytics.YOLO(...)` tal como pide el plan (y tal como se debe usar en la Pi/Linux, donde
`torch` sí carga normalmente).

## 4. Benchmark: qué se intentó, qué falló y por qué

### 4.1 El bloqueo: Smart App Control (Windows 11) contra `torch`

Esta laptop tiene **Smart App Control** activo
(`HKLM\SYSTEM\CurrentControlSet\Control\CI\Policy!VerifiedAndReputablePolicyState = 1`), una
función de Windows 11 que bloquea binarios no firmados o sin reputación —y que **no se
puede desactivar sin permisos de administrador, y una vez desactivada no se puede volver a
activar sin reinstalar Windows**. No se intentó desactivarla: no hay permisos de
administrador en esta sesión, y es un cambio de seguridad irreversible fuera del alcance de
esta tarea.

El registro de Code Integrity (`Microsoft-Windows-CodeIntegrity/Operational`, eventos 3077 /
3089 / 3118, Policy ID `{0283ac0f-fff1-49ae-ada1-8a933130cad6}`) confirma que bloqueó, entre
otras, estas DLL del wheel de PyPI `torch==2.14.1` (instalado como dependencia de
`ultralytics==8.4.175`):

```
torch\lib\shm.dll
torch\lib\torch_python.dll
```

`torch_python.dll` es el binding de Python a `torch._C`: sin él, `import torch` falla por
completo (no es un problema de una función opcional, es el módulo entero). Como
`ultralytics` importa `torch` sin condiciones en `ultralytics/utils/__init__.py`, **ninguna**
funcionalidad de Ultralytics está disponible en este entorno: ni `YOLO(...).export(...)`
(para cualquier formato: ncnn, onnx u openvino) ni `YOLO(...).predict(...)` en formato `pt`.

Se verificó por separado que el paquete `ncnn` (el motor de inferencia en sí, independiente
de Ultralytics) **sí se importa y funciona** en esta máquina —el bloqueo es específico de
`torch`, no de todo binario de IA—, pero no sirve de nada aquí porque los pesos `.ncnn.*` que
necesitaría solo se generan exportando desde `torch/best.pt`, y ese export también depende
de `torch`. Es decir: no hay forma de producir ni de correr `ncnn@*` sin pasar antes por
`torch`, y `torch` no carga en esta máquina.

(De forma relacionada, `numpy.random` —específicamente la extensión compilada
`numpy.random._common`— también cae bajo el mismo tipo de bloqueo. `tools/benchmark.py` usa
aritmética modular en vez de `numpy.random` para generar el clip sintético precisamente por
esto; ver el docstring de `generate_synthetic_clip` en `raspberrypi/tools/benchmark.py`.)

### 4.2 Comandos ejecutados y error real obtenido

Entorno: venv local `.venv-edge/` (Python 3.14.7, Windows 11), con
`raspberrypi/requirements.txt` instalado (`ultralytics==8.4.175`, `ncnn==1.0.20260526`,
`opencv-python-headless==5.0.0.93`, `numpy==2.5.4`, etc.).

```
python raspberrypi\tools\export_model.py --format ncnn --imgsz 416
```
```
Falló la exportación de torch\best.pt a ncnn (imgsz=416): [WinError 4551] Una directiva de
Control de aplicaciones bloqueó este archivo. Error loading
"...\.venv-edge\Lib\site-packages\torch\lib\shm.dll" or one of its dependencies.
```
(código de salida 1; mismo error con `--format onnx` y `--format openvino`, porque falla al
importar `torch` antes de llegar a elegir el formato)

```
python raspberrypi\tools\benchmark.py --sizes 416 --formats pt,ncnn --warmup 2 --iters 3
```
```
Midiendo pt@416 (2 warm-up + 3 medición)…
  ERROR en pt@416: [WinError 4551] Una directiva de Control de aplicaciones bloqueó este
  archivo. Error loading ".../torch/lib/shm.dll" or one of its dependencies.
Midiendo ncnn@416 (2 warm-up + 3 medición)…
  ERROR en ncnn@416: [WinError 4551] ... (mismo error: el export automático también
  necesita torch)

combo        p50_ms   p95_ms     fps  mAP50-95
----------------------------------------------
pt@416     ERROR: [WinError 4551] ...
ncnn@416   ERROR: [WinError 4551] ...

Combinación elegida: ncnn@416
Motivo: sin dataset de validación local (mAP50-95 no disponible): se aplica el supuesto por
defecto indicado en el plan, no una medición
```
(código de salida 1: `benchmark.py` considera que no pudo medir nada; esto es intencional —
ver §4.4 sobre el diseño del script)

### 4.3 Sin dataset de validación

No existe ningún `data.yaml` de validación en esta laptop (ni en `torch/`, ni en ninguna otra
carpeta del repo). `train_args.data` del checkpoint apunta a una ruta local del equipo de
entrenamiento (`training/data/epp_v3/data.yaml`) que tampoco está disponible aquí. Por lo
tanto, para **ninguna** combinación hay `mAP50-95`, ni real ni estimado: se reporta como
"no disponible, sin dataset de validación local", tal como indica esta tarea.

### 4.4 Qué sí se ejecutó de verdad

Para no perder toda la capa de verificación real disponible en esta máquina, lo que **no**
depende de `torch` sí se corrió de verdad:

- Generación del clip sintético: `media/bench_synthetic.mp4` (1280×720, 20 fps, ~6 s,
  figuras simples sobre una textura de fondo determinista). Es **solo** para tener una
  fuente de frames consistente entre combinaciones si el benchmark pudiera correr; **no**
  representa escenas reales de la Pi y **no** sirve para medir precisión. Está fuera de git
  (`*.mp4` en `.gitignore`).
- `tools/benchmark.py` y `tools/export_model.py` se ejecutaron de verdad (no es código sin
  probar): el manejo de errores por combinación, la tabla de resultados, la regla de
  selección y el cálculo de percentiles están ejercitados end-to-end; lo único que no
  funcionó fue la parte que depende de `torch`, documentada arriba con el error real.
- Tests unitarios de la parte pura de `benchmark.py` (percentiles, tabla, regla de
  selección) en `raspberrypi/tests/test_benchmark.py`, con un `Runner` falso — no necesitan
  `torch`/`ultralytics`/`ncnn` y pasan en esta máquina (`pytest
  raspberrypi/tests/test_benchmark.py`, 9 tests).
- Temperatura de CPU: se intentó con `psutil.sensors_temperatures()`
  (`tools/benchmark.py::cpu_temperature_celsius`); en Windows no está implementado
  (`AttributeError`), se reporta como "no disponible en este sistema operativo (normal en
  Windows; sí funciona en la Pi)". No se intentó ningún otro mecanismo específico de Windows
  (WMI, etc.) porque la temperatura de la CPU de esta laptop de desarrollo no es el dato que
  importa para producción: lo que importa es la de la Pi real.

### 4.5 Por qué no se resolvió en esta máquina

Las dos rutas de solución real son:

1. **Desactivar Smart App Control.** Requiere administrador y, una vez desactivada, la
   única forma de reactivarla es reinstalar Windows. Es un cambio de seguridad permanente e
   importante del equipo del usuario: no se tomó esa decisión unilateralmente, no hay
   permisos de administrador en esta sesión, y de todas formas no sería deseable como
   solución de desarrollo habitual.
2. **Correr en un entorno sin esta política de Windows:** la Raspberry Pi real (el destino
   final de todo esto) o cualquier Linux/WSL. No hay Pi física disponible en esta sesión
   (confirmado en `docs/HANDOFF_CLAUDE_CODE.md`), ni WSL instalado en esta laptop.

Por eso `tools/export_model.py` y `tools/benchmark.py` quedan **completos y correctos**,
listos para correr en la Pi real o en cualquier Linux, pero no se pudo generar ni un solo
número real de latencia en esta sesión. No se simuló ni se inventó ningún valor de
latencia/FPS para rellenar la tabla.

## 5. Regla de selección aplicada

Regla del plan (§11): la combinación más rápida con `mAP50-95` ≤ 2 puntos porcentuales por
debajo de `pt@640` y `p95 < 0.7 / INFERENCE_FPS`. **Sin dataset de validación: se documenta
`ncnn@416` como supuesto** (tal como indica el plan textualmente para este caso), no como
resultado de una medición. `tools/benchmark.py::select_combo()` implementa esta misma regla
y, al no tener `mAP50-95`, devuelve exactamente esto (ver la salida real en §4.2).

`ncnn@416` además:

- Coincide con `MODEL_IMGSZ=416` (default ya fijado en `epp_edge/config.py`, Fase 2): no
  requiere ningún cambio de configuración para adoptarse.
- Es razonable a priori porque NCNN está pensado para CPU ARM (la Pi) y es más liviano que
  `pt`/PyTorch completo; 416 es un punto intermedio habitual entre velocidad (320) y
  precisión (640) para YOLOv8n en dispositivos de borde.

## 6. Pendiente (cuando haya una Raspberry Pi real o Linux disponible)

1. Rehacer `pip freeze` de `raspberrypi/requirements.txt` en la Pi real (aarch64): el
   archivo actual se generó en Windows/Python 3.14 y el propio archivo ya trae la advertencia
   de que `ultralytics`/`ncnn` no están validados en aarch64 (ver cabecera de
   `raspberrypi/requirements.txt` y `docs/HANDOFF_CLAUDE_CODE.md` §8).
2. Correr de verdad `python -m tools.export_model --format ncnn --imgsz 416` (y 320/640) en
   ese entorno para producir los pesos NCNN reales.
3. Correr de verdad `python -m tools.benchmark` (con `--data` apuntando a un `data.yaml` de
   validación real, si existe, para obtener `mAP50-95`) y **reemplazar la tabla de la §4.2**
   por números reales de latencia/FPS/temperatura de CPU.
4. Re-aplicar `select_combo()` con esos números reales; si cambia la combinación elegida
   respecto a `ncnn@416`, actualizar este documento y, si aplica, `MODEL_IMGSZ` en
   `edge.env`.
5. Si se reentrena el modelo para el trabajo futuro de la tesis (clase `person`/ausencia +
   tracking, §11 del plan), repetir este documento completo con el nuevo checkpoint (SHA-256,
   clases, fecha, benchmark).
