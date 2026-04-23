"""
hive_chaos.py — HiveChaos ChaosSwarm Formation Router & Executor
=================================================================
The formation is an emergent property of the task — computed at runtime,
never predetermined. No fixed topology. The swarm self-sizes.

Endpoints:
  POST /chaos/execute     — main endpoint, x402-gated at dynamic price
  POST /chaos/quote       — free, returns formation + price without executing
  GET  /chaos/formations  — full formation lattice
  GET  /chaos/status      — service DID, tier, tasks_run, heads_fired, stamps
  GET  /health            — instant 200
  GET  /llms.txt          — discovery
  GET  /.well-known/agent.json — A2A card

Run: python hive_chaos.py
"""

import os, time, json, base64, asyncio
from datetime import datetime, timezone
from aiohttp import web
import aiohttp

# ── Constants ──────────────────────────────────────────────────────────────────
TRIDENT_URL  = "https://hive-trident.onrender.com"
PHALANX_URL  = "https://hive-phalanx.onrender.com"
LOCUS_URL    = "https://hive-locus.onrender.com"
HIVEGATE_URL = "https://hivegate.onrender.com"
COMPUTE_URL  = "https://hivecompute-g2g7.onrender.com"
PULSE_URL    = "https://hive-pulse.onrender.com"
HIVE_KEY     = os.environ.get(
    "HIVE_KEY",
    "hive_internal_125e04e071e8829be631ea0216dd4a0c9b707975fcecaf8c62c6a2ab43327d46"
)
KILLSWITCH   = f"{HIVEGATE_URL}/v1/control/status"
PORT         = int(os.environ.get("PORT", 8769))

HEADERS = {
    "Content-Type":  "application/json",
    "X-Hive-Key":    HIVE_KEY,
}

# ── Formation Lattice ──────────────────────────────────────────────────────────
FORMATIONS = {
    "trident":   {"base": "trident",  "units": 1,  "heads": 3,   "price": 0.03,
                  "shape": "1×3",   "grid": (1, 3)},
    "duo":       {"base": "trident",  "units": 2,  "heads": 6,   "price": 0.06,
                  "shape": "1×2",   "grid": (1, 2)},
    "quad":      {"base": "trident",  "units": 4,  "heads": 12,  "price": 0.12,
                  "shape": "2×2",   "grid": (2, 2)},
    "phalanx":   {"base": "phalanx", "units": 1,  "heads": 15,  "price": 0.15,
                  "shape": "1×1",   "grid": (1, 1)},
    "swarm_2x2": {"base": "phalanx", "units": 4,  "heads": 60,  "price": 0.60,
                  "shape": "2×2",   "grid": (2, 2)},
    "swarm_2x3": {"base": "phalanx", "units": 6,  "heads": 90,  "price": 0.90,
                  "shape": "2×3",   "grid": (2, 3)},
    "swarm_3x3": {"base": "phalanx", "units": 9,  "heads": 135, "price": 1.35,
                  "shape": "3×3",   "grid": (3, 3)},
    "swarm_3x4": {"base": "phalanx", "units": 12, "heads": 180, "price": 1.80,
                  "shape": "3×4",   "grid": (3, 4)},
    "swarm_3x5": {"base": "phalanx", "units": 15, "heads": 225, "price": 2.25,
                  "shape": "3×5",   "grid": (3, 5)},
    "swarm_4x4": {"base": "phalanx", "units": 16, "heads": 240, "price": 2.40,
                  "shape": "4×4",   "grid": (4, 4)},
    "swarm_4x5": {"base": "phalanx", "units": 20, "heads": 300, "price": 3.00,
                  "shape": "4×5",   "grid": (4, 5)},
    "swarm_5x5": {"base": "phalanx", "units": 25, "heads": 375, "price": 3.75,
                  "shape": "5×5",   "grid": (5, 5)},
}

# ── Service State ──────────────────────────────────────────────────────────────
state = {
    "did":                None,
    "smsh_name":          None,
    "tier":               "VOID",
    "tasks_run":          0,
    "total_heads_fired":  0,
    "total_smsh_stamps":  0,
    "booted_at":          None,
    "boot_complete":      False,
}

