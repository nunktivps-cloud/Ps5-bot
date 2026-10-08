"""Monitor de preços PS5 (leitor de disco + jogos baratos) -> Telegram. Só usa a biblioteca padrão."""
import datetime, html as htmllib, json, os, re, statistics, unicodedata, urllib.request, urllib.parse

TOKEN = re.sub(r"[^A-Za-z0-9:_-]", "", os.environ.get("TELEGRAM_TOKEN", ""))
CHAT_ID = re.sub(r"[^0-9-]", "", os.environ.get("TELEGRAM_CHAT_ID", ""))
TEST = os.environ.get("TEST", "") not in ("", "0")
STATE_FILE = "state.json"

# ---------- LEITOR DE DISCO (mesmo comportamento do PS5) ----------
DRIVE_BUY = float(os.environ.get("DRIVE_BUY", "325"))      # meta de compra
DRIVE_ALERT = float(os.environ.get("DRIVE_ALERT", "900"))  # só avisa até esse valor
DRIVE_MIN, DRIVE_MAX = 250, 1500                           # faixa de preço válida

# ---------- JOGOS ----------
GAME_MIN, GAME_MAX = 20, 400   # faixa de preço válida
GAME_DROP = 0.30               # avisa se o jogo cair 30% ou mais...
GAME_DROP_MAX = 300            # ...e o preço novo for até esse valor
TOP_GAMES = 10                 # quantos jogos no resumo diário
# Lista de desejos (opcional): "nome do jogo": preço-alvo. Exemplo:
# WISHLIST = {"god of war ragnarok": 150, "spider-man 2": 180}
WISHLIST = {}

SHOW_SUSPECT = True  # True = também avisa preços suspeitos (leitor de disco)
MARKETPLACES = ("Mercado Livre", "Shopee")
# marcas de produto importado: "consola" (português de Portugal), título traduzido por máquina, etc.
IMPORT_RE = re.compile(r"consola|importad|internacional|\b(branco|branca)\s+(branco|branca)\b", re.I)
IMPORT_URL_RE = re.compile(r"MLBU|/up/", re.I)
IMPORT_KEY_RE = re.compile(r"internationa|cross.?border|cbt", re.I)

BAD_DRIVE = ["console", "controle", "dualsense", "capa", "case", "placa", "faceplate", "suporte", "cabo",
             "bundle", "pacote", "1tb", "825", "externo", "usb", "notebook", "xbox", "ps4", "jogo", "headset",
             "usado", "seminovo", "recondicionado", "defeito", "sucata", "peças"]
BAD_GAME = ["console", "controle", "dualsense", "headset", "fone", "capa", "case", "suporte", "cabo", "carregador",
            "base ", "volante", "gift", "cartão", "cartao", "psn", "plus", "assinatura", "código", "codigo",
            "digital", "chave", "conta ", "primária", "primaria", "secundária", "secundaria", "usado", "seminovo",
            "recondicionado", "ps4", "playstation 4", "kit", "bundle", "pacote", "leitor", "portal", "pulse",
            "mouse", "teclado", "película", "pelicula", "skin", "adesivo", "defeito", "sucata"]

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


def is_import(d):
    for k, v in deep_items(d, 3):
        if IMPORT_KEY_RE.search(str(k)) and v not in (False, None, "", "false", "False", 0):
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
            if is_used(d) or is_import(d):
                continue
            link = find_link(d)
            full = urllib.parse.urljoin(url, link or url)
            if IMPORT_URL_RE.search(full):  # anúncio internacional (MLBU)
                continue
            direct = bool(link) and full.rstrip("/") != url.rstrip("/")
            if (name.lower(), p) in seen:
                continue
            seen.add((name.lower(), p))
            pix, parcel, inst, coupon = extras(d, p)
            out.append(dict(title=name, price=pix or p, pix=pix, parcel=parcel, inst=inst,
                            coupon=coupon, image=image_of(d), url=full, direct=direct))
    return out


def slug(q):
    q = unicodedata.normalize("NFKD", q).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", q).strip("-")


SITES = {
    "Kabum": lambda q: "https://www.kabum.com.br/busca/" + slug(q),
    "Buscapé": lambda q: "https://www.buscape.com.br/search?q=" + urllib.parse.quote_plus(q),
    "Zoom": lambda q: "https://www.zoom.com.br/search?q=" + urllib.parse.quote_plus(q),
    "Mercado Livre": lambda q: "https://lista.mercadolivre.com.br/" + slug(q),
}


def is_ps5(t):
    return bool(re.search(r"ps ?5|playstation ?5", t)) and not IMPORT_RE.search(t)


def drive_ok(it):
    t = it["title"].lower()
    return (DRIVE_MIN <= it["price"] <= DRIVE_MAX and is_ps5(t) and re.search(r"leitor|drive|unidade", t)
            and not any(w in t for w in BAD_DRIVE))


def game_ok(it):
    t = it["title"].lower()
    return GAME_MIN <= it["price"] <= GAME_MAX and is_ps5(t) and not any(w in t for w in BAD_GAME)


