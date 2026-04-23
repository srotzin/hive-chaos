# HiveChaos — ChaosSwarm Formation Router

The formation is an emergent property of the task — computed at runtime, never predetermined. No fixed topology. The swarm self-sizes.

## What it does

HiveChaos is the dynamic swarm orchestrator for the Hive network. It:

1. Analyzes your task for complexity
2. Looks up the calling agent's locus coordinates (trust, velocity, depth)
3. Selects the optimal formation from the lattice — balancing cost and cognitive power
4. Fires N parallel Trident or Phalanx units simultaneously
5. Applies spatial consensus weighting (center units 1.5×, edges 1.0×, corners 0.75×)
6. Returns the highest-weighted answer with full provenance

## Formation Lattice

| Formation   | Base    | Units | Heads | Price (USDC) |
|-------------|---------|-------|-------|--------------|
| trident     | trident | 1     | 3     | $0.03        |
| duo         | trident | 2     | 6     | $0.06        |
| quad        | trident | 4     | 12    | $0.12        |
| phalanx     | phalanx | 1     | 15    | $0.15        |
| swarm_2x2   | phalanx | 4     | 60    | $0.60        |
| swarm_2x3   | phalanx | 6     | 90    | $0.90        |
| swarm_3x3   | phalanx | 9     | 135   | $1.35        |
| swarm_3x4   | phalanx | 12    | 180   | $1.80        |
| swarm_3x5   | phalanx | 15    | 225   | $2.25        |
| swarm_4x4   | phalanx | 16    | 240   | $2.40        |
| swarm_4x5   | phalanx | 20    | 300   | $3.00        |
| swarm_5x5   | phalanx | 25    | 375   | $3.75        |

## Endpoints

### `POST /chaos/execute` — x402-gated, dynamic price

```json
{
  "task": "Analyze the long-term implications of quantum computing on cryptography",
  "max_cost_usdc": 1.35,
  "agent_did": "did:hive:abc123",
  "formation": "swarm_3x3"
}
```

- `task` — required. The task to execute.
- `max_cost_usdc` — optional, default `0.15`. Maximum budget; the router picks the best formation that fits.
- `agent_did` — optional. If provided, the router fetches the agent's locus from HiveLocus to influence formation selection.
- `formation` — optional. Override the router with a specific formation name.

Payment: x402 EIP-3009, Base L2 USDC. Price matches the selected formation. Send the `X-PAYMENT` header with a signed `TransferWithAuthorization`. A `402` response with price details is returned if payment is missing or insufficient.

**Response:**
```json
{
  "answer": "final consensus answer",
  "formation": "swarm_3x3",
  "formation_shape": "3×3",
  "units_fired": 9,
  "heads_fired": 135,
  "smsh_stamps_generated": 135,
  "price_paid_usdc": 1.35,
  "agent_locus": {"x": 0.72, "y": 0.61, "z": 0.50},
  "complexity_score": 6.2,
  "formation_score": 8.1,
  "spatial_consensus": {"center_weight": 1.5, "edge_weight": 1.0, "corner_weight": 0.75},
  "chaos_did": "did:hive:...",
  "tier": "MOZ",
  "tasks_run_total": 5,
  "wall_clock_ms": 1200
}
```

### `POST /chaos/quote` — free

Same body as `/chaos/execute`. Returns the formation that would be selected plus the price, without executing or requiring payment.

### `GET /chaos/formations` — free

Returns the full formation lattice with all 12 configurations.

### `GET /chaos/status` — free

Returns service DID, SMSH name, tier, `tasks_run`, `total_heads_fired`, `total_smsh_stamps`.

### `GET /health` — instant 200

No external calls. Always fast.

### `GET /llms.txt` — discovery

Machine-readable service description for agent discovery.

### `GET /.well-known/agent.json` — A2A card

Full A2A agent card with capabilities, endpoints, and payment details.

## Router Logic

```
complexity  = min(10, words/50 + questions*0.5 + 1.0)
score       = complexity*0.5 + trust*2.0 + velocity*1.5 + depth*1.0

→ largest formation where: price ≤ budget AND score ≥ price*2
→ fallback: cheapest formation within budget
```

## Spatial Consensus

Units are assigned positions in an N×M grid. Center units carry 1.5× weight, edge units 1.0×, corners 0.75×. Answers are grouped by content and weights summed — the highest-weight answer wins.

```
weight(i) = 1.5 - (manhattan_dist_from_center / max_dist) * 0.75
```

## Dependencies

- [HiveGate](https://hivegate.onrender.com) — DID minting
- [HiveCompute](https://hivecompute-g2g7.onrender.com) — SMSH registration
- [HivePulse](https://hive-pulse.onrender.com) — pulse ticking
- [HiveLocus](https://hive-locus.onrender.com) — agent locus coordinates
- [HiveTrident](https://hive-trident.onrender.com) — 3-head inference units
- [HivePhalanx](https://hive-phalanx.onrender.com) — 15-head inference units

## Running locally

```bash
pip install -r requirements.txt
HIVE_KEY=your_key PORT=8769 python hive_chaos.py
```

## Deploying to Render

The `render.yaml` in this directory configures the service. Set the `HIVE_KEY` environment variable if overriding the default.

```bash
# Port 8769 is set in render.yaml
# Service name: hive-chaos
# URL: https://hive-chaos.onrender.com
```
