# TI-Adresssuche (KIM-Verzeichnis)

Kleine Desktop-Anwendung für Praxen mit eigener TI-Anbindung: gezielte Suche
von KIM-Adressen im **TI-Verzeichnisdienst (VZD)** — Name, Ort, PLZ, Fachrichtung
(Zahnarztpraxis, KFO, MKG, Arzt-Fachgruppen, Psychotherapie, Krankenhaus, Apotheke),
Trefferliste mit KIM-Adresse(n), Detailansicht mit **allen** Verzeichnis-Attributen
(einschließlich telematikID, kimData, Eintragstyp), KIM-Adresse per Knopfdruck in
die Zwischenablage.

Zwei Varianten, gleiche Bedienung:

| Variante | Pfad | Abhängigkeiten |
|---|---|---|
| **Windows** | `windows/ti_adresssuche.py` | nur Python 3.8+ (tkinter/ssl/socket — reine Standardbibliothek) |
| **Linux (Mint/Flatpak)** | `linux/ti_adresssuche.py` | `ldapsearch` aus einem Flathub-Flatpak (z. B. Claws Mail), tkinter |

Entwickelt von einem **KI-Agenten** (Qwen Code, GLM-Modell), Lizenz **AGPL-3.0** (siehe LICENSE).

## Warum dieses Werkzeug?

Die Praxis nutzt **Claws Mail als alternativen E-Mail-Client für KIM** — vom
KI-Agenten für das **Senden und Empfangen** eingerichtet, und dafür
funktioniert Claws Mail sehr zuverlässig. Für die Verzeichnis-Suche ist
Claws Mail aber nur bedingt geeignet: Die Linux-Variante bringt nur eine
schwache LDAP-Suche mit, die Windows-Variante ist für das TI-LDAP (mit
Client-Zertifikats-Authentifizierung über den Konnektor) ungeeignet. Auch
die LDAP-Suchen, die Praxisverwaltungs-Systeme (PVS) eingebaut haben, sind
vermutlich häufig zu spartanisch — ohne Fachrichtungs- und Facetten-Filter
und ohne echte Detailansicht.

Diese Anwendung schließt die Lücke: eigenständige Suche mit
Fachrichtungs-Filters, Facetten (KFO, MKG, …), vollständiger
Detailansicht jedes Eintrags und direkter KIM-Adress-Übernahme.

---

## Zugangsmodell — bitte vor der ersten Nutzung lesen

Die Anwendung ist ein normaler LDAP-Client für eine **von gematik öffentlich
dokumentierte Schnittstelle**: Der (virtuelle) Konnektor stellt den
TI-Verzeichnisdienst per **I_Directory_Query** über LDAPS bereit
(gemSpec_KON / gemSpec_VZD / api-vzd-Dokumentation der gematik). Keine
Umgehung, keine Sonderwege — die Suche ist der dokumentierte Zweck des
Verzeichnisses.

Drei Dinge werden vorausgesetzt und **nicht** mitgeliefert:

1. **Eigene TI-Anbindung** — eigener Konnektor bzw. eigenes TI-Gateway
   (virtueller Konnektor) mit Zugang aus dem Praxisnetz. Wer KIM nutzt,
   hat das bereits.
2. **Eigenes TLS-Client-Zertifikat** — das Konlan-Zertifikat **Ihres eigenen
   Clientsystems**. In diesem Repository liegen bewußt **keine Zertifikate,
   Schlüssel und Passwörter**. Geben Sie Ihre Zertifikate niemals weiter.
3. **Netz-Zugang zum Konnektor** (Port 636/LDAPS) aus dem Praxisnetz bzw.
   über den eigenen TI-Tunnel.

Die Verzeichnisdaten sind öffentliche berufliche Kontaktdaten; die
Anwendung speichert nichts — sie sucht und zeigt an.

---

## Zertifikat gewinnen (einmalig)

Das Client-Zertifikat liegt im Konnektor und wird aus dessen Verwaltung
exportiert (Wege am Beispiel eines RISE-basierten virtuellen Konnektors;
bei anderen Herstellern entsprechend dessen Anleitung):

