#!/usr/bin/env python3
"""
Suivi quotidien du prix d'un aller-retour précis (API Ignav).

    python tracker.py            relevé du jour : historique, page graphique, notification
    python tracker.py --render   régénère seulement la page à partir de l'historique
    python tracker.py --demo     écrit demo.html avec de fausses données (rien n'est modifié dans data/)

Aucune dépendance externe : bibliothèque standard uniquement.
"""
from __future__ import annotations

import csv
import json
import math
import os
import random
import sys
import time
import datetime as dt
import urllib.error
import urllib.request
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
HISTORY_CSV = ROOT / "data" / "prices.csv"
LAST_ITINERARY = ROOT / "data" / "last_itinerary.json"
PAGE = ROOT / "docs" / "index.html"

API_URL = "https://ignav.com/api/fares/round-trip"
NTFY_URL = "https://ntfy.sh"
FIELDS = ["date", "price", "currency", "cheapest_any"]

MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
        "août", "septembre", "octobre", "novembre", "décembre"]
MOIS_COURT = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.",
              "août", "sept.", "oct.", "nov.", "déc."]
JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
NBSP, NNBSP = "\u00a0", "\u202f"


# --------------------------------------------------------------------------
# Mise en forme
# --------------------------------------------------------------------------
def d_iso(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def fmt_date(d: dt.date, court: bool = False, annee: bool = False) -> str:
    mois = MOIS_COURT[d.month - 1] if court else MOIS[d.month - 1]
    base = f"{d.day} {mois}"
    return f"{base} {d.year}" if annee else base


def fmt_long(d: dt.date) -> str:
    return f"{JOURS[d.weekday()]} {d.day} {MOIS[d.month - 1]}"


def money(x: float, cur: str) -> str:
    n = f"{round(x):,}".replace(",", NNBSP)
    return f"{n}{NBSP}€" if cur == "EUR" else f"{cur}{NBSP}{n}"


def delta(x: float, cur: str) -> str:
    if round(x) == 0:
        return "inchangé"
    return f"{'+' if x > 0 else '−'}{money(abs(x), cur)}"


def adults_text() -> str:
    n = CFG["adults"]
    return f"{n} adulte{'s' if n > 1 else ''}"


def today() -> dt.date:
    return dt.datetime.now(ZoneInfo(CFG.get("timezone", "Europe/Zurich"))).date()


# --------------------------------------------------------------------------
# Appel à l'API et reconnaissance de l'itinéraire exact
# --------------------------------------------------------------------------
def norm_flight(code: str) -> str:
    """'AF0758' -> 'AF758' (le zéro initial est ignoré)."""
    code = str(code).strip().upper().replace(" ", "")
    return code[:2] + code[2:].lstrip("0")


def seg_id(seg: dict) -> str:
    return norm_flight(f"{seg['marketing_carrier_code']}{seg['flight_number']}")


def fetch_itineraries(key: str) -> list[dict]:
    keys = ("origin", "destination", "departure_date", "return_date",
            "adults", "cabin_class", "market")
    body = {k: CFG[k] for k in keys if CFG.get(k) is not None}
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"X-Api-Key": key, "Content-Type": "application/json",
                 "User-Agent": "flight-tracker/1.0"},
    )
    last_error = "erreur inconnue"
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                return json.load(resp).get("itineraries", [])
        except urllib.error.HTTPError as e:
            last_error = f"HTTP {e.code} : {e.read().decode('utf-8', 'replace')[:300]}"
            if e.code < 500 and e.code != 429:   # erreur « définitive » : inutile de réessayer
                break
        except Exception as e:                   # réseau, délai dépassé, JSON invalide…
            last_error = repr(e)
        time.sleep(5 * (attempt + 1))
    raise RuntimeError(last_error)


def find_match(itineraries: list[dict]) -> dict | None:
    """L'offre la moins chère dont les vols sont exactement ceux de la config."""
    want_out = [norm_flight(f) for f in CFG["outbound_flights"]]
    want_in = [norm_flight(f) for f in CFG["inbound_flights"]]
    best = None
    for it in itineraries:
        out = [seg_id(s) for s in (it.get("outbound") or {}).get("segments", [])]
        inn = [seg_id(s) for s in (it.get("inbound") or {}).get("segments", [])]
        if out == want_out and inn == want_in:
            if best is None or it["price"]["amount"] < best["price"]["amount"]:
                best = it
    return best


