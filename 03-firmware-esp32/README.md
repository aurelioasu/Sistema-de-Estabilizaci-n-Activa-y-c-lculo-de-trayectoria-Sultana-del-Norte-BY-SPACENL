# Firmware ESP32-S3

Esta sección contiene los tres programas de aviónica de Sultana del Norte. Los sketches se mantienen separados para que cada función pueda probarse y cargarse desde Arduino IDE sin depender de la aplicación de escritorio.

## Programas

| Programa | Función | Ruta |
| --- | --- | --- |
| Diagnóstico | Pruebas de sensores, almacenamiento, GPS, radio y servos en banco | [`diagnostico/Diagnostico_Sultana/`](diagnostico/Diagnostico_Sultana/) |
| Control de vuelo | Estimación de actitud, control PID, telemetría y máquina de estados | [`control-de-vuelo/Control_Vuelo_Sultana/`](control-de-vuelo/Control_Vuelo_Sultana/) |
| Estación terrena | Recepción nRF24 y salida de telemetría por puerto serie | [`estacion-terrena/Estacion_Terrena_Sultana/`](estacion-terrena/Estacion_Terrena_Sultana/) |

## Plataforma verificada

- Tarjeta: ESP32-S3 Dev Module
- FQBN: `esp32:esp32:esp32s3`
- Núcleo ESP32: 3.3.2
- Monitor serie: 115200 baud
- Radio: nRF24L01, canal 76, 250 kbps, CRC de 16 bits
- Dirección del proyecto: `SNL01`
- Protocolo de telemetría: versión 2, paquetes de 32 bytes

Bibliotecas verificadas:

- Adafruit BNO08x 1.2.5
- Adafruit BMP3XX 2.1.6
- Adafruit BusIO
- Adafruit Unified Sensor
- ESP32Servo 3.1.3
- RF24 1.5.0
- TinyGPSPlus

## Mapa de pines vigente en el firmware

| Señal | GPIO |
| --- | ---: |
| BNO085 reset | 4 |
| BNO085 interrupción | 5 |
| microSD CS | 6 |
| nRF24 IRQ | 7 |
| I²C SCL | 9 |
| nRF24 CSN | 10 |
| SPI MOSI | 11 |
| SPI MISO | 12 |
| SPI SCK | 13 |
| nRF24 CE | 14 |
| GPS PPS | 15 |
| GPS RX | 16 |
| GPS TX | 17 |
| I²C SDA | 18 |
| Servos 1–4 | 41, 2, 21, 47 |
| Alerta de paracaídas | 40 |

> [!CAUTION]
> Algunos archivos visuales de electrónica corresponden a una revisión distinta del cableado. Antes de energizar actuadores, valida este mapa contra la PCB fabricada y el arnés físico.

## Bloqueos de seguridad

El control de vuelo se distribuye con estas salidas deshabilitadas:

```cpp
const bool PERMITIR_MOVIMIENTO_CANARDS = false;
const bool MEZCLA_Y_SENTIDOS_VALIDADOS = false;
const bool HABILITAR_SALIDA_PARACAIDAS = false;
```

No cambies estos valores hasta terminar la calibración mecánica, confirmar los sentidos de cada servo y validar la matriz de mezcla. La salida de alerta nunca debe conectarse directamente a una carga de despliegue.

## Compilación

Desde la raíz del repositorio, con Arduino CLI y el núcleo ESP32 instalados:

```powershell
arduino-cli compile --fqbn esp32:esp32:esp32s3 03-firmware-esp32/diagnostico/Diagnostico_Sultana
arduino-cli compile --fqbn esp32:esp32:esp32s3 03-firmware-esp32/control-de-vuelo/Control_Vuelo_Sultana
arduino-cli compile --fqbn esp32:esp32:esp32s3 03-firmware-esp32/estacion-terrena/Estacion_Terrena_Sultana
```

## Comandos de banco

El sketch de diagnóstico exige la frase `ARMAR SERVOS X08` antes de generar PWM. El control de vuelo acepta `ARMAR SULTANA`, `DESARMAR` y `ESTADO`; aun así, los canards siguen bloqueados mientras los dos seguros de movimiento permanezcan en `false`.

## Resultado de compilación de referencia

| Programa | Flash | RAM global |
| --- | ---: | ---: |
| Diagnóstico | 455 483 bytes | 27 320 bytes |
| Control de vuelo | 429 243 bytes | 27 808 bytes |
| Estación terrena | 321 739 bytes | 20 800 bytes |

Resultados obtenidos el 17 de agosto de 2026 con ESP32 core 3.3.2. El tamaño puede variar ligeramente entre versiones del compilador.