# ── Formation Router ───────────────────────────────────────────────────────────
def select_formation(task: str, budget: float, locus: dict):
    """Select the optimal formation based on task complexity, locus, and budget."""
    words     = len(task.split())
    questions = task.count("?")
    complexity = min(10.0, (words / 50) + (questions * 0.5) + 1.0)

    trust    = locus.get("x", 0.5)
    velocity = locus.get("y", 0.5)
    depth    = locus.get("z", 0.33)

    score = (complexity * 0.5) + (trust * 2.0) + (velocity * 1.5) + (depth * 1.0)

    # Select largest formation that fits budget AND score justifies
    candidates = sorted(FORMATIONS.items(), key=lambda x: -x[1]["price"])
    for name, f in candidates:
        if f["price"] <= budget and score >= f["price"] * 2:
            return name, f, complexity, score

    # Fallback: cheapest that fits budget
    for name, f in sorted(FORMATIONS.items(), key=lambda x: x[1]["price"]):
        if f["price"] <= budget:
            return name, f, complexity, score

    return "trident", FORMATIONS["trident"], complexity, score


# ── Spatial Consensus Grid ─────────────────────────────────────────────────────
def grid_weight(i: int, N: int, M: int) -> float:
    """Compute weight for unit at position i in an N×M grid."""
    row      = i // M
    col      = i % M
    center_r = (N - 1) / 2
    center_c = (M - 1) / 2
    dist     = abs(row - center_r) + abs(col - center_c)
    max_dist = center_r + center_c
    if max_dist == 0:
        return 1.5
    return 1.5 - (dist / max_dist) * 0.75  # 1.5 center → 0.75 corners


def spatial_consensus(results: list, N: int, M: int) -> str:
    """
    Apply spatial weighting to unit results and return the highest-weighted answer.
    Groups answers by text similarity (exact match) and sums weights.
    """
    if not results:
        return ""

    weights: dict[str, float] = {}
    answer_texts: dict[str, str] = {}

    for i, res in enumerate(results):
        w      = grid_weight(i, N, M)
        answer = (res.get("answer") or res.get("content") or "").strip()
        if not answer:
            continue
        # Use first 200 chars as grouping key to handle minor whitespace diffs
        key = answer[:200]
        weights[key]      = weights.get(key, 0) + w
        answer_texts[key] = answer   # keep the full text

    if not weights:
        # Fallback: return first successful result
        for r in results:
            a = (r.get("answer") or r.get("content") or "").strip()
            if a:
                return a
        return ""

    best_key = max(weights, key=lambda k: weights[k])
    return answer_texts[best_key]


# ── Pulse meet ─────────────────────────────────────────────────────────────────
async def pulse_meet(session, did: str, agent_name: str, total_jobs: int = 0):
    """Fire a POST /pulse/meet to register or tick this agent on pulse.smsh."""
    try:
        async with session.post(
            f"{PULSE_URL}/pulse/meet",
            headers={"Content-Type": "application/json"},
            json={
                "did":             did,
                "agent_name":      agent_name,
                "smsh_registered": True,
                "total_jobs":      total_jobs,
                "metadata":        {"service": "chaos", "pattern": "NxM"},
            },
            timeout=aiohttp.ClientTimeout(total=10)
        ) as r:
            data = await r.json()
            tier = data.get("tier", "VOID")
            state["tier"] = tier
            print(f"[CHAOS] pulse.smsh ← {agent_name} | tier={tier} | jobs={total_jobs}")
            return tier
    except Exception as e:
        print(f"[CHAOS] pulse.smsh meet failed: {e}")
        return None


