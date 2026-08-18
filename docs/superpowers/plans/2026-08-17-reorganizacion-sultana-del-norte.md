# Plan de implementación: repositorio Sultana del Norte v1.0.0

> **Ejecución:** completar cada tarea en orden, verificar su resultado y crear un commit temático antes de continuar.

**Objetivo:** transformar el material entregado y el código recuperado en un repositorio público limpio, organizado y verificable de Sultana del Norte, con firmware ESP32 compilable, aplicación Windows descargable y una portada visual.

**Arquitectura:** el repositorio se divide por disciplina (`01-diseno-cad`, `02-electronica`, `03-firmware-esp32`, `04-app-sultana`, `05-documentacion`). Git contiene fuentes y activos curados; GitHub Releases contiene ejecutables, videos y paquetes pesados. Una auditoría automática comprueba identidad, tamaños, secretos accidentales, rutas requeridas y bloqueos de seguridad.

**Tecnologías:** Arduino/ESP32-S3, Python 3.13, PySide6, C++/CMake, Go, pytest, CTest, PyInstaller, Git y GitHub Releases.

---

## Tarea 1: preparar el esqueleto y la auditoría del repositorio

**Archivos:**

- Crear: `.gitignore`
- Crear: `.gitattributes`
- Crear: `THIRD_PARTY_NOTICES.md`
- Crear: `MANIFESTO_DE_CONTENIDO.md`
- Crear: `tools/repo_audit.py`
- Crear: `tests/test_repository_hygiene.py`
- Crear: `01-diseno-cad/README.md`
- Crear: `02-electronica/README.md`
- Crear: `03-firmware-esp32/README.md`
- Crear: `04-app-sultana/README.md`
- Crear: `05-documentacion/README.md`

### Paso 1: escribir primero las pruebas de higiene

La prueba debe exigir las cinco áreas, comprobar que no haya archivos Git mayores de 100 MiB y rechazar nombres propios heredados a CANSAT fuera de una lista explícita de documentos históricos de diseño.

Ejecutar:

```powershell
& 'C:\Users\yeyoz\OneDrive\Escritorio\CANSAT\.venv\Scripts\python.exe' -m pytest tests/test_repository_hygiene.py -q
```

Resultado esperado: falla porque la estructura todavía no existe.

### Paso 2: crear el esqueleto mínimo y la herramienta de auditoría

La auditoría deberá usar únicamente la biblioteca estándar para que pueda ejecutarse sin instalar la aplicación.

### Paso 3: repetir la prueba

Resultado esperado: pasa con el repositorio vacío estructuralmente válido.

### Paso 4: commit

```powershell
git add .gitignore .gitattributes THIRD_PARTY_NOTICES.md MANIFESTO_DE_CONTENIDO.md tools tests 01-diseno-cad 02-electronica 03-firmware-esp32 04-app-sultana 05-documentacion
git commit -m "chore: scaffold curated Sultana repository"
```

## Tarea 2: importar y limpiar el firmware ESP32

**Archivos origen:**

- `C:/Users/yeyoz/OneDrive/Escritorio/CANSAT/CANSAT_ARDUINO_IDE/01_CanSat_Diagnostico/01_CanSat_Diagnostico.ino`
- `C:/Users/yeyoz/OneDrive/Escritorio/CANSAT/CANSAT_ARDUINO_IDE/02_CanSat_Vuelo/02_CanSat_Vuelo.ino`
- `C:/Users/yeyoz/OneDrive/Escritorio/CANSAT/CANSAT_ARDUINO_IDE/03_Estacion_Terrestre/03_Estacion_Terrestre.ino`

**Archivos destino:**

- Crear: `03-firmware-esp32/diagnostico/Diagnostico_Sultana/Diagnostico_Sultana.ino`
- Crear: `03-firmware-esp32/control-de-vuelo/Control_Vuelo_Sultana/Control_Vuelo_Sultana.ino`
- Crear: `03-firmware-esp32/estacion-terrena/Estacion_Terrena_Sultana/Estacion_Terrena_Sultana.ino`
- Crear: `tests/test_firmware_contract.py`
- Actualizar: `03-firmware-esp32/README.md`

### Paso 1: importar los tres sketches sin cambiar su lógica

Conservar el mapa de pines actual y todos los bloqueos de seguridad.

### Paso 2: escribir la prueba del contrato antes del renombrado

La prueba debe exigir:

