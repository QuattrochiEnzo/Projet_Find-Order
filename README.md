
# POIP — Slotting en entrepôt sous contraintes opérationnelles

Ce projet traite un **problème de slotting logistique** :
affecter des produits à des racks dans un entrepôt afin de **minimiser la distance totale de picking**, tout en respectant des contraintes opérationnelles fortes.

Le projet combine :

* **prétraitements de données (Python)**,
* **métaheuristiques et modèles exacts (C++)**,
* **outils de validation et de visualisation (Python)**,
* **analyses expérimentales et approches par apprentissage (RL)**.

Il a été développé dans un cadre académique (projet M2), avec une attention particulière portée à :

* la robustesse,
* la reproductibilité,
* la séparation claire des responsabilités,
* la structuration modulaire.

---

## Problème étudié

### Objectif

Minimiser la distance totale de picking induite par les commandes clients.

### Contraintes principales

* chaque produit est affecté à **un seul rack**,
* la **capacité des racks** est limitée,
* un pourcentage minimal de racks doit rester libre par allée (**aération**),
* les produits d’un même **circuit / famille logistique** doivent être placés dans des racks **contigus**,

  * des racks obstacles ou d’aération peuvent exister entre ces racks.

---

## Philosophie du projet

Principes structurants :

* **Séparation stricte des rôles**

  * Python : données, orchestration, validation, analyse , apprentissage par renforcement (RL)
  * C++ : décision algorithmique uniquement
* **Formats uniques et stricts**

  * définis dans `SPECIFICATION_FORMATS.md`
  * aucun parsing dupliqué
* **Validation systématique**

  * toute solution passe par `tools/checker.py`
* **Robustesse avant optimalité**

  * une solution faisable > une solution idéale mais invalide
* **Modularité**

  * chaque composant peut évoluer indépendamment

Les approches par apprentissage sont étudiées comme compléments exploratoires aux métaheuristiques, et non comme substituts directs.

---

## Vue d’ensemble du pipeline

Pipeline logique global :

```
instance brute
↓
process_data/        (analyses, enrichissements, benchmarks)
↓
tools/main.py        (orchestration des solveurs)
↓
metaheuristics/      (heuristiques / MILP)
↓
tools/checker.py     (validation + coût)
↓
analysis/            (visualisations, comparaisons, figures)
```

---

## Arborescence du projet

```
project/
├── metaheuristics/         Solveurs C++ (heuristiques + MILP)
│   └── README_metaheuristics.md
│
├── process_data/           Prétraitements et enrichissements
│   └── README_process.md
│
├── tools/                  Orchestration, validation, visualisation
│   └── README_tools.md
│
├── analysis/               Analyses expérimentales et figures
│   └── README_analysis.md
│
├── RL/                     Approches par Reinforcement Learning
│   └── README_RL.md
│
├── data/                   Instances et solutions
├── SPECIFICATION_FORMATS.md
├── run_bench_heuristic.sh
└── README.md
```

---

## Description des sous-modules

### `process_data/` — Préparation et enrichissement

* analyse des commandes,
* calcul de popularité, affinités, profils,
* génération de scores exploitables par les heuristiques,
* génération de sous-instances et benchmarks.

👉 Voir `process_data/README_process.md`

---

### `metaheuristics/` — Solveurs C++

* heuristiques gloutonnes rapides et robustes,
* métaheuristiques (Tabu Search, variantes),
* modèles exacts MILP avec Gurobi (instances jouets),
* cœur algorithmique du projet.

👉 Voir `metaheuristics/README_metaheuristics.md`

---

### `tools/` — Orchestration et validation

* chargement unique des instances,
* lancement des solveurs,
* validation stricte des contraintes,
* calcul des coûts,
* visualisation des solutions (SVG).

👉 Voir `tools/README_tools.md`

---

### `analysis/` — Analyse expérimentale

Module dédié à l’évaluation des méthodes :

* comparaison greedy vs tabu,
* évolution du proxy score,
* études de sensibilité (itérations, paramètres),
* génération de figures pour le rapport.

Ce module ne contient **aucune logique décisionnelle** :
il exploite uniquement les solutions produites.

Il permet :

* la reproductibilité des expériences,
* l’évaluation comparative des méthodes,
* la production des figures académiques.

👉 Voir `analysis/README_analysis.md`

---

### `RL/` — Approches par apprentissage

Module exploratoire intégrant des approches par **Reinforcement Learning**.

Objectifs :

* apprendre une politique d’affectation produit → rack,
* comparer les performances avec les métaheuristiques,
* analyser la capacité d’un agent à respecter les contraintes structurelles.

Contenu typique :

* environnement simulant le problème de slotting,
* définition des états et actions,
* mécanisme de récompense basé sur le proxy ou la distance réelle,
* scripts d’entraînement et d’inférence.

Cette partie est expérimentale et vise à :

* explorer des alternatives aux métaheuristiques,
* étudier la stabilité et la généralisabilité,
* comparer approche constructive et approche apprenante.

👉 Voir `RL/README_RL.md`

---

## Lancement rapide

### Prétraitement des données

```bash
python -m process_data.main_data \
  --instance data/bench_milp \
  --out enriched \
  --methods popularity order_stats neighbors profiles actionable
```

### Lancer un solveur

```bash
python tools/main.py --instance market
```

### Vérifier une solution

```bash
python tools/checker.py \
  --instance market \
  --solution solutions/rack_product_assignment.txt
```

### Visualiser

```bash
python tools/visualisation_solution.py \
  --instance market \
  --solution solutions/rack_product_assignment.txt
```

---

## Benchmarks

* `run_bench_heuristic.sh` : lancement automatisé de benchmarks heuristiques
* scripts Python pour :

  * comparaison heuristiques vs MILP,
  * études de sensibilité (alpha, scores, ordres),
  * génération de tableaux comparatifs.

---

## À retenir

* Le projet est **modulaire**, **structuré** et **extensible**
* Python structure, C++ décide
* Les formats sont contractuels
* Toute solution non validée est considérée invalide
* Les approches RL sont exploratoires et complémentaires

---

## Auteurs

Projet réalisé dans le cadre du **Master 2**.

Paul-Henri Leplomb
Maïna Boivent
Enzo Quattrochi
