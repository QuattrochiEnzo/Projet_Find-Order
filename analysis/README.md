analysis — Visualisation et suivi expérimental

Ce dossier regroupe les scripts Python dédiés à l’analyse expérimentale des solutions produites par :

les heuristiques C++,

le Tabu Search,

les modèles exacts (Gurobi),

les approches RL.

Il constitue la couche post-solveur du pipeline POIP.

⚠️ Garantie contractuelle

Ce module :

❌ ne modifie jamais les instances

❌ ne modifie jamais les solutions

❌ ne modifie jamais les formats contractuels (SPECIFICATION_FORMATS.md)

Il se contente de :

charger les instances via WarehouseLoader,

valider / évaluer les solutions via checker.py,

produire des graphiques comparatifs,

générer des fichiers statistiques lisibles.

👉 Aucune logique métier n’est dupliquée.

🏗 Structure du dossier
analysis/
├── plot_solution_values.py
├── plot_solution_values_toy.py
├── plot_evolution_IA.py
├── figures/
└── README_analysis.md
📊 Position dans le pipeline global
instance
↓
solveur C++
↓
solutions/
↓
checker.py
↓
analysis/
↓
figures + stats

Ce dossier est purement analytique et expérimental.
Il ne doit jamais influencer la génération des solutions.

1️⃣ Comparaison des solutions — Instances BIG
plot_solution_values.py
🎯 Objectif

Comparer les coûts de picking entre :

Greedy

Greedy_Smart

Tabu

sur les instances :

category

family

market

⚙ Fonctionnement

Le script :

Charge chaque instance via WarehouseLoader

Lit les fichiers dans solutions/

Calcule le coût réel via checker.py

Génère :

graphique comparatif des coûts

graphique comparatif des temps

fichiers stat_<instance>.txt

⚠️ Les temps sont injectés manuellement (benchmark contrôlé).

▶ Lancer

Depuis la racine du projet :

python analysis/plot_solution_values.py
📁 Sorties

Affichage graphique interactif

Analyses/stat_category.txt

Analyses/stat_family.txt

Analyses/stat_market.txt

Analyses/times_comparison_big.png

2️⃣ Comparaison sur instance TOY
plot_solution_values_toy.py
🎯 Objectif

Comparer :

Greedy

Greedy_Smart

Tabu

Gurobi

sur l’instance toy.

Cette instance permet :

comparaison heuristique vs optimal

analyse fine des écarts relatifs

📈 Génère

graphique comparatif des coûts

graphique comparatif des temps
(log-scale si nécessaire)

Analyses/stat_toy.txt

▶ Lancer
python analysis/plot_solution_values_toy.py
3️⃣ Suivi d’entraînement RL
plot_evolution_IA.py
🎯 Objectif

Visualiser l’évolution du coût pendant :

l’entraînement
(feasible_ep*_cost*.txt)

l’inférence
(infer_step*_cost_*.txt)

📈 Méthodologie

Les courbes utilisent :

timestamp fichier → axe X

coût → axe Y (format millions)

marge dynamique pour lisibilité

Permet de suivre :

convergence,

stagnation,

amélioration après changement d’opérateur.

▶ Lancer
python analysis/plot_evolution_IA.py \
  --instance market \
  --mode auto \
  --seed 0
🔧 Options

--mode auto : swap_products puis destroy

--mode swap

--mode destroy

--seed : reproductibilité

📁 Sorties

Les figures sont enregistrées automatiquement dans :

analysis/figures/
📊 Dossier figures/

Contient :

courbes d’entraînement RL

courbes d’inférence RL

figures comparatives

Ce dossier est :

généré automatiquement

recommandé en .gitignore

nettoyable entre batchs d’expérimentations

🔁 Règles de cohérence projet

Ces scripts respectent la philosophie POIP :

aucun parsing manuel

toujours WarehouseLoader

toujours checker.py

aucune logique métier redondante

formats strictement conformes à SPECIFICATION_FORMATS.md

🧪 Reproductibilité expérimentale

Pour garantir des résultats cohérents :

fixer les seeds lorsque possible

documenter les temps injectés

conserver les fichiers stat_*.txt

comparer les solutions validées uniquement

📈 Types d’analyses produites

comparaison inter-heuristiques

comparaison heuristique vs optimal

suivi dynamique RL

analyse des écarts relatifs

analyse temps vs qualité

⚠️ Limitations

Les temps sont parfois injectés manuellement.

Les graphiques interactifs dépendent de l’environnement local.

Le suivi RL repose sur la cohérence des noms de fichiers.

🧠 Philosophie

Séparation stricte analyse / décision

Validation systématique avant comparaison

Reproductibilité expérimentale

Aucune altération des solutions

✅ Bonnes pratiques

Toujours valider les solutions avant analyse

Ne pas coder de chemins en dur

Garder les temps cohérents avec les benchmarks

Nettoyer figures/ avant gros batch

Versionner uniquement les statistiques pertinentes