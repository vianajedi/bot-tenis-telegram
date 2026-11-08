#!/usr/bin/env python3
"""
Bot de scouting de ténis ao vivo (Betano PT via OddsPapi) com alertas no Telegram.

O que faz, em cada ciclo:
  1. Lista os jogos de ténis ao vivo (OddsPapi, sportId 12).
  2. Guarda a última odd pré-jogo da Betano de cada jogador (para saber quem é o favorito).
  3. Deteta o fim do 1.º set pelo mercado "vencedor do 1.º set" (período p1):
     quando um jogador chega a odd <= LIMITE_P1 nesse mercado (ou o mercado fecha
     depois disso), considera-se que ganhou o set.
  4. Se o FAVORITO perdeu o 1.º set, compara as odds atuais da Betano no vencedor do
     jogo com as odds justas da tabela histórica (tennis-data 2023-2026).
  5. Se uma odd estiver acima da odd justa x (1 + margem de segurança), envia alerta
     no Telegram com o link. Regista SEMPRE a leitura em CSV, com ou sem alerta.

NÃO faz apostas. Só lê odds e envia mensagens.
"""
import csv
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

import requests

BASE_URL = "https://api.oddspapi.io/v4"
SPORT_TENNIS = 12
MERCADO_VENCEDOR = "121"          # Winner (jogo), confirmado no blog da OddsPapi
BOOK = "betano.pt"

AQUI = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(AQUI, "config.json")
ESTADO_PATH = os.path.join(AQUI, "estado.json")
REGISTO_PATH = os.path.join(AQUI, "registo_leituras.csv")

# Odds justas (= 1 / probabilidade histórica), melhor de 3, favorito PERDEU o 1.º set.
# Fonte: tennis-data.co.uk ATP+WTA 2023 -> 1 nov 2026, odds Bet365 pré-jogo, 19 105 jogos.
# (odd_fav_min, odd_fav_max, justa_nao_favorito, justa_favorito, n)
TABELA_BO3 = [
    (1.05, 1.10, 2.65, 1.61, 98),
    (1.10, 1.15, 2.20, 1.83, 213),
    (1.15, 1.20, 1.97, 2.03, 140),
    (1.20, 1.25, 1.80, 2.25, 263),
    (1.25, 1.30, 1.60, 2.67, 392),
    (1.30, 1.45, 1.49, 3.04, 1509),
    (1.45, 1.60, 1.42, 3.36, 1259),
    (1.60, 1.80, 1.34, 3.91, 1654),
    (1.80, 2.10, 1.29, 4.46, 635),
]

log = logging.getLogger("bot_tenis")


# ---------------------------------------------------------------- utilidades
def agora():
    return datetime.now(timezone.utc)


def carregar_json(caminho, defeito):
    try:
        with open(caminho, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return defeito


def gravar_json(caminho, dados):
    tmp = caminho + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=1)
    os.replace(tmp, caminho)


def banda(odd_fav):
    for lo, hi, justa_dog, justa_fav, n in TABELA_BO3:
        if lo <= odd_fav < hi:
            return {"lo": lo, "hi": hi, "justa_dog": justa_dog, "justa_fav": justa_fav, "n": n}
    return None


# ---------------------------------------------------------------- OddsPapi
class OddsPapi:
    def __init__(self, api_key):
        self.key = api_key
        self.s = requests.Session()
        self.chamadas = 0

    def get(self, caminho, **params):
        params["apiKey"] = self.key
        self.chamadas += 1
        r = self.s.get(f"{BASE_URL}/{caminho}", params=params, timeout=20)
        if r.status_code == 429:
            raise RuntimeError("Limite de pedidos da OddsPapi atingido (429). Aumenta o intervalo.")
        r.raise_for_status()
        return r.json()

    def jogos_hoje(self):
        hoje = agora().date()
        amanha = datetime.fromordinal(hoje.toordinal() + 1).date()
        dados = self.get("fixtures", sportId=SPORT_TENNIS, **{"from": hoje.isoformat(), "to": amanha.isoformat()})
        return dados if isinstance(dados, list) else dados.get("data", [])

    def odds(self, fixture_id):
        return self.get("odds", fixtureId=fixture_id)

    def mercados(self):
        dados = self.get("markets", sportId=SPORT_TENNIS)
        return dados if isinstance(dados, list) else dados.get("data", [])


