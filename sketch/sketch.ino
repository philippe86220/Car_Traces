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

void afficherLeds(int etat) {
  if (!boutonsOK || etat == dernierEtat) return; 
  buttons.setLeds((etat & 1) != 0, (etat & 2) != 0, (etat & 4) != 0); // Méthode Modulino.h -> buttons.setLeds(bool a,bool b,bool c)
  dernierEtat = etat;
}

void lireBoutons() {
  if (!boutonsOK || !buttons.update()) return; // Méthode Modulino.h -> buttons.update() -> bool
  for (int i = 0; i < 3; i++) {
    bool appuye = buttons.isPressed(i); // Méthode Modulino.h -> buttons.isPressed(i)
    bool front = appuye && !etatPrecedent[i];
    etatPrecedent[i] = appuye;
    if (front) {
      Monitor.print("Bouton -> ");
      Monitor.println(commandes[i]);
      int etat = 0;
      if (Bridge.call("bouton", commandes[i]).result(etat)) {
        afficherLeds(etat);
      } else {
        afficherLeds(0);
        Monitor.println("Pas de réponse de main.py");
      }
      derniereSynchroLeds = millis();
    }
  }
}

void synchroLeds() {
  if (!boutonsOK || millis() - derniereSynchroLeds < 2000UL) return;
  derniereSynchroLeds = millis();
  int etat = 0;
  if (Bridge.call("etat_leds").result(etat)) afficherLeds(etat); 
  else afficherLeds(0);
}

void setup() {
  Bridge.begin();
  Monitor.begin();
  Modulino.begin();
  boutonsOK = buttons.begin();
  if (!boutonsOK) Monitor.println("Modulino Buttons introuvable !");
  afficherLeds(0);
  Serial1.begin(9600);
}

void loop() {
  // Priorité à la lecture du GPS.
  while (Serial1.available() > 0) gps.encode(Serial1.read());

  if (gps.location.isUpdated() && gps.location.isValid() &&
      gps.date.isValid() && gps.time.isValid() &&
      gps.altitude.isValid() && gps.satellites.isValid() && gps.hdop.isValid()) {
    Bridge.call("update_gps",
                gps.location.lat(), gps.location.lng(),
                gps.date.day(), gps.date.month(), gps.date.year(),
                gps.time.hour(), gps.time.minute(), gps.time.second(),
                gps.satellites.value(), gps.altitude.meters(), gps.hdop.hdop());
  }
  lireBoutons();
  synchroLeds();
}