def slim(it: dict) -> dict:
    def leg(l: dict) -> list[dict]:
        return [{"flight": seg_id(s), "from": s["departure_airport"],
                 "to": s["arrival_airport"], "dep": s["departure_time_local"],
                 "arr": s["arrival_time_local"]} for s in l["segments"]]
    return {"outbound": leg(it["outbound"]), "inbound": leg(it["inbound"])}


# --------------------------------------------------------------------------
# Historique
# --------------------------------------------------------------------------
def load_history() -> list[dict]:
    if not HISTORY_CSV.exists():
        return []
    rows = []
    with HISTORY_CSV.open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            rows.append({
                "date": d_iso(r["date"]),
                "price": float(r["price"]) if r["price"] else None,
                "currency": r["currency"] or "EUR",
                "cheapest_any": float(r["cheapest_any"]) if r["cheapest_any"] else None,
            })
    return sorted(rows, key=lambda r: r["date"])


def save_history(rows: list[dict]) -> None:
    HISTORY_CSV.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(FIELDS)
        for r in rows:
            w.writerow([r["date"].isoformat(),
                        "" if r["price"] is None else f"{r['price']:g}",
                        r["currency"],
                        "" if r["cheapest_any"] is None else f"{r['cheapest_any']:g}"])


# --------------------------------------------------------------------------
# Notification (ntfy)
# --------------------------------------------------------------------------
def notify(title: str, message: str, priority: int = 3, tags: list[str] | None = None) -> None:
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        print(f"[ntfy désactivé] {title} — {message}")
        return
    payload = {"topic": topic, "title": title, "message": message,
               "priority": priority, "tags": tags or []}
    if os.environ.get("PAGE_URL"):
        payload["click"] = os.environ["PAGE_URL"]
    req = urllib.request.Request(NTFY_URL, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        urllib.request.urlopen(req, timeout=30).read()
        print(f"[ntfy] envoyé : {title}")
    except Exception as e:
        print(f"[ntfy] échec : {e!r}")


def decide_notification(prev: list[dict], row: dict) -> tuple[str, str, int, list[str]] | None:
    """Une seule notification par jour, la plus utile. None = silence."""
    cur = row["currency"]
    p = row["price"]
    route = f"{CFG['origin']} → {CFG['destination']}"
    context = f"{route}, {adults_text()}"

    # Combinaison absente
    if p is None:
        streak = 1
        for r in reversed(prev):
            if r["price"] is None:
                streak += 1
            else:
                break
        if streak == CFG.get("missing_alert_after_days", 3):
            return ("Combinaison introuvable",
                    f"{context} : ces vols n'apparaissent plus dans les résultats depuis "
                    f"{streak} jours. Ils sont peut-être complets ou modifiés.", 4, ["warning"])
        return None

    past = [r for r in prev if r["price"] is not None]
    if not past:
        return ("Suivi activé", f"{money(p, cur)} pour {adults_text()} ({route}). "
                f"Je te préviens dès que ça bouge.", 3, ["airplane"])

    last = past[-1]["price"]
    low_row = min(past, key=lambda r: r["price"])
    low = low_row["price"]
    target = CFG.get("target_price")
    since_yesterday = f"{delta(p - last, cur)} par rapport au dernier relevé"

    if target and p <= target < last:
        return (f"Sous ton objectif : {money(p, cur)}",
                f"{context}. Objectif fixé à {money(target, cur)}.", 5, ["tada", "airplane"])
    if p < low:
        return (f"Nouveau plus bas : {money(p, cur)}",
                f"{context}. Ancien plus bas : {money(low, cur)} le {fmt_date(low_row['date'])}.",
                4, ["tada"])
    if last - p >= CFG.get("alert_min_drop", 20):
        return (f"Baisse : {money(p, cur)}", f"{context}. {since_yesterday.capitalize()}.",
                3, ["chart_with_downwards_trend"])
    if row["date"].weekday() == CFG.get("weekly_recap_weekday", 6):
        return (f"Récap de la semaine : {money(p, cur)}",
                f"{context}. {since_yesterday.capitalize()}. Plus bas observé : {money(low, cur)}.",
                2, ["airplane"])
    return None


# --------------------------------------------------------------------------
# Graphique SVG
# --------------------------------------------------------------------------
def nice_step(x: float) -> float:
    e = 10 ** math.floor(math.log10(x))
    f = x / e
    for m in (1, 2, 2.5, 5, 10):
        if f <= m:
            return m * e
    return 10 * e


def chart_svg(rows: list[dict], W: int, H: int, uid: str, nx: int) -> str:
    found = [r for r in rows if r["price"] is not None]
    cur = rows[-1]["currency"] if rows else "EUR"
    label = f"Évolution du prix de {len(found)} relevés"
    if len(found) < 2:
        return (f'<svg viewBox="0 0 {W} {H // 2}" role="img" aria-label="Graphique en attente">'
                f'<text x="{W / 2}" y="{H // 4}" text-anchor="middle" class="empty">'
                f'La courbe apparaîtra dès le deuxième relevé.</text></svg>')

    L, R, T, B = 10, 16, 34, 34
    d0, d1 = rows[0]["date"], rows[-1]["date"]
    span = max((d1 - d0).days, 1)
    target = CFG.get("target_price")
    prices = [r["price"] for r in found] + ([target] if target else [])
    lo, hi = min(prices), max(prices)
    rng = max(hi - lo, hi * 0.02, 1)
    step = nice_step(rng / 4)
    ymin = math.floor((lo - rng * 0.12) / step) * step
    ymax = math.ceil((hi + rng * 0.18) / step) * step

    def X(d: dt.date) -> float:
        return L + (d - d0).days / span * (W - L - R)

    def Y(p: float) -> float:
        return T + (ymax - p) / (ymax - ymin) * (H - T - B)

    base = H - B
    out = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{escape(label)}">',
           f'<defs><linearGradient id="fill{uid}" x1="0" y1="0" x2="0" y2="1">'
           f'<stop offset="0" style="stop-color:var(--line);stop-opacity:.28"/>'
           f'<stop offset="1" style="stop-color:var(--line);stop-opacity:0"/></linearGradient></defs>']

    # grille + repères de prix (posés au-dessus de chaque ligne, côté gauche)
    v = ymin
    while v <= ymax + 1e-9:
        y = Y(v)
        out.append(f'<line class="grid" x1="{L}" x2="{W - R}" y1="{y:.1f}" y2="{y:.1f}"/>')
        if v > ymin:
            out.append(f'<text class="ylab" x="{L}" y="{y - 6:.1f}">{f"{round(v):,}".replace(",", NNBSP)}</text>')
        v += step

    # repères de dates
    for i in range(nx):
        d = d0 + dt.timedelta(days=round(i * span / (nx - 1)))
        anchor = "start" if i == 0 else "end" if i == nx - 1 else "middle"
        out.append(f'<text class="xlab" x="{X(d):.1f}" y="{H - 10}" text-anchor="{anchor}">'
                   f'{fmt_date(d, court=True)}</text>')

    # objectif
    if target:
        y = Y(target)
        out.append(f'<line class="tgt" x1="{L}" x2="{W - R}" y1="{y:.1f}" y2="{y:.1f}"/>')
        out.append(f'<text class="tgtlab" x="{W - R}" y="{y - 7:.1f}" text-anchor="end">'
                   f'objectif {money(target, cur)}</text>')

    # courbe (interrompue aux jours « introuvable »)
    segments, current = [], []
    for r in rows:
        if r["price"] is None:
            if current:
                segments.append(current)
            current = []
        else:
            current.append(r)
    if current:
        segments.append(current)
    for seg in segments:
        pts = [(X(r["date"]), Y(r["price"])) for r in seg]
        if len(pts) >= 2:
            line = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)
            out.append(f'<path d="{line} L{pts[-1][0]:.1f},{base} L{pts[0][0]:.1f},{base} Z" fill="url(#fill{uid})"/>')
            out.append(f'<path class="ln" d="{line}"/>')

    # jours sans la combinaison
    for r in rows:
        if r["price"] is None:
            x = X(r["date"])
            out.append(f'<path class="miss" d="M{x - 3:.1f},{base - 11} L{x + 3:.1f},{base - 5} '
                       f'M{x + 3:.1f},{base - 11} L{x - 3:.1f},{base - 5}">'
                       f'<title>{fmt_date(r["date"])} : combinaison introuvable</title></path>')

    # points + infobulles
    dots = len(found) <= 45
    for r in found:
        x, y = X(r["date"]), Y(r["price"])
        tip = f'{fmt_date(r["date"])} : {money(r["price"], cur)}'
        if dots:
            out.append(f'<circle class="dot" cx="{x:.1f}" cy="{y:.1f}" r="2.6"/>')
        out.append(f'<circle class="hit" cx="{x:.1f}" cy="{y:.1f}" r="9"><title>{tip}</title></circle>')

    # plus bas
    low_r = min(found, key=lambda r: (r["price"], -r["date"].toordinal()))
    lx, ly = X(low_r["date"]), Y(low_r["price"])
    out.append(f'<circle class="lowdot" cx="{lx:.1f}" cy="{ly:.1f}" r="6"/>')
    anchor = "start" if lx < W * 0.3 else "end" if lx > W * 0.7 else "middle"
    # le point le plus bas n'a rien en dessous de lui : l'étiquette y est à l'abri de la courbe
    ty = ly + 26 if ly + 26 < base - 2 else ly - 14
    out.append(f'<text class="lowlab" x="{lx:.1f}" y="{ty:.1f}" text-anchor="{anchor}">'
               f'plus bas {money(low_r["price"], cur)}</text>')

    # dernier relevé
    last_r = found[-1]
    if last_r is not low_r:
        x, y = X(last_r["date"]), Y(last_r["price"])
        out.append(f'<circle class="now" cx="{x:.1f}" cy="{y:.1f}" r="5.5"/>')
    out.append("</svg>")
    return "".join(out)


