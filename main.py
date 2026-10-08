import os
import re
import json
import html
import hashlib
import base64
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from urllib.parse import quote
import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps
from google import genai

PAGE_ID = "1128027710403407"
GRAPH_VERSION = os.getenv("FACEBOOK_GRAPH_VERSION", "v26.0")
GEMINI_MODEL = os.getenv("GEMINI_TEXT_MODEL", "gemini-3.5-flash")
GEMINI_IMAGE_MODEL = os.getenv("GEMINI_IMAGE_MODEL", "gemini-3.1-flash-lite-image")
IMAGE_TIMEOUT_SECONDS = int(os.getenv("GEMINI_IMAGE_TIMEOUT_SECONDS", "35"))
TEXT_TIMEOUT_SECONDS = int(os.getenv("GEMINI_TEXT_TIMEOUT_SECONDS", "25"))
RSS_TIMEOUT_SECONDS = int(os.getenv("RSS_TIMEOUT_SECONDS", "12"))
HISTORY_FILE = "posted_news.json"
IMAGE_FILE = "news_image.jpg"
BACKGROUND_FILES = [
    "news_background.jpg",
    "news_background.png",
    "background.jpg",
    "background.png",
    "assets/news_background.jpg",
    "assets/news_background.png",
]
MAX_HISTORY = 2000
MAX_AGE_HOURS = 48
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
FACEBOOK_TOKEN = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN")

if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY is missing")
if not FACEBOOK_TOKEN:
    raise RuntimeError("FACEBOOK_PAGE_ACCESS_TOKEN is missing")

client = genai.Client(api_key=GEMINI_API_KEY)
http = requests.Session()
http.headers.update(
    {"User-Agent": "ASO-NEWS-Bot/2.0", "Accept-Language": "en-US,en;q=0.9"}
)

SOURCES = [
    (
        "Rudaw",
        72,
        "site:rudaw.net Kurdistan OR Iraq OR Erbil OR Sulaymaniyah OR Duhok OR Kirkuk",
    ),
    (
        "Kurdistan24",
        71,
        "site:kurdistan24.net Kurdistan OR Iraq OR Erbil OR Sulaymaniyah OR Duhok OR Kirkuk",
    ),
    (
        "NRT",
        70,
        "site:nrt-news.com Kurdistan OR Iraq OR Erbil OR Sulaymaniyah OR Duhok OR Kirkuk",
    ),
    (
        "BasNews",
        69,
        "site:basnews.com Kurdistan OR Iraq OR Erbil OR Sulaymaniyah OR Duhok OR Kirkuk",
    ),
    (
        "Shafaq News",
        68,
        "site:shafaq.com Iraq OR Kurdistan OR Erbil OR Baghdad OR Kirkuk OR Mosul",
    ),
    (
        "Iraqi News",
        63,
        "site:iraqinews.com Iraq OR Kurdistan OR Baghdad OR Erbil OR Mosul",
    ),
    ("KRG", 62, "site:gov.krd Kurdistan OR Erbil OR KRG OR government"),
    (
        "Iraqi News Agency",
        60,
        "site:ina.iq Iraq OR Kurdistan OR Baghdad OR Erbil OR Kirkuk",
    ),
    (
        "Alsumaria",
        57,
        "site:alsumaria.tv Iraq OR Kurdistan OR Baghdad OR Erbil OR Mosul",
    ),
    ("Reuters", 48, "site:reuters.com Iraq OR Kurdistan OR Baghdad OR Erbil OR Kirkuk"),
    ("AP News", 46, "site:apnews.com Iraq OR Kurdistan OR Baghdad OR Erbil OR Mosul"),
    ("BBC", 40, "site:bbc.com/news Iraq OR Kurdistan OR Baghdad OR Erbil"),
]

