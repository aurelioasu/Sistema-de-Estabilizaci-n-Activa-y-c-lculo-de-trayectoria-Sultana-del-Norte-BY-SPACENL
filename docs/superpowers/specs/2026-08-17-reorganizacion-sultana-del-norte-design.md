# Diseño de reorganización y publicación de Sultana del Norte

Fecha: 2026-08-17

Estado: aprobado por el propietario para ejecución

Repositorio: `aurelioasu/Sistema-de-Estabilizaci-n-Activa-y-c-lculo-de-trayectoria-Sultana-del-Norte-BY-SPACENL`

## 1. Objetivo

Convertir el material disperso del proyecto en un repositorio público profesional, entendible y reproducible para **Sultana del Norte**, un sistema de estabilización y corrección activa de vuelo. La portada deberá explicar el proyecto visualmente y conducir a cuatro áreas principales: diseño mecánico/CAD, electrónica, firmware ESP32 y aplicación de escritorio.

La publicación debe incluir el código fuente, documentación técnica, archivos de fabricación y descargas ejecutables, sin presentar el proyecto como un CANSAT.

## 2. Alcance aprobado

- Limpiar completamente la identidad heredada de CANSAT en rutas, nombres de módulos propios, comentarios, textos visibles, documentación curada y aplicación.
- Mantener intactos los créditos, licencias y nombres legítimos de dependencias de terceros.
- Organizar todos los archivos técnicamente útiles entregados y recuperados del área de trabajo.
- Eliminar duplicados exactos y separar material irrelevante o ajeno al sistema de estabilización.
- Incluir código fuente de la aplicación y firmware compilable para ESP32.
- Generar una nueva aplicación Windows después de la limpieza y publicar tanto el ejecutable individual como un paquete portable ZIP.
- Crear un README principal detallado, visual y en español.
- Publicar archivos grandes mediante GitHub Releases, no dentro del historial normal de Git.

## 3. Fuentes de material

### Entrega documental y multimedia

`C:/Users/yeyoz/Downloads/drive-download-20260817T201427Z-1-001/`

Contiene documentos explicativos, CAD, electrónica, fotografías, renders, videos y material de presentación.

### Código y aplicación recuperados

`C:/Users/yeyoz/OneDrive/Escritorio/CANSAT/`

Contiene firmware ESP32, aplicación Python, núcleo C++, módulo Kutta, configuraciones, recursos, pruebas y herramientas de compilación. Las carpetas de entornos virtuales, cachés, resultados temporales y compilaciones previas no se importarán.

## 4. Estructura pública

```text
README.md
LICENSES/
THIRD_PARTY_NOTICES.md
assets/
  readme/
01-diseno-cad/
  modelos/
  fabricacion/
  renders/
  simulacion-cfd/
  README.md
02-electronica/
  esquematicos/
  pcb/
  bom/
  modelos-3d/
  README.md
03-firmware-esp32/
  diagnostico/
  control-de-vuelo/
  estacion-terrena/
  README.md
04-app-sultana/
  python/
  cpp/
  kutta/
  configuraciones/
  recursos/
  pruebas/
  herramientas/
  README.md
05-documentacion/
  informe-tecnico/
  manuales/
  arquitectura/
  material-complementario/
  README.md
docs/
  superpowers/specs/
  superpowers/plans/
```

Se usarán nombres ASCII y rutas sin espacios para facilitar compilaciones en Windows, Linux y automatizaciones.

## 5. Política de limpieza

### Identidad del proyecto

La identidad pública será **Sultana del Norte** y la descripción base será **sistema de estabilización y corrección activa de vuelo**.

Los identificadores propios heredados se renombrarán de manera coherente. Ejemplos:

- Carpetas y sketches `CANSAT_*` pasarán a nombres `Sultana_*` o descriptivos.
- Mensajes como `ARMAR CANSAT` pasarán a `ARMAR SULTANA`.
- La dirección de radio propia `CANS1` se reemplazará de forma coordinada por `SNL01` en transmisor, receptor, diagnóstico y pruebas.
- Variables propias como `MAGIA_CANSAT` pasarán a `MAGIA_SULTANA`.
- Etiquetas visibles de la interfaz usarán “Sultana del Norte”, “vehículo” o “sistema de vuelo”, según el contexto.

No se hará un reemplazo textual ciego: cada cambio deberá conservar compatibilidad entre los componentes del protocolo.

### Duplicados y exclusiones

- Se conservará una sola copia de archivos con contenido idéntico.
- Se priorizará la versión editable o de mayor calidad cuando existan varias representaciones del mismo elemento.
- Material exclusivamente relacionado con CANSAT y sin relación técnica con Sultana del Norte no se publicará.
- El plano del motor KNSB se clasificará como material complementario de propulsión, no como parte del sistema de estabilización.
- Se generará un manifiesto de procedencia y exclusiones para que la limpieza sea auditable.

### Archivos generados

No se versionarán `.venv`, `build`, `out`, cachés, resultados temporales ni binarios antiguos. El nuevo ejecutable se publicará únicamente después de completar las pruebas.

## 6. Arquitectura mostrada al visitante

El README describirá el flujo del sistema de forma breve:

```text
Sensores de vuelo
      ↓
ESP32-S3 y estimación de actitud
      ↓
Control PID y lógica de seguridad
      ↓
Actuadores/canards
      ↓
Telemetría hacia la estación y aplicación Sultana
      ↓
Visualización 3D, análisis, CFD y trayectoria
```

