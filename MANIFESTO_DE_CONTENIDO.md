# Manifiesto de contenido

Este documento registra cómo se transforma la entrega original en la publicación curada de Sultana del Norte.

## Reglas de selección

- Se conserva una sola copia de cada archivo idéntico.
- Se priorizan modelos editables, archivos de fabricación y evidencias técnicas legibles.
- Los videos, fotografías completas y paquetes pesados se entregan mediante GitHub Releases.
- Se excluye material ajeno al sistema de estabilización y corrección activa de vuelo.
- La documentación vigente se distingue de borradores y revisiones históricas.
- No se publican entornos virtuales, cachés, resultados temporales ni binarios antiguos.

## Estado por área

| Área | Fuente | Destino | Estado |
| --- | --- | --- | --- |
| Diseño mecánico y CFD | Entrega técnica | `01-diseno-cad/` | Curado: 97 archivos en Git y 12 activos únicos reservados para Release |
| Electrónica | Entrega técnica | `02-electronica/` | Curado: 15 de 15 archivos incluidos |
| Firmware ESP32 | Área de trabajo local | `03-firmware-esp32/` | Completo: 3 sketches limpios y compilados |
| Aplicación | Área de trabajo local | `04-app-sultana/` | Completo: fuentes Python, C++, Go, configuraciones y datos |
| Documentación | Entrega técnica | `05-documentacion/` | Curada: 3 documentos en DOCX/PDF, arquitectura y manuales enlazados al código |
| Multimedia pesado | Entrega técnica | GitHub Release | Pendiente de empaquetado |

## Decisiones verificadas

### Diseño CAD y CFD

- La entrega contiene 126 archivos y 109 contenidos únicos por SHA-256.
- Se incluyeron en Git 97 contenidos únicos: modelo STL, plano complementario, renders y 72 resultados CFD.
- Doce contenidos dinámicos únicos —HTML, MP4 y ZIP— se reservaron para la Release.
- Se descartaron 17 copias exactas; entre ellas, los dos STL con nombres de revisión distintos resultaron idénticos.
- El detalle archivo por archivo se encuentra en [`01-diseno-cad/INVENTARIO.csv`](01-diseno-cad/INVENTARIO.csv).

### Electrónica

- Los 15 archivos entregados son distintos por SHA-256 y se conservaron.
- STEP, OBJ, MTL y BOM mantienen su formato original; las rutas públicas se normalizaron.
- El detalle se encuentra en [`02-electronica/INVENTARIO.csv`](02-electronica/INVENTARIO.csv).

### Software

- Se excluyeron entornos virtuales, cachés, resultados, compilaciones y ejecutables anteriores.
- La aplicación conserva 46 archivos Python, 5 C++, 107 de Kutta, 7 configuraciones y 31 recursos de datos.
- La identidad anterior fue eliminada de rutas, interfaz, protocolo propio y textos públicos.

### Documentación

- El reporte técnico final de 84 páginas se conserva como referencia integral vigente en DOCX y PDF verificado.
- El informe CAD se conserva como material complementario en ambos formatos.
- El protocolo de mayo de 2026 se conserva como revisión histórica y se advierte que no define el hardware ni el firmware vigentes.
- Cinco manuales anteriores con identidad heredada no se publican; sus instrucciones útiles se consolidaron en los README de firmware, electrónica y aplicación.
- El detalle y los hashes se encuentran en [`05-documentacion/INVENTARIO.csv`](05-documentacion/INVENTARIO.csv).

### Licencias

- Se conserva el texto MIT original del componente Kutta en [`LICENSES/Kutta-MIT.txt`](LICENSES/Kutta-MIT.txt).
- Las atribuciones y el estado de licencia del código propio se explican en [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

La tabla volverá a actualizarse al cerrar los paquetes y la Release `v1.0.0`.
