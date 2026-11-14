# -*- coding: utf-8 -*-
"""TI-Adresssuche — Suche im KIM-Verzeichnis (TI-Verzeichnisdienst VZD).

Linux-Variante (Mint/Flatpak): nutzt das Programm „ldapsearch" aus dem
Flathub-Paket von Claws Mail — OpenLDAP bringt die TLS-Client-
Zertifikats-Unterstützung mit; die Umgebungsvariablen LDAPTLS_* werden
dem Flatpak pro Aufruf übergeben.

Zugangsmodell (bitte README lesen):
  - eigener TI-Anschluß mit (virtuellem) Konnektor,
  - eigenes TLS-Client-Zertifikat (Konlan-Zertifikat des eigenen Clientsystems),
  - Erreichbarkeit des Konnektors (Praxisnetz oder eigener Tunnel).

Alle Verbindungsdaten stehen in config.ini (vorher aus config.example.ini
erzeugen). Diese Datei enthält KEINE Zugangsdaten — das Zertifikat bleibt
ausschließlich auf dem eigenen Rechner.

Version 1.1 (14.11.2026) — Fenstergröße/-position und Spaltenbreiten der
Trefferliste werden benutzerspezifisch gemerkt (je Konto:
~/.config/ti-adresssuche/fenster.json).
"""
import base64
import configparser
import json
import os
import re
import subprocess
import tkinter as tk
from tkinter import ttk

SKRIPTPFAD = os.path.dirname(os.path.abspath(__file__))
CONFIG_DATEI = os.path.join(SKRIPTPFAD, "config.ini")

# --- Konfiguration laden (config.ini neben dem Skript) --------------------
try:
    _cp = configparser.ConfigParser()
    if not _cp.read(CONFIG_DATEI, encoding="utf-8"):
        raise RuntimeError(
            "config.ini nicht gefunden (erwartet: %s). Bitte aus "
            "config.example.ini erzeugen und die eigenen Werte eintragen."
            % CONFIG_DATEI)
    if "ldap" not in _cp:
        raise RuntimeError("config.ini enthält den Abschnitt [ldap] nicht.")
    _abschnitt = _cp["ldap"]
    URL = _abschnitt.get("url", "").strip()
    BASE_DN = _abschnitt.get("base_dn", "dc=data,dc=vzd").strip()
    CERT = os.path.expanduser(_abschnitt.get("cert", "").strip())
    KEY = os.path.expanduser(_abschnitt.get("key", "").strip())
    LIMIT = _abschnitt.getint("limit", fallback=60)
    FLATPAK = _cp.get("ldapsearch", "flatpak", fallback="/usr/bin/flatpak")
    APP = _cp.get("ldapsearch", "app", fallback="org.claws_mail.Claws-Mail")
    if not URL or not CERT or not KEY:
        raise RuntimeError("config.ini: 'url', 'cert' und 'key' sind Pflichtfelder.")
    CONFIG_FEHLER = ""
except Exception as fehler:
    CONFIG_FEHLER = str(fehler)

FACPREF = "urn:psc:1.3.6.1.4.1.19376.3.276.1.5.4:"

# (Anzeige, professionOIDs [Mehrfach = ODER], specialization-Facetten)
# Offizielle Liste: gemSpec_VZD, Tabelle Eintragstyp ↔ professionOID.
FACHRICHTUNGEN = [
    ("Alle Fachrichtungen", [], []),
    ("Zahnarztpraxis", ["1.2.276.0.76.4.51"], []),
    ("Kieferorthopädische Praxis", ["1.2.276.0.76.4.51"], ["KIEF"]),
    ("Kiefer-/MKG-/Oralchirurgen (Zahn + Arzt, MKGC)", ["1.2.276.0.76.4.51", "1.2.276.0.76.4.50"], ["MKGC"]),
    ("Betriebsstätte Arzt", ["1.2.276.0.76.4.50"], []),
    ("Arzt — Allgemeinmedizin", ["1.2.276.0.76.4.50"], ["ALLG"]),
    ("Arzt — Innere Medizin", ["1.2.276.0.76.4.50"], ["INNE"]),
    ("Arzt — Kardiologie", ["1.2.276.0.76.4.50"], ["KARD"]),
    ("Betriebsstätte Psychotherapeut", ["1.2.276.0.76.4.52"], []),
    ("Krankenhaus", ["1.2.276.0.76.4.53"], []),
    ("Öffentliche Apotheke", ["1.2.276.0.76.4.54"], []),
    ("Eigene OID eingeben", [], []),
]

