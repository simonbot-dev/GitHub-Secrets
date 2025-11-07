# bot.py
import os, json, time, requests
from datetime import datetime, timezone
from dateutil import parser
from requests_oauthlib import OAuth1

# Config from env (set as GitHub secrets)
X_API_KEY = os.environ['X_API_KEY']
X_API_SECRET_KEY = os.environ['X_API_SECRET_KEY']
X_ACCESS_TOKEN = os.environ['X_ACCESS_TOKEN']
X_ACCESS_TOKEN_SECRET = os.environ['X_ACCESS_TOKEN_SECRET']
FINNHUB = os.environ.get('FINNHUB_KEY')
FRED = os.environ.get('FRED_KEY')
TARGET_SYMBOLS = [s.strip().upper() for s in os.environ.get('TARGET_SYMBOLS','AAPL,SPY').split(',')]

IFTTT_FALLBACK = None  # not used if direct X posting works

HEADERS = {'User-Agent': 'market-bot/1.0'}
STATE_FILE = 'last_state.json'

# OAuth1 client for posting to X (v1.1 endpoint)
auth = OAuth1(client_key=X_API_KEY,
              client_secret=X_API_SECRET_KEY,
              resource_owner_key=X_ACCESS_TOKEN,
              resource_owner_secret=X_ACCESS_TOKEN_SECRET)

def load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    with open(STATE_FILE,'r') as f:
        try:
            return json.load(f)
        except:
            return {}

def save_state(state):
    with open(STATE_FILE,'w') as f:
        json.dump(state, f, indent=2)

def short_num(x):
    x = float(x)
    if abs(x) >= 1_000_000_000:
        return f"{x/1_000_000_000:.2f}B"
    if abs(x) >= 1_000_000:
        return f"{x/1_000_000:.2f}M"
    if abs(x) >= 1_000:
        return f"{x/1_000:.2f}k"
    return f"{x:.2f}".rstrip('0').rstrip('.')

def format_time_utc():
    return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')

def tweet_price_move(sym, price, pct):
    arrow = "📈" if pct>0 else "📉"
    tag = "MARKET"
    text = f"{arrow} {sym} — {price:.2f} ({pct:+.2f}%) | {tag} | {format_time_utc()} #stocks"
    if len(text) > 280:
        text = text[:276] + "..."
    return text

def tweet_earnings(sym, beat_miss, rev, rev_est, eps, eps_est):
    text = f"🔔 {sym} earnings {beat_miss.upper()} | Rev {short_num(rev)} (est {short_num(rev_est)}) | EPS {eps:.2f} (est {eps_est:.2f}) | #earnings {format_time_utc()}"
    if len(text) > 280:
        text = text[:276] + "..."
    return text

def tweet_econ_release(name, value, prev=None, est=None):
    text = f"📊 {name} {value}"
    extras = []
    if est is not None:
        extras.append(f"est {est}")
    if prev is not None:
        extras.append(f"prev {prev}")
    if extras:
        text += " (" + " • ".join(extras) + ")"
    text += f" | #economicdata {format_time_utc()}"
    if len(text) > 280:
        text = text[:276] + "..."
    return text

def tweet_news(sym, headline, url=None, source=None):
    src = f" • {source}" if source else ""
    text = f"📰 {sym} — {headline}{src} | {format_time_utc()}"
    if url:
        if len(text) + 1 + len(url) <= 280:
            text = f"{text} {url}"
    if len(text) > 280:
        text = text[:276] + "..."
    return text

def fh_quote(sym):
    try:
        url = f"https://finnhub.io/api/v1/quote?symbol={sym}&token={FINNHUB}"
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.ok:
            return r.json()
    except Exception as e:
        print("quote error", e)
    return None

def fh_company_news(sym, days=1):
    today = datetime.utcnow().date()
    start = (today).isoformat()
    end = (today).isoformat()
    try:
        url = f"https://finnhub.io/api/v1/company-news?symbol={sym}&from={start}&to={end}&token={FINNHUB}"
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.ok:
            return r.json()
    except Exception as e:
        print("news error", e)
    return []

