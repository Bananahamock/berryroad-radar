# Berryroad Radar

Scannt täglich Paper und Fachartikel zu Produktion, Robotik und Industrial AI,
bewertet jeden Treffer gegen das Berryroad-Profil und schickt dir:

- **Montag früh** den Wochen-Digest mit Lage der Woche und den relevanten Treffern, sortiert nach vier Rubriken
  (Start-ups, Beratungen, Forschung, Markt/Regulierung/Förderung)
- **Sofort** eine Alarm-Mail, wenn ein Treffer 9 oder 10 von 10 erreicht

Jeder Treffer hat: Was ist es, Reifegrad, was heißt das für Berryroad, Empfehlung (vertiefen, beobachten, ignorieren).
Alle Digests liegen zusätzlich im Ordner `digests/`.

## Einrichten (ca. 15 Minuten)

1. **Repo anlegen:** Auf GitHub ein neues, privates Repo `berryroad-radar` anlegen.
2. **Dateien hochladen:** Inhalt dieses Ordners hochladen ("Add file" → "Upload files"),
   inklusive Ordner `.github/workflows`. Achtung: Den Ordner `.github` blendet Finder/Explorer gern aus.
3. **Secrets setzen:** Repo → Settings → Secrets and variables → Actions → "New repository secret":

   | Secret | Inhalt |
   |---|---|
   | `ANTHROPIC_API_KEY` | API-Key aus console.anthropic.com (dort ein monatliches Ausgabenlimit setzen) |
   | `SMTP_HOST` | wie beim Jobradar |
   | `SMTP_PORT` | 587 oder 465 |
   | `SMTP_USER` | Absender-Login |
   | `SMTP_PASS` | App-Passwort |
   | `MAIL_TO` | mario@berryroad.de |
   | `MAIL_FROM` | optional, sonst SMTP_USER |

4. **Schreibrechte:** Settings → Actions → General → Workflow permissions → "Read and write permissions".
5. **Testlauf:** Actions → "Berryroad Radar" → "Run workflow" mit `daily`, danach nochmal mit `digest`.

Ohne `ANTHROPIC_API_KEY` läuft der Radar auch, bewertet dann aber nur per Stichwort.
Das ist deutlich schwächer und ohne Zusammenfassung.

## Anpassen

- `config/profil.md`: wer Berryroad ist und was relevant ist. **Das ist der wichtigste Hebel.**
  Wenn dir Treffer zu breit oder zu eng sind, hier nachschärfen.
- `config/sources.yaml`: Quellen und Schwellen (`alarm_ab_score`, `digest_ab_score`, `digest_max_eintraege`).
  Neue Quelle: einfach einen Block kopieren. `gnews` mit `site:domain.de` deckt fast jede Website ab.

## Zeitplan

- Täglich ca. 05:15 Uhr (Wiener Zeit): sammeln, bewerten, Alarm
- Montag ca. 06:20 Uhr: Wochen-Digest
- In der Sommerzeit jeweils eine Stunde später (GitHub rechnet in UTC)

## Lokal testen

```
pip install -r requirements.txt
python radar.py demo      # Beispieldaten, kein Netz, keine Mail
python radar.py daily
python radar.py digest
```
