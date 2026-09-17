"""
Récupération de tous les chiffres décrits dans collector/indicateurs.yaml.

Aucune clé d'API nulle part. Chaque indicateur est récupéré isolément :
s'il échoue, il s'affiche « indisponible » et les autres passent quand même.

Sources :
  bce      data-api.ecb.europa.eu (taux directeurs, Euribor, souverain, IPCH, chômage)
  fed      markets.newyorkfed.org (EFFR, SOFR)
  boe      bankofengland.co.uk (bank rate)
  marche   query1.finance.yahoo.com, secours stooq.com
  change   api.frankfurter.dev
  crypto   api.coingecko.com (euro et dollar dans le même appel)
  global   api.coingecko.com/global
  peur     api.alternative.me/fng
  manuel   valeur écrite dans le fichier de configuration
  calcule  différence entre deux autres indicateurs
"""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime, timedelta, timezone

import requests

TIMEOUT = 25
UA = "veille-financiere/2.0 (usage personnel)"


def _get(url, headers=None):
    entetes = {"User-Agent": UA, "Accept": "*/*"}
    if headers:
        entetes.update(headers)
    r = requests.get(url, headers=entetes, timeout=TIMEOUT)
    r.raise_for_status()
    return r


def _neuf(conf):
    return {
        "id": conf["id"],
        "label": conf["label"],
        "groupe": conf["groupe"],
        "unite": conf.get("unite"),
        "note": conf.get("note"),
        "valeur": None,
        "valeur_usd": None,
        "variation": None,
        "variation_pct": None,
        "date": None,
        "source": None,
        "statut": "indisponible",
        "erreur": None,
    }


# ---------------------------------------------------------------------
# BCE
# ---------------------------------------------------------------------

def _bce(conf, ind):
    url = (
        f"https://data-api.ecb.europa.eu/service/data/{conf['cle']}"
        "?lastNObservations=2&format=csvdata"
    )
    lignes = list(csv.DictReader(io.StringIO(_get(url).text)))
    points = [
        (l["TIME_PERIOD"], float(l["OBS_VALUE"]))
        for l in lignes
        if l.get("OBS_VALUE") not in (None, "", "NaN")
    ]
    if not points:
        raise ValueError("série vide")
    points.sort(key=lambda p: p[0])
    ind["valeur"] = round(points[-1][1], 3)
    ind["date"] = points[-1][0]
    ind["source"] = "BCE"
    if len(points) > 1:
        ind["variation"] = round(points[-1][1] - points[-2][1], 3)


# ---------------------------------------------------------------------
# Fed de New York
# ---------------------------------------------------------------------

def _fed(conf, ind):
    url = f"https://markets.newyorkfed.org/api/rates/{conf['cle']}/last/2.json"
    taux = _get(url).json().get("refRates", [])
    if not taux:
        raise ValueError("aucun taux renvoyé")
    taux.sort(key=lambda t: t["effectiveDate"])
    ind["valeur"] = round(float(taux[-1]["percentRate"]), 3)
    ind["date"] = taux[-1]["effectiveDate"]
    ind["source"] = "Fed de New York"
    if len(taux) > 1:
        ind["variation"] = round(
            float(taux[-1]["percentRate"]) - float(taux[-2]["percentRate"]), 3
        )


# ---------------------------------------------------------------------
# Banque d'Angleterre
# ---------------------------------------------------------------------

def _boe(conf, ind):
    depuis = (datetime.now(timezone.utc) - timedelta(days=400)).strftime("%d/%b/%Y")
    url = (
        "https://www.bankofengland.co.uk/boeapps/iadb/fromshowcolumns.asp"
        f"?csv.x=yes&Datefrom={depuis}&Dateto=now&SeriesCodes={conf['cle']}"
        "&CSVF=TN&UsingCodes=Y&VPD=Y&VFD=N"
    )
    lignes = list(csv.reader(io.StringIO(_get(url).text)))
    points = []
    for l in lignes[1:]:
        if len(l) >= 2:
            try:
                points.append((l[0], float(l[1])))
            except ValueError:
                continue
    if not points:
        raise ValueError("csv vide")
    ind["valeur"] = round(points[-1][1], 3)
    ind["date"] = points[-1][0]
    ind["source"] = "Banque d'Angleterre"
    if len(points) > 1:
        ind["variation"] = round(points[-1][1] - points[-2][1], 3)


