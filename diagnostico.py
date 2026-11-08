"""Diagnóstico Live Tennis API: uso e jogos ao vivo (sem a chave)."""
import json, os, requests
B = "https://api.livetennisapi.com/api/public/v1"
s = requests.Session(); s.headers["Authorization"] = f"Bearer {os.environ['LIVETENNIS_KEY']}"
for p, kw in (("usage", {}), ("matches", {"status": "live", "draw": "singles", "limit": 200})):
    r = s.get(f"{B}/{p}", params=kw, timeout=30)
    print(f"GET /{p} -> {r.status_code} | restam {r.headers.get('X-RateLimit-Remaining')}")
    if not r.ok: print("  ", r.text[:300]); continue
    d = r.json()
    if p == "usage": print("  ", json.dumps(d)[:500]); continue
    print("   meta:", json.dumps(d.get("meta"))[:300])
    for m in d.get("data", [])[:40]:
        sc = (m.get("score") or {}).get("games")
        print(f"   {m.get('tour')} | {m.get('tournament')} | {m['players']['p1']['name']} v {m['players']['p2']['name']} | {m.get('format')} | {sc}")
