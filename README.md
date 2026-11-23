# Car_Traces — Enregistreur de trajets GPS avec Arduino UNO Q

> **Documentation française — version de travail (23 novembre 2026).** Le programme a fait l'objet d'essais fonctionnels, dont un test concluant de rotation à 20 fichiers GPX. La validation sur plusieurs trajets réels et le test de coupure brutale d'alimentation restent à compléter avant publication définitive.

## 1. Présentation

**Car_Traces** enregistre les trajets d'un véhicule au format **GPX 1.1** à l'aide d'une **Arduino UNO Q**, d'un récepteur GPS et d'un **Modulino Buttons**. Les traces sont stockées sur l'UNO Q et récupérables par un serveur HTTP activé à la demande.

Ce projet illustre la collaboration entre les deux environnements de l'UNO Q :

- le **MCU**, programmé en C++, lit le GPS et les boutons et pilote les LED ;
- le **MPU**, sous Linux et programmé en Python avec Arduino App Lab, détient l'état de référence, applique la logique métier, écrit les GPX et gère le serveur HTTP.

Le principe architectural est volontairement simple : **le MCU détecte les événements et transmet les données ; le MPU prend les décisions applicatives ; le MCU reflète ensuite l'état décidé par le MPU.**

## 2. Matériel et environnement

- Arduino **UNO Q** ;
- récepteur GPS fournissant des trames NMEA sur liaison série UART ;
- **Modulino Buttons** (trois boutons et trois LED) ;
- alimentation adaptée à l'UNO Q, notamment pour l'usage embarqué ;
- Arduino App Lab et les bibliothèques utilisées dans le sketch : `TinyGPSPlus`, `Arduino_RouterBridge`, `Arduino_Modulino`.

Le sketch utilise `Serial1.begin(9600)` pour le GPS et le bus utilisé par `Modulino.begin()` pour les boutons. **Avant publication, ajouter une photographie et un schéma de câblage vérifié** (alimentation, masse, TX/RX et connexion du Modulino). Ne pas déduire le brochage du seul nom `Serial1`.

## 3. Architecture MCU / MPU

```text
 Récepteur GPS (NMEA)                 Modulino Buttons
          |                                  |
          v                                  v
 +---------------------------------------------------+
 | MCU — sketch.ino                                  |
 | - Décode les trames GPS avec TinyGPSPlus           |
 | - Détecte les fronts d'appui sur A, B et C         |
 | - Envoie les données et commandes au MPU           |
 | - Affiche sur les LED l'état reçu du MPU           |
 +-----------------------+---------------------------+
                         | Arduino Bridge
                         v
 +---------------------------------------------------+
 | MPU — python/main.py                              |
 | - État applicatif et décisions START/STOP/HTTP     |
 | - Vérification du fix récent                       |
 | - Création, écriture et rotation des fichiers GPX  |
 | - Réparation au démarrage des GPX non fermés       |
 | - Serveur HTTP de consultation et téléchargement   |
 +---------------------------------------------------+
```

### Échanges par Bridge

| Fonction Python exposée | Appelée par le MCU | Rôle |
|---|---|---|
| `update_gps(...)` | À la réception d'une position mise à jour et valide selon les contrôles du sketch | Met à jour les données GPS et écrit éventuellement un point |
| `bouton("START"/"STOP"/"HTTP")` | Sur un **front d'appui** | Demande une transition ; retourne l'état des LED |
| `etat_leds()` | Environ toutes les deux secondes | Retourne l'état applicatif actuel sous forme de masque binaire |

Ces fonctions sont enregistrées avec `Bridge.provide(...)`, puis l'application Python lance `App.run()`.

## 4. Boutons, LED et logique métier

| Bouton | Commande | Action demandée au MPU | LED |
|---|---|---|---|
| **A** | `START` | Démarrer un enregistrement si le serveur HTTP est arrêté et si un fix GPS récent est disponible | A : enregistrement actif |
| **B** | `STOP` | Terminer l'enregistrement en écrivant les balises de fermeture GPX | B : **fix GPS récent**, et non état STOP |
| **C** | `HTTP` | Activer/désactiver le serveur HTTP si aucun enregistrement n'est actif | C : serveur HTTP actif |

**Attention :** la correspondance des LED est indépendante de la signification des commandes des boutons. La LED B indique la réception GPS, pas l'arrêt de l'enregistrement.

### Transitions autorisées

- **START** est refusé lorsque le serveur HTTP est actif ou lorsqu'aucun fix récent n'est disponible.
- **HTTP** est refusé pendant un enregistrement.
- **STOP** termine l'enregistrement actif ; sans enregistrement, la commande est sans effet.
- Un nouvel appui sur **HTTP** désactive le serveur lorsqu'il est déjà actif.

