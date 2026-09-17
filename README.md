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

### Résumé du jour par Claude (facultatif)

Dans **Settings → Secrets and variables → Actions**, ajoute un secret nommé
`ANTHROPIC_API_KEY` avec une clé de la console Anthropic. Le collecteur fera
alors écrire quatre phrases de synthèse en tête de page. Sans ce secret, tout
le reste fonctionne à l'identique.

## Tester en local

```bash
pip install -r collector/requirements.txt
python collector/fetch.py --check     # teste chaque source, n'écrit rien
python collector/fetch.py             # collecte et écrit docs/data/feed.json
python -m http.server -d docs 8000    # puis ouvre http://localhost:8000
```

Pour voir l'interface avant la première collecte :
`cp docs/data/feed.exemple.json docs/data/feed.json` (les chiffres et les
titres de ce fichier sont fictifs, la première vraie collecte les remplace).

## Régler ce que tu suis

Tout est dans `collector/sources.yaml`, en français, sans code.

- `flux` : les RSS officiels. Une source qui meurt se désactive avec
  `actif: false`.
- `recherches` : des requêtes de mots-clés transformées en flux Google
  Actualités. C'est le levier principal : ajoute un bloc avec un `q` et tu
  suis un nouveau sujet. La syntaxe Google marche (`"guillemets"`, `OR`,
  `-exclusion`, `site:lesechos.fr`).
- `poids` : de 1 à 3, fait remonter la source dans le classement.
- `signaux_forts` : les mots qui font passer un article en tête et lui
  donnent son liseré doré.
- `bruit` : les titres à jeter.

Après chaque modification, lance `python collector/fetch.py --check` : il liste
les sources injoignables ou vides, et le pied de page du tableau de bord
affiche en permanence combien de sources sont à corriger.

## Comment c'est fait

```
collector/sources.yaml   ce que tu suis
collector/fetch.py       collecte, déduplique, note, écrit le JSON
collector/markets.py     taux BCE, indices, crypto, change
docs/index.html          le tableau de bord (aucune dépendance, aucun build)
docs/data/feed.json      le seul fichier d'échange
.github/workflows/       la collecte horaire
```

Le collecteur garde 21 jours d'historique et retient la date à laquelle il a
vu un article pour la première fois : c'est ce qui permet au tableau de bord
de te montrer uniquement ce qui est arrivé depuis ta dernière visite.

Les titres et les liens proviennent des éditeurs : le tableau de bord affiche
un titre et renvoie vers l'article d'origine, il ne recopie pas le contenu.
