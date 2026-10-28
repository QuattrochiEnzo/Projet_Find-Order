RL — Reinforcement Learning Module

This package contains the Reinforcement Learning (RL) components of the POIP project.

Its purpose is to learn improvement policies over warehouse slotting solutions
using a Deep Q-Network (DQN) agent interacting with a custom environment.

⚠️ This module does not modify instances or constraints.
It operates strictly on valid solutions and aims to improve them.

🎯 Role in the Global Architecture

Within the global POIP pipeline:

instance
↓
C++ heuristic / MILP
↓
feasible solution
↓
RL improvement
↓
improved solution
↓
checker.py (validation)
↓
analysis

The RL module acts as a learning-based improvement layer on top of deterministic solvers.

It does not replace heuristics.
It learns how to select transformation operators intelligently.

🏗 Global Architecture

The learning loop follows the classical RL interaction pattern:

state → agent → action
↑                 ↓
environment ← transition ← reward

The environment encodes the slotting solution state.

The agent (DQN) selects an operator via epsilon-greedy exploration.

The selected operator modifies the solution.

The environment returns:

next state

reward

termination signal

The agent stores the transition and updates its Q-network.

📂 Folder Structure
RL/
├── env/
├── operators/
├── training/
├── dqn.py
└── README.md
🧠 env/ — Environment Definition

Defines the RL environment modeling the slotting improvement process.

Responsibilities:

represent the current warehouse solution,

apply selected operators,

compute reward signals,

ensure feasibility preservation,

define observation and action spaces.

API (Gym-like):

reset()

step(action)

observation_space

action_space

The environment ensures that:

feasibility is preserved (or repaired),

transitions are consistent with slotting constraints,

rewards reflect improvement in picking cost.

🔧 operators/ — Action Space

Contains transformation operators used as discrete actions.

Each operator:

modifies the current solution,

preserves feasibility (or applies repair),

returns a new candidate solution.

Examples:

destroy operator

relocate block

swap circuits

local improvement step

These operators define the discrete action space of the RL agent.

🎓 training/ — Experiment Orchestration

Contains scripts for:

initializing environment and agent,

running training episodes,

logging metrics,

saving trained models,

evaluating learned policies.

Training logic is separated from the DQN definition
to allow reuse of the agent in different experiments.

🧩 dqn.py — Core RL Components

Implements:

DQN → neural network approximator

ReplayBuffer → experience replay storage

DQNAgent → epsilon-greedy agent with target network

Core mechanisms:

TD(0) learning

Hard target network update

RMSprop optimizer

MSE loss

Off-policy training

The DQN module is environment-agnostic and reusable.

🎯 Reward Design

The reward is typically based on:

improvement in picking cost

penalty for infeasible transitions (if allowed)

optional shaping terms

Common formulation:

reward = previous_cost - new_cost

Positive reward indicates improvement.

Reward design is decoupled from:

operator implementation

DQN architecture

This allows easy experimentation.

🧪 Reproducibility

To ensure reproducible experiments:

fix random seeds (Python / NumPy / PyTorch),

control epsilon schedule,

log model checkpoints,

store training statistics.

RL results should always be validated with checker.py.

🧠 Design Principles
1️⃣ Strict Modularity

Each layer has a single responsibility:

Layer	Role
Environment	Defines the MDP
Operators	Define discrete actions
Agent	Learns policy
Training	Runs experiments

No unnecessary cross-dependencies.

2️⃣ Extensibility

The architecture allows:

adding new operators without modifying the agent,

replacing DQN with another RL algorithm,

modifying reward design independently,

running ablation studies easily.

3️⃣ Separation from Deterministic Solvers

The RL module does not replace classical heuristics.

Instead, it:

refines existing solutions,

learns operator selection strategies,

adapts policies to instance structure.

It acts as an adaptive meta-heuristic.

🔄 Typical Workflow

Generate or load an initial feasible solution.

Initialize the RL environment with that solution.

Train DQN over multiple episodes.

Save the trained model.

Use the trained agent for inference on new instances.

Validate improved solution via checker.py.

📊 Experimental Outputs

Training typically produces:

episode reward curves,

cost evolution logs,

saved model checkpoints,

inference snapshots (infer_step*_cost_*.txt).

These outputs are later visualized via the analysis/ module.

⚠️ Current Limitations

Discrete action space only.

Vector-based state representation.

Online off-policy learning.

Hard target update (no soft update).

No Double DQN.

No Dueling architecture.

No prioritized replay.

No curriculum learning.

🚀 Possible Extensions

Double DQN to reduce overestimation bias.

Dueling DQN architecture.

Soft target updates.

Prioritized replay buffer.

Curriculum learning over instance size.

Hybrid RL + Tabu framework.

Adaptive epsilon scheduling.

🧠 Philosophy

RL augments heuristics.

Deterministic solvers provide strong baselines.

Learning focuses on operator selection.

Feasibility is never sacrificed.

Validation is external and mandatory.