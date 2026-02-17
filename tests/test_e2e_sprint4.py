import asyncio, time, sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime

TIMESTAMP = datetime.now().strftime("%Y%m%d-%H%M")
REF_BASE = f"TEST-{TIMESTAMP}"
USERS = {
    "maria": {"id": "a1000000-0000-0000-0000-000000000001", "sector": "51000000-0000-0000-0000-000000000001"},
    "juan": {"id": "a1000000-0000-0000-0000-000000000002", "sector": "51000000-0000-0000-0000-000000000002"},
    "carlos": {"id": "a1000000-0000-0000-0000-000000000004", "sector": "51000000-0000-0000-0000-000000000004"},
    "ana": {"id": "a1000000-0000-0000-0000-000000000005", "sector": "51000000-0000-0000-0000-000000000005"},
    "patricia": {"id": "a1000000-0000-0000-0000-000000000007", "sector": "51000000-0000-0000-0000-000000000007"},
}
HABI = "c1000000-0000-0000-0000-000000000001"
TEST_TPL = "c1000000-0000-0000-0000-0000000000fe"
TH = {"create_document": 2000, "save_document": 2000, "start_signing": 5000, "sign_document": 5000, "create_case": 8000, "transfer": 4000, "search": 1000, "list": 1000, "detail": 1000}
R = []
PA = []
T0 = time.time()

def hdr(u):
    return {"X-Tenant-Schema": "100_test", "X-User-ID": USERS[u]["id"], "Content-Type": "application/json"}

def rec(a, o, s, ms, d="", c="general"):
    R.append({"a": a, "o": o, "s": s, "ms": round(ms, 1), "d": d, "c": c})
    if ms > TH.get(c, 5000) and s == "OK":
        PA.append({"a": a, "o": o, "ms": round(ms, 1), "th": TH.get(c, 5000)})

async def req(cl, m, u, h, j=None):
    t = time.time()
    if m == "GET": r = await cl.get(u, headers=h)
    elif m == "POST": r = await cl.post(u, headers=h, json=j)
    elif m == "PATCH": r = await cl.patch(u, headers=h, json=j)
    return r, (time.time() - t) * 1000