- dirección de radio `SNL01` en todos los extremos aplicables;
- nombres y mensajes de Sultana;
- ausencia de identificadores propios `CANS1`, `MAGIA_CANSAT` y `ARMAR CANSAT`;
- valores `false` en `PERMITIR_MOVIMIENTO_CANARDS`, `MEZCLA_Y_SENTIDOS_VALIDADOS` y `HABILITAR_SALIDA_PARACAIDAS`.

Resultado esperado: falla con los identificadores heredados.

### Paso 3: aplicar el renombrado coordinado

Cambiar únicamente nombres y protocolo propio; no modificar ganancias, pines ni habilitar actuadores.

### Paso 4: ejecutar pruebas y compilar sketches

```powershell
& 'C:\Users\yeyoz\OneDrive\Escritorio\CANSAT\.venv\Scripts\python.exe' -m pytest tests/test_firmware_contract.py -q
& 'C:\Program Files\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe' compile --fqbn esp32:esp32:esp32s3 03-firmware-esp32/diagnostico/Diagnostico_Sultana
& 'C:\Program Files\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe' compile --fqbn esp32:esp32:esp32s3 03-firmware-esp32/control-de-vuelo/Control_Vuelo_Sultana
& 'C:\Program Files\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe' compile --fqbn esp32:esp32:esp32s3 03-firmware-esp32/estacion-terrena/Estacion_Terrena_Sultana
```

Resultado esperado: prueba y tres compilaciones exitosas.

### Paso 5: commit

```powershell
git add 03-firmware-esp32 tests/test_firmware_contract.py
git commit -m "feat: publish cleaned ESP32 firmware"
```

## Tarea 3: importar la aplicación y conservar la línea base

**Directorios origen:**

- `C:/Users/yeyoz/OneDrive/Escritorio/CANSAT/python/`
- `C:/Users/yeyoz/OneDrive/Escritorio/CANSAT/cpp/`
- `C:/Users/yeyoz/OneDrive/Escritorio/CANSAT/kutta/`
- `C:/Users/yeyoz/OneDrive/Escritorio/CANSAT/configs/`
- `C:/Users/yeyoz/OneDrive/Escritorio/CANSAT/data/`

**Archivos origen adicionales:**

- `pyproject.toml`
- `CMakeLists.txt`
- `CMakePresets.json`
- `vcpkg.json`
- `run_all.py`
- `build_exe.py`
- `build_kutta.py`

**Destino:** `04-app-sultana/`

### Paso 1: copiar únicamente fuentes reproducibles

Excluir `.venv`, `__pycache__`, `*.egg-info`, `build`, `out`, `output`, resultados, capturas temporales y ejecutables previos.

### Paso 2: ajustar rutas de construcción a la nueva raíz

Mantener `python`, `cpp`, `kutta`, `configs` y `data` como subdirectorios directos de `04-app-sultana` para minimizar cambios.

### Paso 3: ejecutar la línea base desde la ubicación nueva

```powershell
& 'C:\Users\yeyoz\OneDrive\Escritorio\CANSAT\.venv\Scripts\python.exe' -m pytest 04-app-sultana/python/tests -q
cmake -S 04-app-sultana -B 04-app-sultana/build/cpp -DBUILD_TESTING=ON
cmake --build 04-app-sultana/build/cpp --config Release
ctest --test-dir 04-app-sultana/build/cpp -C Release --output-on-failure
Push-Location 04-app-sultana/kutta; go test ./...; Pop-Location
```

Resultado esperado: mismas pruebas funcionales que la fuente original.

### Paso 4: commit

```powershell
git add 04-app-sultana
git commit -m "feat: import Sultana desktop application sources"
```

## Tarea 4: limpiar identidad y comportamiento visible de la aplicación

**Archivos:**

- Crear: `tests/test_application_identity.py`
- Modificar: `04-app-sultana/python/app/**/*.py`
- Modificar: `04-app-sultana/python/tests/**/*.py`
- Renombrar/modificar: `04-app-sultana/kutta/cansat.go`
- Renombrar/modificar: `04-app-sultana/kutta/cansat_test.go`
- Modificar: `04-app-sultana/kutta/**/*.go`
- Modificar: `04-app-sultana/kutta/web/*`
- Modificar: `04-app-sultana/build_exe.py`
- Modificar: `04-app-sultana/README.md`

### Paso 1: escribir una prueba de identidad que inicialmente falle

