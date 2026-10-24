#!/usr/bin/env python3
"""
Berryroad Radar
Sammelt Paper und Fachartikel zu Produktion, Robotik und Industrial AI,
bewertet sie gegen das Berryroad-Profil und verschickt Alarm-Mails und einen Wochen-Digest.

Aufruf:
  python radar.py daily     sammeln, bewerten, speichern, Alarm-Mail bei Top-Treffern
  python radar.py digest    Wochen-Digest der letzten 7 Tage erstellen und mailen
  python radar.py demo      Testlauf mit Beispieldaten, ohne Netz und ohne Mail

Umgebungsvariablen (in GitHub als Secrets):
  ANTHROPIC_API_KEY   ohne Key wird per Stichwort bewertet (deutlich schwächer)
  SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, MAIL_TO, MAIL_FROM (optional)
  RADAR_MODEL_SCORE   Standard claude-haiku-5-5
  RADAR_MODEL_DIGEST  Standard claude-sonnet-5-5
  RADAR_NO_MAIL=1     Mails nicht senden, nur Dateien schreiben
"""

import datetime as dt
import hashlib
import html
import json
import os
import re
import smtplib
import sys
import urllib.parse
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import feedparser
import requests
import yaml

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config"
STATE = ROOT / "state"
DATA = ROOT / "data"
DIGESTS = ROOT / "digests"

MODEL_SCORE = os.getenv("RADAR_MODEL_SCORE", "claude-haiku-5-5")
MODEL_DIGEST = os.getenv("RADAR_MODEL_DIGEST", "claude-sonnet-5-5")
UA = {"User-Agent": "Mozilla/5.0 (BerryroadRadar/1.0; +https://berryroad.de)"}

RUBRIKEN = {
    "startups": "Start-ups: Wie stellen sie sich auf?",
    "beratungen": "Beratungen: Wohin entwickeln sie sich?",
    "forschung": "Forschung: Was ist reif für die Industrie?",
    "markt": "Markt, Regulierung, Förderung (DACH)",
}
EMPFEHLUNGEN = ["vertiefen", "beobachten", "ignorieren"]
REIFEGRADE = ["Forschung", "Pilot", "industrialisierbar", "im Markt"]


# ----------------------------------------------------------------- Hilfen

def heute() -> dt.date:
    return dt.datetime.now(dt.timezone.utc).date()


