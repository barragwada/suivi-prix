# Suivi du prix GVA ⇄ PTP

Chaque jour, GitHub lance `tracker.py`. Le script demande à l'API Ignav les offres pour tes dates,
repère **ta combinaison exacte** (AF1843 + AF758 à l'aller, AF751 + AF1542 au retour), garde le prix
dans `data/prices.csv`, redessine la page `docs/index.html` et t'envoie une notification sur ton
iPhone quand il y a quelque chose d'utile à savoir.

## Mise en route (environ 15 minutes)

1. **Notifications** : installe l'app gratuite **ntfy** sur l'iPhone. Abonne-toi à un sujet dont le nom
   est long et impossible à deviner (par exemple `gva-ptp-` suivi de 12 caractères au hasard).
   Ce nom joue le rôle d'un mot de passe : ne le publie nulle part.
2. **Clé API** : crée ton compte sur ignav.com et copie ta clé.
3. **Dépôt GitHub** : crée un dépôt **public** (nécessaire pour la page gratuite) avec un nom neutre,
   puis envoie-y le contenu de ce dossier.
4. **Secrets** : dans le dépôt, *Settings → Secrets and variables → Actions*, ajoute
   `IGNAV_API_KEY` (ta clé) et `NTFY_TOPIC` (le nom du sujet ntfy).
5. **Page web** : *Settings → Pages*, source « Deploy from a branch », branche `main`, dossier `/docs`.
6. **Premier relevé** : onglet *Actions → Suivi quotidien du prix → Run workflow*.
   Vérifie que le prix et les horaires affichés correspondent à ce que tu vois sur le site d'Air France.

## Réglages (`config.json`)

| Clé | Rôle |
|---|---|
| `market` | Pays de tarification, qui fixe la devise (`FR` pour des euros, `CH` pour des francs suisses) |
| `target_price` | Ton prix cible. Quand le prix passe dessous, tu reçois une alerte prioritaire. `null` pour désactiver |
| `alert_min_drop` | Baisse minimale (dans la devise) par rapport à la veille pour déclencher une alerte |
| `missing_alert_after_days` | Nombre de jours sans trouver la combinaison avant de t'alerter |
| `weekly_recap_weekday` | Jour du récap hebdomadaire (0 = lundi … 6 = dimanche) |

## Notifications

Une seule par jour au maximum, et seulement si elle apporte quelque chose : premier relevé, nouveau
plus bas, passage sous l'objectif, baisse notable, combinaison introuvable pendant plusieurs jours,
et un récap le dimanche. Un appui sur la notification ouvre la page.

## À savoir

- Le dépôt étant public, on peut y lire tes dates et tes numéros de vol (pas ton nom). Ta clé API et
  ton sujet ntfy restent secrets.
- Le suivi s'arrête tout seul après la date de départ.
- Les prix sont ceux d'un moteur de recherche, pas un tarif garanti : vérifie toujours sur le site de
  la compagnie avant de réserver.

## Tester sans rien installer

`python tracker.py --demo` génère `demo.html` avec de fausses données pour voir le rendu.
