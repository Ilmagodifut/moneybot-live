import json

import anthropic
import httpx2 as httpx
import pytest

from moneybot import bot, config, judge
from moneybot.data import Candle
from moneybot.indicators import atr, ema, rsi
from moneybot.portfolio import Broker, State, read_trades
from moneybot.strategy import rules_only_gate, snapshot

HOUR = 3600 * 1000


def make_candles(closes, start=0, spread=0.01):
    return [
        Candle(
            open_time=start + i * HOUR,
            open=c,
            high=c * (1 + spread),
            low=c * (1 - spread),
            close=c,
            volume=1000.0,
            close_time=start + (i + 1) * HOUR - 1,
        )
        for i, c in enumerate(closes)
    ]


# --- indicatori ---

def test_ema_of_constant_is_constant():
    assert ema([5.0] * 30, 10)[-1] == pytest.approx(5.0)


def test_rsi_extremes():
    assert rsi([float(i) for i in range(1, 40)])[-1] == 100.0
    assert rsi([float(i) for i in range(40, 1, -1)])[-1] == pytest.approx(0.0)


def test_atr_of_fixed_range():
    highs, lows, closes = [11.0] * 30, [9.0] * 30, [10.0] * 30
    assert atr(highs, lows, closes)[-1] == pytest.approx(2.0)


def test_snapshot_needs_enough_history():
    assert snapshot("X", make_candles([1.0] * 500)) is None
    s = snapshot("X", make_candles([100 + i * 0.1 for i in range(1000)]))
    assert s.trend_fast > s.trend_mid > s.trend_slow


# --- portafoglio ---

def test_buy_then_sell_flat_loses_only_costs(tmp_path):
    state = State()
    broker = Broker(state, tmp_path / "trades.csv")
    assert broker.buy("BTC", 100.0, atr=1.0, time_ms=0, equity=1000.0, reason="test")
    assert state.cash == pytest.approx(900.0)
    pnl = broker.sell("BTC", 100.0, time_ms=HOUR, reason="test")
    # due commissioni (0,1%) e due slippage (0,05%) su ~100$: circa -0,30$
    assert -0.35 < pnl < -0.25
    assert state.cash == pytest.approx(1000.0 + pnl)
    assert [t["side"] for t in read_trades(tmp_path / "trades.csv")] == ["BUY", "SELL"]
    assert state.cooldowns["BTC"] == HOUR + config.COOLDOWN_HOURS * HOUR


def test_stop_has_minimum_distance(tmp_path):
    state = State()
    Broker(state, tmp_path / "t.csv").buy("TRX", 1.0, atr=0.001, time_ms=0, equity=1000.0, reason="test")
    pos = state.positions["TRX"]
    assert pos.stop == pytest.approx(pos.entry_price * (1 - config.STOP_MIN_PCT))


def test_state_roundtrip(tmp_path):
    state = State()
    Broker(state, tmp_path / "t.csv").buy("ETH", 50.0, atr=1.0, time_ms=0, equity=1000.0, reason="test")
    state.save(tmp_path / "state.json")
    loaded = State.load(tmp_path / "state.json")
    assert loaded.positions["ETH"].qty == pytest.approx(state.positions["ETH"].qty)
    assert loaded.cash == pytest.approx(state.cash)


# --- gestione posizioni e sicurezza ---

def test_position_is_stopped_out_when_price_falls(tmp_path):
    closes = [100 + i * 0.1 for i in range(1000)]
    candles = make_candles(closes)
    state = State()
    broker = Broker(state, tmp_path / "t.csv")
    entry_time = candles[-6].close_time
    broker.buy("ETH", closes[-6], atr=0.5, time_ms=entry_time, equity=1000.0, reason="test")
    stop = state.positions["ETH"].stop
    # crollo nell'ultima candela, sotto lo stop
    candles[-1] = Candle(candles[-1].open_time, closes[-2], closes[-2], stop * 0.9, stop * 0.95, 1000.0, candles[-1].close_time)
    snaps = {"ETH": snapshot("ETH", candles)}
    bot.manage_positions(state, broker, {"ETH": candles}, snaps)
    assert "ETH" not in state.positions
    sell = read_trades(tmp_path / "t.csv")[-1]
    assert sell["reason"] == "stop"


def test_trailing_stop_only_moves_up(tmp_path):
    closes = [100 + i * 0.5 for i in range(1000)]
    candles = make_candles(closes, spread=0.001)
    state = State()
    broker = Broker(state, tmp_path / "t.csv")
    broker.buy("SOL", closes[-20], atr=0.5, time_ms=candles[-20].close_time, equity=1000.0, reason="test")
    first_stop = state.positions["SOL"].stop
    bot.manage_positions(state, broker, {"SOL": candles}, {"SOL": snapshot("SOL", candles)})
    assert state.positions["SOL"].stop > first_stop