1. Konnektor-Verwaltung öffnen (https://`<konnektor-ip>`:8443) und anmelden.
2. Client-Zertifikat des eigenen Clientsystems finden und als
   PKCS#12 (`.p12`) exportieren („TLS (Kopie)"). Das zugehörige Paßwort
   steht in der Konnektor-Verwaltung — **nur lokal notieren, nie teilen**.
3. Ältere Konnektor-Exporte sind mit dem veralteten Algorithmus RC2-40
   verschlüsselt; OpenSSL 3 braucht dafür den Legacy-Provider. Mit der
   beiliegenden [`openssl/legacy.cnf`](openssl/legacy.cnf):

   ```bash
   OPENSSL_CONF=../openssl/legacy.cnf openssl pkcs12 \
     -in client.p12 -out client.crt -clcerts -nokeys
   OPENSSL_CONF=../openssl/legacy.cnf openssl pkcs12 \
     -in client.p12 -out client.key -nocerts -nodes
   ```

   (Am Ende `client.p12` lokal löschen oder sicher verwahren; `client.key`
   nur lokal ablegen, keine Netzfreigabe.)

---

## Konfiguration

In beiden Varianten liegt die Verbindungsdaten-Datei `config.ini` **neben dem
Skript** — vorher aus `config.example.ini` kopieren und die eigenen Werte
eintragen:

```ini
[ldap]
host = <Adresse Ihres Konnektors>
port = 636
base_dn = dc=data,dc=vzd
cert = <Pfad zu client.crt>
key  = <Pfad zu client.key>
limit = 60
```

- **Windows:** `%APPDATA%`-Umgebungsvariablen in den Pfaden sind erlaubt.
- **Linux:** `url = ldaps://…` (Praxis-Netzadresse oder eigener Tunnel, z. B.
  SSH-LocalForward auf `127.0.0.1:1636`). **Wichtig:** Die Zertifikats-Pfade
  müssen innerhalb des Flatpak-Sandkastens lesbar sein — entweder unter
  `~/.local/…` ablegen (Flathub-Standard-Override) oder einen Dateisystem-
  Override für den Zertifikats-Ordner setzen.

---

## Start

- **Windows:** Python 3.8+ installiert → Doppelklick auf
  `ti_adresssuche.py` (oder Verknüpfung auf `pythonw.exe` mit dem Skript als
  Argument — dann ohne Konsolenfenster). Fehler in der Konfiguration werden
  in einem Meldungsfenster angezeigt.
- **Linux:** `python3 ti_adresssuche.py` oder Desktop-Starter anlegen.
  Headless-Test ohne GUI:
  `python3 ti_adresssuche.py --test-name <Nachname>` bzw.
  `--test-code "Zahnarztpraxis" --test-plz <PLZ>` (unter Windows zusätzlich
  `--demo-logies` / `--demo-osnabrueck`).

## Bedienung

- Suchfelder: Name, Ort, PLZ, Straße, Bundesland, Organisation — **echte
  Teilstring-Suche** („Logies" findet auch „Michael Logies"), Kombination
  der Felder per UND.
- **Fachrichtung** (professionOID gemäß gemSpec_VZD) und Zusatz-Facetten
  (KFO = KIEF, MKG = MKGC) als weitere Filter; „Alle Fachrichtungen" für
  branchenübergreifende Suche (nicht jeder Eintrag trägt eine Facette ein).
- **Doppelklick** auf einen Treffer → Detail-Fenster mit allen Verzeichnis-
  Attributen; Binärzertifikate erscheinen als Platzhalter.
- „KIM-Adresse(n) kopieren" setzt die Adresse(n) des markierten Eintrags
  in die Zwischenablage.

## Fehlerbilder

| Symptom | Übliche Ursache |
|---|---|
| `LDAP error (connect): 81` | Kein/gültiges Client-Zertifikat übergeben oder falscher Port (636 = LDAPS Pflicht) |
| Fehler 52 „nicht verfügbar" | Verbindungsanfrage nach abgebrochenem TLS-Handshake — folgt auf Fehler 81 |
| Timeout / „Verbindung fehlgeschlagen" | Konnektor aus dem Netz nicht erreichbar (Tunnel/Praxisnetz prüfen) |
| 0 Treffer trotz richtiger Schreibweise | Fachrichtung auf „Alle" stellen; weniger Felder kombinieren; manche Personeneinträge tragen keine Facette |

Hinweis: Bei schmalen Kombinationen (z. B. exakter Name + volle PLZ) kann
der Verzeichnisdienst gelegentlich 0 liefern, während weitere Kombinationen
Treffer zeigen — serverseitiges Verhalten, nicht ein Fehler der Anwendung.

## Technik-Notizen

- Die Windows-Variante spricht LDAP mit einem kleinen eigenen BER-Encoder/
  -Decoder (Standardbibliothek) — bewußt ohne externe Pakete; ldap3/bundled
  OpenLDAP-Bindings sind gegen Konnektor-Antworten anfällig.
- Die Suche nutzt Substring-Filter mit `any`-Komponente („*Begriff*"); die
  Kombination `initial`+`any` in einem Filter liefert am Verzeichnisdienst 0
  und wird vermieden.
- Die erste Suchantwort wird mitgezählt (häufige Quellen „off-by-one"
  verlorener Treffer bei Hand-Routen); sizeLimit/Timeouts kommen aus der
  `config.ini`.

## Lizenz, Herkunft und Rückmeldung

**Herkunft:** Diese Anwendung wurde von einem **KI-Agenten** entwickelt
(Qwen Code, GLM-Modell) — nicht von einer natürlichen Person. Dem
entsprechend erhebt diese Datei keinen Copyright-Anspruch für Menschen;
die Software steht unter der **GNU Affero General Public License v3.0**
(siehe [LICENSE](LICENSE)), wird ohne Gewähr bereitgestellt und ist im
Rahmen der eigenen TI-Anbindung und der geltenden gematik-Regelungen zu
nutzen.

**Empfohlener Installationsweg:** Beauftragen Sie Ihren KI-Agenten mit der
Einrichtung — die Schritte in diesem README (Python prüfen, Zertifikat
gewinnen, `config.ini` aus `config.example.ini` erzeugen, Desktop-Starter
anlegen) sind vollständig automatisierbar, und ein KI-Agent kann sie
direkt umsetzen. Ein Beispiel-Auftrag:

> „Installiere die TI-Adresssuche aus diesem Repository gemäß README auf
> diesem Rechner. Zertifikat liegt unter `<pfad>`, Konnektor unter
> `<adresse>:636`. Lege `config.ini` und einen Desktop-Starter an und
> teste die Suche mit einer Beispielabfrage."

**Rückmeldung bei Problemen:** Bitte Probleme **direkt als GitHub-Issue in
diesem Repository** melden — kurz beschreiben: was getan wurde, was
erwartet wurde, was stattdessen passierte (ggf. mit Fehlermeldung oder
Screenshot). Ein KI-Agent betreut dieses Repository und bearbeitet die
Meldungen dort.

**Funktionswünsche:** Auch Wünsche für den weiteren Funktionsausbau sind
ausdrücklich willkommen — einfach als Issue beschreiben, was gesucht
werden können soll. Ein KI-Agent kann Umsetzungen zügig liefern.