def lade_config() -> dict:
    with open(CONFIG / "sources.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def lade_profil() -> str:
    return (CONFIG / "profil.md").read_text(encoding="utf-8")


def lade_json(pfad: Path, default):
    if pfad.exists():
        try:
            return json.loads(pfad.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return default
    return default


def speichere_json(pfad: Path, daten) -> None:
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_text(json.dumps(daten, ensure_ascii=False, indent=1), encoding="utf-8")


def text_sauber(s: str, max_len: int = 600) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:max_len]


def schluessel(titel: str) -> str:
    norm = re.sub(r"[^a-z0-9äöüß]+", "", titel.lower())
    return hashlib.sha1(norm.encode()).hexdigest()[:16]


def eintrag_datum(e) -> dt.date | None:
    for feld in ("published_parsed", "updated_parsed"):
        t = e.get(feld)
        if t:
            return dt.date(t.tm_year, t.tm_mon, t.tm_mday)
    return None


# ----------------------------------------------------------------- Sammeln

def feed_url(q: dict, max_n: int) -> str:
    typ = q["typ"]
    if typ == "rss":
        return q["url"]
    if typ == "arxiv":
        params = {
            "search_query": q["query"],
            "sortBy": "submittedDate",
            "sortOrder": "descending",
            "max_results": str(max_n),
        }
        return "https://export.arxiv.org/api/query?" + urllib.parse.urlencode(params)
    if typ == "gnews":
        query = q["query"] + " when:7d"
        return ("https://news.google.com/rss/search?q=" + urllib.parse.quote(query)
                + "&hl=de&gl=AT&ceid=AT:de")
    raise ValueError(f"Unbekannter Quellentyp: {typ}")


def sammle(cfg: dict) -> tuple[list[dict], dict]:
    eins = cfg["einstellungen"]
    max_n = eins["max_pro_quelle"]
    grenze = heute() - dt.timedelta(days=eins["max_alter_tage"])
    gesehen = lade_json(STATE / "seen.json", {})
    neu, status = [], {}

    for q in cfg["quellen"]:
        name = q["name"]
        try:
            r = requests.get(feed_url(q, max_n), headers=UA, timeout=30)
            r.raise_for_status()
            feed = feedparser.parse(r.content)
            if feed.bozo and not feed.entries:
                raise RuntimeError("Feed nicht lesbar")
            zaehler = 0
            for e in feed.entries[:max_n]:
                titel = text_sauber(e.get("title", ""), 300)
                if not titel:
                    continue
                d = eintrag_datum(e)
                if d and d < grenze:
                    continue
                k = schluessel(titel)
                if k in gesehen:
                    continue
                gesehen[k] = heute().isoformat()
                quelle_detail = ""
                if q["typ"] == "gnews" and e.get("source"):
                    quelle_detail = e["source"].get("title", "")
                neu.append({
                    "id": k,
                    "titel": titel,
                    "link": e.get("link", ""),
                    "text": text_sauber(e.get("summary", "")),
                    "quelle": quelle_detail or name,
                    "quellgruppe": name,
                    "rubrik_hinweis": q.get("rubrik", "markt"),
                    "datum": (d or heute()).isoformat(),
                    "gesammelt": heute().isoformat(),
                })
                zaehler += 1
            status[name] = {"ok": True, "neu": zaehler, "stand": heute().isoformat()}
        except Exception as ex:  # eine kaputte Quelle darf den Lauf nicht stoppen
            status[name] = {"ok": False, "fehler": str(ex)[:160], "stand": heute().isoformat()}

    # alte Schlüssel nach 120 Tagen vergessen
    grenze_seen = (heute() - dt.timedelta(days=120)).isoformat()
    gesehen = {k: v for k, v in gesehen.items() if v >= grenze_seen}
    speichere_json(STATE / "seen.json", gesehen)
    return neu, status


# ----------------------------------------------------------------- Bewerten

BEWERTUNG_TOOL = {
    "name": "bewertung",
    "description": "Bewertung aller übergebenen Einträge gegen das Berryroad-Profil.",
    "input_schema": {
        "type": "object",
        "properties": {
            "eintraege": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "score": {"type": "integer", "minimum": 0, "maximum": 10},
                        "rubrik": {"type": "string", "enum": list(RUBRIKEN)},
                        "reifegrad": {"type": "string", "enum": REIFEGRADE},
                        "was": {"type": "string", "description": "Was ist es, 1 bis 2 Sätze, Deutsch"},
                        "berryroad": {"type": "string", "description": "Was heißt das konkret für Berryroad, 1 bis 2 Sätze, Deutsch"},
                        "empfehlung": {"type": "string", "enum": EMPFEHLUNGEN},
                    },
                    "required": ["id", "score", "rubrik", "reifegrad", "was", "berryroad", "empfehlung"],
                },
            }
        },
        "required": ["eintraege"],
    },
}