# ── Boot ───────────────────────────────────────────────────────────────────────
async def boot():
    """Mint DID, register on smsh, and meet on pulse. Never blocks health."""
    print("[CHAOS] Booting HiveChaos...")
    async with aiohttp.ClientSession() as session:
        # 1. Onboard → get DID
        try:
            async with session.post(
                f"{HIVEGATE_URL}/v1/gate/onboard",
                headers=HEADERS,
                json={"agent_name": "HiveChaos"},
                timeout=aiohttp.ClientTimeout(total=20)
            ) as r:
                data = await r.json()
                did  = data.get("did")
                if not did:
                    raise ValueError(f"No DID returned: {json.dumps(data)[:120]}")
                state["did"] = did
                print(f"[CHAOS] DID minted: {did}")
        except Exception as e:
            print(f"[CHAOS] WARNING: onboard failed: {e}")
            state["boot_complete"] = True
            return

        # 2. smsh register
        try:
            async with session.post(
                f"{COMPUTE_URL}/v1/compute/smsh/register",
                headers=HEADERS,
                json={"did": did, "agent_name": "HiveChaos"},
                timeout=aiohttp.ClientTimeout(total=20)
            ) as r2:
                reg = await r2.json()
                state["smsh_name"] = reg.get("smsh_name", "HiveChaos.smsh")
                print(f"[CHAOS] smsh registered: {state['smsh_name']}")
        except Exception as e:
            print(f"[CHAOS] WARNING: smsh register failed: {e}")

        # 3. Pulse meet
        await pulse_meet(session, did, "HiveChaos", total_jobs=0)

    state["boot_complete"] = True
    state["booted_at"]     = datetime.now(timezone.utc).isoformat()
    print(f"[CHAOS] Boot complete. DID={state['did']} tier={state['tier']}")


# ── x402 Payment Verification ─────────────────────────────────────────────────
def verify_payment(req: web.Request, price_usdc: float) -> tuple[bool, str]:
    """
    Verify X-PAYMENT header for the given price.
    Returns (ok, error_message).
    """
    header = req.headers.get("X-PAYMENT")
    if not header:
        return False, "missing"

    try:
        payload = json.loads(base64.b64decode(header).decode())
        auth    = payload.get("payload", {}).get("authorization", {})
        value   = int(auth.get("value", 0))
        # USDC has 6 decimals
        paid_usdc = value / 1_000_000
        if paid_usdc < price_usdc:
            return False, f"insufficient: paid {paid_usdc} < required {price_usdc}"
        # Check validity window
        now = int(time.time())
        valid_after  = int(auth.get("validAfter",  0))
        valid_before = int(auth.get("validBefore", 0))
        if now < valid_after:
            return False, "payment not yet valid"
        if now > valid_before:
            return False, "payment expired"
        return True, ""
    except Exception as e:
        return False, f"decode error: {e}"


def payment_required_response(price_usdc: float, formation_name: str, formation: dict) -> web.Response:
    """Return a 402 Payment Required response with x402 details."""
    body = {
        "x402Version": 1,
        "error":       "Payment Required",
        "accepts":     [{
            "scheme":  "exact",
            "network": "base",
            "maxAmountRequired": str(int(price_usdc * 1_000_000)),
            "resource": "https://hive-chaos.onrender.com/chaos/execute",
            "description": f"HiveChaos {formation_name} formation ({formation['heads']} heads)",
            "mimeType":  "application/json",
            "payTo":     "0xE5588c407b6AdD3E83ce34190C77De20eaC1BeFe",
            "maxTimeoutSeconds": 300,
            "asset":     "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
            "extra": {
                "name":     "USDC",
                "version":  "2",
            },
        }],
        "formation":  formation_name,
        "price_usdc": price_usdc,
        "heads":      formation["heads"],
    }
    return web.json_response(body, status=402)


# ── Locus lookup ──────────────────────────────────────────────────────────────
async def get_agent_locus(session, agent_did: str) -> dict:
    """Fetch locus coordinates for an agent DID. Returns defaults on failure."""
    default = {"x": 0.5, "y": 0.5, "z": 0.33}
    if not agent_did:
        return default
    try:
        async with session.post(
            f"{LOCUS_URL}/locus/locate/agent",
            headers=HEADERS,
            json={"agent_did": agent_did},
            timeout=aiohttp.ClientTimeout(total=8)
        ) as r:
            if r.status == 200:
                data = await r.json()
                locus = data.get("locus") or data
                x = locus.get("x", 0.5)
                y = locus.get("y", 0.5)
                z = locus.get("z", 0.33)
                return {"x": float(x), "y": float(y), "z": float(z)}
    except Exception as e:
        print(f"[CHAOS] locus lookup failed for {agent_did}: {e}")
    return default