KURDISTAN = [
    "kurdistan",
    "kurdish",
    "erbil",
    "hawler",
    "sulaymaniyah",
    "sulaimani",
    "duhok",
    "kirkuk",
    "halabja",
    "هەولێر",
    "سلێمانی",
    "دهۆک",
    "کوردستان",
    "کەرکووک",
]
IRAQ = [
    "iraq",
    "baghdad",
    "mosul",
    "basra",
    "najaf",
    "karbala",
    "anbar",
    "nineveh",
    "عێراق",
    "بغداد",
    "مووسڵ",
    "بەصرە",
    "نەجەف",
    "کەربەلا",
]
BREAKING = [
    "breaking",
    "urgent",
    "attack",
    "strike",
    "drone",
    "missile",
    "earthquake",
    "fire",
    "killed",
    "death",
    "crisis",
    "هێرش",
    "تەقینەوە",
    "درۆن",
    "مووشەک",
    "ئاگر",
    "کوژراو",
    "قەیران",
    "فۆری",
]
KURDISH_CHARS = set("ئەپچڕڵێۆیەوەکگژخدرتننمڵۆهسشعغفڤقڕێ")
LATIN_RE = re.compile(r"[A-Za-z]")


def clean(x):
    x = html.unescape(str(x or ""))
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", x)).strip()


def nid(title, link):
    return hashlib.sha256(
        (clean(link) + "|" + clean(title).lower()).encode()
    ).hexdigest()


def load():
    try:
        d = json.load(open(HISTORY_FILE, encoding="utf-8"))
        return [
            x if isinstance(x, str) else x.get("id")
            for x in d
            if isinstance(x, str) or isinstance(x, dict) and x.get("id")
        ]
    except Exception:
        return []


posted = load()