def precos_mercado(book_odds, mercado_id):
    """Devolve {"0": preço jogador 1, "1": preço jogador 2} para um mercado de 2 resultados.
    Os outcomes vêm por ID (ex.: 121 = jogador 1, 122 = jogador 2 no mercado 121, como no
    ficheiro piloto da OddsPapi); ordena-se pelo ID. "players" -> "0" é o preço principal."""
    mk = (book_odds.get("markets") or {}).get(str(mercado_id))
    if not mk:
        return {}
    outs = mk.get("outcomes") or {}
    if len(outs) != 2:
        return {}
    res = {}
    for i, oid in enumerate(sorted(outs, key=lambda x: int(x) if str(x).isdigit() else str(x))):
        p = (outs[oid].get("players") or {}).get("0") or {}
        if p.get("active", True) and p.get("price"):
            res[str(i)] = float(p["price"])
    return res


def encontrar_link(book_odds, fixture, cfg):
    """Procura um link direto para o evento na Betano nos campos da resposta.
    Os nomes destes campos NÃO estão confirmados na documentação pública; se nenhum
    existir, usa a página de ténis ao vivo da Betano."""
    for campo in ("fixturePath", "link", "url", "deepLink", "eventUrl"):
        v = book_odds.get(campo) or fixture.get(campo)
        if isinstance(v, str) and v.startswith("http"):
            return v
        if isinstance(v, str) and v.startswith("/"):
            return "https://www.betano.pt" + v
    return cfg.get("link_fallback", "https://www.betano.pt/live/")


# ---------------------------------------------------------------- Telegram
def telegram(cfg, texto):
    url = f"https://api.telegram.org/bot{cfg['telegram_token']}/sendMessage"
    r = requests.post(url, json={
        "chat_id": cfg["telegram_chat_id"],
        "text": texto,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }, timeout=20)
    if not r.ok:
        log.error("Telegram falhou: %s %s", r.status_code, r.text[:200])
    return r.ok


# ---------------------------------------------------------------- registo
CAMPOS = ["hora_utc", "fixture", "jogo", "torneio", "fav", "odd_fav_pre", "odd_dog_pre",
          "set1_vencedor", "odd_fav_vivo", "odd_dog_vivo", "justa_fav", "justa_dog",
          "alerta", "lado_alerta", "link"]


def registar(linha):
    novo = not os.path.exists(REGISTO_PATH)
    with open(REGISTO_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS, delimiter=";")
        if novo:
            w.writeheader()
        w.writerow({k: linha.get(k, "") for k in CAMPOS})


# ---------------------------------------------------------------- lógica
def descobrir_mercado_p1(api, cfg):
    """Procura o ID do mercado 'vencedor do 1.º set' (period == 'p1', 2 resultados, sem handicap)."""
    if cfg.get("mercado_set1"):
        return str(cfg["mercado_set1"])
    candidatos = []
    for m in api.mercados():
        nome = str(m.get("marketName", "")).lower()
        if str(m.get("period", "")).lower() == "p1" and not m.get("handicap") and \
                ("winner" in nome or "vencedor" in nome) and "game" not in nome:
            candidatos.append(m)
    if not candidatos:
        raise RuntimeError("Não encontrei o mercado do 1.º set em /markets. Define 'mercado_set1' no config.json.")
    escolhido = str(candidatos[0]["marketId"])
    log.info("Mercado do 1.º set: %s (%s)", escolhido, candidatos[0].get("marketName"))
    return escolhido


