/*
  DIAGNOSTICO COMPLETO SULTANA DEL NORTE - ESP32-S3
  Abrir directamente con Arduino IDE.

  Placa: ESP32S3 Dev Module
  Monitor serie: 115200 baud, "Nueva linea"

  IMPORTANTE: la primera prueba de servos se realiza SIN varillajes/canards.
*/

#include <Wire.h>
#include <SPI.h>
#include <SD.h>
#include <Adafruit_BNO08x.h>
#include <Adafruit_BMP3XX.h>
#include <ESP32Servo.h>
#include <RF24.h>
#include <TinyGPS++.h>

// ----------------------- PINES DEL ESQUEMA -----------------------
const int PIN_BNO_RST = 4;
const int PIN_BNO_INT = 5;
const int PIN_SD_CS = 6;
const int PIN_NRF_IRQ = 7;
const int PIN_I2C_SCL = 9;
const int PIN_NRF_CSN = 10;
const int PIN_SPI_MOSI = 11;
const int PIN_SPI_MISO = 12;
const int PIN_SPI_SCK = 13;
const int PIN_NRF_CE = 14;
const int PIN_GPS_PPS = 15;
const int PIN_GPS_RX = 16;  // RX ESP32 <- TX GPS
const int PIN_GPS_TX = 17;  // TX ESP32 -> RX GPS
const int PIN_I2C_SDA = 18;
const int PIN_SERVO[4] = {41, 2, 21, 47};

// KST X08 Plus V6.0
const int SERVO_HZ = 333;
const int SERVO_MIN_US = 900;
const int SERVO_MAX_US = 2100;
int servoCentroUs[4] = {1500, 1500, 1500, 1500};
int servoDireccion[4] = {+1, +1, +1, +1};
const float US_POR_GRADO = 1000.0f / 120.0f;
const float PRESION_NIVEL_MAR_HPA = 1013.25f;  // Ajustar con estacion local.

const byte RADIO_DIRECCION[6] = "SNL01";
const byte RADIO_CANAL = 76;  // 2476 MHz

Adafruit_BNO08x bno(PIN_BNO_RST);
Adafruit_BMP3XX bmp;
sh2_SensorValue_t eventoBno;
TinyGPSPlus gps;
HardwareSerial gpsSerial(1);
RF24 radio(PIN_NRF_CE, PIN_NRF_CSN);
Servo servos[4];

bool bnoOK = false;
bool bmpOK = false;
bool sdOK = false;
bool radioOK = false;
bool servosArmados = false;
bool transmitirDatos = true;

float qw = 1.0f, qx = 0.0f, qy = 0.0f, qz = 0.0f;
float gx = 0.0f, gy = 0.0f, gz = 0.0f;
float ax = 0.0f, ay = 0.0f, az = 0.0f;
float presionPa = NAN;
float temperaturaC = NAN;
float altitudBmpM = NAN;
uint32_t ultimaImpresionMs = 0;
volatile uint32_t pulsosPPS = 0;

void IRAM_ATTR interrupcionPPS() {
  pulsosPPS++;
}

bool activarReportesBNO() {
  bool ok = true;
  ok &= bno.enableReport(SH2_GAME_ROTATION_VECTOR, 10000);   // 100 Hz
  ok &= bno.enableReport(SH2_GYROSCOPE_CALIBRATED, 10000);  // 100 Hz
  ok &= bno.enableReport(SH2_LINEAR_ACCELERATION, 10000);   // 100 Hz
  return ok;
}

void escanearI2C() {
  Serial.println("\n--- ESCANEO I2C ---");
  int encontrados = 0;
  for (byte direccion = 1; direccion < 127; direccion++) {
    Wire.beginTransmission(direccion);
    if (Wire.endTransmission() == 0) {
      Serial.printf("Dispositivo encontrado: 0x%02X\n", direccion);
      encontrados++;
    }
  }
  Serial.printf("Total: %d. Esperados: BNO085=0x4A y BMP390=0x76 o 0x77\n\n",
                encontrados);
}

