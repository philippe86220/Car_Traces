# Liaison MCU ↔ MPU : boutons et LEDs du Modulino

Ce document explique comment le **MCU** (sketch Arduino) et le **MPU** (`main.py` sous Linux) de l'UNO Q coopèrent pour gérer les boutons et les LEDs du Modulino Buttons.

---

## 1. Qui fait quoi ?

| | MCU (STM32, sketch Arduino) | MPU (Linux, `main.py`) |
|---|---|---|
| Rôle | Interface physique | Cerveau de l'application |
| Ce qu'il fait | Lit les boutons, pilote les LEDs, acquiert et transmet les données GPS | Applique les règles, gère l'enregistrement GPS et le serveur HTTP |
| Ce qu'il sait | Le dernier état communiqué par le MPU et les états locaux des boutons | L’état métier de référence (**seule source de vérité**) |
| Décisions métier ? | Non | Oui |

> **Analogie** : le MCU est un **interrupteur avec voyants** fixé au mur ; le MPU est le **tableau électrique**.
> On appuie sur l'interrupteur, le tableau décide s'il allume ou non, puis renvoie aux voyants ce qui est **réellement** allumé.
> Dans cette analogie, l’interrupteur ne connaît aucune règle métier : il transmet l'appui et affiche ce qu'on lui dit.

---

## 2. Le lien entre les deux : le Bridge (RPC)

Le **Bridge** permet au MCU d'**appeler à distance une fonction Python exécutée sur le MPU**.
C'est un **appel de procédure à distance** (RPC, *Remote Procedure Call*).

**Côté MPU**, on **publie** (on expose) des fonctions sous un nom :

```python
Bridge.provide("bouton", bouton)
Bridge.provide("etat_leds", etat_leds)
```

**Côté MCU**, on les **appelle** par ce nom :

```cpp
Bridge.call("bouton", commandes[i])
Bridge.call("etat_leds")
```

Points importants :

- Les noms doivent être **strictement identiques** des deux côtés.
- Dans les appels présentés ici, `result()` attend la réponse du MPU (ou un échec de communication) avant que le MCU poursuive son exécution.
- Dans ces échanges, c'est le MCU qui initie l'appel RPC et le MPU qui répond.

---

## 3. Ce qui circule : un entier de 3 bits (masque de bits)

Le MPU ne renvoie pas trois booléens mais **un seul entier**, dont les trois bits de poids faible a un sens :

| Bit | Valeur | Signification (MPU) | LED (MCU) |
|---|---|---|---|
| 0 | 1 | Enregistrement GPS en cours | A |
| 1 | 2 | Fix GPS récent | B |
| 2 | 4 | Serveur HTTP actif | C |

Exemples :

| Valeur | Binaire | Signification |
|---|---|---|
| 0 | `000` | Aucun des trois états signalés |
| 3 | `011` | Enregistrement + fix GPS |
| 6 | `110` | Fix GPS + HTTP actif |

- Le **MPU construit** cette valeur avec des OU binaires `|`.
- Le **MCU la décode** avec des ET binaires `&`.

---

## 4. Le principe essentiel

> Le MCU ne demande pas **« ma commande a-t-elle marché ? »**
> Il demande **« dans quel état est le système maintenant ? »**
>
> Les LEDs affichent donc **le dernier état communiqué par le MPU**, que la commande ait été acceptée ou refusée. En cas d’échec de communication, elles sont toutes éteintes.

---

## 5. Chemin n°1 : appui sur un bouton

```
        MCU                                              MPU
 ─────────────────────                     ──────────────────────────────
 lireBoutons()
   détecte un appui (front montant)
   Bridge.call("bouton", "START") ───────►  bouton("START")
                                              1. applique la logique métier
                                                 (exécute ou refuse)
                                              2. écrit une trace (print)
                                              3. return _etat_leds()
                                                    └─ construit le masque
   .result(etat)  ◄────────────────────────    valeur renvoyée, ex. 3
   afficherLeds(etat)
```

### En 3 temps

1. **Le MCU envoie une commande.** Il appelle à distance `bouton()` avec `"START"`, `"STOP"` ou `"HTTP"`.
2. **Le MPU exécute la commande si la logique métier l'autorise**, puis écrit le résultat dans son journal. Il **ne renvoie pas** le succès ou l'échec.
3. **Le MPU renvoie l'état réel**, calculé par `_etat_leds()`. Le MCU le récupère dans sa variable `etat` et allume les LEDs.

---

## 6. Chemin n°2 : synchronisation périodique (intervalle de 2 secondes)

L'état peut changer côté MPU **sans qu'aucun bouton soit appuyé** :

- le fix GPS peut être acquis ou perdu indépendamment de tout appui sur un bouton.

Dans l’architecture retenue pour Car_Traces, le MPU ne transmet pas spontanément les changements d’état des LEDs au MCU : il **répond** aux appels.
C'est donc le MCU qui **vient demander** l'état régulièrement (*polling*), lorsque 2 secondes au moins se sont écoulées depuis la dernière synchronisation ou le dernier appel déclenché par un bouton :

