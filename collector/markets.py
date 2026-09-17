"""
Chiffres de marché et taux de référence.

Toutes les sources ici sont publiques et sans clé d'API :
  - BCE (data-api.ecb.europa.eu) : taux directeurs, Euribor, taux longs
  - Yahoo Finance (endpoint public) puis Stooq en secours : indices
  - CoinGecko : crypto
  - Frankfurter : change

Aucune n'est garantie éternelle. Chaque indicateur échoue seul : s'il
tombe, les autres passent quand même et le tableau de bord affiche
« indisponible » à sa place plutôt que de casser.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone

import requests

TIMEOUT = 20
UA = "veille-financiere/1.0 (usage personnel)"


def _get(url: str, **kw) -> requests.Response:
    headers = {"User-Agent": UA, "Accept": "*/*"}
    headers.update(kw.pop("headers", {}))
    r = requests.get(url, headers=headers, timeout=TIMEOUT, **kw)
    r.raise_for_status()
    return r


def _indicateur(id_, label, unite, groupe):
    return {
        "id": id_,
        "label": label,
        "unite": unite,
        "groupe": groupe,
        "valeur": None,
        "variation": None,
        "variation_pct": None,
        "date": None,
        "source": None,
        "statut": "indisponible",
        "erreur": None,
    }


# ---------------------------------------------------------------------
# BCE — taux directeurs, Euribor, taux longs
# ---------------------------------------------------------------------

def _serie_bce(cle: str, n: int = 2):
    """Renvoie [(date, valeur), ...] du plus ancien au plus récent."""
    url = (
        f"https://data-api.ecb.europa.eu/service/data/{cle}"
        f"?lastNObservations={n}&format=csvdata"
    )
    texte = _get(url).text
    lignes = list(csv.DictReader(io.StringIO(texte)))
    points = []
    for l in lignes:
        valeur = l.get("OBS_VALUE")
        periode = l.get("TIME_PERIOD")
        if valeur in (None, "", "NaN"):
            continue
        points.append((periode, float(valeur)))
    points.sort(key=lambda p: p[0])
    return points


def _ajouter_bce(ind, cle, source):
    points = _serie_bce(cle)
    if not points:
        raise ValueError("série vide")
    date, valeur = points[-1]
    ind["valeur"] = round(valeur, 3)
    ind["date"] = date
    ind["source"] = source
    ind["statut"] = "ok"
    if len(points) > 1:
        ind["variation"] = round(valeur - points[-2][1], 3)
    return ind


def taux_bce():
    """Taux de la facilité de dépôt + refinancement + Euribor 3 mois + OAT 10 ans."""
    definitions = [
        ("bce_depot", "Taux de dépôt BCE", "FM/D.U2.EUR.4F.KR.DFR.LEV", "BCE"),
        ("bce_refi", "Taux de refinancement BCE", "FM/D.U2.EUR.4F.KR.MRR_FR.LEV", "BCE"),
        ("euribor_3m", "Euribor 3 mois", "FM/D.U2.EUR.RT.MM.EURIBOR3MD_.HSTA", "BCE"),
        ("oat_10a", "Taux long France 10 ans", "IRS/M.FR.L.L40.CI.0000.EUR.N.Z", "BCE"),
    ]
    sortie = []
    for id_, label, cle, source in definitions:
        ind = _indicateur(id_, label, "%", "taux")
        try:
            _ajouter_bce(ind, cle, source)
        except Exception as e:  # noqa: BLE001
            ind["erreur"] = f"{type(e).__name__}: {e}"
        sortie.append(ind)
    return sortie


# ---------------------------------------------------------------------
# Indices actions — Yahoo puis Stooq en secours
# ---------------------------------------------------------------------

INDICES = [
    ("cac40", "CAC 40", "^FCHI", "^cac"),
    ("sp500", "S&P 500", "^GSPC", "^spx"),
    ("nasdaq", "Nasdaq 100", "^NDX", "^ndx"),
    ("stoxx600", "Stoxx Europe 600", "^STOXX", "^stoxx"),
]


def _yahoo(symbole):
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbole}"
        "?range=5d&interval=1d"
    )
    data = _get(url).json()
    res = data["chart"]["result"][0]
    meta = res["meta"]
    cours = meta.get("regularMarketPrice")
    veille = meta.get("chartPreviousClose") or meta.get("previousClose")
    horodatage = meta.get("regularMarketTime")
    date = (
        datetime.fromtimestamp(horodatage, tz=timezone.utc).date().isoformat()
        if horodatage
        else None
    )
    if cours is None:
        raise ValueError("pas de cours")
    return float(cours), (float(veille) if veille else None), date, "Yahoo Finance"


def _stooq(symbole):
    url = f"https://stooq.com/q/l/?s={symbole}&f=sd2t2ohlcvp&h&e=csv"
    lignes = list(csv.DictReader(io.StringIO(_get(url).text)))
    if not lignes:
        raise ValueError("csv vide")
    l = lignes[0]
    if l.get("Close") in (None, "", "N/D"):
        raise ValueError("cours indisponible")
    cours = float(l["Close"])
    ouverture = float(l["Open"]) if l.get("Open") not in (None, "", "N/D") else None
    return cours, ouverture, l.get("Date"), "Stooq"


def indices():
    sortie = []
    for id_, label, sym_yahoo, sym_stooq in INDICES:
        ind = _indicateur(id_, label, "pts", "marches")
        erreurs = []
        for recuperer, symbole in ((_yahoo, sym_yahoo), (_stooq, sym_stooq)):
            try:
                cours, reference, date, source = recuperer(symbole)
                ind["valeur"] = round(cours, 2)
                ind["date"] = date
                ind["source"] = source
                ind["statut"] = "ok"
                if reference:
                    ind["variation"] = round(cours - reference, 2)
                    ind["variation_pct"] = round((cours / reference - 1) * 100, 2)
                break
            except Exception as e:  # noqa: BLE001
                erreurs.append(f"{symbole}: {type(e).__name__}")
        if ind["statut"] != "ok":
            ind["erreur"] = " / ".join(erreurs)
        sortie.append(ind)
    return sortie


# ---------------------------------------------------------------------
# Crypto — CoinGecko
# ---------------------------------------------------------------------

CRYPTOS = [("bitcoin", "btc", "Bitcoin"), ("ethereum", "eth", "Ethereum")]


def crypto():
    sortie = []
    ids = ",".join(c[0] for c in CRYPTOS)
    donnees = None
    erreur = None
    try:
        url = (
            "https://api.coingecko.com/api/v3/simple/price"
            f"?ids={ids}&vs_currencies=eur&include_24hr_change=true"
        )
        donnees = _get(url).json()
    except Exception as e:  # noqa: BLE001
        erreur = f"{type(e).__name__}: {e}"

    for cg_id, court, label in CRYPTOS:
        ind = _indicateur(court, label, "€", "crypto")
        if donnees and cg_id in donnees:
            bloc = donnees[cg_id]
            ind["valeur"] = round(bloc["eur"], 2)
            ind["variation_pct"] = round(bloc.get("eur_24h_change") or 0, 2)
            ind["date"] = datetime.now(timezone.utc).date().isoformat()
            ind["source"] = "CoinGecko"
            ind["statut"] = "ok"
        else:
            ind["erreur"] = erreur or "absent de la réponse"
        sortie.append(ind)
    return sortie


# ---------------------------------------------------------------------
# Change — Frankfurter (BCE en source)
# ---------------------------------------------------------------------

def change():
    ind = _indicateur("eurusd", "EUR / USD", "$", "marches")
    for base in ("https://api.frankfurter.dev/v1", "https://api.frankfurter.app"):
        try:
            data = _get(f"{base}/latest?base=EUR&symbols=USD").json()
            ind["valeur"] = round(data["rates"]["USD"], 4)
            ind["date"] = data.get("date")
            ind["source"] = "Frankfurter (BCE)"
            ind["statut"] = "ok"
            return [ind]
        except Exception as e:  # noqa: BLE001
            ind["erreur"] = f"{type(e).__name__}: {e}"
    return [ind]


def tous_les_indicateurs():
    indicateurs = []
    for bloc in (taux_bce, indices, crypto, change):
        try:
            indicateurs.extend(bloc())
        except Exception as e:  # noqa: BLE001
            print(f"  ! bloc {bloc.__name__} en échec : {e}")
    return indicateurs


if __name__ == "__main__":
    print(json.dumps(tous_les_indicateurs(), ensure_ascii=False, indent=2))