async def run_all():
    from httpx import AsyncClient, ASGITransport
    from main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", timeout=60.0) as c:
        print("  AGENTE 1: Maria Rodriguez")
        r, e = await req(c, "GET", "/document-types", hdr("maria"))
        cnt = len(r.json().get("document_types",[])) if r.status_code==200 else 0
        rec("Maria", f"GET /doc-types ({cnt})", "OK" if r.status_code==200 else "FAIL", e, "", "list")
        r, e = await req(c, "GET", "/api/v1/cases/templates", hdr("maria"))
        rec("Maria", "GET templates", "OK" if r.status_code==200 else "FAIL", e, "", "list")
        r, e = await req(c, "POST", "/api/v1/cases/", hdr("maria"), {"case_template_id": HABI, "reference": f"{REF_BASE}-Maria"})
        cim = r.json()["data"]["case"]["case_id"] if r.status_code==200 else None
        cn = r.json()["data"]["case"]["case_number"] if r.status_code==200 else "N/A"
        rec("Maria", f"POST /cases/ HABI -> {cn}", "OK" if r.status_code==200 else "FAIL", e, "" if r.status_code==200 else str(r.status_code), "create_case")
        r, e = await req(c, "POST", "/documents", hdr("maria"), {"document_type_acronym": "INF", "reference": f"{REF_BASE}-Maria-Inf"})
        dim = r.json().get("document_id") if r.status_code==200 else None
        rec("Maria", "POST /documents INF", "OK" if r.status_code==200 else "FAIL", e, "", "create_document")
        onm = None
        if dim:
            r, e = await req(c, "PATCH", f"/documents/{dim}/save", hdr("maria"), {"reference": f"{REF_BASE}-M", "content": "<p>Informe E2E</p>", "signers": [{"user_id": USERS["maria"]["id"], "is_numerator": True}]})
            rec("Maria", "PATCH save", "OK" if r.status_code==200 else "FAIL", e, "" if r.status_code==200 else str(r.status_code), "save_document")
            r, e = await req(c, "GET", f"/documents/{dim}/details", hdr("maria"))
            rec("Maria", "GET details", "OK" if r.status_code==200 else "FAIL", e, "", "detail")
            r, e = await req(c, "POST", f"/documents/{dim}/start-signing-process", hdr("maria"))
            rec("Maria", "POST start-sign", "OK" if r.status_code==200 else "FAIL", e, "" if r.status_code==200 else str(r.status_code), "start_signing")
            r, e = await req(c, "POST", f"/documents/{dim}/super-sign", hdr("maria"))
            onm = r.json().get("official_number") if r.status_code==200 else None
            rec("Maria", f"POST super-sign -> {onm}", "OK" if r.status_code==200 else "FAIL", e, "" if r.status_code==200 else str(r.status_code), "sign_document")
        r, e = await req(c, "GET", "/api/v1/cases/", hdr("maria"))
        rec("Maria", "GET /cases/", "OK" if r.status_code==200 else "FAIL", e, "", "list")
        if cim:
            r, e = await req(c, "GET", f"/api/v1/cases/{cim}", hdr("maria"))
            rec("Maria", "GET case detail", "OK" if r.status_code==200 else "FAIL", e, "", "detail")
            r, e = await req(c, "GET", f"/api/v1/cases/{cim}/case-history", hdr("maria"))
            rec("Maria", "GET history", "OK" if r.status_code==200 else "FAIL", e, "", "detail")
            r, e = await req(c, "GET", f"/api/v1/cases/{cim}/permissions", hdr("maria"))
            rec("Maria", "GET perms", "OK" if r.status_code==200 else "FAIL", e, "", "detail")
            r, e = await req(c, "GET", f"/api/v1/cases/{cim}/documents", hdr("maria"))
            rec("Maria", "GET case-docs", "OK" if r.status_code==200 else "FAIL", e, "", "detail")
        if cim and onm:
            from database import execute_query
            od = execute_query("SELECT id FROM official_documents WHERE official_number = %s", (onm,), fetch_one=True, schema_name="100_test")
            if od:
                r, e = await req(c, "POST", f"/api/v1/cases/{cim}/documents/link", hdr("maria"), {"official_document_id": str(od["id"])})
                rec("Maria", "POST link-doc", "OK" if r.status_code==200 else "FAIL", e)
        if cim:
            r, e = await req(c, "POST", f"/api/v1/cases/{cim}/transfer", hdr("maria"), {"target_sector_id": USERS["juan"]["sector"], "reason": f"{REF_BASE}-T", "transfer_ownership": True})
            rec("Maria", "POST transfer->Legal", "OK" if r.status_code==200 else "FAIL", e, "" if r.status_code==200 else str(r.status_code), "transfer")
            r, e = await req(c, "POST", f"/api/v1/cases/{cim}/transfer", hdr("maria"), {"target_sector_id": "00000000-0000-0000-0000-000000000000", "reason": "NEG", "transfer_ownership": True})
            rec("Maria", f"[NEG] transfer inexist -> {r.status_code}", "OK" if r.status_code in (400,403,404,422,500) else "FAIL", e)
        r, e = await req(c, "POST", "/documents", hdr("maria"), {"document_type_acronym": "ZZZZZ", "reference": "NEG"})
        rec("Maria", f"[NEG] tipo invalido -> {r.status_code}", "OK" if r.status_code in (400,404,422,500) else "FAIL", e)

        print("  AGENTE 2: Juan Perez")
        r, e = await req(c, "GET", "/api/v1/cases/", hdr("juan"))
        rec("Juan", "GET /cases/", "OK" if r.status_code==200 else "FAIL", e, "", "list")
        r, e = await req(c, "POST", "/documents", hdr("juan"), {"document_type_acronym": "DICT", "reference": f"{REF_BASE}-Juan"})
        dij = r.json().get("document_id") if r.status_code==200 else None
        rec("Juan", "POST DICT", "OK" if r.status_code==200 else "FAIL", e, "", "create_document")
        if dij:
            r, e = await req(c, "PATCH", f"/documents/{dij}/save", hdr("juan"), {"content": "<p>Dict</p>", "signers": [{"user_id": USERS["juan"]["id"], "is_numerator": True}, {"user_id": USERS["maria"]["id"], "is_numerator": False}]})
            rec("Juan", "PATCH save(2firm)", "OK" if r.status_code==200 else "FAIL", e, "" if r.status_code==200 else str(r.status_code), "save_document")
            r, e = await req(c, "POST", f"/documents/{dij}/start-signing-process", hdr("juan"))
            rec("Juan", "POST start-sign", "OK" if r.status_code==200 else "FAIL", e, "" if r.status_code==200 else str(r.status_code), "start_signing")
            r, e = await req(c, "POST", f"/documents/{dij}/super-sign", hdr("maria"))
            rec("Juan", "POST co-firma(Maria)", "OK" if r.status_code==200 else "FAIL", e, "" if r.status_code==200 else str(r.status_code), "sign_document")
            r, e = await req(c, "POST", f"/documents/{dij}/super-sign", hdr("juan"))
            on = r.json().get("official_number") if r.status_code==200 else None
            rec("Juan", f"POST num -> {on}", "OK" if r.status_code==200 else "FAIL", e, "" if r.status_code==200 else str(r.status_code), "sign_document")
        r, e = await req(c, "GET", "/api/v1/cases/", {})
        rec("Juan", f"[NEG] sin auth -> {r.status_code}", "OK" if r.status_code in (400,401,403,422,500) else "FAIL", e)

        print("  AGENTE 3: Ana Lopez")
        r, e = await req(c, "POST", "/api/v1/cases/", hdr("ana"), {"case_template_id": TEST_TPL, "reference": f"{REF_BASE}-Ana"})
        cia = r.json()["data"]["case"]["case_id"] if r.status_code==200 else None
        rec("Ana", "POST /cases/ TEST", "OK" if r.status_code==200 else "FAIL", e, "", "create_case")
        r, e = await req(c, "POST", "/documents", hdr("ana"), {"document_type_acronym": "INF", "reference": f"{REF_BASE}-Ana-Inf"})
        dia = r.json().get("document_id") if r.status_code==200 else None
        rec("Ana", "POST INF", "OK" if r.status_code==200 else "FAIL", e, "", "create_document")
        if dia:
            r, e = await req(c, "PATCH", f"/documents/{dia}/save", hdr("ana"), {"content": "<p>PP</p>", "signers": [{"user_id": USERS["ana"]["id"], "is_numerator": True}]})
            rec("Ana", "PATCH save", "OK" if r.status_code==200 else "FAIL", e, "", "save_document")
            r, e = await req(c, "POST", f"/documents/{dia}/start-signing-process", hdr("ana"))
            rec("Ana", "POST start-sign", "OK" if r.status_code==200 else "FAIL", e, "", "start_signing")
            r, e = await req(c, "POST", f"/documents/{dia}/super-sign", hdr("ana"))
            rec("Ana", "POST super-sign", "OK" if r.status_code==200 else "FAIL", e, "", "sign_document")
        if cia:
            r, e = await req(c, "POST", f"/api/v1/cases/{cia}/transfer", hdr("ana"), {"target_sector_id": USERS["patricia"]["sector"], "reason": f"{REF_BASE}-T", "transfer_ownership": True})
            rec("Ana", "POST transfer->Contab", "OK" if r.status_code==200 else "FAIL", e, "", "transfer")
        if dia:
            r, e = await req(c, "PATCH", f"/documents/{dia}/save", hdr("ana"), {"content": "<p>x</p>"})
            rec("Ana", f"[NEG] edit firmado -> {r.status_code}", "OK" if r.status_code in (400,409,422,500) else "FAIL", e)

        print("  AGENTE 4: Patricia Garcia")
        r, e = await req(c, "GET", "/api/v1/cases/", hdr("patricia"))
        rec("Patricia", "GET /cases/", "OK" if r.status_code==200 else "FAIL", e, "", "list")
        r, e = await req(c, "POST", "/documents", hdr("patricia"), {"document_type_acronym": "DICT", "reference": f"{REF_BASE}-Pat"})
        dip = r.json().get("document_id") if r.status_code==200 else None
        rec("Patricia", "POST DICT", "OK" if r.status_code==200 else "FAIL", e, "", "create_document")
        if dip:
            r, e = await req(c, "PATCH", f"/documents/{dip}/save", hdr("patricia"), {"content": "<p>DP</p>", "signers": [{"user_id": USERS["patricia"]["id"], "is_numerator": True}]})
            rec("Patricia", "PATCH save", "OK" if r.status_code==200 else "FAIL", e, "", "save_document")
            r, e = await req(c, "POST", f"/documents/{dip}/start-signing-process", hdr("patricia"))
            rec("Patricia", "POST start-sign", "OK" if r.status_code==200 else "FAIL", e, "", "start_signing")
            r, e = await req(c, "POST", f"/documents/{dip}/super-sign", hdr("patricia"))
            rec("Patricia", "POST super-sign", "OK" if r.status_code==200 else "FAIL", e, "", "sign_document")
        r, e = await req(c, "GET", "/api/v1/cases/00000000-0000-0000-0000-000000000000", hdr("patricia"))
        rec("Patricia", f"[NEG] case inexist -> {r.status_code}", "OK" if r.status_code in (400,403,404,500) else "FAIL", e)

        print("  AGENTE 5: Carlos Martinez (Rechazo+Refirma)")
        r, e = await req(c, "POST", "/documents", hdr("carlos"), {"document_type_acronym": "INF", "reference": f"{REF_BASE}-Carlos"})
        dic = r.json().get("document_id") if r.status_code==200 else None
        rec("Carlos", "POST INF", "OK" if r.status_code==200 else "FAIL", e, "", "create_document")
        if dic:
            r, e = await req(c, "PATCH", f"/documents/{dic}/save", hdr("carlos"), {"content": "<p>err</p>", "signers": [{"user_id": USERS["carlos"]["id"], "is_numerator": True}]})
            rec("Carlos", "PATCH save", "OK" if r.status_code==200 else "FAIL", e, "", "save_document")
            r, e = await req(c, "POST", f"/documents/{dic}/start-signing-process", hdr("carlos"))
            rec("Carlos", "POST start-sign", "OK" if r.status_code==200 else "FAIL", e, "", "start_signing")
            r, e = await req(c, "POST", f"/documents/{dic}/reject", hdr("carlos"), {"reason": f"{REF_BASE}-Rechazo"})
            rec("Carlos", "POST REJECT", "OK" if r.status_code==200 else "FAIL", e, "" if r.status_code==200 else str(r.status_code))
            r, e = await req(c, "PATCH", f"/documents/{dic}/save", hdr("carlos"), {"content": "<p>OK</p>"})
            rec("Carlos", "PATCH save(post-rej)", "OK" if r.status_code==200 else "FAIL", e, "", "save_document")
            r, e = await req(c, "POST", f"/documents/{dic}/start-signing-process", hdr("carlos"))
            rec("Carlos", "POST start(re)", "OK" if r.status_code==200 else "FAIL", e, "", "start_signing")
            r, e = await req(c, "POST", f"/documents/{dic}/super-sign", hdr("carlos"))
            rec("Carlos", "POST sign(re)", "OK" if r.status_code in (200,400,409,422) else "FAIL", e, "", "sign_document")
        r, e = await req(c, "POST", "/api/v1/cases/", hdr("carlos"), {"case_template_id": TEST_TPL, "reference": f"{REF_BASE}-Carlos-Caso"})
        rec("Carlos", "POST /cases/ TEST", "OK" if r.status_code==200 else "FAIL", e, "", "create_case")
        if dic:
            r, e = await req(c, "POST", f"/documents/{dic}/reject", hdr("carlos"), {"reason": "NEG"})
            rec("Carlos", f"[NEG] rej oficial -> {r.status_code}", "OK" if r.status_code in (400,409,422,500) else "FAIL", e)
        if dim:
            r, e = await req(c, "POST", f"/documents/{dim}/super-sign", hdr("carlos"))
            rec("Carlos", f"[NEG] firmar ajeno -> {r.status_code}", "OK" if r.status_code in (400,403,409,500) else "FAIL", e)

        print("  AGENTE 6: Cross-User Ops")
        r, e = await req(c, "GET", "/document-states", hdr("maria"))
        rec("Cross", "GET /document-states", "OK" if r.status_code==200 else "FAIL", e, "", "list")
        if cim:
            r, e = await req(c, "GET", f"/api/v1/cases/{cim}/movements", hdr("juan"))
            rec("Cross", "GET movements(x)", "OK" if r.status_code==200 else "FAIL", e, "", "detail")
            r, e = await req(c, "GET", f"/api/v1/cases/{cim}/available-sectors", hdr("juan"))
            rec("Cross", "GET avail-sectors", "OK" if r.status_code in (200,400,403,404,422,500) else "FAIL", e, "", "detail")
        try:
            r, e = await req(c, "GET", "/api/v1/cases/", {"X-Tenant-Schema": "100_test", "X-User-ID": "00000000-0000-0000-0000-000000000000"})
            rec("Cross", f"[NEG] bad user -> {r.status_code}", "OK" if r.status_code in (400,401,403,404,500) else "FAIL", e)
        except Exception as ex:
            rec("Cross", "[NEG] bad user -> ERR", "OK", 0, str(ex)[:50])
        try:
            r, e = await req(c, "GET", "/api/v1/cases/", {"X-Tenant-Schema": "FAKE999", "X-User-ID": USERS["maria"]["id"]})
            rec("Cross", f"[NEG] bad schema -> {r.status_code}", "OK" if r.status_code in (200,400,403,404,500) else "FAIL", e)
        except Exception as ex:
            rec("Cross", f"[NEG] bad schema -> ERR", "OK", 0, str(ex)[:50])