```cpp
void synchroLeds() {
  if (!boutonsOK || millis() - derniereSynchroLeds < 2000UL) return;
  derniereSynchroLeds = millis();
  int etat = 0;
  if (Bridge.call("etat_leds").result(etat)) afficherLeds(etat);
  else afficherLeds(0);
}
```

---

## 7. Le code MPU expliqué

### 7.1 `_etat_leds()` : **le seul endroit** où l'état est construit

```python
def _etat_leds():
    """Calcule le masque des LEDs (bit0=enreg, bit1=fix, bit2=HTTP).
    Interne : l'appelant doit déjà tenir _lock_commandes."""
    with _lock:
        return ((1 if _enreg["actif"] else 0)
                | (2 if fix_recent() else 0)
                | (4 if serveur is not None else 0))
```

- Elle **lit** l'état du système et le **code** en masque de bits.
- Elle prend `_lock`, qui protège notamment `_enreg` et les données utilisées pour déterminer si le fix GPS est récent.
- Le `_` au début du nom indique, par convention Python, une **fonction interne**, qui n'est pas appelée directement par le MCU.

### 7.2 `etat_leds()` : **point d'entrée** pour la synchronisation

```python
def etat_leds():
    """Point d'entrée RPC appelé par le MCU (synchro périodique).
    Prend _lock_commandes puis délègue à _etat_leds()."""
    with _lock_commandes:
        return _etat_leds()
```

- C'est la fonction **publiée** sous le nom `"etat_leds"`.
- Elle prend `_lock_commandes` pour ne **jamais lire l'état au milieu d'une commande**.
- Elle **délègue** ensuite le calcul à `_etat_leds()`.

### 7.3 `bouton(commande)` : **logique métier** et point d'entrée des commandes

```python
def bouton(commande):
    with _lock_commandes:                      # une seule commande à la fois
        try:
            if commande == "START":
                if serveur is not None:        # règle : pas d'enregistrement si HTTP tourne
                    ok, info = False, "arrêter HTTP avant START"
                else:
                    ok, info = demarrer()
            elif commande == "STOP":
                ok, info = arreter()
            elif commande == "HTTP":           # bascule marche/arrêt
                with _lock:
                    actif = _enreg["actif"]    # lecture protégée, verrou relâché aussitôt
                if actif:                      # règle : pas de HTTP pendant un enregistrement
                    ok, info = False, "enregistrement en cours"
                elif serveur is None:
                    demarrer_serveur()
                    ok, info = True, "HTTP actif"
                else:
                    arreter_serveur()
                    ok, info = True, "HTTP arrêté"
            else:
                ok, info = False, "commande inconnue"
        except OSError as e:                   # erreur système (port occupé, fichier...)
            ok, info = False, str(e)
        print(f"Bouton {commande} : {'OK' if ok else 'refusé'} - {info}", flush=True)
        return _etat_leds()                    # renvoie l'ÉTAT RÉEL au MCU
```

Ce qu'il faut retenir :

- `bouton()` **exécute** les commandes si la logique métier l'autorise.
- `ok` et `info` ne servent **qu'au journal** (`print`). Ils ne sont **jamais envoyés au MCU**.
- `bouton()` **ne construit pas** l'état : elle **relaie** la valeur de retour de `_etat_leds()`.

### 7.4 Publication des fonctions

```python
Bridge.provide("update_gps", update_gps)
Bridge.provide("bouton", bouton)
Bridge.provide("etat_leds", etat_leds)
App.run()
```

---

## 8. Pourquoi deux fonctions, `etat_leds()` et `_etat_leds()` ?

Les deux chemins ont besoin du même calcul, mais dans des situations différentes :

| Appelant | Tient déjà `_lock_commandes` ? | Fonction utilisée |
|---|---|---|
| MCU, synchronisation périodique | Non, il faut le prendre | `etat_leds()` |
| Fin de `bouton()` | **Oui**, déjà pris | `_etat_leds()` |

Si `bouton()` appelait `etat_leds()`, elle essaierait de **reprendre un verrou qu'elle tient déjà**.
Avec un `threading.Lock`, cela provoque un **interblocage (deadlock)** : le thread s'attend lui-même indéfiniment, et le MCU ne reçoit jamais de réponse.

D'où le schéma classique :

- **`etat_leds()`** : point d'entrée public, qui prend le verrou ;
- **`_etat_leds()`** : calcul interne, qui suppose le verrou déjà pris.

Avantage : l'état est calculé à **un seul endroit**, donc les deux chemins renvoient forcément le même format.

---

## 9. Le code MCU expliqué

### 9.1 Réception de la valeur : `result(etat)`

```cpp
int etat = 0;
if (Bridge.call("etat_leds").result(etat)) afficherLeds(etat);
else afficherLeds(0);
```

