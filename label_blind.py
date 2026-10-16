#!/usr/bin/env python3
"""Blind human labeling for `skill_eval.py calibrate --golden` (2026-10-16).

The human never sees the grader: the page embeds no grader verdict, evidence or model, the human picks pass/fail
directly, the label stored is that choice (`human_passed`, copied to `label`), there is no "fill all", and saving is
refused until every item has an answer. Nothing here calls a model.

  python3 label_blind.py build SRC.html|items.json OUT.html     # strip grader fields from an existing items set
  python3 label_blind.py serve OUT.html [--port 8765]           # http://127.0.0.1:PORT/ ; POST /submit saves JSON
"""
import argparse, datetime, hashlib, html, http.server, json, os, re, sys

GRADER_KEYS = ("grader", "grader_passed", "grader_evidence", "grader_model", "label", "human_answer")
KEEP = ("id", "case", "case_name", "arm", "run", "pressure", "pressure_context", "prompt", "expected_output", "output",
        "tool_calls", "item", "set")
DATA_RE = re.compile(r'<script id="data" type="application/json">(.*?)</script>', re.S)


def load_items(src):
    text = open(src, encoding="utf-8").read()
    m = DATA_RE.search(text)
    data = json.loads(m.group(1)) if m else json.loads(text)
    items = data["items"] if isinstance(data, dict) and "items" in data else \
        data["examples"] if isinstance(data, dict) else data
    return (data.get("iteration") if isinstance(data, dict) else None), items


GRADER_KEY_RE = re.compile(r"grader|verdict|human_answer|^label$|^passed$|^human_passed$", re.I)


def strip_grader(v):
    """Deep copy of v without any grader-looking key at any depth."""
    if isinstance(v, dict): return {k: strip_grader(x) for k, x in v.items() if not GRADER_KEY_RE.search(str(k))}
    if isinstance(v, list): return [strip_grader(x) for x in v]
    return v


def has_grader(v):
    if isinstance(v, dict): return any(GRADER_KEY_RE.search(str(k)) or has_grader(x) for k, x in v.items())
    if isinstance(v, list): return any(has_grader(x) for x in v)
    return False


