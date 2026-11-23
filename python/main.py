import datetime
import json
import os
import threading
import time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, unquote
from zoneinfo import ZoneInfo

from arduino.app_utils import App, Bridge

# ============================================================ Réglages
# Dossier "data/gpx" à côté du dossier python/ de l'application
DOSSIER_GPX = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "gpx"))
NB_FICHIERS_MAX = 20      # au-delà, le plus ancien est supprimé
PERIODE_S = 1.5            # secondes entre deux trackpoints (sample = 1500 ms)
PORT = 8000                # port du serveur HTTP
TZ = ZoneInfo("Europe/Paris")   # gère tout seul heure d'été / heure d'hiver

os.makedirs(DOSSIER_GPX, exist_ok=True)
print("Python ready - GPX dans", DOSSIER_GPX, flush=True)

_lock = threading.Lock()
serveur = None
thread_http = None
_lock_commandes = threading.Lock()
FIX_MAX_S = 3.0
_dernier_fix = None

_state = {
    "lat": None, "long": None,
    "jour": None, "mois": None, "annee": None,
    "heure": None, "minute": None, "seconde": None,
    "numsat": None, "altitude": None, "hdop": None,
}
_enreg = {"actif": False, "fichier": None, "dernier_point": 0.0, "nb_points": 0}

ENTETE = (
    '<?xml version="1.0" encoding="UTF-8" standalone="no" ?>\n'
    '<gpx version="1.1" creator="P. COSTES" xmlns="http://www.topografix.com/GPX/1/1" '
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">\n'
    '  <metadata>\n'
    '    <name>{nom}</name>\n'
    '    <desc>Arduino UNO Q - GPX</desc>\n'
    '    <author>\n'
    '      <name>P. COSTES</name>\n'
    '    </author>\n'
    '  </metadata>\n'
    '  <trk>\n'
    '    <name>{nom}</name>\n'
    '    <desc>Arduino UNO Q</desc>\n'
    '    <trkseg>\n'
)
FIN = '    </trkseg>\n  </trk>\n</gpx>\n'


# ============================================================ Outils
def now_utc_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def heure_locale():
    """Heure GPS (UTC) convertie en heure de Paris, avec le bon décalage."""
    s = _state
    utc = datetime.datetime(s["annee"], s["mois"], s["jour"],
                            s["heure"], s["minute"], s["seconde"],
                            tzinfo=datetime.timezone.utc)
    return utc.astimezone(TZ)


def chemin(nom):
    return os.path.join(DOSSIER_GPX, os.path.basename(nom))


def liste_gpx():
    """Fichiers GPX triés du plus ancien au plus récent (le nom contient la date)."""
    return sorted((f for f in os.listdir(DOSSIER_GPX) if f.endswith(".gpx")),
                  key=lambda f: f[:-4])


def rotation():
    """Supprime les plus anciens pour laisser la place au nouveau fichier."""
    fichiers = liste_gpx()
    while len(fichiers) >= NB_FICHIERS_MAX:
        os.remove(chemin(fichiers.pop(0)))


def reparer_gpx():
    """Répare les fichiers GPX interrompus pendant un enregistrement."""

    for nom in liste_gpx():
        fichier = chemin(nom)

        with open(fichier, "rb+") as f:
            # Vérifier si le fichier est déjà correctement fermé.
            f.seek(0, os.SEEK_END)
            taille = f.tell()

            f.seek(max(0, taille - 30))
            fin = f.read()

            if b"</gpx>" in fin:
                continue

            # Rechercher le dernier trackpoint complet.
            # Lecture intégrale pour cette première version.
            f.seek(0)
            contenu = f.read()

            balise = b"</trkpt>"
            position = contenu.rfind(balise)

            if position == -1:
                print("Aucun trackpoint complet :", nom, flush=True)
                continue

            # Conserver les données jusqu'à la fin
            # du dernier trackpoint valide.
            position += len(balise)

            f.seek(position)
            f.truncate()

            # Ajouter les balises de fermeture du GPX.
            f.write(("\n" + FIN).encode("utf-8"))

        print("Fichier réparé :", nom, flush=True)


def ajouter_trackpt():
    s = _state
    point = (
        f'      <trkpt lat="{s["lat"]:.8f}" lon="{s["long"]:.8f}">\n'
        f'        <ele>{s["altitude"]:.2f}</ele>\n'
        f'        <time>{heure_locale().isoformat()}</time>\n'   # ex. 2026-11-18T13:34:00+01:00
        f'        <sat>{s["numsat"]}</sat>\n'
        f'        <hdop>{s["hdop"]:.2f}</hdop>\n'
        f'      </trkpt>\n'
    )
    with open(chemin(_enreg["fichier"]), "a", encoding="utf-8") as f:
        f.write(point)
    _enreg["nb_points"] += 1


# ============================================================ Enregistrement
def fix_recent():
    # Appeler sous _lock.
    return _dernier_fix is not None and time.monotonic() - _dernier_fix <= FIX_MAX_S


