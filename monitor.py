"""Monitor de preço PS5 Slim -> Telegram (com foto). Só usa a biblioteca padrão."""
import json, os, re, urllib.request, urllib.parse

TOKEN = re.sub(r"[^A-Za-z0-9:_-]", "", os.environ.get("TELEGRAM_TOKEN", ""))
CHAT_ID = re.sub(r"[^0-9-]", "", os.environ.get("TELEGRAM_CHAT_ID", ""))
BUY_PRICE = float(os.environ.get("MAX_PRICE", "3750"))      # meta de compra
ALERT_MAX = float(os.environ.get("ALERT_MAX", "4199.99"))   # só avisa até esse valor
MIN_PRICE = 2000  # ignora acessórios e jogos
TEST = os.environ.get("TEST", "") not in ("", "0")
STATE_FILE = "state.json"

BAD = ["digital", "controle", "dualsense", "capa ", "suporte", "headset", "pulse",
       "cabo", "carregador", "ps4", "playstation 4", "ps vita", "portal", "base "]
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
            link = d.get("url") or d.get("link") or d.get("permalink") or d.get("path") or url
            if not isinstance(link, str) or (name.lower(), p) in seen:
                continue
            seen.add((name.lower(), p))
            pix, parcel, inst, coupon = extras(d, p)
            out.append(dict(title=name, price=pix or p, pix=pix, parcel=parcel, inst=inst,
                            coupon=coupon, image=image_of(d), url=urllib.parse.urljoin(url, link)))
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
    return "com jogos" if re.search(GAMES, title.lower()) else "só o console"


def build_msg(it, store, record, p):
    L = []
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
    L.append(it["url"])
    return "\n".join(L)


def tg(method, params):
    data = urllib.parse.urlencode(params).encode()
    return urllib.request.urlopen("https://api.telegram.org/bot%s/%s" % (TOKEN, method), data, timeout=25)


def telegram(text, photo=None):
    if not TOKEN or not CHAT_ID:
        print("[sem Telegram]", text)
        return
    if photo:
        try:
            tg("sendPhoto", {"chat_id": CHAT_ID, "photo": photo, "caption": text[:1000]})
            return
        except Exception as e:
            print("foto falhou:", e)
    tg("sendMessage", {"chat_id": CHAT_ID, "text": text})


def main():
    try:
        state = json.load(open(STATE_FILE))
    except Exception:
        state = {}
    best = state.setdefault("best", {})
    seen = state.setdefault("seen", {})
    report, found = [], []
    for store, fn in STORES.items():
        try:
            items = fn()
        except Exception as e:
            report.append("%s: ERRO (%s)" % (store, str(e)[:60]))
            continue
        cons = [it for it in items if it.get("price") and it["price"] >= MIN_PRICE and is_console(it["title"])]
        low = (" (menor: %s)" % brl(min(i["price"] for i in cons))) if cons else ""
        report.append("%s: %d itens, %d consoles%s" % (store, len(items), len(cons), low))
        found += [(store, it) for it in cons]

    alerts = []
    for store, it in sorted(found, key=lambda x: x[1]["price"]):
        p, v, u = it["price"], variant(it["title"]), it["url"]
        record = p < best.get(v, 1e12) - 0.5
        last = seen.get(u)
        changed = last is None or p < last * 0.99
        if record:
            best[v] = p
        seen[u] = p
        if p <= ALERT_MAX and (record or changed):
            alerts.append((build_msg(it, store, record, p), it.get("image")))
    print("\n".join(report))
    for text, img in alerts[:5]:
        telegram(text, img)
    if TEST:
        telegram("✅ Teste do monitor\n" + "\n".join(report) +
                 "\nMelhores até agora: " + (", ".join("%s %s" % (k, brl(x)) for k, x in best.items()) or "nenhum"))
    json.dump(state, open(STATE_FILE, "w"))


if __name__ == "__main__":
    main()