# ── Sub-service execution ──────────────────────────────────────────────────────
async def call_trident(session, task: str, unit_index: int) -> dict:
    """Call /trident/execute for one unit."""
    try:
        async with session.post(
            f"{TRIDENT_URL}/trident/execute",
            headers=HEADERS,
            json={
                "messages": [{"role": "user", "content": task}],
                "max_tokens": 512,
                "mode": "consensus",
            },
            timeout=aiohttp.ClientTimeout(total=60)
        ) as r:
            if r.status == 200:
                data = await r.json()
                return {"ok": True, "answer": data.get("answer", ""), "unit": unit_index, "raw": data}
            else:
                text = await r.text()
                return {"ok": False, "error": f"HTTP {r.status}: {text[:200]}", "unit": unit_index}
    except Exception as e:
        return {"ok": False, "error": str(e), "unit": unit_index}


async def call_phalanx(session, task: str, unit_index: int) -> dict:
    """Call /phalanx/execute for one unit."""
    try:
        async with session.post(
            f"{PHALANX_URL}/phalanx/execute",
            headers=HEADERS,
            json={
                "messages": [{"role": "user", "content": task}],
                "max_tokens": 512,
            },
            timeout=aiohttp.ClientTimeout(total=90)
        ) as r:
            if r.status == 200:
                data = await r.json()
                return {"ok": True, "answer": data.get("answer", ""), "unit": unit_index, "raw": data}
            else:
                text = await r.text()
                return {"ok": False, "error": f"HTTP {r.status}: {text[:200]}", "unit": unit_index}
    except Exception as e:
        return {"ok": False, "error": str(e), "unit": unit_index}


# ── Endpoints ──────────────────────────────────────────────────────────────────

async def health(req: web.Request) -> web.Response:
    """GET /health — instant 200."""
    return web.json_response({"status": "ok", "service": "hive-chaos"})


async def chaos_formations(req: web.Request) -> web.Response:
    """GET /chaos/formations — full formation lattice."""
    return web.json_response({
        "formations": {
            name: {
                "base":   f["base"],
                "units":  f["units"],
                "heads":  f["heads"],
                "price":  f["price"],
                "shape":  f["shape"],
            }
            for name, f in FORMATIONS.items()
        },
        "count": len(FORMATIONS),
    })


async def chaos_status(req: web.Request) -> web.Response:
    """GET /chaos/status — service DID, tier, tasks_run, total_heads_fired, total_smsh_stamps."""
    return web.json_response({
        "service":             "hive-chaos",
        "chaos_did":           state["did"],
        "smsh_name":           state["smsh_name"],
        "tier":                state["tier"],
        "tasks_run":           state["tasks_run"],
        "total_heads_fired":   state["total_heads_fired"],
        "total_smsh_stamps":   state["total_smsh_stamps"],
        "booted_at":           state["booted_at"],
        "boot_complete":       state["boot_complete"],
    })


