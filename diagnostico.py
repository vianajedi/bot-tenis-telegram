"""Diagnóstico: campos de jogos ao vivo e endpoint de resultados (sem a chave)."""
import json, os
from datetime import date, timedelta
import requests
B = "https://api.oddspapi.io/v4"; K = os.environ["ODDSPAPI_KEY"]
def get(p, **kw):
    kw["apiKey"] = K
    r = requests.get(f"{B}/{p}", params=kw, timeout=30)
    print(f"GET /{p} {{{', '.join(k for k in kw if k!='apiKey')}}} -> {r.status_code} | limites: " +
          ", ".join(f"{h}={v}" for h, v in r.headers.items() if "limit" in h.lower() or "remaining" in h.lower()))
    if not r.ok: print("   corpo:", r.text[:300]); return None
    return r.json()
fx = get("fixtures", sportId=12, **{"from": date.today().isoformat(), "to": (date.today()+timedelta(days=1)).isoformat()})
fx = fx if isinstance(fx, list) else fx.get("data", fx)
vivos = [f for f in fx if str(f.get("statusName", "")).lower() == "live"]
print("ao vivo:", len(vivos))
if vivos:
    f = vivos[0]; print("fixture completo:", json.dumps(f)[:1500])
    for p, kw in (("scores", {"fixtureId": f["fixtureId"]}), ("fixture", {"fixtureId": f["fixtureId"]}), ("livescores", {"sportId": 12})):
        r = get(p, **kw)
        if r is not None: print("  ", json.dumps(r)[:1500])