# ---------------------------------------------------------------------
# Marchés : Yahoo, secours Stooq
# ---------------------------------------------------------------------

def _yahoo(symbole):
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbole}"
        "?range=5d&interval=1d"
    )
    meta = _get(url).json()["chart"]["result"][0]["meta"]
    cours = meta.get("regularMarketPrice")
    if cours is None:
        raise ValueError("pas de cours")
    reference = meta.get("chartPreviousClose") or meta.get("previousClose")
    horodatage = meta.get("regularMarketTime")
    date = (
        datetime.fromtimestamp(horodatage, tz=timezone.utc).date().isoformat()
        if horodatage
        else None
    )
    return float(cours), (float(reference) if reference else None), date, "Yahoo Finance"


def _stooq(symbole):
    url = f"https://stooq.com/q/l/?s={symbole}&f=sd2t2ohlcv&h&e=csv"
    lignes = list(csv.DictReader(io.StringIO(_get(url).text)))
    if not lignes or lignes[0].get("Close") in (None, "", "N/D"):
        raise ValueError("cours indisponible")
    l = lignes[0]
    ouverture = None
    if l.get("Open") not in (None, "", "N/D"):
        ouverture = float(l["Open"])
    return float(l["Close"]), ouverture, l.get("Date"), "Stooq"


def _marche(conf, ind):
    erreurs = []
    tentatives = [(_yahoo, conf["cle"])]
    if conf.get("secours"):
        tentatives.append((_stooq, conf["secours"]))
    for recuperer, symbole in tentatives:
        try:
            cours, reference, date, source = recuperer(symbole)
            ind["valeur"] = round(cours, 2)
            ind["date"] = date
            ind["source"] = source
            if reference:
                ind["variation"] = round(cours - reference, 2)
                ind["variation_pct"] = round((cours / reference - 1) * 100, 2)
            return
        except Exception as e:  # noqa: BLE001
            erreurs.append(f"{symbole}: {type(e).__name__}")
    raise ValueError(" / ".join(erreurs))


# ---------------------------------------------------------------------
# Change
# ---------------------------------------------------------------------

_cache_change = {}


def _change(conf, ind):
    if not _cache_change:
        for base in ("https://api.frankfurter.dev/v1", "https://api.frankfurter.app"):
            try:
                data = _get(f"{base}/latest?base=EUR").json()
                _cache_change.update(data["rates"])
                _cache_change["__date__"] = data.get("date")
                break
            except Exception:  # noqa: BLE001
                continue
    if conf["cle"] not in _cache_change:
        raise ValueError("devise absente")
    ind["valeur"] = round(_cache_change[conf["cle"]], 4)
    ind["date"] = _cache_change.get("__date__")
    ind["source"] = "Frankfurter (BCE)"


# ---------------------------------------------------------------------
# Crypto
# ---------------------------------------------------------------------

_cache_crypto = {}
_cache_global = {}


def _charger_crypto(identifiants):
    if _cache_crypto or not identifiants:
        return
    url = (
        "https://api.coingecko.com/api/v3/simple/price"
        f"?ids={','.join(identifiants)}&vs_currencies=eur,usd&include_24hr_change=true"
    )
    _cache_crypto.update(_get(url).json())


def _crypto(conf, ind):
    bloc = _cache_crypto.get(conf["cle"])
    if not bloc:
        raise ValueError("absent de la réponse CoinGecko")
    ind["valeur"] = round(bloc["eur"], 2)
    ind["valeur_usd"] = round(bloc["usd"], 2)
    ind["unite"] = "€"
    ind["variation_pct"] = round(bloc.get("eur_24h_change") or 0, 2)
    ind["date"] = datetime.now(timezone.utc).date().isoformat()
    ind["source"] = "CoinGecko"