FELDER = [
    ("Name", "displayName"),
    ("Ort", "localityName"),
    ("PLZ", "postalCode"),
    ("Straße", "streetAddress"),
    ("Bundesland", "stateOrProvinceName"),
    ("Organisation", "organization"),
]

# Bei der All-Attribute-Abfrage antwortet der VZD mit den kanonischen
# Attributnamen (l, street, st, o) statt der Alias-Namen — normalisieren,
# damit die Anzeige-Spalten wieder greifen.
ATTR_ALIASES = {"l": "localityName", "street": "streetAddress",
                "st": "stateOrProvinceName", "o": "organization"}


def baue_filter(werte, oid_liste, facetten):
    teile = []
    for key, attr in FELDER:
        roh = (werte.get(key) or "").strip()
        if not roh:
            continue
        roh = roh.replace("\\", "").replace("*", "").replace("(", "").replace(")", "")
        # *roh* = SubstringFilter mit any-Komponente — echte Teilstring-Suche
        teile.append("(" + attr + "=*" + roh + "*)")
    if oid_liste:
        oids = ["(professionOID=%s)" % o for o in oid_liste]
        teile.append(oids[0] if len(oids) == 1 else "(|" + "".join(oids) + ")")
    if facetten:
        facs = ["(specialization=%s%s)" % (FAC_PREFIX, f) for f in facetten]
        teile.append(facs[0] if len(facs) == 1 else "(|" + "".join(facs) + ")")
    if len(teile) > 1:
        return "(&" + "".join(teile) + ")"
    return teile[0] if teile else "(displayName=*)"


def baue_befehl(filterstr):
    return (FLATPAK + " run --command=ldapsearch "
            "--env=LDAPTLS_REQCERT=never "
            "--env=LDAPTLS_CERT=" + CERT + " "
            "--env=LDAPTLS_KEY=" + KEY + " "
            + APP + " -x -H " + URL + " -b " + BASE_DN + " "
            + repr(filterstr))


def suche(filterstr):
    try:
        p = subprocess.run(baue_befehl(filterstr),
                           shell=True, capture_output=True, text=True, timeout=90)
        return p.stdout
    except Exception as e:
        return "FEHLER: %s" % e


def _lesbar(s):
    for c in s:
        o = ord(c)
        if (o < 32 and c not in "\t\n\r") or o == 0xFFFD:
            return False
    return True


def _dn_wert(zeile):
    rest = zeile[3:]
    if zeile.startswith("dn::"):
        try:
            return base64.b64decode(rest.strip()).decode("utf-8")
        except Exception:
            return rest.strip()
    return rest.strip()


def parse_ldif(ldif):
    """LDIF → Liste von Einträgen {"dn": str, "attrs": [(name, [werte])]}.

    RFC-2849-Unfolding (Fortsetzungszeilen mit führendem Leerzeichen werden
    angehängt), Base64-Werte (::) werden dekodiert, unlesbare (Binär-)Werte
    landen als Platzhalter."""
    zeilen = []
    for roh in ldif.splitlines():
        if roh.startswith(" ") and zeilen:
            zeilen[-1] += roh[1:]
        else:
            zeilen.append(roh)
    eintraege, aktueller = [], None
    for zeile in zeilen:
        if not zeile or zeile.startswith("#"):
            continue
        low = zeile.lower()
        if low.startswith(("search:", "result:", "numentries:", "totalentries:")):
            continue
        if low.startswith("dn:"):
            if aktueller:
                eintraege.append(aktueller)
            aktueller = {"dn": _dn_wert(zeile), "attrs": []}
            continue
        if aktueller is None or ":" not in zeile:
            continue
        name, rest = zeile.split(":", 1)
        name = name.strip()
        name = ATTR_ALIASES.get(name.lower(), name)
        if rest.startswith(":"):
            try:
                val = base64.b64decode(rest[1:].strip()).decode("utf-8")
            except Exception:
                val = None
        else:
            val = rest.strip()
        if val is None or not _lesbar(val):
            val = "<Binärdaten>"
        for vorhanden in aktueller["attrs"]:
            if vorhanden[0] == name:
                vorhanden[1].append(val)
                break
        else:
            aktueller["attrs"].append((name, [val]))
    if aktueller:
        eintraege.append(aktueller)
    return eintraege