# --------------------------------------------------------------------------
# Page web
# --------------------------------------------------------------------------
def leg_html(title: str, segs: list[dict]) -> str:
    items = []
    for s in segs:
        dep, arr = s["dep"], s["arr"]
        plus = (d_iso(arr[:10]) - d_iso(dep[:10])).days
        suffix = f"<sup>+{plus}</sup>" if plus > 0 else ""
        items.append(
            f'<li><span class="fl">{escape(s["flight"])}</span>'
            f'<span class="tm">{s["from"]} {dep[11:16]} → {s["to"]} {arr[11:16]}{suffix}</span></li>')
    return f'<section class="leg"><h3>{escape(title)}</h3><ul>{"".join(items)}</ul></section>'


def itinerary_html(info: dict | None) -> str:
    if info:
        out_d = d_iso(info["outbound"][0]["dep"][:10])
        in_d = d_iso(info["inbound"][0]["dep"][:10])
        return (leg_html(f"Aller, {fmt_long(out_d)}", info["outbound"])
                + leg_html(f"Retour, {fmt_long(in_d)}", info["inbound"]))
    out_d, in_d = d_iso(CFG["departure_date"]), d_iso(CFG["return_date"])

    def plain(title: str, flights: list[str]) -> str:
        li = "".join(f'<li><span class="fl">{escape(norm_flight(f))}</span></li>' for f in flights)
        return f'<section class="leg"><h3>{escape(title)}</h3><ul>{li}</ul></section>'
    return (plain(f"Aller, {fmt_long(out_d)}", CFG["outbound_flights"])
            + plain(f"Retour, {fmt_long(in_d)}", CFG["inbound_flights"]))