Le MCU n'entretient pas une deuxième machine à états : les décisions sont prises par `main.py`.

### Pourquoi synchroniser les LED toutes les deux secondes ?

Après un appui, le MPU retourne immédiatement l'état des LED. Cependant, **la présence d'un fix GPS récent peut changer sans aucun appui**, par exemple à l'entrée ou à la sortie d'un tunnel. Une synchronisation périodique permet donc de mettre à jour la LED B et de rétablir la cohérence après un éventuel échec de communication.

Cette interrogation n'entraîne pas systématiquement une écriture sur le Modulino : `afficherLeds()` compare l'état reçu à `dernierEtat` et n'appelle `buttons.setLeds(...)` **que lorsque l'état change**.

### Comprendre le masque binaire

Le MPU encode les trois LED dans un seul entier :

| Bit | Valeur | Signification |
|---|---:|---|
| 0 | `1` | Enregistrement actif (LED A) |
| 1 | `2` | Fix GPS récent (LED B) |
| 2 | `4` | Serveur HTTP actif (LED C) |

Par exemple, `3 = 1 + 2` signifie « enregistrement actif et fix récent » ; `6 = 2 + 4` signifie « fix récent et serveur HTTP actif ». Le MCU décode ces informations avec `etat & 1`, `etat & 2` et `etat & 4`.

Les états où l'enregistrement et HTTP sont simultanément actifs ne sont pas prévus par la logique métier. **Simulateur interactif :** [ouvrir le simulateur des masques binaires](docs/simulateur_bits.html). Le fichier HTML fonctionne localement dans un navigateur, sans connexion à l’UNO Q. Pour le consulter directement depuis GitHub, télécharger le fichier ou activer GitHub Pages pour le dossier `docs/` ; GitHub n’exécute pas le JavaScript dans l’aperçu du README.

## 5. Acquisition GPS et génération des GPX

Le MCU lit les octets reçus sur `Serial1` et les transmet au décodeur `TinyGPSPlus`. L'envoi au MPU intervient lorsque la position a été mise à jour et que la position, la date, l'heure, l'altitude, le nombre de satellites et le HDOP sont signalés comme valides.

Le MPU mémorise l'instant de réception d'une mise à jour avec `time.monotonic()`. Un **fix récent** est défini par `FIX_MAX_S = 3.0` secondes. Un démarrage d'enregistrement exige un tel fix.

Pendant un enregistrement, `PERIODE_S = 1.5` fixe **l'intervalle minimal entre deux points écrits**, à condition que de nouvelles données GPS arrivent. Ce réglage ne garantit pas un point exactement toutes les 1,5 secondes : la fréquence effective dépend notamment des trames reçues.

Chaque point contient latitude, longitude, altitude (`ele`), horodatage (`time`), nombre de satellites (`sat`) et HDOP (`hdop`). Les dates/heures GPS, reçues en UTC, sont converties en heure locale française avec `ZoneInfo("Europe/Paris")`, qui gère les changements d'heure saisonniers.

Exemple simplifié :

```xml
<trkpt lat="46.00000000" lon="0.00000000">
  <ele>100.00</ele>
  <time>2026-11-23T14:00:00+01:00</time>
  <sat>10</sat>
  <hdop>0.90</hdop>
</trkpt>
```

Les valeurs ci-dessus sont **fictives** et servent uniquement à illustrer la structure.

Une perte de réception GPS peut produire un **intervalle sans nouveaux points**, sans rendre pour autant le fichier XML invalide. Une position aberrante peut déformer le tracé même si le XML reste correctement formé ; l'application ne prétend pas corriger systématiquement les erreurs de positionnement satellite.

## 6. Stockage et rotation des fichiers

Les GPX sont stockés dans le dossier `data/gpx` de l'application, créé automatiquement si nécessaire. Les noms suivent le modèle `track_YYYYMMDD_HHMMSS.gpx` (date et heure **UTC**) ; un suffixe numéroté évite les collisions de noms.

`NB_FICHIERS_MAX = 20` limite le nombre de fichiers. **À la création d'un nouvel enregistrement**, la fonction `rotation()` supprime le ou les plus anciens fichiers nécessaires pour rester sous la limite. Il n'y a pas de suppression à chaque nouveau point GPS.

**Précaution :** récupérer régulièrement les trajets importants. La rotation supprime définitivement les fichiers les plus anciens ; il ne s'agit pas d'une sauvegarde.

## 7. Serveur HTTP et récupération des traces

