# Tools — Pipeline Python du projet POIP

Ce dossier contient l’ensemble des **outils Python** utilisés pour :
- charger et inspecter les instances,
- lancer les solveurs C++,
- valider les solutions,
- visualiser les layouts et affectations,
- orchestrer les benchmarks.

👉 **Règle absolue du projet** :  
le parsing et la validation passent **uniquement** par ces outils (jamais de parsing recodé ailleurs).

---

## Vue d’ensemble du pipeline

Pipeline logique :

```

instance → load → solve → check → visualize

````

Correspondance des fichiers :
- `warehouse_loader.py` : chargement unique des données
- `main.py` : orchestration solveurs / benchmarks
- `checker.py` : validation stricte des contraintes + coût
- `visualisation_solution.py` / `aisle_svg.py` : visualisation

---

## Chargement et parsing

### `warehouse_loader.py`

Point d’entrée **unique** pour charger une instance.

Fonctions principales :
- lecture des fichiers `.txt` d’instance,
- construction d’un objet `Warehouse` cohérent,
- accès normalisé aux racks, produits, circuits, distances.

✔️ Utilisé par **tous** les outils  
❌ Ne jamais parser les fichiers directement ailleurs

---

### `parser.py`

Outil d’**inspection rapide** des instances :
- statistiques de base,
- dimensions (produits, racks, circuits),
- détection d’incohérences simples.

Usage typique :
```bash
python tools/parser.py --instance market
````

---

### `export_instance.py`

Export d’une instance vers des **formats intermédiaires** (ex: C++).

Utilisé notamment pour :

* générer des fichiers `.dat` compatibles Gurobi,
* faciliter les tests hors pipeline Python.

---
Export vers le format C++ (instance.dat)
export_instance.py

Ce script convertit une instance au format .txt (décrit dans 

SPECIFICATION_FORMATS

) vers un fichier unique instance.dat, utilisé par les solveurs C++.

🎯 Objectif

Fournir une représentation compacte et unifiée

Éviter tout parsing complexe côté C++

Injecter automatiquement les métriques calculées par process_data

Structure du fichier .dat

Le fichier généré contient :

1️⃣ Bloc META
META num_racks total_slots aeration_rate
     num_products num_circuits num_aisles num_orders

Correspond exactement à metadata.txt.

2️⃣ Capacités
CAP c0 c1 c2 ...

Copie directe de rack_capacity.txt.

3️⃣ Circuits produits
CIR circuit_p0 circuit_p1 ...

Copie directe de product_circuit.txt.

4️⃣ Allées
AIS nb_aisles
A nb_racks r1 r2 ...

Reconstruction fidèle de aisle_racks.txt.

5️⃣ Matrice d’adjacence
ADJ n
D d00 d01 d02 ...
D d10 d11 ...

Représente les distances asymétriques (incluant START=0 et END=n-1).

6️⃣ Commandes
ORD nb_orders
O nb_products p1 p2 ...

Copie directe de orders.txt.

Injection automatique des métriques enrichies

Si un dossier enriched/ existe (généré par process_data/), le script ajoute automatiquement :

Tag	Signification	Taille attendue
PMET	Product metrics	num_products
RMET	Rack metrics	num_racks
AMET	Aisle metrics	num_aisles
CMET	Circuit metrics	num_circuits
SMET	Scalar metrics	scalaire
🔎 Fonctionnement interne important
read_vector_file(path)

Lit un fichier vectoriel strict :

n
v1
v2
...
vn

Conditions :

première ligne = taille attendue

exactement un scalaire par ligne

sinon → erreur

Cela évite toute ambiguïté (ex: matrices ou fichiers mal formatés).

load_vector_metrics_filtered(...)

Charge automatiquement :

uniquement les fichiers .txt

dont la taille correspond exactement

avec filtrage par préfixe (rack_, aisle_, circuit_)

Permet une séparation propre des namespaces.

Alias supportés

Le script accepte :

toy
market
family
category

ou un chemin explicite vers une instance.

Usage
Export d’une instance
python tools/export_instance.py --instance market
Export des 4 instances standards
python tools/export_instance.py --all
Sortie console

Exemple :

[OK] warehouse_big_market -> instance.dat |
PMET=3 CMET=2 AMET=1 RMET=4

Cela indique combien de métriques ont été injectées.
## Représentation et validation des solutions

### `solution_state.py`

Représentation Python d’une solution :

* affectation produit → rack,
* calculs intermédiaires (charges, violations),
* support des racks spéciaux (START / END / cap=0).

Utilisé par :

* `checker.py`
* visualisation
* analyses post-solveur

---

### `checker.py`

**Validateur central du projet**.

Vérifie :

* capacité des racks,
* aération minimale par allée,
* contiguïté des circuits,
* cohérence produit → rack,
* calcul du **coût total de picking**.

👉 Toute solution produite doit passer par ce fichier.

Usage :

```bash
python tools/checker.py --instance market --solution solutions/rack_product_assignment.txt
```

---

## Orchestration des solveurs

### `main.py`

Script **central** pour :

* lancer un solveur sur une instance,
* lancer des benchmarks sur plusieurs instances,
* faire du grid-search sur les paramètres.

Fonctionnalités clés :

* support des solveurs C++ (heuristiques, Gurobi),
* gestion des seeds,
* gestion des options avancées (alpha, scores, ordres),
* intégration avec le checker.

Exemples :

```bash
python tools/main.py --instance market
python tools/main.py --instance category --all
python tools/main.py --instance family --improve
```

---

## Visualisation

### `aisle_layout.py`

Construit le **layout logique des allées** :

* ordonnancement des racks par allée,
* gestion des zones d’aération,
* base commune aux visualisations.

---

### `aisle_svg.py`

Génération d’un **SVG statique** :

* racks colorés par circuit dominant,
* indication des racks vides / spéciaux,
* rendu déterministe par allée.

---

### `visualisation_solution.py`

Visualisation détaillée d’une solution :

* chargement instance + solution,
* appel à `aisle_layout`,
* génération SVG finale.

Options notables :

* `--allow-special` : tolère les placements dans racks spéciaux (debug Gurobi).

Usage :

```bash
python tools/visualisation_solution.py --instance market --solution solutions/rack_product_assignment.txt
```

---

### `visu_all.py`

Visualisation **batch** :

* génère automatiquement les SVG pour plusieurs solutions,
* utile pour comparer des solveurs ou paramètres.