def hero_texts(rows: list[dict]) -> tuple[str, str, str, str]:
    """(prix affiché, phrase principale, phrase secondaire, classe CSS)"""
    if not rows:
        return "—", "En attente du premier relevé.", "", ""
    found = [r for r in rows if r["price"] is not None]
    last = rows[-1]
    cur = last["currency"]
    if not found:
        return "—", "Combinaison introuvable pour l'instant.", "", ""
    if last["price"] is None:
        k = found[-1]
        return (money(k["price"], cur), "Combinaison introuvable aujourd'hui.",
                f"Dernier prix connu, relevé le {fmt_date(k['date'])}.", "")
    p = last["price"]
    low_r = min(found, key=lambda r: (r["price"], -r["date"].toordinal()))
    if len(found) == 1:
        return money(p, cur), "Premier relevé.", "Les variations s'afficheront dès demain.", ""
    if p <= low_r["price"]:
        main = "C'est le prix le plus bas relevé."
        cls = "low"
    else:
        main = f"{money(p - low_r['price'], cur)} au-dessus du plus bas, relevé le {fmt_date(low_r['date'])}."
        cls = ""
    week_ago = [r for r in found if r["date"] <= last["date"] - dt.timedelta(days=7)]
    trend = ""
    if week_ago:
        d = p - week_ago[-1]["price"]
        trend = f"{delta(d, cur).capitalize()} en 7 jours." if round(d) else "Stable sur 7 jours."
    return money(p, cur), main, trend, cls