Le serveur Python `ThreadingHTTPServer` écoute sur le **port 8000** et est **désactivé par défaut**. Le bouton C l'active ou le désactive. Le serveur ne peut pas être activé pendant un enregistrement.

Remplacer `ADRESSE_UNO_Q` par l'adresse réseau réellement accessible de l'UNO Q :

| URL | Fonction |
|---|---|
| `http://ADRESSE_UNO_Q:8000/` | Télécharge le dernier GPX terminé |
| `http://ADRESSE_UNO_Q:8000/interpret` | Sert ce GPX sans forcer le téléchargement |
| `http://ADRESSE_UNO_Q:8000/liste` | Renvoie la liste des fichiers GPX en JSON |
| `http://ADRESSE_UNO_Q:8000/gpx/NOM.gpx` | Télécharge le fichier désigné |
| `http://ADRESSE_UNO_Q:8000/etat` | Renvoie l'état de l'application et les dernières données GPS en JSON |

### Télécharger le dernier GPX depuis un autre ordinateur

Depuis un terminal sur un ordinateur connecté au même réseau que l’UNO Q (par exemple le Terra), utiliser **l’adresse IP de l’UNO Q** :

```bash
curl -o trace.gpx http://IP_UNO_Q:8000/
```

L’option **`-o` (lettre o minuscule)** permet de **choisir le nom local** du fichier : ici `trace.gpx`. Relancer cette commande remplace le fichier local existant du même nom, ce qui facilite son ouverture dans un logiciel de visualisation. Attention : `-o` peut écraser un fichier local ; ne pas l’utiliser pour conserver un historique sous des noms différents.

La route `/` renvoie toujours **le dernier GPX terminé**. Si plusieurs enregistrements ont été réalisés au cours d’un trajet, cette commande n’en récupère qu’un seul.

### Consulter la liste et télécharger une trace précise

```bash
curl http://IP_UNO_Q:8000/liste
```

Après avoir repéré le nom souhaité dans la liste JSON :

```bash
curl -O http://IP_UNO_Q:8000/gpx/track_20261123_103000.gpx
```

**Attention : `-O` est un O MAJUSCULE, et non un `-o` minuscule !** L’option `-O` enregistre le fichier sous son **nom distant** (ici `track_20261123_103000.gpx`). Avec `-o`, il faudrait préciser soi-même un nom local après l’option. Le nom d’exemple ci-dessus est fictif : utiliser un nom réellement renvoyé par `/liste`.

Pour consulter le dernier fichier sans imposer le téléchargement dans le navigateur :

```text
http://IP_UNO_Q:8000/interpret
```

Sous Windows, on peut employer explicitement `curl.exe` à la place de `curl`.

**`localhost` ne convient pas depuis un autre ordinateur** : il désigne la machine qui exécute la commande. `http://localhost:8000/etat` ne peut être utilisé que depuis le système hôte Linux de l’UNO Q (si le port y est publié). Pour récupérer les traces depuis le Terra, un Mac ou un autre appareil, utiliser `IP_UNO_Q`.

La route `/` et la route `/interpret` sélectionnent le **dernier fichier terminé**, à l'exclusion du fichier en cours d'enregistrement. La route `/gpx/NOM.gpx`, quant à elle, ne comporte pas cette exclusion explicite dans le code ; en utilisation normale, le serveur HTTP et l'enregistrement sont mutuellement exclusifs.

**Sécurité :** le serveur écoute sur `0.0.0.0` et ne comporte pas d'authentification. Il est destiné à un réseau de confiance ; ne pas exposer directement son port sur Internet.

## 8. Réparation des fichiers après interruption

Au lancement de `main.py`, `reparer_gpx()` examine les fichiers `.gpx` :

1. Si la balise `</gpx>` apparaît à la fin du fichier, celui-ci est considéré comme fermé et n'est pas modifié.
2. Sinon, la fonction recherche la **dernière occurrence de `</trkpt>`**.
3. Si elle en trouve une, elle tronque le fichier juste après cette balise et ajoute les balises finales `</trkseg>`, `</trk>` et `</gpx>`.
4. Si aucun trackpoint complet n'est trouvé, elle signale le cas dans les journaux et laisse le fichier tel quel.

Cette réparation a été testée après une **troncature manuelle** d'un GPX. Elle doit encore être validée lors d'une coupure d'alimentation réelle.

### Limites de la réparation

La fonction actuelle ne valide **pas** tous les points du document et ne protège pas contre toutes les formes de corruption. Elle modifie directement le fichier concerné et ne crée pas de copie de sauvegarde préalable. Les données non encore écrites durablement sur le stockage au moment d'une coupure ne peuvent pas être garanties récupérables.

