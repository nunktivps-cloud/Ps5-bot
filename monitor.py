"""Monitor de preço PS5 Slim -> Telegram (com foto). Só usa a biblioteca padrão."""
import datetime, json, os, re, statistics, urllib.request, urllib.parse

TOKEN = re.sub(r"[^A-Za-z0-9:_-]", "", os.environ.get("TELEGRAM_TOKEN", ""))
CHAT_ID = re.sub(r"[^0-9-]", "", os.environ.get("TELEGRAM_CHAT_ID", ""))
BUY_PRICE = float(os.environ.get("MAX_PRICE", "3750"))      # meta de compra
ALERT_MAX = float(os.environ.get("ALERT_MAX", "4199.99"))   # só avisa até esse valor
MIN_PRICE = 2000  # ignora acessórios e jogos
TEST = os.environ.get("TEST", "") not in ("", "0")
STATE_FILE = "state.json"

BAD = ["825", "controle", "dualsense", "capa ", "suporte", "headset", "pulse",
       "cabo", "carregador", "ps4", "playstation 4", "ps vita", "portal", "base ",
       "usado", "seminovo", "semi-novo", "recondicionado", "defeito", "sucata", "caixa vazia", "peças"]
MARKETPLACES = ("Mercado Livre", "Shopee")
GAMES = r"jogo|game|astro bot|gran turismo|god of war|spider|fc ?2\d|ea sports|ratchet|horizon|last of us|ghost|returnal|call of duty|mortal kombat"

UA = ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36")


