metaheuristics — Solveurs et heuristiques de slotting

Ce dossier contient l’implémentation C++ des solveurs du problème de slotting logistique.

Il constitue le cœur décisionnel du projet POIP :

heuristiques gloutonnes rapides et scalables,

modèles exacts MILP avec Gurobi (validation / instances réduites).

🎯 Objectif du module C++

Affecter des produits à des racks afin de :

Minimiser la distance totale de picking

sous contraintes opérationnelles strictes :

capacité des racks,

aération minimale par allée,

contiguïté des circuits (familles logistiques),

respect des racks spéciaux (START = 0, END = n−1).

Les contraintes sont définies formellement dans
SPECIFICATION_FORMATS.md 

SPECIFICATION_FORMATS

.

🏗 Architecture générale
Séparation des responsabilités
Python (tools/)	C++ (metaheuristics/)
Parsing des instances	Décision algorithmique
Validation des contraintes	Construction d’une affectation
Calcul officiel du coût	Optimisation interne
Visualisation	Export solution

👉 Le C++ ne valide jamais officiellement une solution.
Toute solution produite doit passer par tools/checker.py.

📂 Arborescence
metaheuristics/
├── common/                 Structures partagées
│   ├── instance.hpp/.cpp
│   └── solution.hpp/.cpp
│
├── heuristics/             Heuristiques & MILP
│   ├── heuristic_blanck.cpp
│   ├── heuristic_greedy_circuit.cpp
│   ├── heuristic_greedy_circuit_smart.cpp
│   ├── gurobi_slotting_toy.cpp
│   ├── gurobi_model2.cpp
│   └── gurobi_new_objectif.cpp
│
├── cmake/
│   └── FindGUROBI.cmake
│
├── test_load.cpp
├── CMakeLists.txt
└── README_metaheuristics.md
📦 Format d’entrée utilisé par le C++
instance.dat

Les solveurs C++ ne lisent jamais directement les fichiers .txt bruts.

Ils utilisent exclusivement :

instance.dat

Ce fichier est généré par :

python tools/export_instance.py --instance market

Il contient :

META (dimensions globales)

CAP (capacités)

CIR (circuit de chaque produit)

AIS (structure des allées)

ADJ (matrice des distances)

ORD (commandes)

métriques optionnelles (PMET, RMET, AMET, CMET)

Cela garantit :

cohérence totale entre Python et C++

parsing simplifié

reproductibilité

🧠 common/ — Modèle interne partagé
instance.hpp / instance.cpp

Représente une instance chargée depuis instance.dat.

Contient :

produits

racks

circuits

capacités

allées

matrice d’adjacence

commandes

👉 Tous les solveurs utilisent exactement cette structure.

solution.hpp / solution.cpp

Représente une solution :

vecteur product → rack

calcul interne du coût

fonctions de vérification basiques

⚠️ Le coût interne est cohérent avec la matrice d’adjacence,
mais la validation officielle est faite en Python.

🏃 Heuristiques gloutonnes
1️⃣ heuristic_blanck.cpp

Heuristique baseline minimale.

placement simple

aucune logique avancée

Usage :

test pipeline

sanity check

comparaison de base

2️⃣ heuristic_greedy_circuit.cpp

Heuristique orientée circuits.

Stratégie :

placement circuit par circuit

intervalle contigu de racks

respect strict capacités + aération

Avantages :

rapide

respecte les contraintes

Limite :

peut échouer si fragmentation locale

3️⃣ heuristic_greedy_circuit_smart.cpp

Heuristique principale du projet.

Ajouts :

scores produits / allées

pondération paramétrable

gestion dynamique des priorités

Mécanisme RECOVERY

Si un circuit ne rentre pas :

compactage local

compactage moyen

compactage global

LEFT-PACK global

Objectif :

Maximiser la probabilité d’obtenir une solution faisable.

👉 Utilisée pour les grandes instances industrielles.

🧮 Modèles exacts (MILP) — Gurobi

⚠️ Réservés aux instances réduites.

gurobi_slotting_toy.cpp

Modèle exact :

variables binaires produit → rack

contraintes de capacité

contraintes d’aération

contiguïté via bornes (L_c, U_c)

✔️ Optimalité garantie
❌ Non scalable

gurobi_model2.cpp

Formulation expérimentale :

reformulations

relaxations partielles

gurobi_new_objectif.cpp

Objectif alternatif :

pondérations

corrélations

termes expérimentaux

⚠️ Les coûts peuvent ne pas être comparables directement.

▶️ Exécution des solveurs

Après compilation :

metaheuristics/build/bin/
Heuristique simple
./bin/heuristic_greedy_circuit instance.dat solutions/
Heuristique smart
./bin/heuristic_greedy_circuit_smart instance.dat solutions/
Exemple avec paramètres (si supportés)
./bin/heuristic_tabu_circuit_blocks market \
  --iters 200 \
  --k 2000 \
  --seed 1
🔁 Cycle complet d’utilisation

1️⃣ Export instance :

python tools/export_instance.py --instance market

2️⃣ Lancer solveur C++ :

./bin/heuristic_greedy_circuit_smart instance.dat solutions/

3️⃣ Validation officielle :

python tools/checker.py \
  --instance market \
  --solution solutions/rack_product_assignment.txt

4️⃣ Visualisation :

python tools/visualisation_solution.py \
  --instance market \
  --solution solutions/rack_product_assignment.txt
⚙️ Compilation
Dépendances

C++17

Gurobi (optionnel)

Build standard
mkdir -p metaheuristics/build
cd metaheuristics/build
cmake ..
make
Optimisation recommandée (Release)
cmake -DCMAKE_BUILD_TYPE=Release ..
make

Recommandé :

-O3 -march=native

pour accélérer les heuristiques.

📈 Scalabilité
Méthode	Complexité	Usage
heuristic_blanck	très faible	debug
greedy_circuit	faible	rapide
greedy_circuit_smart	modérée	production
MILP	exponentielle	petites instances
⚠️ Limitations

Les MILP ne passent pas à l’échelle.

Les heuristiques peuvent converger vers des minima locaux.

Les solutions doivent être validées côté Python.

🧩 Philosophie finale

C++ = moteur d’optimisation

Python = contrôle qualité

Formats contractuels

Reproductibilité par seeds

Validation systématique