/*
  CONTROL DE VUELO SULTANA DEL NORTE - ESP32-S3 - ARCHIVO UNICO PARA ARDUINO IDE

  Placa: ESP32S3 Dev Module
  Monitor serie: 115200 baud

  CONTROL:
  - Actitud mediante cuaterniones (sin singularidades de Euler).
  - Lazo externo de actitud + PID interno de velocidad angular.
  - Anti-windup, derivada filtrada, saturacion y limite de velocidad de servos.
  - Kalman vertical de 3 estados: altura, velocidad y sesgo del acelerometro.
  - Mezcla 4x3 para roll, pitch y yaw.
  - microSD a 50 Hz y nRF24 a 20 Hz en el segundo nucleo.

  SEGURIDAD:
  Los dos bloqueos siguientes vienen en false. NO ponerlos en true hasta
  calibrar centros, sentidos y matriz de mezcla con el sketch Diagnostico.
*/

#include <Wire.h>
#include <SPI.h>
#include <SD.h>
#include <Adafruit_BNO08x.h>
#include <Adafruit_BMP3XX.h>
#include <ESP32Servo.h>
#include <RF24.h>
#include <TinyGPS++.h>

// Declaraciones adelantadas requeridas por el preprocesador de archivos .ino.
struct PaqueteControl;
struct PaqueteNavegacion;
struct DatosCompartidos;

// ========================= BLOQUEOS DE SEGURIDAD =========================
const bool PERMITIR_MOVIMIENTO_CANARDS = false;
const bool MEZCLA_Y_SENTIDOS_VALIDADOS = false;
const bool ARMADO_AUTOMATICO = false;
const bool HABILITAR_SALIDA_PARACAIDAS = false;

// ============================== PINES ====================================
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
const int PIN_GPS_RX = 16;
const int PIN_GPS_TX = 17;
const int PIN_I2C_SDA = 18;
const int PIN_SERVO[4] = {41, 2, 21, 47};
// GPIO libre propuesto. Debe conectarse SOLO a la entrada logica de un
// controlador externo de despliegue; nunca directamente a una carga.
const int PIN_ALERTA_PARACAIDAS = 40;

// =========================== KST X08 PLUS ================================
const int SERVO_HZ = 333;
const int SERVO_MIN_US = 900;
const int SERVO_MAX_US = 2100;
int servoCentroUs[4] = {1500, 1500, 1500, 1500};
int servoDireccion[4] = {+1, +1, +1, +1};  // CALIBRAR UNO POR UNO
const float US_POR_GRADO = 1000.0f / 120.0f;
const float ANGULO_MAX_CANARD = 12.0f;
const float VELOCIDAD_MAX_CANARD_GRADOS_S = 400.0f;

// Filas: servo 1..4. Columnas: roll, pitch, yaw.
// ES UNA HIPOTESIS INICIAL. Debe validarse con la geometria fisica real.
float mezclaCanards[4][3] = {
  {+1.0f, +1.0f,  0.0f},
  {+1.0f, -1.0f,  0.0f},
  {-1.0f,  0.0f, +1.0f},
  {-1.0f,  0.0f, -1.0f}
};

// =========================== CONFIGURACION ===============================
const uint32_t PERIODO_CONTROL_US = 5000;       // 200 Hz
const uint32_t PERIODO_BNO_US = 5000;           // 200 Hz
const uint32_t PERIODO_BAROMETRO_US = 20000;    // 50 Hz
const uint32_t PERIODO_LOG_MS = 20;              // 50 Hz
const uint32_t PERIODO_RADIO_MS = 50;            // 20 Hz
const uint32_t TIEMPO_CALIBRACION_MS = 5000;

// Ganancias iniciales de banco: roll, pitch, yaw. NO son ganancias finales.
float KpActitud[3] = {2.2f, 3.0f, 3.0f};
float KpRate[3] = {1.2f, 2.0f, 2.0f};
float KiRate[3] = {0.15f, 0.25f, 0.25f};
float KdRate[3] = {0.025f, 0.035f, 0.035f};
const float CORTE_DERIVADA_HZ = 25.0f;
const float GANANCIA_ANTI_WINDUP = 6.0f;
const float RATE_MAX_RAD_S = 5.0f;

const float Q_DINAMICA_REFERENCIA_PA = 600.0f;
const float ESCALA_Q_MIN = 0.30f;

const float RUIDO_ACEL_M_S2 = 1.8f;
const float CAMINATA_SESGO_M_S2 = 0.08f;
const float RUIDO_BARO_M = 1.5f;
const float INNOVACION_BARO_MAX_M = 30.0f;

const float ACEL_LANZAMIENTO_M_S2 = 12.0f;
const uint32_t CONFIRMAR_LANZAMIENTO_MS = 100;
const float ACEL_BURNOUT_M_S2 = 3.0f;
const uint32_t CONFIRMAR_BURNOUT_MS = 300;
const float VELOCIDAD_APOGEO_M_S = -1.5f;
const float ALTURA_MIN_APOGEO_M = 15.0f;
const uint32_t VUELO_ACTIVO_MAX_MS = 45000;
const float VELOCIDAD_ALERTA_CAIDA_M_S = -1.5f;
const float ALTURA_MIN_ALERTA_PARACAIDAS_M = 15.0f;
const uint32_t CONFIRMAR_CAIDA_MS = 250;
const uint32_t PULSO_ALERTA_PARACAIDAS_MS = 500;

const uint32_t TIMEOUT_ACTITUD_US = 100000;
const uint32_t TIMEOUT_GYRO_US = 100000;
const uint32_t TIMEOUT_BARO_US = 250000;