def scan(tag, stores, queries, valid, report, stat):
    """Busca em cada loja; devolve [(loja, item)] válidos e anota o relatório."""
    found = []
    for store in stores:
        n_items, ok_items, errs = 0, [], 0
        for q in queries:
            try:
                items = from_html(SITES[store](q))
            except Exception as e:
                errs += 1
                last_err = str(e)[:50]
                continue
            n_items += len(items)
            ok_items += [it for it in items if it.get("price") and valid(it)]
        stat[1] += 1
        if errs == len(queries):
            report.append("%s - %s: ERRO (%s)" % (tag, store, last_err))
            continue
        stat[0] += 1
        low = (" (menor: %s)" % brl(min(i["price"] for i in ok_items))) if ok_items else ""
        report.append("%s - %s: %d itens, %d válidos%s" % (tag, store, n_items, len(ok_items), low))
        found += [(store, it) for it in ok_items]
    return found


def build_msg(it, store, p, tag, buy, record=False, flash=None, suspect=False):
    L = []
    if suspect:
        L.append("⚠️ PREÇO SUSPEITO: bem abaixo do mercado. Pode ser usado, peça solta ou golpe. "
                 "Confira vendedor, reputação e avaliações antes de pagar.")
    else:
        if flash:
            L.append(flash)
        if record:
            L.append("🏆 MENOR PREÇO até agora (%s)" % tag)
        if p <= buy:
            L.append("✅ ABAIXO DA META (%s)" % brl(buy))
        else:
            L.append("📉 Ainda %s acima da meta (%s)" % (brl(p - buy), brl(buy)))
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


def game_msg(it, store, head):
    L = [head, it["title"], "💰 Preço: " + brl(it["price"]), "🏪 " + store]
    if store in MARKETPLACES:
        L.append("⚠️ Marketplace: confira se é loja oficial e a reputação do vendedor.")
    if not it.get("direct", True):
        L.append("🔎 Link da BUSCA (não achei o link direto do produto):")
    L.append(it["url"])
    return "\n".join(L)


def tg(method, params):
    data = urllib.parse.urlencode(params).encode()
    return urllib.request.urlopen("https://api.telegram.org/bot%s/%s" % (TOKEN, method), data, timeout=25)


def telegram(text, photo=None, url=None, label="🛒 Ver oferta", rich=False):
    if not TOKEN or not CHAT_ID:
        print("[sem Telegram]", text)
        return
    if rich:  # texto com links clicáveis (HTML)
        tg("sendMessage", dict(chat_id=CHAT_ID, text=text, parse_mode="HTML", disable_web_page_preview="true"))
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


def tkey_of(title):
    return re.sub(r"\W+", "", title.lower())[:80]


def a(it, text):
    return '<a href="%s">%s</a>' % (htmllib.escape(it["url"], quote=True), htmllib.escape(text))


def handle_drive(found, st, buy, alert_max):
    """Mesmo comportamento do PS5: meta, relâmpago, recorde, suspeito."""
    seen = st.setdefault("seen", {})
    prices = sorted({round(it["price"]) for _, it in found})
    med = statistics.median(prices) if len(prices) >= 4 else None
    alerts, top, done, updated, n_susp = [], [], set(), set(), 0
    for store, it in sorted(found, key=lambda x: x[1]["price"]):
        p = it["price"]
        suspect = med is not None and p < 0.80 * med
        tkey = tkey_of(it["title"])
        if (tkey, round(p)) in done:
            continue
        done.add((tkey, round(p)))
        if suspect and not SHOW_SUSPECT:
            n_susp += 1
            continue
        best = st.get("best")
        record = (not suspect) and (best is None or p < best - 0.5)
        last = seen.get(tkey)
        changed = last is None or p < last * 0.99
        flash = None
        if suspect:
            flash = None
        elif p <= buy and changed:
            flash = "🚨 HORA DE COMPRAR! Abaixo da sua meta"
        elif last is not None and p <= last * 0.92:
            flash = "🚨 PROMOÇÃO RELÂMPAGO! Caiu %d%% (era %s)" % (round((1 - p / last) * 100), brl(last))
        if record:
            st["best"] = p
        if tkey not in updated:
            seen[tkey] = p
            updated.add(tkey)
        m = dict(text=build_msg(it, store, p, "leitor de disco", buy, record, flash, suspect), img=it.get("image"),
                 url=it["url"], label="🛒 Ver oferta" if it.get("direct", True) else "🔎 Ver busca",
                 flash=bool(flash), it=it, store=store, p=p)
        if not suspect:
            top.append(m)
        if p <= alert_max and (record or changed):
            alerts.append(m)
    alerts.sort(key=lambda m: not m["flash"])
    return alerts, top[:3], n_susp