def bewerte_llm(items: list[dict], profil: str) -> None:
    import anthropic
    client = anthropic.Anthropic()
    system = (
        "Du bist der Markt- und Technologie-Scout von Berryroad. Bewerte nüchtern und kritisch, "
        "ohne Hype. Schreibe auf Deutsch, kurz und konkret, ohne Gedankenstriche. "
        "Nutze ausschließlich die Angaben im Eintrag, erfinde keine Fakten.\n\n" + profil
    )
    for i in range(0, len(items), 15):
        batch = items[i:i + 15]
        payload = [{"id": it["id"], "titel": it["titel"], "quelle": it["quelle"],
                    "rubrik_hinweis": it["rubrik_hinweis"], "text": it["text"]} for it in batch]
        try:
            resp = client.messages.create(
                model=MODEL_SCORE,
                max_tokens=4000,
                system=system,
                tools=[BEWERTUNG_TOOL],
                tool_choice={"type": "tool", "name": "bewertung"},
                messages=[{"role": "user", "content":
                           "Bewerte diese Einträge:\n" + json.dumps(payload, ensure_ascii=False)}],
            )
            ergebnis = next(b.input for b in resp.content if b.type == "tool_use")
            nach_id = {e["id"]: e for e in ergebnis.get("eintraege", [])}
            for it in batch:
                e = nach_id.get(it["id"])
                if e:
                    it.update({k: e[k] for k in ("score", "rubrik", "reifegrad", "was", "berryroad", "empfehlung")})
                    it["bewertet_von"] = MODEL_SCORE
                else:
                    bewerte_stichwort([it])
        except Exception as ex:
            print(f"LLM-Bewertung fehlgeschlagen, Stichwort-Fallback: {ex}", file=sys.stderr)
            bewerte_stichwort(batch)


STICHWORTE = {
    3: ["bin picking", "physical ai", "high-mix", "maschinenverordnung", "2023/1230", "humanoid",
        "fabrikplanung", "factory layout", "mittelstand", "kmu", "förderung"],
    2: ["robot", "robotik", "automation", "automatisierung", "digital twin", "digitaler zwilling",
        "manufacturing", "fertigung", "assembly", "montage", "grasp", "greifen"],
    1: ["ai", "ki", "startup", "funding", "raises", "simulation", "vision"],
}


def bewerte_stichwort(items: list[dict]) -> None:
    for it in items:
        t = (it["titel"] + " " + it["text"]).lower()
        punkte = sum(g for g, worte in STICHWORTE.items() for w in worte if w in t)
        it.update({
            "score": min(10, punkte),
            "rubrik": it["rubrik_hinweis"],
            "reifegrad": "Forschung" if it["rubrik_hinweis"] == "forschung" else "im Markt",
            "was": it["text"][:220] or it["titel"],
            "berryroad": "Automatisch per Stichwort bewertet, bitte selbst einordnen.",
            "empfehlung": "beobachten" if punkte >= 6 else "ignorieren",
            "bewertet_von": "stichwort",
        })


def bewerte(items: list[dict]) -> None:
    if not items:
        return
    if os.getenv("ANTHROPIC_API_KEY"):
        bewerte_llm(items, lade_profil())
    else:
        bewerte_stichwort(items)


# ----------------------------------------------------------------- Speichern