async def chaos_quote(req: web.Request) -> web.Response:
    """
    POST /chaos/quote — free. Returns formation that would be selected + price,
    without executing. No payment required.
    """
    try:
        body          = await req.json()
        task          = body.get("task", "")
        max_cost_usdc = float(body.get("max_cost_usdc", 0.15))
        agent_did     = body.get("agent_did")
        formation_override = body.get("formation")

        if not task:
            return web.json_response({"error": "task required"}, status=400)

        # Get locus
        async with aiohttp.ClientSession() as session:
            locus = await get_agent_locus(session, agent_did)

        # Select formation
        if formation_override and formation_override in FORMATIONS:
            fname    = formation_override
            f        = FORMATIONS[fname]
            _, _, complexity, score = select_formation(task, max_cost_usdc, locus)
        else:
            fname, f, complexity, score = select_formation(task, max_cost_usdc, locus)

        N, M = f["grid"]

        return web.json_response({
            "formation":        fname,
            "formation_shape":  f["shape"],
            "units":            f["units"],
            "heads":            f["heads"],
            "price_usdc":       f["price"],
            "base":             f["base"],
            "agent_locus":      locus,
            "complexity_score": round(complexity, 2),
            "formation_score":  round(score, 2),
            "spatial_consensus": {
                "center_weight": 1.5,
                "edge_weight":   1.0,
                "corner_weight": 0.75,
            },
            "grid":             {"N": N, "M": M},
        })

    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def chaos_execute(req: web.Request) -> web.Response:
    """
    POST /chaos/execute — main endpoint, x402-gated at dynamic price.
    Body: {task, max_cost_usdc (default 0.15), agent_did (optional), formation (optional)}
    """
    # ── 1. Kill switch check ────────────────────────────────────────────────
    try:
        async with aiohttp.ClientSession() as ks:
            async with ks.get(KILLSWITCH, timeout=aiohttp.ClientTimeout(total=5)) as r:
                d = await r.json()
                if d.get("directive") != "run":
                    return web.json_response({"error": "Kill switch active — swarm offline"}, status=503)
    except Exception:
        pass  # kill switch unreachable — continue

    # ── 2. Parse body ────────────────────────────────────────────────────────
    try:
        body = await req.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    task               = body.get("task", "")
    max_cost_usdc      = float(body.get("max_cost_usdc", 0.15))
    agent_did          = body.get("agent_did")
    formation_override = body.get("formation")

    if not task:
        return web.json_response({"error": "task required"}, status=400)

    # ── 3. Locus lookup ──────────────────────────────────────────────────────
    async with aiohttp.ClientSession() as session:
        locus = await get_agent_locus(session, agent_did)

    # ── 4. Formation selection ───────────────────────────────────────────────
    if formation_override and formation_override in FORMATIONS:
        fname    = formation_override
        f        = FORMATIONS[fname]
        _, _, complexity, score = select_formation(task, max_cost_usdc, locus)
    else:
        fname, f, complexity, score = select_formation(task, max_cost_usdc, locus)

    N, M = f["grid"]

    # ── 5. x402 payment check at formation price ─────────────────────────────
    ok, err = verify_payment(req, f["price"])
    if not ok:
        print(f"[CHAOS] 402 — {err} — formation={fname} price={f['price']}")
        return payment_required_response(f["price"], fname, f)

    t_start = time.time()

    # ── 6. Execute formation ─────────────────────────────────────────────────
    async with aiohttp.ClientSession() as session:
        tasks_coros = []
        for i in range(f["units"]):
            if f["base"] == "trident":
                tasks_coros.append(call_trident(session, task, i))
            else:
                tasks_coros.append(call_phalanx(session, task, i))

        results = await asyncio.gather(*tasks_coros)

    wall_ms = round((time.time() - t_start) * 1000)

    # ── 7. Spatial consensus ─────────────────────────────────────────────────
    answer = spatial_consensus(list(results), N, M)
    if not answer:
        # Last-resort fallback
        for r in results:
            a = (r.get("answer") or r.get("content") or "").strip()
            if a:
                answer = a
                break
        if not answer:
            answer = "No consensus reached — all units failed."

    # ── 8. Update state ──────────────────────────────────────────────────────
    units_ok    = sum(1 for r in results if r.get("ok"))
    heads_fired = f["heads"] if units_ok > 0 else 0
    stamps      = heads_fired

    state["tasks_run"]         += 1
    state["total_heads_fired"] += heads_fired
    state["total_smsh_stamps"] += stamps

    # ── 8b. Pulse tick (fire-and-forget) ────────────────────────────────────
    async def _pulse_tick():
        if state["did"]:
            async with aiohttp.ClientSession() as ps:
                await pulse_meet(ps, state["did"], "HiveChaos",
                                 total_jobs=state["tasks_run"])

    asyncio.create_task(_pulse_tick())

    # ── 9. Return structured response ────────────────────────────────────────
    return web.json_response({
        "answer":               answer,
        "formation":            fname,
        "formation_shape":      f["shape"],
        "units_fired":          f["units"],
        "heads_fired":          heads_fired,
        "smsh_stamps_generated": stamps,
        "price_paid_usdc":      f["price"],
        "agent_locus":          locus,
        "complexity_score":     round(complexity, 2),
        "formation_score":      round(score, 2),
        "spatial_consensus": {
            "center_weight": 1.5,
            "edge_weight":   1.0,
            "corner_weight": 0.75,
        },
        "chaos_did":            state["did"],
        "tier":                 state["tier"],
        "tasks_run_total":      state["tasks_run"],
        "wall_clock_ms":        wall_ms,
        "units_ok":             units_ok,
        "units_total":          f["units"],
    })