Debe inspeccionar rutas y textos propios publicables, ignorar licencias de terceros y exigir que la interfaz se identifique como Sultana del Norte.

### Paso 2: reemplazar referencias por contexto

- producto: `Sultana del Norte`;
- vehículo genérico: `vehículo` o `cohete`;
- integración propia de Kutta: archivos y símbolos `sultana`;
- ejecutable: `Sultana-del-Norte`.

No alterar algoritmos numéricos ni textos de licencias de terceros.

### Paso 3: ejecutar pruebas específicas y completas

```powershell
& 'C:\Users\yeyoz\OneDrive\Escritorio\CANSAT\.venv\Scripts\python.exe' -m pytest tests/test_application_identity.py 04-app-sultana/python/tests -q
Push-Location 04-app-sultana/kutta; go test ./...; Pop-Location
ctest --test-dir 04-app-sultana/build/cpp -C Release --output-on-failure
```

### Paso 4: commit

```powershell
git add 04-app-sultana tests/test_application_identity.py
git commit -m "refactor: complete Sultana product identity cleanup"
```

## Tarea 5: curar CAD, CFD y electrónica

**Origen CAD:** `C:/Users/yeyoz/Downloads/drive-download-20260817T201427Z-1-001/DISEÑO 3D/`

**Origen electrónica:** `C:/Users/yeyoz/Downloads/drive-download-20260817T201427Z-1-001/Electronica/`

**Destinos:**

- `01-diseno-cad/modelos/`
- `01-diseno-cad/renders/`
- `01-diseno-cad/simulacion-cfd/`
- `01-diseno-cad/material-complementario/`
- `02-electronica/esquematicos/`
- `02-electronica/pcb/`
- `02-electronica/bom/`
- `02-electronica/modelos-3d/`

### Paso 1: calcular hashes y crear un inventario de decisiones

Registrar archivo original, hash, destino, categoría y motivo de exclusión. Eliminar copias exactas entre `Img_Doc` y `Renders`.

### Paso 2: copiar archivos editables y evidencias técnicas

- modelos STL finales y de validación;
- STEP/OBJ/MTL de PCB;
- esquema SVG y PNG;
- BOM interactiva;
- renders representativos;
- resultados CFD organizados por condición y ángulo;
- plano KNSB únicamente como material complementario de propulsión.

Los MP4, ZIP, HTML de giros 3D y series fotográficas completas irán al paquete de Release cuando sean demasiado pesados o redundantes para Git.

### Paso 3: actualizar README de ambas áreas

Documentar formatos, revisiones, discrepancia de pines y límites de validación.

### Paso 4: auditar y commit

```powershell
& 'C:\Users\yeyoz\OneDrive\Escritorio\CANSAT\.venv\Scripts\python.exe' tools/repo_audit.py
git add 01-diseno-cad 02-electronica MANIFESTO_DE_CONTENIDO.md
git commit -m "docs: curate CAD CFD and electronics assets"
```

## Tarea 6: curar documentación y atribuciones

**Origen principal:**

- `Reporte_ExpoCNL_Sultana_del_Norte_2026_final.docx`
- `Reporte_Sultana_Final_Completo_diselo CAD.docx`
- documentación técnica de Arduino, electrónica y aplicación;
- `C:/Users/yeyoz/OneDrive/Escritorio/CANSAT/kutta/LICENSE`.

**Archivos destino:**

- `05-documentacion/informe-tecnico/`
- `05-documentacion/manuales/`
- `05-documentacion/arquitectura/`
- `05-documentacion/material-complementario/`
- `THIRD_PARTY_NOTICES.md`
- `05-documentacion/README.md`

### Paso 1: seleccionar la versión autoritativa

Usar el informe Sultana más reciente como documento principal. No publicar borradores duplicados como si fueran versiones vigentes.

### Paso 2: generar versiones públicas limpias

Renombrar títulos y referencias propias; conservar atribuciones. Convertir a PDF mediante Microsoft Word cuando corresponda y renderizar el PDF para inspección visual.

### Paso 3: verificar documentos

- extraer texto y buscar identidad heredada;
- renderizar páginas y revisar portada, tablas, figuras y saltos;
- comprobar que no existan instrucciones internas o credenciales.

### Paso 4: commit

```powershell
git add 05-documentacion THIRD_PARTY_NOTICES.md MANIFESTO_DE_CONTENIDO.md
git commit -m "docs: publish curated technical documentation"
```

## Tarea 7: construir la portada visual

**Archivos:**

