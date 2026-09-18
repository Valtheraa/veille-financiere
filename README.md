# Veille financière

Un collecteur qui tourne tout seul toutes les heures, et un tableau de bord web
que tu installes comme une application sur ton téléphone et ton PC.

Couvre : taux et crédit immobilier, marchés et ETF, crypto, macro et
réglementation AMF, géopolitique à effet marché.

## Ce que ça coûte

Rien.

| Poste | Coût |
| --- | --- |
| Hébergement du tableau de bord (GitHub Pages) | 0 € |
| Exécution horaire du collecteur (GitHub Actions) | 0 € sur dépôt public |
| Flux RSS officiels (BCE, Fed, Banque de France, AMF, INSEE) | 0 € |
| Google Actualités en RSS | 0 €, sans clé, sans quota |
| Cours et taux (BCE, Yahoo, Stooq, CoinGecko, Frankfurter) | 0 €, sans clé |
| Résumé du jour écrit par Claude — **facultatif** | environ 0,20 €/mois |

Le seul cas où tu paierais vraiment, c'est du temps réel boursier propre
(Bloomberg, Refinitiv, ou une API type Polygon) : plusieurs dizaines d'euros
par mois, et inutile pour de la veille.

Une seule limite à connaître : sur un dépôt **privé**, GitHub offre
2 000 minutes d'Actions par mois. Une collecte prend moins d'une minute, donc
17 par jour tiennent largement dedans. Sur un dépôt **public**, c'est illimité.
Comme il n'y a aucune donnée personnelle ici, le public est le choix simple.

## Comment la page est organisée

En haut, toujours visibles : le taux de dépôt BCE en gros, le CAC 40, le
Bitcoin, le résumé du jour et les alertes en cours.

En dessous, trois vues qui ne se mélangent pas :

- **Actualités** — le flux, ses rubriques, la recherche, les sujets gardés et
  la vue hebdomadaire. Le compteur doré indique ce qui est arrivé depuis ta
  dernière visite.
- **Chiffres** — 76 indicateurs rangés comme chez Trading Economics : on
  choisit un pays (France, zone euro, États-Unis, Royaume-Uni, Allemagne,
  Italie, Japon, Monde), on lit ses thèmes. Une recherche traverse tous les
  pays d'un coup. Cliquer sur un chiffre ouvre les articles qui en parlent.

Les trois chiffres du haut suivent le pays choisi : sur l'onglet États-Unis,
c'est le taux de la Fed qui domine, pas celui de la BCE. Ce choix se règle dans
la section `heros:` de `collector/indicateurs.yaml`.
- **Agenda** — les prochains rendez-vous, avec un compteur doré pour ceux qui
  tombent dans les sept jours.

La vue et la famille choisies sont mémorisées : tu retrouves la page là où tu
l'avais laissée.

## Mise en route

1. Crée un dépôt GitHub, par exemple `veille`, et pousse ce dossier dedans.
2. Dans **Settings → Pages**, choisis la source « Deploy from a branch »,
   branche `main`, dossier `/docs`. Enregistre.
3. Dans **Settings → Actions → General**, section *Workflow permissions*,
   coche « Read and write permissions ». Sans ça le bot ne peut pas publier
   ses résultats.
4. Onglet **Actions → Collecte de la veille → Run workflow**. La première
   collecte prend une minute.
5. Ouvre `https://<ton-pseudo>.github.io/veille/`. Ton tableau de bord est en ligne.
6. Sur iPhone : Safari → Partager → « Sur l'écran d'accueil ». Sur Android ou
   sur PC : le navigateur propose « Installer ». Tu auras une icône, un
   lancement plein écran, et la lecture reste possible hors ligne.

Ensuite, plus rien à faire : le collecteur passe toutes les heures entre 7 h et
23 h (heure française) et met le tableau de bord à jour tout seul.

### Résumé du jour

Un résumé factuel est calculé à chaque collecte sans aucune clé : alertes du
moment, plus fortes variations sur 24 heures, volume de sujets et rubrique
dominante.

