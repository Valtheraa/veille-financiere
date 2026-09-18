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
import os
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
        "pays": conf["pays"],
        "theme": conf["theme"],
        "mots": conf.get("mots"),
        "revision": conf.get("revision"),
        "unite": conf.get("unite"),
        "note": conf.get("note"),
        "valeur": None,
        "valeur_usd": None,
        "variation": None,
        "variation_pct": None,
        "date": None,
        "source": None,
        "statut": "indisponible",
        "repli": False,
        "ecart_jours": None,
        "ambigu": None,
        "erreur": None,
    }


# ---------------------------------------------------------------------
# BCE
# ---------------------------------------------------------------------

def _bce(conf, ind):
    """
    Une clé BCE peut désigner plusieurs séries à la fois (corrigée ou non des
    variations saisonnières, par exemple). Le format CSV les renvoie toutes à
    la suite : il faut les séparer avant de lire la dernière valeur, sinon on
    mélange deux séries et on affiche une observation vieille de neuf mois.
    """
    url = (
        f"https://data-api.ecb.europa.eu/service/data/{conf['cle']}"
        "?lastNObservations=3&format=csvdata"
    )
    lignes = list(csv.DictReader(io.StringIO(_get(url).text)))
    if not lignes:
        raise ValueError("réponse vide")

    colonne_serie = next((c for c in lignes[0] if c in ("KEY", "SERIES_KEY", "SERIES")), None)
    series = {}
    for l in lignes:
        if l.get("OBS_VALUE") in (None, "", "NaN"):
            continue
        identifiant = l.get(colonne_serie) if colonne_serie else "unique"
        series.setdefault(identifiant, []).append((l["TIME_PERIOD"], float(l["OBS_VALUE"])))
    if not series:
        raise ValueError("série vide")

    # La clé demandée, écrite comme la BCE l'écrit dans ses réponses.
    demandee = conf["cle"].replace("/", ".")
    exacte = next((k for k in series if k and k.upper() == demandee.upper()), None)
    if exacte:
        points = sorted(series[exacte])
    else:
        # Pas de correspondance exacte : on prend la plus fraîche et on le signale,
        # car c'est le signe d'une clé trop large qu'il faudra préciser.
        points = sorted(max(series.values(), key=lambda pts: max(p[0] for p in pts)))
        if len(series) > 1:
            ind["ambigu"] = len(series)

    ind["valeur"] = round(points[-1][1], 3)
    ind["date"] = points[-1][0]
    ind["source"] = "BCE"
    if len(points) > 1:
        ind["variation"] = round(points[-1][1] - points[-2][1], 3)
        ind["ecart_jours"] = _ecart_en_jours(points[-2][0], points[-1][0])


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
# Eurostat
# ---------------------------------------------------------------------

def _eurostat(conf, ind):
    """
    Eurostat publie l'inflation, le chômage, le PIB et la dette de tous les pays
    européens, gratuitement et sans clé. C'est la source de référence : les
    séries équivalentes de la BCE se sont révélées arrêtées ou introuvables.
    Le format renvoyé est du JSON-stat : les valeurs sont indexées par position.
    """
    url = ("https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/"
           f"{conf['cle']}?format=JSON&lang=FR&lastTimePeriod=3&{conf.get('filtres', '')}")
    data = _get(url).json()

    periodes = data.get("dimension", {}).get("time", {}).get("category", {}).get("index", {})
    valeurs = data.get("value", {})
    if not periodes or not valeurs:
        raise ValueError("réponse sans observation (filtres trop larges ou trop stricts ?)")

    points = []
    for periode, position in periodes.items():
        valeur = valeurs.get(str(position), valeurs.get(position))
        if valeur is not None:
            points.append((periode, float(valeur)))
    if not points:
        raise ValueError("aucune valeur exploitable")

    points.sort(key=lambda p: p[0])
    ind["valeur"] = round(points[-1][1], 3)
    ind["date"] = points[-1][0]
    ind["source"] = "Eurostat"
    if len(points) > 1:
        ind["variation"] = round(points[-1][1] - points[-2][1], 3)
        ind["ecart_jours"] = _ecart_en_jours(points[-2][0], points[-1][0])


# ---------------------------------------------------------------------
# Autres banques centrales
# ---------------------------------------------------------------------

