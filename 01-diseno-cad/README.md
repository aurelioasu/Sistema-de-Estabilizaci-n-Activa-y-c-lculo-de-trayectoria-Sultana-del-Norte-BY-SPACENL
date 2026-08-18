# Diseño CAD y aerodinámica

Modelos mecánicos, renders y evidencias CFD del vehículo Sultana del Norte y su sistema de superficies de control.

![Ensamble de Sultana del Norte](renders/ensamble-v1a.png)

## Contenido

| Ruta | Descripción |
| --- | --- |
| [`modelos/`](modelos/) | Modelo STL listo para inspección o fabricación |
| [`renders/`](renders/) | Ensamble, canards, comparativas y resultados de diseño |
| [`simulacion-cfd/`](simulacion-cfd/) | Capturas organizadas por condición y ángulo |
| [`material-complementario/`](material-complementario/) | Plano de propulsión separado del sistema de control |
| [`INVENTARIO.csv`](INVENTARIO.csv) | Origen, destino, tamaño y SHA-256 de cada archivo |

## Modelo 3D

El archivo principal es [`sultana-del-norte-final-naca.stl`](modelos/sultana-del-norte-final-naca.stl). La entrega contenía un STL “final corregido” y otro de “validación”; ambos tienen exactamente el mismo SHA-256, por lo que se conserva una sola copia con un nombre inequívoco.

Los modelos usados internamente por la aplicación —OBJ, STL y VTP— se encuentran en [`04-app-sultana/data/models/`](../04-app-sultana/data/models/).

## Evidencia CFD

Las 72 capturas se normalizaron en dos familias a 171 m/s:

| Ángulo | Turbulencia | Condiciones reales |
| ---: | ---: | ---: |
| 0° | 7 | 5 |
| 5° | 8 | 6 |
| 10° | 5 | 7 |
| 15° | 9 | 7 |
| 20° | 11 | 7 |

Las imágenes permiten comparar campos y tendencias, pero no contienen por sí solas una malla, un caso de solver ni todos los parámetros necesarios para reproducir el cálculo. Por ello se presentan como evidencia visual de la iteración de diseño, no como validación aerodinámica definitiva.

![Comparativa con viento adverso](renders/comparativa-viento-adverso.png)

## Material pesado

Los giros interactivos, MP4 y ZIP de visualización se incluirán en la Release `v1.0.0`. Se omitieron del historial normal las copias exactas que aparecían simultáneamente en las carpetas de documentación y renders.

## Material complementario de propulsión

[`plano-motor-knsb-snl-220-01.pdf`](material-complementario/plano-motor-knsb-snl-220-01.pdf) es un plano independiente del motor. Se conserva como referencia de propulsión y no debe interpretarse como plano del mecanismo de estabilización activa.

## Antes de fabricar

- Confirma unidades, escala y tolerancias en el programa CAD.
- Verifica holguras, ejes y límites mecánicos de los canards.
- Realiza pruebas de banco sin propulsión y con actuadores desenergizados al montar.
- No uses una captura CFD como sustituto de verificación estructural o aerodinámica.