const byte DIRECCION_RADIO[6] = "SNL01";
const byte CANAL_RADIO = 76;  // 2476 MHz

// ========================== MATEMATICA 3D ================================
struct Vector3 {
  float x, y, z;
  Vector3(float X = 0, float Y = 0, float Z = 0) : x(X), y(Y), z(Z) {}
  float norma() const { return sqrtf(x*x + y*y + z*z); }
};

struct Cuaternion {
  float w, x, y, z;
  Cuaternion(float W = 1, float X = 0, float Y = 0, float Z = 0)
      : w(W), x(X), y(Y), z(Z) {}

  bool normalizar() {
    float n2 = w*w + x*x + y*y + z*z;
    if (!isfinite(n2) || n2 < 0.25f || n2 > 2.25f) return false;
    float inv = 1.0f / sqrtf(n2);
    w *= inv; x *= inv; y *= inv; z *= inv;
    return true;
  }

  Cuaternion conjugado() const { return Cuaternion(w, -x, -y, -z); }

  Cuaternion operator*(const Cuaternion &b) const {
    return Cuaternion(
      w*b.w - x*b.x - y*b.y - z*b.z,
      w*b.x + x*b.w + y*b.z - z*b.y,
      w*b.y - x*b.z + y*b.w + z*b.x,
      w*b.z + x*b.y - y*b.x + z*b.w
    );
  }

  Vector3 rotar(const Vector3 &v) const {
    Cuaternion resultado = (*this) * Cuaternion(0, v.x, v.y, v.z) * conjugado();
    return Vector3(resultado.x, resultado.y, resultado.z);
  }
};

float limitar(float valor, float minimo, float maximo) {
  return valor < minimo ? minimo : (valor > maximo ? maximo : valor);
}

Vector3 errorRotacionMinima(const Cuaternion &actual, const Cuaternion &objetivo) {
  Cuaternion error = actual.conjugado() * objetivo;
  error.normalizar();
  if (error.w < 0) {
    error.w = -error.w; error.x = -error.x;
    error.y = -error.y; error.z = -error.z;
  }
  float normaVector = sqrtf(error.x*error.x + error.y*error.y + error.z*error.z);
  if (normaVector < 1.0e-6f)
    return Vector3(2*error.x, 2*error.y, 2*error.z);
  float angulo = 2.0f * atan2f(normaVector, error.w);
  float escala = angulo / normaVector;
  return Vector3(error.x*escala, error.y*escala, error.z*escala);
}

// =========================== KALMAN VERTICAL =============================
class KalmanVertical {
public:
  float x[3] = {0, 0, 0};  // altura, velocidad, sesgo acelerometro
  float P[3][3];
  bool iniciado = false;

  void reiniciar(float altura = 0) {
    x[0] = altura; x[1] = 0; x[2] = 0;
    memset(P, 0, sizeof(P));
    P[0][0] = 4.0f; P[1][1] = 9.0f; P[2][2] = 1.0f;
    iniciado = true;
  }

  void predecir(float aceleracionArriba, float dt) {
    if (!iniciado || !isfinite(aceleracionArriba) || dt <= 0 || dt > 0.1f) return;
    float dt2 = dt*dt;
    float acelSinSesgo = aceleracionArriba - x[2];
    x[0] += x[1]*dt + 0.5f*acelSinSesgo*dt2;
    x[1] += acelSinSesgo*dt;

    float F[3][3] = {
      {1, dt, -0.5f*dt2},
      {0, 1, -dt},
      {0, 0, 1}
    };
    float FP[3][3] = {};
    float nuevaP[3][3] = {};
    for (int r=0; r<3; r++)
      for (int c=0; c<3; c++)
        for (int k=0; k<3; k++) FP[r][c] += F[r][k]*P[k][c];
    for (int r=0; r<3; r++)
      for (int c=0; c<3; c++)
        for (int k=0; k<3; k++) nuevaP[r][c] += FP[r][k]*F[c][k];

    float varAcel = RUIDO_ACEL_M_S2*RUIDO_ACEL_M_S2;
    float G[3] = {0.5f*dt2, dt, 0};
    for (int r=0; r<3; r++)
      for (int c=0; c<3; c++) nuevaP[r][c] += varAcel*G[r]*G[c];
    nuevaP[2][2] += CAMINATA_SESGO_M_S2*CAMINATA_SESGO_M_S2*dt;
    memcpy(P, nuevaP, sizeof(P));
  }

  bool corregirBarometro(float altura) {
    if (!iniciado || !isfinite(altura)) return false;
    float R = RUIDO_BARO_M*RUIDO_BARO_M;
    float innovacion = altura - x[0];
    float S = P[0][0] + R;
    float compuerta = max(INNOVACION_BARO_MAX_M, 6.0f*sqrtf(S));
    if (fabsf(innovacion) > compuerta) return false;

    float K[3] = {P[0][0]/S, P[1][0]/S, P[2][0]/S};
    for (int i=0; i<3; i++) x[i] += K[i]*innovacion;

    // Forma Joseph: numericamente estable.
    float A[3][3] = {
      {1-K[0], 0, 0},
      {-K[1], 1, 0},
      {-K[2], 0, 1}
    };
    float AP[3][3] = {};
    float nuevaP[3][3] = {};
    for (int r=0; r<3; r++)
      for (int c=0; c<3; c++)
        for (int j=0; j<3; j++) AP[r][c] += A[r][j]*P[j][c];
    for (int r=0; r<3; r++)
      for (int c=0; c<3; c++)
        for (int j=0; j<3; j++) nuevaP[r][c] += AP[r][j]*A[c][j];
    for (int r=0; r<3; r++)
      for (int c=0; c<3; c++) nuevaP[r][c] += K[r]*R*K[c];
    memcpy(P, nuevaP, sizeof(P));
    return true;
  }
};