def save():
    json.dump(
        posted[-MAX_HISTORY:],
        open(HISTORY_FILE, "w", encoding="utf-8"),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def rss(q):
    return (
        "https://news.google.com/rss/search?q="
        + quote(q)
        + "&hl=en-US&gl=US&ceid=US:en"
    )


def fetch(name, priority, q):
    try:
        root = ET.fromstring(http.get(rss(q), timeout=RSS_TIMEOUT_SECONDS).content)
    except Exception as e:
        print("RSS error", name, e)
        return []
    now = datetime.now(timezone.utc).timestamp()
    out = []
    for it in root.findall(".//item")[:12]:
        title = clean(it.findtext("title"))
        link = clean(it.findtext("link"))
        summary = clean(it.findtext("description"))
        pub = clean(it.findtext("pubDate"))
        if not title or not link:
            continue
        try:
            age = max(0, (now - parsedate_to_datetime(pub).timestamp()) / 3600)
        except Exception:
            age = 999
        if age > MAX_AGE_HOURS:
            continue
        ident = nid(title, link)
        if ident in posted:
            continue
        t = (title + " " + summary).lower()
        score = (
            priority
            + min(sum(k in t for k in KURDISTAN), 3) * 28
            + min(sum(k in t for k in IRAQ), 3) * 18
            + min(sum(k in t for k in KURDISTAN + IRAQ), 4) * 8
            + min(sum(k in t for k in BREAKING), 3) * 9
        )
        if age < 1:
            score += 24
        elif age < 3:
            score += 18
        elif age < 6:
            score += 12
        elif age < 12:
            score += 7
        elif age < 24:
            score += 3
        out.append(
            {
                "id": ident,
                "title": title,
                "summary": summary,
                "link": link,
                "source": name,
                "score": score,
            }
        )
    return out


def collect():
    a = []
    # Fetch all RSS sources in parallel so one slow source cannot consume minutes.
    with ThreadPoolExecutor(max_workers=min(12, len(SOURCES))) as pool:
        futures = [pool.submit(fetch, *s) for s in SOURCES]
        for future in as_completed(futures):
            try:
                a += future.result()
            except Exception as e:
                print("RSS worker error", e)
    return sorted(
        {x["id"]: x for x in a}.values(), key=lambda x: x["score"], reverse=True
    )[:30]


def is_sorani(text):
    if not text:
        return False
    t = clean(text)
    kur = sum(c in KURDISH_CHARS for c in t)
    latin = len(LATIN_RE.findall(t))
    return kur >= 5 and kur >= latin


def call_gemini_with_retry(func, *args, retries=3, delay=3, **kwargs):
    for attempt in range(retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            err_str = str(e)
            if (
                "503" in err_str
                or "UNAVAILABLE" in err_str
            ):
                if attempt == retries - 1:
                    raise e
                print(
                    f"Gemini service unavailable (503). Retrying in {delay} seconds... (Attempt {attempt+1}/{retries})"
                )
                time.sleep(delay)
                delay *= 2
            else:
                raise e


def edit_news(items):
    block = "\n\n".join(
        f"SOURCE_NUMBER: {i}\nSOURCE: {x['source']}\nTITLE: {x['title']}\nSUMMARY: {x['summary'][:1000]}\nURL: {x['link']}"
        for i, x in enumerate(items, 1)
    )
    prompt = (
        """تۆ دەستکار و نووسەری هەواڵی ASO NEWS ـیت.
هەموو دەقەکە دەبێت بە کوردیی سۆرانیی ڕوون و سروشتی بێت.
هیچ وشەی ئینگلیزی لە TITLE و BODY و FULL_BODY بەکارمەهێنە، تەنانەت ئەگەر سەرچاوەکە ئینگلیزی بێت؛ ناونیشان و ناوەڕۆک وەرگێڕە بۆ سۆرانی.
کوردستان پێش عێراق و عێراق پێش جیهانە. هیچ زانیارییەک زیاد مەکە.
تەنها ئەمانە بگەڕێنەوە:
SOURCE_NUMBER: 1
TITLE: ناونیشانی کوردی سۆرانی، 5 تا 10 وشە
BODY: 2 تا 4 ڕستەی کورت بە کوردی سۆرانی
FULL_BODY: 2 تا 5 پاراگرافی کورت بە کوردی سۆرانی
HASHTAGS: #ASONEWS #کوردستان #عێراق
SOURCE: ناوی سەرچاوە

CANDIDATES:
"""
        + block
    )
    try:
        r = call_gemini_with_retry(
            client.models.generate_content, model=GEMINI_MODEL, contents=prompt, timeout=TEXT_TIMEOUT_SECONDS
        )
        text = (r.text or "").strip()
    except Exception as e:
        print("Gemini error", e)
        return None
    m = re.search(r"SOURCE_NUMBER\s*:\s*(\d+)", text, re.I)
    idx = max(0, min((int(m.group(1)) - 1 if m else 0), len(items) - 1))
    x = items[idx].copy()

    def field(n, d):
        m = re.search(
            rf"^{n}\s*:\s*(.*?)(?=^\w[\w_ ]*\s*:|\Z)", text, re.I | re.M | re.S
        )
        return clean(m.group(1)) if m else d

    x["kur_title"] = field("TITLE", x["title"])
    x["body"] = field("BODY", x["summary"][:500])
    x["full_body"] = field("FULL_BODY", x["body"])
    x["hashtags"] = field("HASHTAGS", "#ASONEWS #کوردستان #عێراق")
    x["source"] = field("SOURCE", x["source"])
    if not is_sorani(x["kur_title"] + " " + x["body"]):
        print("Gemini output was not Sorani; retrying translation")
        try:
            tr = call_gemini_with_retry(
                client.models.generate_content,
                model=GEMINI_MODEL,
                contents=f"""ئەم هەواڵە بە تەواوی وەرگێڕە بۆ کوردی سۆرانی. هیچ ئینگلیزییەک مەهێڵە جگە لە هاشتاکەکان.
TITLE: {x["kur_title"]}
BODY: {x["body"]}
FULL_BODY: {x["full_body"]}
تەنها بەو شێوەیە بگەڕێنەوە:
TITLE: ...
BODY: ...
FULL_BODY: ...""",
                timeout=TEXT_TIMEOUT_SECONDS,
            )
            tt = (tr.text or "").strip()
            x["kur_title"] = field_from_text(tt, "TITLE", x["kur_title"])
            x["body"] = field_from_text(tt, "BODY", x["body"])
            x["full_body"] = field_from_text(tt, "FULL_BODY", x["full_body"])
        except Exception as e:
            print("Kurdish translation error", e)
    return x


def field_from_text(text, name, default):
    m = re.search(
        rf"^{name}\s*:\s*(.*?)(?=^\w[\w_ ]*\s*:|\Z)", text, re.I | re.M | re.S
    )
    return clean(m.group(1)) if m else default


def fallback(items):
    x = items[0]
    title = clean(x.get("title", "")) or "هەواڵێکی تازە"
    summary = clean(x.get("summary", "")) or title
    # The image gets a short version; the first comment gets the longer RSS detail.
    if len(title) > 120:
        title = title[:117].rsplit(" ", 1)[0] + "..."
    short = summary[:360]
    full = summary[:3000]
    return {
        **x,
        "kur_title": title,
        "body": short,
        "full_body": full,
        "hashtags": "#ASONEWS #کوردستان #عێراق",
    }


def find_background():
    for p in BACKGROUND_FILES:
        if os.path.exists(p) and os.path.getsize(p) > 0:
            return p
    return None


def generate_ai_image(news):
    prompt = f"""Create a professional editorial news image for ASO NEWS.
News title: {news["kur_title"]}
News summary: {news["body"]}
Source context: {news.get("summary","")[:900]}

Create a realistic, visually strong scene that directly represents this news topic.
Use relevant people, places, objects, buildings, vehicles or events only when they fit the story.
No captions, no text, no logos, no watermarks and no invented newspaper graphics.
The result is an illustrative AI-generated news visual, not a claim that it is a real photograph.
"""
    try:
        print(f"Generating AI news image with {GEMINI_IMAGE_MODEL} (timeout {IMAGE_TIMEOUT_SECONDS}s)...")
        interaction = call_gemini_with_retry(
            client.interactions.create,
            model=GEMINI_IMAGE_MODEL,
            input=prompt,
            response_format={
                "type": "image",
                "mime_type": "image/jpeg",
                "aspect_ratio": "4:5",
                "image_size": "1K",
            },
            retries=1,
            timeout=IMAGE_TIMEOUT_SECONDS,
        )
        data = getattr(getattr(interaction, "output_image", None), "data", None)
        if data:
            with open("ai_news_image.jpg", "wb") as f:
                f.write(base64.b64decode(data))
            print("AI news image generated:", GEMINI_IMAGE_MODEL)
            return "ai_news_image.jpg"
        for step in getattr(interaction, "steps", []) or []:
            if getattr(step, "type", None) == "model_output":
                for part in getattr(step, "content", []) or []:
                    if (
                        getattr(part, "type", None) == "image"
                        and getattr(part, "data", None)
                    ):
                        with open("ai_news_image.jpg", "wb") as f:
                            f.write(base64.b64decode(part.data))
                        return "ai_news_image.jpg"
    except Exception as e:
        print(f"Gemini image generation failed or timed out after {IMAGE_TIMEOUT_SECONDS}s:", e)
    return None


def fit_image(img, size):
    return ImageOps.fit(
        img.convert("RGB"), size, method=Image.Resampling.LANCZOS, centering=(0.5, 0.5)
    )


def paste_topic_image(base, path):
    if not path or not os.path.exists(path) or os.path.getsize(path) == 0:
        return
    w, h = base.size
    pw, ph = round(w * 0.49), round(h * 0.64)
    panel = fit_image(Image.open(path), (pw, ph))
    mask = Image.new("L", (pw, ph), 0)
    md = ImageDraw.Draw(mask)
    md.polygon(
        [
            (round(pw * 0.18), 0),
            (pw, 0),
            (pw, round(ph * 0.78)),
            (round(pw * 0.76), ph),
            (0, round(ph * 0.82)),
        ],
        fill=255,
    )
    base.paste(panel, (round(w * 0.50), round(h * 0.16)), mask)


def make_image(news):
    bg = find_background()
    original = None
    if bg:
        try:
            original = Image.open(bg).convert("RGB")
        except Exception as e:
            print(f"Error opening background image: {e}")

    if original:
        target_w = 1200
        target_h = round(original.height * target_w / original.width)
        im = original.resize((target_w, target_h), Image.Resampling.LANCZOS)
        print("Using custom background:", bg, im.size)
    else:
        im = Image.new("RGB", (1200, 1352), (245, 247, 249))
        print("Custom background not found or broken; using fallback background")

    topic_path = None
    # AI image generation is intentionally disabled for now so publishing never waits on it.

    d = ImageDraw.Draw(im)
    bold = ImageFont.load_default()
    regular = ImageFont.load_default()
    for p in [
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]:
        if os.path.exists(p):
            if "Bold" in p:
                bold = ImageFont.truetype(p, 58)
            elif "Regular" in p:
                regular = ImageFont.truetype(p, 38)

    title = clean(news["kur_title"])[:150]
    body = clean(news.get("body", ""))[:360]
    max_width = round(im.width * 0.44)

    def wrap(text, font, limit):
        result, line = [], ""
        for word in text.split():
            test = (line + " " + word).strip()
            if d.textbbox((0, 0), test, font=font)[2] <= limit:
                line = test
            else:
                if line:
                    result.append(line)
                line = word
            if len(result) >= 5:
                break
        if line and len(result) < 5:
            result.append(line)
        return result

    title_lines = wrap(title, bold, max_width)[:4]
    body_lines = wrap(body, regular, round(im.width * 0.42))[:4]
    title_h = 64
    body_h = 44
    total_h = len(title_lines) * title_h + len(body_lines) * body_h + 25
    y0 = round(im.height * 0.43 - total_h / 2)
    center_x = round(im.width * 0.25)

    for i, line in enumerate(title_lines):
        d.text(
            (center_x, y0 + i * title_h),
            line,
            font=bold,
            fill=(13, 35, 58),
            anchor="ma",
            align="center",
        )

    by = y0 + len(title_lines) * title_h + 18
    for i, line in enumerate(body_lines):
        d.text(
            (center_x, by + i * body_h),
            line,
            font=regular,
            fill=(35, 45, 55),
            anchor="ma",
            align="center",
        )

    if False and topic_path:
        label = "وێنەی دروستکراوی AI"
        d.rounded_rectangle(
            (
                round(im.width * 0.69),
                round(im.height * 0.80),
                round(im.width * 0.97),
                round(im.height * 0.85),
            ),
            radius=12,
            fill=(13, 29, 48),
        )
        d.text(
            (round(im.width * 0.83), round(im.height * 0.825)),
            label,
            font=regular,
            fill="white",
            anchor="mm",
        )

    im.save(IMAGE_FILE, "JPEG", quality=93, optimize=True)
    return IMAGE_FILE


def publish(message, image):
    try:
        with open(image, "rb") as f:
            r = http.post(
                f"https://graph.facebook.com/{GRAPH_VERSION}/{PAGE_ID}/photos",
                data={"access_token": FACEBOOK_TOKEN, "message": message},
                files={"source": ("news.jpg", f, "image/jpeg")},
                timeout=90,
            )
        print("Facebook status:", r.status_code)
        if r.ok:
            d = r.json()
            return d.get("post_id") or d.get("id")
        print("Facebook error:", r.text[:1000])
    except Exception as e:
        print("Facebook error:", e)
    return None


def first_comment(pid, text):
    targets = (
        [str(pid), f"{PAGE_ID}_{pid}"] if "_" not in str(pid) else [str(pid)]
    )
    for t in targets:
        try:
            r = http.post(
                f"https://graph.facebook.com/{GRAPH_VERSION}/{t}/comments",
                data={"access_token": FACEBOOK_TOKEN, "message": text},
                timeout=40,
            )
            if r.ok:
                return True
        except Exception:
            pass
    return False


def main():
    items = collect()
    print("Candidates:", len(items))
    if not items:
        return
    news = fallback(items)
    message = f"{news['kur_title']}\n\n{news['body']}\n\n{news['hashtags']}"
    extra = f"{news['full_body']}\n\nسەرچاوە: {news['source']}\n{news['link']}"
    pid = publish(message, make_image(news))
    if not pid:
        raise RuntimeError("Facebook post failed")
    first_comment(pid, extra)
    posted.append(news["id"])
    save()
    print("Published:", pid)


if __name__ == "__main__":
    main()
