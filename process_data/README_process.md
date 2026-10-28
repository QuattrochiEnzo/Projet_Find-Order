process_data — Prétraitements, enrichissements et benchmarks

Ce dossier regroupe l’ensemble des scripts Python dédiés à la préparation des données du problème de slotting logistique.

Il constitue la couche analytique et expérimentale du projet POIP.

🎯 Objectifs

Les objectifs principaux sont :

analyser les commandes et la structure des distances,

enrichir les instances avec des scores et métriques exploitables,

générer des sous-instances contrôlées,

construire des benchmarks expérimentaux,

produire des features pour les heuristiques C++,

constituer des datasets pour des approches ML.

⚠️ Garantie contractuelle

Les scripts de process_data/ :

n’altèrent jamais les contraintes du problème,

ne modifient pas la faisabilité,

ne modifient pas les formats définis dans
SPECIFICATION_FORMATS.md 

SPECIFICATION_FORMATS

.

Ils produisent uniquement des métriques informatives.

🏗 Vue d’ensemble

Pipeline conceptuel :

instance brute
↓
analyse commandes & distances
↓
features produits / circuits / allées
↓
instance enrichie (enriched/)
↓
export vers instance.dat
↓
solveur C++
↓
validation / analyse

Les résultats sont écrits dans :

enriched/

bench_*

dossiers d’analyse intermédiaires

🔁 Intégration avec le pipeline global

Les fichiers générés dans enriched/ sont automatiquement :

détectés par tools/export_instance.py

injectés dans instance.dat

transmis aux solveurs C++

Les métriques deviennent alors :

PMET (product metrics)

RMET (rack metrics)

AMET (aisle metrics)

CMET (circuit metrics)

👉 Cela garantit une transmission propre Python → C++ sans duplication de parsing.

📦 Format contractuel des métriques

Les métriques vectorielles doivent respecter un format strict :

n
v1
v2
...
vn

Contraintes :

première ligne = taille

exactement un scalaire par ligne

la taille doit correspondre exactement à :

num_products

num_racks

num_aisles

num_circuits

Tout fichier non conforme est ignoré.

Ce mécanisme garantit :

robustesse,

absence d’ambiguïté,

cohérence entre modules.

🧠 Script central
main_data.py

Point d’entrée principal.

Fonctions :

charge une ou plusieurs instances,

applique des méthodes d’enrichissement,

écrit les résultats (JSON / CSV / TXT),

gère les écrasements (--overwrite).

Exemple :

python -m process_data.main_data \
  --instance data/bench_milp \
  --out enriched \
  --methods popularity order_stats neighbors profiles actionable \
  --overwrite
📊 Analyse des commandes
order_stats.py

Analyse statistique brute :

fréquences produits,

co-occurrences,

tailles de commandes,

statistiques globales.

Complexité dominée par le nombre total de produits commandés.

Sert de base aux autres modules.

popularity.py

Calcul de la popularité :

fréquence normalisée,

percentiles,

classements.

Utilisée directement par :

heuristiques gloutonnes,

analyses expérimentales.

🔗 Relations et affinités
neighbors.py

Construction des affinités produits :

co-occurrence,

similarité Jaccard,

pondération via alpha.

Paramètres :

k : nombre de voisins conservés

alpha : pondération fréquence / affinité

Peut devenir coûteux si la matrice produit-produit est dense.

profiles.py

Construction de profils agrégés :

popularité,

voisinages,

circuit,

métriques synthétiques.

Objectif :

fournir des features stables,

simplifier l’exploitation côté C++.

🏭 Circuits et allées
circuit_stats.py

Statistiques par circuit :

volume,

dispersion,

taille,

métriques dérivées.

Utilisé pour :

ordonnancement des circuits,

analyse structurelle.

aisle_entry_exit.py

Scores liés aux allées :

distance entrée → allée,

distance allée → sortie,

accessibilité.

Guide les heuristiques vers des allées favorables.

🎯 Scores exploitables
actionable.py

Fusion multi-métriques :

popularité,

profils,

distances,

stats circuits / allées.

C’est le pont direct vers le C++.

Les scores produits ici sont injectés comme PMET.

🧩 Infrastructure interne
instance_api.py

Interface interne stable.

Encapsule la lecture brute.

io_utils.py

Utilitaires I/O :

JSON

CSV

gestion dossiers

paths.py

Centralisation stricte des chemins.

⚠️ Aucun chemin en dur.

__init__.py

Déclare le module Python.

🧪 Génération de sous-instances
Objectifs

Les grandes instances ne sont pas résolubles à l’optimal via MILP.

On génère des sous-instances pour :

comparer glouton vs optimal,

observer des patterns,

créer un dataset ML supervisé.

make_subinstance.py

Principe :

sélection contrôlée de produits,

conservation des circuits,

filtrage commandes,

maintien distances et allées.

Respect strict de :

SPECIFICATION_FORMATS.md 

SPECIFICATION_FORMATS

contraintes d’origine.

Paramètres contrôlés

nombre produits

nombre circuits

nombre commandes

stratégie sélection

seed

🔬 Reproductibilité expérimentale

Toutes les opérations aléatoires :

sélection produits

tirage commandes

génération benchmarks

peuvent être contrôlées via seed.

Recommandations :

toujours fixer la seed pour publication

générer plusieurs sous-instances par configuration

comparer glouton et MILP

📈 Benchmarks
generate_benchmark.py

Création lots d’instances.

run_heuristic_bench.py

Benchmark heuristiques :

exécutions multiples

collecte coût / temps

sauvegarde CSV

run_solvers_benchmark.py

Comparaison :

heuristiques vs MILP

multi-instances

multi-paramètres

🤖 Dataset ML
Inputs

popularité

profils

circuits

affinités

distances

Features

produit

pairwise

spatiales

Conçues pour être :

comparables entre instances,

indépendantes d’une solution.

Labels

Dérivés des solutions MILP :

affectation produit → rack

allée cible

intervalle circuit

relations proximité

La solution gloutonne sert de baseline.

📌 Exemple pipeline complet

1️⃣ Génération sous-instances :

python -m process_data.make_subinstance \
  --instance data/warehouse_big \
  --out data/subinstances \
  --n 20 \
  --seed 1

2️⃣ Heuristique :

python -m process_data.run_heuristic_bench \
  --instances data/subinstances

3️⃣ MILP :

python -m process_data.run_solvers_benchmark \
  --instances data/subinstances \
  --solver gurobi
⚠️ Limitations

Les affinités peuvent être coûteuses à calculer.

Les sous-instances peuvent introduire des biais si mal contrôlées.

Les patterns observés doivent être validés sur plusieurs configurations.

🧠 Philosophie

Python analyse

C++ décide

Formats contractuels

Reproductibilité stricte

Aucune altération des contraintes