- Reemplazar: `README.md`
- Crear: `assets/readme/hero.png`
- Crear: `assets/readme/cad.png`
- Crear: `assets/readme/cfd.png`
- Crear: `assets/readme/electronica.png`
- Crear: `assets/readme/app-simulador.png`
- Crear: `assets/readme/app-telemetria.png`
- Crear: `assets/readme/app-laboratorio-cfd.png`
- Crear: `assets/readme/app-tunel-viento.png`

### Paso 1: seleccionar y optimizar imágenes existentes

Usar recursos de Space NL, ensamble CAD, comparativa CFD, PCB y capturas reales de la aplicación. Corregir o volver a capturar cualquier pantalla que muestre identidad CANSAT.

### Paso 2: redactar el README

Incluir resumen, arquitectura, galería, navegación por áreas, descarga, compilación, estado de pruebas, limitaciones, seguridad, atribuciones y licencia.

### Paso 3: verificar enlaces y peso

Ejecutar la auditoría y comprobar manualmente todas las rutas de imagen y navegación.

### Paso 4: commit

```powershell
git add README.md assets/readme
git commit -m "docs: create visual Sultana project homepage"
```

## Tarea 8: compilar y comprobar la aplicación Windows

**Archivos:**

- Modificar si es necesario: `04-app-sultana/build_exe.py`
- Generar fuera de Git: `release-assets/Sultana-del-Norte-v1.0.0-Windows-x64.exe`
- Generar fuera de Git: `release-assets/Sultana-del-Norte-v1.0.0-Windows-x64-portable.zip`

### Paso 1: ejecutar todas las pruebas antes de compilar

No construir una versión si Python, C++, Go o firmware fallan.

### Paso 2: crear EXE y ZIP portable desde fuentes limpias

Usar PyInstaller y los recursos versionados. No reutilizar el binario anterior.

### Paso 3: abrir y recorrer la aplicación

Comprobar splash, ventana principal, simulador 3D, telemetría, CFD y túnel de viento. Confirmar que no hay etiquetas heredadas.

### Paso 4: calcular hashes

```powershell
Get-FileHash release-assets/* -Algorithm SHA256
```

Guardar: `release-assets/SHA256SUMS.txt`.

## Tarea 9: preparar el paquete multimedia y la Release

**Archivos fuera de Git:**

- `release-assets/Expociencias-Sultana-del-Norte-2026.mp4`
- `release-assets/Material-tecnico-y-multimedia-Sultana-v1.0.0.zip`
- `release-assets/SHA256SUMS.txt`

### Paso 1: construir el archivo complementario

Incluir fotografías, videos, giros 3D y recursos técnicos útiles que no se versionaron. Excluir duplicados exactos y archivos ajenos a Sultana.

### Paso 2: validar límites e integridad

Cada activo debe medir menos de 2 GiB y su hash debe coincidir con `SHA256SUMS.txt`.

### Paso 3: redactar notas de la versión

Crear localmente `release-assets/RELEASE_NOTES.md` con contenido, requisitos, advertencia de binario sin firma y hashes.

## Tarea 10: verificación final y publicación

### Paso 1: ejecutar verificación fresca

```powershell
& 'C:\Users\yeyoz\OneDrive\Escritorio\CANSAT\.venv\Scripts\python.exe' -m pytest tests 04-app-sultana/python/tests -q
ctest --test-dir 04-app-sultana/build/cpp -C Release --output-on-failure
Push-Location 04-app-sultana/kutta; go test ./...; Pop-Location
& 'C:\Users\yeyoz\OneDrive\Escritorio\CANSAT\.venv\Scripts\python.exe' tools/repo_audit.py
git diff --check
git status --short
```

Repetir las tres compilaciones Arduino y revisar resultados reales.

### Paso 2: revisar el conjunto exacto de commits

```powershell
git log --oneline main..HEAD
git diff --stat main...HEAD
```

### Paso 3: publicar rama e integrar

Confirmar autenticación de GitHub, subir `codex/reorganizacion-sultana-v1`, revisar el contenido y actualizar `main` sin reescribir el historial remoto.

### Paso 4: crear Release `v1.0.0`

Subir los activos desde `release-assets`, comprobar las URLs de descarga y verificar que el README apunte a la Release correcta.

### Paso 5: comprobación remota

Abrir la portada pública, navegar las cinco áreas y comprobar cada activo de la Release. Solo entonces declarar completada la publicación.
