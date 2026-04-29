"""
hive_chaos.py — HiveChaos ChaosSwarm Formation Router & Executor
=================================================================
The formation is an emergent property of the task — computed at runtime,
never predetermined. No fixed topology. The swarm self-sizes.

Wave D Section 8 — x402 intercept on /chaos/execute (confirmed), Spectral
receipt emit on every fee event, BOGO every 6th call, subscription endpoint.
Ref: /home/user/workspace/launch_artifacts/WAVE_D_SCOPING_20260429.md

Endpoints:
  POST /chaos/execute     — main endpoint, x402-gated at dynamic price
  POST /chaos/quote       — free, returns formation + price without executing
  POST /v1/subscription   — formation rental subscription (Wave D)
  GET  /chaos/formations  — full formation lattice
  GET  /chaos/status      — service DID, tier, tasks_run, heads_fired, stamps
  GET  /health            — instant 200
  GET  /llms.txt          — discovery
  GET  /.well-known/agent.json — A2A card
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
SPECTRAL_URL = "https://hive-receipt.onrender.com/v1/receipt/sign"
HIVE_KEY     = os.environ.get(
    "HIVE_KEY",
    "hive_internal_125e04e071e8829be631ea0216dd4a0c9b707975fcecaf8c62c6a2ab43327d46"
)
TREASURY     = "0x15184bf50b3d3f52b60434f8942b7d52f2eb436e"   # Monroe W1
USDC         = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
KILLSWITCH   = f"{HIVEGATE_URL}/v1/control/status"
PORT         = int(os.environ.get("PORT", 8769))

# Subscription tiers
SUB_ENTERPRISE_USDC = 200_000_000  # $200/mo
SUB_API_USDC        =  50_000_000  # $50/mo

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
    # BOGO: track per-caller paid calls; every 6th is free
    "bogo_counters":      {},   # {caller_did: int}
}

# ── Formation Router ───────────────────────────────────────────────────────────
def select_formation(task: str, budget: float, locus: dict):
    words     = len(task.split())
    questions = task.count("?")
    complexity = min(10.0, (words / 50) + (questions * 0.5) + 1.0)

    trust    = locus.get("x", 0.5)
    velocity = locus.get("y", 0.5)
    depth    = locus.get("z", 0.33)

    score = (complexity * 0.5) + (trust * 2.0) + (velocity * 1.5) + (depth * 1.0)

    candidates = sorted(FORMATIONS.items(), key=lambda x: -x[1]["price"])
    for name, f in candidates:
        if f["price"] <= budget and score >= f["price"] * 2:
            return name, f, complexity, score

    for name, f in sorted(FORMATIONS.items(), key=lambda x: x[1]["price"]):
        if f["price"] <= budget:
            return name, f, complexity, score

    return "trident", FORMATIONS["trident"], complexity, score


# ── Spatial Consensus Grid ─────────────────────────────────────────────────────
def grid_weight(i: int, N: int, M: int) -> float:
    row      = i // M
    col      = i % M
    center_r = (N - 1) / 2
    center_c = (M - 1) / 2
    dist     = abs(row - center_r) + abs(col - center_c)
    max_dist = center_r + center_c
    if max_dist == 0:
        return 1.5
    return 1.5 - (dist / max_dist) * 0.75


def spatial_consensus(results: list, N: int, M: int) -> str:
    if not results:
        return ""

    weights: dict[str, float] = {}
    answer_texts: dict[str, str] = {}

    for i, res in enumerate(results):
        w      = grid_weight(i, N, M)
        answer = (res.get("answer") or res.get("content") or "").strip()
        if not answer:
            continue
        key = answer[:200]
        weights[key]      = weights.get(key, 0) + w
        answer_texts[key] = answer

    if not weights:
        for r in results:
            a = (r.get("answer") or r.get("content") or "").strip()
            if a:
                return a
        return ""

    best_key = max(weights, key=lambda k: weights[k])
    return answer_texts[best_key]


# ── Spectral receipt ───────────────────────────────────────────────────────────

async def emit_spectral_receipt(
    route: str,
    amount_usdc: float,
    caller_did,
    loyalty_free: bool = False,
):
    """POST receipt to hive-receipt.onrender.com (fire-and-forget)."""
    try:
        async with aiohttp.ClientSession() as s:
            await s.post(
                SPECTRAL_URL,
                json={
                    "service":      "hive-chaos",
                    "route":        route,
                    "amount_usdc":  amount_usdc,
                    "treasury":     TREASURY,
                    "caller_did":   caller_did,
                    "loyalty_free": loyalty_free,
                    "timestamp":    int(time.time()),
                    "brand_color":  "#C08D23",
                },
                headers={"X-Hive-Key": HIVE_KEY, "Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=8),
            )
    except Exception:
        pass  # non-fatal


# ── BOGO logic ─────────────────────────────────────────────────────────────────

def check_bogo(caller_did) -> bool:
    """Every 6th paid call from same caller is free. Returns True if loyalty-free."""
    if not caller_did:
        return False
    count = state["bogo_counters"].get(caller_did, 0)
    return count > 0 and count % 6 == 0


def increment_bogo(caller_did):
    if not caller_did:
        return
    state["bogo_counters"][caller_did] = state["bogo_counters"].get(caller_did, 0) + 1


# ── Pulse meet ─────────────────────────────────────────────────────────────────
async def pulse_meet(session, did: str, agent_name: str, total_jobs: int = 0):
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
    print("[CHAOS] Booting HiveChaos...")
    async with aiohttp.ClientSession() as session:
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
        paid_usdc = value / 1_000_000
        if paid_usdc < price_usdc:
            return False, f"insufficient: paid {paid_usdc} < required {price_usdc}"
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
            "payTo":     TREASURY,
            "maxTimeoutSeconds": 300,
            "asset":     USDC,
            "extra":     {"name": "USDC", "version": "2"},
        }],
        "formation":  formation_name,
        "price_usdc": price_usdc,
        "heads":      formation["heads"],
    }
    return web.json_response(body, status=402)


# ── Locus lookup ──────────────────────────────────────────────────────────────
async def get_agent_locus(session, agent_did: str) -> dict:
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
                return {
                    "x": float(locus.get("x", 0.5)),
                    "y": float(locus.get("y", 0.5)),
                    "z": float(locus.get("z", 0.33)),
                }
    except Exception as e:
        print(f"[CHAOS] locus lookup failed for {agent_did}: {e}")
    return default


# ── Sub-service execution ──────────────────────────────────────────────────────
async def call_trident(session, task: str, unit_index: int) -> dict:
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
    return web.json_response({"status": "ok", "service": "hive-chaos"})


async def chaos_formations(req: web.Request) -> web.Response:
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
    """POST /chaos/quote — free. Returns formation + price without executing."""
    try:
        body          = await req.json()
        task          = body.get("task", "")
        max_cost_usdc = float(body.get("max_cost_usdc", 0.15))
        agent_did     = body.get("agent_did")
        formation_override = body.get("formation")

        if not task:
            return web.json_response({"error": "task required"}, status=400)

        async with aiohttp.ClientSession() as session:
            locus = await get_agent_locus(session, agent_did)

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
            "grid": {"N": N, "M": M},
        })

    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


# ── Subscription endpoint ──────────────────────────────────────────────────────

async def chaos_subscription(req: web.Request) -> web.Response:
    """
    POST /v1/subscription
    Formation rental subscription. Enterprise $200/mo | API $50/mo.
    x402-gated. Wave D Section 8 formation rental model.
    """
    try:
        body = await req.json()
    except Exception:
        body = {}

    sub_tier   = body.get("tier", "api")  # "enterprise" | "api"
    caller_did = req.headers.get("x-hive-did") or req.headers.get("x-agent-did")

    required_usdc_atomic = SUB_API_USDC if sub_tier == "api" else SUB_ENTERPRISE_USDC
    required_label       = "$50/mo" if sub_tier == "api" else "$200/mo"

    x_payment = req.headers.get("X-PAYMENT") or req.headers.get("x-payment")
    if not x_payment:
        return web.json_response(
            {
                "error": "Payment required for subscription",
                "x402": {
                    "version": 1,
                    "accepts": [{
                        "scheme":   "exact",
                        "network":  "base",
                        "maxAmountRequired": str(required_usdc_atomic),
                        "asset":    USDC,
                        "payTo":    TREASURY,
                        "description": f"HiveChaos {sub_tier} subscription {required_label}",
                    }],
                },
            },
            status=402,
        )

    try:
        decoded = json.loads(base64.b64decode(x_payment).decode())
        auth    = decoded.get("payload", {}).get("authorization", {})
        value   = int(auth.get("value", 0))
        if value < required_usdc_atomic:
            return web.json_response(
                {
                    "error":    "Insufficient payment for subscription tier",
                    "required": required_usdc_atomic,
                    "provided": value,
                    "tier":     sub_tier,
                },
                status=402,
            )
    except Exception as exc:
        return web.json_response({"error": f"Malformed X-PAYMENT header: {exc}"}, status=402)

    # Spectral receipt for subscription
    asyncio.create_task(emit_spectral_receipt(
        route="/v1/subscription",
        amount_usdc=required_usdc_atomic / 1_000_000,
        caller_did=caller_did,
        loyalty_free=False,
    ))

    return web.json_response({
        "success":        True,
        "tier":           sub_tier,
        "amount_usdc":    required_usdc_atomic / 1_000_000,
        "treasury":       TREASURY,
        "treasury_label": "Monroe W1",
        "includes": [
            "Unlimited /chaos/execute calls (fair-use)",
            "All formation sizes including 5×5 (375 heads)",
            "Priority formation routing",
            "BOGO loyalty programme",
            "pulse.smsh tier acceleration",
        ] if sub_tier == "enterprise" else [
            "500 /chaos/execute calls/mo",
            "All formation sizes up to swarm_3x3",
            "BOGO loyalty programme",
            "pulse.smsh tracking",
        ],
        "renews":      "monthly",
        "brand_color": "#C08D23",
    })


async def chaos_execute(req: web.Request) -> web.Response:
    """
    POST /chaos/execute — main endpoint, x402-gated at dynamic price.
    Body: {task, max_cost_usdc (default 0.15), agent_did (optional), formation (optional)}
    BOGO: every 6th paid call is free (x-hive-loyalty-free: true header).
    """
    # 1. Kill switch check
    try:
        async with aiohttp.ClientSession() as ks:
            async with ks.get(KILLSWITCH, timeout=aiohttp.ClientTimeout(total=5)) as r:
                d = await r.json()
                if d.get("directive") != "run":
                    return web.json_response({"error": "Kill switch active — swarm offline"}, status=503)
    except Exception:
        pass

    # 2. Parse body
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

    # 3. Locus lookup
    async with aiohttp.ClientSession() as session:
        locus = await get_agent_locus(session, agent_did)

    # 4. Formation selection
    if formation_override and formation_override in FORMATIONS:
        fname    = formation_override
        f        = FORMATIONS[fname]
        _, _, complexity, score = select_formation(task, max_cost_usdc, locus)
    else:
        fname, f, complexity, score = select_formation(task, max_cost_usdc, locus)

    N, M = f["grid"]

    # 5. Identify caller for BOGO
    caller_did_header = req.headers.get("x-hive-did") or req.headers.get("x-agent-did") or agent_did

    # 6. BOGO check
    loyalty_free = check_bogo(caller_did_header)

    if not loyalty_free:
        # 7. x402 payment check at formation price
        ok, err = verify_payment(req, f["price"])
        if not ok:
            print(f"[CHAOS] 402 — {err} — formation={fname} price={f['price']}")
            return payment_required_response(f["price"], fname, f)

    # Increment BOGO counter
    increment_bogo(caller_did_header)

    t_start = time.time()

    # 8. Execute formation
    async with aiohttp.ClientSession() as session:
        tasks_coros = []
        for i in range(f["units"]):
            if f["base"] == "trident":
                tasks_coros.append(call_trident(session, task, i))
            else:
                tasks_coros.append(call_phalanx(session, task, i))

        results = await asyncio.gather(*tasks_coros)

    wall_ms = round((time.time() - t_start) * 1000)

    # 9. Spatial consensus
    answer = spatial_consensus(list(results), N, M)
    if not answer:
        for r in results:
            a = (r.get("answer") or r.get("content") or "").strip()
            if a:
                answer = a
                break
        if not answer:
            answer = "No consensus reached — all units failed."

    # 10. Update state
    units_ok    = sum(1 for r in results if r.get("ok"))
    heads_fired = f["heads"] if units_ok > 0 else 0
    stamps      = heads_fired

    state["tasks_run"]         += 1
    state["total_heads_fired"] += heads_fired
    state["total_smsh_stamps"] += stamps

    # 11. Fire-and-forget: pulse tick + Spectral receipt
    async def _pulse_tick():
        if state["did"]:
            async with aiohttp.ClientSession() as ps:
                await pulse_meet(ps, state["did"], "HiveChaos",
                                 total_jobs=state["tasks_run"])

    asyncio.create_task(_pulse_tick())
    asyncio.create_task(emit_spectral_receipt(
        route="/chaos/execute",
        amount_usdc=0.0 if loyalty_free else f["price"],
        caller_did=caller_did_header,
        loyalty_free=loyalty_free,
    ))

    # 12. Return structured response
    response_headers = {}
    if loyalty_free:
        response_headers["x-hive-loyalty-free"] = "true"

    return web.json_response(
        {
            "answer":               answer,
            "formation":            fname,
            "formation_shape":      f["shape"],
            "units_fired":          f["units"],
            "heads_fired":          heads_fired,
            "smsh_stamps_generated": stamps,
            "price_paid_usdc":      0.0 if loyalty_free else f["price"],
            "loyalty_free":         loyalty_free,
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
        },
        headers=response_headers,
    )


# ── HiveAI helper ──────────────────────────────────────────────────────────────
HIVEAI_URL   = "https://hive-ai-1.onrender.com/v1/chat/completions"
HIVEAI_MODEL = "meta-llama/llama-3.1-8b-instruct"


async def _call_hive_ai(system_prompt: str, user_prompt: str):
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                HIVEAI_URL,
                headers={
                    "Content-Type":  "application/json",
                    "Authorization": f"Bearer {HIVE_KEY}",
                },
                json={
                    "model":      HIVEAI_MODEL,
                    "max_tokens": 150,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user",   "content": user_prompt},
                    ],
                },
                timeout=aiohttp.ClientTimeout(total=30),
            ) as r:
                data = await r.json()
                return data["choices"][0]["message"]["content"]
    except Exception:
        return None


async def chaos_ai_recommend_formation(req: web.Request) -> web.Response:
    """POST /chaos/ai/recommend-formation ($0.02/call)"""
    try:
        body = await req.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    task_description = body.get("task_description", "")
    budget_usdc      = float(body.get("budget_usdc", 0.15))
    urgency          = body.get("urgency", "normal")

    if not task_description:
        return web.json_response({"error": "task_description required"}, status=400)

    system_prompt = (
        "You are HiveChaos — the formation router. "
        "Given this task and budget, recommend the optimal swarm formation: "
        "trident(3)=$0.03, duo(6)=$0.06, quad(12)=$0.12, phalanx(15)=$0.15, "
        "swarm_2x2(60)=$0.60, swarm_3x3(135)=$1.35, up to swarm_5x5(375)=$3.75. "
        "Pick the formation that maximizes result quality within budget. "
        "2 sentences: formation name + why."
    )
    user_prompt = (
        f"Task: {task_description}\n"
        f"Budget: ${budget_usdc:.2f} USDC\n"
        f"Urgency: {urgency}\n\n"
        "Recommend the optimal formation."
    )

    brief = await _call_hive_ai(system_prompt, user_prompt)

    formation_name = "trident"
    for fname, f in sorted(FORMATIONS.items(), key=lambda x: -x[1]["price"]):
        if f["price"] <= budget_usdc:
            formation_name = fname
            break

    if brief:
        lower = brief.lower()
        for fname in FORMATIONS:
            if fname.replace("_", " ") in lower or fname in lower:
                formation_name = fname
                break

    f = FORMATIONS.get(formation_name, FORMATIONS["trident"])

    fallback_brief = (
        f"Based on your ${budget_usdc:.2f} budget and task complexity, "
        f"the {formation_name} formation ({f['heads']} heads, ${f['price']:.2f}) is optimal. "
        f"This configuration provides the best signal-to-noise ratio for your workload."
    )

    return web.json_response({
        "success":               True,
        "recommended_formation": formation_name,
        "formation_size":        f["heads"],
        "estimated_cost_usdc":   f["price"],
        "brief":                 brief or fallback_brief,
        "price_usdc":            0.02,
    })


async def llms_txt(req: web.Request) -> web.Response:
    content = """# HiveChaos — ChaosSwarm Formation Router
