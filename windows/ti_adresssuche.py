# -*- coding: utf-8 -*-
"""TI-Adresssuche — Suche im KIM-Verzeichnis (TI-Verzeichnisdienst VZD).

Windows-Variante: reine Python-Standardbibliothek (tkinter/ssl/socket) mit
eigenem Mini-BER-LDAP-Client — keine Paketinstallation nötig.

Zugangsmodell (bitte README lesen):
  - eigener TI-Anschluß mit (virtuellem) Konnektor,
  - eigenes TLS-Client-Zertifikat (Konlan-Zertifikat des eigenen Clientsystems),
  - Verbindung zum Konnektor auf Port 636 (LDAPS) aus dem Praxisnetz/Tunnel.

Alle Verbindungsdaten stehen in config.ini (vorher aus config.example.ini
erzeugen). Diese Datei enthält KEINE Zugangsdaten — das Zertifikat bleibt
ausschließlich auf dem eigenen Rechner.

Suchlogik: echte Teilstring-Suche (LDAP SubstringFilter any-Komponente).
Der VZD wertet any= voll aus (auch Umlaute); die Mischung initial+any in
einem Filter liefert 0 und wird daher nicht verwendet.

Version 1.0 (14.11.2026)
"""
import configparser
import os
import ssl
import socket
import sys
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
    HOST = os.path.expandvars(_abschnitt.get("host", "")).strip()
    PORT = _abschnitt.getint("port", fallback=636)
    BASE_DN = _abschnitt.get("base_dn", "dc=data,dc=vzd").strip()
    CERT = os.path.expandvars(_abschnitt.get("cert", "")).strip()
    KEY = os.path.expandvars(_abschnitt.get("key", "")).strip()
    LIMIT = _abschnitt.getint("limit", fallback=60)
    if not HOST or not CERT or not KEY:
        raise RuntimeError("config.ini: 'host', 'cert' und 'key' sind Pflichtfelder.")
    for pfad in (CERT, KEY):
        if not os.path.isfile(pfad):
            raise RuntimeError("config.ini: Datei nicht gefunden: " + pfad)
    CONFIG_FEHLER = ""
except Exception as fehler:  # freundliche Meldung statt Traceback
    CONFIG_FEHLER = str(fehler)

FACPREF = "urn:psc:1.3.6.1.4.1.19376.3.276.1.5.4:"

# (Anzeige, professionOIDs [Mehrfach = ODER], specialization-Facetten)
# Offizielle Liste: gemSpec_VZD, Tabelle Eintragstyp ↔ professionOID
# (4.50 Betriebsstätte Arzt, 4.51 Zahnarztpraxis, 4.52 BS Psychotherapeut,
#  4.53 Krankenhaus, 4.54 Öffentliche Apotheke).
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

FELDER = [("Name", "displayName"), ("Ort", "localityName"), ("PLZ", "postalCode"),
          ("Straße", "streetAddress"), ("Bundesland", "stateOrProvinceName"),
          ("Organisation", "organization")]

# Bei der All-Attribute-Abfrage antwortet der VZD mit den kanonischen
# Attributnamen (l, street, st, o) statt der Alias-Namen — normalisieren,
# damit die Anzeige-Spalten wieder greifen.
ATTR_ALIASES = {"l": "localityName", "street": "streetAddress",
                "st": "stateOrProvinceName", "o": "organization"}


# ---------- Mini-BER-Encode ----------

def ber_len(n):
    if n < 0x80:
        return bytes([n])
    aus = b""
    while n:
        aus = bytes([n & 0xFF]) + aus
        n >>= 8
    return bytes([0x80 | len(aus)]) + aus


def ber(tag, payload):
    return bytes([tag]) + ber_len(len(payload)) + payload


