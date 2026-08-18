# Aplicación Sultana del Norte

Aplicación de escritorio para simulación 6-DoF, cálculo de trayectoria, telemetría, análisis CFD y visualización del vehículo de Sultana del Norte.

## Funciones principales

- Simulación tridimensional de vuelo y trayectoria.
- Configuración del vehículo, entorno, masa y curva de empuje.
- Importación de proyectos OpenRocket.
- Telemetría serie y análisis de datos recibidos.
- Ensayos CFD mediante OpenFOAM cuando Docker está disponible.
- Túnel de viento 2D cualitativo basado en Kutta.
- Monte Carlo, clima, terreno y exportación de resultados.

## Organización

| Ruta | Contenido |
| --- | --- |
| [`python/`](python/) | Interfaz PySide6, servicios y pruebas |
| [`cpp/`](cpp/) | Núcleo físico C++ y enlace para Python |
| [`kutta/`](kutta/) | Túnel de viento 2D en Go, bajo licencia MIT |
| [`configs/`](configs/) | Vehículos, entornos y escenarios reproducibles |
| [`data/`](data/) | Modelos 3D, calibración y tablas preliminares |
| [`run_all.py`](run_all.py) | Preparación y verificación integral |
| [`build_exe.py`](build_exe.py) | Construcción del ejecutable Windows |

## Descargar la aplicación

La versión lista para Windows se publica en [GitHub Releases](https://github.com/aurelioasu/Sistema-de-Estabilizaci-n-Activa-y-c-lculo-de-trayectoria-Sultana-del-Norte-BY-SPACENL/releases/latest).

Descargas previstas para `v1.0.0`:

- `Sultana-del-Norte-v1.0.0-Windows-x64.exe`
- `Sultana-del-Norte-v1.0.0-Windows-x64-portable.zip`

El binario no cuenta con firma digital comercial. Windows puede mostrar una advertencia de SmartScreen; verifica primero la suma SHA-256 publicada en la misma Release.

## Ejecutar desde el código fuente

Requisitos recomendados:

- Windows 10/11 x64;
- Python 3.11 o posterior;
- Visual Studio 2022 Build Tools con C++;
- CMake 3.24 o posterior;
- Docker Desktop únicamente para ejecutar casos OpenFOAM.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install cmake ninja
.\.venv\Scripts\python.exe -m pip install -e ".[3d,export,dev]"
.\.venv\Scripts\python.exe run_all.py --no-install
.\.venv\Scripts\python.exe -m app.bootstrap
```

La interfaz puede abrir sin Docker. El servicio solo es necesario al iniciar una simulación CFD externa.

## Pruebas

```powershell
.\.venv\Scripts\python.exe -m pytest python/tests -q
ctest --test-dir build/windows-current -C Release --output-on-failure
Push-Location kutta
go test ./...
Pop-Location
```

Línea base de la versión `v1.0.0`:

- 112 pruebas Python de la aplicación;
- 1 ejecutable de pruebas C++;
- pruebas Go en los paquetes Kutta, foil, LBM, scene, sceneio y viz.

## Construir el ejecutable

Después de preparar el entorno, compilar el núcleo y generar Kutta:

```powershell
.\.venv\Scripts\python.exe -m pip install pyinstaller
.\.venv\Scripts\python.exe build_kutta.py
.\.venv\Scripts\python.exe build_exe.py
```

El resultado local es `output/Sultana-del-Norte.exe`. Los paquetes versionados y sus hashes se generan fuera del historial Git.

## Alcance de validación

Los datos aerodinámicos, propiedades de masa, curvas de empuje y ganancias incluidas son preliminares o de ensayo salvo que su archivo indique lo contrario. Kutta es una herramienta cualitativa para observar patrones de flujo; no sustituye CFD validado ni ensayos físicos.

## Licencia de terceros

Kutta fue desarrollado por Cesar Gimenes y se distribuye bajo licencia MIT. Consulta [`kutta/LICENSE`](kutta/LICENSE) y el archivo raíz [`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md).