# The formation thinks for itself.
# Dynamic NxM multiplicative swarms. Formation selected at runtime. Never predetermined.
# BOGO: every 6th paid call is free (x-hive-loyalty-free: true). Treasury: Monroe W1.

POST https://hive-chaos.onrender.com/chaos/execute
Body: {"task": "...", "max_cost_usdc": 1.35}
Payment: x402 EIP-3009, Base L2 USDC — price matches selected formation

Formations: trident(3) → duo(6) → quad(12) → phalanx(15) → 2x2(60) → 2x3(90) →
            3x3(135) → 3x4(180) → 3x5(225) → 4x4(240) → 4x5(300) → 5x5(375 heads)

Subscription:
  POST /v1/subscription  {"tier": "enterprise"}  $200/mo
  POST /v1/subscription  {"tier": "api"}          $50/mo
  Payment via x402, treasury Monroe W1

GET https://hive-chaos.onrender.com/chaos/formations — full lattice
POST https://hive-chaos.onrender.com/chaos/quote — get formation + price without executing

Identity: pulse.smsh — DID, tier, vapor trails per execution
Entry: https://hivegate.onrender.com/v1/gate/onboard
Integrate: https://github.com/srotzin/hive-pulse/blob/master/INTEGRATE.md
"""
    return web.Response(text=content, content_type="text/plain")


async def agent_json(req: web.Request) -> web.Response:
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
            {"method": "POST", "path": "/v1/subscription",  "auth": "x402", "description": "Formation rental subscription $200/mo or $50/mo"},
            {"method": "GET",  "path": "/chaos/formations", "auth": "none", "description": "Full formation lattice"},
            {"method": "GET",  "path": "/chaos/status",     "auth": "none", "description": "Service status and stats"},
            {"method": "GET",  "path": "/health",           "auth": "none", "description": "Health check"},
        ],
        "payment": {
            "scheme":   "x402",
            "protocol": "x402",
            "network":  "base",
            "currency": "USDC",
            "asset":    "USDC",
            "address":   TREASURY,
            "recipient": TREASURY,
            "treasury":  "Monroe (W1)",
            "rails": [
                {"chain": "base",     "asset": "USDC", "address": TREASURY},
                {"chain": "base",     "asset": "USDT", "address": TREASURY},
                {"chain": "ethereum", "asset": "USDT", "address": TREASURY},
                {"chain": "solana",   "asset": "USDC", "address": "B1N61cuL35fhskWz5dw8XqDyP6LWi3ZWmq8CNA9L3FVn"},
                {"chain": "solana",   "asset": "USDT", "address": "B1N61cuL35fhskWz5dw8XqDyP6LWi3ZWmq8CNA9L3FVn"},
            ],
        },
        "extensions": {
            "hive_pricing": {
                "currency": "USDC", "network": "base", "model": "per_call",
                "first_call_free": True, "loyalty_threshold": 6,
                "loyalty_message": "Every 6th paid call is free",
                "treasury": TREASURY,
                "treasury_codename": "Monroe (W1)",
                "subscription": {
                    "enterprise": {"price_usdc": 200, "period": "monthly"},
                    "api":        {"price_usdc": 50,  "period": "monthly"},
                    "endpoint":   "/v1/subscription",
                },
            },
        },
        "bogo": {
            "first_call_free": True, "loyalty_threshold": 6,
            "pitch": "Pay this once, your 6th paid call is on the house.",
            "claim_with": "x-hive-did header",
        },
        "smsh_name": state.get("smsh_name"),
        "tier":      state.get("tier", "VOID"),
    }
    return web.json_response(card)


# ── App + startup ──────────────────────────────────────────────────────────────
async def on_startup(app):
    asyncio.create_task(boot())


async def run_server():
    app = web.Application()
    app.on_startup.append(on_startup)

    app.router.add_get("/health",                        health)
    app.router.add_get("/chaos/formations",              chaos_formations)
    app.router.add_get("/chaos/status",                  chaos_status)
    app.router.add_post("/chaos/quote",                  chaos_quote)
    app.router.add_post("/chaos/execute",                chaos_execute)
    app.router.add_post("/chaos/ai/recommend-formation", chaos_ai_recommend_formation)
    app.router.add_get("/llms.txt",                      llms_txt)
    app.router.add_get("/.well-known/agent.json",        agent_json)
    # Wave D Section 8 — subscription endpoint
    app.router.add_post("/v1/subscription",              chaos_subscription)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    print(f"[CHAOS] HiveChaos running on port {PORT}")
    print(f"[CHAOS] POST /chaos/execute   — ChaosSwarm formation execution (x402)")
    print(f"[CHAOS] POST /chaos/quote     — formation + price quote (free)")
    print(f"[CHAOS] POST /v1/subscription — formation rental $200/mo or $50/mo")
    print(f"[CHAOS] GET  /chaos/formations — full formation lattice")
    print(f"[CHAOS] GET  /chaos/status    — DID, tier, stats")
    print(f"[CHAOS] BOGO: every 6th paid call free | Spectral receipt on all fee events")
    print(f"[CHAOS] Formations: {', '.join(FORMATIONS.keys())}")

    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(run_server())