Attention, deux choses distinctes :

- **`etat`** est passé **par référence** : `result()` y **écrit** la valeur entière renvoyée par le MPU.
- **La valeur renvoyée par `result()`** est un **booléen** :
  - `true` : le résultat de l’appel RPC a été récupéré avec succès ;
  - `false` : échec de récupération du résultat (par exemple, absence de réponse). Dans ce cas, on éteint tout.

> Le MPU ne connaît pas la variable `etat` du MCU. Il renvoie simplement une **valeur** (0 à 7).
> C'est le MCU qui range cette valeur dans **sa** variable locale `etat`.

> ⚠️ `result()` renvoie `true` **même si la commande a été refusée** : il indique que **l'échange** a réussi, pas que la commande a été acceptée.

### 9.2 Décodage : `afficherLeds(etat)`

```cpp
void afficherLeds(int etat) {
  if (!boutonsOK || etat == dernierEtat) return;   // rien à faire si inchangé
  buttons.setLeds((etat & 1) != 0,                  // bit 0 → LED A
                  (etat & 2) != 0,                  // bit 1 → LED B
                  (etat & 4) != 0);                 // bit 2 → LED C
  dernierEtat = etat;
}
```

- Chaque `etat & n` isole un bit.
- `dernierEtat` évite de réécrire sur le bus I2C quand rien n'a changé.

### 9.3 Envoi d'une commande : `lireBoutons()`

```cpp
bool appuye = buttons.isPressed(i);
bool front = appuye && !etatPrecedent[i];   // vrai uniquement au moment de l'appui
etatPrecedent[i] = appuye;
if (front) {
  int etat = 0;
  if (Bridge.call("bouton", commandes[i]).result(etat)) afficherLeds(etat);
  else { afficherLeds(0); Monitor.println("Pas de réponse de main.py"); }
  derniereSynchroLeds = millis();           // LEDs fraîches : on repousse la synchro
}
```

- La **détection de front** garantit qu'un appui maintenu n'envoie **qu'une seule** commande.

---

## 10. Le trajet complet de la valeur

```
MCU  Bridge.call("etat_leds")
        │
        ▼
MPU  etat_leds()          ← prend _lock_commandes
        │
        ▼
MPU  _etat_leds()         ← prend _lock, construit le masque
        │  return 0..7
        ▼
     réponse transmise par le Bridge
        │
        ▼
MCU  result(etat)         ← écrit la valeur dans etat, renvoie true
        │
        ▼
MCU  afficherLeds(etat)   ← décode les bits, allume les LEDs
```

Pour un appui sur un bouton, seul le début change : `Bridge.call("bouton", ...)` → `bouton()` → `_etat_leds()` → … la suite est identique.

---

## 11. Questions fréquentes

**Comment le MCU sait-il qu'une commande a été refusée ?**
Il ne peut pas le déterminer directement : il reçoit un masque représentant l’état du système, et non un indicateur d’acceptation de la commande. Le motif du refus figure dans le journal du MPU (`print`).

**Pourquoi le MCU ne prend-il pas les décisions métier ?**
Pour avoir **une seule source de vérité**. Toutes les règles (« pas de START si HTTP tourne », etc.) sont au même endroit, côté MPU.

**Pourquoi interroger le MPU toutes les 2 s ?**
Parce que l’état peut changer sans appui (acquisition ou perte du fix GPS) et que, dans Car_Traces, le MPU ne transmet pas spontanément ces changements au MCU. La synchronisation permet aussi de rétablir l’affichage après un échec de communication.

**Que se passe-t-il si `main.py` ne tourne pas ?**
`result()` renvoie `false` en cas d’échec de communication et le MCU éteint toutes les LEDs. Le message « Pas de réponse de main.py » est affiché lors d’un appel déclenché par un bouton, mais pas lors de la synchronisation périodique.

---

## 12. Vocabulaire

| Terme | Signification ici |
|---|---|
| **RPC** (appel de procédure à distance) | Le MCU demande l’exécution d’une fonction sur le MPU |
| **Publier / exposer** | `Bridge.provide` rend une fonction Python appelable par le MCU |
| **Appel synchrone** | Le MCU attend la réponse ou un échec de communication avant de continuer |
| **Logique métier** | Les règles de fonctionnement de l'application (ce qui est autorisé ou non) |
| **Masque de bits** | Un entier où chaque bit représente une information vrai/faux |
| **Valeur de retour** | Ce que renvoie le `return` d'une fonction |
| **Passage par référence** | `result(etat)` écrit directement dans la variable `etat` du MCU |
| **Verrou (lock)** | Sérialise les accès aux données ou opérations protégées par le même verrou |
| **Interblocage (deadlock)** | Dans le cas présenté, un thread attend un verrou non réentrant qu’il détient déjà |
| **Front montant** | Le passage de « relâché » à « appuyé », détecté une seule fois par appui |
| **Polling** | Interroger régulièrement pour connaître un état |