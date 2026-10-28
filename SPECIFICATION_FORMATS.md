SPECIFICATION_FORMATS.md
Spécification contractuelle des formats — POIP

Ce document définit l’unique référence contractuelle des formats de données du projet POIP.

Il décrit :

les formats d’instances brutes (warehouse_XXX/)

le format de solution (rack_product_assignment.txt)

les contraintes de validation

le format intermédiaire instance.dat utilisé par metaheuristics/

les conventions globales obligatoires

⚠️ Toute solution produite par le projet doit être conforme à ce document.

0️⃣ Convention fondamentale — Racks spéciaux

Dans toutes les instances :

Rack 0 → point de départ (START) — capacité = 0

Rack n−1 → point d’arrivée (END) — capacité = 0

Ces racks :

sont inclus dans la matrice d’adjacence

participent au calcul des distances

ne peuvent contenir aucun produit

1️⃣ Structure d’une instance brute
warehouse_XXX/
├── metadata.txt
├── rack_capacity.txt
├── rack_adjacency_matrix.txt
├── product_circuit.txt
├── aisle_racks.txt
├── orders.txt
│
├── enriched/                  # métriques optionnelles (process_data)
│   ├── popularity.txt
│   ├── neighbors.txt
│   └── ...
│
├── help/                      # fichiers optionnels
│   ├── rack_coordinates.txt
│   └── aisle_directions.txt
│
└── solutions/
    └── rack_product_assignment.txt

Les fichiers enriched/ sont facultatifs et utilisés uniquement via tools/export_instance.py.

2️⃣ Fichiers d’entrée (format brut)
2.1 metadata.txt
<nombre_de_racks>
<nombre_total_d_emplacements>
<taux_d_aeration_en_pourcentage>
<nombre_de_produits>
<nombre_de_circuits>
<nombre_d_allees>
<nombre_de_commandes>

Contraintes :

tous les nombres sont entiers sauf taux d’aération

cohérence obligatoire entre fichiers

2.2 rack_capacity.txt
<nombre_de_racks>
<capacite_rack_0>
<capacite_rack_1>
...

rack 0 et rack n−1 doivent avoir capacité 0

somme des capacités = nombre_total_d_emplacements

2.3 rack_adjacency_matrix.txt
<nombre_de_noeuds>
<distance[0][0]> <distance[0][1]> ...
<distance[1][0]> <distance[1][1]> ...
...

Propriétés :

matrice asymétrique autorisée

distance[i][i] = 0

node 0 = START

node n−1 = END

2.4 product_circuit.txt
<nombre_de_produits>
<circuit_produit_0>
<circuit_produit_1>
...

circuits indexés 0 … C−1

chaque produit appartient à exactement un circuit

2.5 aisle_racks.txt
<nombre_d_allees>
<nb_racks> <rack_id_1> <rack_id_2> ...
...

Contraintes :

chaque rack appartient à exactement une allée

START et END peuvent appartenir à des allées techniques

2.6 orders.txt
<nombre_de_commandes>
<nb_produits> <product_id_1> <product_id_2> ...
...

Contraintes :

produits distincts dans une même commande

identifiants valides

3️⃣ Fichier de solution
3.1 rack_product_assignment.txt
<nombre_de_produits>
<rack_du_produit_0>
<rack_du_produit_1>
...

Contraintes :

exactement un rack par produit

rack valide

aucun produit dans rack capacité 0

4️⃣ Contraintes de validation officielles

Validation effectuée exclusivement via tools/checker.py.

4.1 Capacité

Pour chaque rack r :

nb_produits(r) ≤ capacité(r)
4.2 Aération par allée

Pour chaque allée :

emplacements_vides ≥ ceil(capacite_totale × taux_aeration)

avec :

emplacements_vides = capacite - produits
4.3 Contiguïté des circuits

Pour chaque circuit c :

intervalle_c = [min_rack_c, max_rack_c]

Les intervalles :

ne peuvent se chevaucher qu’aux bornes

doivent former une partition ordonnée

Exemple valide :

C1: [1,45]
C2: [46,60]
C3: [60,77]

Exemple invalide :

C1: [1,45]
C3: [44,77]
5️⃣ Calcul du coût de picking

Pour chaque commande :

extraire les racks des produits

supprimer doublons

trier par index croissant

calculer :

START → r1 → r2 → ... → rk → END

Le coût total est la somme sur toutes les commandes.

La matrice d’adjacence définit toutes les distances.

6️⃣ Format intermédiaire — instance.dat

Utilisé exclusivement par metaheuristics/.

Généré par :

tools/export_instance.py

Structure :

META ...
CAP ...
CIR ...
AIS ...
ADJ ...
ORD ...
PMET ...
RMET ...
AMET ...
CMET ...
SMET ...

Les blocs métriques sont optionnels.

6.1 Format des métriques vectorielles

Format strict :

n
v1
v2
...
vn

Conditions :

taille exacte

un scalaire par ligne

correspondance stricte avec dimension attendue

Sinon ignoré.

7️⃣ Fichiers d’aide (optionnels)
7.1 rack_coordinates.txt
<nombre_de_racks>
<x> <y>
...

Utilisé pour visualisation uniquement.

7.2 aisle_directions.txt
<nombre_d_allees>
<direction>
...
Code	Direction
0	DOWN
1	UP
2	LEFT
3	RIGHT
4	NO_DIRS
8️⃣ Règles de cohérence globale

Aucun parsing manuel hors tools/

Aucun format alternatif toléré

Toute solution doit passer par checker.py

metaheuristics/ ne valide jamais officiellement

analysis/ ne modifie jamais les solutions

process_data/ ne modifie jamais les contraintes

9️⃣ Versioning et évolutivité

Toute modification de format :

doit être reflétée ici

doit rester rétro-compatible si possible

doit être validée sur toutes les briques (tools, metaheuristics, analysis, RL)

🔒 Ce document est contractuel

Il constitue la référence unique pour :

développement

validation

reproduction expérimentale

soutenance académique