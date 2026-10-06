import os
import re
import json
import html
import hashlib
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from urllib.parse import quote

import requests
from PIL import Image, ImageDraw, ImageFont
from google import genai

PAGE_ID = "1128027710403407"
GRAPH_VERSION = os.getenv("FACEBOOK_GRAPH_VERSION", "v26.0")
GEMINI_MODEL = os.getenv("GEMINI_TEXT_MODEL", "gemini-3.5-flash")
HISTORY_FILE = "posted_news.json"
IMAGE_FILE = "news_image.jpg"
MAX_HISTORY = 2000
MAX_AGE_HOURS = 48
MAX_CANDIDATES = 30
MIN_SCORE = 8

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
FACEBOOK_TOKEN = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN")
if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY is missing")
if not FACEBOOK_TOKEN:
    raise RuntimeError("FACEBOOK_PAGE_ACCESS_TOKEN is missing")

client = genai.Client(api_key=GEMINI_API_KEY)
http = requests.Session()
http.headers.update({"User-Agent": "ASO-NEWS-Bot/2.0", "Accept-Language": "en-US,en;q=0.9"})

SOURCES = [
("Rudaw",72,"site:rudaw.net Kurdistan OR Iraq OR Erbil OR Sulaymaniyah OR Duhok OR Kirkuk"),
("Kurdistan24",71,"site:kurdistan24.net Kurdistan OR Iraq OR Erbil OR Sulaymaniyah OR Duhok OR Kirkuk"),
("NRT",70,"site:nrt-news.com Kurdistan OR Iraq OR Erbil OR Sulaymaniyah OR Duhok OR Kirkuk"),
("BasNews",69,"site:basnews.com Kurdistan OR Iraq OR Erbil OR Sulaymaniyah OR Duhok OR Kirkuk"),
("Shafaq News",68,"site:shafaq.com Iraq OR Kurdistan OR Erbil OR Baghdad OR Kirkuk OR Mosul"),
("Iraqi News",63,"site:iraqinews.com Iraq OR Kurdistan OR Baghdad OR Erbil OR Mosul"),
("KRG",62,"site:gov.krd Kurdistan OR Erbil OR KRG OR government"),
("Iraqi News Agency",60,"site:ina.iq Iraq OR Kurdistan OR Baghdad OR Erbil OR Kirkuk"),
("Alsumaria",57,"site:alsumaria.tv Iraq OR Kurdistan OR Baghdad OR Erbil OR Mosul"),
("Reuters",48,"site:reuters.com Iraq OR Kurdistan OR Baghdad OR Erbil OR Kirkuk"),
("AP News",46,"site:apnews.com Iraq OR Kurdistan OR Baghdad OR Erbil OR Mosul"),
("BBC",40,"site:bbc.com/news Iraq OR Kurdistan OR Baghdad OR Erbil"),
]

KURDISTAN = ["kurdistan","kurdish","erbil","hawler","sulaymaniyah","sulaimani","duhok","kirkuk","halabja","هەولێر","سلێمانی","دهۆک","کوردستان","کەرکووک"]
IRAQ = ["iraq","baghdad","mosul","basra","najaf","karbala","anbar","nineveh","عێراق","بغداد","مووسڵ","بەصرە","نەجەف","کەربەلا"]
LOCAL = KURDISTAN + IRAQ
BREAKING = ["breaking","urgent","attack","strike","drone","missile","earthquake","fire","killed","death","crisis","هێرش","تەقینەوە","درۆن","مووشەک","ئاگر","کوژراو","قەیران","فۆری"]

def clean(x):
    x = html.unescape(str(x or ""))
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", x)).strip()

def make_id(title, link):
    return hashlib.sha256((clean(link) + "|" + clean(title).lower()).encode()).hexdigest()

def load_history():
    try:
        data = json.load(open(HISTORY_FILE, encoding="utf-8"))
        return [x if isinstance(x,str) else x.get("id") for x in data if isinstance(x,str) or isinstance(x,dict) and x.get("id")]
    except Exception:
        return []

def save_history():
    with open(HISTORY_FILE,"w",encoding="utf-8") as f:
        json.dump(posted[-MAX_HISTORY:],f,ensure_ascii=False,separators=(",",":"))

posted = load_history()

def rss(query):
    return "https://news.google.com/rss/search?q=" + quote(query) + "&hl=en-US&gl=US&ceid=US:en"

def fetch(name, priority, query):
    try:
        root = ET.fromstring(http.get(rss(query),timeout=30).content)
    except Exception as e:
        print("RSS error",name,e)
        return []
    now = datetime.now(timezone.utc).timestamp()
    out=[]
    for item in root.findall(".//item")[:12]:
        title=clean(item.findtext("title")); link=clean(item.findtext("link")); summary=clean(item.findtext("description")); pub=clean(item.findtext("pubDate"))
        if not title or not link: continue
        try: age=max(0,(now-parsedate_to_datetime(pub).timestamp())/3600)
        except Exception: age=999
        if age>MAX_AGE_HOURS: continue
        ident=make_id(title,link)
        if ident in posted: continue
        text=(title+" "+summary).lower()
        score=priority+min(sum(k in text for k in KURDISTAN),3)*28+min(sum(k in text for k in IRAQ),3)*18+min(sum(k in text for k in LOCAL),4)*8+min(sum(k in text for k in BREAKING),3)*9
        if age<1: score+=24
        elif age<3: score+=18
        elif age<6: score+=12
        elif age<12: score+=7
        elif age<24: score+=3
        out.append({"id":ident,"title":title,"summary":summary,"link":link,"source":name,"score":score})
    return out

