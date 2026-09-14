"""The test-case bank: ONE self-contained HTML file (tests/deck2511.html) that holds every case
(prompt, the client's drawing when the deck had one) together with the results of the latest
run batch, and renders itself.  It is the root of all future test runs.

    data = bank.load()                          # the JSON blob embedded in the page
    cases = bank.harness_cases(data)            # -> tests/rerun_deck_ir.py case dicts
    bank.ensure_cases(data, extra_cases)        # cases from fixture files join the bank (chapter 9)
    bank.update_from_run(data, out_dir, runs)   # store the batch: per-run bbox / built, one drawing
    bank.save(data)                             # rewrite the page (shell + blob)

"Complex shape" cases of the deck are listed under data["excluded"] only: they have no record
and are never run.
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


def case_dir(c):
    return "%s_%s_n%s" % (c["uid"], slug(c["section"]), c["num"] if c.get("num") is not None else "x")


def harness_cases(data, french=False):
    return [{"uid": c["uid"], "dir": case_dir(c), "section": c["section"], "num": c.get("num"),
             "prompt": (c.get("prompt_fr") if french else None) or c["prompt_en"], "slide": c.get("slide"),
             "august_uid": c.get("august_uid")} for c in data["cases"]]


def ensure_cases(data, cases):
    """Add harness cases (from fixture files) that the bank does not know yet; returns how many were added."""
    known = {c["uid"] for c in data["cases"]}
    added = 0
    for h in cases:
        if h["uid"] in known:
            continue
        sec = h.get("section") or ""
        m = re.match(r"\s*(\d+)", sec)
        data["cases"].append({"uid": h["uid"], "slide": None, "section": sec, "chapter": int(m.group(1)) if m else None,
                              "num": h.get("num"), "prompt_en": h["prompt"], "prompt_fr": None, "verdict_2511": None,
                              "tester_note": None, "august_uid": None, "gt": None, "result": None, "history": []})
        known.add(h["uid"])
        added += 1
    data["cases"].sort(key=lambda c: (c.get("chapter") or 0, c["uid"]))
    return added


def _bbox(rec):
    bb = (rec.get("build") or {}).get("bbox")
    if bb and len(bb) == 6:
        return sorted(round(bb[i + 1] - bb[i], 1) for i in (0, 2, 4))
    info = ((rec.get("files") or {}).get("obj") or {}).get("mesh_info") or ""
    m = re.match(r"bbox ([\d.]+) ([\d.]+) ([\d.]+)", info)
    return sorted(round(float(v), 1) for v in m.groups()) if m else None


def _same(a, b):
    return bool(a and b) and all(abs(x - y) <= max(0.5, 0.01 * max(x, y)) for x, y in zip(a, b))


def update_from_run(data, out, runs=(1,), label=None):
    """Store one batch (all runs of every case that has results under out/); one drawing per case."""
    out = Path(out)
    date = time.strftime("%Y-%m-%d %H:%M")
    label = label or out.name
    n_cases, n_all_built, n_stable, cost = 0, 0, 0, 0.0
    for c in data["cases"]:
        per_run, drawing = [], None
        for run in runs:
            hits = sorted(out.glob("%s_*/run%d/result.json" % (c["uid"], run)))
            if not hits:
                continue
            r = json.loads(hits[0].read_text(encoding="utf-8"))
            built = bool(r.get("generated"))
            per_run.append({"run": run, "built": built, "bbox": _bbox(r), "asked": r.get("bot_asked"), "turns": r.get("n_turns"),
                            "cost_usd": r.get("total_cost_usd"), "reply": re.sub(r"\s+", " ", r.get("final_reply") or "")[:700]})
            cost += float(r.get("total_cost_usd") or 0)
            png = hits[0].parent / "drawing-1.png"
            if built and drawing is None and png.exists():
                drawing = webp_uri(png, width=900)
        if not per_run:
            continue
        boxes = [p["bbox"] for p in per_run if p["built"] and p["bbox"]]
        all_built = all(p["built"] for p in per_run)
        stable = all_built and len(boxes) == len(per_run) and all(_same(boxes[0], b) for b in boxes[1:])
        c["result"] = {"date": date, "label": label, "runs": per_run, "built": any(p["built"] for p in per_run),
                       "all_built": all_built, "stable": stable, "bbox": boxes[0] if boxes else None, "drawing": drawing,
                       "prompt_used": r.get("prompt")}
        c.setdefault("history", []).append({"date": date, "label": label, "bboxes": [p["bbox"] if p["built"] else None for p in per_run]})
        n_cases += 1
        n_all_built += all_built
        n_stable += stable
    data.setdefault("runs", []).append({"date": date, "label": label, "runs_per_case": len(runs), "cases": n_cases,
                                        "all_built": n_all_built, "stable": n_stable, "cost_usd": round(cost, 4)})
    return {"cases": n_cases, "all_built": n_all_built, "stable": n_stable, "cost_usd": round(cost, 4)}


SHELL = r'''<!doctype html>
<html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Deck 2511 test bank</title>
<style>
:root{--paper:#f5f6f4;--panel:#fff;--ink:#1b222b;--muted:#66717d;--line:#d8dee4;--chip:#e9eef4;--accent:#1f5fa8;--ok:#1e7f4f;--bad:#b23a2e;--warn:#9a6700}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--paper:#12171c;--panel:#1a2129;--ink:#e8edf2;--muted:#97a3af;--line:#2b343e;--chip:#223041;--accent:#7fb2f0;--ok:#5fd39b;--bad:#ff8b82;--warn:#e0b25a}}
:root[data-theme=dark]{--paper:#12171c;--panel:#1a2129;--ink:#e8edf2;--muted:#97a3af;--line:#2b343e;--chip:#223041;--accent:#7fb2f0;--ok:#5fd39b;--bad:#ff8b82;--warn:#e0b25a}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.5 "Segoe UI",system-ui,Arial,sans-serif}
.wrap{max-width:1500px;margin:0 auto;padding:24px 22px 60px}
h1{font-size:28px;margin:0 0 6px}.lede{color:var(--muted);margin:0 0 16px;max-width:95ch}
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
.prompt{font-size:14px;margin:0 0 8px;max-width:120ch}
.runs{display:flex;gap:6px;flex-wrap:wrap;font:12px Consolas,monospace;color:var(--muted);margin:0 0 10px}
.runs span{border:1px solid var(--line);border-radius:6px;padding:2px 7px;background:var(--paper)}.runs span.ok{border-color:var(--ok)}.runs span.bad{border-color:var(--bad);color:var(--bad)}
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
  var D=JSON.parse(document.getElementById('bank').textContent), C=D.cases;
  var esc=function(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});};
  var fmt=function(b){return b?b.map(function(v){return Math.abs(v-Math.round(v))<0.05?Math.round(v):v.toFixed(1);}).join(' × '):'—';};
  var same=function(a,b){if(!a||!b)return false;for(var i=0;i<3;i++){if(Math.abs(a[i]-b[i])>Math.max(0.5,0.01*Math.max(a[i],b[i])))return false;}return true;};
  var last=D.runs&&D.runs.length?D.runs[D.runs.length-1]:null;
  C.forEach(function(c){var h=c.history||[];c._prev=h.length>1?h[h.length-2]:null;var r=c.result;
    var pb=c._prev&&c._prev.bboxes?c._prev.bboxes.filter(Boolean)[0]:null;
    c._changed=!!(r&&r.bbox&&pb&&!same(pb,r.bbox));c._flip=!!(r&&c._prev&&((c._prev.bboxes||[]).some(Boolean)!==r.built));});
  var ran=C.filter(function(c){return c.result;}),allb=ran.filter(function(c){return c.result.all_built;}),stable=ran.filter(function(c){return c.result.stable;}),none=ran.filter(function(c){return !c.result.built;});
  var rpc=last?last.runs_per_case:1;
  document.getElementById('lede').textContent=(D.source?'Nguồn: '+D.source+' (trích '+D.created+'). ':'')+C.length+' case ('+C.filter(function(c){return c.gt;}).length+' có bản vẽ của khách làm ground truth; chương 9 là case bổ sung không có bản vẽ khách). '+(D.excluded?D.excluded.length+' case "complex shape" của deck đã loại, không test. ':'')+'Chạy lại: SKIP_DB=true python tests/rerun_deck_ir.py --runs 3 → kết quả được ghi vào chính file này.'+(last?' Lần chạy gần nhất: '+last.label+' ('+last.date+', '+rpc+' lượt mỗi case).':'');
  var st=[[C.length,'case'],[ran.length,'đã chạy'],[allb.length,'ra model cả '+rpc+' lượt'],[stable.length,'cùng kích thước cả '+rpc+' lượt'],[ran.length-allb.length-none.length,'ra model một phần'],[none.length,'không ra model'],[C.filter(function(c){return c._changed||c._flip;}).length,'khác lần chạy trước'],['$'+((last&&last.cost_usd)||0).toFixed(2),'chi phí LLM lần gần nhất']];
  document.getElementById('stats').innerHTML=st.map(function(s){return '<div class="stat"><b>'+s[0]+'</b><span>'+s[1]+'</span></div>';}).join('');
  var chapters=[];C.forEach(function(c){if(chapters.indexOf(c.chapter)<0)chapters.push(c.chapter);});
  var filters=[['all','Tất cả'],['unstable','Không ổn định giữa các lượt'],['nomodel','Không ra model'],['changed','Khác lần chạy trước'],['notrun','Chưa chạy'],['note','Có ghi chú của tester']].concat(chapters.map(function(k){return ['ch'+k,'Chương '+k];}));
  var bar=document.getElementById('bar');bar.innerHTML=filters.map(function(f,i){return '<button class="chip" data-f="'+f[0]+'" aria-pressed="'+(i===0)+'">'+f[1]+'</button>';}).join('')+'<span class="count" id="count"></span>';
  var html=[],cur=null;
  C.forEach(function(c){
    if(c.chapter!==cur){cur=c.chapter;html.push('<h2 class="chap" data-ch="'+cur+'">Chương '+cur+'</h2>');}
    var r=c.result,flags=['ch'+c.chapter],n=r?r.runs.length:0,nb=r?r.runs.filter(function(p){return p.built;}).length:0;
    if(!r)flags.push('notrun');if(r&&!r.built)flags.push('nomodel');if(r&&r.built&&!r.stable)flags.push('unstable');if(c._changed||c._flip)flags.push('changed');if(c.tester_note)flags.push('note');
    var pills='';
    if(!r)pills+='<span class="pill mute">chưa chạy</span>';
    else if(!r.built)pills+='<span class="pill bad">'+nb+'/'+n+' ra model</span>';
    else if(r.stable)pills+='<span class="pill ok">'+nb+'/'+n+' ra model · cùng kích thước</span>';
    else if(r.all_built)pills+='<span class="pill warn">'+nb+'/'+n+' ra model · kích thước khác nhau giữa các lượt</span>';
    else pills+='<span class="pill warn">'+nb+'/'+n+' ra model</span>';
    if(c._changed)pills+='<span class="pill warn">khác lần trước: '+esc(fmt(c._prev.bboxes.filter(Boolean)[0]))+'</span>';
    if(c._flip)pills+='<span class="pill warn">lần trước '+(c._prev.bboxes.some(Boolean)?'ra':'không ra')+' model</span>';
    if(c.tester_note)pills+='<span class="pill mute">'+esc(c.tester_note)+'</span>';if(c.verdict_2511)pills+='<span class="pill mute">2511: '+esc(c.verdict_2511)+'</span>';
    var runs=r?'<div class="runs">'+r.runs.map(function(p){return '<span class="'+(p.built?'ok':'bad')+'">run'+p.run+': '+(p.built?esc(fmt(p.bbox)):'không ra model')+'</span>';}).join('')+'</div>':'';
    var gt='<figure><figcaption><b>Bản vẽ khách (GT)</b></figcaption>'+(c.gt?'<img loading="lazy" src="'+c.gt+'" alt="GT">':'<div class="nomodel"><div class="why" style="color:var(--muted)">không có</div>Case bổ sung, deck của khách không có bản vẽ.</div>')+'</figure>';
    var res;if(!r)res='<figure><figcaption><b>Kết quả</b></figcaption><div class="nomodel"><div class="why" style="color:var(--muted)">chưa chạy</div></div></figure>';
    else if(r.built)res='<figure><figcaption><b>Kết quả '+esc(r.date)+'</b><span class="bbox">'+esc(fmt(r.bbox))+'</span></figcaption>'+(r.drawing?'<img loading="lazy" src="'+r.drawing+'" alt="drawing">':'<div class="nomodel">không có bản vẽ</div>')+'</figure>';
    else res='<figure><figcaption><b>Kết quả '+esc(r.date)+'</b></figcaption><div class="nomodel"><div class="why">không ra model</div>'+esc((r.runs[r.runs.length-1].reply||'').slice(0,500))+'</div></figure>';
    var replies=r?r.runs.map(function(p){return '<pre><b>run'+p.run+'</b>'+(p.asked?' · turn 1: '+esc(p.asked):'')+(p.turns?' · '+p.turns+' turn':'')+(p.cost_usd!=null?' · $'+p.cost_usd:'')+'\n'+esc(p.reply||'—')+'</pre>';}).join(''):'';
    var hist=(c.history||[]).map(function(h){return h.date+': '+(h.bboxes||[]).map(function(b){return b?fmt(b):'—';}).join(' | ');}).join('  →  ');
    html.push('<article class="case" id="c'+esc(c.uid)+'" data-flags="'+flags.join(' ')+'"><div class="head"><span class="uid">'+esc(c.uid)+'</span><h3>'+esc(c.section)+(c.num!=null?' #'+esc(c.num):'')+'</h3>'+pills+'</div>'
      +'<p class="prompt">'+esc(c.prompt_en)+'</p>'+(c.prompt_fr?'<details><summary>Prompt tiếng Pháp gốc</summary><pre>'+esc(c.prompt_fr)+'</pre></details>':'')+runs
      +'<div class="pair">'+gt+res+'</div>'+(r?'<details><summary>Trả lời cuối của bot theo từng lượt</summary>'+replies+'</details>':'')
      +(hist?'<div class="hist">'+esc(hist)+'</div>':'')+'</article>');
  });
  document.getElementById('cases').innerHTML=html.join('');
  var chips=bar.querySelectorAll('.chip'),cards=document.querySelectorAll('.case');
  function apply(f){var k=0;cards.forEach(function(el){var ok=f==='all'||el.dataset.flags.split(' ').indexOf(f)>=0;el.hidden=!ok;if(ok)k++;});
    document.querySelectorAll('h2.chap').forEach(function(h){var e=h.nextElementSibling,vis=false;while(e&&e.tagName!=='H2'){if(!e.hidden)vis=true;e=e.nextElementSibling;}h.hidden=!vis;});
    document.getElementById('count').textContent='Hiển thị '+k+' / '+cards.length+' case';}
  chips.forEach(function(b){b.addEventListener('click',function(){chips.forEach(function(x){x.setAttribute('aria-pressed','false');});b.setAttribute('aria-pressed','true');apply(b.dataset.f);});});
  document.addEventListener('click',function(e){var im=e.target.closest('figure img');if(im)im.closest('figure').classList.toggle('zoom');});
  apply('all');
})();
</script></body></html>
'''