def build_page(rows: list[dict], info: dict | None, demo: bool = False) -> str:
    cur = rows[-1]["currency"] if rows else "EUR"
    found = [r for r in rows if r["price"] is not None]
    price_txt, main_txt, sub_txt, cls = hero_texts(rows)
    day = rows[-1]["date"] if rows else today()
    dep = d_iso(CFG["departure_date"])
    ret = d_iso(CFG["return_date"])

    if found:
        low_r = min(found, key=lambda r: (r["price"], -r["date"].toordinal()))
        high_r = max(found, key=lambda r: r["price"])
        mean = sum(r["price"] for r in found) / len(found)
        stats = [
            ("Plus bas", money(low_r["price"], cur), fmt_date(low_r["date"])),
            ("Plus haut", money(high_r["price"], cur), fmt_date(high_r["date"])),
            ("Moyenne", money(mean, cur), f'{len(found)} relevé{"s" if len(found) > 1 else ""}'),
            ("Départ dans", f'{max((dep - day).days, 0)} jours', fmt_date(dep, annee=True)),
        ]
    else:
        stats = [("Départ dans", f'{max((dep - day).days, 0)} jours', fmt_date(dep, annee=True))]
    stats_html = "".join(f"<div><dt>{a}</dt><dd>{b}</dd><dd class='n'>{c}</dd></div>" for a, b, c in stats)

    subtitle = (f"{fmt_date(dep)} → {fmt_date(ret, annee=True)}, {adults_text()}, "
                f"{'économie' if CFG.get('cabin_class', 'economy') == 'economy' else CFG.get('cabin_class')}")
    updated = f"Dernier relevé le {fmt_date(day, annee=True)}." if rows else ""
    banner = '<p class="demo">Données de démonstration, ce ne sont pas de vrais prix.</p>' if demo else ""

    page = TEMPLATE
    for k, v in {
        "%%TITLE%%": f"{CFG['origin']} → {CFG['destination']}",
        "%%H1%%": "Genève → Pointe-à-Pitre" if (CFG["origin"], CFG["destination"]) == ("GVA", "PTP")
                  else f"{CFG['origin']} → {CFG['destination']}",
        "%%SUBTITLE%%": escape(subtitle),
        "%%BANNER%%": banner,
        "%%PRICE%%": price_txt,
        "%%VERDICT_CLASS%%": cls,
        "%%MAIN%%": escape(main_txt),
        "%%SUB%%": escape(sub_txt),
        "%%CHART_DESKTOP%%": chart_svg(rows, 880, 340, "d", 6),
        "%%CHART_MOBILE%%": chart_svg(rows, 420, 330, "m", 4),
        "%%STATS%%": stats_html,
        "%%LEGS%%": itinerary_html(info),
        "%%UPDATED%%": escape(updated),
    }.items():
        page = page.replace(k, v)
    return page