// ================================ PID ====================================
class PIDRate {
public:
  float kp=0, ki=0, kd=0;
  float integral=0, derivadaFiltrada=0, medicionAnterior=0;
  bool tieneAnterior=false;

  void configurar(float P, float I, float D) { kp=P; ki=I; kd=D; }
  void reiniciar() {
    integral=0; derivadaFiltrada=0; medicionAnterior=0; tieneAnterior=false;
  }

  float actualizar(float objetivo, float medicion, float dt, float limite) {
    if (dt <= 0 || dt > 0.05f || !isfinite(medicion)) return 0;
    float error = objetivo - medicion;
    float derivada = tieneAnterior ? -(medicion-medicionAnterior)/dt : 0;
    medicionAnterior = medicion;
    tieneAnterior = true;

    float rc = 1.0f/(2.0f*PI*CORTE_DERIVADA_HZ);
    float alfa = dt/(rc+dt);
    derivadaFiltrada += alfa*(derivada-derivadaFiltrada);

    float sinLimitar = kp*error + integral + kd*derivadaFiltrada;
    float salida = limitar(sinLimitar, -limite, limite);
    integral += (ki*error + GANANCIA_ANTI_WINDUP*(salida-sinLimitar))*dt;
    integral = limitar(integral, -limite, limite);
    return salida;
  }
};

// ============================= TELEMETRIA ================================
enum EstadoVuelo : uint8_t {
  INICIO=0, CALIBRANDO, SEGURO, ARMADO, PROPULSADO,
  COAST, DESCENSO, ATERRIZADO, FALLA
};

const char* nombreEstado(uint8_t e) {
  switch(e) {
    case INICIO: return "INICIO";
    case CALIBRANDO: return "CALIBRANDO";
    case SEGURO: return "SEGURO";
    case ARMADO: return "ARMADO";
    case PROPULSADO: return "PROPULSADO";
    case COAST: return "COAST";
    case DESCENSO: return "DESCENSO";
    case ATERRIZADO: return "ATERRIZADO";
    case FALLA: return "FALLA";
  }
  return "DESCONOCIDO";
}

// Gestor POO independiente para confirmar la caida y producir una sola alerta.
class GestorParacaidas {
public:
  void iniciar() {
    pinMode(PIN_ALERTA_PARACAIDAS, OUTPUT);
    digitalWrite(PIN_ALERTA_PARACAIDAS, LOW);
  }

  void armar() {
    armado = true;
    alerta = false;
    salidaActiva = false;
    candidatoDesdeMs = 0;
    digitalWrite(PIN_ALERTA_PARACAIDAS, LOW);
  }

  void desarmar() {
    armado = false;
    candidatoDesdeMs = 0;
    salidaActiva = false;
    digitalWrite(PIN_ALERTA_PARACAIDAS, LOW);
  }

  void actualizar(uint32_t ahoraMs, EstadoVuelo estadoActual,
                  float alturaM, float velocidadM_S) {
    if (salidaActiva && ahoraMs - inicioPulsoMs >= PULSO_ALERTA_PARACAIDAS_MS) {
      digitalWrite(PIN_ALERTA_PARACAIDAS, LOW);
      salidaActiva = false;
    }
    if (!armado || alerta) return;

    bool inicioDeCaida = estadoActual == DESCENSO &&
                         velocidadM_S <= VELOCIDAD_ALERTA_CAIDA_M_S;
    if (inicioDeCaida && candidatoDesdeMs == 0 &&
        alturaM >= ALTURA_MIN_ALERTA_PARACAIDAS_M) {
      candidatoDesdeMs = ahoraMs;
    }
    if (!inicioDeCaida) {
      candidatoDesdeMs = 0;
      return;
    }
    if (candidatoDesdeMs != 0 && ahoraMs - candidatoDesdeMs >= CONFIRMAR_CAIDA_MS) {
      alerta = true;
      Serial.printf("ALERTA PARACAIDAS: CAIDA CONFIRMADA, altura=%.2f m, Vz=%.2f m/s\n",
                    alturaM, velocidadM_S);
      if (HABILITAR_SALIDA_PARACAIDAS) {
        digitalWrite(PIN_ALERTA_PARACAIDAS, HIGH);
        salidaActiva = true;
        inicioPulsoMs = ahoraMs;
      }
    }
  }

  bool alertaActiva() const { return alerta; }
  bool estaArmado() const { return armado; }

private:
  bool armado = false;
  bool alerta = false;
  bool salidaActiva = false;
  uint32_t candidatoDesdeMs = 0;
  uint32_t inicioPulsoMs = 0;
};

#pragma pack(push, 1)
struct PaqueteControl {
  uint16_t magia;       // 0xCA57
  uint8_t tipo;         // 1 = control
  uint8_t version;
  uint16_t secuencia;
  uint8_t estado;
  uint8_t banderas;
  uint32_t tiempoMs;
  int16_t cuaternion[4];
  int16_t gyro[3];
  int16_t velocidadCms;
  int8_t canards[4];
};