def _charger_global():
    if _cache_global:
        return
    _cache_global.update(_get("https://api.coingecko.com/api/v3/global").json()["data"])


def _global(conf, ind):
    _charger_global()
    if conf["cle"] == "market_cap":
        ind["valeur"] = round(_cache_global["total_market_cap"]["eur"] / 1e9, 1)
        ind["valeur_usd"] = round(_cache_global["total_market_cap"]["usd"] / 1e9, 1)
        ind["unite"] = "Md €"
        ind["variation_pct"] = round(
            _cache_global.get("market_cap_change_percentage_24h_usd") or 0, 2
        )
    else:
        ind["valeur"] = round(_cache_global["market_cap_percentage"]["btc"], 1)
        ind["unite"] = "%"
    ind["date"] = datetime.now(timezone.utc).date().isoformat()
    ind["source"] = "CoinGecko"


def _peur(conf, ind):
    data = _get("https://api.alternative.me/fng/?limit=2").json()["data"]
    ind["valeur"] = float(data[0]["value"])
    ind["note"] = data[0].get("value_classification") or ind.get("note")
    ind["date"] = datetime.now(timezone.utc).date().isoformat()
    ind["source"] = "Alternative.me"
    if len(data) > 1:
        ind["variation"] = float(data[0]["value"]) - float(data[1]["value"])


# ---------------------------------------------------------------------

def _manuel(conf, ind):
    ind["valeur"] = float(conf["valeur"])
    date = conf.get("date")
    ind["date"] = date.isoformat() if hasattr(date, "isoformat") else date
    ind["source"] = "saisi à la main"


RECUPERATEURS = {
    "bce": _bce,
    "fed": _fed,
    "boe": _boe,
    "marche": _marche,
    "change": _change,
    "crypto": _crypto,
    "global": _global,
    "peur": _peur,
    "manuel": _manuel,
}


def _calculer(conf, resultats, ind):
    correspondance = re.match(r"\s*(\w+)\s*-\s*(\w+)\s*$", conf.get("formule", ""))
    if not correspondance:
        raise ValueError("formule non reconnue")
    a, b = correspondance.group(1), correspondance.group(2)
    gauche, droite = resultats.get(a), resultats.get(b)
    if not gauche or not droite or gauche["valeur"] is None or droite["valeur"] is None:
        raise ValueError(f"{a} ou {b} indisponible")
    ind["valeur"] = round(gauche["valeur"] - droite["valeur"], 2)
    ind["date"] = gauche["date"]
    ind["source"] = "calculé"


def tous_les_indicateurs(config, verbeux=True):
    actifs = [i for i in config["indicateurs"] if i.get("actif", True)]

    identifiants = [i["cle"] for i in actifs if i["source"] == "crypto"]
    if identifiants:
        try:
            _charger_crypto(identifiants)
        except Exception as e:  # noqa: BLE001
            if verbeux:
                print(f"  ! CoinGecko injoignable : {e}")

    resultats = {}
    ordre = [i for i in actifs if i["source"] != "calcule"]
    ordre += [i for i in actifs if i["source"] == "calcule"]

    for conf in ordre:
        ind = _neuf(conf)
        try:
            if conf["source"] == "calcule":
                _calculer(conf, resultats, ind)
            else:
                RECUPERATEURS[conf["source"]](conf, ind)
            ind["statut"] = "ok"
        except Exception as e:  # noqa: BLE001
            ind["erreur"] = f"{type(e).__name__}: {e}"
        resultats[conf["id"]] = ind
        if verbeux:
            marque = "ok   " if ind["statut"] == "ok" else "ÉCHEC"
            detail = ind["valeur"] if ind["statut"] == "ok" else ind["erreur"][:70]
            print(f"  {marque}  {conf['label']:<36} {detail}")

    return [resultats[i["id"]] for i in actifs]


if __name__ == "__main__":
    from pathlib import Path

    import yaml

    chemin = Path(__file__).resolve().parent / "indicateurs.yaml"
    print(json.dumps(
        tous_les_indicateurs(yaml.safe_load(chemin.read_text(encoding="utf-8"))),
        ensure_ascii=False, indent=2,
    ))
