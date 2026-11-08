"""Diagnóstico rápido da OddsPapi: mostra a estrutura das respostas (sem a chave)."""
import json, os, sys
import requests
B = "https://api.oddspapi.io/v4"; K = os.environ["ODDSPAPI_KEY"]
def get(p, **kw):
    kw["apiKey"] = K
    r = requests.get(f"{B}/{p}", params=kw, timeout=30)
    print(f"GET /{p} -> {r.status_code}")
    r.raise_for_status(); return r.json()
mk = get("markets", sportId=12)
mk = mk if isinstance(mk, list) else mk.get("data", mk)
print("mercados:", len(mk))
for m in mk:
    if str(m.get("period", "")).lower() in ("p1", "result") and not m.get("handicap"):
        print("  ", {k: m.get(k) for k in ("marketId", "marketName", "period", "handicap", "outcomes")})
from datetime import date, timedelta
fx = get("fixtures", sportId=12, **{"from": date.today().isoformat(), "to": (date.today() + timedelta(days=1)).isoformat()})
fx = fx if isinstance(fx, list) else fx.get("data", fx)
print("jogos hoje:", len(fx))
from collections import Counter
print("estados:", Counter(str(f.get("statusName")) for f in fx))
if fx: print("campos fixture:", sorted(fx[0].keys()))
vivos = [f for f in fx if str(f.get("statusName", "")).lower() == "live"]
for f in vivos[:6]:
    o = get("odds", fixtureId=f["fixtureId"])
    bo = o.get("bookmakerOdds", {})
    print("\n", f.get("participant1Name"), "v", f.get("participant2Name"), "|", f.get("tournamentName"))
    print("  campos topo odds:", sorted(k for k in o.keys() if k != "bookmakerOdds"))
    print("  casas:", sorted(bo.keys())[:15], "... total", len(bo))
    b = bo.get("betano.pt")
    if not b: print("  sem betano.pt"); continue
    print("  campos betano:", {k: (v if not isinstance(v, (dict, list)) else type(v).__name__) for k, v in b.items()})
    mkts = b.get("markets", {})
    print("  mercados betano:", sorted(mkts.keys(), key=lambda x: int(x) if str(x).isdigit() else 0)[:40])
    for mid in list(mkts)[:3]:
        print("  exemplo mercado", mid, json.dumps(mkts[mid])[:600])
    break
