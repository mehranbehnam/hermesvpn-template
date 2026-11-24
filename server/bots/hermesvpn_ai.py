"""HermesVPN — LLM agent loop on OpenRouter (owner 2026-11-14: Claude Haiku 5.5 for the admin bot,
later customer support). Generic: callers pass a system prompt, tool schemas and tool implementations.
Key: /root/.hermesvpn-openrouter-key (600, $5/month cap on OpenRouter). Usage → /var/log/hermesvpn-ai.jsonl
"""
import datetime
import json
import time
import urllib.request

KEY_FILE = "/root/.hermesvpn-openrouter-key"
MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"   # default = free
HAIKU = "anthropic/claude-haiku-5.5"   # owner 2026-11-17: Haiku ONLY for the admin bot (and Agent Hermes admin/leyla on Hetzner), not customer support
# owner 2026-11-17: «مدل رایگان‌های خودمان را هم وصل کن» — اگر Haiku جواب نداد (سقف ۵ دلار، خطای سرویس، کندی)،
# به ترتیب Agent Hermes سراغ رایگان‌ها برو: اول ERNIE (همان پشتیبان اول Agent Hermes)، بعد رایگان‌های OpenRouter
# که ابزار (tool calling) پشتیبانی می‌کنند. Gemini/LongCat/StepFun/Dahl کلیدشان روی هتزنر است و اینجا نیستند.
FALLBACKS = ["baidu/ernie-5.5-flash-preview:free", "google/gemma-4-31b-it:free"]
USAGE_LOG = "/var/log/hermesvpn-ai.jsonl"
URL = "https://openrouter.ai/api/v1/chat/completions"


def available():
    try:
        return bool(open(KEY_FILE).read().strip())
    except Exception:
        return False


def jalali_today():
    """Today's date in Tehran, Gregorian + Jalali (computed, never left to the model)."""
    tz = datetime.timezone(datetime.timedelta(hours=3, minutes=30))
    now = datetime.datetime.now(tz)
    try:
        import jdatetime
        j = jdatetime.datetime.fromgregorian(datetime=now.replace(tzinfo=None))
        jd = j.strftime("%Y/%m/%d")
    except Exception:
        jd = "?"
    return f"{now:%Y-%m-%d %H:%M} (Tehran) = {jd} شمسی"


def _post(payload, timeout=90):
    key = open(KEY_FILE).read().strip()
    req = urllib.request.Request(URL, data=json.dumps(payload).encode(),
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                                          "X-Title": "HermesVPN admin bot"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _log_usage(who, resp):
    try:
        u = resp.get("usage") or {}
        with open(USAGE_LOG, "a") as f:
            f.write(json.dumps({"ts": int(time.time()), "who": who, "model": resp.get("model"),
                                "in": u.get("prompt_tokens"), "out": u.get("completion_tokens"),
                                "cost": u.get("cost")}) + "\n")
    except Exception:
        pass


def _post_chain(payload, who, primary=None):
    """primary (e.g. Haiku for the admin bot) first, then the free chain."""
    last = None
    for model in ([primary] if primary else []) + [MODEL] + FALLBACKS:
        try:
            resp = _post(dict(payload, model=model), timeout=60 if model == MODEL else 90)
            if resp.get("error") or not resp.get("choices"):
                raise RuntimeError(str(resp.get("error"))[:200])
            if model != MODEL:
                resp.setdefault("model", model)
            return resp
        except Exception as e:
            last = e
            try:
                with open(USAGE_LOG, "a") as f:
                    f.write(json.dumps({"ts": int(time.time()), "who": who, "model": model, "error": str(e)[:160]}) + "\n")
            except Exception:
                pass
    raise RuntimeError(f"all models failed: {last}")


def run_agent(system, history, user_text, tools, impl, who="admin", max_steps=7, max_tokens=900, primary=None):
    """history: list of prior {"role","content"} (user/assistant text only). Returns (reply_text, new_history).
    impl: name -> callable(**args) -> str. Tool errors are returned to the model as text."""
    msgs = [{"role": "system", "content": system}] + list(history) + [{"role": "user", "content": user_text}]
    for _ in range(max_steps):
        resp = _post_chain({"messages": msgs, "tools": tools, "max_tokens": max_tokens,
                            "temperature": 0.2, "usage": {"include": True}}, who, primary)
        _log_usage(who, resp)
        m = resp["choices"][0]["message"]
        calls = m.get("tool_calls") or []
        if not calls:
            text = (m.get("content") or "").strip()
            new_hist = (list(history) + [{"role": "user", "content": user_text},
                                         {"role": "assistant", "content": text}])[-30:]
            return text, new_hist
        msgs.append({"role": "assistant", "content": m.get("content") or "", "tool_calls": calls})
        for c in calls:
            name = c["function"]["name"]
            try:
                args = json.loads(c["function"].get("arguments") or "{}")
            except Exception:
                args = {}
            try:
                out = impl[name](**args) if name in impl else f"unknown tool {name}"
            except Exception as e:
                out = f"ERROR {type(e).__name__}: {e}"
            msgs.append({"role": "tool", "tool_call_id": c["id"], "content": str(out)[:6000]})
    return "کار طولانی شد و نیمه‌تمام ماند؛ لطفاً دقیق‌تر بگو چه کنم.", list(history)[-16:]


def month_cost():
    """Rough spend this month from the usage log (OpenRouter returns cost in USD when available)."""
    t0 = datetime.datetime.now().replace(day=1, hour=0, minute=0, second=0).timestamp()
    tot = 0.0
    try:
        for ln in open(USAGE_LOG):
            d = json.loads(ln)
            if d.get("ts", 0) >= t0 and d.get("cost"):
                tot += float(d["cost"])
    except Exception:
        pass
    return tot