### Les sources de la macro européenne

L'inflation, le chômage, le PIB, la dette, le déficit, la production
industrielle, la confiance des ménages et les prix des logements viennent
d'**Eurostat**, gratuit et sans clé. Les séries équivalentes de la BCE se sont
révélées arrêtées fin 2025 ou introuvables : la BCE ne sert plus que pour ce
qu'elle publie le mieux, les taux directeurs, l'Euribor et les taux souverains.

### Données américaines complètes (facultatif)

Chômage, inflation, taux 10 ans et 2 ans américains fonctionnent sans rien
configurer : ils viennent du Bureau of Labor Statistics et du Trésor.

Six lignes passent par FRED, la base de la Fed de Saint-Louis, qui demande une
clé : le **taux directeur de la Fed** (la cible annoncée, celle que citent les
journaux), les créations d'emplois, le PIB américain, la dette et le déficit
américains, l'inflation japonaise. Sans la clé, le taux directeur est remplacé
à l'écran par le taux effectif, constaté sur le marché, qui suit la cible avec
un jour de retard — les deux diffèrent donc le jour d'une décision. Elle est gratuite et immédiate :
crée un compte sur fredaccount.stlouisfed.org, demande une clé API, puis
ajoute-la en secret GitHub sous le nom `FRED_API_KEY`. Sans elle, ces deux
lignes affichent « indisponible » et le reste fonctionne normalement.

### Résumé rédigé par Claude (facultatif)

Dans **Settings → Secrets and variables → Actions**, ajoute un secret nommé
`ANTHROPIC_API_KEY` avec une clé de la console Anthropic. Le résumé calculé est
alors remplacé par quatre phrases rédigées, et Claude tranche en plus les
regroupements douteux : deux titres qui parlent visiblement du même événement
sans partager le même vocabulaire sont fusionnés. Sans ce secret, tout le reste
fonctionne à l'identique.

## Tester en local

```bash
pip install -r collector/requirements.txt
python collector/fetch.py --check     # teste chaque source, n'écrit rien
python collector/fetch.py             # collecte et écrit docs/data/feed.json
python collector/fetch.py --check-chiffres   # teste les 47 indicateurs un par un
python -m http.server -d docs 8000    # puis ouvre http://localhost:8000
```

Pour voir l'interface avant la première collecte :
`cp docs/data/feed.exemple.json docs/data/feed.json` (les chiffres et les
titres de ce fichier sont fictifs, la première vraie collecte les remplace).

## Être prévenu sans ouvrir la page

`collector/alertes.yaml` décrit les franchissements qui méritent un signal :
la BCE qui bouge, le Livret A qui change, l'écart France / Allemagne au-dessus
de 80 points de base, le VIX au-dessus de 25, le CAC 40 qui varie de plus de
2,5 % dans la journée.

Quand une règle se déclenche, l'action GitHub ouvre une issue sur le dépôt, et
GitHub t'envoie le mail. Aucun serveur, aucun identifiant SMTP, aucun réglage.
Une alerte ne sonne qu'au franchissement : tant que le seuil reste dépassé,
elle se tait.

## Les rendez-vous à venir

`collector/agenda.yaml` alimente le bloc « Prochains rendez-vous ». Trois
écritures : une date unique, `recurrent: annuel` avec un jour-mois, ou
`recurrent: mensuel` avec un jour. Les échéances structurelles sont déjà là
(révisions du Livret A, loi de finances, publications INSEE).

Les dates de décision de la BCE et de la Fed sont déjà dedans jusqu'à
septembre 2027, ainsi que les publications récurrentes : emploi américain le
premier vendredi du mois, inflation américaine vers le 12, inflation française
le dernier jour du mois, inflation et chômage de la zone euro en début de mois.
Ces calendriers sont publiés un an à l'avance : à recompléter chaque automne
depuis ecb.europa.eu et federalreserve.gov. La vue Agenda affiche les
vingt-quatre prochaines échéances.