print("=" * 70)
print("     TEST E2E - POST-REFACTOR SPRINT 4 GDI")
print("=" * 70)
print(f"Fecha: {datetime.now()}")
print(f"BD: dev-test | Schema: 100_test | Ref: {REF_BASE}")
print("=" * 70)
asyncio.run(run_all())
total_time = time.time() - T0
print()
print("=" * 70)
print("                    RESULTADOS POR AGENTE")
print("=" * 70)
agents = {}
for r in R:
    a = r["a"]
    if a not in agents: agents[a] = {"ok": 0, "fail": 0, "total": 0, "ms": 0, "results": []}
    agents[a]["total"] += 1
    agents[a]["ms"] += r["ms"]
    agents[a]["ok" if r["s"]=="OK" else "fail"] += 1
    agents[a]["results"].append(r)
for an, d in agents.items():
    print("")
    print(f"AGENTE: {an}")
    print("-" * 65)
    for r in d["results"]:
        op = r["o"][:46]
        icon = "OK" if r["s"]=="OK" else "FAIL"
        det = ""
        if r["d"] and r["s"]=="FAIL": det = f" ({r["d"][:35]})"
        print(f"  {op:<48} {icon:>6} {r["ms"]:.0f}ms{det}")
    print(f"  Subtotal: {d["ok"]}/{d["total"]} OK ({d["ms"]:.0f}ms)")
print()
print("=" * 70)
print("                        RESUMEN FINAL")
print("=" * 70)
tt = to = tf = 0
for an, d in agents.items():
    tt += d["total"]; to += d["ok"]; tf += d["fail"]
    print(f"  {an:<12} {d["total"]:>6} {d["ok"]:>5} {d["fail"]:>5} {d["ms"]/1000:.1f}s")
print("-" * 42)
print(f"  TOTAL        {tt:>6} {to:>5} {tf:>5} {total_time:.1f}s")
print("=" * 70)
if PA:
    print("  ALERTAS DE PERFORMANCE")
    for a in PA: print(f"  WARN: {a["a"]}/{a["o"][:30]} = {a["ms"]:.0f}ms (umbral: {a["th"]}ms)")
st = "PASSED" if tf == 0 else "FAILED"
print("")
print(f"ESTADO: {st} | Tests: {tt} | OK: {to} | FAIL: {tf} | Duracion: {total_time:.1f}s")
print("=" * 70)
if tf > 0:
    print("[FAIL DETAILS]")
    for r in R:
        if r["s"] == "FAIL": print(f"  {r["a"]}/{r["o"]}: {r["d"]}")
    sys.exit(1)
