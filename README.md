# Bot de ténis ao vivo (alertas Telegram)

Lê odds da Betano PT (via OddsPapi) nos jogos de ténis ao vivo. Quando o favorito perde o 1.º set,
compara com odds justas históricas (tennis-data ATP+WTA 2023–2026, 19 105 jogos, melhor de 3) e
envia alerta no Telegram se a odd estiver ≥ odd justa + 5%, nos primeiros 8 min do 2.º set.
Não faz apostas. Corre no GitHub Actions (de hora a hora, cada execução dura ~5h45, uma de cada vez).

## Segredos necessários (Settings → Secrets and variables → Actions)
- `TELEGRAM_TOKEN` — token do @BotFather
- `TELEGRAM_CHAT_ID` — obtido com o workflow "Descobrir chat_id"
- `ODDSPAPI_KEY` — chave OddsPapi

## Registo
`registo_leituras.csv` é atualizado no fim de cada execução (todas as leituras, com ou sem alerta).

## Limitações
- Fim do 1.º set deduzido do mercado "vencedor do 1.º set" (não do marcador).
- Link direto para o jogo depende de a OddsPapi devolver esse campo; senão abre a página ao vivo da Betano.
- Agendamentos do GitHub podem atrasar alguns minutos; nesse intervalo o bot não está a ver jogos.
- Limites de pedidos do plano OddsPapi desconhecidos (≈700 pedidos/hora com 12 jogos e 60 s).