def fingerprint(items):
    return hashlib.sha256(json.dumps(items, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


def blind_items(items):
    out = []
    for it in items:
        b = {k: strip_grader(it.get(k)) for k in KEEP if k in it}
        b.setdefault("tool_calls", [])
        out.append(b)
    ids = [b["id"] for b in out]
    if len(set(ids)) != len(ids): raise ValueError("duplicate item ids")
    return out


def validate_submission(sub, items):
    """(golden dict, None) or (None, error). Every item must carry human_passed true/false; nothing grader-derived."""
    ans = (sub or {}).get("answers")
    if not isinstance(ans, dict): return None, "answers missing"
    missing = [it["id"] for it in items if not isinstance((ans.get(it["id"]) or {}).get("human_passed"), bool)]
    if missing: return None, "%d item(s) unanswered" % len(missing)
    extra = sorted(set(ans) - {it["id"] for it in items})
    if extra: return None, "unknown item ids: %s" % ", ".join(extra[:5])
    ex = []
    for it in items:
        a = ans[it["id"]]
        note = a.get("note") if isinstance(a.get("note"), str) else ""
        e = {k: strip_grader(it.get(k)) for k in ("id", "prompt", "expected_output", "output", "tool_calls", "item", "set", "case",
                                     "arm", "run", "pressure")}
        e.update(human_passed=a["human_passed"], label=a["human_passed"], note=note, blind=True)
        ex.append(e)
    return {"source": "blind human labels (label_blind.py)", "blind": True, "examples": ex, "skipped": []}, None


PAGE = r"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>블라인드 라벨링 — __TITLE__</title><style>
body{font-family:-apple-system,"Apple SD Gothic Neo",sans-serif;max-width:960px;margin:0 auto;padding:16px;background:#f6f7f9;color:#222}
.card{background:#fff;border-radius:10px;padding:16px;margin:14px 0;box-shadow:0 1px 3px #0002}.card.done{border-left:6px solid #2a7}
.tag{display:inline-block;font-size:12px;padding:2px 8px;border-radius:9px;background:#eef;margin-right:4px}
pre{white-space:pre-wrap;word-break:break-word;background:#f2f2f2;padding:8px;border-radius:6px;max-height:260px;overflow:auto;font-size:13px}
.rubric{font-weight:600;font-size:16px;background:#fff8dc;padding:8px;border-radius:6px}
button{font-size:15px;padding:6px 14px;margin-right:6px;border-radius:6px;border:1px solid #888;background:#fff;cursor:pointer}
button.sel{background:#335;color:#fff}textarea{width:100%;min-height:44px;margin-top:8px}
#bar{position:sticky;top:0;background:#fff;padding:10px;z-index:9;box-shadow:0 1px 3px #0002}</style></head><body>
<div id="bar"><b>블라인드 라벨링</b> · <span id="pt"></span> <button id="jump">다음 미완료로</button> <button id="sub">저장(제출)</button> <span id="subst"></span></div>
<div class="card"><p>각 항목에서 AI 결과가 <b>채점 기준 문장</b>을 만족하는지 직접 보고 <b>통과 / 불통과</b>를 고르세요. 채점기의 판정은 보여주지 않습니다. 모든 항목에 답해야 저장할 수 있습니다. 답은 이 브라우저(localStorage)에 남습니다.</p></div>
<div id="list"></div>
<script id="data" type="application/json">__DATA__</script>
<script>
const D=JSON.parse(document.getElementById('data').textContent),K='skill_eval_blind_'+D.key;
let S={};try{S=JSON.parse(localStorage.getItem(K)||'{}')}catch(e){S={}}
const el=(t,a,x)=>{const e=document.createElement(t);if(a)for(const k in a)e.setAttribute(k,a[k]);if(x!=null)e.textContent=x;return e};
const left=()=>D.items.filter(i=>typeof (S[i.id]||{}).human_passed!=='boolean');
const prog=()=>{pt.textContent=`${D.items.length-left().length} / ${D.items.length} 완료`;sub.disabled=left().length>0};
const save=()=>{localStorage.setItem(K,JSON.stringify(S));prog()};
D.items.forEach((it,i)=>{const c=el('div',{class:'card',id:'c'+i});c.append(el('div',null,`#${i+1} / ${D.items.length}`));
 [`과제 ${it.case} (${it.case_name||''})`,it.arm==='on'?'스킬 사용':'스킬 없음',it.run,it.pressure?'압박 과제':'일반 과제'].forEach(t=>c.append(el('span',{class:'tag'},t)));
 c.append(el('h4',null,'사용자 요청'),el('pre',null,(it.pressure_context?'[압박 상황] '+it.pressure_context+'\n\n':'')+it.prompt));
 if(it.expected_output){const d=el('details');d.append(el('summary',null,'기대 결과 보기'),el('pre',null,it.expected_output));c.append(d)}
 c.append(el('h4',null,'채점 기준 문장'),el('div',{class:'rubric'},it.item),el('h4',null,'AI 결과 (최종 메시지)'),el('pre',null,it.output||'(빈 메시지)'));
 const tc=el('details');tc.append(el('summary',null,`도구 호출 기록 (${it.tool_calls.length}건)`),el('pre',null,it.tool_calls.map((x,j)=>(j+1)+'. '+x.tool+': '+JSON.stringify(x.input)).join('\n')));c.append(tc);
 const b=el('div'),m=el('textarea',{placeholder:'메모 (선택)'}),btns={};
 const paint=()=>{const v=(S[it.id]||{}).human_passed;btns.t.className=v===true?'sel':'';btns.f.className=v===false?'sel':'';c.className='card'+(typeof v==='boolean'?' done':'')};
 [['t',true,'통과 (기준 만족)'],['f',false,'불통과 (기준 불만족)']].forEach(([k,v,t])=>{const x=el('button',{type:'button'},t);x.onclick=()=>{S[it.id]=Object.assign(S[it.id]||{},{human_passed:v});save();paint()};btns[k]=x;b.append(x)});
 m.value=(S[it.id]||{}).note||'';m.oninput=()=>{S[it.id]=Object.assign(S[it.id]||{},{note:m.value});save()};
 c.append(el('h4',null,'당신의 판단'),b,m);list.append(c);paint()});
prog();
jump.onclick=()=>{const l=left();if(!l.length)alert('모두 완료했습니다.');else document.getElementById('c'+D.items.indexOf(l[0])).scrollIntoView({behavior:'smooth'})};
sub.onclick=async()=>{const l=left();if(l.length){alert(`아직 ${l.length}개 항목에 답하지 않았습니다.`);return}
 const answers={};D.items.forEach(i=>{answers[i.id]={human_passed:S[i.id].human_passed,note:S[i.id].note||''}});
 subst.textContent='저장 중…';try{const r=await fetch('/submit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({answers})});
 const j=await r.json();subst.textContent=j.ok?`저장 완료 (${j.n}개: ${j.file})`:`저장 실패: ${j.error}`}catch(e){subst.textContent='저장 실패: 서버(label_blind.py serve)에 연결할 수 없습니다.'}};
</script></body></html>"""


def render(items, title, key):
    data = json.dumps({"key": key, "items": items}, ensure_ascii=False).replace("</", "<\\/")
    return PAGE.replace("__TITLE__", html.escape(title)).replace("__DATA__", data)


def build(src, out):
    it_n, items = load_items(src)
    b = blind_items(items)
    key = "%s_it%s_%s" % (os.path.basename(os.path.dirname(os.path.dirname(os.path.abspath(out)))), it_n,
                          fingerprint(b))  # changed items never restore old answers
    with open(out, "x", encoding="utf-8") as f:  # never overwrite an existing page
        f.write(render(b, os.path.basename(out), key))
    return len(b)


def serve(page, port):
    _, items = load_items(page)
    if has_grader(items): sys.exit("page carries grader fields; refusing")
    outdir = os.path.dirname(os.path.abspath(page))
    items = blind_items(items)  # the served page is regenerated from the sanitized items, never the file as-is
    body = render(items, os.path.basename(page), "%s_%s" % (os.path.basename(os.path.dirname(os.path.dirname(outdir))),
                                                            fingerprint(items))).encode("utf-8")

    class H(http.server.BaseHTTPRequestHandler):
        def _send(self, code, ctype, data):
            self.send_response(code); self.send_header("Content-Type", ctype); self.end_headers(); self.wfile.write(data)
        def do_GET(self):
            if self.path in ("/", "/index.html"): self._send(200, "text/html; charset=utf-8", body)
            else: self._send(404, "text/plain", b"not found")
        def do_POST(self):
            if self.path != "/submit": return self._send(404, "text/plain", b"not found")
            try: sub = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
            except ValueError: sub = None
            g, err = validate_submission(sub, items)
            if err: return self._send(400, "application/json", json.dumps({"ok": False, "error": err}).encode())
            name = "golden_labels_blind_%s.json" % datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            with open(os.path.join(outdir, name), "x", encoding="utf-8") as f:
                json.dump(g, f, ensure_ascii=False, indent=2)
            self._send(200, "application/json", json.dumps({"ok": True, "n": len(g["examples"]), "file": name}).encode())
        def log_message(self, *a): pass

    print("http://127.0.0.1:%d/  (Ctrl-C to stop; saves into %s)" % (port, outdir))
    http.server.ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    s = p.add_subparsers(dest="cmd", required=True)
    b = s.add_parser("build"); b.add_argument("src"); b.add_argument("out")
    v = s.add_parser("serve"); v.add_argument("page"); v.add_argument("--port", type=int, default=8765)
    a = p.parse_args(argv)
    if a.cmd == "build": print("wrote %s (%d items, no grader fields)" % (a.out, build(a.src, a.out)))
    else: serve(a.page, a.port)


if __name__ == "__main__":
    main()
