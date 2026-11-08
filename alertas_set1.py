#!/usr/bin/env python3
"""
Alertas Telegram: favorito perdeu o 1.º set (ténis).

- Lista do dia em lista_hoje.json (odds pré-jogo da Betano, preparada à parte).
- Marcador ao vivo: Live Tennis API (plano grátis: 100 pedidos/dia).
  Só consulta enquanto houver jogos da lista na janela de jogo; 1 pedido por ciclo.
- Quando o favorito perde o 1.º set, envia a odd alvo de cada jogador
  (odds justas históricas, tennis-data ATP+WTA 2023-2026).
Não lê odds ao vivo e não faz apostas.
"""
import json, os, re, subprocess, sys, time, unicodedata, logging
from datetime import datetime, timedelta, timezone
import requests
from zoneinfo import ZoneInfo
LISBOA = ZoneInfo("Europe/Lisbon")

API = "https://api.livetennisapi.com/api/public/v1"
AQUI = os.path.dirname(os.path.abspath(__file__))
LISTA = os.path.join(AQUI, "lista_hoje.json")
LISTA_URL = os.environ.get("LISTA_URL", "https://raw.githubusercontent.com/vianajedi/bot-tenis-telegram/main/lista_hoje.json")
ESTADO = os.path.join(AQUI, "estado_set1.json")
CASOS = os.path.join(AQUI, "casos.json")
MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]
CASAS = ["betano", "betclic", "solverde", "bwin", "placard", "esconline", "lebull", "goldenpark", "retabet"]
LIMITE_DIA = int(os.environ.get("LIMITE_PEDIDOS_DIA", "90"))
INTERVALO = int(os.environ.get("INTERVALO_SEGUNDOS", "300"))
log = logging.getLogger("set1")

# Probabilidade do favorito ganhar o jogo depois de PERDER o 1.º set (%)
BANDAS = [(1.01, 1.15), (1.15, 1.30), (1.30, 1.45), (1.45, 1.60), (1.60, 1.80), (1.80, 2.10)]
PERDEU = {"BO3": [57.7, 41.9, 32.9, 29.8, 25.6, 22.4], "BO5": [78.2, 55.6, 46.3, 34.9, 33.3, 28.9]}
# Sets 1-1 (melhor de 3): probabilidade do favorito ganhar o 3.º set / o jogo (%)
UM_UM = {"ganhou_s1": [78.6, 66.3, 60.9, 53.2, 48.0, 48.8], "perdeu_s1": [80.3, 70.1, 62.2, 62.8, 57.2, 52.8]}
FINAS_BO3 = [(1.05, 1.10, 62.2), (1.10, 1.15, 54.5), (1.15, 1.20, 49.3), (1.20, 1.25, 44.5), (1.25, 1.30, 37.5)]


def p_fav(odd, fmt):
    if fmt != "BO5":
        for a, b, p in FINAS_BO3:
            if a <= odd < b:
                return p
    for i, (a, b) in enumerate(BANDAS):
        if a <= odd < b:
            return PERDEU.get(fmt, PERDEU["BO3"])[i]
    return None


def p_fav_11(odd, fav_ganhou_s1):
    for i, (a, b) in enumerate(BANDAS):
        if a <= odd < b:
            return UM_UM["ganhou_s1" if fav_ganhou_s1 else "perdeu_s1"][i]
    return None


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return " ".join(s.replace("-", " ").replace(".", " ").split())


def apelido(nome):
    return norm(nome).split()[-1] if norm(nome) else ""


def mesmo(nome_lista, nome_api):
    a, b = norm(nome_lista).split(), norm(nome_api).split()
    return bool(a and b) and (a[-1] in b or b[-1] in a)


def carregar(p, d):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return d


def gravar(p, d):
    with open(p, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)


def ler_lista():
    """Lê a lista mais recente do repositório (assim apanha atualizações sem reiniciar)."""
    try:
        r = requests.get(LISTA_URL, params={"t": int(time.time())}, timeout=20)
        if r.ok:
            return r.json()
    except Exception as e:
        log.warning("Lista remota indisponível: %s", e)
    return carregar(LISTA, {"jogos": []})


def telegram(txt):
    r = requests.post(f"https://api.telegram.org/bot{os.environ['TELEGRAM_TOKEN']}/sendMessage", json={
        "chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": txt, "parse_mode": "HTML",
        "disable_web_page_preview": True}, timeout=20)
    if not r.ok:
        log.error("Telegram: %s %s", r.status_code, r.text[:200])
        return None
    return r.json().get("result", {}).get("message_id") or True


