/*
  ================================================================
  HOME SECURITY SYSTEM — Arduino Code
  ================================================================

  HARDWARE CONNECTIONS:
  ┌──────────────────────────────────────────────────────────────┐
  │  Component            │  Arduino Pin                         │
  ├──────────────────────────────────────────────────────────────┤
  │  Servo Motor          │  D5                                  │
  │  Buzzer (Piezo)       │  D6                                  │
  │  IR Sensor            │  D7  — HIGH = restart system        │
  │  Right Voltage Sensor │  A1  (30kΩ + 7.5kΩ divider)        │
  │  Left  Voltage Sensor │  A0  (30kΩ + 7.5kΩ divider)        │
  └──────────────────────────────────────────────────────────────┘

  FLASK REPLY CODES:
    0 = unknown person in front → Arduino plays long beep immediately,
        keeps beeping until reply 1 arrives
    1 = known family member confirmed (stayed 5 seconds)
        → three pleasant rising tones, wait 5s, resume

  HOW IT WORKS:
  Unknown appears → Flask sends 0 → Arduino long beep (2 sec) → waits
  If unknown stays → Flask keeps sending 0 → more long beeps
  Known confirmed  → Flask sends 1 → beep stops → welcome melody plays

  VOLTAGE SENSITIVITY:
  SENSOR_THRESHOLD = 1 → trigger on any voltage above 0.
  Raise to 5-10 if electrical noise causes false triggers.

  DEBOUNCE: 30ms — sensor must stay HIGH for 30ms to be real.

  ================================================================
*/

#include <Servo.h>

// ── PINS ──────────────────────────────────────────────────────
#define SERVO_PIN         5
#define BUZZER_PIN        6
#define IR_SENSOR_PIN     7
#define RIGHT_SENSOR_PIN  A1
#define LEFT_SENSOR_PIN   A0

// ── IR TRIGGER STATE ──────────────────────────────────────────
#define IR_TRIGGER_STATE  HIGH   // change to LOW if sensor is inverted

// ── SENSITIVITY ───────────────────────────────────────────────
#define SENSOR_THRESHOLD  1      // 1 = any voltage > 0 triggers

// ── DEBOUNCE ──────────────────────────────────────────────────
#define DEBOUNCE_MS       30

// ── SERVO ─────────────────────────────────────────────────────
#define SERVO_MIN_ANGLE   0
#define SERVO_MAX_ANGLE   180
#define SERVO_STEP        1
#define SERVO_STEP_DELAY  20

// ── TIMING ────────────────────────────────────────────────────
#define WAIT_FOR_FLASK_MS 15000  // max 15s wait for Flask reply
#define RESUME_DELAY_MS   20000  // 20s pause after unknown confirmed
#define KNOWN_DELAY_MS    5000   //  5s pause after known confirmed

// ── STATE ─────────────────────────────────────────────────────
Servo servo;

int           servoAngle     = 0;
int           servoDir       = 1;
bool          sweeping       = true;
unsigned long lastStepTime   = 0;

unsigned long rightHighSince = 0;
unsigned long leftHighSince  = 0;
bool          rightArmed     = false;
bool          leftArmed      = false;

int           lastIrState    = LOW;

// ── PROTOTYPES ────────────────────────────────────────────────
void handleTrigger(const char* side, int stopAngle);
void systemRestart();
int  waitForFlaskReply();
void longBeepBuzzer();
void alarmBuzzer();
void welcomeBuzzer();
void resumeSurveillance();

// =============================================================
// SETUP
// =============================================================
void setup() {
  Serial.begin(9600);

  pinMode(BUZZER_PIN,    OUTPUT);
  pinMode(IR_SENSOR_PIN, INPUT);

  servo.attach(SERVO_PIN);
  servo.write(SERVO_MIN_ANGLE);
  servoAngle   = SERVO_MIN_ANGLE;
  servoDir     = 1;
  sweeping     = true;
  lastStepTime = millis();
  lastIrState  = digitalRead(IR_SENSOR_PIN);

  // Startup beep
  tone(BUZZER_PIN, 1000, 150); delay(200);
  tone(BUZZER_PIN, 1600, 150); delay(200);
  noTone(BUZZER_PIN);

  Serial.println("HOME_SECURITY_READY");
}

// =============================================================
// MAIN LOOP
// =============================================================
void loop() {
  unsigned long now = millis();

  // ── IR SENSOR — checked always ───────────────────────────────
  int irNow = digitalRead(IR_SENSOR_PIN);
  if (irNow == IR_TRIGGER_STATE && lastIrState != IR_TRIGGER_STATE) {
    lastIrState = irNow;
    systemRestart();
    return;
  }
  lastIrState = irNow;

  if (!sweeping) return;

  // ── SERVO SWEEP ──────────────────────────────────────────────
  if (now - lastStepTime >= SERVO_STEP_DELAY) {
    lastStepTime = now;
    servoAngle  += servoDir * SERVO_STEP;
    if (servoAngle >= SERVO_MAX_ANGLE) { servoAngle = SERVO_MAX_ANGLE; servoDir = -1; }
    if (servoAngle <= SERVO_MIN_ANGLE) { servoAngle = SERVO_MIN_ANGLE; servoDir =  1; }
    servo.write(servoAngle);
  }

  // ── VOLTAGE SENSORS ──────────────────────────────────────────
  int rightVal = analogRead(RIGHT_SENSOR_PIN);
  int leftVal  = analogRead(LEFT_SENSOR_PIN);

  // RIGHT (A1)
  if (rightVal > SENSOR_THRESHOLD) {
    if (!rightArmed) { rightArmed = true; rightHighSince = now; }
    else if (now - rightHighSince >= DEBOUNCE_MS) {
      rightArmed = false; leftArmed = false;
      handleTrigger("R", SERVO_MIN_ANGLE);
      return;
    }
  } else { rightArmed = false; }

  // LEFT (A0)
  if (leftVal > SENSOR_THRESHOLD) {
    if (!leftArmed) { leftArmed = true; leftHighSince = now; }
    else if (now - leftHighSince >= DEBOUNCE_MS) {
      leftArmed = false; rightArmed = false;
      handleTrigger("L", SERVO_MAX_ANGLE);
      return;
    }
  } else { leftArmed = false; }
}