def hole(e, name, default=""):
    for n, vals in e["attrs"]:
        if n.lower() == name.lower():
            return vals[0] if vals else default
    return default


def mails_von(e):
    for n, vals in e["attrs"]:
        if n.lower() == "mail":
            return list(vals)
    return []


# ---------- Fensterzustand (benutzerspezifisch) -----------------------------
# Fenstergröße/-position und Spaltenbreiten der Trefferliste werden je
# Benutzerkonto gemerkt (Linux: ~/.config/ti-adresssuche/fenster.json,
# Windows: %APPDATA%\TI-Adresssuche\fenster.json). Die Installation selbst
# darf geteilt liegen — der Zustand ist bewusst kontenlokal.

SPALTEN_BREITEN_DEFAULT = {"name": 180, "org": 160, "ort": 90, "plz": 50,
                           "str": 140, "mail": 240}


def _zustand_datei():
    if os.name == "nt":
        basis = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(basis, "TI-Adresssuche", "fenster.json")
    basis = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(basis, "ti-adresssuche", "fenster.json")


def lade_fensterzustand():
    try:
        with open(_zustand_datei(), "r", encoding="utf-8") as f:
            daten = json.load(f)
        return daten if isinstance(daten, dict) else None
    except Exception:
        return None


def speichere_fensterzustand(daten):
    try:
        pfad = _zustand_datei()
        os.makedirs(os.path.dirname(pfad), exist_ok=True)
        tmp = pfad + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(daten, f)
        os.replace(tmp, pfad)
    except Exception:
        pass  # Zustand ist Komfort — niemals die Anwendung blockieren