def git_push(msg):
    """Grava casos.json no repositório (o GITHUB_TOKEN do workflow tem permissão de escrita)."""
    if not os.environ.get("GITHUB_ACTIONS"):
        return
    try:
        subprocess.run(["git", "config", "user.name", "bot-tenis"], cwd=AQUI, check=True)
        subprocess.run(["git", "config", "user.email", "bot-tenis@users.noreply.github.com"], cwd=AQUI, check=True)
        subprocess.run(["git", "add", "casos.json"], cwd=AQUI, check=True)
        if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=AQUI).returncode == 0:
            return
        subprocess.run(["git", "commit", "-q", "-m", msg], cwd=AQUI, check=True)
        for _ in range(3):
            subprocess.run(["git", "pull", "-q", "--rebase"], cwd=AQUI)
            if subprocess.run(["git", "push", "-q"], cwd=AQUI).returncode == 0:
                return
            time.sleep(5)
        log.warning("Não consegui gravar casos.json no repositório.")
    except Exception as ex:
        log.warning("git: %s", ex)


def guardar_caso(rec):
    casos = carregar(CASOS, [])
    casos.append(rec)
    gravar(CASOS, casos[-200:])
    git_push(f"Caso: {rec['fav']} v {rec['dog']} ({rec['momento']})")


def processar_respostas(est):
    """Respostas no Telegram a um alerta: odds ao vivo e/ou resultado.
    Ex.: '3,10 1,40 betano' (favorito primeiro) ou 'ganhou Maxted'."""
    try:
        r = requests.get(f"https://api.telegram.org/bot{os.environ['TELEGRAM_TOKEN']}/getUpdates",
                         params={"offset": est.get("tg_offset", 0), "timeout": 0}, timeout=20)
        ups = r.json().get("result", []) if r.ok else []
    except Exception as ex:
        log.warning("getUpdates: %s", ex)
        return
    if not ups:
        return
    casos = carregar(CASOS, [])
    mudou = False
    for u in ups:
        est["tg_offset"] = u["update_id"] + 1
        m = u.get("message") or {}
        ref = (m.get("reply_to_message") or {}).get("message_id")
        txt = m.get("text") or ""
        if str(m.get("chat", {}).get("id")) != str(os.environ["TELEGRAM_CHAT_ID"]) or not ref or not txt:
            continue
        c = next((x for x in casos if x.get("msg_id") == ref), None)
        if not c:
            continue
        t = norm(txt)
        nums = [float(x.replace(",", ".")) for x in re.findall(r"\d+[.,]\d+", txt)]
        feito = []
        if len(nums) >= 2:
            c["liveFav"], c["liveDog"] = nums[0], nums[1]; feito.append("odds ao vivo")
        elif len(nums) == 1:
            if apelido(c["dog"]) in t and apelido(c["fav"]) not in t:
                c["liveDog"] = nums[0]
            else:
                c["liveFav"] = nums[0]
            feito.append("odd ao vivo")
        casa = next((k for k in CASAS if k in t), None)
        if casa:
            c["casa"] = casa.capitalize()
        if "ganh" in t or "venc" in t:
            for nome in (c["fav"], c["dog"]):
                if apelido(nome) in t:
                    c["resultado"] = f"Ganhou {nome.split()[-1]}"; feito.append("resultado")
        if feito:
            mudou = True
            telegram(f"✅ Registado ({', '.join(feito)}): {c['fav']} v {c['dog']}")
        else:
            telegram("Não percebi. Responde ao alerta com as odds (favorito primeiro), ex.: <code>3,10 1,40 betano</code>, ou com <code>ganhou Apelido</code>.")
    if mudou:
        gravar(CASOS, casos)
        git_push("Casos: odds ao vivo/resultado")