def fetch(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept-Language": "pt-BR,pt;q=0.9",
        "Accept": "text/html,application/json,*/*"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read().decode("utf-8", "ignore")


def to_float(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[^\d.,]", "", str(v))
    if not s:
        return None
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def brl(v):
    return "R$ " + "{:,.2f}".format(v).replace(",", "X").replace(".", ",").replace("X", ".")


PRICE_KEYS = ("bestPrice", "priceWithDiscount", "price_with_discount", "salePrice", "lowPrice", "price")


def walk(o):
    if isinstance(o, dict):
        yield o
        for v in o.values():
            yield from walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from walk(v)


def deep_items(o, depth=3):
    if depth < 0:
        return
    if isinstance(o, dict):
        for k, v in o.items():
            yield k, v
            if isinstance(v, (dict, list)):
                yield from deep_items(v, depth - 1)
    elif isinstance(o, list):
        for v in o[:5]:
            yield from deep_items(v, depth - 1)


def price_of(d):
    for k in PRICE_KEYS:
        v = d.get(k)
        if isinstance(v, dict):
            p = price_of(v)
            if p:
                return p
        elif v is not None and not isinstance(v, list):
            p = to_float(v)
            if p:
                return p
    off = d.get("offers")
    if isinstance(off, list) and off:
        off = off[0]
    if isinstance(off, dict):
        return price_of(off)
    return None


def image_of(d):
    for k in ("image", "imageUrl", "image_url", "thumbnail", "photo", "img"):
        v = d.get(k)
        if isinstance(v, list) and v:
            v = v[0]
        if isinstance(v, dict):
            v = v.get("url") or v.get("src")
        if isinstance(v, str) and v.startswith(("http", "//")):
            return "https:" + v if v.startswith("//") else v
    return None


def extras(d, price):
    pix = parcel = inst = coupon = None
    for k, v in deep_items(d):
        kl = str(k).lower()
        if pix is None and any(s in kl for s in ("pix", "cash", "avista")) and not isinstance(v, (dict, list)):
            p = to_float(v)
            if p and 0.7 * price <= p <= price:
                pix = p
        if coupon is None and ("coupon" in kl or "cupom" in kl) and isinstance(v, str) and v.strip():
            coupon = v.strip()
        if inst is None and "install" in kl and isinstance(v, dict):
            qn = next((v[x] for x in ("quantity", "installments", "count", "number") if x in v), None)
            am = next((to_float(v[x]) for x in ("amount", "value", "price") if x in v), None)
            if qn and am:
                inst = "%sx %s" % (qn, brl(am))
    if pix and pix < price:
        parcel, price = price, pix
    return pix, parcel, inst, coupon


LINK_KEYS = ("url", "permalink", "link", "href", "path", "productUrl", "product_url")
IMG_RE = re.compile(r"\.(jpe?g|png|webp|gif)(\?|$)", re.I)


def find_link(d):
    for k in LINK_KEYS:
        v = d.get(k)
        if isinstance(v, str) and v and not IMG_RE.search(v):
            return v
    for k, v in deep_items(d, 2):
        if str(k) in LINK_KEYS and isinstance(v, str) and v.startswith(("http", "/")) and not IMG_RE.search(v):
            return v
    return None


def is_used(d):
    for k, v in deep_items(d, 2):
        if "condition" in str(k).lower() and isinstance(v, str) and re.search(r"used|usado|refurb|recondic", v, re.I):
            return True
    return False


def from_html(url):
    html = fetch(url)
    blobs = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S)
    blobs += re.findall(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    out, seen = [], set()
    for b in blobs:
        try:
            obj = json.loads(b)
        except Exception:
            continue
        for d in walk(obj):
            name = d.get("name") or d.get("title")
            if not isinstance(name, str):
                continue
            p = price_of(d)
            if not p:
                continue
            if is_used(d):
                continue
            link = find_link(d)
            full = urllib.parse.urljoin(url, link or url)
            direct = bool(link) and full.rstrip("/") != url.rstrip("/")
            if (name.lower(), p) in seen:
                continue
            seen.add((name.lower(), p))
            pix, parcel, inst, coupon = extras(d, p)
            out.append(dict(title=name, price=pix or p, pix=pix, parcel=parcel, inst=inst,
                            coupon=coupon, image=image_of(d), url=full, direct=direct))
    return out


STORES = {
    "Kabum": lambda: from_html("https://www.kabum.com.br/busca/ps5-slim"),
    "Buscapé": lambda: from_html("https://www.buscape.com.br/search?q=ps5+slim"),
    "Zoom": lambda: from_html("https://www.zoom.com.br/search?q=ps5+slim"),
    "Magalu": lambda: from_html("https://www.magazineluiza.com.br/busca/ps5+slim/"),
    "Americanas": lambda: from_html("https://www.americanas.com.br/busca/ps5-slim"),
    "Mercado Livre": lambda: from_html("https://lista.mercadolivre.com.br/ps5-slim"),
    "Carrefour": lambda: from_html("https://www.carrefour.com.br/busca/ps5-slim"),
    "Shopee": lambda: from_html("https://shopee.com.br/search?keyword=ps5%20slim"),
}


def is_console(title):
    t = title.lower()
    return bool(re.search(r"ps ?5|playstation ?5", t)) and not any(w in t for w in BAD)


def variant(title):
    t = title.lower()
    if "digital" in t:
        return "digital"
    return "com jogos" if re.search(GAMES, t) else "só o console"


def build_msg(it, store, record, p, flash=None, suspect=False):
    L = []
    if suspect:
        L.append("⚠️ PREÇO SUSPEITO: bem abaixo do mercado. Pode ser usado, peça solta ou golpe. "
                 "Confira vendedor, reputação e avaliações antes de pagar.")
    else:
        if flash:
            L.append(flash)
        if record:
            L.append("🏆 MENOR PREÇO até agora (%s)" % variant(it["title"]))
        if p <= BUY_PRICE:
            L.append("✅ ABAIXO DA META (%s)" % brl(BUY_PRICE))
        else:
            L.append("📉 Ainda %s acima da meta (%s)" % (brl(p - BUY_PRICE), brl(BUY_PRICE)))
    L.append(it["title"])
    if it.get("pix"):
        L.append("💰 Pix/à vista: " + brl(it["pix"]))
        if it.get("parcel"):
            L.append("💳 Parcelado: " + brl(it["parcel"]) + (" (%s)" % it["inst"] if it.get("inst") else ""))
    else:
        L.append("💰 Preço: " + brl(it["price"]) + (" (%s)" % it["inst"] if it.get("inst") else ""))
    L.append("🎟️ Cupom: " + (it["coupon"] if it.get("coupon") else "não detectado (confira no site)"))
    L.append("🏪 " + store)
    if store in MARKETPLACES and not suspect:
        L.append("⚠️ Marketplace: confira se é loja oficial e a reputação do vendedor.")
    if not it.get("direct", True):
        L.append("🔎 Link da BUSCA (não achei o link direto do produto):")
    L.append(it["url"])
    return "\n".join(L)


def tg(method, params):
    data = urllib.parse.urlencode(params).encode()
    return urllib.request.urlopen("https://api.telegram.org/bot%s/%s" % (TOKEN, method), data, timeout=25)


def telegram(text, photo=None, url=None, label="🛒 Ver oferta"):
    if not TOKEN or not CHAT_ID:
        print("[sem Telegram]", text)
        return
    extra = {}
    if url:
        extra["reply_markup"] = json.dumps({"inline_keyboard": [[{"text": label, "url": url}]]})
    if photo:
        try:
            tg("sendPhoto", dict(chat_id=CHAT_ID, photo=photo, caption=text[:1000], **extra))
            return
        except Exception as e:
            print("foto falhou:", e)
    tg("sendMessage", dict(chat_id=CHAT_ID, text=text, **extra))


def main():
    try:
        state = json.load(open(STATE_FILE))
    except Exception:
        state = {}
    best = state.setdefault("best", {})
    seen = state.setdefault("seen", {})
    report, found, ok_stores = [], [], 0
    for store, fn in STORES.items():
        try:
            items = fn()
        except Exception as e:
            report.append("%s: ERRO (%s)" % (store, str(e)[:60]))
            continue
        ok_stores += 1
        cons = [it for it in items if it.get("price") and it["price"] >= MIN_PRICE and is_console(it["title"])]
        low = (" (menor: %s)" % brl(min(i["price"] for i in cons))) if cons else ""
        report.append("%s: %d itens, %d consoles%s" % (store, len(items), len(cons), low))
        found += [(store, it) for it in cons]

    prices = sorted({round(it["price"]) for _, it in found})
    med = statistics.median(prices) if len(prices) >= 4 else None  # referência de mercado
    alerts, samples, done, updated, n_susp = [], [], set(), set(), 0
    for store, it in sorted(found, key=lambda x: x[1]["price"]):
        p, v = it["price"], variant(it["title"])
        suspect = med is not None and p < 0.80 * med
        tkey = re.sub(r"\W+", "", it["title"].lower())[:80]
        key = (tkey, round(p))
        if key in done:  # mesma oferta repetida em outra loja/comparador
            continue
        done.add(key)
        record = (not suspect) and p < best.get(v, 1e12) - 0.5
        last = seen.get(tkey)
        changed = last is None or p < last * 0.99
        flash = None
        if suspect:
            flash = None
        elif p <= BUY_PRICE and changed:
            flash = "🚨 HORA DE COMPRAR! Abaixo da sua meta"
        elif last is not None and p <= last * 0.92:
            flash = "🚨 PROMOÇÃO RELÂMPAGO! Caiu %d%% (era %s)" % (round((1 - p / last) * 100), brl(last))
        if record:
            best[v] = p
        if tkey not in updated:
            seen[tkey] = p
            updated.add(tkey)
        m = dict(text=build_msg(it, store, record, p, flash, suspect), img=it.get("image"), url=it["url"],
                 label="🛒 Ver oferta" if it.get("direct", True) else "🔎 Ver busca",
                 flash=bool(flash), it=it, store=store, p=p)
        if suspect:
            n_susp += 1
        elif len(samples) < 3:
            samples.append(m)
        if p <= ALERT_MAX and (record or changed):
            alerts.append(m)
    alerts.sort(key=lambda m: not m["flash"])
    print("\n".join(report))
    for m in alerts[:5]:
        telegram(m["text"], m["img"], m["url"], m["label"])

    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=-3)))  # horário de Brasília
    today = now.strftime("%Y-%m-%d")
    if TEST or (now.hour >= 9 and state.get("summary_day") != today):
        L = ["☀️ Resumo do dia %s: o bot está ativo (%d/%d lojas lendo)" % (now.strftime("%d/%m"), ok_stores, len(STORES))]
        if samples:
            L.append("Menores preços agora:")
            for i, m in enumerate(samples, 1):
                L.append("%d) %s - %s (%s)" % (i, brl(m["p"]), m["it"]["title"][:70], m["store"]))
        else:
            L.append("⚠️ Não achei nenhum console hoje. As lojas podem estar bloqueando.")
        if n_susp:
            L.append("⚠️ %d oferta(s) com preço suspeito ignorada(s) nos recordes." % n_susp)
        if best:
            L.append("Melhor já visto: " + ", ".join("%s %s" % (k, brl(x)) for k, x in best.items()))
        top = samples[0] if samples else None
        telegram("\n".join(L), top["img"] if top else None, top["url"] if top else None,
                 top["label"] if top else "🛒 Ver oferta")
        if not TEST:
            state["summary_day"] = today

    if TEST:
        telegram("✅ Teste do monitor\n" + "\n".join(report))
        for m in samples:
            telegram("🧪 EXEMPLO de aviso\n" + m["text"], m["img"], m["url"], m["label"])
    json.dump(state, open(STATE_FILE, "w"))


if __name__ == "__main__":
    main()