def _boc(conf, ind):
    """Banque du Canada. Son API « Valet » est ouverte, sans clé."""
    url = f"https://www.bankofcanada.ca/valet/observations/{conf['cle']}/json?recent=3"
    observations = _get(url).json().get("observations", [])
    points = []
    for o in observations:
        bloc = o.get(conf["cle"])
        if bloc and bloc.get("v") not in (None, ""):
            points.append((o["d"], float(bloc["v"])))
    if not points:
        raise ValueError("série vide")
    points.sort(key=lambda p: p[0])
    ind["valeur"] = round(points[-1][1], 3)
    ind["date"] = points[-1][0]
    ind["source"] = "Banque du Canada"
    if len(points) > 1:
        ind["variation"] = round(points[-1][1] - points[-2][1], 3)
        ind["ecart_jours"] = _ecart_en_jours(points[-2][0], points[-1][0])


def _snb(conf, ind):
    """Banque nationale suisse. Portail de données ouvert, format CSV à points-virgules."""
    url = f"https://data.snb.ch/api/cube/{conf['cle']}/data/csv/fr"
    points = []
    for ligne in _get(url).text.splitlines():
        morceaux = [m.strip().strip('"') for m in re.split(r"[;,\t]", ligne)]
        date = next((m for m in morceaux if re.match(r"^\d{4}(-\d{2}){0,2}$", m)), None)
        if not date:
            continue
        for candidat in reversed(morceaux):
            try:
                points.append((date, float(candidat.replace(",", "."))))
                break
            except ValueError:
                continue
    if not points:
        raise ValueError("aucune observation exploitable")
    points.sort(key=lambda p: p[0])
    ind["valeur"] = round(points[-1][1], 3)
    ind["date"] = points[-1][0]
    ind["source"] = "Banque nationale suisse"
    if len(points) > 1:
        ind["variation"] = round(points[-1][1] - points[-2][1], 3)
        ind["ecart_jours"] = _ecart_en_jours(points[-2][0], points[-1][0])


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


# ---------------------------------------------------------------------
# Épargne réglementée française
# ---------------------------------------------------------------------

WEBSTAT = "https://webstat.banque-france.fr/api/explore/v2.1"


def _chercher_jeu_webstat(termes):
    """Retrouve le jeu de données Webstat qui parle de ces termes."""
    condition = " or ".join(f'search(title, "{t}")' for t in termes)
    url = f"{WEBSTAT}/catalog/datasets?where={requests.utils.quote(condition)}&limit=10"
    for jeu in _get(url).json().get("results", []):
        identifiant = jeu.get("dataset_id") or jeu.get("datasetid")
        if identifiant:
            yield identifiant


def _derniere_valeur_webstat(dataset, motif):
    """Dernière observation numérique d'un jeu Webstat dont le libellé colle au motif."""
    url = (f"{WEBSTAT}/catalog/datasets/{dataset}/records"
           f"?where={requests.utils.quote(motif)}&order_by=-date&limit=1")
    resultats = _get(url).json().get("results", [])
    if not resultats:
        raise ValueError("aucune observation")
    ligne = resultats[0]
    valeur = next((v for c, v in ligne.items()
                   if isinstance(v, (int, float)) and "valeur" in c.lower() or c.lower() in ("obs_value", "value")),
                  None)
    if valeur is None:
        valeur = next((v for v in ligne.values() if isinstance(v, (int, float))), None)
    if valeur is None:
        raise ValueError("pas de valeur numérique dans la réponse")
    date = next((v for c, v in ligne.items() if "date" in c.lower() and isinstance(v, str)), None)
    return float(valeur), date


def _epargne_fr(conf, ind):
    """
    Taux réglementés. On tente Webstat, qui est ouvert et sans clé ; à défaut,
    on retombe sur la valeur écrite dans la configuration — jamais sur rien.
    """
    erreurs = []
    for termes in (conf.get("webstat_termes") or [conf["label"]],):
        try:
            for dataset in _chercher_jeu_webstat(termes):
                for motif in (conf.get("webstat_motif", ""), ""):
                    try:
                        valeur, date = _derniere_valeur_webstat(dataset, motif)
                        ind["valeur"] = round(valeur, 3)
                        ind["date"] = (date or "")[:10] or None
                        ind["source"] = "Banque de France (Webstat)"
                        return
                    except Exception as e:  # noqa: BLE001
                        erreurs.append(f"{dataset}: {type(e).__name__}")
        except Exception as e:  # noqa: BLE001
            erreurs.append(f"catalogue: {type(e).__name__}")

    if conf.get("valeur") is None:
        raise ValueError("Webstat injoignable et aucune valeur de repli : " + " / ".join(erreurs[:3]))

    date = conf.get("date")
    ind["valeur"] = float(conf["valeur"])
    ind["date"] = date.isoformat() if hasattr(date, "isoformat") else date
    ind["source"] = "saisi à la main"
    ind["repli"] = True