struct PaqueteNavegacion {
  uint16_t magia;       // 0xCA57
  uint8_t tipo;         // 2 = GPS + BMP390
  uint8_t version;
  uint16_t secuencia;
  uint8_t estado;
  uint8_t banderas;
  int32_t latitudE7;       // grados * 10^7
  int32_t longitudE7;      // grados * 10^7
  int32_t alturaGpsCm;
  int32_t alturaBaroCm;
  uint32_t presionPa;
  int16_t temperaturaCentiC;
  int16_t velocidadCms;
};
#pragma pack(pop)
static_assert(sizeof(PaqueteControl)==32, "Paquete control debe medir 32 bytes");
static_assert(sizeof(PaqueteNavegacion)==32, "Paquete navegacion debe medir 32 bytes");

struct DatosCompartidos {
  uint32_t tiempoMs;
  EstadoVuelo estado;
  uint8_t banderas;
  Cuaternion q;
  Vector3 gyro;
  Vector3 acel;
  float presionPa, temperaturaC;
  float alturaM, velocidadM_S, sesgoAcelM_S2, qDinamicaPa;
  float errorActitud[3];
  float canardGrados[4];
  double latitud, longitud;
  float alturaGps;
  uint8_t satelites;
  bool alertaParacaidas;
};

// Prototipo explicito para evitar que el preprocesador de Arduino coloque
// esta declaracion antes de las estructuras personalizadas.
void empacarControl(PaqueteControl &p, const DatosCompartidos &d, uint16_t secuencia);
void empacarNavegacion(PaqueteNavegacion &p, const DatosCompartidos &d,
                       uint16_t secuencia);

// =============================== OBJETOS =================================
Adafruit_BNO08x bno(PIN_BNO_RST);
Adafruit_BMP3XX bmp;
sh2_SensorValue_t eventoBno;
TinyGPSPlus gps;
HardwareSerial gpsSerial(1);
RF24 radio(PIN_NRF_CE, PIN_NRF_CSN);
Servo servos[4];
KalmanVertical kalman;
PIDRate pidRate[3];
GestorParacaidas paracaidas;

SemaphoreHandle_t mutexSPI = nullptr;
portMUX_TYPE mutexDatos = portMUX_INITIALIZER_UNLOCKED;
DatosCompartidos datosCompartidos = {};
File archivoLog;

Cuaternion actitud;
Cuaternion actitudObjetivo;
Vector3 gyro;
Vector3 aceleracionLineal;
Vector3 errorActitud;
Vector3 salidaEjes;
float comandoCanard[4] = {0,0,0,0};

EstadoVuelo estado = INICIO;
uint32_t entradaEstadoMs=0, inicioVueloMs=0;
uint32_t ultimaActitudUs=0, ultimoGyroUs=0, ultimaAcelUs=0, ultimoBaroUs=0;
uint32_t ultimoControlUs=0, ultimoBaroPollUs=0;
uint32_t candidatoLanzamientoMs=0, candidatoBurnoutMs=0, candidatoAterrizajeMs=0;

float presionPa=NAN, temperaturaC=NAN;
float presionBasePa=0;
double acumuladorPresion=0;
uint32_t muestrasPresion=0;
bool bnoOK=false, bmpOK=false, sdOK=false, radioOK=false;
bool baroRechazado=false;
volatile uint32_t pulsosPPS=0;

void IRAM_ATTR interrupcionPPS() { pulsosPPS++; }

int16_t aInt16(float valor) {
  if (!isfinite(valor)) return 0;
  return (int16_t)lroundf(limitar(valor, -32768.0f, 32767.0f));
}

void reiniciarControladores() {
  for (int i=0; i<3; i++) pidRate[i].reiniciar();
}

bool activarReportesBNO() {
  bool ok=true;
  ok &= bno.enableReport(SH2_GAME_ROTATION_VECTOR, PERIODO_BNO_US);
  ok &= bno.enableReport(SH2_GYROSCOPE_CALIBRATED, PERIODO_BNO_US);
  ok &= bno.enableReport(SH2_LINEAR_ACCELERATION, PERIODO_BNO_US*2);
  return ok;
}

void cambiarEstado(uint8_t nuevoValor) {
  EstadoVuelo nuevo = (EstadoVuelo)nuevoValor;
  if (estado==nuevo) return;
  Serial.printf("ESTADO: %s -> %s, t=%lu ms\n", nombreEstado(estado),
                nombreEstado(nuevo), (unsigned long)millis());
  estado=nuevo;
  entradaEstadoMs=millis();
  if (nuevo==ARMADO) paracaidas.armar();
  if (nuevo==SEGURO || nuevo==FALLA || nuevo==ATERRIZADO) paracaidas.desarmar();
  if (nuevo==SEGURO || nuevo==FALLA || nuevo==DESCENSO || nuevo==ATERRIZADO)
    reiniciarControladores();
}

bool sensoresRecientes(uint32_t ahoraUs) {
  return bnoOK && bmpOK &&
         ahoraUs-ultimaActitudUs < TIMEOUT_ACTITUD_US &&
         ahoraUs-ultimoGyroUs < TIMEOUT_GYRO_US &&
         ahoraUs-ultimoBaroUs < TIMEOUT_BARO_US;
}