def demarrer():
    with _lock:
        if _enreg["actif"]:
            return True, _enreg["fichier"]
        if not fix_recent():
            return False, "pas de fix GPS"
        base = heure_locale().astimezone(datetime.timezone.utc).strftime("track_%Y%m%d_%H%M%S")
        nom = base + ".gpx"
        n = 1
        while os.path.exists(chemin(nom)):
            nom = f"{base}_{n:04d}.gpx"
            n += 1
        rotation()
        with open(chemin(nom), "x", encoding="utf-8") as f:
            f.write(ENTETE.format(nom=nom))
        _enreg.update(actif=True, fichier=nom, dernier_point=0.0, nb_points=0)
        print("Début enregistrement :", nom, flush=True)
        return True, nom


def arreter():
    with _lock:
        if not _enreg["actif"]:
            return False, "aucun enregistrement en cours"
        with open(chemin(_enreg["fichier"]), "a", encoding="utf-8") as f:
            f.write(FIN)
        _enreg["actif"] = False
        print("Fin enregistrement :", _enreg["fichier"], flush=True)
        return True, _enreg["fichier"]


# ============================================================ Bridge (appelé par le MCU)
def update_gps(lat, long, jour, mois, annee, heure, minute, seconde,
               numsat, altitude, hdop):
    global _dernier_fix
    with _lock:
        _dernier_fix = time.monotonic()
        _state.update(lat=lat, long=long, jour=jour, mois=mois, annee=annee,
                      heure=heure, minute=minute, seconde=seconde,
                      numsat=numsat, altitude=altitude, hdop=hdop)

        if _enreg["actif"] and time.monotonic() - _enreg["dernier_point"] >= PERIODE_S:
            ajouter_trackpt()
            _enreg["dernier_point"] = time.monotonic()

def api_state():
    with _lock:
        payload = dict(_state)
        payload["fix_recent"] = fix_recent()
        payload["now_utc"] = now_utc_iso()
        payload["enregistrement"] = _enreg["actif"]
        payload["fichier"] = _enreg["fichier"]
        payload["nb_points"] = _enreg["nb_points"]
    return payload


#============================================================= Fonctions de gestion du serveur

def demarrer_serveur():
    # Appeler uniquement sous _lock_commandes.
    global serveur, thread_http
    serveur = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    thread_http = threading.Thread(target=serveur.serve_forever,
                                   kwargs={"poll_interval": 0.1}, daemon=True)
    try:
        thread_http.start()
    except Exception:
        serveur.server_close()
        serveur = thread_http = None
        raise
    print("Serveur HTTP actif", flush=True)


def arreter_serveur():
    global serveur, thread_http
    serveur.shutdown()
    serveur.server_close()
    thread_http.join()
    serveur = thread_http = None
    print("Serveur HTTP arrêté", flush=True)


class Handler(BaseHTTPRequestHandler):

    def _json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _fichier(self, nom, telecharger):
        if nom != os.path.basename(nom):
            return self._json({"erreur": "nom invalide"}, 400)
        p = chemin(nom)
        if not nom.endswith(".gpx") or not os.path.isfile(p):
            return self._json({"erreur": "fichier introuvable"}, 404)
        try:
            with open(p, "rb") as f:
                data = f.read()
        except FileNotFoundError:
            return self._json({"erreur": "fichier introuvable"}, 404)
        mode = "attachment" if telecharger else "inline"
        self.send_response(200)
        self.send_header("Content-Type", "text/xml; charset=utf-8")
        self.send_header("Content-Disposition", f'{mode}; filename="{os.path.basename(nom)}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = unquote(urlparse(self.path).path)

        if url in ("/", "/interpret"):
            # dernier fichier terminé (pas celui en cours d'écriture)
            with _lock:
                fichiers = [f for f in liste_gpx()
                            if not (_enreg["actif"] and f == _enreg["fichier"])]
            if not fichiers:
                return self._json({"erreur": "aucun fichier GPX"}, 404)
            self._fichier(fichiers[-1], telecharger=(url == "/"))

        elif url == "/liste":
            self._json(liste_gpx())
        elif url.startswith("/gpx/"):
            self._fichier(url[len("/gpx/"):], telecharger=True)
        elif url == "/etat":
            self._json(api_state())
        else:
            self._json({"erreur": "inconnu"}, 404)

    def log_message(self, *args):
        pass   # pas de log à chaque requête


# ============================================================ Commandes physiques

def _etat_leds():
    # Appeler sous _lock_commandes.
    with _lock:
        return ((1 if _enreg["actif"] else 0)
                | (2 if fix_recent() else 0)
                | (4 if serveur is not None else 0))


def etat_leds():
    with _lock_commandes:
        return _etat_leds()


def bouton(commande):
    with _lock_commandes:
        try:
            if commande == "START":
                if serveur is not None:
                    ok, info = False, "arrêter HTTP avant START"
                else:
                    ok, info = demarrer()
            elif commande == "STOP":
                ok, info = arreter()
            elif commande == "HTTP":
                with _lock:
                    actif = _enreg["actif"]
                if actif:
                    ok, info = False, "enregistrement en cours"
                elif serveur is None:
                    demarrer_serveur()
                    ok, info = True, "HTTP actif"
                else:
                    arreter_serveur()
                    ok, info = True, "HTTP arrêté"
            else:
                ok, info = False, "commande inconnue"
        except OSError as e:
            ok, info = False, str(e)
        print(f"Bouton {commande} : {'OK' if ok else 'refusé'} - {info}", flush=True)
        return _etat_leds()


reparer_gpx()
Bridge.provide("update_gps", update_gps)
Bridge.provide("bouton", bouton)
Bridge.provide("etat_leds", etat_leds)
App.run()
