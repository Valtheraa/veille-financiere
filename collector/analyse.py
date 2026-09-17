"""
Ce que le collecteur fait des données une fois récupérées :

  historique   archive une valeur par jour et par indicateur, pour les courbes
  alertes      détecte les franchissements de seuil d'une collecte à l'autre
  agenda       calcule les prochains rendez-vous économiques
  regroupement rassemble les articles qui parlent du même sujet
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

RETENTION_HISTORIQUE = 400   # jours conservés
POINTS_COURBE = 40           # points envoyés au tableau de bord


# ---------------------------------------------------------------------
# Historique
# ---------------------------------------------------------------------

def charger_historique(chemin: Path):
    if chemin.exists():
        try:
            return json.loads(chemin.read_text(encoding="utf-8")).get("points", [])
        except Exception:  # noqa: BLE001
            return []
    return []


def verifier_coherence(indicateurs, points, ecart_pct=25.0, ecart_pt=1.5):
    """
    Un CAC 40 qui décuple ou un taux qui bondit de trois points du jour au
    lendemain, c'est une API qui a changé de format, pas un événement.
    La valeur reste affichée mais n'entre pas dans l'historique.
    """
    if not points:
        return []
    reference = points[-1]["valeurs"]
    anomalies = []
    for ind in indicateurs:
        avant = reference.get(ind["id"])
        if ind["statut"] != "ok" or ind["valeur"] is None or avant in (None, 0):
            continue
        if ind.get("unite") in ("%", "pt"):
            aberrant = abs(ind["valeur"] - avant) > ecart_pt
        else:
            aberrant = abs(ind["valeur"] - avant) / abs(avant) * 100 > ecart_pct
        if aberrant:
            ind["suspect"] = True
            lisible_avant = round(avant, 2)
            lisible_apres = round(ind["valeur"], 2)
            anomalies.append({
                "id": ind["id"],
                "label": ind["label"],
                "valeur": lisible_apres,
                "precedent": lisible_avant,
                "message": f"{ind['label']} : {lisible_avant} puis {lisible_apres}, "
                           "écart invraisemblable — valeur non archivée",
            })
    return anomalies


def mettre_a_jour_historique(points, indicateurs):
    """Un point par jour. Une nouvelle collecte du jour écrase la précédente."""
    aujourdhui = datetime.now(timezone.utc).date().isoformat()
    valeurs = {
        i["id"]: i["valeur"]
        for i in indicateurs
        if i["statut"] == "ok" and i["valeur"] is not None and not i.get("suspect")
    }
    points = [p for p in points if p["date"] != aujourdhui]
    points.append({"date": aujourdhui, "valeurs": valeurs})
    points.sort(key=lambda p: p["date"])

    limite = (datetime.now(timezone.utc).date() - timedelta(days=RETENTION_HISTORIQUE)).isoformat()
    return [p for p in points if p["date"] >= limite]


def courbes(points, indicateurs):
    """Les dernières valeurs de chaque indicateur, pour tracer une micro-courbe."""
    recents = points[-POINTS_COURBE:]
    sortie = {}
    for ind in indicateurs:
        serie = [p["valeurs"].get(ind["id"]) for p in recents]
        serie = [v for v in serie if v is not None]
        if len(serie) >= 4:
            sortie[ind["id"]] = serie
    return sortie


def evolution(points, indicateurs, jours=30):
    """Variation sur N jours, ajoutée à chaque indicateur."""
    if not points:
        return
    cible = (datetime.now(timezone.utc).date() - timedelta(days=jours)).isoformat()
    anciens = [p for p in points if p["date"] <= cible]
    if not anciens:
        return
    reference = anciens[-1]["valeurs"]
    for ind in indicateurs:
        depart = reference.get(ind["id"])
        if depart is not None and ind["valeur"] is not None and ind["statut"] == "ok":
            ind["evolution_30j"] = round(ind["valeur"] - depart, 3)


# ---------------------------------------------------------------------
# Alertes
# ---------------------------------------------------------------------

def evaluer_alertes(indicateurs, points, regles):
    """
    Compare la valeur du moment à celle du dernier passage.
    Une alerte ne se déclenche qu'au franchissement, pas tant que le seuil
    reste dépassé : sinon elle sonnerait toutes les heures.
    """
    precedent = points[-1]["valeurs"] if points else {}
    par_id = {i["id"]: i for i in indicateurs}
    declenchees = []

    for regle in regles or []:
        ind = par_id.get(regle["indicateur"])
        if not ind or ind["statut"] != "ok" or ind["valeur"] is None:
            continue
        avant = precedent.get(regle["indicateur"])
        valeur = ind["valeur"]
        condition = regle.get("condition", "change")
        seuil = regle.get("seuil")
        touche = False

        if condition == "change":
            touche = avant is not None and abs(valeur - avant) > 1e-9
        elif condition == "sup":
            touche = valeur > seuil and (avant is None or avant <= seuil)
        elif condition == "inf":
            touche = valeur < seuil and (avant is None or avant >= seuil)
        elif condition == "variation":
            touche = (
                ind.get("variation_pct") is not None
                and abs(ind["variation_pct"]) >= seuil
            )

        if touche:
            declenchees.append({
                "id": ind["id"],
                "label": ind["label"],
                "message": regle.get("message") or ind["label"],
                "valeur": valeur,
                "precedent": avant,
                "unite": ind.get("unite"),
            })
    return declenchees


def alertes_en_texte(alertes):
    lignes = []
    for a in alertes:
        unite = f" {a['unite']}" if a.get("unite") else ""
        avant = "" if a["precedent"] is None else f" (avant : {a['precedent']}{unite})"
        lignes.append(f"- **{a['message']}** — {a['valeur']}{unite}{avant}")
    return "\n".join(lignes)


# ---------------------------------------------------------------------
# Agenda
# ---------------------------------------------------------------------

def _prochaine_date(entree, aujourdhui):
    if entree.get("date"):
        valeur = entree["date"]
        return valeur if isinstance(valeur, date) else date.fromisoformat(str(valeur))

    recurrence = entree.get("recurrent")
    if recurrence == "annuel":
        jour, mois = str(entree["jour"]).split("-")
        for annee in (aujourdhui.year, aujourdhui.year + 1):
            try:
                candidat = date(annee, int(mois), int(jour))
            except ValueError:
                continue
            if candidat >= aujourdhui:
                return candidat
    elif recurrence == "mensuel":
        demande = entree["jour"]
        for decalage in range(0, 70):
            candidat = aujourdhui + timedelta(days=decalage)
            dernier = (candidat.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
            if str(demande) == "dernier":
                if candidat == dernier:
                    return candidat
            elif candidat.day == min(int(demande), dernier.day):
                return candidat
    return None


def prochains_rendez_vous(agenda, combien=6):
    aujourdhui = datetime.now(timezone.utc).date()
    sortie = []
    for entree in (agenda or {}).get("rendez_vous", []):
        if not entree.get("actif", True):
            continue
        quand = _prochaine_date(entree, aujourdhui)
        if not quand:
            continue
        sortie.append({
            "label": entree["label"],
            "date": quand.isoformat(),
            "jours": (quand - aujourdhui).days,
            "detail": entree.get("detail"),
        })
    sortie.sort(key=lambda r: r["date"])
    return sortie[:combien]


# ---------------------------------------------------------------------
# Regroupement des articles qui traitent du même sujet
# ---------------------------------------------------------------------

MOTS_VIDES = {
    "avec", "dans", "pour", "plus", "cette", "leur", "leurs", "comme", "sans",
    "mais", "entre", "sont", "être", "avoir", "fait", "faire", "tout", "tous",
    "toute", "toutes", "quoi", "dont", "elle", "elles", "nous", "vous", "ils",
    "selon", "apres", "avant", "encore", "aussi", "ainsi", "cela", "chez",
    "vers", "depuis", "alors", "quand", "pourquoi", "comment", "voici", "voila",
    "les", "des", "une", "par", "sur", "aux", "que", "qui", "son", "ses", "est",
    "ont", "non", "pas", "ete", "apr", "ces", "son", "lui", "ils", "sont",
}


def _jetons(titre):
    base = "".join(
        c for c in unicodedata.normalize("NFD", titre.lower())
        if unicodedata.category(c) != "Mn"
    )
    mots = re.findall(r"[a-z0-9]+", base)
    # Trois lettres suffisent : « CAC », « BCE », « AMF », « ETF », « PEA ».
    return {m for m in mots if len(m) >= 3 and m not in MOTS_VIDES}


def _similarite(a, b):
    """
    Deux mesures, sans pondération par la rareté : sur un flux de presse, les
    mots partagés sont justement les mots du sujet (« CAC », « pétrole »,
    « CLARITY »), et les pénaliser empêchait les reprises de se rejoindre.
    Le recouvrement pèse plus lourd que l'union, car deux titres du même
    événement n'ont presque jamais la même longueur.
    """
    if not a or not b:
        return 0.0
    commun = len(a & b)
    jaccard = commun / len(a | b)
    recouvrement = commun / min(len(a), len(b))
    return 0.35 * jaccard + 0.65 * recouvrement


def _absorber(tete, article):
    """Rattache un article à un sujet, sans répéter deux fois le même média."""
    reprises = tete.setdefault("autres", [])
    candidates = [{"source": article["source"], "url": article["url"]}]
    candidates += article.get("autres", [])
    vus = {tete["source"].lower()} | {r["source"].lower() for r in reprises}
    liens = {tete["url"]} | {r["url"] for r in reprises}
    for reprise in candidates:
        if reprise["source"].lower() in vus or reprise["url"] in liens:
            continue
        reprises.append(reprise)
        vus.add(reprise["source"].lower())
        liens.add(reprise["url"])


def limiter_par_media(articles, maxi=6):
    """
    Certains sites publient quinze variations du même sujet. On garde leurs
    meilleurs articles et on laisse la place aux autres médias.
    """
    from collections import defaultdict
    compteur = defaultdict(int)
    gardes = []
    for article in sorted(articles, key=lambda a: a["score"], reverse=True):
        cle = (article["source"] or "").lower()
        if compteur[cle] >= maxi:
            continue
        compteur[cle] += 1
        gardes.append(article)
    return gardes


def regrouper(articles, seuil=0.30, zone_grise=0.20, arbitre=None):
    """
    Un seul article par sujet : le mieux noté porte le sujet, les autres
    deviennent des reprises listées sous son titre.

    Les titres proches sans être identiques (entre `zone_grise` et `seuil`)
    sont laissés séparés, sauf si un arbitre est fourni : c'est le crochet
    par lequel Claude tranche les cas douteux quand une clé est disponible.
    """
    classes = sorted(articles, key=lambda a: (a["score"], a["publie_le"]), reverse=True)
    # Les regroupements sont recalculés à chaque collecte : on repart des
    # articles seuls, sinon les reprises d'hier s'empilent sur celles d'aujourd'hui.
    for article in classes:
        article["autres"] = []
    tetes, empreintes, vocabulaires, douteux = [], [], [], []

    for article in classes:
        jetons = _jetons(article["titre"])
        meilleur, score = None, 0.0
        for index, vocabulaire in enumerate(vocabulaires):
            valeur = _similarite(jetons, vocabulaire)
            # Garde-fou anti-dérive : soit deux mots en commun avec le titre qui
            # porte le sujet, soit trois avec le vocabulaire accumulé du groupe.
            if len(jetons & empreintes[index]) < 2 and len(jetons & vocabulaire) < 3:
                valeur = min(valeur, zone_grise)
            if valeur > score:
                meilleur, score = index, valeur

        if meilleur is not None and score >= seuil:
            _absorber(tetes[meilleur], article)
            if len(tetes[meilleur].get("autres", [])) <= 3:
                vocabulaires[meilleur] = vocabulaires[meilleur] | jetons
            continue

        tetes.append(article)
        empreintes.append(jetons)
        vocabulaires.append(set(jetons))
        if meilleur is not None and score >= zone_grise:
            douteux.append((len(tetes) - 1, meilleur))

    if arbitre and douteux:
        paires = [(tetes[i]["titre"], tetes[j]["titre"]) for i, j in douteux]
        try:
            verdicts = arbitre(paires)
        except Exception:  # noqa: BLE001
            verdicts = []
        a_retirer = set()
        for (i, j), identiques in zip(douteux, verdicts):
            if identiques and i not in a_retirer and j not in a_retirer:
                _absorber(tetes[j], tetes[i])
                a_retirer.add(i)
        tetes = [t for index, t in enumerate(tetes) if index not in a_retirer]

    tetes.sort(key=lambda a: (a["publie_le"], a["score"]), reverse=True)
    return tetes


# ---------------------------------------------------------------------
# Archive : la mémoire longue, au-delà des 21 jours du flux
# ---------------------------------------------------------------------

PLAFOND_MOIS = 1500


def repartir_archive(dossier, articles):
    """
    Un fichier par mois plus un index : le navigateur ne télécharge que les
    mois qu'il consulte, au lieu de tout l'historique à la première recherche.
    """
    dossier.mkdir(parents=True, exist_ok=True)
    par_mois = {}
    for article in articles:
        mois = article["publie_le"][:7]
        par_mois.setdefault(mois, []).append({
            "id": article["id"],
            "titre": article["titre"],
            "url": article["url"],
            "source": article["source"],
            "categorie": article["categorie"],
            "publie_le": article["publie_le"],
        })

    for mois, nouveaux in par_mois.items():
        chemin = dossier / f"{mois}.json"
        existants = []
        if chemin.exists():
            try:
                existants = json.loads(chemin.read_text(encoding="utf-8")).get("articles", [])
            except Exception:  # noqa: BLE001
                existants = []
        connus = {a["id"] for a in existants}
        existants.extend(a for a in nouveaux if a["id"] not in connus)
        existants.sort(key=lambda a: a["publie_le"], reverse=True)
        chemin.write_text(
            json.dumps({"articles": existants[:PLAFOND_MOIS]}, ensure_ascii=False,
                       separators=(",", ":")),
            encoding="utf-8",
        )

    index = []
    for chemin in sorted(dossier.glob("20*.json"), reverse=True):
        try:
            nombre = len(json.loads(chemin.read_text(encoding="utf-8")).get("articles", []))
        except Exception:  # noqa: BLE001
            nombre = 0
        index.append({"mois": chemin.stem, "nombre": nombre})
    (dossier / "index.json").write_text(
        json.dumps({"mois": index}, ensure_ascii=False), encoding="utf-8"
    )
    return index


# ---------------------------------------------------------------------
# Résumé de repli, sans intelligence artificielle
# ---------------------------------------------------------------------

def resume_automatique(indicateurs, alertes, articles):
    """Deux ou trois phrases factuelles, quand aucune clé n'est configurée."""
    phrases = []   # les alertes ont déjà leur bandeau : on ne les répète pas

    bougeurs = [
        i for i in indicateurs
        if i["statut"] == "ok" and i.get("variation_pct") not in (None, 0)
        and i["groupe"] in ("indices", "crypto", "matieres")
    ]
    bougeurs.sort(key=lambda i: abs(i["variation_pct"]), reverse=True)
    if bougeurs:
        morceaux = [
            "{} {}{} %".format(
                i["label"],
                "+" if i["variation_pct"] > 0 else "",
                f"{i['variation_pct']:.2f}".replace(".", ","),
            )
            for i in bougeurs[:3]
        ]
        phrases.append("Sur 24 heures : " + ", ".join(morceaux) + ".")

    recents = [a for a in articles if _est_recent(a)]
    if recents:
        from collections import Counter
        dominante = Counter(a["categorie"] for a in recents).most_common(1)[0]
        phrases.append(
            f"{len(recents)} sujets depuis hier, surtout en {dominante[0]}."
        )

    return " ".join(phrases) or None


def _est_recent(article, heures=24):
    try:
        publie = datetime.fromisoformat(article["publie_le"])
    except Exception:  # noqa: BLE001
        return False
    return (datetime.now(timezone.utc) - publie).total_seconds() < heures * 3600