def test_safety_brakes():
    state = State()
    assert bot.update_safety(state, 1000.0, now=0)
    assert not bot.update_safety(state, 960.0, now=HOUR)       # -4% nel giorno
    assert bot.update_safety(state, 960.0, now=25 * HOUR)      # nuovo giorno, si riparte
    assert not bot.update_safety(state, 700.0, now=26 * HOUR)  # -30% dal massimo
    assert state.halted
    assert not bot.update_safety(state, 1000.0, now=50 * HOUR) # resta fermo finché non lo riattivi


# --- il giudice ---

def good_verdict(**over):
    v = dict(
        trend_healthy="yes", trend_healthy_prob=0.8, overextended="no", overextended_prob=0.7,
        market_risk=2, action="buy", action_prob=0.75, reason="ok",
    )
    v.update(over)
    return judge.Verdict(**v)


def test_gate():
    assert good_verdict().passes_gate()
    assert not good_verdict(action="watch").passes_gate()
    assert not good_verdict(action_prob=0.6).passes_gate()
    assert not good_verdict(overextended="yes").passes_gate()
    assert not good_verdict(market_risk=4).passes_gate()
    assert not good_verdict(trend_healthy="no").passes_gate()


def fake_client(answer: dict, stop_reason="end_turn"):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        seen["beta"] = request.headers.get("anthropic-beta")
        return httpx.Response(200, json={
            "id": "msg_test", "type": "message", "role": "assistant", "model": config.JUDGE_MODEL,
            "content": [{"type": "text", "text": json.dumps(answer)}],
            "stop_reason": stop_reason, "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 10},
        })

    client = anthropic.Anthropic(api_key="test", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    return client, seen


def test_judge_request_and_parse_haiku(monkeypatch):
    monkeypatch.setattr(config, "JUDGE_MODEL", "claude-haiku-5-5")
    s = snapshot("ETH", make_candles([100 + i * 0.1 for i in range(1000)]))
    client, seen = fake_client(good_verdict().__dict__)
    assert judge.ask(client, s, s) == good_verdict()
    assert seen["body"]["model"] == "claude-haiku-5-5"
    assert "fallbacks" not in seen["body"]
    assert seen["body"]["output_config"]["format"]["type"] == "json_schema"
    assert "symbol: ETH" in seen["body"]["messages"][0]["content"]


def test_judge_uses_fallbacks_on_bigger_models(monkeypatch):
    monkeypatch.setattr(config, "JUDGE_MODEL", "claude-opus-5-5")
    s = snapshot("ETH", make_candles([100 + i * 0.1 for i in range(1000)]))
    client, seen = fake_client(good_verdict().__dict__)
    assert judge.ask(client, s, s) == good_verdict()
    assert seen["body"]["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in seen["beta"]


def test_daily_judge_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(bot.judge, "ask", lambda *a: None)
    monkeypatch.setattr(bot, "is_candidate", lambda s: True)
    candles = make_candles([100 + i * 0.1 for i in range(1000)])
    snaps = {sym: snapshot(sym, candles) for sym in ["A", "B", "C"]}
    state = State(day="2026-01-01", judge_day="2026-01-01", judge_calls_today=config.MAX_JUDGE_CALLS_PER_DAY - 1)
    bot.look_for_entries(state, Broker(state, tmp_path / "t.csv"), snaps, 1000.0, candles[-1].close_time, tmp_path / "v.jsonl")
    assert state.judge_calls_today == config.MAX_JUDGE_CALLS_PER_DAY
    assert len((tmp_path / "v.jsonl").read_text().splitlines()) == 1


def test_judge_refusal_is_a_pass():
    s = snapshot("ETH", make_candles([100 + i * 0.1 for i in range(1000)]))
    client, _ = fake_client({}, stop_reason="refusal")
    assert judge.ask(client, s, None) is None


# --- dati per la dashboard ---

def test_export_writes_row_files(tmp_path):
    from moneybot import export
    candles = make_candles([100 + i * 0.1 for i in range(1000)])
    snaps = {"BTC": snapshot("BTC", candles)}
    state = State()
    p = bot.paths(tmp_path)
    Broker(state, p["trades"]).buy("BTC", 120.0, atr=1.0, time_ms=candles[-1].close_time, equity=1000.0, reason="test")
    state.benchmark_start = {"BTC": 100.0}
    export.append_equity(state, {"BTC": 129.9}, candles[-1].close_time, p["equity"])
    export.write_all(state, snaps, p, p["dashboard"])
    summary = json.loads((p["dashboard"] / "summary.json").read_text())[0]
    assert summary["open_positions"] == 1
    assert summary["benchmark"] == pytest.approx(1999.0, rel=1e-3)
    assert json.loads((p["dashboard"] / "market.json").read_text())[0]["trend"] == "up"
    assert json.loads((p["dashboard"] / "positions.json").read_text())[0]["symbol"] == "BTC"
    assert len(json.loads((p["dashboard"] / "equity.json").read_text())) == 1


def test_same_candle_is_evaluated_once(tmp_path, monkeypatch):
    candles = make_candles([100 + i * 0.1 for i in range(1000)])
    monkeypatch.setattr(bot, "fetch_candles", lambda *a: candles)
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(config, "REPORT_PATH", tmp_path / "REPORT.md")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    calls = []
    real = bot.look_for_entries
    monkeypatch.setattr(bot, "look_for_entries", lambda *a: calls.append(1) or real(*a))
    bot.run()
    bot.run()
    assert len(calls) == 1
    assert len((tmp_path / "equity.csv").read_text().splitlines()) == 2  # intestazione + 1 riga


def test_rules_take_over_when_claude_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")

    def no_credit(*a):
        raise judge.JudgeUnavailable

    monkeypatch.setattr(bot.judge, "ask", no_credit)
    monkeypatch.setattr(bot, "is_candidate", lambda s: True)
    monkeypatch.setattr(bot, "rules_only_gate", lambda s: True)
    candles = make_candles([100 + i * 0.1 for i in range(1000)])
    snaps = {sym: snapshot(sym, candles) for sym in ["A", "B"]}
    state = State(day="2026-01-01")
    bot.look_for_entries(state, Broker(state, tmp_path / "t.csv"), snaps, 1000.0, candles[-1].close_time, tmp_path / "v.jsonl")
    rows = [json.loads(l) for l in (tmp_path / "v.jsonl").read_text().splitlines()]
    assert [r["mode"] for r in rows] == ["rules", "rules"]
    assert set(state.positions) == {"A", "B"}


def test_no_credit_error_becomes_unavailable():
    s = snapshot("ETH", make_candles([100 + i * 0.1 for i in range(1000)]))

    def handler(request):
        return httpx.Response(400, json={"type": "error", "error": {"type": "invalid_request_error", "message": "Your credit balance is too low to access the Anthropic API."}})

    client = anthropic.Anthropic(api_key="test", max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(judge.JudgeUnavailable):
        judge.ask(client, s, None)


# --- backtest ---

def test_backtest_trades_and_stats():
    from moneybot import backtest
    # salita lenta, poi crollo: il bot deve entrare e poi uscire
    closes = [100 * (1.0004 ** i) for i in range(2500)] + [100 * (1.0004 ** 2500) * (0.995 ** i) for i in range(300)]
    rows = [[i * HOUR, c, c * 1.002, c * 0.998, c, 1, i * HOUR + HOUR - 1, 1000.0] for i, c in enumerate(closes)]
    s = backtest.build_series(rows)
    times = sorted(s.index)[backtest.WARMUP:]
    res = backtest.simulate({"BTC": s}, times, backtest.LIVE)
    assert res["trades"], "nessuna operazione"
    assert res["trades"][-1]["reason"] in ("stop", "trend")
    st = backtest.stats(res, times[0], times[-1] + HOUR)
    assert st["trades"] == len(res["trades"])
    assert -1 < st["return"] < 1


def test_backtest_rules_match_live_rules():
    from moneybot import backtest
    closes = [100 + i * 0.05 + (i % 7) * 0.3 for i in range(1500)]
    candles = make_candles(closes)
    rows = [[c.open_time, c.open, c.high, c.low, c.close, 1, c.close_time, c.volume] for c in candles]
    s = backtest.build_series(rows)
    snap = snapshot("X", candles)
    assert backtest.passes_rules(s, len(rows) - 1, backtest.LIVE) == rules_only_gate(snap)


# --- capitale rapido ---

def test_fast_capital_buys_dip_and_sells_rebound(tmp_path):
    from moneybot import fast
    base = [100.0] * 1300
    dip = base + [91.0]                 # -9% sotto la media: compra
    rebound = dip + [101.0]             # torna sopra la media: vende
    out = tmp_path / "dash"
    out.mkdir()
    for k, closes in enumerate([dip, rebound]):
        cs = make_candles(closes)
        fast.run({"BTC": cs, "ETH": make_candles(base)}, cs[-1].close_time, True, tmp_path, out)
        state = State.load(fast.paths(tmp_path)["state"])
        if k == 0:
            assert "BTC" in state.positions
            assert state.positions["BTC"].stop == pytest.approx(state.positions["BTC"].entry_price * 0.9)
    assert state.positions == {}
    trades = read_trades(fast.paths(tmp_path)["trades"])
    assert [x["side"] for x in trades] == ["BUY", "SELL"] and float(trades[-1]["pnl"]) > 0
    assert json.loads((out / "fast_summary.json").read_text())[0]["closed_trades"] == 1
    assert len(json.loads((out / "fast_watch.json").read_text())) == 2