La documentation doit donc parler de **récupération des fichiers non fermés**, et non de garantie d'intégrité absolue.

## 9. Configuration App Lab : `app.yaml`

La déclaration du port est essentielle pour l'accès au serveur HTTP dans l'environnement Arduino App Lab :

```yaml
name: Car_Traces
description: "Enregistreur de trajets GPS au format GPX avec Arduino UNO Q"
ports:
  - 8000
bricks: []
icon: 🚗
```

L'icône **🚗** remplace l'icône provisoire 😀. La description est également proposée pour mieux identifier l'application. **Vérifier la prise en charge de cette icône par la version d'App Lab utilisée.**

**Origine de cette configuration :** échange avec **ptillich** sur le forum Arduino, [message nº 11](https://forum.arduino.cc/t/arduino-app-lab-exposing-a-json-api-directly-from-main-py-without-a-custom-brick/1466096/11). La publication du port simplifie l’accès depuis l’hôte et les autres machines du réseau ; elle n’est **pas nécessaire pour que le serveur Python démarre** dans son conteneur. Vérification effectuée avec `docker ps` : `0.0.0.0:8000->8000/tcp` et `:::8000->8000/tcp`.

## 10. Installation et mise en service

1. Créer ou ouvrir l'application **Car_Traces** dans Arduino App Lab.
2. Placer le sketch C++ dans la partie MCU et `main.py` dans la partie Python de l'application.
3. Configurer `app.yaml`, notamment `ports: - 8000`.
4. Installer ou vérifier les bibliothèques nécessaires au sketch.
5. Raccorder le GPS et le Modulino Buttons conformément au schéma matériel **à ajouter**.
6. Lancer l'application ; attendre un fix GPS récent (LED B).
7. Appuyer sur **A** pour enregistrer, **B** pour terminer, puis **C** pour activer le serveur HTTP et récupérer les traces.

Le programme Python crée automatiquement `data/gpx` et répare, au démarrage, les GPX non fermés qu'il peut récupérer.

**À documenter avant publication :** versions exactes d'App Lab, du firmware et des bibliothèques ; disposition définitive des fichiers du dépôt ; mode de lancement automatique retenu et éventuelles particularités de la connexion réseau.

## 11. Vérifications réalisées et validation restante

**Vérifications déjà rapportées :** acquisition GPS et création de traces, lecture dans un outil de visualisation GPX, fonctionnement des commandes et des LED, téléchargement HTTP depuis une autre machine, consultation de `/liste` et récupération d’un GPX précis avec `-O`, réparation d'un fichier volontairement tronqué et rotation à **20 GPX**.

**Avant publication définitive :** réaliser plusieurs trajets réels, vérifier la reprise après perte de réception (tunnel), contrôler les fichiers téléchargés et tester une coupure d'alimentation dans des conditions sûres. Ne pas réaliser de manipulation électrique pendant la conduite.

## 12. Intérêt pédagogique

Car_Traces réunit plusieurs notions : UART/NMEA, TinyGPSPlus, I²C et périphériques Modulino, détection de fronts d'appui, échanges interprocesseurs avec Bridge, séparation entre acquisition matérielle et logique métier, synchronisation d'état, masques binaires, concurrence et verrous Python, serveur HTTP, XML/GPX, fuseaux horaires, rotation et récupération de fichiers.

La leçon centrale est la **répartition explicite des responsabilités** : le MCU assure l'interface matérielle, tandis que le MPU reste la source de vérité et prend les décisions applicatives.

## 13. Crédits

**Projet porté par P. COSTES :** idée, réalisation matérielle, intégration sur Arduino UNO Q, essais et validation en conditions réelles.

**Développement en collaboration avec ChatGPT (OpenAI) :** échanges sur l’architecture MCU/MPU, conception et analyse de la logique applicative, assistance à l’écriture et à la révision du code C++ et Python, recherche de solutions aux problèmes rencontrés, préparation des scénarios de test et rédaction de la documentation. Les propositions ont été examinées et éprouvées sur le matériel par P. COSTES.

**Simulateur pédagogique des masques binaires :** réalisé avec l’assistance de ChatGPT (OpenAI).

Remerciements à **ptillich** pour son aide concernant la [publication du port HTTP dans Arduino App Lab](https://forum.arduino.cc/t/arduino-app-lab-exposing-a-json-api-directly-from-main-py-without-a-custom-brick/1466096/11).

---

*Ce README français est une base de travail. Après validation en conditions réelles, il pourra être finalisé, traduit en anglais et accompagné d'une présentation sur le forum Arduino.*