def handle_games(found, st):
    """Queda grande de preço + lista dos mais baratos do dia."""
    seen = st.setdefault("seen", {})
    low = st.setdefault("low", {})
    uniq, done = [], set()
    for store, it in sorted(found, key=lambda x: x[1]["price"]):
        tkey = tkey_of(it["title"])
        if tkey in done:
            continue
        done.add(tkey)
        uniq.append((store, it, tkey))
    alerts = []
    for store, it, tkey in uniq:
        p = it["price"]
        last = seen.get(tkey)
        if last is not None and p <= last * (1 - GAME_DROP) and p <= GAME_DROP_MAX:
            head = "💥 JOGO DESPENCOU! Caiu %d%% (era %s)" % (round((1 - p / last) * 100), brl(last))
            if p <= low.get(tkey, p):
                head += "\n🏆 Menor preço que já vi desse jogo"
            alerts.append(dict(text=game_msg(it, store, head), img=it.get("image"), url=it["url"],
                               label="🛒 Ver oferta" if it.get("direct", True) else "🔎 Ver busca"))
        seen[tkey] = p
        low[tkey] = min(p, low.get(tkey, p))
    for d in (seen, low):  # limita o tamanho do histórico
        while len(d) > 2000:
            d.pop(next(iter(d)))
    return alerts, uniq


def handle_wishlist(st):
    alerts = []
    for title, target in WISHLIST.items():
        words = [w for w in slug(title).split("-") if len(w) > 2]
        found = scan("Desejo", ["Buscapé", "Kabum"], [title + " ps5"],
                     lambda it: game_ok(it) and all(w in slug(it["title"]) for w in words), [], [0, 0])
        if not found:
            continue
        store, it = min(found, key=lambda x: x[1]["price"])
        p, key = it["price"], slug(title)
        if p <= target and (key not in st or p < st[key] * 0.99):
            st[key] = p
            alerts.append(dict(text=game_msg(it, store, "⭐ DA SUA LISTA! Abaixo de %s" % brl(target)),
                               img=it.get("image"), url=it["url"],
                               label="🛒 Ver oferta" if it.get("direct", True) else "🔎 Ver busca"))
    return alerts


def main():
    try:
        state = json.load(open(STATE_FILE))
    except Exception:
        state = {}
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=-3)))  # horário de Brasília
    today = now.strftime("%Y-%m-%d")
    slot = "%s-%d-%d" % (today, now.hour, now.minute // 30)
    digest_due = now.hour >= 9 and state.get("summary_day") != today
    report, stat = [], [0, 0]

    # ---- leitor de disco: a cada execução ----
    drive_found = scan("Leitor", ["Kabum", "Buscapé", "Zoom", "Mercado Livre"], ["leitor de disco ps5"],
                       drive_ok, report, stat)
    d_alerts, d_top, n_susp = handle_drive(drive_found, state.setdefault("drive", {}), DRIVE_BUY, DRIVE_ALERT)

    # ---- jogos: a cada 30 min (ou quando o resumo do dia está pendente) ----
    g_alerts, g_uniq, w_alerts = [], [], []
    if TEST or digest_due or state.get("games_slot") != slot:
        state["games_slot"] = slot
        games_found = scan("Jogos", ["Buscapé", "Kabum"], ["jogo ps5", "jogos ps5 midia fisica"],
                           game_ok, report, stat)
        g_alerts, g_uniq = handle_games(games_found, state.setdefault("games", {}))
        if WISHLIST:
            w_alerts = handle_wishlist(state.setdefault("wish", {}))

    print("\n".join(report))
    for m in w_alerts + g_alerts + d_alerts[:5]:
        telegram(m["text"], m["img"], m["url"], m["label"])

    if TEST or digest_due:
        L = ["☀️ <b>Resumo do dia %s</b> (%d/%d lojas lendo)" % (now.strftime("%d/%m"), stat[0], stat[1]), "",
             "🔌 <b>Leitor de disco</b> - menores agora:"]
        if d_top:
            for i, m in enumerate(d_top, 1):
                L.append("%d) %s - %s (%s)" % (i, brl(m["p"]), a(m["it"], m["it"]["title"][:60]), m["store"]))
        else:
            L.append("Nenhuma oferta válida agora.")
        best = state["drive"].get("best")
        if best:
            L.append("🏆 Melhor já visto: %s (meta %s)" % (brl(best), brl(DRIVE_BUY)))
        if n_susp:
            L.append("⚠️ %d oferta(s) suspeita(s) ignorada(s)." % n_susp)
        L += ["", "🎮 <b>Jogos mais baratos hoje:</b>"]
        if g_uniq:
            for i, (store, it, _) in enumerate(g_uniq[:TOP_GAMES], 1):
                L.append("%d) %s - %s (%s)" % (i, brl(it["price"]), a(it, it["title"][:55]), store))
        else:
            L.append("Nenhum jogo encontrado (as lojas podem estar bloqueando).")
        telegram("\n".join(L), rich=True)
        if not TEST:
            state["summary_day"] = today

    if TEST:
        telegram("✅ Teste do monitor\n" + "\n".join(report))
        for m in d_top:
            telegram("🧪 EXEMPLO de aviso (leitor)\n" + m["text"], m["img"], m["url"], m["label"])
        for m in (g_alerts[:1] or []):
            telegram("🧪 EXEMPLO de aviso (jogo)\n" + m["text"], m["img"], m["url"], m["label"])
    json.dump(state, open(STATE_FILE, "w"))


if __name__ == "__main__":
    main()
