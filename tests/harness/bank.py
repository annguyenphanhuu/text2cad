"""The test-case bank: ONE self-contained HTML file (tests/deck2511.html) that holds every case
(prompt, the client's drawing) together with the latest run result, and renders itself.

    data = bank.load()                      # the JSON blob embedded in the page
    cases = bank.harness_cases(data)        # -> tests/rerun_deck_ir.py case dicts
    bank.update_from_run(data, out_dir)     # write the results of a run back into the blob
    bank.save(data)                         # rewrite the page (shell + blob)

Nothing else is needed to re-run the tests: the .pptx the bank came from can go.
"""
import base64
import io
import json
import re
import time
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
BANK = ROOT / "tests" / "deck2511.html"
_BLOB_RE = re.compile(r'<script id="bank" type="application/json">(.*?)</script>', re.S)


def webp_uri(src, width=None, quality=78):
    """data: URI of an image (path or bytes) re-encoded as WebP, optionally downscaled."""
    im = Image.open(io.BytesIO(src) if isinstance(src, (bytes, bytearray)) else src).convert("RGB")
    if width and im.width > width:
        im = im.resize((width, int(im.height * width / im.width)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "WEBP", quality=quality, method=6)
    return "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def load(path=BANK):
    m = _BLOB_RE.search(Path(path).read_text(encoding="utf-8"))
    if not m:
        raise ValueError("%s carries no bank blob" % path)
    return json.loads(m.group(1))


def save(data, path=BANK):
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    Path(path).write_text(SHELL.replace("__BLOB__", blob), encoding="utf-8")
    return Path(path).stat().st_size


def slug(s):
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")


def harness_cases(data, french=False):
    out = []
    for c in data["cases"]:
        prompt = (c.get("prompt_fr") if french else None) or c["prompt_en"]
        out.append({"uid": c["uid"], "dir": "%s_%s_n%s" % (c["uid"], slug(c["section"]), c["num"] if c["num"] is not None else "x"),
                    "section": c["section"], "num": c["num"], "prompt": prompt, "slide": c.get("slide"),
                    "august_uid": c.get("august_uid")})
    return out


def _bbox(rec):
    bb = (rec.get("build") or {}).get("bbox")
    if bb and len(bb) == 6:
        return sorted(round(bb[i + 1] - bb[i], 1) for i in (0, 2, 4))
    info = ((rec.get("files") or {}).get("obj") or {}).get("mesh_info") or ""
    m = re.match(r"bbox ([\d.]+) ([\d.]+) ([\d.]+)", info)
    return sorted(round(float(v), 1) for v in m.groups()) if m else None


def update_from_run(data, out, run_dir="run1", label=None):
    """Store the latest result (and a bbox history line) of every case that has a result.json under out/."""
    out = Path(out)
    date = time.strftime("%Y-%m-%d %H:%M")
    label = label or out.name
    n, built, cost = 0, 0, 0.0
    for c in data["cases"]:
        hits = sorted(out.glob("%s_*/%s/result.json" % (c["uid"], run_dir)))
        if not hits:
            continue
        r = json.loads(hits[0].read_text(encoding="utf-8"))
        png = hits[0].parent / "drawing-1.png"
        res = {"date": date, "label": label, "built": bool(r.get("generated")), "bbox": _bbox(r),
               "asked": r.get("bot_asked"), "turns": r.get("n_turns"), "cost_usd": r.get("total_cost_usd"),
               "reply": re.sub(r"\s+", " ", r.get("final_reply") or "")[:700],
               "drawing": webp_uri(png, width=900) if png.exists() else None,
               "prompt_used": r.get("prompt")}
        c["result"] = res
        c.setdefault("history", []).append({"date": date, "label": label, "built": res["built"], "bbox": res["bbox"]})
        n += 1
        built += res["built"]
        cost += float(r.get("total_cost_usd") or 0)
    data.setdefault("runs", []).append({"date": date, "label": label, "cases": n, "built": built, "cost_usd": round(cost, 4)})
    return {"cases": n, "built": built, "cost_usd": round(cost, 4)}


SHELL = r'''<!doctype html>
<html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Deck 2511 test bank</title>
<style>
:root{--paper:#f5f6f4;--panel:#fff;--ink:#1b222b;--muted:#66717d;--line:#d8dee4;--chip:#e9eef4;--accent:#1f5fa8;--ok:#1e7f4f;--bad:#b23a2e;--warn:#9a6700}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--paper:#12171c;--panel:#1a2129;--ink:#e8edf2;--muted:#97a3af;--line:#2b343e;--chip:#223041;--accent:#7fb2f0;--ok:#5fd39b;--bad:#ff8b82;--warn:#e0b25a}}
:root[data-theme=dark]{--paper:#12171c;--panel:#1a2129;--ink:#e8edf2;--muted:#97a3af;--line:#2b343e;--chip:#223041;--accent:#7fb2f0;--ok:#5fd39b;--bad:#ff8b82;--warn:#e0b25a}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.5 "Segoe UI",system-ui,Arial,sans-serif}
.wrap{max-width:1500px;margin:0 auto;padding:24px 22px 60px}
h1{font-size:28px;margin:0 0 6px}.lede{color:var(--muted);margin:0 0 16px;max-width:90ch}
.stats{display:flex;flex-wrap:wrap;gap:10px;margin:0 0 18px}.stat{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:8px 14px;min-width:120px}
.stat b{display:block;font-size:22px;font-variant-numeric:tabular-nums}.stat span{font-size:12px;color:var(--muted)}
.bar{position:sticky;top:0;z-index:5;background:var(--paper);padding:8px 0 10px;border-bottom:1px solid var(--line);display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-bottom:18px}
.chip{border:1px solid var(--line);background:var(--panel);color:var(--ink);border-radius:999px;padding:4px 11px;font-size:13px;font-weight:600;cursor:pointer}
.chip[aria-pressed=true]{background:var(--accent);border-color:var(--accent);color:#fff}.bar .count{margin-left:auto;font-size:13px;color:var(--muted)}
h2.chap{font-size:19px;margin:30px 0 8px}
.case{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 16px 12px;margin:0 0 14px}
.head{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:6px}
.uid{font:13px/1 Consolas,monospace;background:var(--chip);padding:5px 8px;border-radius:6px}
h3{font-size:16px;margin:0;font-weight:600}
.pill{font-size:12px;font-weight:600;letter-spacing:.03em;text-transform:uppercase;padding:2px 8px;border-radius:999px;border:1px solid currentColor}
.pill.ok{color:var(--ok)}.pill.bad{color:var(--bad)}.pill.warn{color:var(--warn)}.pill.mute{color:var(--muted)}
.prompt{font-size:14px;margin:0 0 10px;max-width:120ch}
.pair{display:grid;grid-template-columns:repeat(auto-fit,minmax(380px,1fr));gap:12px}
figure{margin:0;border:1px solid var(--line);border-radius:8px;overflow:hidden;background:#fff}
figcaption{display:flex;gap:8px;align-items:baseline;flex-wrap:wrap;padding:6px 10px;border-bottom:1px solid var(--line);background:var(--panel);color:var(--ink);font-size:13px}
figcaption b{font-weight:600}figcaption .bbox{font:13px Consolas,monospace;color:var(--muted);margin-left:auto}
figure img{display:block;width:100%;height:auto;cursor:zoom-in}figure.zoom{grid-column:1/-1}figure.zoom img{cursor:zoom-out}
.nomodel{padding:12px;font-size:14px;background:var(--panel);min-height:110px;color:var(--ink)}.nomodel .why{font-size:12px;letter-spacing:.06em;text-transform:uppercase;font-weight:600;margin-bottom:4px;color:var(--bad)}
details{margin-top:8px}summary{cursor:pointer;color:var(--accent);font-size:13px}
pre{white-space:pre-wrap;font:12px/1.5 Consolas,monospace;background:var(--paper);border:1px solid var(--line);border-radius:6px;padding:8px 10px;margin:6px 0 0}
.hist{font:12px Consolas,monospace;color:var(--muted);margin-top:6px}
.case[hidden],h2.chap[hidden]{display:none}
</style></head><body><div class="wrap">
<h1>Deck 2511 &mdash; ngân hàng test case</h1>
<p class="lede" id="lede"></p>
<div class="stats" id="stats"></div>
<div class="bar" id="bar" role="toolbar" aria-label="Lọc"></div>
<div id="cases"></div>
</div>
<script id="bank" type="application/json">__BLOB__</script>
<script>
(function(){
  var D=JSON.parse(document.getElementById('bank').textContent), C=D.cases, esc=function(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});};
  var fmt=function(b){return b?b.map(function(v){return Math.abs(v-Math.round(v))<0.05?Math.round(v):v.toFixed(1);}).join(' × '):'—';};
  var same=function(a,b){if(!a||!b)return false;for(var i=0;i<3;i++){if(Math.abs(a[i]-b[i])>Math.max(0.5,0.01*Math.max(a[i],b[i])))return false;}return true;};
  var last=D.runs&&D.runs.length?D.runs[D.runs.length-1]:null;
  C.forEach(function(c){var h=c.history||[];c._prev=h.length>1?h[h.length-2]:null;c._changed=!!(c.result&&c._prev&&c._prev.built&&c.result.built&&!same(c._prev.bbox,c.result.bbox));c._flip=!!(c.result&&c._prev&&c._prev.built!==c.result.built);});
  var built=C.filter(function(c){return c.result&&c.result.built;}).length, ran=C.filter(function(c){return c.result;}).length;
  document.getElementById('lede').textContent=(D.source?'Nguồn: '+D.source+' (trích '+D.created+'). ':'')+C.length+' case, mỗi case có prompt và bản vẽ của khách (ground truth). '+(D.excluded?D.excluded.length+' case "complex shape" đã loại. ':'')+'Chạy lại: SKIP_DB=true python tests/rerun_deck_ir.py  → kết quả mới nhất được ghi vào chính file này.'+(last?' Lần chạy gần nhất: '+last.label+' ('+last.date+').':'');
  var st=[[C.length,'case'],[ran,'đã chạy'],[built,'ra model'],[ran-built,'không ra model'],[C.filter(function(c){return c._changed;}).length,'đổi kích thước so với lần trước'],[C.filter(function(c){return c._flip;}).length,'đổi kết quả so với lần trước'],[(D.runs||[]).length,'lần chạy đã ghi'],['$'+((last&&last.cost_usd)||0).toFixed(2),'chi phí lần gần nhất']];
  document.getElementById('stats').innerHTML=st.map(function(s){return '<div class="stat"><b>'+s[0]+'</b><span>'+s[1]+'</span></div>';}).join('');
  var chapters=[];C.forEach(function(c){if(chapters.indexOf(c.chapter)<0)chapters.push(c.chapter);});
  var filters=[['all','Tất cả'],['notrun','Chưa chạy'],['nomodel','Không ra model'],['changed','Đổi so với lần trước'],['note','Có ghi chú của tester']].concat(chapters.map(function(k){return ['ch'+k,'Chương '+k];}));
  var bar=document.getElementById('bar');bar.innerHTML=filters.map(function(f,i){return '<button class="chip" data-f="'+f[0]+'" aria-pressed="'+(i===0)+'">'+f[1]+'</button>';}).join('')+'<span class="count" id="count"></span>';
  var html=[],cur=null;
  C.forEach(function(c){
    if(c.chapter!==cur){cur=c.chapter;html.push('<h2 class="chap" data-ch="'+cur+'">Chương '+cur+'</h2>');}
    var r=c.result,flags=['ch'+c.chapter];if(!r)flags.push('notrun');if(r&&!r.built)flags.push('nomodel');if(c._changed||c._flip)flags.push('changed');if(c.tester_note)flags.push('note');
    var pills='';if(!r)pills+='<span class="pill mute">chưa chạy</span>';else if(r.built)pills+='<span class="pill ok">ra model</span>';else pills+='<span class="pill bad">không ra model</span>';
    if(c._changed)pills+='<span class="pill warn">khác lần trước: '+esc(fmt(c._prev.bbox))+'</span>';if(c._flip)pills+='<span class="pill warn">lần trước '+(c._prev.built?'ra':'không ra')+' model</span>';
    if(c.tester_note)pills+='<span class="pill mute">'+esc(c.tester_note)+'</span>';if(c.verdict_2511)pills+='<span class="pill mute">2511: '+esc(c.verdict_2511)+'</span>';
    var gt='<figure><figcaption><b>Bản vẽ khách (GT)</b></figcaption>'+(c.gt?'<img loading="lazy" src="'+c.gt+'" alt="GT">':'<div class="nomodel">không có</div>')+'</figure>';
    var res;if(!r)res='<figure><figcaption><b>Kết quả</b></figcaption><div class="nomodel"><div class="why" style="color:var(--muted)">chưa chạy</div></div></figure>';
    else if(r.built)res='<figure><figcaption><b>Kết quả '+esc(r.date)+'</b><span class="bbox">'+esc(fmt(r.bbox))+'</span></figcaption>'+(r.drawing?'<img loading="lazy" src="'+r.drawing+'" alt="drawing">':'<div class="nomodel">không có bản vẽ</div>')+'</figure>';
    else res='<figure><figcaption><b>Kết quả '+esc(r.date)+'</b></figcaption><div class="nomodel"><div class="why">không ra model</div>'+esc((r.reply||'').slice(0,500))+'</div></figure>';
    var hist=(c.history||[]).map(function(h){return h.date+' '+(h.built?fmt(h.bbox):'không ra model');}).join(' → ');
    html.push('<article class="case" id="c'+esc(c.uid)+'" data-flags="'+flags.join(' ')+'"><div class="head"><span class="uid">'+esc(c.uid)+'</span><h3>'+esc(c.section)+(c.num!=null?' #'+esc(c.num):'')+'</h3>'+pills+'</div>'
      +'<p class="prompt">'+esc(c.prompt_en)+'</p>'+(c.prompt_fr?'<details><summary>Prompt tiếng Pháp gốc</summary><pre>'+esc(c.prompt_fr)+'</pre></details>':'')
      +'<div class="pair">'+gt+res+'</div>'+(r?'<details><summary>Trả lời cuối của bot'+(r.asked?' · turn 1: '+esc(r.asked):'')+(r.turns?' · '+r.turns+' turn':'')+(r.cost_usd!=null?' · $'+r.cost_usd:'')+'</summary><pre>'+esc(r.reply)+'</pre></details>':'')
      +(hist?'<div class="hist">'+esc(hist)+'</div>':'')+'</article>');
  });
  document.getElementById('cases').innerHTML=html.join('');
  var chips=bar.querySelectorAll('.chip'),cards=document.querySelectorAll('.case');
  function apply(f){var n=0;cards.forEach(function(k){var ok=f==='all'||k.dataset.flags.split(' ').indexOf(f)>=0;k.hidden=!ok;if(ok)n++;});
    document.querySelectorAll('h2.chap').forEach(function(h){var e=h.nextElementSibling,vis=false;while(e&&e.tagName!=='H2'){if(!e.hidden)vis=true;e=e.nextElementSibling;}h.hidden=!vis;});
    document.getElementById('count').textContent='Hiển thị '+n+' / '+cards.length+' case';}
  chips.forEach(function(b){b.addEventListener('click',function(){chips.forEach(function(x){x.setAttribute('aria-pressed','false');});b.setAttribute('aria-pressed','true');apply(b.dataset.f);});});
  document.addEventListener('click',function(e){var im=e.target.closest('figure img');if(im)im.closest('figure').classList.toggle('zoom');});
  apply('all');
})();
</script></body></html>
'''