void leerBNO() {
  if (!bnoOK) return;
  if (bno.wasReset()) bnoOK = activarReportesBNO();

  for (int i = 0; i < 16 && bno.getSensorEvent(&eventoBno); i++) {
    switch (eventoBno.sensorId) {
      case SH2_GAME_ROTATION_VECTOR:
        qw = eventoBno.un.gameRotationVector.real;
        qx = eventoBno.un.gameRotationVector.i;
        qy = eventoBno.un.gameRotationVector.j;
        qz = eventoBno.un.gameRotationVector.k;
        break;
      case SH2_GYROSCOPE_CALIBRATED:
        gx = eventoBno.un.gyroscope.x;
        gy = eventoBno.un.gyroscope.y;
        gz = eventoBno.un.gyroscope.z;
        break;
      case SH2_LINEAR_ACCELERATION:
        ax = eventoBno.un.linearAcceleration.x;
        ay = eventoBno.un.linearAcceleration.y;
        az = eventoBno.un.linearAcceleration.z;
        break;
      default:
        break;
    }
  }
}

void leerGPS() {
  while (gpsSerial.available()) gps.encode(gpsSerial.read());
}

void armarServos() {
  if (servosArmados) return;

  ESP32PWM::allocateTimer(0);
  ESP32PWM::allocateTimer(1);
  ESP32PWM::allocateTimer(2);
  ESP32PWM::allocateTimer(3);

  for (int i = 0; i < 4; i++) {
    servos[i].setPeriodHertz(SERVO_HZ);
    servos[i].attach(PIN_SERVO[i], SERVO_MIN_US, SERVO_MAX_US);
    servos[i].writeMicroseconds(servoCentroUs[i]);
  }
  servosArmados = true;
  Serial.println("SERVOS ARMADOS A 333 Hz Y CENTRADOS");
}

void desarmarServos() {
  for (int i = 0; i < 4; i++) {
    if (servos[i].attached()) {
      servos[i].writeMicroseconds(servoCentroUs[i]);
      delay(50);
      servos[i].detach();
    }
  }
  servosArmados = false;
  Serial.println("SERVOS DESARMADOS: sin señal PWM");
}

void moverServo(int numero, float grados) {
  if (!servosArmados) {
    Serial.println("ERROR: primero escribe ARMAR SERVOS X08");
    return;
  }
  if (numero < 1 || numero > 4 || !isfinite(grados) || fabs(grados) > 10.0f) {
    Serial.println("ERROR: usa SERVO numero grados; numero 1-4, grados -10 a +10");
    return;
  }

  int i = numero - 1;
  float pulso = servoCentroUs[i] + servoDireccion[i] * grados * US_POR_GRADO;
  pulso = constrain(pulso, (float)SERVO_MIN_US, (float)SERVO_MAX_US);
  servos[i].writeMicroseconds((int)roundf(pulso));
  Serial.printf("Servo %d: %.2f grados, %.0f us\n", numero, grados, pulso);
}

void centrarServos() {
  if (!servosArmados) {
    Serial.println("Los servos no estan armados");
    return;
  }
  for (int i = 0; i < 4; i++) servos[i].writeMicroseconds(servoCentroUs[i]);
  Serial.println("Cuatro servos centrados");
}

void probarSD() {
  if (!sdOK) {
    Serial.println("SD: ERROR, no inicializada");
    return;
  }
  digitalWrite(PIN_NRF_CSN, HIGH);
  File archivo = SD.open("/diagnostico.txt", FILE_APPEND);
  if (!archivo) {
    Serial.println("SD: ERROR al abrir /diagnostico.txt");
    return;
  }
  archivo.printf("tiempo=%lu presion=%.1f temperatura=%.2f\n",
                 (unsigned long)millis(), presionPa, temperaturaC);
  archivo.flush();
  archivo.close();
  Serial.println("SD: OK, se escribio /diagnostico.txt");
}

#pragma pack(push, 1)
struct PaqueteNavegacion {
  uint16_t magia;
  uint8_t tipo;
  uint8_t version;
  uint16_t secuencia;
  uint8_t estado;
  uint8_t banderas;
  int32_t latitudE7;
  int32_t longitudE7;
  int32_t alturaGpsCm;
  int32_t alturaBaroCm;
  uint32_t presionPa;
  int16_t temperaturaCentiC;
  int16_t velocidadCms;
};
#pragma pack(pop)
static_assert(sizeof(PaqueteNavegacion) == 32, "Paquete debe medir 32 bytes");