// =============================================================
// HANDLE SENSOR TRIGGER
//
// Flask reply codes:
//   0 = unknown person visible (not confirmed yet)
//       → long continuous beep while waiting for next reply
//   1 = unknown confirmed (10 sec stayed) → alarm beeps
//   2 = known confirmed   ( 5 sec stayed) → welcome melody
// =============================================================
void handleTrigger(const char* side, int stopAngle) {
  sweeping = false;
  servo.write(stopAngle);

  Serial.println(side);   // tell Flask which side triggered

  int reply = waitForFlaskReply();

  noTone(BUZZER_PIN);     // make sure buzzer is off before acting

  if (reply == 1) {
    // Known person confirmed (5 sec) → welcome melody
    welcomeBuzzer();
    delay(KNOWN_DELAY_MS);
  }
  // reply ==  0 : unknown in front → long beep handled inside waitForFlaskReply
  // reply == -1 : IR triggered restart → already handled inside wait
  // timeout     : no reply → resume quietly

  resumeSurveillance();
}

// =============================================================
// WAIT FOR FLASK REPLY
//
// Stays in this loop until Flask sends 1 or 2.
// While waiting:
//   - If Flask sends 0 → unknown person spotted →
//     play one long beep (2 seconds) then keep waiting
//   - Keeps checking IR sensor to allow restart at any time
// Returns: 1, 2, 0 (timeout), or -1 (IR restart)
// =============================================================
int waitForFlaskReply() {
  unsigned long start       = millis();
  bool          buzzerState = false;    // is buzzer currently ON?

  while (millis() - start < WAIT_FOR_FLASK_MS) {

    // Always check IR sensor — allow restart even during wait
    int irNow = digitalRead(IR_SENSOR_PIN);
    if (irNow == IR_TRIGGER_STATE && lastIrState != IR_TRIGGER_STATE) {
      lastIrState = irNow;
      noTone(BUZZER_PIN);
      systemRestart();
      return -1;
    }
    lastIrState = irNow;

    // Read serial byte from Flask
    if (Serial.available() > 0) {
      int val = Serial.read();

      if (val == 0) {
        // Unknown person in front → long beep (2 sec), then keep waiting
        noTone(BUZZER_PIN);
        tone(BUZZER_PIN, 1800);
        delay(2000);
        noTone(BUZZER_PIN);
        delay(200);
        // keep looping — wait for reply 1 (known confirmed)

      } else if (val == 1) {
        // Known person confirmed (5 sec) → exit, play welcome
        noTone(BUZZER_PIN);
        return 1;
      }
    }

    delay(20);
  }

  noTone(BUZZER_PIN);
  return 0;   // timeout — no reply arrived
}

// =============================================================
// SYSTEM RESTART — IR sensor on D7 triggered
// =============================================================
void systemRestart() {
  noTone(BUZZER_PIN);

  servo.write(SERVO_MIN_ANGLE);
  delay(300);

  servoAngle     = SERVO_MIN_ANGLE;
  servoDir       = 1;
  sweeping       = true;
  lastStepTime   = millis();
  rightArmed     = false;
  leftArmed      = false;
  rightHighSince = 0;
  leftHighSince  = 0;

  tone(BUZZER_PIN, 1200, 100);
  delay(150);
  noTone(BUZZER_PIN);

  Serial.println("RESTART");
}

// =============================================================
// ALARM BUZZER — Unknown person confirmed (10 sec)
// Fast aggressive beeps for ~5 seconds
// =============================================================
void alarmBuzzer() {
  for (int i = 0; i < 25; i++) {
    tone(BUZZER_PIN, 2200);
    delay(120);
    noTone(BUZZER_PIN);
    delay(80);
  }
}

// =============================================================
// WELCOME BUZZER — Known family member confirmed (5 sec)
// Three pleasant rising tones
// =============================================================
void welcomeBuzzer() {
  tone(BUZZER_PIN, 1047, 180);  // C6
  delay(230);
  tone(BUZZER_PIN, 1319, 180);  // E6
  delay(230);
  tone(BUZZER_PIN, 1568, 260);  // G6
  delay(300);
  noTone(BUZZER_PIN);
}

// =============================================================
// RESUME SURVEILLANCE SWEEP
// =============================================================
void resumeSurveillance() {
  Serial.println("CLEAR");
  sweeping       = true;
  rightArmed     = false;
  leftArmed      = false;
  lastStepTime   = millis();
}