# ---------------------------------------------------------------------
# États-Unis
# ---------------------------------------------------------------------

def _tresor_us(conf, ind):
    """Courbe des taux du Trésor américain, publiée en CSV, sans clé."""
    annee = datetime.now(timezone.utc).year
    url = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
           f"daily-treasury-rates.csv/{annee}/all"
           "?type=daily_treasury_yield_curve&field_tdr_date_value=all&page&_format=csv")
    lignes = list(csv.DictReader(io.StringIO(_get(url).text)))
    colonne = next((c for c in (lignes[0] if lignes else {}) if c.strip() == conf["cle"]), None)
    if not colonne:
        raise ValueError(f"colonne « {conf['cle']} » absente")
    points = [(l["Date"], float(l[colonne])) for l in lignes if l.get(colonne) not in (None, "", "N/A")]
    if not points:
        raise ValueError("aucune observation")
    # Le fichier est classé du plus récent au plus ancien.
    ind["valeur"] = round(points[0][1], 3)
    ind["date"] = points[0][0]
    ind["source"] = "Trésor américain"
    if len(points) > 1:
        ind["variation"] = round(points[0][1] - points[1][1], 3)


def _bls(conf, ind):
    """
    Statistiques du travail américaines. L'accès sans clé est plafonné à
    25 appels par jour, largement suffisant pour deux séries mensuelles.
    """
    annee = datetime.now(timezone.utc).year
    url = f"https://api.bls.gov/publicAPI/v1/timeseries/data/{conf['cle']}"
    reponse = _get(f"{url}?startyear={annee - 2}&endyear={annee}")
    series = reponse.json().get("Results", {}).get("series", [])
    donnees = series[0].get("data", []) if series else []
    if not donnees:
        raise ValueError("série vide (quota BLS dépassé ?)")

    points = []
    for observation in donnees:
        try:
            points.append((f"{observation['year']}-{observation['period'][1:]}-01",
                           float(observation["value"])))
        except (KeyError, ValueError):
            continue
    points.sort(key=lambda p: p[0])
    if not points:
        raise ValueError("aucune valeur exploitable")

    if conf.get("calcul") == "variation_annuelle":
        # Un indice de prix ne dit rien seul : c'est sa variation sur douze mois
        # qui est « l'inflation ».
        if len(points) < 13:
            raise ValueError("historique trop court pour une variation annuelle")
        recent, il_y_a_un_an = points[-1][1], points[-13][1]
        ind["valeur"] = round((recent / il_y_a_un_an - 1) * 100, 2)
        if len(points) >= 14:
            precedent = (points[-2][1] / points[-14][1] - 1) * 100
            ind["variation"] = round(ind["valeur"] - precedent, 2)
    else:
        ind["valeur"] = round(points[-1][1], 2)
        if len(points) > 1:
            ind["variation"] = round(points[-1][1] - points[-2][1], 2)

    ind["date"] = points[-1][0]
    ind["source"] = "Bureau of Labor Statistics"


def _fred(conf, ind):
    """Base de la Fed de Saint-Louis. Nécessite une clé gratuite (FRED_API_KEY)."""
    cle_api = os.environ.get("FRED_API_KEY")
    if not cle_api:
        raise ValueError("clé FRED absente — ajoute le secret FRED_API_KEY")
    url = ("https://api.stlouisfed.org/fred/series/observations"
           f"?series_id={conf['cle']}&api_key={cle_api}&file_type=json"
           "&sort_order=desc&limit=14")
    if conf.get("transformation"):
        url += f"&units={conf['transformation']}"
    observations = _get(url).json().get("observations", [])
    points = [(o["date"], float(o["value"])) for o in observations if o.get("value") not in (".", "", None)]
    if not points:
        raise ValueError("série vide")
    ind["valeur"] = round(points[0][1], 2)
    ind["date"] = points[0][0]
    ind["source"] = "FRED"
    if len(points) > 1:
        ind["variation"] = round(points[0][1] - points[1][1], 2)