def speichere_items(items: list[dict]) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    monat = DATA / f"{heute():%Y-%m}.jsonl"
    with open(monat, "a", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")


def lade_items(tage: int) -> list[dict]:
    grenze = (heute() - dt.timedelta(days=tage)).isoformat()
    out = []
    for p in sorted(DATA.glob("*.jsonl")):
        for zeile in p.read_text(encoding="utf-8").splitlines():
            if zeile.strip():
                it = json.loads(zeile)
                if it.get("gesammelt", "") >= grenze:
                    out.append(it)
    return out


# ----------------------------------------------------------------- Ausgabe

FARBE = "#0097a7"


def html_eintrag(it: dict) -> str:
    badge = {"vertiefen": "#0097a7", "beobachten": "#607d8b", "ignorieren": "#b0bec5"}[it.get("empfehlung", "beobachten")]
    return f"""
<tr><td style="padding:12px 0;border-bottom:1px solid #e0e0e0">
  <div style="font-size:12px;color:#607d8b">{html.escape(it['quelle'])} · {it['datum']} · Reifegrad: {html.escape(it.get('reifegrad',''))}</div>
  <div style="font-size:15px;font-weight:600;margin:4px 0"><a href="{html.escape(it['link'])}" style="color:#263238;text-decoration:none">{html.escape(it['titel'])}</a></div>
  <div style="font-size:14px;color:#37474f"><b>Was:</b> {html.escape(it.get('was',''))}</div>
  <div style="font-size:14px;color:#37474f;margin-top:2px"><b>Für Berryroad:</b> {html.escape(it.get('berryroad',''))}</div>
  <div style="margin-top:6px"><span style="background:{badge};color:#fff;font-size:12px;padding:2px 8px;border-radius:10px">{it.get('empfehlung','')} · {it.get('score',0)}/10</span></div>
</td></tr>"""


def html_rahmen(titel: str, intro_html: str, koerper: str, fuss: str = "") -> str:
    return f"""<!doctype html><html><body style="margin:0;background:#f5f7f8;font-family:Arial,Helvetica,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">
<table width="640" cellpadding="0" cellspacing="0" style="background:#fff;max-width:640px">
<tr><td style="height:6px;background:{FARBE}"></td></tr>
<tr><td style="padding:24px 28px 8px"><div style="font-size:22px;font-weight:700;color:{FARBE}">{html.escape(titel)}</div></td></tr>
<tr><td style="padding:0 28px;font-size:14px;color:#37474f;line-height:1.5">{intro_html}</td></tr>
<tr><td style="padding:0 28px"><table width="100%" cellpadding="0" cellspacing="0">{koerper}</table></td></tr>
<tr><td style="padding:20px 28px;font-size:12px;color:#90a4ae">{fuss}</td></tr>
</table></td></tr></table></body></html>"""


def md_eintrag(it: dict) -> str:
    return (f"- **[{it['titel']}]({it['link']})** ({it['quelle']}, {it['datum']}, {it.get('reifegrad','')})  \n"
            f"  Was: {it.get('was','')}  \n"
            f"  Für Berryroad: {it.get('berryroad','')}  \n"
            f"  → **{it.get('empfehlung','')}** · {it.get('score',0)}/10\n")


def wochen_intro(items: list[dict]) -> str:
    """Kurze Lageeinschätzung der Woche durch das größere Modell."""
    if not os.getenv("ANTHROPIC_API_KEY") or not items:
        return ""
    try:
        import anthropic
        client = anthropic.Anthropic()
        kurz = [{"titel": i["titel"], "rubrik": i["rubrik"], "score": i["score"],
                 "was": i["was"], "berryroad": i["berryroad"]} for i in items]
        resp = client.messages.create(
            model=MODEL_DIGEST,
            max_tokens=900,
            system=("Du bist der Markt-Scout von Berryroad, einem unabhängigen Planungsbüro für Automatisierung "
                    "und Physical AI für Mittelständler in Bayern und Österreich. Schreibe auf Deutsch, direkt, "
                    "kurze Sätze, keine Gedankenstriche, kein Hype. Nur auf Basis der übergebenen Einträge.\n\n"
                    + lade_profil()),
            messages=[{"role": "user", "content":
                       "Schreibe die Lage der Woche in genau diesem Format, ohne Überschrift:\n"
                       "3 nummerierte Kernaussagen (je 1 bis 2 Sätze), was sich bewegt.\n"
                       "Dann eine Zeile 'Signal für Berryroad:' mit der einen wichtigsten Konsequenz.\n"
                       "Dann eine Zeile 'Vertiefen?' mit dem einen Thema, das sich für eine Paper-Analyse lohnt, und warum.\n\n"
                       + json.dumps(kurz, ensure_ascii=False)}],
        )
        return "".join(b.text for b in resp.content if b.type == "text").strip()
    except Exception as ex:
        print(f"Wochen-Intro fehlgeschlagen: {ex}", file=sys.stderr)
        return ""


def status_text(status: dict) -> tuple[str, str]:
    kaputt = [n for n, s in status.items() if not s.get("ok")]
    if not status:
        return "", ""
    if not kaputt:
        t = f"Quellen-Status: alle {len(status)} Quellen erreichbar."
    else:
        t = f"Quellen-Status: {len(status) - len(kaputt)} von {len(status)} erreichbar. Nicht erreichbar: " + ", ".join(kaputt)
    return html.escape(t), t


# ----------------------------------------------------------------- Mail

def sende_mail(betreff: str, html_body: str, text_body: str) -> None:
    if os.getenv("RADAR_NO_MAIL") == "1":
        print(f"[RADAR_NO_MAIL] Mail nicht gesendet: {betreff}")
        return
    host = os.getenv("SMTP_HOST")
    if not host:
        print("Kein SMTP_HOST gesetzt, Mail übersprungen.")
        return
    host = host.strip()
    port_txt = (os.getenv("SMTP_PORT") or "").strip()
    port = int(port_txt) if port_txt.isdigit() else 587
    user = (os.getenv("SMTP_USER") or "").strip()
    pw = (os.getenv("SMTP_PASS") or "").replace(" ", "").strip()  # Google zeigt App-Passwörter mit Leerzeichen
    an = (os.getenv("MAIL_TO") or "").strip()
    von = (os.getenv("MAIL_FROM") or "").strip() or user
    fehlend = [n for n, v in (("SMTP_USER", user), ("SMTP_PASS", pw), ("MAIL_TO", an)) if not v]
    if fehlend:
        raise MailFehler("Secret fehlt oder ist leer: " + ", ".join(fehlend))
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = betreff, von, an
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    empfaenger = [a.strip() for a in an.split(",") if a.strip()]
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=30) as s:
                s.login(user, pw)
                s.sendmail(von, empfaenger, msg.as_string())
        else:
            with smtplib.SMTP(host, port, timeout=30) as s:
                s.starttls()
                s.login(user, pw)
                s.sendmail(von, empfaenger, msg.as_string())
    except smtplib.SMTPAuthenticationError as ex:
        raise MailFehler(f"Login bei {host} abgelehnt. SMTP_USER muss die volle Gmail-Adresse sein, "
                         f"SMTP_PASS das App-Passwort. Antwort: {ex.smtp_code} {ex.smtp_error[:120]!r}")
    except (OSError, smtplib.SMTPException) as ex:
        raise MailFehler(f"Versand über {host}:{port} fehlgeschlagen: {type(ex).__name__}: {ex}")
    print(f"Mail gesendet: {betreff}")


