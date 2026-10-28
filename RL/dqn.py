# dqn/dqn.py
"""
Deep Q-Network (DQN) components.

This module provides:
- DQN:         simple MLP approximator for Q(s, a)
- ReplayBuffer:experience replay storage + mini-batch sampling
- DQNAgent:    epsilon-greedy control + target network training loop

Design goals:
- Keep training loop minimal and readable
- Centralize all learning-related hyperparameters in DQNAgent
- Ensure fast batch conversion (NumPy -> Torch) in ReplayBuffer
"""

from __future__ import annotations

import os
import random
from collections import deque
from typing import Tuple

import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim


class DQN(nn.Module):
    """Feed-forward Q-network: maps state vector -> Q-values over discrete actions."""

    def __init__(self, input_dim: int, output_dim: int):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(input_dim, 128),          # Project state to hidden space
            nn.ReLU(),                          # Non-linearity
            nn.Linear(128, 128),                # Hidden layer
            nn.ReLU(),                          # Non-linearity
            nn.Linear(128, output_dim),         # Output Q-values for each action
        )

    def forward(self, x):
        return self.net(x)                      # Compute Q(s, ·)


class ReplayBuffer:
    """Fixed-capacity FIFO buffer storing transitions (s, a, r, s', done)."""

    def __init__(self, capacity: int = 10000):
        self.buffer = deque(maxlen=capacity)    # Auto-discard oldest transitions

    def push(self, state, action, reward, next_state, done):
        self.buffer.append(                     # Store one transition tuple
            (state, int(action), float(reward), next_state, float(done))
        )

    def sample(self, batch_size: int) -> Tuple[torch.Tensor, ...]:
        batch = random.sample(self.buffer, batch_size)      # Uniform replay sampling
        states, actions, rewards, next_states, dones = zip(*batch)

        # Fast conversion: pack to contiguous NumPy arrays before Torch tensors
        states = np.array(states, dtype=np.float32)         # (B, state_dim)
        next_states = np.array(next_states, dtype=np.float32)
        rewards = np.array(rewards, dtype=np.float32)       # (B,)
        dones = np.array(dones, dtype=np.float32)           # (B,) in {0.0, 1.0}
        actions = np.array(actions, dtype=np.int64)         # (B,) action indices

        return (
            torch.from_numpy(states),                       # states tensor
            torch.from_numpy(actions),                      # actions tensor
            torch.from_numpy(rewards),                      # rewards tensor
            torch.from_numpy(next_states),                  # next_states tensor
            torch.from_numpy(dones),                        # dones tensor
        )

    def __len__(self):
        return len(self.buffer)                             # Current number of stored transitions


class DQNAgent:
    """
    DQN agent for discrete-action environments.

    Core mechanisms:
    - epsilon-greedy exploration
    - experience replay (ReplayBuffer)
    - target network for stabilized TD targets
    """

    def __init__(
        self,
        env,
        gamma: float = 0.99,
        alpha: float = 5e-4,
        epsilon: float = 1.0,
        epsilon_decay: float = 0.999,
        min_epsilon: float = 0.05,
        buffer_size: int = 50000,
        batch_size: int = 64,
        target_update_freq: int = 200,
        device: str | None = None,
    ):
        self.env = env                                      # Environment handle
        self.gamma = float(gamma)                           # Discount factor
        self.epsilon = float(epsilon)                       # Exploration probability
        self.epsilon_decay = float(epsilon_decay)           # Multiplicative decay per update
        self.min_epsilon = float(min_epsilon)               # Lower bound for exploration

        self.batch_size = int(batch_size)                   # Mini-batch size
        self.target_update_freq = int(target_update_freq)   # Hard update period
        self.learn_step = 0                                 # Training step counter

        self.state_dim = int(env.observation_space.shape[0])  # State vector dimension
        self.action_dim = int(env.action_space.n)             # Number of discrete actions
        self.actions = range(self.action_dim)               # Action index iterable

        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")  # Training device

        self.q_network = DQN(self.state_dim, self.action_dim).to(self.device)        # Online network
        self.target_network = DQN(self.state_dim, self.action_dim).to(self.device)   # Target network
        self.target_network.load_state_dict(self.q_network.state_dict())             # Sync weights
        self.target_network.eval()                                                   # No grad / dropout

        self.optimizer = optim.RMSprop(self.q_network.parameters(), lr=float(alpha))  # DQN-style optimizer
        self.loss_fn = nn.MSELoss()                                                   # TD error loss (L2)

        self.buffer = ReplayBuffer(capacity=int(buffer_size))                         # Experience replay

    def play(self, state) -> int:
        """Select one action using epsilon-greedy policy from the current Q-network."""
        if np.random.rand() < self.epsilon:
            return int(np.random.choice(list(self.actions)))  # Explore: random action

        with torch.no_grad():
            st = torch.tensor(state, dtype=torch.float32, device=self.device).unsqueeze(0)  # Add batch dim
            q = self.q_network(st)                                                          # Q(s, ·)
            return int(torch.argmax(q).item())                                               # Exploit: best action

    def save(self, path: str):
        """Save online network parameters to disk."""
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)  # Ensure parent directory exists
        torch.save(self.q_network.state_dict(), path)             # Serialize state_dict

    def load(self, path: str):
        """Load online network parameters from disk and resync target network."""
        state = torch.load(path, map_location=self.device)        # Load weights on chosen device
        self.q_network.load_state_dict(state)                     # Restore online network
        self.target_network.load_state_dict(self.q_network.state_dict())  # Sync target weights
        self.target_network.eval()                                # Keep target network in eval mode

    def update(self, state, action, reward, next_state, done):
        """Store transition and run one gradient update when enough samples are available."""
        self.buffer.push(state, action, reward, next_state, done)  # Append transition to replay

        if len(self.buffer) < self.batch_size:
            return                                                  # Wait until buffer is warm enough

        states, actions, rewards, next_states, dones = self.buffer.sample(self.batch_size)  # Sample replay batch
        states = states.to(self.device)                                                     # Move to device
        actions = actions.to(self.device)
        rewards = rewards.to(self.device)
        next_states = next_states.to(self.device)
        dones = dones.to(self.device)

        q_values = self.q_network(states)                             # Q(s, ·) for all actions
        q_sa = q_values.gather(1, actions.unsqueeze(1)).squeeze(1)     # Q(s, a) for taken actions

        with torch.no_grad():
            q_next = self.target_network(next_states).max(1)[0]        # max_a' Q_target(s', a')
            target = rewards + self.gamma * q_next * (1 - dones)       # TD(0) target with terminal mask

        loss = self.loss_fn(q_sa, target)                              # TD error loss

        self.optimizer.zero_grad()                                     # Clear old gradients
        loss.backward()                                                # Backprop TD loss
        self.optimizer.step()                                          # Update online network weights

        self.epsilon = max(self.min_epsilon, self.epsilon * self.epsilon_decay)  # Decay exploration

        self.learn_step += 1                                           # Advance step counter
        if self.learn_step % self.target_update_freq == 0:
            self.target_network.load_state_dict(self.q_network.state_dict())     # Periodic hard sync