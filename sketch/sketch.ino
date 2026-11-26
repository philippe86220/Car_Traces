
#include <Arduino.h>
#include <TinyGPSPlus.h>
#include <Arduino_RouterBridge.h>
#include <Arduino_Modulino.h>

TinyGPSPlus gps;
ModulinoButtons buttons;

const char* commandes[3] = { "START", "STOP", "HTTP" };
bool etatPrecedent[3] = { false, false, false };
bool boutonsOK = false;
unsigned long derniereSynchroLeds = 0;
int dernierEtat = -1;

// Dernier masque communiqué par le MPU,
// ou 0 en cas d'échec de communication.
int etatMPU = 0;

// Signalisation de fin d'initialisation.
enum EtatInitialisation {
  ATTENTE_MPU,
  CLIGNOTEMENT,
  TERMINE
};

EtatInitialisation initialisation = ATTENTE_MPU;

const unsigned long PERIODE_CLIGNOTEMENT = 200UL;
const unsigned int NB_CLIGNOTEMENTS = 3;

unsigned long debutClignotement = 0;

void afficherLeds(int etat) {
  if (!boutonsOK || etat == dernierEtat) return;

  buttons.setLeds(
    (etat & 1) != 0,
    (etat & 2) != 0,
    (etat & 4) != 0
  );

  dernierEtat = etat;
}

// Mémorise l'état communiqué par le MPU.
// Pendant le clignotement, la LED C est
// temporairement réservée à l'initialisation.
void memoriserEtat(int etat) {
  etatMPU = etat;

  if (initialisation != CLIGNOTEMENT) {
    afficherLeds(etatMPU);
  }
}

// Signalisation non bloquante de fin d'initialisation.
void signalerInitialisation() {
  if (initialisation != CLIGNOTEMENT) return;

  unsigned long ecoule = millis() - debutClignotement;

  unsigned long phase = ecoule / PERIODE_CLIGNOTEMENT;

  if (phase >= NB_CLIGNOTEMENTS * 2UL) {
    initialisation = TERMINE;

    // Rétablissement de l'état réel des trois LED.
    afficherLeds(etatMPU);
    return;
  }

  // Conservation des bits A et B.
  // Le bit C est temporairement piloté
  // par la séquence de clignotement.
  int etat = etatMPU & 3;

  if (phase % 2 == 0) {
    etat |= 4;
  }

  afficherLeds(etat);
}

void lireBoutons() {
  if (!boutonsOK || !buttons.update()) return;

  for (int i = 0; i < 3; i++) {
    bool appuye = buttons.isPressed(i);
    bool front = appuye && !etatPrecedent[i];
    etatPrecedent[i] = appuye;

    if (front) {
      Monitor.print("Bouton -> ");
      Monitor.println(commandes[i]);

      int etat = 0;

      if (Bridge.call("bouton", commandes[i]).result(etat)) {
        memoriserEtat(etat);
      } else {
        memoriserEtat(0);
        Monitor.println("Pas de réponse de main.py");
      }

      derniereSynchroLeds = millis();
    }
  }
}

void synchroLeds() {
  if (!boutonsOK ||
      millis() - derniereSynchroLeds < 2000UL) return;

  derniereSynchroLeds = millis();

  int etat = 0;

  if (Bridge.call("etat_leds").result(etat)) {

    memoriserEtat(etat);

    // Une seule séquence par démarrage du MCU.
    if (initialisation == ATTENTE_MPU) {
      initialisation = CLIGNOTEMENT;
      debutClignotement = millis();

      Monitor.println("Car_Traces operationnel");
    }

  } else {
    memoriserEtat(0);
  }
}

void setup() {
  Bridge.begin();
  Monitor.begin();
  Modulino.begin();

  boutonsOK = buttons.begin();

  if (!boutonsOK) {
    Monitor.println("Modulino Buttons introuvable !");
  }

  afficherLeds(0);
  Serial1.begin(9600);
}

void loop() {
  // Priorité à la lecture du GPS.
  while (Serial1.available() > 0) gps.encode(Serial1.read());

  if (gps.location.isUpdated() && gps.location.isValid() &&
      gps.date.isValid() && gps.time.isValid() &&
      gps.altitude.isValid() && gps.satellites.isValid() &&
      gps.hdop.isValid()) {

    Bridge.call("update_gps",
                gps.location.lat(), gps.location.lng(),
                gps.date.day(), gps.date.month(), gps.date.year(),
                gps.time.hour(), gps.time.minute(), gps.time.second(),
                gps.satellites.value(), gps.altitude.meters(),
                gps.hdop.hdop());
  }

  lireBoutons();
  synchroLeds();
  signalerInitialisation();
}
