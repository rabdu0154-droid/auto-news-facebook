import os,re,json,html,hashlib,xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from datetime import datetime,timezone
from urllib.parse import quote
import requests
from PIL import Image,ImageDraw,ImageFont,ImageOps
from google import genai

PAGE_ID="1128027710403407"
GRAPH_VERSION=os.getenv("FACEBOOK_GRAPH_VERSION","v26.0")
GEMINI_MODEL=os.getenv("GEMINI_TEXT_MODEL","gemini-3.5-flash")
HISTORY_FILE="posted_news.json"
IMAGE_FILE="news_image.jpg"
BACKGROUND_FILES=["news_background.jpg","news_background.png","background.jpg","background.png","assets/news_background.jpg","assets/news_background.png"]
MAX_HISTORY=2000
MAX_AGE_HOURS=48
GEMINI_API_KEY=os.getenv("GEMINI_API_KEY")
FACEBOOK_TOKEN=os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN")
if not GEMINI_API_KEY: raise RuntimeError("GEMINI_API_KEY is missing")
if not FACEBOOK_TOKEN: raise RuntimeError("FACEBOOK_PAGE_ACCESS_TOKEN is missing")
client=genai.Client(api_key=GEMINI_API_KEY)
http=requests.Session()
http.headers.update({"User-Agent":"ASO-NEWS-Bot/2.0","Accept-Language":"en-US,en;q=0.9"})

SOURCES=[
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
("BBC",40,"site:bbc.com/news Iraq OR Kurdistan OR Baghdad OR Erbil")]

KURDISTAN=["kurdistan","kurdish","erbil","hawler","sulaymaniyah","sulaimani","duhok","kirkuk","halabja","هەولێر","سلێمانی","دهۆک","کوردستان","کەرکووک"]
IRAQ=["iraq","baghdad","mosul","basra","najaf","karbala","anbar","nineveh","عێراق","بغداد","مووسڵ","بەصرە","نەجەف","کەربەلا"]
BREAKING=["breaking","urgent","attack","strike","drone","missile","earthquake","fire","killed","death","crisis","هێرش","تەقینەوە","درۆن","مووشەک","ئاگر","کوژراو","قەیران","فۆری"]
KURDISH_CHARS=set("ئەپچڕڵێۆیەوەکگژخدرتننمڵۆهسشعغفڤقڕێ")
LATIN_RE=re.compile(r"[A-Za-z]")

def clean(x):
    x=html.unescape(str(x or ""))
    return re.sub(r"\s+"," ",re.sub(r"<[^>]+>"," ",x)).strip()

def nid(title,link):
    return hashlib.sha256((clean(link)+"|"+clean(title).lower()).encode()).hexdigest()

def load():
    try:
        d=json.load(open(HISTORY_FILE,encoding="utf-8"))
        return [x if isinstance(x,str) else x.get("id") for x in d if isinstance(x,str) or isinstance(x,dict) and x.get("id")]
    except Exception:return []

posted=load()

def save():
    json.dump(posted[-MAX_HISTORY:],open(HISTORY_FILE,"w",encoding="utf-8"),ensure_ascii=False,separators=(",",":"))

def rss(q):
    return "https://news.google.com/rss/search?q="+quote(q)+"&hl=en-US&gl=US&ceid=US:en"