void probarRadio() {
  if (!radioOK) {
    Serial.println("RADIO: ERROR, no inicializado");
    return;
  }
  if (bmpOK && bmp.performReading()) {
    presionPa = bmp.pressure;
    temperaturaC = bmp.temperature;
    altitudBmpM = 44330.0f *
      (1.0f - powf(presionPa / (PRESION_NIVEL_MAR_HPA * 100.0f), 0.19029495f));
  }
  PaqueteNavegacion paquete = {};
  paquete.magia = 0xCA57;
  paquete.tipo = 2;
  paquete.version = 2;
  paquete.secuencia = (uint16_t)millis();
  paquete.estado = 2;
  paquete.banderas = (bnoOK ? 0x01 : 0) | (bmpOK ? 0x02 : 0) |
                      (sdOK ? 0x04 : 0) | (radioOK ? 0x08 : 0) |
                      (gps.location.isValid() ? 0x10 : 0);
  if (gps.location.isValid()) {
    paquete.latitudE7 = (int32_t)llround(gps.location.lat() * 10000000.0);
    paquete.longitudE7 = (int32_t)llround(gps.location.lng() * 10000000.0);
  }
  if (gps.altitude.isValid())
    paquete.alturaGpsCm = (int32_t)lround(gps.altitude.meters() * 100.0);
  if (isfinite(altitudBmpM)) paquete.alturaBaroCm = (int32_t)lroundf(altitudBmpM * 100.0f);
  if (isfinite(presionPa)) paquete.presionPa = (uint32_t)lroundf(presionPa);
  if (isfinite(temperaturaC)) paquete.temperaturaCentiC = (int16_t)lroundf(temperaturaC * 100.0f);

  digitalWrite(PIN_SD_CS, HIGH);
  bool recibido = radio.write(&paquete, sizeof(paquete));
  Serial.println(recibido ? "RADIO: OK, ACK recibido" :
                            "RADIO: sin ACK; enciende la estacion terrestre");
}

void mostrarAyuda() {
  Serial.println("\n========== COMANDOS ==========");
  Serial.println("I2C");
  Serial.println("ESTADO");
  Serial.println("DATOS ON");
  Serial.println("DATOS OFF");
  Serial.println("PROBAR SD");
  Serial.println("PROBAR RADIO");
  Serial.println("ARMAR SERVOS X08");
  Serial.println("SERVO 1 3");
  Serial.println("SERVO 1 -3");
  Serial.println("CENTRAR SERVOS");
  Serial.println("DESARMAR SERVOS");
  Serial.println("AYUDA");
  Serial.println("==============================\n");
}

void procesarComando(char *comando) {
  if (strcmp(comando, "I2C") == 0) {
    escanearI2C();
  } else if (strcmp(comando, "ESTADO") == 0) {
    Serial.printf("BNO085=%s BMP390=%s SD=%s RADIO=%s GPS_DATOS=%lu GPS_FIX=%s LAT=%.7f LON=%.7f ALT_GPS=%.2fm P=%.1fPa ALT_BMP=%.2fm PPS=%lu SERVOS=%s\n",
                  bnoOK ? "OK" : "ERROR", bmpOK ? "OK" : "ERROR",
                  sdOK ? "OK" : "ERROR", radioOK ? "OK" : "ERROR",
                  (unsigned long)gps.charsProcessed(),
                  gps.location.isValid() ? "SI" : "NO",
                  gps.location.isValid() ? gps.location.lat() : NAN,
                  gps.location.isValid() ? gps.location.lng() : NAN,
                  gps.altitude.isValid() ? gps.altitude.meters() : NAN,
                  presionPa, altitudBmpM,
                  (unsigned long)pulsosPPS, servosArmados ? "ARMADOS" : "APAGADOS");
  } else if (strcmp(comando, "DATOS ON") == 0) {
    transmitirDatos = true;
  } else if (strcmp(comando, "DATOS OFF") == 0) {
    transmitirDatos = false;
  } else if (strcmp(comando, "PROBAR SD") == 0) {
    probarSD();
  } else if (strcmp(comando, "PROBAR RADIO") == 0) {
    probarRadio();
  } else if (strcmp(comando, "ARMAR SERVOS X08") == 0) {
    armarServos();
  } else if (strcmp(comando, "CENTRAR SERVOS") == 0) {
    centrarServos();
  } else if (strcmp(comando, "DESARMAR SERVOS") == 0) {
    desarmarServos();
  } else if (strncmp(comando, "SERVO ", 6) == 0) {
    int numero;
    float grados;
    if (sscanf(comando + 6, "%d %f", &numero, &grados) == 2)
      moverServo(numero, grados);
    else
      Serial.println("Formato: SERVO numero grados");
  } else if (strcmp(comando, "AYUDA") == 0 || comando[0] == '\0') {
    mostrarAyuda();
  } else {
    Serial.println("Comando desconocido. Escribe AYUDA");
  }
}