def ber_int(v):
    return ber(0x02, v.to_bytes(max(1, (v.bit_length() + 7) // 8), "big"))


def ber_oct(s):
    return ber(0x04, s.encode("utf-8"))


def f_eq(attr, val):
    return ber(0xA3, ber_oct(attr) + ber(0x04, val.encode("utf-8")))


def f_sub(attr, teil):
    # any= = echte Teilstring-Suche („Logies" findet auch „Michael Logies")
    return ber(0xA4, ber_oct(attr) + ber(0x30, ber(0x81, teil.encode("utf-8"))))


def f_or(teile):
    return ber(0xA1, b"".join(teile)) if len(teile) > 1 else teile[0]


def f_and(teile):
    return ber(0xA0, b"".join(teile)) if len(teile) > 1 else teile[0]


def baue_filter_ber(werte, oid_liste, facetten):
    teile = []
    for key, attr in FELDER:
        roh = (werte.get(key) or "").strip()
        if not roh:
            continue
        roh = roh.replace("\\", "").replace("*", "").replace("(", "").replace(")", "")
        teile.append(f_sub(attr, roh))
    if oid_liste:
        teile.append(f_or([f_eq("professionOID", o) for o in oid_liste]))
    if facetten:
        teile.append(f_or([f_eq("specialization", FACPREF + f) for f in facetten]))
    return f_and(teile)


def build_search_msg(msg_id, filter_ber, limit):
    # Leere Attributliste = ALLE Benutzerattribute (für die Detailansicht).
    # timeLimit 0 (unbegrenzt) wie beim Standard-ldapsearch; sizeLimit = limit.
    body = ber_oct(BASE_DN) + b"\x02\x01\x02" + b"\x02\x01\x00" + ber_int(limit) + \
        b"\x02\x01\x00" + b"\x01\x01\x00" + filter_ber + \
        ber(0x30, b"")
    return ber(0x30, ber_int(msg_id) + ber(0x63, body))


def build_bind_msg(msg_id):
    return ber(0x30, ber_int(msg_id) + ber(0x60, b"\x02\x01\x03" + b"\x04\x00" + b"\x80\x00"))


# ---------- TLV-Hilfen (Lese-Seite) ----------

def tlv_at(data, pos):
    """(tag, wert_start, wert_ende, nächstes_TLV) einer TLV an pos."""
    tag = data[pos]
    pos += 1
    ln = data[pos]
    pos += 1
    if ln & 0x80:
        nbytes = ln & 0x7F
        ln = int.from_bytes(data[pos:pos + nbytes], "big")
        pos += nbytes
    return tag, pos, pos + ln, pos + ln


def _recv_meldung(sock, buf):
    """Liest EINE komplette LDAPMessage; gibt (meldung, rest_buffer) zurück."""
    while True:
        try:
            end = tlv_at(buf, 0)[3]
            if len(buf) >= end:
                break
        except IndexError:
            pass
        chunk = sock.recv(65536)
        if not chunk:
            raise ConnectionError("Server schloß die Verbindung")
        buf += chunk
    end = tlv_at(buf, 0)[3]
    return buf[0:end], buf[end:]


# ---------- Verbindung ----------

class LdapVerbindung:
    def __init__(self):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ctx.load_cert_chain(CERT, KEY)
        raw = socket.create_connection((HOST, PORT), timeout=30)
        self.sock = ctx.wrap_socket(raw, server_hostname=HOST)
        self.buf = b""
        self.msg_id = 0

    def _send_and_read(self, msg):
        self.sock.send(msg)
        meld, self.buf = _recv_meldung(self.sock, self.buf)
        return meld

    def bind(self):
        self.msg_id += 1
        meld = self._send_and_read(build_bind_msg(self.msg_id))
        idx = meld.find(b"\x0a\x01")
        code = meld[idx + 2] if idx != -1 else -1
        if code != 0:
            raise ConnectionError("Bind resultCode %s" % code)

    def suche(self, filter_ber, limit=None):
        limit = limit or LIMIT
        self.msg_id += 1
        self.sock.settimeout(8)
        meld = self._send_and_read(build_search_msg(self.msg_id, filter_ber, limit))
        # Bind-Response ist in bind() schon konsumiert — die erste Antwort hier
        # ist bereits ein Suchtreffer und bleibt im Puffer
        self.buf = meld + self.buf
        # Alles empfangen bis zur Empfangs-Stille (3 s). WICHTIG: Der
        # Verzeichnisdienst positioniert die searchResDone-Nachricht nicht
        # zuverlässig am Ende — sie kann NACH DEM ERSTEN EINTRAG kommen und
        # weitere Einträge folgen danach. Deshalb nie bei „done" aufhören,
        # sondern bei der Stille, und beim Parsen done überspringen.
        self.sock.settimeout(3)
        try:
            while True:
                chunk = self.sock.recv(65536)
                if not chunk:
                    break
                self.buf += chunk
        except (socket.timeout, TimeoutError):
            pass
        self.sock.settimeout(30)
        eintraege = []
        pos = 0
        while pos < len(self.buf):
            try:
                # tlv_at liefert (tag, wert_start, wert_ende, nächstes_TLV)
                t_tag, in_start, _in_end, end_meldung = tlv_at(self.buf, pos)
                if end_meldung > len(self.buf):
                    break  # Chunk-Grenze: Nachricht noch unvollständig
                if t_tag == 0x30:
                    _t2, _mstart, _mend, e_id = tlv_at(self.buf, in_start)  # msgID
                    _t3, p_op, p_op_end, _eo = tlv_at(self.buf, e_id)  # protocolOp
                    if _t3 == 0x65:  # searchResDone — ÜBERSPRINGEN (nicht abbrechen!):
                        pos = end_meldung  # der Dienst legt done mitunter früh ab,
                        continue           # danach kommen weitere Einträge
                    if _t3 == 0x64:  # searchResEntry
                        op = self.buf[p_op:p_op_end]
                        # objectName überspringen, dann die Attribut-LISTE öffnen
                        _tn, _ns, _ne, end_name = tlv_at(op, 0)
                        _tl, list_start, list_end, _nextl = tlv_at(op, end_name)
                        p2i = list_start
                        attrs = []
                        while p2i < list_end:
                            _ta, a_start, a_end, a_next = tlv_at(op, p2i)
                            pa = op[a_start:a_end]  # Inhalt: type-TLV + SET-OF-Werte
                            _tt, t_start, t_end, t_next = tlv_at(pa, 0)
                            name_attr = pa[t_start:t_end].decode("utf-8")
                            name_attr = ATTR_ALIASES.get(name_attr.strip().lower(), name_attr)
                            _tv, v_start, v_end, _nextv = tlv_at(pa, t_next)
                            vals = []
                            q = v_start
                            while q < v_end:
                                _tb, vs, ve, vn = tlv_at(pa, q)
                                roh = pa[vs:ve]
                                try:
                                    vals.append(roh.decode("utf-8"))
                                except UnicodeDecodeError:
                                    vals.append("<%d Bytes Binärdaten>" % len(roh))
                                q = vn
                            attrs.append((name_attr, vals))
                            p2i = a_next
                        eintraege.append(attrs)
            except IndexError:
                break  # abgeschnittene Nachricht am Pufferende — nichts raten
            pos = end_meldung
        return eintraege


def hole(attrs, name, default=""):
    for n, vals in attrs:
        if n == name:
            return vals[0] if vals else default
    return default


def mails_von(attrs):
    for n, vals in attrs:
        if n == "mail":
            return list(vals)
    return []


# ---------- GUI ----------

def main_gui():
    if CONFIG_FEHLER:
        root = tk.Tk()
        root.withdraw()
        from tkinter import messagebox
        messagebox.showerror("TI-Adresssuche — Konfiguration", CONFIG_FEHLER)
        return
    root = tk.Tk()
    root.title("TI-Adresssuche (KIM-Verzeichnis)")
    root.geometry("1000x560")
    kopf = ttk.Frame(root)
    kopf.pack(fill="x", padx=8, pady=6)
    felder = {}
    for i, (label, _attr) in enumerate(FELDER):
        ttk.Label(kopf, text=label + ":").grid(row=i // 3, column=(i % 3) * 2, sticky="e", padx=4)
        e = ttk.Entry(kopf, width=22)
        e.grid(row=i // 3, column=(i % 3) * 2 + 1, sticky="w", padx=2)
        felder[label] = e
    ttk.Label(kopf, text="Fachrichtung:").grid(row=2, column=0, sticky="e", padx=4)
    fach = ttk.Combobox(kopf, width=32, state="readonly", values=[f[0] for f in FACHRICHTUNGEN])
    fach.current(0)
    fach.grid(row=2, column=1, sticky="w", padx=2)
    ttk.Label(kopf, text="OID:").grid(row=2, column=2, sticky="e", padx=4)
    oid = ttk.Entry(kopf, width=28)
    oid.grid(row=2, column=3, sticky="w", padx=2)
    spalten = ("name", "org", "ort", "plz", "str", "mail")
    kopftext = ("Name", "Organisation", "Ort", "PLZ", "Straße", "KIM-Adresse(n)")
    baum = ttk.Treeview(root, columns=spalten, show="headings")
    for s, k in zip(spalten, kopftext):
        baum.heading(s, text=k)
    baum.column("name", width=180); baum.column("org", width=150)
    baum.column("ort", width=90); baum.column("plz", width=50)
    baum.column("str", width=140); baum.column("mail", width=230)
    baum.pack(fill="both", expand=True, padx=8, pady=4)
    status = ttk.Label(root, text="Verbinde …")
    status.pack(fill="x", padx=8)
    treffer = []
    conn = [None]  # Behälter — jede Suche baut die Verbindung frisch auf

    def verbinde():
        if conn[0] is not None:
            try:
                conn[0].sock.close()
            except OSError:
                pass
        conn[0] = LdapVerbindung()
        conn[0].bind()

    try:
        verbinde()
        status.config(text="Verbunden (LDAPS %s:%s)" % (HOST, PORT))
    except Exception as e:
        status.config(text="Verbindung fehlgeschlagen: %s" % e)

    def do_suche():
        nonlocal treffer
        oid_liste, facetten = [], []
        name = fach.get()
        manuell = oid.get().strip()
        if name == "Eigene OID eingeben" and manuell:
            oid_liste = [manuell]
        else:
            for anzeige, oids, facs in FACHRICHTUNGEN:
                if anzeige == name:
                    oid_liste, facetten = oids, facs
                    break
        f = baue_filter_ber({k: v.get() for k, v in felder.items()}, oid_liste, facetten)
        status.config(text="Suche läuft …")
        root.update_idletasks()
        try:
            verbinde()  # frische Verbindung je Suche — tote Altverbindungen ausgeschlossen
            treffer = conn[0].suche(f)
        except Exception as e:
            status.config(text="Fehler: %s" % e)
            return
        baum.delete(*baum.get_children())
        for t in treffer:
            baum.insert("", "end", values=(hole(t, "displayName"), hole(t, "organization"),
                                           hole(t, "localityName"), hole(t, "postalCode"),
                                           hole(t, "streetAddress"), "; ".join(mails_von(t))))
        if treffer:
            status.config(text="%d Treffer" % len(treffer))
        else:
            status.config(text="0 Treffer — Fachrichtung auf ‚Alle' stellen, oder weniger Felder "
                               "kombinieren")

    ttk.Button(kopf, text="Suchen", command=do_suche).grid(row=2, column=4, padx=8)

    def kopiere():
        auswahl = baum.selection()
        if auswahl and treffer:
            idx = baum.index(auswahl[0])
            mails = mails_von(treffer[idx])
            if not mails:
                status.config(text="Kein mail-Attribut am Eintrag — Details per Doppelklick")
                return
            root.clipboard_clear()
            root.clipboard_append("; ".join(mails))
            status.config(text="Kopiert: " + "; ".join(mails))

    def detail(event=None):
        auswahl = baum.selection()
        if not auswahl or not treffer:
            return
        attrs = treffer[baum.index(auswahl[0])]
        fenster = tk.Toplevel(root)
        fenster.title("Detail — " + (hole(attrs, "displayName") or "Eintrag"))
        fenster.geometry("760x540")
        txt = tk.Text(fenster, wrap="none")
        sbv = ttk.Scrollbar(fenster, orient="vertical", command=txt.yview)
        sbh = ttk.Scrollbar(fenster, orient="horizontal", command=txt.xview)
        txt.configure(yscrollcommand=sbv.set, xscrollcommand=sbh.set)
        sbv.pack(side="right", fill="y")
        sbh.pack(side="bottom", fill="x")
        txt.pack(fill="both", expand=True)
        for name, vals in sorted(attrs):
            if vals:
                for v in vals:
                    txt.insert("end", "%s: %s\n" % (name, v))
            else:
                txt.insert("end", "%s: (ohne Wert)\n" % name)
        txt.configure(state="disabled")

    baum.bind("<Double-1>", detail)
    ttk.Button(root, text="KIM-Adresse(n) des Eintrags kopieren", command=kopiere).pack(padx=8, pady=2)
    ttk.Label(root, text="Doppelklick auf einen Treffer zeigt alle LDAP-Infos des Eintrags").pack(padx=8)
    root.mainloop()


def main_headless(argv):
    if CONFIG_FEHLER:
        print("FEHLER:", CONFIG_FEHLER)
        sys.exit(1)
    code = "Zahnarztpraxis"
    plz = ""
    name = ""
    ort = ""
    if "--demo-logies" in argv:
        name, plz = "Logies", "49134"
    if "--demo-osnabrueck" in argv:
        ort = "Osnabrück"
    if "--test-code" in argv:
        code = argv[argv.index("--test-code") + 1]
    if "--test-plz" in argv:
        plz = argv[argv.index("--test-plz") + 1]
    if "--test-name" in argv:
        name = argv[argv.index("--test-name") + 1]
    if "--test-ort" in argv:
        ort = argv[argv.index("--test-ort") + 1]
    oid_liste, facetten = [], []
    for anzeige, oids, facs in FACHRICHTUNGEN:
        if anzeige == code:
            oid_liste, facetten = oids, facs
            break
    werte = {"Name": name, "Ort": ort, "PLZ": plz, "Straße": "", "Bundesland": "", "Organisation": ""}
    f = baue_filter_ber(werte, oid_liste, facetten)
    conn = LdapVerbindung()
    conn.bind()
    roh = conn.suche(f)
    print("TREFFER:", len(roh))
    for r in roh[:5]:
        print("-", hole(r, "displayName"), "|", hole(r, "localityName"),
              hole(r, "postalCode"), "|", "; ".join(mails_von(r)))


if __name__ == "__main__":
    if any(a.startswith("--test") or a.startswith("--demo") for a in sys.argv):
        main_headless(sys.argv[1:])
    else:
        main_gui()