def fetch(name,priority,q):
    try: root=ET.fromstring(http.get(rss(q),timeout=30).content)
    except Exception as e: print("RSS error",name,e); return []
    now=datetime.now(timezone.utc).timestamp(); out=[]
    for it in root.findall(".//item")[:12]:
        title=clean(it.findtext("title")); link=clean(it.findtext("link")); summary=clean(it.findtext("description")); pub=clean(it.findtext("pubDate"))
        if not title or not link: continue
        try: age=max(0,(now-parsedate_to_datetime(pub).timestamp())/3600)
        except Exception: age=999
        if age>MAX_AGE_HOURS: continue
        ident=nid(title,link)
        if ident in posted: continue
        t=(title+" "+summary).lower()
        score=priority+min(sum(k in t for k in KURDISTAN),3)*28+min(sum(k in t for k in IRAQ),3)*18+min(sum(k in t for k in KURDISTAN+IRAQ),4)*8+min(sum(k in t for k in BREAKING),3)*9
        if age<1: score+=24
        elif age<3: score+=18
        elif age<6: score+=12
        elif age<12: score+=7
        elif age<24: score+=3
        out.append({"id":ident,"title":title,"summary":summary,"link":link,"source":name,"score":score})
    return out

def collect():
    a=[]
    for s in SOURCES:a+=fetch(*s)
    return sorted({x["id"]:x for x in a}.values(),key=lambda x:x["score"],reverse=True)[:30]

def is_sorani(text):
    if not text:return False
    t=clean(text)
    kur=sum(c in KURDISH_CHARS for c in t)
    latin=len(LATIN_RE.findall(t))
    return kur>=5 and kur>=latin

def edit_news(items):
    block="\n\n".join(f"SOURCE_NUMBER: {i}\nSOURCE: {x['source']}\nTITLE: {x['title']}\nSUMMARY: {x['summary'][:1000]}\nURL: {x['link']}" for i,x in enumerate(items,1))
    prompt="""تۆ دەستکار و نووسەری هەواڵی ASO NEWS ـیت.
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
"""+block
    try:
        r=client.models.generate_content(model=GEMINI_MODEL,contents=prompt)
        text=(r.text or "").strip()
    except Exception as e:
        print("Gemini error",e); return None
    m=re.search(r"SOURCE_NUMBER\s*:\s*(\d+)",text,re.I)
    idx=max(0,min((int(m.group(1))-1 if m else 0),len(items)-1))
    x=items[idx].copy()
    def field(n,d):
        m=re.search(rf"^{n}\s*:\s*(.*?)(?=^\w[\w_ ]*\s*:|\Z)",text,re.I|re.M|re.S)
        return clean(m.group(1)) if m else d
    x["kur_title"]=field("TITLE",x["title"])
    x["body"]=field("BODY",x["summary"][:500])
    x["full_body"]=field("FULL_BODY",x["body"])
    x["hashtags"]=field("HASHTAGS","#ASONEWS #کوردستان #عێراق")
    x["source"]=field("SOURCE",x["source"])
    if not is_sorani(x["kur_title"]+" "+x["body"]):
        print("Gemini output was not Sorani; retrying translation")
        try:
            tr=client.models.generate_content(
                model=GEMINI_MODEL,
                contents=f"""ئەم هەواڵە بە تەواوی وەرگێڕە بۆ کوردی سۆرانی. هیچ ئینگلیزییەک مەهێڵە جگە لە هاشتاکەکان.
TITLE: {x["kur_title"]}
BODY: {x["body"]}
FULL_BODY: {x["full_body"]}
تەنها بەو شێوەیە بگەڕێنەوە:
TITLE: ...
BODY: ...
FULL_BODY: ..."""
            )
            tt=(tr.text or "").strip()
            x["kur_title"]=field_from_text(tt,"TITLE",x["kur_title"])
            x["body"]=field_from_text(tt,"BODY",x["body"])
            x["full_body"]=field_from_text(tt,"FULL_BODY",x["full_body"])
        except Exception as e:
            print("Kurdish translation error",e)
    return x

def field_from_text(text,name,default):
    m=re.search(rf"^{name}\s*:\s*(.*?)(?=^\w[\w_ ]*\s*:|\Z)",text,re.I|re.M|re.S)
    return clean(m.group(1)) if m else default

