"""Diagnóstico rápido da OddsPapi: mostra estado de acesso aos endpoints (sem a chave)."""
import json, os
from datetime import date, timedelta, datetime, timezone
import requests
B = "https://api.oddspapi.io/v4"; K = os.environ["ODDSPAPI_KEY"]
def get(p, **kw):
    kw["apiKey"] = K
    r = requests.get(f"{B}/{p}", params=kw, timeout=30)
    print(f"GET /{p} {{{', '.join(k for k in kw if k!='apiKey')}}} -> {r.status_code}")
    if not r.ok: print("   corpo:", r.text[:400]); return None
    return r.json()
fx = get("fixtures", sportId=12, **{"from": date.today().isoformat(), "to": (date.today()+timedelta(days=1)).isoformat()})
fx = fx if isinstance(fx, list) else fx.get("data", fx)
vivos = [f for f in fx if str(f.get("statusName", "")).lower() == "live" and f.get("hasOdds")]
print("ao vivo com odds:", len(vivos))
f = vivos[0]; fid = f["fixtureId"]
print(f.get("participant1Name"), "v", f.get("participant2Name"), "|", f.get("tournamentName"), "| externalProviders:", f.get("externalProviders"))
get("odds", fixtureId=fid)
get("odds", fixtureId=fid, bookmakers="betano.pt")
h = get("historical-odds", fixtureId=fid, bookmakers="betano.pt")
if h:
    print("campos topo:", list(h.keys()) if isinstance(h, dict) else type(h))
    bo = (h.get("bookmakers") or h.get("bookmakerOdds") or {}) if isinstance(h, dict) else {}
    b = bo.get("betano.pt") if isinstance(bo, dict) else None
    s = json.dumps(b if b else h)[:1500]
    print("amostra:", s)
    # última atualização do mercado 121
    try:
        m = b["markets"]["121"]["outcomes"]
        for oid, o in m.items():
            pl = o["players"]["0"]
            last = pl[-1] if isinstance(pl, list) else pl
            print("  outcome", oid, "último:", last)
    except Exception as e:
        print("  (estrutura diferente:", e, ")")
print("agora UTC:", datetime.now(timezone.utc).isoformat())
for p in ("account", "usage", "me"):
    get(p)