La documentación no afirmará capacidades que no estén respaldadas por código, pruebas o archivos técnicos.

## 7. Seguridad de hardware

Los bloqueos existentes del firmware permanecerán desactivados por defecto:

- movimiento de canards;
- mezcla y sentidos de control;
- salida de paracaídas.

El README advertirá que las pruebas con actuadores, radio, alimentación y mecanismos deben realizarse en banco, con el vehículo inmovilizado y bajo supervisión competente.

Existe una discrepancia entre algunos pines mostrados en el esquema electrónico y los definidos en el firmware. No se inventará una corrección: el firmware documentará su mapa actual y la sección de electrónica marcará el esquema como revisión pendiente de validación física.

## 8. Portada visual

El README principal tendrá:

1. Cabecera con nombre, propósito y marca Space NL.
2. Imagen principal del ensamble o una composición limpia con CAD, PCB y aplicación.
3. Insignias sencillas para ESP32-S3, Python, C++, Arduino y plataforma Windows, sin simular estados de CI inexistentes.
4. Resumen ejecutivo y características principales.
5. Diagrama de arquitectura.
6. Galería compacta de CAD, CFD, electrónica y capturas de la aplicación.
7. Tabla de navegación hacia las cuatro áreas del proyecto.
8. Instrucciones de descarga y ejecución de la aplicación.
9. Instrucciones de compilación para firmware y aplicación.
10. Estado de validación, limitaciones y advertencias de seguridad.
11. Créditos, componentes de terceros y estado de licencia.

Las imágenes se optimizarán para web y usarán rutas relativas, evitando videos o fotografías masivas dentro de la portada.

## 9. Aplicación Windows

La limpieza de la aplicación incluirá código Python, C++, interfaz, recursos y pruebas. Después se ejecutará la suite completa y se generarán dos activos:

- `Sultana-del-Norte-v1.0.0-Windows-x64.exe`
- `Sultana-del-Norte-v1.0.0-Windows-x64-portable.zip`

El ZIP portable será la descarga recomendada si ofrece un arranque más rápido o una extracción más confiable. Se publicará una suma SHA-256 para cada activo. La falta de firma digital se indicará claramente para no confundir una advertencia de Windows con una infección.

## 10. GitHub Release v1.0.0

Los activos grandes previstos son:

- ejecutable Windows;
- paquete portable ZIP;
- video principal de Expociencias;
- paquete de medios seleccionados y material técnico pesado;
- archivo de sumas SHA-256;
- manifiesto de contenido y procedencia.

Git no almacenará archivos individuales mayores a 100 MiB. Los activos de Release se mantendrán por debajo de 2 GiB cada uno.

## 11. Licencias y procedencia

- Se conservará la licencia MIT del componente Kutta y cualquier otra licencia incluida por dependencias.
- Se creará `THIRD_PARTY_NOTICES.md` con atribuciones verificables.
- No se asignará automáticamente una licencia abierta al trabajo propio. Mientras el propietario no elija una, el README indicará que no se concede una licencia de reutilización del código propio.
- Ningún secreto, credencial, token o archivo de configuración privado se publicará.

## 12. Verificación requerida

Antes de publicar:

- pruebas Python completas;
- compilación y pruebas C++;
- pruebas Go/Kutta;
- compilación de los tres sketches ESP32 con las dependencias declaradas;
- búsqueda de referencias heredadas a CANSAT en el contenido público;
- búsqueda de secretos y archivos privados;
- comprobación de enlaces e imágenes del README;
- comprobación de que ningún archivo Git supere 100 MiB;
- apertura real del ejecutable recién generado;
- cálculo y comprobación de sumas SHA-256;
- inspección del estado Git y del contenido exacto a publicar.

## 13. Criterios de aceptación

El trabajo se considera terminado cuando:

1. El repositorio muestra una portada visual y detallada de Sultana del Norte.
2. Las cuatro áreas solicitadas son fáciles de localizar y tienen su propio README.
3. El firmware y las pruebas de software pasan desde la estructura reorganizada.
4. No hay identidad CANSAT en el contenido público propio del proyecto.
5. La aplicación tiene código fuente y una descarga Windows verificable.
6. Los archivos grandes están disponibles en una Release y no dañan el historial Git.
7. Se conserva la atribución legal de terceros y no se publican secretos.

## 14. Riesgos y mitigaciones

- **Renombrado rompe protocolos:** cambiar identificadores en todos los extremos y cubrirlos con pruebas.
- **Documentos antiguos contradicen el código:** presentar el firmware compilado como fuente vigente y marcar revisiones históricas.
- **Ejecutable demasiado grande:** publicar en Releases y ofrecer ZIP portable.
- **Material duplicado o irrelevante:** usar hashes e inventario de exclusiones.
- **Advertencias de Windows:** explicar que el binario no está firmado y publicar hashes.
- **Discrepancias de pines:** no activar actuadores hasta validar el cableado físico.

## 15. Estrategia de publicación

La implementación se preparará en una rama dedicada con commits temáticos. Después de las verificaciones se subirá la rama, se integrará en `main` y se creará la Release `v1.0.0`. La publicación no modificará ni eliminará el material fuente original fuera del repositorio de trabajo.