def fallback(items):
    x=items[0]
    return {**x,
        "kur_title":"هەواڵێکی گرنگ لە کوردستان",
        "body":"هەواڵێکی تازە و گرنگ لە ناوچەکەوە بڵاوکراوەتەوە. وردەکارییەکان بەدوای پشتڕاستکردنەوەی سەرچاوەکە دەخرێنەڕوو.",
        "full_body":"هەواڵەکە پەیوەندی بە کوردستان و عێراقەوە هەیە و لە سەرچاوەی سەرەکییەوە وەرگیراوە.",
        "hashtags":"#ASONEWS #کوردستان #عێراق"}

def find_background():
    for p in BACKGROUND_FILES:
        if os.path.exists(p):
            return p
    return None

def make_image(news):
    bg=find_background()
    if bg:
        im=Image.open(bg).convert("RGB")
        # Keep the complete supplied background instead of cropping it to landscape.
        target_w=1200
        target_h=round(im.height*target_w/im.width)
        im=im.resize((target_w,target_h),Image.Resampling.LANCZOS)
        print("Using custom background:",bg,im.size)
    else:
        im=Image.new("RGB",(1200,675),(18,30,45))
        d=ImageDraw.Draw(im)
        for y in range(675):
            v=int(18+25*y/675)
            d.line((0,y,1200,y),fill=(v,v+8,v+18))
        print("Custom background not found; using fallback background")
    d=ImageDraw.Draw(im)
    paths=["/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf","/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
    f=ImageFont.load_default()
    for p in paths:
        if os.path.exists(p):
            f=ImageFont.truetype(p,52)
            break

    # Put the Kurdish headline in the large empty center area of the supplied design.
    title=clean(news["kur_title"])[:140]
    max_width=im.width-260
    words=title.split()
    lines=[]
    line=""
    for word in words:
        test=(line+" "+word).strip()
        if d.textbbox((0,0),test,font=f)[2] <= max_width:
            line=test
        else:
            if line: lines.append(line)
            line=word
    if line: lines.append(line)
    lines=lines[:4]
    bbox=f.getbbox("کوردستان")
    line_h=bbox[3]-bbox[1]+18
    total_h=line_h*len(lines)
    y0=round(im.height*0.43-total_h/2)
    for i,line in enumerate(lines):
        d.text((im.width//2,y0+i*line_h),line,font=f,fill="white",anchor="ma",align="center",
               stroke_width=3,stroke_fill="black")

    im.save(IMAGE_FILE,"JPEG",quality=92,optimize=True)
    return IMAGE_FILE

def publish(message,image):
    try:
        with open(image,"rb") as f:
            r=http.post(f"https://graph.facebook.com/{GRAPH_VERSION}/{PAGE_ID}/photos",
                data={"access_token":FACEBOOK_TOKEN,"message":message},
                files={"source":("news.jpg",f,"image/jpeg")},timeout=90)
        print("Facebook status:",r.status_code)
        if r.ok:
            d=r.json()
            return d.get("post_id") or d.get("id")
        print("Facebook error:",r.text[:1000])
    except Exception as e: print("Facebook error:",e)
    return None

def first_comment(pid,text):
    targets=[str(pid),f"{PAGE_ID}_{pid}"] if "_" not in str(pid) else [str(pid)]
    for t in targets:
        try:
            r=http.post(f"https://graph.facebook.com/{GRAPH_VERSION}/{t}/comments",data={"access_token":FACEBOOK_TOKEN,"message":text},timeout=40)
            if r.ok:return True
        except Exception:pass
    return False

def main():
    items=collect(); print("Candidates:",len(items))
    if not items:return
    news=edit_news(items) or fallback(items)
    message=f"{news['kur_title']}\n\n{news['body']}\n\n{news['hashtags']}"
    extra=f"{news['full_body']}\n\nسەرچاوە: {news['source']}\n{news['link']}"
    pid=publish(message,make_image(news))
    if not pid:raise RuntimeError("Facebook post failed")
    first_comment(pid,extra)
    posted.append(news["id"]); save(); print("Published:",pid)

if __name__=="__main__":main()