async def llms_txt(req: web.Request) -> web.Response:
    """GET /llms.txt — discovery."""
    content = """# HiveChaos — ChaosSwarm Formation Router
# The formation thinks for itself.
# Dynamic NxM multiplicative swarms. Formation selected at runtime. Never predetermined.

POST https://hive-chaos.onrender.com/chaos/execute
Body: {"task": "...", "max_cost_usdc": 1.35}
Payment: x402 EIP-3009, Base L2 USDC — price matches selected formation

Formations: trident(3) → duo(6) → quad(12) → phalanx(15) → 2x2(60) → 2x3(90) →
            3x3(135) → 3x4(180) → 3x5(225) → 4x4(240) → 4x5(300) → 5x5(375 heads)

Router logic: task complexity + agent locus (X=trust, Y=velocity, Z=depth) + budget
Spatial consensus: center units weighted 1.5x, edges 1.0x, corners 0.75x

GET https://hive-chaos.onrender.com/chaos/formations — full lattice
POST https://hive-chaos.onrender.com/chaos/quote — get formation + price without executing

Identity: pulse.smsh — DID, tier, vapor trails per execution
Entry: https://hivegate.onrender.com/v1/gate/onboard
Integrate: https://github.com/srotzin/hive-pulse/blob/master/INTEGRATE.md
"""
    return web.Response(text=content, content_type="text/plain")


async def agent_json(req: web.Request) -> web.Response:
    """GET /.well-known/agent.json — A2A agent card."""
    card = {
        "schemaVersion":  "1.0",
        "id":             state.get("did") or "did:hive:chaos:pending",
        "name":           "HiveChaos",
        "description":    "ChaosSwarm Formation Router & Executor. Dynamic NxM multiplicative swarms. Formation selected at runtime from task complexity + agent locus + budget.",
        "version":        "1.0.0",
        "url":            "https://hive-chaos.onrender.com",
        "capabilities": {
            "formations":        list(FORMATIONS.keys()),
            "max_heads":         375,
            "dynamic_routing":   True,
            "spatial_consensus": True,
            "x402_payment":      True,
        },
        "endpoints": [
            {"method": "POST", "path": "/chaos/execute",    "auth": "x402", "description": "Execute ChaosSwarm formation on a task"},
            {"method": "POST", "path": "/chaos/quote",      "auth": "none", "description": "Get formation + price quote without executing"},
            {"method": "GET",  "path": "/chaos/formations", "auth": "none", "description": "Full formation lattice"},
            {"method": "GET",  "path": "/chaos/status",     "auth": "none", "description": "Service status and stats"},
            {"method": "GET",  "path": "/health",           "auth": "none", "description": "Health check"},
        ],
        "payment": {
            "scheme":  "x402",
            "network": "base",
            "asset":   "USDC",
            "price":   "dynamic — matches selected formation",
            "range":   {"min_usdc": 0.03, "max_usdc": 3.75},
        },
        "smsh_name":  state.get("smsh_name"),
        "tier":       state.get("tier", "VOID"),
    }
    return web.json_response(card)


# ── App + startup ──────────────────────────────────────────────────────────────
async def on_startup(app):
    asyncio.create_task(boot())


async def run_server():
    app = web.Application()
    app.on_startup.append(on_startup)

    app.router.add_get("/health",                    health)
    app.router.add_get("/chaos/formations",          chaos_formations)
    app.router.add_get("/chaos/status",              chaos_status)
    app.router.add_post("/chaos/quote",              chaos_quote)
    app.router.add_post("/chaos/execute",            chaos_execute)
    app.router.add_get("/llms.txt",                  llms_txt)
    app.router.add_get("/.well-known/agent.json",    agent_json)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    print(f"[CHAOS] HiveChaos running on port {PORT}")
    print(f"[CHAOS] POST /chaos/execute   — ChaosSwarm formation execution (x402)")
    print(f"[CHAOS] POST /chaos/quote     — formation + price quote (free)")
    print(f"[CHAOS] GET  /chaos/formations — full formation lattice")
    print(f"[CHAOS] GET  /chaos/status    — DID, tier, stats")
    print(f"[CHAOS] Formations: {', '.join(FORMATIONS.keys())}")

    # Keep running
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(run_server())
