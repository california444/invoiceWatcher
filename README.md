# Invoice Watcher

Überwacht einen E-Mail-Posteingang per IMAP IDLE und erzeugt automatisch
einen **GiroCode / EPC-QR-Code** aus eingehenden Rechnungen.

## Funktionsweise

```text
Neue E-Mail mit PDF-Anhang
        │
        ▼
┌─────────────────────┐
│ ZUGFeRD / Factur-X? │  ──→  XML aus PDF extrahieren
└─────────────────────┘
        │ nein
        ▼
┌─────────────────────┐
│  XRechnung (XML)?   │  ──→  XML-Anhang parsen
└─────────────────────┘
        │ nein
        ▼
┌─────────────────────┐
│   LLM (OpenAI API)  │  ──→  PDF-Seiten als Bilder an Vision-Modell
└─────────────────────┘
        │
        ▼
  EPC-QR-Code erzeugen
        │
        ▼
  E-Mail mit GiroCode-PNG
  an Absender zurücksenden
```

## Voraussetzungen

- Python 3.11+ (Docker-Image: Python 3.13 auf Debian 13 "trixie")
- API-Key eines OpenAI-kompatiblen Anbieters (z. B. [OpenRouter](https://openrouter.ai))

## LLM-API einrichten (einmalig)

Der LLM-Fallback nutzt eine externe OpenAI-kompatible API. Empfohlen wird [OpenRouter](https://openrouter.ai):

1. Konto anlegen unter [openrouter.ai](https://openrouter.ai)
2. API-Key erstellen unter [openrouter.ai/keys](https://openrouter.ai/keys)
3. Optional: Guthaben aufladen (Prepaid, kein Abo nötig)

**Verfügbare Modelle auf OpenRouter (Stand Juni 2026):**

| Modell | Größe | Kosten/Seite | Empfehlung |
| --- | --- | --- | --- |
| `qwen/qwen3-vl-8b-instruct` | 8B | ~$0.00005 | Einstieg, schnell |
| `qwen/qwen3-vl-32b-instruct` | 32B | ~$0.0002 | Gute Balance |
| `qwen/qwen2.5-vl-72b-instruct` | 72B | ~$0.0004 | Bewährt für Dokumente |
| `gpt-4o` | – | ~$0.003 | Beste Genauigkeit |

Alternative Anbieter: [Together.ai](https://api.together.xyz) · [OpenAI](https://platform.openai.com)

```bash
# Ins Projektverzeichnis wechseln
cd invoice_watcher

# Virtuelle Umgebung anlegen und aktivieren
python3 -m venv .venv
source .venv/bin/activate

# Abhängigkeiten installieren
pip install -r requirements.txt
```

## Konfiguration

```bash
cp .env.example .env
```

Dann `.env` mit den eigenen Zugangsdaten befüllen:

| Variable | Beschreibung | Beispiel |
| --- | --- | --- |
| `IMAP_HOST` | IMAP-Serveradresse | `imap.gmail.com` |
| `IMAP_PORT` | IMAP-Port (SSL) | `993` |
| `IMAP_USER` | E-Mail-Adresse | `you@gmail.com` |
| `IMAP_PASSWORD` | IMAP-Passwort / App-Passwort | |
| `IMAP_MAILBOX` | Zu überwachendes Postfach | `INBOX` |
| `IMAP_TARGET_RECIPIENT` | Optional: Es werden nur Mails verarbeitet, die (auch) an diese Adresse gehen (To/Cc-Header). Nur passende Mails werden danach als `\Seen` markiert – das verhindert doppelte Verarbeitung/doppelten QR-Code-Versand nach einem Reconnect oder Neustart. Ohne diese Variable wird jede ungelesene Mail mit PDF/XML-Anhang verarbeitet (ohne Markierung als gelesen). | `rechnungen@example.de` |
| `SMTP_HOST` | SMTP-Serveradresse | `smtp.gmail.com` |
| `SMTP_PORT` | SMTP-Port (STARTTLS: 587, SSL: 465) | `587` |
| `SMTP_USER` | Login-Benutzername für die SMTP-Authentifizierung | `you@gmail.com` |
| `SMTP_PASSWORD` | SMTP-Passwort / App-Passwort | |
| `SMTP_FROM` | Optional: Absenderadresse im `From`-Header, falls abweichend von `SMTP_USER` (Login erfolgt weiterhin mit `SMTP_USER`; Provider muss "Senden im Namen von" erlauben, sonst greifen SPF/DKIM nicht) | `noreply@example.com` |
| `LLM_API_BASE_URL` | API-Endpunkt (OpenAI-kompatibel) | `https://openrouter.ai/api/v1` |
| `LLM_API_KEY` | API-Key des Anbieters | `sk-or-v1-…` |
| `LLM_MODEL` | Modell-ID | `qwen/qwen3-vl-8b-instruct` |
| `LLM_MAX_PAGES` | Max. Seiten pro PDF (0 = alle) | `3` |

### Gmail / Google Workspace

Gmail erfordert ein **App-Passwort** statt des normalen Kontopassworts:  
Google-Konto → Sicherheit → 2-Faktor-Authentifizierung → App-Passwörter

IMAP muss außerdem in den Gmail-Einstellungen aktiviert sein:  
Einstellungen → Alle Einstellungen → Weiterleitung und POP/IMAP → IMAP aktivieren

### GMX / Web.de / Posteo / Mailbox.org

Diese Anbieter unterstützen IMAP IDLE standardmäßig. Normale Zugangsdaten verwenden.

## Starten

```bash
source .venv/bin/activate
python invoice_watcher.py
```

Der Watcher läuft im Vordergrund und gibt Statusmeldungen auf der Konsole aus.  
Beenden mit `Ctrl+C`.

### Als Hintergrunddienst (macOS launchd)

Für automatischen Start beim Login eine Datei
`~/Library/LaunchAgents/com.invoicewatcher.plist` anlegen:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.invoicewatcher</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/roman/Nextcloud/Roman/12_Code/01_BobbyCar/control/invoice_watcher/.venv/bin/python</string>
    <string>/Users/roman/Nextcloud/Roman/12_Code/01_BobbyCar/control/invoice_watcher/invoice_watcher.py</string>
  </array>
  <key>WorkingDirectory</key>
  <string>/Users/roman/Nextcloud/Roman/12_Code/01_BobbyCar/control/invoice_watcher</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>/tmp/invoice_watcher.log</string>
  <key>StandardErrorPath</key>
  <string>/tmp/invoice_watcher.log</string>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.invoicewatcher.plist
```

### Docker

Bei jedem Push auf `main` baut GitHub Actions das Image für `linux/arm64`
(Raspberry Pi) und veröffentlicht es unter
`ghcr.io/california444/invoicewatcher`. Zusätzlich wird es jeden Montag neu
gebaut, damit Sicherheitsupdates des Basis-Images ankommen. Verfügbare Tags:

| Tag | Bedeutung |
| --- | --- |
| `latest` | aktueller Stand von `main` |
| `sha-<commit>` | genau dieser Commit – für Rollbacks |
| `<JJJJMMTT>` | Stand des jeweiligen Build-Tages |

Starten mit der mitgelieferten [docker-compose.yml](docker-compose.yml)
(Zugangsdaten vorher eintragen):

```bash
docker compose up -d
```

Auf eine neue Version aktualisieren:

```bash
docker compose pull && docker compose up -d
```

Selbst bauen (optional):

```bash
docker build -t invoicewatcher .
```

Der Build nimmt den Quellcode aus dem Arbeitsverzeichnis, nicht aus dem
GitHub-Repo – das gebaute Image entspricht also dem ausgecheckten Stand.

> **Hinweis:** Neue Packages in der GitHub Container Registry sind zunächst
> privat. Für einen Pull ohne Anmeldung muss das Package in den
> Package-Einstellungen auf "public" gestellt werden, sonst ist auf dem Host
> ein `docker login ghcr.io` mit einem PAT (Scope `read:packages`) nötig.
> Die Datei `.env` ist durch `.dockerignore` vom Image-Build ausgeschlossen.

## Unterstützte Rechnungsformate

### Strukturierte E-Rechnungen (automatisch, kein LLM nötig)

| Format | Beschreibung |
| --- | --- |
| **ZUGFeRD / Factur-X** | XML eingebettet in PDF (gängig in D/A/CH/FR) |
| **XRechnung CII** | Standalone-XML als Anhang (Cross Industry Invoice) |
| **XRechnung UBL** | Standalone-XML als Anhang (Universal Business Language) |

### LLM-Fallback (OpenAI-kompatible API)

Wenn kein strukturiertes Format erkannt wird, werden PDF-Seiten als Bilder
(150 DPI) an das konfigurierte Vision-Modell gesendet. Die Anzahl der Seiten
ist über `LLM_MAX_PAGES` steuerbar (Standard: 3, `0` = alle Seiten). Das
Modell prüft, ob es sich um eine Rechnung handelt, und extrahiert IBAN, BIC,
Betrag und Verwendungszweck.

> **Hinweis:** LLM-Extraktion ist fehleranfällig. Die versendete E-Mail
> enthält zur Sicherheit immer auch die extrahierten Felder im Textbody sowie
> die Quelle der Daten (`ZUGFeRD/Factur-X`, `XRechnung` oder
> `LLM (Modellname)`) zur manuellen Prüfung.

## GiroCode / EPC-QR-Code

Der erzeugte QR-Code entspricht dem **EPC069-12 v2.1 Standard** (European
Payments Council). Er kann mit jeder gängigen Banking-App (z. B. Deutsche
Bank, Sparkasse, ING, DKB, N26) direkt gescannt werden, um eine
SEPA-Überweisung vorzubefüllen.

Enthaltene Felder:

- Empfänger (Name)
- IBAN
- BIC (optional, seit SEPA 2016 nicht mehr zwingend)
- Betrag in EUR
- Verwendungszweck

Der QR-Code wird als `girocode.png` an die versendete E-Mail angehängt.

## Dateiübersicht

```text
invoice_watcher/
├── invoice_watcher.py   # Entry-Point, IMAP-IDLE-Loop, Orchestrierung
├── invoice_parser.py    # ZUGFeRD / XRechnung XML-Extraktion
├── llm_extractor.py     # LLM-Fallback via OpenAI-kompatibler API
├── epc_qr.py            # EPC-QR-Code-Erzeugung
├── mailer.py            # SMTP-E-Mail-Versand
├── requirements.txt
└── .env.example
```

## Fehlerbehebung

### IMAP IDLE wird nicht unterstützt**

Manche Anbieter unterstützen IDLE nicht. `imapclient` fällt in diesem Fall
auf reguläres Polling zurück. Betrieb ist trotzdem möglich, aber weniger
effizient.

### API-Key ungültig oder Modell nicht gefunden**

```bash
# Verfügbare Modelle abfragen:
python3 -c "
from openai import OpenAI
client = OpenAI(base_url='https://openrouter.ai/api/v1', api_key='DEIN_KEY')
for m in client.models.list().data:
    if 'vl' in m.id: print(m.id)
"
```

**`factur-x` erkennt kein eingebettetes XML**

Nicht alle PDFs enthalten ZUGFeRD/Factur-X-Daten, auch wenn sie als
E-Rechnung bezeichnet werden. In diesem Fall greift automatisch der
LLM-Fallback.

### E-Mail wird nicht gesendet (SMTP-Fehler)**

- Port 587: STARTTLS wird erwartet – Anbieter muss STARTTLS unterstützen.
- Port 465: SMTP_SSL – älteres, aber weit verbreitetes Verfahren.
- Bei Gmail / GMX unbedingt App-Passwörter verwenden.

### Rechnung wird mehrfach verarbeitet / GiroCode kommt doppelt an

Ohne `IMAP_TARGET_RECIPIENT` markiert der Watcher verarbeitete Mails nicht als
gelesen (damit sie im Postfach ungelesen bleiben). Bei jedem Reconnect
(Netzwerk-Hänger, IDLE-Timeout, Neustart) gilt die Mail dann wieder als neu und
wird erneut verarbeitet. Abhilfe: `IMAP_TARGET_RECIPIENT` setzen – nur Mails an
diese Adresse werden verarbeitet und anschließend serverseitig als `\Seen`
markiert, sodass sie nach einem Reconnect nicht erneut auftauchen.