void leerComandosSerie() {
  static char linea[64];
  static size_t longitud = 0;

  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\r') continue;
    if (c == '\n') {
      linea[longitud] = '\0';
      procesarComando(linea);
      longitud = 0;
    } else if (longitud < sizeof(linea) - 1) {
      linea[longitud++] = c;
    } else {
      longitud = 0;
    }
  }
}

void imprimirDatos() {
  if (!transmitirDatos || millis() - ultimaImpresionMs < 100) return;
  ultimaImpresionMs = millis();

  if (bmpOK && bmp.performReading()) {
    presionPa = bmp.pressure;
    temperaturaC = bmp.temperature;
    altitudBmpM = 44330.0f *
      (1.0f - powf(presionPa / (PRESION_NIVEL_MAR_HPA * 100.0f), 0.19029495f));
  }

  Serial.printf("DATOS t=%lu | Q=%.5f %.5f %.5f %.5f | G=%.3f %.3f %.3f rad/s | A=%.2f %.2f %.2f m/s2 | BMP: P=%.1f Pa ALT=%.2f m T=%.2f C | GPS=%s LAT=%.7f LON=%.7f ALT=%.2f m VEL=%.2f km/h SAT=%lu PPS=%lu\n",
                (unsigned long)millis(), qw, qx, qy, qz, gx, gy, gz,
                ax, ay, az, presionPa, altitudBmpM, temperaturaC,
                gps.location.isValid() ? "FIX" : "SIN_FIX",
                gps.location.isValid() ? gps.location.lat() : NAN,
                gps.location.isValid() ? gps.location.lng() : NAN,
                gps.altitude.isValid() ? gps.altitude.meters() : NAN,
                gps.speed.isValid() ? gps.speed.kmph() : NAN,
                gps.satellites.isValid() ? (unsigned long)gps.satellites.value() : 0UL,
                (unsigned long)pulsosPPS);
}

void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println("\nSULTANA DEL NORTE - DIAGNOSTICO ESP32-S3");
  Serial.println("PRIMERA PRUEBA DE SERVOS SIN VARILLAJES NI CANARDS");

  Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL, 400000);
  escanearI2C();

  bnoOK = bno.begin_I2C(0x4A, &Wire) && activarReportesBNO();
  bmpOK = bmp.begin_I2C(0x77, &Wire);
  if (!bmpOK) bmpOK = bmp.begin_I2C(0x76, &Wire);
  if (bmpOK) {
    bmp.setTemperatureOversampling(BMP3_OVERSAMPLING_2X);
    bmp.setPressureOversampling(BMP3_OVERSAMPLING_8X);
    bmp.setIIRFilterCoeff(BMP3_IIR_FILTER_COEFF_3);
    bmp.setOutputDataRate(BMP3_ODR_50_HZ);
  }

  gpsSerial.setRxBufferSize(2048);
  gpsSerial.begin(9600, SERIAL_8N1, PIN_GPS_RX, PIN_GPS_TX);
  pinMode(PIN_GPS_PPS, INPUT);
  attachInterrupt(digitalPinToInterrupt(PIN_GPS_PPS), interrupcionPPS, RISING);

  pinMode(PIN_SD_CS, OUTPUT);
  pinMode(PIN_NRF_CSN, OUTPUT);
  digitalWrite(PIN_SD_CS, HIGH);
  digitalWrite(PIN_NRF_CSN, HIGH);
  SPI.begin(PIN_SPI_SCK, PIN_SPI_MISO, PIN_SPI_MOSI);

  sdOK = SD.begin(PIN_SD_CS, SPI, 16000000);
  radioOK = radio.begin(&SPI);
  if (radioOK) {
    radio.setChannel(RADIO_CANAL);
    radio.setDataRate(RF24_250KBPS);
    radio.setPALevel(RF24_PA_LOW);  // Baja potencia para prueba de mesa
    radio.setCRCLength(RF24_CRC_16);
    radio.setRetries(3, 5);
    radio.setPayloadSize(sizeof(PaqueteNavegacion));
    radio.openWritingPipe(RADIO_DIRECCION);
    radio.stopListening();
  }

  Serial.printf("INICIO: BNO085=%s BMP390=%s SD=%s RADIO=%s\n",
                bnoOK ? "OK" : "ERROR", bmpOK ? "OK" : "ERROR",
                sdOK ? "OK" : "ERROR", radioOK ? "OK" : "ERROR");
  mostrarAyuda();
}

void loop() {
  leerComandosSerie();
  leerGPS();
  leerBNO();
  imprimirDatos();
  delay(1);
}