class Suche(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("TI-Adresssuche (KIM-Verzeichnis)")
        self.geometry("980x560")
        kopf = ttk.Frame(self); kopf.pack(fill="x", padx=8, pady=6)
        self.felder = {}
        spalte = 0
        for label, _ in FELDER:
            l = ttk.Label(kopf, text=label + ":")
            l.grid(row=spalte // 3, column=(spalte % 3) * 2, sticky="e", padx=4)
            e = ttk.Entry(kopf, width=22)
            e.grid(row=spalte // 3, column=(spalte % 3) * 2 + 1, sticky="w", padx=2)
            self.felder[label] = e
            spalte += 1
        lf = ttk.Label(kopf, text="Fachrichtung:"); lf.grid(row=2, column=0, sticky="e", padx=4)
        self.fach = ttk.Combobox(kopf, width=28, state="readonly",
                                 values=[f[0] for f in FACHRICHTUNGEN])
        self.fach.current(0); self.fach.grid(row=2, column=1, sticky="w", padx=2)
        lo = ttk.Label(kopf, text="OID:"); lo.grid(row=2, column=2, sticky="e", padx=4)
        self.oid = ttk.Entry(kopf, width=28); self.oid.grid(row=2, column=3, sticky="w", padx=2)
        kn = ttk.Button(kopf, text="Suchen", command=self.suche)
        kn.grid(row=2, column=4, padx=8)
        self.status = ttk.Label(kopf, text=""); self.status.grid(row=2, column=5)
        spalten = ("name", "org", "ort", "plz", "str", "mail")
        kopftext = ("Name", "Organisation", "Ort", "PLZ", "Straße", "KIM-Adresse(n)")
        self.baum = ttk.Treeview(self, columns=spalten, show="headings")
        for s, k in zip(spalten, kopftext):
            self.baum.heading(s, text=k)
        for s, w in SPALTEN_BREITEN_DEFAULT.items():
            self.baum.column(s, width=w)
        zustand = lade_fensterzustand()
        if isinstance(zustand, dict):
            geo = zustand.get("fenster")
            if isinstance(geo, str) and re.match(r"^\d+x\d+[+-]\d+[+-]\d+$", geo):
                self.geometry(geo)
            breiten = zustand.get("spalten")
            if isinstance(breiten, dict):
                for s in spalten:
                    w = breiten.get(s)
                    if isinstance(w, int) and w > 20:
                        self.baum.column(s, width=w)
        self.baum.pack(fill="both", expand=True, padx=8, pady=4)
        fuss = ttk.Frame(self); fuss.pack(fill="x", padx=8, pady=4)
        ttk.Button(fuss, text="KIM-Adresse(n) des Eintrags kopieren",
                   command=self.kopiere).pack(side="left")
        self.info = ttk.Label(fuss, text="Doppelklick auf einen Treffer zeigt alle LDAP-Infos des Eintrags")
        self.info.pack(side="left", padx=10)
        self.baum.bind("<Double-1>", self.detail)
        self.protocol("WM_DELETE_WINDOW", self._beende)
        self.treffer = []
        if CONFIG_FEHLER:
            self.status.config(text="Konfigurationsfehler — siehe Konsole/README")
            print("FEHLER:", CONFIG_FEHLER)

    def _beende(self):
        try:
            speichere_fensterzustand({
                "fenster": self.geometry(),
                "spalten": {s: self.baum.column(s, "width")
                            for s in SPALTEN_BREITEN_DEFAULT},
            })
        except Exception:
            pass
        self.destroy()

    def suche(self):
        name, wert = self.fach.get(), self.oid.get().strip()
        oid_liste, facetten = [], []
        if name == "Eigene OID eingeben" and wert:
            oid_liste = [wert.strip()]
        else:
            for anzeige, oids, facs in FACHRICHTUNGEN:
                if anzeige == name:
                    oid_liste, facetten = oids, facs
                    break
        f = baue_filter({k: v.get() for k, v in self.felder.items()}, oid_liste, facetten)
        self.status.config(text="Suche läuft …")
        self.update_idletasks()
        out = suche(f)
        eintraege = parse_ldif(out)
        self.baum.delete(*self.baum.get_children())
        self.treffer = []
        for e in eintraege:
            zeile = (hole(e, "displayName") or hole(e, "cn"), hole(e, "organization"),
                     hole(e, "localityName"), hole(e, "postalCode"),
                     hole(e, "streetAddress"), "; ".join(mails_von(e)))
            self.baum.insert("", "end", values=zeile)
            self.treffer.append(e)
        if eintraege:
            self.status.config(text="%d Treffer" % len(eintraege))
        else:
            self.status.config(text="0 Treffer — ggf. Fachrichtung auf ‚Alle Fachrichtungen'"
                                    " stellen oder weniger Felder kombinieren")

    def kopiere(self):
        auswahl = self.baum.selection()
        if not auswahl or not self.treffer:
            return
        idx = self.baum.index(auswahl[0])
        mails = mails_von(self.treffer[idx])
        if not mails:
            self.status.config(text="Kein mail-Attribut am Eintrag — Details per Doppelklick")
            return
        self.clipboard_clear()
        self.clipboard_append("; ".join(mails))
        self.status.config(text="Kopiert: " + "; ".join(mails))

    def detail(self, event=None):
        auswahl = self.baum.selection()
        if not auswahl or not self.treffer:
            return
        e = self.treffer[self.baum.index(auswahl[0])]
        fenster = tk.Toplevel(self)
        fenster.title("Detail — " + (hole(e, "displayName") or hole(e, "cn") or "Eintrag"))
        fenster.geometry("760x540")
        txt = tk.Text(fenster, wrap="none")
        sbv = ttk.Scrollbar(fenster, orient="vertical", command=txt.yview)
        sbh = ttk.Scrollbar(fenster, orient="horizontal", command=txt.xview)
        txt.configure(yscrollcommand=sbv.set, xscrollcommand=sbh.set)
        sbv.pack(side="right", fill="y")
        sbh.pack(side="bottom", fill="x")
        txt.pack(fill="both", expand=True)
        txt.insert("end", "dn: %s\n" % e.get("dn", ""))
        for name, vals in sorted(e["attrs"]):
            for v in vals:
                txt.insert("end", "%s: %s\n" % (name, v))
        txt.configure(state="disabled")
        self.status.config(text="Detail geöffnet")


if __name__ == "__main__":
    Suche().mainloop()