class MailFehler(Exception):
    pass


MAIL_FEHLER: list[str] = []


def sende_mail_sicher(betreff: str, html_body: str, text_body: str) -> None:
    """Mailfehler dürfen die gespeicherten Ergebnisse nicht verhindern."""
    try:
        sende_mail(betreff, html_body, text_body)
    except MailFehler as ex:
        print(f"::error title=Mailversand::{ex}")  # erscheint in GitHub unter Annotations
        MAIL_FEHLER.append(str(ex))


# ----------------------------------------------------------------- Läufe

def lauf_daily(items: list[dict] | None = None, status: dict | None = None) -> None:
    cfg = lade_config()
    if items is None:
        items, status = sammle(cfg)
    bewerte(items)
    speichere_items(items)
    alt = lade_json(STATE / "status.json", {})
    alt.update(status or {})
    speichere_json(STATE / "status.json", alt)
    print(f"{len(items)} neue Einträge bewertet.")

    schwelle = cfg["einstellungen"]["alarm_ab_score"]
    top = sorted([i for i in items if i.get("score", 0) >= schwelle], key=lambda i: -i["score"])
    if top:
        koerper = "".join(html_eintrag(i) for i in top)
        intro = "<p>Diese Treffer liegen über deiner Alarm-Schwelle und sollten nicht bis Montag warten.</p>"
        sende_mail_sicher(f"Berryroad Radar: {len(top)} Top-Treffer",
                   html_rahmen("Radar-Alarm", intro, koerper),
                   "\n".join(md_eintrag(i) for i in top))


