# Documentación de Sultana del Norte

Esta sección reúne la documentación técnica curada del **sistema de estabilización y corrección activa de vuelo Sultana del Norte**. Los archivos se separan por vigencia para evitar que un documento anterior se interprete como la definición actual del hardware o del software.

## Documento vigente

| Documento | Estado | Formatos |
|---|---|---|
| Reporte técnico Sultana del Norte 2026 | **Referencia integral vigente** | [PDF](informe-tecnico/Reporte-tecnico-Sultana-del-Norte-2026.pdf) · [DOCX editable](informe-tecnico/Reporte-tecnico-Sultana-del-Norte-2026.docx) |

El reporte técnico de 84 páginas integra planteamiento, arquitectura, electrónica, modelo físico, control, firmware, CAD, CFD, aplicación, validación declarada, limitaciones y anexos. Cuando exista una discrepancia con un documento de mayo de 2026, prevalecen el código versionado, los README de cada área y este reporte final.

## Material complementario

| Documento | Estado | Uso recomendado | Formatos |
|---|---|---|---|
| Informe de diseño CAD | Complementario | Geometría, integración mecánica, configuración de cuatro superficies y CFD preliminar | [PDF](material-complementario/Informe-diseno-CAD-Sultana-del-Norte.pdf) · [DOCX](material-complementario/Informe-diseno-CAD-Sultana-del-Norte.docx) |
| Protocolo ExpoCiencias NL, mayo de 2026 | **Histórico** | Trazabilidad del desarrollo y de la propuesta inicial | [PDF](material-complementario/Protocolo-ExpoCiencias-NL-mayo-2026.pdf) · [DOCX](material-complementario/Protocolo-ExpoCiencias-NL-mayo-2026.docx) |

> [!CAUTION]
> El protocolo de mayo describe una etapa anterior —incluidos BNO055, dos canards y frecuencias preliminares—. No debe usarse como mapa de conexiones, lista de componentes ni especificación de firmware vigente.

## Manuales operativos vigentes

- [Aplicación Sultana del Norte](../04-app-sultana/README.md): instalación, ejecución, telemetría, simulación, CFD y compilación.
- [Firmware ESP32-S3](../03-firmware-esp32/README.md): sketches, bibliotecas, compilación, pines y bloqueos de seguridad.
- [Electrónica y aviónica](../02-electronica/README.md): esquemáticos, PCB, BOM y discrepancias que requieren validación física.
- [Diseño CAD y CFD](../01-diseno-cad/README.md): modelo 3D, renders, condiciones simuladas e inventario.

## Arquitectura

La vista compacta de componentes y flujo de datos está en [arquitectura-del-sistema.md](arquitectura/arquitectura-del-sistema.md).

## Curaduría

Los manuales antiguos que todavía presentaban el proyecto bajo una identidad ajena no se publicaron: su contenido útil se consolidó en los README vigentes. Los documentos incluidos fueron revisados tanto en texto como visualmente; los PDF son exportaciones verificadas de sus fuentes DOCX. Los tamaños y hashes de estos archivos están en [INVENTARIO.csv](INVENTARIO.csv).