void leerBNO() {
  if (!bnoOK) return;
  if (bno.wasReset()) { bnoOK=activarReportesBNO(); return; }
  for (int i=0; i<16 && bno.getSensorEvent(&eventoBno); i++) {
    uint32_t ahora=micros();
    switch(eventoBno.sensorId) {
      case SH2_GAME_ROTATION_VECTOR: {
        Cuaternion q(eventoBno.un.gameRotationVector.real,
                     eventoBno.un.gameRotationVector.i,
                     eventoBno.un.gameRotationVector.j,
                     eventoBno.un.gameRotationVector.k);
        if (q.normalizar()) { actitud=q; ultimaActitudUs=ahora; }
        break;
      }
      case SH2_GYROSCOPE_CALIBRATED:
        gyro=Vector3(eventoBno.un.gyroscope.x, eventoBno.un.gyroscope.y,
                     eventoBno.un.gyroscope.z);
        ultimoGyroUs=ahora;
        break;
      case SH2_LINEAR_ACCELERATION:
        aceleracionLineal=Vector3(eventoBno.un.linearAcceleration.x,
                                  eventoBno.un.linearAcceleration.y,
                                  eventoBno.un.linearAcceleration.z);
        ultimaAcelUs=ahora;
        break;
    }
  }
}

float alturaRelativa(float p) {
  if (presionBasePa < 10000 || p < 10000) return 0;
  return 44330.0f*(1.0f-powf(p/presionBasePa, 0.19029495f));
}

void leerBarometro(uint32_t ahoraUs) {
  if (!bmpOK || ahoraUs-ultimoBaroPollUs < PERIODO_BAROMETRO_US) return;
  ultimoBaroPollUs=ahoraUs;
  if (!bmp.performReading()) return;
  presionPa=bmp.pressure;
  temperaturaC=bmp.temperature;
  ultimoBaroUs=ahoraUs;

  if (estado==CALIBRANDO && isfinite(presionPa)) {
    acumuladorPresion += presionPa;
    muestrasPresion++;
    presionBasePa=(float)(acumuladorPresion/muestrasPresion);
  }
  if (muestrasPresion>=25) {
    float altura=alturaRelativa(presionPa);
    if (!kalman.iniciado) kalman.reiniciar(altura);
    baroRechazado=!kalman.corregirBarometro(altura);
  }
}

void leerGPS() {
  while (gpsSerial.available()) gps.encode(gpsSerial.read());
}

float presionDinamica() {
  if (!isfinite(presionPa) || !isfinite(temperaturaC)) return 0;
  float densidad=presionPa/(287.05f*(temperaturaC+273.15f));
  return 0.5f*densidad*kalman.x[1]*kalman.x[1];
}

void aplicarCanards(const Vector3 &ejes, float dt, bool activo) {
  float deseado[4]={0,0,0,0};
  if (activo) {
    float vectorEjes[3]={ejes.x,ejes.y,ejes.z};
    float factor=1.0f;
    for (int s=0;s<4;s++) {
      for (int a=0;a<3;a++) deseado[s]+=mezclaCanards[s][a]*vectorEjes[a];
      factor=max(factor,fabsf(deseado[s])/ANGULO_MAX_CANARD);
    }
    for (int s=0;s<4;s++) deseado[s]/=factor;
  }

  float pasoMax=VELOCIDAD_MAX_CANARD_GRADOS_S*limitar(dt,0,0.05f);
  for (int s=0;s<4;s++) {
    float objetivo=limitar(deseado[s],-ANGULO_MAX_CANARD,ANGULO_MAX_CANARD);
    comandoCanard[s]+=limitar(objetivo-comandoCanard[s],-pasoMax,pasoMax);
    if (servos[s].attached()) {
      float pulso=servoCentroUs[s]+servoDireccion[s]*comandoCanard[s]*US_POR_GRADO;
      pulso=limitar(pulso,SERVO_MIN_US,SERVO_MAX_US);
      servos[s].writeMicroseconds((int)lroundf(pulso));
    }
  }
}

void actualizarControl(uint32_t ahoraUs) {
  if (ultimoControlUs==0) { ultimoControlUs=ahoraUs; return; }
  if (ahoraUs-ultimoControlUs < PERIODO_CONTROL_US) return;
  float dt=limitar((ahoraUs-ultimoControlUs)*1.0e-6f,0.001f,0.02f);
  ultimoControlUs=ahoraUs;

  if (ultimaAcelUs && ultimaActitudUs) {
    Vector3 acelMundo=actitud.rotar(aceleracionLineal);
    kalman.predecir(acelMundo.z,dt); // Z mundo arriba; verificar en diagnostico.
  }

  bool activo=(estado==PROPULSADO || estado==COAST) && sensoresRecientes(ahoraUs);
  if (activo) {
    errorActitud=errorRotacionMinima(actitud,actitudObjetivo);
    float error[3]={errorActitud.x,errorActitud.y,errorActitud.z};
    float rateMedido[3]={gyro.x,gyro.y,gyro.z};
    float salida[3];
    float escalaQ=limitar(Q_DINAMICA_REFERENCIA_PA /
                          max(presionDinamica(),Q_DINAMICA_REFERENCIA_PA),
                          ESCALA_Q_MIN,1.0f);
    for (int i=0;i<3;i++) {
      float rateObjetivo=limitar(KpActitud[i]*error[i],-RATE_MAX_RAD_S,RATE_MAX_RAD_S);
      salida[i]=pidRate[i].actualizar(rateObjetivo,rateMedido[i],dt,
                                      ANGULO_MAX_CANARD)*escalaQ;
    }
    salidaEjes=Vector3(salida[0],salida[1],salida[2]);
  } else {
    reiniciarControladores();
    errorActitud=Vector3();
    salidaEjes=Vector3();
  }
  aplicarCanards(salidaEjes,dt,activo);
}