def ciclo(api, cfg, estado, mercado_p1):
    margem = float(cfg.get("margem_seguranca", 0.05))
    janela_min = float(cfg.get("minutos_validos_apos_set1", 8))
    limite_p1 = float(cfg.get("limite_odd_set1", 1.08))

    jogos = api.jogos_hoje()
    vivos = [j for j in jogos if str(j.get("statusName", "")).lower() == "live" and j.get("hasOdds", True)]
    pre = [j for j in jogos if str(j.get("statusName", "")).lower() in ("pre-game", "pregame", "not started")]

    # 1) odds pré-jogo: só para jogos que começam nos próximos 30 min (poupa pedidos)
    for j in pre:
        fid = j["fixtureId"]
        try:
            ini = datetime.fromisoformat(str(j["startTime"]).replace("Z", "+00:00"))
        except Exception:
            continue
        if (ini - agora()).total_seconds() > 1800:
            continue
        try:
            bo = api.odds(fid).get("bookmakerOdds", {}).get(BOOK)
        except Exception as e:
            log.warning("odds pré %s: %s", fid, e)
            continue
        if not bo:
            continue
        pr = precos_mercado(bo, MERCADO_VENCEDOR)
        if len(pr) == 2:
            e = estado.setdefault(fid, {})
            e["pre"] = pr
            e["nomes"] = {"0": j.get("participant1Name"), "1": j.get("participant2Name")}

    # 2) jogos ao vivo
    for j in vivos:
        fid = j["fixtureId"]
        e = estado.setdefault(fid, {})
        if e.get("concluido"):
            continue
        try:
            bo = api.odds(fid).get("bookmakerOdds", {}).get(BOOK)
        except Exception as ex:
            log.warning("odds vivo %s: %s", fid, ex)
            continue
        if not bo:
            continue
        nomes = {"0": j.get("participant1Name"), "1": j.get("participant2Name")}
        e["nomes"] = nomes
        vivo = {} if bo.get("suspended") else precos_mercado(bo, MERCADO_VENCEDOR)

        if "pre" not in e:
            # Jogo já estava a decorrer quando o bot arrancou: sem odd pré-jogo, ignora.
            e["concluido"] = "sem_odd_pre"
            continue
        pre_odds = e["pre"]
        if len(pre_odds) != 2 or pre_odds["0"] == pre_odds["1"]:
            e["concluido"] = "sem_favorito"
            continue
        fav = "0" if pre_odds["0"] < pre_odds["1"] else "1"
        dog = "1" if fav == "0" else "0"

        # deteção do vencedor do 1.º set
        if not e.get("set1"):
            p1 = precos_mercado(bo, mercado_p1)
            for pidx, preco in p1.items():
                if preco <= limite_p1:
                    e["quase_set1"] = pidx
            if not p1 and e.get("quase_set1"):
                # mercado do 1.º set fechou depois de um jogador estar quase a ganhar
                e["set1"] = e["quase_set1"]
                e["set1_hora"] = agora().isoformat()
            elif p1 and any(v <= 1.01 for v in p1.values()):
                e["set1"] = min(p1, key=p1.get)
                e["set1_hora"] = agora().isoformat()
            if not e.get("set1"):
                continue

        if e["set1"] == fav:
            e["concluido"] = "favorito_ganhou_set1"
            continue

        # favorito perdeu o 1.º set: só avalia na janela inicial do 2.º set
        decorrido = (agora() - datetime.fromisoformat(e["set1_hora"])).total_seconds() / 60
        if decorrido > janela_min:
            e["concluido"] = "fora_da_janela"
            continue
        if len(vivo) != 2:
            continue

        b = banda(pre_odds[fav])
        if not b:
            e["concluido"] = f"odd_fav_fora_tabela_{pre_odds[fav]}"
            continue

        odd_fav, odd_dog = vivo[fav], vivo[dog]
        lado = ""
        if odd_dog >= b["justa_dog"] * (1 + margem):
            lado = "dog"
        elif odd_fav >= b["justa_fav"] * (1 + margem):
            lado = "fav"

        link = encontrar_link(bo, j, cfg)
        chave_leitura = f"{round(odd_fav, 2)}|{round(odd_dog, 2)}"
        if e.get("ultima_leitura") != chave_leitura:
            e["ultima_leitura"] = chave_leitura
            registar({
                "hora_utc": agora().strftime("%Y-%m-%d %H:%M:%S"), "fixture": fid,
                "jogo": f"{nomes['0']} v {nomes['1']}", "torneio": j.get("tournamentName", ""),
                "fav": nomes[fav], "odd_fav_pre": pre_odds[fav], "odd_dog_pre": pre_odds[dog],
                "set1_vencedor": nomes[e["set1"]], "odd_fav_vivo": odd_fav, "odd_dog_vivo": odd_dog,
                "justa_fav": b["justa_fav"], "justa_dog": b["justa_dog"],
                "alerta": "sim" if lado else "não", "lado_alerta": lado, "link": link,
            })

        if lado and not e.get("alertado"):
            alvo = dog if lado == "dog" else fav
            odd_alvo = odd_dog if lado == "dog" else odd_fav
            justa = b["justa_dog"] if lado == "dog" else b["justa_fav"]
            vantagem = odd_alvo / justa - 1
            texto = (
                f"🎾 <b>Possível valor — {nomes[alvo]}</b>\n"
                f"{j.get('tournamentName', '')}\n"
                f"{nomes['0']} v {nomes['1']}\n\n"
                f"Favorito antes do jogo: {nomes[fav]} @ {pre_odds[fav]:.2f} "
                f"(adversário {pre_odds[dog]:.2f})\n"
                f"1.º set: ganhou <b>{nomes[e['set1']]}</b>\n\n"
                f"Odd Betano agora: <b>{odd_alvo:.2f}</b>\n"
                f"Odd justa (histórico, n={b['n']}): {justa:.2f}\n"
                f"Vantagem estimada: {vantagem:+.1%}\n\n"
                f"⚠️ Confirma o marcador (quebra no 2.º set muda tudo) e a odd antes de apostar.\n"
                f"<a href=\"{link}\">Abrir na Betano</a>"
            )
            if telegram(cfg, texto):
                e["alertado"] = True
                log.info("ALERTA enviado: %s %s @ %.2f", fid, nomes[alvo], odd_alvo)

    # limpeza: jogos fora da lista de hoje
    ids_hoje = {j["fixtureId"] for j in jogos}
    for fid in list(estado):
        if fid not in ids_hoje:
            del estado[fid]


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout),
                                  logging.FileHandler(os.path.join(AQUI, "bot_tenis.log"), encoding="utf-8")])
    cfg = carregar_json(CONFIG_PATH, None)
    if not cfg and os.environ.get("TELEGRAM_TOKEN"):
        # Modo GitHub Actions: configuração por variáveis de ambiente (segredos do repositório)
        cfg = carregar_json(os.path.join(AQUI, "config.example.json"), {})
        cfg.update({
            "oddspapi_key": os.environ.get("ODDSPAPI_KEY", ""),
            "telegram_token": os.environ["TELEGRAM_TOKEN"],
            "telegram_chat_id": os.environ.get("TELEGRAM_CHAT_ID", ""),
        })
        for k in ("intervalo_segundos", "margem_seguranca", "minutos_validos_apos_set1"):
            if os.environ.get(k.upper()):
                cfg[k] = float(os.environ[k.upper()])
    if not cfg:
        sys.exit("Falta o config.json (copia config.example.json e preenche).")
    duracao = None
    for a in sys.argv:
        if a.startswith("--duracao-min="):
            duracao = float(a.split("=", 1)[1]) * 60
    arranque = time.time()

    if "--chatid" in sys.argv:
        r = requests.get(f"https://api.telegram.org/bot{cfg['telegram_token']}/getUpdates", timeout=20).json()
        if not r.get("ok"):
            sys.exit(f"Telegram recusou o token: {r}")
        ids = {u[k]["chat"]["id"]: u[k]["chat"].get("first_name", "")
               for u in r.get("result", []) for k in ("message", "edited_message") if k in u}
        if not ids:
            sys.exit("Sem mensagens. Abre o bot no Telegram, carrega em Start, envia 'olá' e corre outra vez.")
        chat_id, nome = list(ids.items())[-1]
        cfg["telegram_chat_id"] = str(chat_id)
        gravar_json(CONFIG_PATH, cfg)
        print(f"chat_id {chat_id} ({nome}) gravado no config.json.")
        sys.exit(0)

    if "--teste" in sys.argv:
        ok = telegram(cfg, "✅ Bot de ténis ligado. Esta é uma mensagem de teste.")
        sys.exit(0 if ok else 1)

    api = OddsPapi(cfg["oddspapi_key"])
    mercado_p1 = descobrir_mercado_p1(api, cfg)
    estado = carregar_json(ESTADO_PATH, {})
    intervalo = int(cfg.get("intervalo_segundos", 60))
    log.info("A correr. Intervalo %ss, margem de segurança %.0f%%.", intervalo,
             100 * float(cfg.get("margem_seguranca", 0.05)))

    while True:
        inicio = time.time()
        try:
            ciclo(api, cfg, estado, mercado_p1)
            gravar_json(ESTADO_PATH, estado)
        except Exception as ex:
            log.error("Erro no ciclo: %s", ex)
        log.info("Ciclo feito. Pedidos à API até agora: %d", api.chamadas)
        if "--uma-vez" in sys.argv:
            break
        if duracao and time.time() - arranque > duracao:
            log.info("Duração máxima atingida; a terminar.")
            break
        time.sleep(max(5, intervalo - (time.time() - inicio)))


if __name__ == "__main__":
    main()