def collect():
    items=[]
    for source in SOURCES: items += fetch(*source)
    unique={x["id"]:x for x in items}
    return sorted(unique.values(),key=lambda x:x["score"],reverse=True)[:MAX_CANDIDATES]

def ai_edit(items):
    candidates="\n\n".join(f"SOURCE_NUMBER: {i}\nSOURCE: {x['source']}\nTITLE: {x['title']}\nSUMMARY: {x['summary'][:1000]}\nURL: {x['link']}" for i,x in enumerate(items,1))
    prompt="""تۆ دەستکار و نووسەری هەواڵی ASO NEWS ـیت. تەنها یەک هەواڵ هەڵبژێرە. هەواڵی کوردستان پێش عێراق، و عێراق پێش جیهان. هیچ زانیارییەک زیاد مەکە.
تەنها ئەم شێوازەی خوارەوە بگەڕێنەوە:
SOURCE_NUMBER: 1
TITLE: ناونیشانی کوردی سۆرانی، 4 تا 8 وشە
BODY: 2 تا 4 ڕستەی کورت
FULL_BODY: 2 تا 5 پاراگرافی کورت
HASHTAGS: #ASONEWS #کوردستان #عێراق
SOURCE: ناوی سەرچاوە

CANDIDATES:
""" + candidates
    try:
        r=client.models.generate_content(model=GEMINI_MODEL,contents=prompt)
        text=(r.text or "").strip()
    except Exception as e:
        print("Gemini error",e); return None
    m=re.search(r"SOURCE_NUMBER\s*:\s*(\d+)",text,re.I); idx=max(0,min((int(m.group(1))-1 if m else 0),len(items)-1))
    chosen=items[idx].copy()
    def field(name,default):
        m=re.search(rf"^{name}\s*:\s*(.*?)(?=^\w[\w_ ]*\s*:|\Z)",text,re.I|re.M|re.S)
        return clean(m.group(1)) if m else default
    chosen["kur_title"]=field("TITLE",chosen["title"])
    chosen["body"]=field("BODY",chosen["summary"][:500])
    chosen["full_body"]=field("FULL_BODY",chosen["body"])
    chosen["hashtags"]=field("HASHTAGS","#ASONEWS #کوردستان #عێراق")
    return chosen

def fallback(items):
    x=items[0]
    return {**x,"kur_title":x["title"],"body":x["summary"][:500],"full_body":x["summary"][:1000],"hashtags":"#ASONEWS #کوردستان #عێراق"}

def font(size,bold=True):
    paths=["/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf" if bold else "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf","/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
    for p in paths:
        if os.path.exists(p): return ImageFont.truetype(p,size)
    return ImageFont.load_default()

def make_image(news):
    w,h=1200,675
    im=Image.new("RGB",(w,h),(18,30,45)); d=ImageDraw.Draw(im)
    for y in range(h):
        v=int(18+25*y/h); d.line((0,y,w,y),fill=(v,v+8,v+18))
    d.text((w//2,h//2),news["kur_title"][:100],font=font(52),fill="white",anchor="mm",align="center",spacing=10,stroke_width=2,stroke_fill=(0,0,0))
    d.text((w-40,35),"ASO NEWS",font=font(30),fill=(246,88,18),anchor="ra")
    im.save(IMAGE_FILE,"JPEG",quality=92)
    return IMAGE_FILE

def publish(message,image):
    url=f"https://graph.facebook.com/{GRAPH_VERSION}/{PAGE_ID}/photos"
    try:
        with open(image,"rb") as f:
            r=http.post(url,data={"access_token":FACEBOOK_TOKEN,"message":message},files={"source":("news.jpg",f,"image/jpeg")},timeout=90)
        print("Facebook status:",r.status_code)
        if r.ok:
            data=r.json(); return data.get("post_id") or data.get("id")
        print("Facebook error:",r.text[:1000])
    except Exception as e: print("Facebook publish error:",e)
    return None

def comment(post_id,text):
    for target in [str(post_id),f"{PAGE_ID}_{post_id}"] if "_" not in str(post_id) else [str(post_id)]:
        try:
            r=http.post(f"https://graph.facebook.com/{GRAPH_VERSION}/{target}/comments",data={"access_token":FACEBOOK_TOKEN,"message":text},timeout=40)
            if r.ok: return True
        except Exception: pass
    return False

def main():
    items=collect()
    print("Candidates:",len(items))
    if not items: return
    items=[x for x in items if x["score"]>=MIN_SCORE] or items
    news=ai_edit(items) or fallback(items)
    message=f"{news['kur_title']}\n\n{news['body']}\n\n{news['hashtags']}"
    extra=f"{news['full_body']}\n\nسەرچاوە: {news['source']}\n{news['link']}"
    post_id=publish(message,make_image(news))
    if not post_id: raise RuntimeError("Facebook post failed")
    comment(post_id,extra)
    posted.append(news["id"]); save_history()
    print("Published:",post_id)

if __name__=="__main__":
    main()