void actualizarEstado(uint32_t ahoraMs,uint32_t ahoraUs) {
  float magnitudAcel=aceleracionLineal.norma();
  switch(estado) {
    case CALIBRANDO:
      if (ahoraMs-entradaEstadoMs>=TIEMPO_CALIBRACION_MS && muestrasPresion>=25 &&
          sensoresRecientes(ahoraUs)) {
        actitudObjetivo=actitud;
        cambiarEstado(ARMADO_AUTOMATICO ? ARMADO : SEGURO);
      }
      break;
    case ARMADO:
      if (!sensoresRecientes(ahoraUs)) cambiarEstado(FALLA);
      else if (magnitudAcel>=ACEL_LANZAMIENTO_M_S2) {
        if (!candidatoLanzamientoMs) candidatoLanzamientoMs=ahoraMs;
        if (ahoraMs-candidatoLanzamientoMs>=CONFIRMAR_LANZAMIENTO_MS) {
          inicioVueloMs=ahoraMs;
          cambiarEstado(PROPULSADO);
        }
      } else candidatoLanzamientoMs=0;
      break;
    case PROPULSADO:
      if (!sensoresRecientes(ahoraUs)) cambiarEstado(FALLA);
      else if (ahoraMs-inicioVueloMs>500 && magnitudAcel<ACEL_BURNOUT_M_S2) {
        if (!candidatoBurnoutMs) candidatoBurnoutMs=ahoraMs;
        if (ahoraMs-candidatoBurnoutMs>=CONFIRMAR_BURNOUT_MS) cambiarEstado(COAST);
      } else candidatoBurnoutMs=0;
      break;
    case COAST:
      if (!sensoresRecientes(ahoraUs)) cambiarEstado(FALLA);
      else if ((kalman.x[0]>=ALTURA_MIN_APOGEO_M && kalman.x[1]<=VELOCIDAD_APOGEO_M_S) ||
               ahoraMs-inicioVueloMs>=VUELO_ACTIVO_MAX_MS) cambiarEstado(DESCENSO);
      break;
    case DESCENSO:
      if (fabsf(kalman.x[1])<1.0f && kalman.x[0]<10.0f) {
        if (!candidatoAterrizajeMs) candidatoAterrizajeMs=ahoraMs;
        if (ahoraMs-candidatoAterrizajeMs>3000) cambiarEstado(ATERRIZADO);
      } else candidatoAterrizajeMs=0;
      break;
    default: break;
  }
}

uint8_t obtenerBanderas(uint32_t ahoraUs) {
  uint8_t f=0;
  if (bnoOK) f|=1<<0;
  if (bmpOK) f|=1<<1;
  if (sdOK) f|=1<<2;
  if (radioOK) f|=1<<3;
  if (gps.location.isValid() && gps.location.age()<2000) f|=1<<4;
  if (servos[0].attached()) f|=1<<5;
  if (sensoresRecientes(ahoraUs)) f|=1<<6;
  if (paracaidas.alertaActiva()) f|=1<<7;
  return f;
}

void publicarDatos(uint32_t ahoraMs,uint32_t ahoraUs) {
  DatosCompartidos d={};
  d.tiempoMs=ahoraMs; d.estado=estado; d.banderas=obtenerBanderas(ahoraUs);
  d.q=actitud; d.gyro=gyro; d.acel=aceleracionLineal;
  d.presionPa=presionPa; d.temperaturaC=temperaturaC;
  d.alturaM=kalman.x[0]; d.velocidadM_S=kalman.x[1]; d.sesgoAcelM_S2=kalman.x[2];
  d.qDinamicaPa=presionDinamica();
  d.errorActitud[0]=errorActitud.x; d.errorActitud[1]=errorActitud.y;
  d.errorActitud[2]=errorActitud.z;
  for (int i=0;i<4;i++) d.canardGrados[i]=comandoCanard[i];
  d.latitud=gps.location.isValid()?gps.location.lat():NAN;
  d.longitud=gps.location.isValid()?gps.location.lng():NAN;
  d.alturaGps=gps.altitude.isValid()?gps.altitude.meters():NAN;
  d.satelites=gps.satellites.isValid()?gps.satellites.value():0;
  d.alertaParacaidas=paracaidas.alertaActiva();
  portENTER_CRITICAL(&mutexDatos);
  datosCompartidos=d;
  portEXIT_CRITICAL(&mutexDatos);
}

void empacarControl(PaqueteControl &p,const DatosCompartidos &d,uint16_t secuencia) {
  memset(&p,0,sizeof(p));
  p.magia=0xCA57; p.tipo=1; p.version=2;
  p.tiempoMs=d.tiempoMs; p.secuencia=secuencia;
  p.estado=d.estado; p.banderas=d.banderas;
  p.cuaternion[0]=aInt16(d.q.w*16384); p.cuaternion[1]=aInt16(d.q.x*16384);
  p.cuaternion[2]=aInt16(d.q.y*16384); p.cuaternion[3]=aInt16(d.q.z*16384);
  p.gyro[0]=aInt16(d.gyro.x*1000); p.gyro[1]=aInt16(d.gyro.y*1000);
  p.gyro[2]=aInt16(d.gyro.z*1000);
  p.velocidadCms=aInt16(d.velocidadM_S*100);
  for (int i=0;i<4;i++) p.canards[i]=(int8_t)lroundf(limitar(d.canardGrados[i]*2,-127,127));
}