def lauf_digest() -> Path:
    cfg = lade_config()
    eins = cfg["einstellungen"]
    alle = lade_items(7)
    relevant = sorted([i for i in alle if i.get("score", 0) >= eins["digest_ab_score"]],
                      key=lambda i: (-i["score"], i["datum"]))[: eins["digest_max_eintraege"]]

    kw = heute().isocalendar()
    titel = f"Berryroad Radar KW {kw.week}/{kw.year}"
    intro = wochen_intro(relevant)
    zahlen = (f"{len(alle)} Einträge gesichtet, {len(relevant)} relevant, "
              f"{sum(1 for i in relevant if i['empfehlung'] == 'vertiefen')} zum Vertiefen.")
    st_html, st_text = status_text(lade_json(STATE / "status.json", {}))

    intro_html = f"<p style='color:#607d8b'>{html.escape(zahlen)}</p>"
    if intro:
        intro_html += ("<div style='background:#e0f2f1;padding:14px 16px;margin:8px 0 4px;border-left:4px solid "
                       f"{FARBE}'><b>Lage der Woche</b><br>" + html.escape(intro).replace("\n", "<br>") + "</div>")

    koerper, md = "", [f"# {titel}\n", f"_{zahlen}_\n"]
    if intro:
        md += ["## Lage der Woche\n", intro + "\n"]
    for key, name in RUBRIKEN.items():
        teil = [i for i in relevant if i.get("rubrik") == key]
        if not teil:
            continue
        koerper += (f"<tr><td style='padding:22px 0 4px;font-size:16px;font-weight:700;color:{FARBE};"
                    f"border-bottom:2px solid {FARBE}'>{html.escape(name)}</td></tr>")
        koerper += "".join(html_eintrag(i) for i in teil)
        md.append(f"\n## {name}\n")
        md += [md_eintrag(i) for i in teil]
    if not relevant:
        koerper = "<tr><td style='padding:16px 0'>Diese Woche nichts über der Schwelle. Ruhige Woche oder Quellen prüfen.</td></tr>"
    md.append(f"\n---\n{st_text}\n")

    DIGESTS.mkdir(parents=True, exist_ok=True)
    pfad = DIGESTS / f"{kw.year}-KW{kw.week:02d}.md"
    pfad.write_text("\n".join(md), encoding="utf-8")
    html_body = html_rahmen(titel, intro_html, koerper, st_html)
    (DIGESTS / "letzter-digest.html").write_text(html_body, encoding="utf-8")
    sende_mail_sicher(titel, html_body, "\n".join(md))
    print(f"Digest geschrieben: {pfad}")
    return pfad


def demo_items() -> list[dict]:
    d = heute().isoformat()
    roh = [
        ("Zero-shot bin picking of unknown industrial parts with a vision-language-action model",
         "We present a VLA policy that grasps unseen metal parts from cluttered bins with 94% success in a factory pilot.",
         "arXiv", "forschung"),
        ("Startup raises $40M to bring robotics-as-a-service to mid-sized manufacturers",
         "The company offers welding and palletizing cells on a monthly subscription for SMEs.",
         "TechCrunch Robotics", "startups"),
        ("McKinsey: Generative AI in factory planning cuts concept phase by 30 percent",
         "New report on AI-assisted layout simulation and line balancing in manufacturing.",
         "mckinsey.com", "beratungen"),
        ("Neue Maschinenverordnung: Was Hersteller ab Januar 2027 bei KI-Funktionen beachten müssen",
         "Die Verordnung 2023/1230 bringt neue Anforderungen an selbstlernende Steuerungen.",
         "VDI nachrichten", "markt"),
        ("Celebrity chef opens new restaurant", "Nothing to do with manufacturing.", "Google News", "markt"),
    ]
    return [{"id": schluessel(t), "titel": t, "link": "https://example.org/" + schluessel(t),
             "text": tx, "quelle": q, "quellgruppe": q, "rubrik_hinweis": r,
             "datum": d, "gesammelt": d} for t, tx, q, r in roh]


def main() -> None:
    modus = sys.argv[1] if len(sys.argv) > 1 else "daily"
    if modus == "daily":
        lauf_daily()
    elif modus == "digest":
        lauf_digest()
    elif modus == "demo":
        os.environ["RADAR_NO_MAIL"] = "1"
        lauf_daily(demo_items(), {"Demo-Quelle": {"ok": True, "neu": 5}})
        lauf_digest()
    else:
        print(__doc__)
        sys.exit(1)
    if MAIL_FEHLER:
        sys.exit(2)  # Lauf rot markieren, Ergebnisse sind trotzdem gespeichert


if __name__ == "__main__":
    main()