def pt(x):
    return f"{x:.2f}".replace(".", ",")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", stream=sys.stdout)
    for v in ("LIVETENNIS_KEY", "TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID"):
        if not os.environ.get(v):
            sys.exit(f"Falta o segredo {v}.")
    duracao = float(os.environ.get("DURACAO_MIN", "345")) * 60
    lista = ler_lista()
    est = carregar(ESTADO, {})
    hoje = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if est.get("dia") != hoje:
        est = {"dia": hoje, "pedidos": 0, "jogos": est.get("jogos", {})}
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {os.environ['LIVETENNIS_KEY']}"
    inicio = time.time()
    log.info("Lista: %d jogos (%s). Pedidos hoje: %d/%d.", len(lista["jogos"]), lista.get("data"), est["pedidos"], LIMITE_DIA)

    def resumo(lista):
        linhas = []
        for j in sorted(lista["jogos"], key=lambda x: x["inicio_utc"]):
            p = p_fav(j["odd_fav"], "BO3")
            alvo = pt(100 / (100 - p)) if p else "–"
            h = datetime.fromisoformat(j["inicio_utc"].replace("Z", "+00:00")).astimezone(LISBOA).strftime("%d/%m %H:%M")
            linhas.append(f"{h} {apelido(j['fav']).title()} {pt(j['odd_fav'])} v {apelido(j['dog']).title()} {pt(j['odd_dog'])} → <b>{alvo}</b>")
        cab = f"📋 <b>Lista de ténis {lista.get('data','')}</b> ({len(lista['jogos'])} jogos, hora de Lisboa)\nNúmero a negrito = odd mínima do não favorito se o favorito perder o 1.º set. Também recebes aviso no início do 3.º set (sets 1-1).\n\n↩️ <b>Para registar um caso, responde ao alerta</b> (não a esta lista):\n• odds ao vivo, favorito primeiro: <code>3,10 1,40 betano</code>\n• no fim do jogo: <code>ganhou Apelido</code>\n\n"
        txt = cab
        for l in linhas:
            if len(txt) + len(l) > 3800:
                telegram(txt); txt = ""
            txt += l + "\n"
        if txt:
            telegram(txt)

    while time.time() - inicio < duracao:
        agora = datetime.now(timezone.utc)
        processar_respostas(est)
        lista = ler_lista()
        if lista.get("jogos") and est.get("lista_avisada") != lista.get("data"):
            resumo(lista)
            est["lista_avisada"] = lista.get("data")
            gravar(ESTADO, est)
        if agora.strftime("%Y-%m-%d") != est["dia"]:
            est["dia"], est["pedidos"] = agora.strftime("%Y-%m-%d"), 0
        pendentes = []
        for j in lista["jogos"]:
            chave = f"{j['fav']}|{j['dog']}"
            if est["jogos"].get(chave, {}).get("fim"):
                continue
            h = datetime.fromisoformat(j["inicio_utc"].replace("Z", "+00:00"))
            if h - timedelta(minutes=10) <= agora <= h + timedelta(hours=5):
                pendentes.append((chave, j))
        futuros = [j for j in lista["jogos"] if datetime.fromisoformat(j["inicio_utc"].replace("Z", "+00:00")) > agora
                   and not est["jogos"].get(f"{j['fav']}|{j['dog']}", {}).get("fim")]
        if not pendentes:
            if not futuros:
                log.info("Sem jogos pendentes na lista. A terminar.")
                processar_respostas(est)
                break
            time.sleep(INTERVALO)
            continue
        if est["pedidos"] >= LIMITE_DIA:
            log.info("Limite diário de pedidos atingido (%d). A terminar.", LIMITE_DIA)
            break

        try:
            r = s.get(f"{API}/matches", params={"status": "live", "draw": "singles", "limit": 200}, timeout=30)
            est["pedidos"] += 1
            restam = r.headers.get("X-RateLimit-Remaining")
            if r.status_code == 429:
                log.warning("429 da API (limite). A terminar: %s", r.text[:200])
                break
            r.raise_for_status()
            vivos = r.json().get("data", [])
        except Exception as e:
            log.warning("Erro na API: %s", e)
            gravar(ESTADO, est)
            time.sleep(INTERVALO)
            continue
        log.info("Jogos ao vivo: %d | pendentes da lista: %d | pedidos hoje %d (restam %s)",
                 len(vivos), len(pendentes), est["pedidos"], restam)

        for chave, j in pendentes:
            m = next((x for x in vivos
                      if (mesmo(j["fav"], x["players"]["p1"]["name"]) and mesmo(j["dog"], x["players"]["p2"]["name"]))
                      or (mesmo(j["fav"], x["players"]["p2"]["name"]) and mesmo(j["dog"], x["players"]["p1"]["name"]))), None)
            if not m:
                continue
            sc = m.get("score") or {}
            g = sc.get("games")
            if not g or len(g) != 2 or len(g[0]) < 2:
                continue  # 1.º set ainda a decorrer (ou marcador indisponível)
            fav_e_p1 = mesmo(j["fav"], m["players"]["p1"]["name"])
            F, Dg = (g[0], g[1]) if fav_e_p1 else (g[1], g[0])
            e = est["jogos"].setdefault(chave, {})
            fmt = m.get("format") or "BO3"
            fav_s1 = F[0] > Dg[0]
            cab = (f"{j.get('torneio', m.get('tournament', ''))} · {fmt}\n"
                   f"Pré-jogo Betano: {j['fav']} {pt(j['odd_fav'])} · {j['dog']} {pt(j['odd_dog'])}\n")
            resp_txt = ("\n\n↩️ Responde a esta mensagem com a odd ao vivo (favorito primeiro), "
                        "ex.: <code>3,10 1,40 betano</code>. No fim: <code>ganhou Apelido</code>.")
            base = {"data": f"{agora.day} {MESES[agora.month - 1]}", "torneio": j.get("torneio", m.get("tournament", "")),
                    "fav": j["fav"], "oddFav": j["odd_fav"], "dog": j["dog"], "oddDog": j["odd_dog"], "ct": "all",
                    "liveFav": None, "liveDog": None, "casa": "", "resultado": "por registar", "criado": agora.isoformat()}
            fim_txt = ("\n\nCompara com a odd ao vivo e confirma o marcador antes de apostar.\n"
                       "<a href=\"https://www.betano.pt/live/\">Abrir Betano ao vivo</a>")
            # Alerta 1: favorito perdeu o 1.º set
            if not e.get("s1"):
                e["s1"] = True
                if not fav_s1:
                    p = p_fav(j["odd_fav"], fmt)
                    alvo = ("Odd do favorito fora da tabela." if p is None else
                            f"<b>{j['dog']}</b> só vale <b>acima de {pt(100 / (100 - p))}</b>\n"
                            f"{j['fav']} só vale acima de {pt(100 / p)}")
                    mid = telegram(f"🎾 <b>{j['fav']} perdeu o 1.º set</b> ({F[0]}-{Dg[0]})\n{cab}"
                                   f"2.º set agora: {F[1]}-{Dg[1]} (favorito primeiro)\n\n{alvo}{fim_txt}{resp_txt}")
                    if mid:
                        e["alertado_s1"] = agora.isoformat()
                        log.info("ALERTA 1.º set: %s", chave)
                        guardar_caso(dict(base, estado="dog", bo=fmt[-1], momento=f"2.º set {F[1]}-{Dg[1]} (1.º set {F[0]}-{Dg[0]})",
                                          msg_id=mid if mid is not True else None))
            # Alerta 2: sets 1-1, começou o 3.º set (só melhor de 3)
            if fmt == "BO3" and len(F) >= 3 and not e.get("s3"):
                e["s3"] = True
                fav_s2 = F[1] > Dg[1]
                if fav_s1 != fav_s2:
                    p = p_fav_11(j["odd_fav"], fav_s1)
                    hist = "ganhou o 1.º e perdeu o 2.º" if fav_s1 else "perdeu o 1.º e ganhou o 2.º"
                    alvo = ("Odd do favorito fora da tabela." if p is None else
                            f"<b>{j['fav']}</b> só vale <b>acima de {pt(100 / p)}</b>\n"
                            f"<b>{j['dog']}</b> só vale <b>acima de {pt(100 / (100 - p))}</b>")
                    mid = telegram(f"🎾 <b>3.º set: {j['fav']} v {j['dog']}</b>\n{cab}"
                                   f"Sets: {F[0]}-{Dg[0]}, {F[1]}-{Dg[1]} (favorito {hist})\n"
                                   f"3.º set agora: {F[2]}-{Dg[2]}\n\n{alvo}{fim_txt}{resp_txt}")
                    if mid:
                        e["alertado_s3"] = agora.isoformat()
                        log.info("ALERTA 3.º set: %s", chave)
                        guardar_caso(dict(base, estado="11a" if fav_s1 else "11b", bo="3",
                                          momento=f"3.º set {F[2]}-{Dg[2]} ({F[0]}-{Dg[0]}, {F[1]}-{Dg[1]})",
                                          msg_id=mid if mid is not True else None))
            if e.get("s3") or fmt != "BO3" and e.get("s1"):
                e["fim"] = True
            elif len(F) >= 2 and ((F[0] > Dg[0]) == (F[1] > Dg[1])) and F[1] + Dg[1] >= 6 and abs(F[1] - Dg[1]) >= 2 and max(F[1], Dg[1]) >= 6:
                e["fim"] = True  # 2-0: jogo acabou
        gravar(ESTADO, est)
        fim_janelas = max((datetime.fromisoformat(j["inicio_utc"].replace("Z", "+00:00")) + timedelta(hours=5)
                           for _, j in pendentes), default=agora)
        fim_janelas = max([fim_janelas] + [datetime.fromisoformat(j["inicio_utc"].replace("Z", "+00:00")) + timedelta(hours=5) for j in futuros])
        fim_dia = agora.replace(hour=23, minute=59, second=59)
        segundos = (min(fim_janelas, fim_dia) - agora).total_seconds()
        restantes = max(1, LIMITE_DIA - est["pedidos"])
        espera = max(INTERVALO, int(segundos / restantes))
        log.info("Próxima verificação em %d s.", espera)
        time.sleep(espera)
    gravar(ESTADO, est)


if __name__ == "__main__":
    main()
