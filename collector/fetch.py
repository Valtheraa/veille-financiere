"""
Collecteur de la veille financière.

    python collector/fetch.py            # collecte et écrit docs/data/feed.json
    python collector/fetch.py --check    # teste chaque source, n'écrit rien
    python collector/fetch.py --resume   # collecte + résumé du jour (clé Anthropic)

Le fichier produit est un simple JSON lu par le tableau de bord.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import html
import json
import re
import sys
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote_plus

import feedparser
import requests
import yaml

import analyse

RACINE = Path(__file__).resolve().parent.parent
CONFIG = RACINE / "collector" / "sources.yaml"
CONFIG_CHIFFRES = RACINE / "collector" / "indicateurs.yaml"
CONFIG_AGENDA = RACINE / "collector" / "agenda.yaml"
CONFIG_ALERTES = RACINE / "collector" / "alertes.yaml"
HISTORIQUE = RACINE / "docs" / "data" / "historique.json"
ALERTE_TEXTE = RACINE / "alerte.md"
ARCHIVE = RACINE / "docs" / "data" / "archives"
SORTIE = RACINE / "docs" / "data" / "feed.json"

RETENTION_JOURS = 21
MAX_ARTICLES = 320
MAX_PAR_MEDIA = 6
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
LIMITE_PAR_SOURCE = 30   # une seule requête ne doit pas noyer le flux


# ---------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------

def maintenant():
    return datetime.now(timezone.utc)


def sans_accents(texte: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", texte) if unicodedata.category(c) != "Mn"
    )


def empreinte(texte: str) -> str:
    return hashlib.sha1(texte.encode("utf-8")).hexdigest()[:16]


def nettoyer_html(brut: str) -> str:
    if not brut:
        return ""
    texte = re.sub(r"<[^>]+>", " ", brut)
    texte = html.unescape(texte)
    texte = re.sub(r"\s+", " ", texte).strip()
    return texte


def cle_doublon(titre: str) -> str:
    base = sans_accents(titre.lower())
    base = re.sub(r"[^a-z0-9 ]+", " ", base)
    base = re.sub(r"\s+", " ", base).strip()
    return empreinte(base[:80])


MOIS_ANGLAIS = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"])}


def date_iso(entree):
    """
    Renvoie (date, estimée). Certains flux institutionnels ne datent pas leurs
    entrées : plutôt que de les faire passer pour l'actualité du jour, on
    cherche la date dans le texte, et on signale l'estimation à défaut.
    """
    for champ in ("published_parsed", "updated_parsed"):
        valeur = getattr(entree, champ, None) or entree.get(champ)
        if valeur:
            return datetime.fromtimestamp(time.mktime(valeur), tz=timezone.utc).isoformat(), False

    texte = f"{entree.get('summary', '')} {entree.get('title', '')}"
    trouve = re.search(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\s+(20\d{2})\b", texte)
    if trouve:
        mois = MOIS_ANGLAIS.get(trouve.group(2).lower())
        if mois:
            try:
                return datetime(int(trouve.group(3)), mois, int(trouve.group(1)),
                                tzinfo=timezone.utc).isoformat(), False
            except ValueError:
                pass
    return maintenant().isoformat(), True


# ---------------------------------------------------------------------
# Récupération
# ---------------------------------------------------------------------

def url_google_actus(requete: str) -> str:
    q = quote_plus(f"{requete} when:7d")
    return f"https://news.google.com/rss/search?q={q}&hl=fr&gl=FR&ceid=FR:fr"


def lire_flux(url: str):
    reponse = requests.get(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "application/rss+xml, application/xml, text/xml, application/atom+xml, */*",
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
        },
        timeout=25,
    )
    reponse.raise_for_status()
    flux = feedparser.parse(reponse.content)
    if flux.bozo and not flux.entries:
        raise ValueError(f"flux illisible ({flux.bozo_exception})")
    return flux.entries


def separer_source_google(titre: str, defaut: str):
    """Google Actualités suffixe ses titres par « - Nom du média »."""
    morceaux = titre.rsplit(" - ", 1)
    if len(morceaux) == 2 and 2 < len(morceaux[1]) < 40:
        return morceaux[0].strip(), morceaux[1].strip()
    return titre, defaut


def construire_sources(config):
    sources = []
    for f in config.get("flux", []):
        if f.get("actif", True):
            sources.append(
                {
                    "nom": f["nom"],
                    "url": f["url"],
                    "categorie": f["categorie"],
                    "poids": f.get("poids", 1),
                    "conserver": f.get("conserver"),
                    "secours_q": f.get("secours_q"),
                    "exiger": f.get("exiger"),
                    "exclure": f.get("exclure"),
                    "genre": "officiel",
                }
            )
    for r in config.get("recherches", []):
        if r.get("actif", True):
            sources.append(
                {
                    "nom": r["nom"],
                    "url": url_google_actus(r["q"]),
                    "categorie": r["categorie"],
                    "poids": r.get("poids", 1),
                    "exiger": r.get("exiger"),
                    "exclure": r.get("exclure"),
                    "genre": "recherche",
                }
            )
    return sources


# ---------------------------------------------------------------------
# Notation
# ---------------------------------------------------------------------

def noter(article, signaux_forts, poids_source):
    texte = sans_accents((article["titre"] + " " + article["resume"]).lower())
    touches = [
        mot for mot in signaux_forts if sans_accents(mot.lower()) in texte
    ]
    score = poids_source * 10 + len(touches) * 6

    age_h = (maintenant() - datetime.fromisoformat(article["publie_le"])).total_seconds() / 3600
    if age_h < 6:
        score += 12
    elif age_h < 24:
        score += 6
    elif age_h > 96:
        score -= 8

    if article["genre"] == "officiel":
        score += 8
    return score, touches


def _contient(texte, mots):
    t = sans_accents(texte.lower())
    return any(sans_accents(str(m).lower()) in t for m in mots or [])


def est_du_bruit(titre, bruit):
    return _contient(titre, bruit)


def passe_les_filtres(titre, source, exclusions_globales):
    """
    `exiger` : le titre doit contenir au moins un de ces mots, sinon il saute.
    C'est ce qui empêche « arrêté » de ramener des faits divers.
    `exclure` : mots qui disqualifient le titre.
    """
    if _contient(titre, exclusions_globales) or _contient(titre, source.get("exclure")):
        return False
    exiger = source.get("exiger")
    if exiger and not _contient(titre, exiger):
        return False
    return True


# ---------------------------------------------------------------------
# Collecte
# ---------------------------------------------------------------------

def collecter(config, verbeux=True):
    sources = construire_sources(config)
    signaux = config.get("signaux_forts", [])
    bruit = config.get("bruit", [])
    exclusions = config.get("exclure_partout", [])

    articles = {}
    vus = set()
    etats = []

    for source in sources:
        etat = {"nom": source["nom"], "genre": source["genre"], "ok": False,
                "nombre": 0, "erreur": None, "secours": False}
        genre = source["genre"]
        try:
            try:
                entrees = lire_flux(source["url"])
            except Exception as premiere:  # noqa: BLE001
                if not source.get("secours_q"):
                    raise
                # Le site officiel ne répond plus : on passe par une recherche
                # limitée à son domaine, ce qui couvre le même contenu.
                entrees = lire_flux(url_google_actus(source["secours_q"]))
                genre = "recherche"
                etat["secours"] = True
                etat["erreur"] = f"flux direct indisponible ({type(premiere).__name__}), repli en place"
            gardes = 0
            for entree in entrees:
                titre_brut = nettoyer_html(entree.get("title", ""))
                lien = entree.get("link", "")
                if not titre_brut or not lien:
                    continue
                if est_du_bruit(titre_brut, bruit):
                    continue
                if not passe_les_filtres(titre_brut, source, exclusions):
                    continue

                if genre == "recherche":
                    titre, editeur = separer_source_google(titre_brut, "Google Actualités")
                    resume = ""
                else:
                    titre, editeur = titre_brut, source["nom"]
                    resume = nettoyer_html(entree.get("summary", ""))[:260]

                publie_le, estimee = date_iso(entree)
                cle = cle_doublon(titre)
                if cle in vus:
                    continue
                vus.add(cle)

                article = {
                    "conserver": source.get("conserver", RETENTION_JOURS),
                    "id": empreinte(lien),
                    "titre": titre,
                    "url": lien,
                    "source": editeur,
                    "rubrique": source["nom"],
                    "categorie": source["categorie"],
                    "genre": genre,
                    "publie_le": publie_le,
                    "date_estimee": estimee,
                    "resume": resume,
                }
                article["score"], article["signaux"] = noter(article, signaux, source["poids"])
                articles[article["id"]] = article
                gardes += 1
                if gardes >= LIMITE_PAR_SOURCE:
                    break

            etat["ok"] = True
            etat["nombre"] = gardes
        except Exception as e:  # noqa: BLE001
            etat["erreur"] = f"{type(e).__name__}: {e}"

        etats.append(etat)
        if verbeux:
            marque = "ok " if etat["ok"] else "ÉCHEC"
            detail = f"{etat['nombre']} articles" if etat["ok"] else etat["erreur"]
            if etat["secours"]:
                detail += " (par repli)"
            print(f"  {marque}  {source['nom']:<34} {detail}")

    return list(articles.values()), etats


def fusionner(nouveaux, anciens):
    """Garde la date de première apparition pour signaler ce qui est neuf."""
    index = {a["id"]: a for a in anciens}
    horodatage = maintenant().isoformat()
    fusion = {}

    for a in anciens:
        fusion[a["id"]] = a
    for n in nouveaux:
        precedent = index.get(n["id"])
        n["premiere_vue"] = precedent["premiere_vue"] if precedent else horodatage
        fusion[n["id"]] = n

    gardes = []
    for a in fusion.values():
        jours = a.get("conserver") or RETENTION_JOURS
        if datetime.fromisoformat(a.get("premiere_vue", horodatage)) > maintenant() - timedelta(days=jours):
            gardes.append(a)
    gardes.sort(key=lambda a: (a["publie_le"], a["score"]), reverse=True)
    return gardes[:MAX_ARTICLES]


def _charger(chemin: Path):
    """Lit un fichier de configuration facultatif."""
    if not chemin.exists():
        return {}
    return yaml.safe_load(chemin.read_text(encoding="utf-8")) or {}


def charger_existant():
    if SORTIE.exists():
        try:
            return json.loads(SORTIE.read_text(encoding="utf-8")).get("articles", [])
        except Exception:  # noqa: BLE001
            return []
    return []


# ---------------------------------------------------------------------
# Résumé optionnel par Claude (facultatif, quelques centimes par mois)
# ---------------------------------------------------------------------

def arbitre_claude(cle_api):
    """Rend une fonction qui dit, pour chaque paire de titres, s'il s'agit du même sujet."""
    def trancher(paires):
        if not paires:
            return []
        liste = "\n".join(f"{i+1}. A : {a}\n   B : {b}" for i, (a, b) in enumerate(paires[:15]))
        invite = (
            "Pour chaque paire de titres de presse, dis si A et B couvrent le même "
            "événement précis (et pas seulement le même thème).\n\n"
            f"{liste}\n\n"
            "Réponds uniquement par un tableau JSON de booléens, un par paire, sans texte autour."
        )
        reponse = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": cle_api, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": "claude-sonnet-4-6", "max_tokens": 300,
                  "messages": [{"role": "user", "content": invite}]},
            timeout=60,
        )
        reponse.raise_for_status()
        texte = "".join(b.get("text", "") for b in reponse.json().get("content", []))
        texte = re.sub(r"```(json)?", "", texte).strip()
        verdicts = json.loads(texte)
        return [bool(v) for v in verdicts]
    return trancher


def resumer(articles, cle_api):
    tete = sorted(articles, key=lambda a: a["score"], reverse=True)[:25]
    liste = "\n".join(f"- [{a['categorie']}] {a['titre']} ({a['source']})" for a in tete)
    invite = (
        "Voici les titres d'actualité financière des dernières 24 h.\n\n"
        f"{liste}\n\n"
        "Écris en français 4 phrases maximum : ce qui a réellement bougé, "
        "et ce que ça change concrètement pour un épargnant français. "
        "Pas de préambule, pas de liste, pas de conseil d'investissement."
    )
    reponse = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": cle_api,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-sonnet-4-6",
            "max_tokens": 400,
            "messages": [{"role": "user", "content": invite}],
        },
        timeout=60,
    )
    reponse.raise_for_status()
    blocs = reponse.json().get("content", [])
    return "".join(b.get("text", "") for b in blocs).strip()


# ---------------------------------------------------------------------

def main():
    parseur = argparse.ArgumentParser(description="Collecteur de veille financière")
    parseur.add_argument("--check", action="store_true", help="tester les sources sans écrire")
    parseur.add_argument("--ia", "--resume", dest="ia", action="store_true",
                         help="faire rédiger le résumé et arbitrer les regroupements par Claude")
    parseur.add_argument("--inspecter", metavar="ID",
                         help="afficher les dernières observations brutes d'un indicateur")
    parseur.add_argument("--check-chiffres", action="store_true",
                         help="tester chaque indicateur un par un, n'écrit rien")
    args = parseur.parse_args()

    if args.inspecter:
        from markets import inspecter

        config_chiffres = yaml.safe_load(CONFIG_CHIFFRES.read_text(encoding="utf-8"))
        conf = next((i for i in config_chiffres["indicateurs"] if i["id"] == args.inspecter), None)
        if not conf:
            print(f"Aucun indicateur nommé {args.inspecter}.")
            return 1
        inspecter(conf)
        return 0

    if args.check_chiffres:
        from markets import tous_les_indicateurs

        config_chiffres = yaml.safe_load(CONFIG_CHIFFRES.read_text(encoding="utf-8"))
        print("Indicateurs :")
        resultats = tous_les_indicateurs(config_chiffres)
        casses = [i for i in resultats if i["statut"] != "ok"]
        print(f"\n{len(resultats) - len(casses)}/{len(resultats)} indicateurs récupérés.")
        for i in casses:
            print(f"  à corriger : {i['label']} — {i['erreur']}")
        return 1 if casses else 0

    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))

    print("Sources :")
    articles, etats = collecter(config)
    morts = [e for e in etats if not e["ok"]]
    vides = [e for e in etats if e["ok"] and e["nombre"] == 0]

    if args.check:
        print(f"\n{len(etats) - len(morts)}/{len(etats)} sources joignables, {len(articles)} articles.")
        for e in morts:
            print(f"  à corriger : {e['nom']} — {e['erreur']}")
        for e in vides:
            print(f"  vide       : {e['nom']}")
        return 1 if morts else 0

    print("\nIndicateurs :")
    from markets import tous_les_indicateurs  # import tardif : le --check n'en a pas besoin

    config_chiffres = yaml.safe_load(CONFIG_CHIFFRES.read_text(encoding="utf-8"))
    indicateurs = tous_les_indicateurs(config_chiffres)

    # Historique, alertes et agenda
    points = analyse.charger_historique(HISTORIQUE)
    anomalies = analyse.verifier_coherence(indicateurs, points)
    for a in anomalies:
        print(f"  ! valeur écartée — {a['message']}")

    # Toutes les recherches muettes d'un coup : c'est Google qui a changé,
    # pas l'actualité qui s'est arrêtée.
    recherches = [e for e in etats if e["genre"] == "recherche"]
    muettes = [e for e in recherches if e["ok"] and e["nombre"] == 0]
    if recherches and len(muettes) >= max(3, int(len(recherches) * 0.8)):
        anomalies.append({
            "id": "recherches",
            "label": "Google Actualités",
            "message": f"{len(muettes)} recherches sur {len(recherches)} ne renvoient rien : "
                       "le flux Google Actualités a probablement changé",
        })

    alertes = analyse.evaluer_alertes(indicateurs, points, _charger(CONFIG_ALERTES).get("regles"))
    points = analyse.mettre_a_jour_historique(points, indicateurs)
    analyse.evolution(points, indicateurs)
    HISTORIQUE.parent.mkdir(parents=True, exist_ok=True)
    HISTORIQUE.write_text(
        json.dumps({"points": points}, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    rendez_vous = analyse.prochains_rendez_vous(_charger(CONFIG_AGENDA))

    if alertes or anomalies:
        print("\nAlertes :")
        for a in alertes:
            print(f"  ! {a['message']} — {a['valeur']}")
        texte = analyse.alertes_en_texte(alertes)
        if anomalies:
            texte += ("\n\n**Anomalies techniques**\n"
                      + "\n".join(f"- {a['message']}" for a in anomalies))
        ALERTE_TEXTE.write_text(texte.strip() + "\n", encoding="utf-8")
    elif ALERTE_TEXTE.exists():
        ALERTE_TEXTE.unlink()

    articles = fusionner(articles, charger_existant())

    cle_api = os.environ.get("ANTHROPIC_API_KEY")
    avant_regroupement = len(articles)
    articles = analyse.limiter_par_media(articles, MAX_PAR_MEDIA)
    articles = analyse.regrouper(
        articles, arbitre=arbitre_claude(cle_api) if cle_api else None
    )
    print(f"\nRegroupement : {avant_regroupement} articles ramenés à {len(articles)} sujets.")

    donnees = {
        "genere_le": maintenant().isoformat(),
        "categories": config["categories"],
        "groupes": config_chiffres["groupes"],
        "indicateurs": indicateurs,
        "courbes": analyse.courbes(points, indicateurs),
        "alertes": alertes,
        "anomalies": anomalies,
        "agenda": rendez_vous,
        "articles": articles,
        "sources": etats,
        "resume": None,
    }

    if args.ia and cle_api:
        try:
            donnees["resume"] = resumer(articles, cle_api)
        except Exception as e:  # noqa: BLE001
            print(f"  ! résumé par Claude indisponible : {e}")
    if not donnees["resume"]:
        donnees["resume"] = analyse.resume_automatique(indicateurs, alertes, articles)

    # Archive : un fichier par mois, plus un index
    index = analyse.repartir_archive(ARCHIVE, articles)
    print(f"Archive : {sum(m['nombre'] for m in index)} titres sur {len(index)} mois.")

    SORTIE.parent.mkdir(parents=True, exist_ok=True)
    SORTIE.write_text(
        json.dumps(donnees, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"\nÉcrit {SORTIE.relative_to(RACINE)} — {len(articles)} articles, {len(indicateurs)} indicateurs.")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    raise SystemExit(main())