void empacarNavegacion(PaqueteNavegacion &p,const DatosCompartidos &d,
                       uint16_t secuencia) {
  memset(&p,0,sizeof(p));
  p.magia=0xCA57; p.tipo=2; p.version=2;
  p.secuencia=secuencia; p.estado=d.estado; p.banderas=d.banderas;
  if (isfinite(d.latitud)) p.latitudE7=(int32_t)llround(d.latitud*10000000.0);
  if (isfinite(d.longitud)) p.longitudE7=(int32_t)llround(d.longitud*10000000.0);
  if (isfinite(d.alturaGps)) p.alturaGpsCm=(int32_t)lroundf(d.alturaGps*100.0f);
  p.alturaBaroCm=(int32_t)lroundf(d.alturaM*100.0f);
  if (isfinite(d.presionPa) && d.presionPa>0) p.presionPa=(uint32_t)lroundf(d.presionPa);
  p.temperaturaCentiC=aInt16(d.temperaturaC*100.0f);
  p.velocidadCms=aInt16(d.velocidadM_S*100.0f);
}

bool abrirArchivoLog() {
  for (int n=0;n<100;n++) {
    char nombre[24];
    snprintf(nombre,sizeof(nombre),"/vuelo_%02d.csv",n);
    if (!SD.exists(nombre)) {
      archivoLog=SD.open(nombre,FILE_WRITE);
      if (!archivoLog) return false;
      archivoLog.println("tiempo_ms,estado,flags,qw,qx,qy,qz,gx,gy,gz,ax,ay,az,presion_pa,temp_c,altura_m,velocidad_m_s,sesgo_acel,q_dinamica,error_roll,error_pitch,error_yaw,c1,c2,c3,c4,lat,lon,altura_gps,satelites,alerta_paracaidas");
      archivoLog.flush();
      Serial.printf("Archivo SD: %s\n",nombre);
      return true;
    }
  }
  return false;
}

void tareaTelemetria(void *parametro) {
  uint32_t siguienteLog=millis(), siguienteRadio=millis(), ultimoFlush=millis();
  uint16_t secuencia=0;
  bool enviarNavegacion=false;
  while(true) {
    uint32_t ahora=millis();
    DatosCompartidos d;
    portENTER_CRITICAL(&mutexDatos); d=datosCompartidos; portEXIT_CRITICAL(&mutexDatos);

    if (sdOK && (int32_t)(ahora-siguienteLog)>=0) {
      siguienteLog+=PERIODO_LOG_MS;
      if (xSemaphoreTake(mutexSPI,pdMS_TO_TICKS(10))==pdTRUE) {
        digitalWrite(PIN_NRF_CSN,HIGH);
        archivoLog.printf("%lu,%s,%u,%.7f,%.7f,%.7f,%.7f,%.5f,%.5f,%.5f,%.3f,%.3f,%.3f,%.1f,%.2f,%.3f,%.3f,%.4f,%.2f,%.5f,%.5f,%.5f,%.2f,%.2f,%.2f,%.2f,%.7f,%.7f,%.2f,%u,%u\n",
          (unsigned long)d.tiempoMs,nombreEstado(d.estado),d.banderas,
          d.q.w,d.q.x,d.q.y,d.q.z,d.gyro.x,d.gyro.y,d.gyro.z,
          d.acel.x,d.acel.y,d.acel.z,d.presionPa,d.temperaturaC,
          d.alturaM,d.velocidadM_S,d.sesgoAcelM_S2,d.qDinamicaPa,
          d.errorActitud[0],d.errorActitud[1],d.errorActitud[2],
          d.canardGrados[0],d.canardGrados[1],d.canardGrados[2],d.canardGrados[3],
          d.latitud,d.longitud,d.alturaGps,d.satelites,d.alertaParacaidas);
        if (ahora-ultimoFlush>=1000) { archivoLog.flush(); ultimoFlush=ahora; }
        xSemaphoreGive(mutexSPI);
      }
    }

    if (radioOK && (int32_t)(ahora-siguienteRadio)>=0) {
      siguienteRadio+=PERIODO_RADIO_MS;
      if (xSemaphoreTake(mutexSPI,pdMS_TO_TICKS(10))==pdTRUE) {
        digitalWrite(PIN_SD_CS,HIGH);
        if (enviarNavegacion) {
          PaqueteNavegacion p;
          empacarNavegacion(p,d,secuencia++);
          radio.write(&p,sizeof(p));
        } else {
          PaqueteControl p;
          empacarControl(p,d,secuencia++);
          radio.write(&p,sizeof(p));
        }
        xSemaphoreGive(mutexSPI);
      }
      enviarNavegacion=!enviarNavegacion;
    }
    vTaskDelay(pdMS_TO_TICKS(5));
  }
}

void procesarComando(const char *comando) {
  if (strcmp(comando,"ARMAR SULTANA")==0) {
    if (estado!=SEGURO) { Serial.println("ARMADO RECHAZADO: estado no SEGURO"); return; }
    if (!sensoresRecientes(micros())) { Serial.println("ARMADO RECHAZADO: sensores"); return; }
    actitudObjetivo=actitud;
    reiniciarControladores();
    cambiarEstado(ARMADO);
    Serial.println(servos[0].attached()?"ARMADO CON CANARDS HABILITADOS":"ARMADO, PERO CANARDS BLOQUEADOS POR SEGURIDAD");
  } else if (strcmp(comando,"DESARMAR")==0) {
    aplicarCanards(Vector3(),0.05f,false);
    cambiarEstado(SEGURO);
  } else if (strcmp(comando,"ESTADO")==0) {
    Serial.printf("Estado=%s BNO=%d BMP=%d SD=%d RADIO=%d PWM=%d P=%.1fPa AltBMP=%.2fm Vz=%.2fm/s GPS=%s LAT=%.7f LON=%.7f AltGPS=%.2fm PARACAIDAS=%s\n",
      nombreEstado(estado),bnoOK,bmpOK,sdOK,radioOK,servos[0].attached(),
      presionPa,kalman.x[0],kalman.x[1],gps.location.isValid()?"FIX":"SIN_FIX",
      gps.location.isValid()?gps.location.lat():NAN,
      gps.location.isValid()?gps.location.lng():NAN,
      gps.altitude.isValid()?gps.altitude.meters():NAN,
      paracaidas.alertaActiva()?"ALERTA":"NORMAL");
  } else if (comando[0]) {
    Serial.println("Comandos: ARMAR SULTANA | DESARMAR | ESTADO");
  }
}