## La mémoire longue

Trois mécanismes, complémentaires :

- **L'archive** (`docs/data/archives/`) garde titre, source, date et lien de
  tout ce qui est passé, un fichier par mois plus un index. Dès que tu tapes
  trois lettres dans la recherche, le navigateur remonte les mois du plus
  récent au plus ancien et s'arrête dès qu'il a douze résultats : il ne
  télécharge jamais tout l'historique.
- **La rétention par source** : les publications de fond (Banque de France,
  AMF, INSEE, BOFiP, ACPR, ESMA, Trésor) restent 180 jours au lieu de 21.
  C'est le `conserver:` dans `sources.yaml`.
- **Les épingles** : le bouton « Garder » met un sujet de côté dans ton
  navigateur, dans un onglet dédié, indéfiniment.

## Savoir ce que tu lis vraiment

Le bouton « Mes lectures » en pied de page compare, source par source, ce que
tu ouvres et ce qui est produit. Une source qui sort trente sujets et que tu
n'ouvres jamais n'a rien à faire dans `sources.yaml`. Le relevé se copie en un
clic. La case « Mes sources » réordonne le flux en mettant en tête ce que tu
ouvres le plus souvent.

Ces compteurs restent dans ton navigateur : ils ne sont ni publiés, ni
envoyés nulle part.

## Les garde-fous

- **Cohérence des chiffres** : si une valeur s'écarte de plus de 25 % (ou de
  1,5 point pour un taux) de celle de la veille, c'est une API qui a changé de
  format, pas un événement. La valeur reste affichée mais n'entre pas dans
  l'historique, et l'anomalie remonte dans le bandeau et dans le mail.
- **Panne de Google Actualités** : si huit recherches sur dix deviennent
  muettes le même jour, c'est signalé comme tel, et non confondu avec une
  journée creuse.
- **Diversité** : le tri « Mes sources » n'aligne jamais plus de deux sujets
  d'affilée venant du même média.

## Sauvegarder ce qui n'existe que chez toi

Les sujets gardés et les compteurs de lecture vivent dans un seul navigateur.
« Sauvegarder mes gardés » les copie ; « Restaurer » les recolle sur un autre
appareil, en fusionnant plutôt qu'en écrasant. C'est la seule donnée du projet
qui n'est pas reconstructible.

## Quand quelque chose casse

Un second workflow, `vigie.yml`, tourne une fois par jour indépendamment du
collecteur. Il ouvre une issue si la dernière collecte date de plus de six
heures, si des sources sont injoignables, ou si plus de quatre indicateurs
sont en échec. Le collecteur, lui, signale ses propres plantages.

Pour vérifier les chiffres à la main :

```bash
python collector/fetch.py --check-chiffres        # teste les 47 indicateurs
python collector/fetch.py --inspecter inflation_fr # montre ce que la série renvoie vraiment
```

`--inspecter` affiche le nom exact de la série chez la source et ses six
dernières observations. C'est l'outil à sortir quand un chiffre semble faux :
il dit si l'erreur vient de la série choisie ou de son interprétation.

## L'historique

Chaque collecte archive une valeur par jour et par indicateur dans
`docs/data/historique.json`, sur 400 jours. C'est ce qui alimente la
micro-courbe à gauche de chaque chiffre et la variation sur 30 jours affichée
au survol. Le fichier se construit tout seul : les courbes apparaissent après
quatre jours de collecte.

## Régler les chiffres

`collector/indicateurs.yaml` liste les 44 indicateurs, en français. Chaque ligne
porte un `source` (`bce`, `fed`, `boe`, `marche`, `change`, `crypto`, `global`,
`peur`, `manuel`, `calcule`) et la clé de la série correspondante.

- pour suivre un ETF ou une action, copie un bloc du groupe `indices` et mets le
  code Yahoo Finance dans `cle` (`CW8.PA`, `ESE.PA`, `AAPL`…)