TEMPLATE = """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<meta name="robots" content="noindex, nofollow">
<title>%%TITLE%%</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,400..800&display=swap" rel="stylesheet">
<style>
:root{
  --bg:#E7F0EF; --surface:#F7FBFA; --ink:#0B2830; --muted:#4F6E76;
  --line:#0C7A8A; --accent:#C8265F; --grid:#CADBDA;
}
@media (prefers-color-scheme: dark){
  :root{ --bg:#08171D; --surface:#0E2530; --ink:#E4F1F1; --muted:#8CB0B6;
         --line:#4CC3D2; --accent:#FF7AA2; --grid:#1D3843; }
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:400 1rem/1.5 "Bricolage Grotesque",ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:920px;margin:0 auto;padding:clamp(20px,5vw,52px) clamp(16px,4vw,32px) 56px}
h1{font-size:clamp(1.15rem,2.6vw,1.35rem);font-weight:600;margin:0;letter-spacing:-.005em}
.meta{margin:2px 0 0;color:var(--muted)}
.demo{display:inline-block;margin:14px 0 0;padding:4px 10px;border:1px solid var(--accent);
  border-radius:6px;color:var(--accent);font-weight:600;font-size:.9rem}
.hero{margin:clamp(28px,6vw,56px) 0 clamp(20px,4vw,32px)}
.price{margin:0;font-size:clamp(3.6rem,15vw,7.4rem);font-weight:800;line-height:.92;
  letter-spacing:-.035em;font-variant-numeric:tabular-nums}
.verdict{margin:14px 0 0;font-size:1.2rem;font-weight:600}
.verdict.low{color:var(--accent)}
.sub{margin:2px 0 0;color:var(--muted)}
.chart{background:var(--surface);border-radius:16px;padding:clamp(10px,2vw,18px) clamp(8px,2vw,16px) 6px}
.chart svg{display:block;width:100%;height:auto;overflow:visible}
.chart.mobile{display:none}
.grid{stroke:var(--grid);stroke-width:1}
.ylab,.xlab{fill:var(--muted);font-size:13px;font-variant-numeric:tabular-nums}
.ylab{paint-order:stroke;stroke:var(--surface);stroke-width:4px;stroke-linejoin:round}
.ln{fill:none;stroke:var(--line);stroke-width:2.6;stroke-linejoin:round;stroke-linecap:round}
.dot{fill:var(--surface);stroke:var(--line);stroke-width:1.8}
.hit{fill:transparent}
.hit:hover{fill:var(--line);fill-opacity:.18}
.lowdot{fill:var(--accent);stroke:var(--surface);stroke-width:2.5}
.lowlab{fill:var(--accent);font-size:14px;font-weight:700;paint-order:stroke;
  stroke:var(--surface);stroke-width:5px;stroke-linejoin:round}
.now{fill:var(--line);stroke:var(--surface);stroke-width:2.5}
.tgt{stroke:var(--accent);stroke-width:1.5;stroke-dasharray:5 5}
.tgtlab{fill:var(--accent);font-size:13px;font-weight:600}
.miss{fill:none;stroke:var(--muted);stroke-width:1.6;stroke-linecap:round}
.empty{fill:var(--muted);font-size:15px}
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:16px 24px;margin:28px 0 0;padding:20px 0 0;
  border-top:1px solid var(--grid)}
.stats dt{color:var(--muted);font-size:.9rem}
.stats dd{margin:0;font-size:1.45rem;font-weight:600;letter-spacing:-.01em;font-variant-numeric:tabular-nums}
.stats dd.n{font-size:.9rem;font-weight:400;color:var(--muted);letter-spacing:0}
.legs{display:grid;grid-template-columns:1fr 1fr;gap:20px 40px;margin-top:36px}
.leg h3{margin:0 0 6px;font-size:1rem;font-weight:600}
.leg ul{list-style:none;margin:0;padding:0}
.leg li{display:flex;gap:14px;align-items:baseline;padding:7px 0;border-top:1px solid var(--grid)}
.fl{font-weight:700;min-width:5.2ch}
.tm{color:var(--muted);font-variant-numeric:tabular-nums}
.tm sup{font-size:.7em;margin-left:2px}
footer{margin-top:40px;color:var(--muted);font-size:.88rem;max-width:62ch}
footer p{margin:0 0 4px}
@media (max-width:640px){
  .chart.desktop{display:none}.chart.mobile{display:block}
  .stats{grid-template-columns:1fr 1fr}
  .legs{grid-template-columns:1fr}
  .ylab,.xlab{font-size:14px}
}
</style>
</head>
<body>
<main>
  <header>
    <h1>%%H1%%</h1>
    <p class="meta">%%SUBTITLE%%</p>
    %%BANNER%%
  </header>

  <section class="hero" aria-live="polite">
    <p class="price">%%PRICE%%</p>
    <p class="verdict %%VERDICT_CLASS%%">%%MAIN%%</p>
    <p class="sub">%%SUB%%</p>
  </section>

  <div class="chart desktop">%%CHART_DESKTOP%%</div>
  <div class="chart mobile">%%CHART_MOBILE%%</div>

  <dl class="stats">%%STATS%%</dl>

  <div class="legs">%%LEGS%%</div>

  <footer>
    <p>%%UPDATED%%</p>
    <p>Prix relevé une fois par jour. Les tarifs bougent en continu : vérifie le prix final sur le site de la compagnie avant d'acheter.</p>
  </footer>
</main>
</body>
</html>
"""