void leerComandosSerie() {
  static char linea[48]; static size_t longitud=0;
  while(Serial.available()) {
    char c=(char)Serial.read();
    if (c=='\r') continue;
    if (c=='\n') { linea[longitud]='\0'; procesarComando(linea); longitud=0; }
    else if (longitud<sizeof(linea)-1) linea[longitud++]=c;
    else longitud=0;
  }
}

void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println("\nSULTANA DEL NORTE - CONTROL DE VUELO ESP32-S3");
  Serial.println("Los canards vienen BLOQUEADOS en las primeras constantes del codigo");
  Serial.println("La salida fisica de paracaidas tambien viene BLOQUEADA");

  for (int i=0;i<3;i++) pidRate[i].configurar(KpRate[i],KiRate[i],KdRate[i]);
  paracaidas.iniciar();

  Wire.begin(PIN_I2C_SDA,PIN_I2C_SCL,400000);
  bnoOK=bno.begin_I2C(0x4A,&Wire) && activarReportesBNO();
  bmpOK=bmp.begin_I2C(0x77,&Wire);
  if (!bmpOK) bmpOK=bmp.begin_I2C(0x76,&Wire);
  if (bmpOK) {
    bmp.setTemperatureOversampling(BMP3_OVERSAMPLING_2X);
    bmp.setPressureOversampling(BMP3_OVERSAMPLING_8X);
    bmp.setIIRFilterCoeff(BMP3_IIR_FILTER_COEFF_3);
    bmp.setOutputDataRate(BMP3_ODR_50_HZ);
  }

  gpsSerial.setRxBufferSize(2048);
  gpsSerial.begin(9600,SERIAL_8N1,PIN_GPS_RX,PIN_GPS_TX);
  pinMode(PIN_GPS_PPS,INPUT);
  attachInterrupt(digitalPinToInterrupt(PIN_GPS_PPS),interrupcionPPS,RISING);

  pinMode(PIN_SD_CS,OUTPUT); pinMode(PIN_NRF_CSN,OUTPUT);
  digitalWrite(PIN_SD_CS,HIGH); digitalWrite(PIN_NRF_CSN,HIGH);
  SPI.begin(PIN_SPI_SCK,PIN_SPI_MISO,PIN_SPI_MOSI);
  mutexSPI=xSemaphoreCreateMutex();

  if (mutexSPI) {
    sdOK=SD.begin(PIN_SD_CS,SPI,16000000) && abrirArchivoLog();
    radioOK=radio.begin(&SPI);
    if (radioOK) {
      radio.setChannel(CANAL_RADIO);
      radio.setDataRate(RF24_250KBPS);
      radio.setPALevel(RF24_PA_HIGH);
      radio.setCRCLength(RF24_CRC_16);
      radio.setRetries(3,5);
      radio.setAutoAck(true);
      radio.setPayloadSize(sizeof(PaqueteControl));
      radio.openWritingPipe(DIRECCION_RADIO);
      radio.stopListening();
    }
  }

  bool habilitarPWM=PERMITIR_MOVIMIENTO_CANARDS && MEZCLA_Y_SENTIDOS_VALIDADOS;
  if (habilitarPWM) {
    ESP32PWM::allocateTimer(0); ESP32PWM::allocateTimer(1);
    ESP32PWM::allocateTimer(2); ESP32PWM::allocateTimer(3);
    for (int i=0;i<4;i++) {
      servos[i].setPeriodHertz(SERVO_HZ);
      servos[i].attach(PIN_SERVO[i],SERVO_MIN_US,SERVO_MAX_US);
      servos[i].writeMicroseconds(servoCentroUs[i]);
    }
  }

  estado=INICIO;
  cambiarEstado((bnoOK && bmpOK)?CALIBRANDO:FALLA);
  publicarDatos(millis(),micros());
  xTaskCreatePinnedToCore(tareaTelemetria,"telemetria",8192,nullptr,1,nullptr,0);

  Serial.printf("BNO=%s BMP=%s SD=%s RADIO=%s PWM_CANARDS=%s\n",
    bnoOK?"OK":"ERROR",bmpOK?"OK":"ERROR",sdOK?"OK":"ERROR",
    radioOK?"OK":"ERROR",habilitarPWM?"HABILITADO":"BLOQUEADO");
  Serial.println("Comandos: ARMAR SULTANA | DESARMAR | ESTADO");
}

void loop() {
  uint32_t ahoraUs=micros();
  uint32_t ahoraMs=millis();
  leerComandosSerie();
  leerGPS();
  leerBNO();
  leerBarometro(ahoraUs);
  actualizarEstado(ahoraMs,ahoraUs);
  paracaidas.actualizar(ahoraMs,estado,kalman.x[0],kalman.x[1]);
  actualizarControl(ahoraUs);
  publicarDatos(ahoraMs,ahoraUs);
  delay(1);
}
