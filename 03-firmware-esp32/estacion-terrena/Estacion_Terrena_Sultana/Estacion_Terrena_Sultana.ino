/*
  ESTACION TERRESTRE SULTANA DEL NORTE - ESP32-S3 + nRF24L01 PA/LNA
  Archivo unico para Arduino IDE, organizado con POO.

  Recibe dos paquetes alternados:
  - CONTROL: cuaternion, giroscopio, velocidad vertical y canards.
  - NAVEGACION: coordenadas GPS, altura GPS, BMP390, presion y temperatura.

  Placa: ESP32S3 Dev Module
  Monitor serie: 115200 baud
*/

#include <SPI.h>
#include <RF24.h>

const int PIN_NRF_CSN = 10;
const int PIN_SPI_MOSI = 11;
const int PIN_SPI_MISO = 12;
const int PIN_SPI_SCK = 13;
const int PIN_NRF_CE = 14;

const byte DIRECCION_RADIO[6] = "SNL01";
const byte CANAL_RADIO = 76;  // 2476 MHz
const uint16_t MAGIA_SULTANA = 0xCA57;

#pragma pack(push, 1)
struct CabeceraRadio {
  uint16_t magia;
  uint8_t tipo;
  uint8_t version;
  uint16_t secuencia;
  uint8_t estado;
  uint8_t banderas;
};

struct PaqueteControl {
  CabeceraRadio cabecera;
  uint32_t tiempoMs;
  int16_t cuaternion[4];
  int16_t gyro[3];
  int16_t velocidadCms;
  int8_t canards[4];
};

struct PaqueteNavegacion {
  CabeceraRadio cabecera;
  int32_t latitudE7;
  int32_t longitudE7;
  int32_t alturaGpsCm;
  int32_t alturaBaroCm;
  uint32_t presionPa;
  int16_t temperaturaCentiC;
  int16_t velocidadCms;
};
#pragma pack(pop)

static_assert(sizeof(PaqueteControl) == 32, "Paquete control incorrecto");
static_assert(sizeof(PaqueteNavegacion) == 32, "Paquete navegacion incorrecto");

class EstacionTerrestre {
public:
  EstacionTerrestre() : radio(PIN_NRF_CE, PIN_NRF_CSN) {}

  bool iniciar() {
    pinMode(PIN_NRF_CSN, OUTPUT);
    digitalWrite(PIN_NRF_CSN, HIGH);
    SPI.begin(PIN_SPI_SCK, PIN_SPI_MISO, PIN_SPI_MOSI);

    if (!radio.begin(&SPI)) return false;
    radio.setChannel(CANAL_RADIO);
    radio.setDataRate(RF24_250KBPS);
    radio.setPALevel(RF24_PA_LOW);  // Pruebas a corta distancia
    radio.setCRCLength(RF24_CRC_16);
    radio.setAutoAck(true);
    radio.setPayloadSize(32);
    radio.openReadingPipe(1, DIRECCION_RADIO);
    radio.startListening();
    return true;
  }

  void actualizar() {
    while (radio.available()) {
      uint8_t datos[32];
      radio.read(datos, sizeof(datos));

      CabeceraRadio cabecera;
      memcpy(&cabecera, datos, sizeof(cabecera));
      if (cabecera.magia != MAGIA_SULTANA || cabecera.version != 2) {
        Serial.println("PAQUETE INVALIDO");
        continue;
      }

      contarSecuencia(cabecera.secuencia);
      if (cabecera.tipo == 1) {
        PaqueteControl paquete;
        memcpy(&paquete, datos, sizeof(paquete));
        imprimirControl(paquete);
      } else if (cabecera.tipo == 2) {
        PaqueteNavegacion paquete;
        memcpy(&paquete, datos, sizeof(paquete));
        imprimirNavegacion(paquete);
      }
    }
  }

private:
  RF24 radio;
  uint16_t ultimaSecuencia = 0;
  bool haySecuencia = false;
  uint32_t recibidos = 0;
  uint32_t perdidos = 0;
  bool alertaYaMostrada = false;

  const char *nombreEstado(uint8_t estado) {
    static const char *nombres[] = {
      "INICIO", "CALIBRANDO", "SEGURO", "ARMADO", "PROPULSADO",
      "COAST", "DESCENSO", "ATERRIZADO", "FALLA"
    };
    return estado < 9 ? nombres[estado] : "DESCONOCIDO";
  }

  void contarSecuencia(uint16_t secuencia) {
    recibidos++;
    if (haySecuencia) perdidos += (uint16_t)(secuencia - ultimaSecuencia - 1U);
    ultimaSecuencia = secuencia;
    haySecuencia = true;
  }

  void imprimirControl(const PaqueteControl &p) {
    Serial.printf("CONTROL seq=%u estado=%s Q=%.5f %.5f %.5f %.5f G=%.3f %.3f %.3f rad/s Vz=%.2f m/s CANARDS=%.1f %.1f %.1f %.1f REC=%lu PERD=%lu\n",
      p.cabecera.secuencia, nombreEstado(p.cabecera.estado),
      p.cuaternion[0]/16384.0f, p.cuaternion[1]/16384.0f,
      p.cuaternion[2]/16384.0f, p.cuaternion[3]/16384.0f,
      p.gyro[0]/1000.0f, p.gyro[1]/1000.0f, p.gyro[2]/1000.0f,
      p.velocidadCms/100.0f, p.canards[0]/2.0f, p.canards[1]/2.0f,
      p.canards[2]/2.0f, p.canards[3]/2.0f,
      (unsigned long)recibidos, (unsigned long)perdidos);
  }

  void imprimirNavegacion(const PaqueteNavegacion &p) {
    bool gpsValido = p.cabecera.banderas & (1 << 4);
    bool alertaParacaidas = p.cabecera.banderas & (1 << 7);
    Serial.printf("NAVEGACION seq=%u estado=%s GPS=%s LAT=%.7f LON=%.7f ALT_GPS=%.2f m | BMP390 P=%lu Pa ALT=%.2f m T=%.2f C | Vz=%.2f m/s | PARACAIDAS=%s\n",
      p.cabecera.secuencia, nombreEstado(p.cabecera.estado),
      gpsValido ? "FIX" : "SIN_FIX",
      p.latitudE7/10000000.0, p.longitudE7/10000000.0,
      p.alturaGpsCm/100.0f, (unsigned long)p.presionPa,
      p.alturaBaroCm/100.0f, p.temperaturaCentiC/100.0f,
      p.velocidadCms/100.0f, alertaParacaidas ? "ALERTA" : "NORMAL");

    if (alertaParacaidas && !alertaYaMostrada) {
      Serial.println("*** ALERTA: CAIDA CONFIRMADA - ORDEN DE EXPULSION DE PARACAIDAS ***");
      alertaYaMostrada = true;
    }
    if (p.cabecera.estado == 0 || p.cabecera.estado == 2)
      alertaYaMostrada = false;
  }
};

EstacionTerrestre estacion;

void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println("\nESTACION TERRESTRE SULTANA DEL NORTE V2");
  if (!estacion.iniciar()) {
    Serial.println("ERROR: nRF24 no encontrado");
    while (true) delay(1000);
  }
  Serial.println("Radio listo: esperando CONTROL y NAVEGACION");
}

void loop() {
  estacion.actualizar();
  delay(1);
}