def render(rows: list[dict], demo: bool = False, info: dict | None = None, out: Path | None = None) -> None:
    out = out or PAGE
    if info is None and not demo and LAST_ITINERARY.exists():
        info = json.loads(LAST_ITINERARY.read_text(encoding="utf-8"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_page(rows, info, demo), encoding="utf-8")
    print(f"Page écrite : {out}")


# --------------------------------------------------------------------------
# Démo
# --------------------------------------------------------------------------
def demo_rows() -> tuple[list[dict], dict]:
    random.seed(11)
    n, p, rows = 62, 2140.0, []
    start = today() - dt.timedelta(days=n - 1)
    for i in range(n):
        p += random.choice([-1, -1, 0, 1, 1, 2]) * random.randint(6, 34)
        p = max(1480, min(2500, p))
        if i == 39:
            p -= 140
        miss = i in (17, 18)
        rows.append({"date": start + dt.timedelta(days=i), "price": None if miss else round(p),
                     "currency": "EUR", "cheapest_any": None if miss else round(p - 60)})
    info = {
        "outbound": [
            {"flight": "AF1843", "from": "GVA", "to": "CDG", "dep": "2027-01-21T12:05:00", "arr": "2027-01-21T13:20:00"},
            {"flight": "AF758", "from": "CDG", "to": "PTP", "dep": "2027-01-21T15:50:00", "arr": "2027-01-21T18:35:00"}],
        "inbound": [
            {"flight": "AF751", "from": "PTP", "to": "CDG", "dep": "2027-02-07T16:30:00", "arr": "2027-02-08T05:45:00"},
            {"flight": "AF1542", "from": "CDG", "to": "GVA", "dep": "2027-02-08T07:10:00", "arr": "2027-02-08T08:20:00"}],
    }
    return rows, info


# --------------------------------------------------------------------------
# Programme principal
# --------------------------------------------------------------------------
def run() -> None:
    day = today()
    if day > d_iso(CFG["departure_date"]):
        print("La date de départ est passée : le suivi est terminé.")
        render(load_history())
        return

    key = os.environ.get("IGNAV_API_KEY")
    if not key:
        sys.exit("Variable IGNAV_API_KEY manquante (voir README, étape « secrets »).")

    try:
        itineraries = fetch_itineraries(key)
    except RuntimeError as e:
        notify("Relevé impossible", f"L'appel à l'API a échoué : {str(e)[:180]}", 3, ["warning"])
        sys.exit(f"Erreur API : {e}")

    match = find_match(itineraries)
    history = [r for r in load_history() if r["date"] != day]
    currency = (match or (itineraries[0] if itineraries else {})).get("price", {}).get("currency") \
        or (history[-1]["currency"] if history else "EUR")
    cheapest = min((float(i["price"]["amount"]) for i in itineraries), default=None)

    row = {"date": day,
           "price": float(match["price"]["amount"]) if match else None,
           "currency": currency, "cheapest_any": cheapest}
    print(f"{len(itineraries)} itinéraires reçus ; combinaison "
          f"{'trouvée : ' + money(row['price'], currency) if match else 'INTROUVABLE'}.")

    rows = history + [row]
    save_history(rows)
    if match:
        info = slim(match)
        LAST_ITINERARY.write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    render(rows)

    alert = decide_notification(history, row)
    if alert:
        notify(*alert)
    else:
        print("Rien de notable aujourd'hui : pas de notification.")


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--demo":
        r, i = demo_rows()
        render(r, demo=True, info=i, out=Path(sys.argv[2]) if len(sys.argv) > 2 else Path("demo.html"))
    elif arg == "--render":
        render(load_history())
    else:
        run()