- pour masquer une ligne, `actif: false`
- les taux de l'épargne réglementée sont récupérés automatiquement sur le
  portail Webstat de la Banque de France, qui est ouvert et sans clé. Le LDDS
  est déduit du Livret A, auquel la loi l'égalise. Si Webstat ne répond pas, la
  valeur écrite dans le fichier prend le relais et le tableau de bord signale
  qu'il fonctionne sur un repli
- plus aucune valeur n'est saisie à la main : tout vient d'une source qui se
  met à jour seule. Les taux de crédit immobilier ont été retirés faute d'API
  fiable — ils restent suivis par l'actualité, dans la rubrique Taux & crédit
- `calcule` fait une soustraction entre deux indicateurs : c'est ainsi que sont
  obtenus l'écart France / Allemagne et le rendement réel du Livret A

Les valeurs crypto sont récupérées en euro et en dollar dans le même appel : la
bascule € / $ en tête du groupe change l'affichage sans recharger.

## Régler ce que tu suis

Tout est dans `collector/sources.yaml`, en français, sans code.

- `flux` : les RSS officiels. Chacun porte un `secours_q` : si le site refuse
  le robot ou refait ses URL, le collecteur bascule tout seul sur une recherche
  ciblée sur le même sujet, et le pied de page indique « en repli ». Une source
  se désactive avec `actif: false`.
- `recherches` : des requêtes de mots-clés transformées en flux Google
  Actualités. C'est le levier principal : ajoute un bloc avec un `q` et tu
  suis un nouveau sujet. La syntaxe Google marche (`"guillemets"`, `OR`,
  `-exclusion`, `site:lesechos.fr`).
- `poids` : de 1 à 3, fait remonter la source dans le classement.
- `exiger:` : le titre doit contenir au moins un de ces mots. C'est
  indispensable en français, où « arrêté » ramène autant de faits divers que de
  textes réglementaires.
- `exclure:` : mots qui disqualifient un titre pour cette source.
- `exclure_partout:` en haut du fichier : faits divers, lois de finances
  étrangères, pages de cotation, offres d'emploi. Écarté quelle que soit la
  source.
- chaque source est plafonnée à 30 articles par collecte, et chaque média à 6
  sujets : un site qui publie quinze variations du même titre ne peut plus
  occuper la page.
- `signaux_forts` : les mots qui font passer un article en tête et lui
  donnent son liseré doré.
- `bruit` : les titres à jeter.

Après chaque modification, lance `python collector/fetch.py --check` : il liste
les sources injoignables ou vides, et le pied de page du tableau de bord
affiche en permanence combien de sources sont à corriger.

## Comment c'est fait

```
collector/sources.yaml      les flux et les recherches
collector/indicateurs.yaml  les 47 chiffres
collector/agenda.yaml       les rendez-vous à venir
collector/alertes.yaml      les seuils qui déclenchent un mail
collector/fetch.py          collecte, déduplique, note, écrit le JSON
collector/markets.py        récupère chaque chiffre à sa source
collector/analyse.py        historique, alertes, agenda, regroupement
docs/index.html             le tableau de bord (aucune dépendance, aucun build)
docs/data/feed.json         ce que lit le tableau de bord
docs/data/historique.json   l'archive qui alimente les courbes
.github/workflows/          la collecte horaire
```

Les articles qui racontent la même chose sont regroupés : le mieux noté porte
le sujet, les reprises apparaissent sous son titre en « Aussi chez… ». Un
bouton copie un article avec sa source et son lien, un autre copie d'un coup
tout ce qui est affiché à l'écran.

Le collecteur garde 21 jours d'historique et retient la date à laquelle il a
vu un article pour la première fois : c'est ce qui permet au tableau de bord
de te montrer uniquement ce qui est arrivé depuis ta dernière visite.

Les titres et les liens proviennent des éditeurs : le tableau de bord affiche
un titre et renvoie vers l'article d'origine, il ne recopie pas le contenu.
