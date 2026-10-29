# MONEYBOT

Bot di trading crypto **simulato** (paper trading): prezzi veri, soldi finti.
Serve a capire se una strategia funziona *prima* di rischiare un euro.

**Web app live: <https://ilmagodifut.github.io/moneybot-live/>**: prezzi in diretta, capitale e posizioni ricalcolati in tempo reale.

Il report testuale aggiornato a ogni giro è in **[REPORT.md](REPORT.md)**.

## Come funziona

Una volta all'ora il bot:

1. scarica i prezzi orari di 18 monete (BTC, ETH, SOL, BNB, XRP, ADA, DOGE, AVAX,
   LINK, DOT, LTC, TRX, SUI, NEAR, ATOM, UNI, TON, APT) dal servizio pubblico di Binance;
2. controlla le posizioni aperte: esce se scatta lo stop o se il trend si rompe;
3. cerca nuove entrate:
   - **regole fisse** scelgono le candidate (trend rialzista su più orizzonti, RSI tra 50 e 70);
   - **Claude fa da giudice**: riceve solo i numeri e risponde a 4 domande chiuse con una probabilità
     (trend sano? prezzo già troppo tirato? rischio di mercato da 1 a 5? compro/aspetto/passo?);
   - **il codice decide**: si compra solo se tutte le risposte superano le soglie scritte in `moneybot/config.py`;
4. aggiorna `REPORT.md` e salva tutto nella cartella `state/`.

Senza chiave Claude il bot funziona lo stesso, usando solo le regole.

### Regole di rischio

| Regola | Valore |
|---|---|
| Capitale simulato iniziale | 1.000 $ |
| Dimensione di ogni posizione | 10% del capitale |
| Posizioni aperte al massimo | 5 |
| Commissioni + slippage simulati | 0,1% + 0,05% a ogni operazione |
| Stop iniziale | 2 ATR sotto l'entrata (minimo 3%) |
| Trailing stop | 3 ATR sotto il massimo raggiunto (minimo 4%) |
| Perdita giornaliera massima | -3%: niente nuovi acquisti fino al giorno dopo |
| Perdita massima dal picco | -25%: bot fermo finché non lo riattivi (`python -m moneybot resume`) |

La sicurezza di Claude **non** aumenta mai la dimensione di una posizione: decide *se*, mai *quanto*.

### Il controllo di onestà (calibrazione)

Ogni verdetto viene ricontrollato 24 ore dopo. Nel report c'è una tabella: quando Claude dice
"buy" con probabilità 0,8, il prezzo è davvero salito circa 8 volte su 10?
Se no, il giudice non sta aggiungendo valore e le soglie vanno riviste.

## Attivare il giro automatico (gratis, su GitHub)

Il file `.github/workflows/bot.yml` fa girare il bot ogni ora su GitHub Actions,
salva stato e report nel repository e ripubblica la web app su GitHub Pages (branch `gh-pages`).
Puoi lanciarlo anche a mano: scheda **Actions** → **MONEYBOT giro orario** → **Run workflow**.

### Aggiungere Claude come giudice

1. Crea una chiave su <https://console.anthropic.com> (Settings → API Keys) e aggiungi un po' di credito.
2. Nel repository GitHub: **Settings → Secrets and variables → Actions → New repository secret**,
   nome `ANTHROPIC_API_KEY`, valore la chiave.

Il modello predefinito è `claude-sonnet-5-5`. Claude viene chiamato solo per le monete candidate,
al massimo 3 volte all'ora e 36 volte al giorno: circa 0,40 $ al giorno al massimo (1 € ogni 2-3 giorni).
Per spendere quasi niente crea nella scheda **Variables** `MONEYBOT_MODEL` = `claude-haiku-5-5`.
Se la chiave non è valida o il credito finisce, il bot continua da solo con le sole regole.

## Usarlo sul tuo computer

```bash
pip install -r requirements.txt
```

```bash
python -m moneybot run
```

## Cosa NON fa

- Non usa soldi veri e non si collega a nessun conto.
- Non promette guadagni: la cosa più probabile è che non batta il semplice "compra e tieni".
  Il report confronta sempre il bot con quel riferimento.