RECUPERATEURS = {
    "bce": _bce,
    "epargne_fr": _epargne_fr,
    "tresor_us": _tresor_us,
    "boc": _boc,
    "eurostat": _eurostat,
    "snb": _snb,
    "bls": _bls,
    "fred": _fred,
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
    for nom, source in ((a, gauche), (b, droite)):
        if not source or source["valeur"] is None or source["statut"] not in ("ok", None):
            raise ValueError(f"{nom} indisponible ou périmé")
    ind["valeur"] = round(gauche["valeur"] - droite["valeur"], 2)
    ind["date"] = gauche["date"]
    ind["source"] = "calculé"


def inspecter(conf):
    """
    Affiche ce que la source renvoie réellement pour un indicateur : le nom
    exact de la série et ses dernières observations. C'est ce qui permet de
    vérifier qu'un chiffre mesure bien ce qu'on croit.
    """
    print(f"Indicateur : {conf['id']} — {conf['label']}")
    print(f"Source : {conf['source']}  |  clé : {conf.get('cle')}")
    print(f"Pays : {conf['pays']}  |  thème : {conf['theme']}\n")

    if conf["source"] != "bce":
        ind = _neuf(conf)
        RECUPERATEURS[conf["source"]](conf, ind)
        print(json.dumps(ind, ensure_ascii=False, indent=2))
        return

    url = (f"https://data-api.ecb.europa.eu/service/data/{conf['cle']}"
           "?lastNObservations=6&format=csvdata")
    lignes = list(csv.DictReader(io.StringIO(_get(url).text)))
    if not lignes:
        print("Réponse vide.")
        return
    descriptifs = [c for c in lignes[0] if c in
                   ("TITLE", "TITLE_COMPL", "UNIT_MEASURE", "UNIT", "FREQ", "KEY", "SERIES")]
    print("Description de la série :")
    for colonne in descriptifs:
        print(f"  {colonne} = {lignes[0][colonne]}")
    print("\nDernières observations :")
    for l in lignes:
        print(f"  {l.get('TIME_PERIOD')}  {l.get('OBS_VALUE')}")


def _lire_date(valeur):
    """Accepte 2026-08-17, 08/17/2026, 2026-08, 2026-Q2 et 2026."""
    texte = str(valeur or "")[:10]
    trimestre = re.match(r"(\d{4})-?Q([1-4])", texte)
    if trimestre:
        return datetime(int(trimestre.group(1)), int(trimestre.group(2)) * 3, 1)
    for format_ in ("%Y-%m-%d", "%m/%d/%Y", "%Y-%m", "%Y"):
        try:
            return datetime.strptime(texte, format_)
        except ValueError:
            continue
    return None


def _ecart_en_jours(avant, apres):
    a, b = _lire_date(avant), _lire_date(apres)
    return (b - a).days if a and b else None


def _periode(ind):
    """
    La période d'une variation, c'est l'intervalle entre les deux dernières
    observations — pas l'âge de la dernière. Une série mensuelle publiée avec
    deux mois de retard compare bien deux mois, pas un trimestre.
    """
    jours = ind.get("ecart_jours")
    if jours is None:
        observee = _lire_date(ind.get("date"))
        if not observee:
            return None
        jours = (datetime.now(timezone.utc).replace(tzinfo=None) - observee).days
    if jours <= 6:
        return "24 h"
    if jours <= 45:
        return "un mois"
    if jours <= 130:
        return "un trimestre"
    return "un an"


def _est_perime(ind):
    if ind.get("repli") or ind.get("source") == "saisi à la main":
        return False
    """
    Une série arrêtée continue de répondre : le Royaume-Uni a quitté les
    statistiques de la BCE, et sa dernière observation date de 2020. L'afficher
    comme un chiffre du jour serait un mensonge.
    """
    observee = _lire_date(ind.get("date"))
    if not observee:
        return False
    age = (datetime.now(timezone.utc).replace(tzinfo=None) - observee).days
    attendu = ind.get("ecart_jours") or 31
    return age > max(3 * attendu, 120)


def tous_les_indicateurs(config, verbeux=True):
    actifs = [i for i in config["indicateurs"] if i.get("actif", True)]
    # Les constantes désactivées servent aux formules sans s'afficher.
    supports = [i for i in config["indicateurs"]
                if not i.get("actif", True) and i["source"] == "manuel"]

    identifiants = [i["cle"] for i in actifs if i["source"] == "crypto"]
    if identifiants:
        try:
            _charger_crypto(identifiants)
        except Exception as e:  # noqa: BLE001
            if verbeux:
                print(f"  ! CoinGecko injoignable : {e}")

    resultats = {}
    ordre = [i for i in actifs + supports if i["source"] != "calcule"]
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
        # « +0,15 » ne veut rien dire sans sa période : une série quotidienne
        # compare deux jours, une série mensuelle deux mois.
        ind["periode"] = _periode(ind)
        if ind["statut"] == "ok" and _est_perime(ind):
            ind["statut"] = "perime"
            ind["erreur"] = f"série arrêtée, dernière observation {ind.get('date')}"
        resultats[conf["id"]] = ind
        if verbeux and conf.get("actif", True):
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
