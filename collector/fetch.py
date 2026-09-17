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

RACINE = Path(__file__).resolve().parent.parent
CONFIG = RACINE / "collector" / "sources.yaml"
SORTIE = RACINE / "docs" / "data" / "feed.json"

RETENTION_JOURS = 21
MAX_ARTICLES = 500
UA = "veille-financiere/1.0 (usage personnel)"


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


def date_iso(entree) -> str:
    for champ in ("published_parsed", "updated_parsed"):
        valeur = getattr(entree, champ, None) or entree.get(champ)
        if valeur:
            return datetime.fromtimestamp(time.mktime(valeur), tz=timezone.utc).isoformat()
    return maintenant().isoformat()


# ---------------------------------------------------------------------
# Récupération
# ---------------------------------------------------------------------

def url_google_actus(requete: str) -> str:
    q = quote_plus(f"{requete} when:7d")
    return f"https://news.google.com/rss/search?q={q}&hl=fr&gl=FR&ceid=FR:fr"


def lire_flux(url: str):
    reponse = requests.get(
        url,
        headers={"User-Agent": UA, "Accept": "application/rss+xml, application/xml, text/xml, */*"},
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


def est_du_bruit(titre, bruit):
    t = sans_accents(titre.lower())
    return any(sans_accents(mot.lower()) in t for mot in bruit)


# ---------------------------------------------------------------------
# Collecte
# ---------------------------------------------------------------------

def collecter(config, verbeux=True):
    sources = construire_sources(config)
    signaux = config.get("signaux_forts", [])
    bruit = config.get("bruit", [])

    articles = {}
    vus = set()
    etats = []

    for source in sources:
        etat = {"nom": source["nom"], "genre": source["genre"], "ok": False, "nombre": 0, "erreur": None}
        try:
            entrees = lire_flux(source["url"])
            gardes = 0
            for entree in entrees:
                titre_brut = nettoyer_html(entree.get("title", ""))
                lien = entree.get("link", "")
                if not titre_brut or not lien:
                    continue
                if est_du_bruit(titre_brut, bruit):
                    continue

                if source["genre"] == "recherche":
                    titre, editeur = separer_source_google(titre_brut, "Google Actualités")
                    resume = ""
                else:
                    titre, editeur = titre_brut, source["nom"]
                    resume = nettoyer_html(entree.get("summary", ""))[:260]

                cle = cle_doublon(titre)
                if cle in vus:
                    continue
                vus.add(cle)

                article = {
                    "id": empreinte(lien),
                    "titre": titre,
                    "url": lien,
                    "source": editeur,
                    "rubrique": source["nom"],
                    "categorie": source["categorie"],
                    "genre": source["genre"],
                    "publie_le": date_iso(entree),
                    "resume": resume,
                }
                article["score"], article["signaux"] = noter(article, signaux, source["poids"])
                articles[article["id"]] = article
                gardes += 1

            etat["ok"] = True
            etat["nombre"] = gardes
        except Exception as e:  # noqa: BLE001
            etat["erreur"] = f"{type(e).__name__}: {e}"

        etats.append(etat)
        if verbeux:
            marque = "ok " if etat["ok"] else "ÉCHEC"
            detail = etat["erreur"] or f"{etat['nombre']} articles"
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

    limite = maintenant() - timedelta(days=RETENTION_JOURS)
    gardes = [
        a for a in fusion.values()
        if datetime.fromisoformat(a.get("premiere_vue", horodatage)) > limite
    ]
    gardes.sort(key=lambda a: (a["publie_le"], a["score"]), reverse=True)
    return gardes[:MAX_ARTICLES]


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
    parseur.add_argument("--resume", action="store_true", help="ajouter le résumé du jour")
    args = parseur.parse_args()

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

    indicateurs = tous_les_indicateurs()
    for i in indicateurs:
        print(f"  {'ok   ' if i['statut'] == 'ok' else 'ÉCHEC'}  {i['label']:<30} {i['valeur']}")

    articles = fusionner(articles, charger_existant())

    donnees = {
        "genere_le": maintenant().isoformat(),
        "categories": config["categories"],
        "indicateurs": indicateurs,
        "articles": articles,
        "sources": etats,
        "resume": None,
    }

    import os

    if args.resume and os.environ.get("ANTHROPIC_API_KEY"):
        try:
            donnees["resume"] = resumer(articles, os.environ["ANTHROPIC_API_KEY"])
        except Exception as e:  # noqa: BLE001
            print(f"  ! résumé indisponible : {e}")

    SORTIE.parent.mkdir(parents=True, exist_ok=True)
    SORTIE.write_text(
        json.dumps(donnees, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"\nÉcrit {SORTIE.relative_to(RACINE)} — {len(articles)} articles, {len(indicateurs)} indicateurs.")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    raise SystemExit(main())