def fh_earnings_calendar(sym):
    try:
        url = f"https://finnhub.io/api/v1/calendar/earnings?symbol={sym}&token={FINNHUB}"
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.ok:
            return r.json()
    except Exception as e:
        print("earnings error", e)
    return {}

def fred_latest_cpi():
    try:
        url = f"https://api.stlouisfed.org/fred/series/observations?series_id=CPIAUCSL&api_key={FRED}&file_type=json"
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.ok:
            data = r.json().get('observations', [])
            for obs in reversed(data):
                if obs.get('value') not in ('.', None):
                    return obs
    except Exception as e:
        print("fred error", e)
    return None

def post_to_x(status):
    url = "https://api.twitter.com/1.1/statuses/update.json"
    try:
        r = requests.post(url, auth=auth, data={'status': status}, timeout=15)
        if r.status_code == 200:
            return True, r.json()
        else:
            print("post failed", r.status_code, r.text)
    except Exception as e:
        print("post exception", e)
    return False, None

def main():
    state = load_state()
    posted = False

    for s in TARGET_SYMBOLS:
        q = fh_quote(s)
        if not q:
            continue
        cur = q.get('c')
        o = q.get('o')
        if o and cur:
            pct = 0.0
            try:
                pct = (cur - o)/o * 100
            except:
                pct = 0.0
            key = f"price_{s}_{datetime.utcnow().date().isoformat()}"
            if abs(pct) >= 2.0 and not state.get(key):
                text = tweet_price_move(s, cur, pct)
                ok, resp = post_to_x(text)
                if ok:
                    state[key] = {'time': datetime.utcnow().isoformat(), 'text': text}
                    print("posted", s, text)
                    posted = True
                time.sleep(1)

    for s in TARGET_SYMBOLS:
        news = fh_company_news(s)
        if news:
            item = news[0]
            uid = f"news_{s}_{item.get('id') or item.get('datetime')}"
            if not state.get(uid):
                headline = item.get('headline') or item.get('summary') or ''
                url = item.get('url')
                source = item.get('source')
                text = tweet_news(s, headline, url, source)
                ok, resp = post_to_x(text)
                if ok:
                    state[uid] = {'time': datetime.utcnow().isoformat(), 'text': text}
                    print("posted news", s)
                    posted = True
                time.sleep(1)

    for s in TARGET_SYMBOLS:
        ev = fh_earnings_calendar(s)
        if ev and isinstance(ev, dict):
            ec = ev.get('earningsCalendar') or ev.get('earnings', [])
            for e in ec:
                date = e.get('date') or e.get('epsEstimated')
                uid = f"earn_{s}_{date}"
                if not state.get(uid):
                    eps = e.get('epsActual')
                    eps_est = e.get('epsEstimate')
                    rev = e.get('revenue')
                    rev_est = e.get('revenueEstimate')
                    if eps is not None:
                        beat = "beat" if eps_est and eps > eps_est else ("miss" if eps_est and eps < eps_est else "inline")
                        text = tweet_earnings(s, beat, rev or 0, rev_est or 0, float(eps), float(eps_est or 0))
                        ok, resp = post_to_x(text)
                        if ok:
                            state[uid] = {'time': datetime.utcnow().isoformat(), 'text': text}
                            print("posted earnings", s)
                            posted = True
                        time.sleep(1)

    cpi = fred_latest_cpi()
    if cpi:
        date = cpi.get('date')
        uid = f"cpi_{date}"
        if not state.get(uid):
            val = cpi.get('value')
            text = tweet_econ_release("US CPI (CPIAUCSL)", val)
            ok, resp = post_to_x(text)
            if ok:
                state[uid] = {'time': datetime.utcnow().isoformat(), 'text': text}
                print("posted cpi", date)
                posted = True

    if posted:
        save_state(state)
    else:
        print("nothing new to post")

if __name__ == "__main__":
    main()
