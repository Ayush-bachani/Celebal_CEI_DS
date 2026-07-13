import os, re, json, time, sqlite3
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
CSV_PATH = os.environ.get("CSV_PATH", os.path.join(BASE_DIR, "healthcare_dataset.csv"))
DB_PATH = os.path.join(BASE_DIR, "hospital.db")
TOP_K = 4
SIM_THRESHOLD = 0.35
MAX_ROWS = 200

_client = None
_conn = None
_embedder = None
_index = None
CHUNKS = []
SCHEMA = ""

HISTORY = []
LOG = []

def _get_client():
    global _client
    if _client is None:
        from groq import Groq
        _client = Groq(api_key=GROQ_API_KEY)
    return _client

def ask_llm(system, user, temperature=0.0, json_mode=False, max_tokens=1024):
    kwargs = dict(
        model=GROQ_MODEL,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
        temperature=temperature, max_tokens=max_tokens,
    )
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    return _get_client().chat.completions.create(**kwargs).choices[0].message.content.strip()

def load_and_clean(csv_path):
    df = pd.read_csv(csv_path)
    before = len(df)
    df = df.drop_duplicates().reset_index(drop=True)
    df["Name"] = df["Name"].str.title().str.strip()
    df["Hospital"] = df["Hospital"].str.replace(r"[,\s]+$", "", regex=True).str.strip()
    df["Date of Admission"] = pd.to_datetime(df["Date of Admission"], errors="coerce")
    df["Discharge Date"] = pd.to_datetime(df["Discharge Date"], errors="coerce")
    df["Billing Amount"] = df["Billing Amount"].round(2)
    return df

def build_database(df, db_path=DB_PATH):
    if os.path.exists(db_path):
        os.remove(db_path)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    cur = conn.cursor()
    cur.executescript("""
    CREATE TABLE patients (
        patient_id INTEGER PRIMARY KEY,
        name TEXT, age INTEGER, gender TEXT, blood_type TEXT
    );
    CREATE TABLE admissions (
        admission_id INTEGER PRIMARY KEY,
        patient_id INTEGER,
        medical_condition TEXT, doctor TEXT, hospital TEXT,
        insurance_provider TEXT, room_number INTEGER, admission_type TEXT,
        date_of_admission TEXT, discharge_date TEXT,
        medication TEXT, test_results TEXT, billing_amount REAL,
        FOREIGN KEY (patient_id) REFERENCES patients(patient_id)
    );""")
    pat = df[["Name","Age","Gender","Blood Type"]].drop_duplicates().reset_index(drop=True)
    pat.insert(0, "patient_id", range(1, len(pat)+1))
    key = {tuple(r): i for i, r in zip(pat["patient_id"],
           pat[["Name","Age","Gender","Blood Type"]].values.tolist())}
    d = df.copy()
    d["patient_id"] = [key[tuple(r)] for r in d[["Name","Age","Gender","Blood Type"]].values.tolist()]
    pat.columns = ["patient_id","name","age","gender","blood_type"]
    pat.to_sql("patients", conn, if_exists="append", index=False)

    adm = d[["patient_id","Medical Condition","Doctor","Hospital","Insurance Provider",
             "Room Number","Admission Type","Date of Admission","Discharge Date",
             "Medication","Test Results","Billing Amount"]].copy()
    adm.columns = ["patient_id","medical_condition","doctor","hospital","insurance_provider",
                   "room_number","admission_type","date_of_admission","discharge_date",
                   "medication","test_results","billing_amount"]
    adm["date_of_admission"] = adm["date_of_admission"].dt.strftime("%Y-%m-%d")
    adm["discharge_date"]    = adm["discharge_date"].dt.strftime("%Y-%m-%d")
    adm.insert(0, "admission_id", range(1, len(adm)+1))
    adm.to_sql("admissions", conn, if_exists="append", index=False)

    for col in ["medical_condition","admission_type","insurance_provider","test_results","date_of_admission"]:
        cur.execute(f"CREATE INDEX idx_adm_{col} ON admissions({col});")
    cur.execute("CREATE INDEX idx_adm_patient ON admissions(patient_id);")
    cur.execute("""
    CREATE VIEW patient_records AS
    SELECT a.admission_id, p.name, p.age, p.gender, p.blood_type,
           a.medical_condition, a.doctor, a.hospital, a.insurance_provider,
           a.room_number, a.admission_type, a.date_of_admission, a.discharge_date,
           a.medication, a.test_results, a.billing_amount
    FROM admissions a JOIN patients p ON a.patient_id = p.patient_id;""")
    conn.commit()
    return conn

CATEGORICAL_HINTS = {
    "gender": ["Male", "Female"],
    "medical_condition": ["Diabetes","Hypertension","Obesity","Cancer","Asthma","Arthritis"],
    "admission_type": ["Elective","Urgent","Emergency"],
    "insurance_provider": ["Medicare","Cigna","Blue Cross","Aetna","UnitedHealthcare"],
    "medication": ["Aspirin","Ibuprofen","Lipitor","Paracetamol","Penicillin"],
    "test_results": ["Normal","Abnormal","Inconclusive"],
    "blood_type": ["A+","A-","B+","B-","AB+","AB-","O+","O-"],
}

def schema_text():
    hints = "\n".join(f"  - {c}: one of {v}" for c, v in CATEGORICAL_HINTS.items())
    return f"""You may ONLY query this read-only view:
    VIEW patient_records(
        admission_id INTEGER,
        name TEXT, age INTEGER, gender TEXT, blood_type TEXT,
        medical_condition TEXT, doctor TEXT, hospital TEXT,
        insurance_provider TEXT, room_number INTEGER, admission_type TEXT,
        date_of_admission TEXT (YYYY-MM-DD), discharge_date TEXT (YYYY-MM-DD),
        medication TEXT, test_results TEXT, billing_amount REAL
    )

    Allowed values for key columns:
    {hints}

    Notes:
    - One row = one hospital admission.
    - Dates are ISO strings; compare with date('...') or string comparison.
    - Use ROUND(...,2) for money. This is SQLite."""

POLICY_DOCUMENTS = {
"admission_policy": """
HOSPITAL ADMISSION POLICY (Document ADM-001, v3.2)
1. Admission Types. Three types exist: Elective (planned), Urgent (within 24-48h),
   and Emergency (immediate). Every admission records its type at registration.
2. Registration. Staff collect name, age, gender, blood type, and a valid insurance
   provider. A room number is assigned by admission type and bed availability.
3. Elective Scheduling. Elective admissions are scheduled at least 5 business days
   ahead; pre-admission testing is completed within 72 hours before admission.
4. Rooms. Assigned by clinical need first, preference second. Private rooms are subject
   to availability and may carry an extra daily charge (see Billing Policy).
5. Consent. No admission proceeds without informed consent, except emergencies where
   implied consent applies until a representative is reached.
""",
"billing_policy": """
HOSPITAL BILLING POLICY (Document BIL-002, v4.1)
1. Billing Basis. Each admission generates one itemized bill (the billing amount)
   combining room, procedures, medication, and tests; finalized at discharge.
2. Insurance Coordination. The hospital bills the insurer first (Medicare, Cigna,
   Blue Cross, Aetna, UnitedHealthcare). The patient covers co-pay/deductible/balance.
3. Estimates. A good-faith estimate is available before elective procedures; it is not
   the final bill.
4. Disputes. A bill may be disputed within 60 days. Negative or zero amounts indicate a
   correction/credit and are flagged for finance review before the account closes.
5. Payment Plans. Interest-free plans up to 12 months for balances over $500. Financial
   assistance must be applied for within 90 days of discharge.
""",
"discharge_policy": """
HOSPITAL DISCHARGE POLICY (Document DIS-003, v2.7)
1. Authorization. Discharge requires the attending doctor's written order. The discharge
   date is recorded and can never precede the admission date.
2. Criteria. The patient must be clinically stable, have a follow-up plan, and have
   medications reconciled before the doctor signs the discharge order.
3. Discharge Summary. Every patient receives a summary with diagnosis, medications, notable
   test results, and follow-up instructions; a copy goes to their primary care physician.
4. Against Medical Advice. A patient may leave AMA; staff document the decision and risks
   and obtain a signed AMA form where possible. AMA discharges still record a discharge date.
5. Follow-up. Patients with abnormal or inconclusive test results get a follow-up within
   14 days, arranged before they leave.
""",
"insurance_policy": """
HOSPITAL INSURANCE AND PRIOR AUTHORIZATION POLICY (Document INS-004, v3.0)
1. Accepted Insurance. Medicare, Cigna, Blue Cross, Aetna, UnitedHealthcare. Each record
   lists exactly one provider.
2. Prior Authorization. Prior insurance approval (pre-authorization) is REQUIRED for all
   elective surgical procedures and non-emergency imaging before the service is delivered.
3. Emergencies. Emergency care never waits for prior authorization; the insurer is notified
   within 24 hours of an emergency admission.
4. Coverage Checks. Coverage is verified at registration. If a provider cannot be verified,
   the patient is counseled on self-pay options before non-urgent care proceeds.
5. Denials. A denied claim may be appealed within 30 days with supporting clinical notes.
""",
"emergency_policy": """
HOSPITAL EMERGENCY CARE POLICY (Document EMR-005, v2.1)
1. Triage. Emergency patients are triaged immediately on arrival and seen in order of
   clinical severity, not arrival time.
2. Treatment First. Life-saving treatment is never delayed for registration or payment;
   administrative steps follow stabilization.
3. Admission. An emergency admission is recorded with admission type Emergency and a room
   assigned as soon as one is available.
4. Consent. Implied consent applies when a patient cannot consent and no representative is
   present, limited to what is needed to stabilize the patient.
5. Transfer. If a needed service is unavailable, the patient is stabilized and transferred
   to an appropriate facility with records forwarded.
""",
}

def chunk_text(text, source, max_chars=700, overlap=120):
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks, buf = [], ""
    for p in paras:
        if len(buf) + len(p) + 1 <= max_chars:
            buf = (buf + "\n" + p).strip()
        else:
            if buf: chunks.append(buf)
            buf = (buf[-overlap:] + "\n" + p).strip() if buf else p
    if buf: chunks.append(buf)
    return [{"source": source, "chunk_id": f"{source}-{i}", "text": c}
            for i, c in enumerate(chunks)]

def retrieve(query, k=TOP_K):
    q = _embedder.encode([query], normalize_embeddings=True)
    scores, idx = _index.search(q, k)
    out = []
    for s, i in zip(scores[0], idx[0]):
        c = dict(CHUNKS[i]); c["score"] = float(s); out.append(c)
    return out

ROUTER_SYSTEM = """You are a router for a hospital assistant. Classify the question into
exactly one route and reply as JSON: {"route": "...", "reason": "..."}.
Routes:
- DATABASE: answerable from patient admission records (counts, averages, lists, filters).
- POLICY: about hospital rules/procedures (admission, billing, discharge, insurance, emergency).
- SMALLTALK: greetings or "what can you do".
- OUT_OF_DOMAIN: unrelated to this hospital's data or policies (general medical advice,
  world facts, coding help, etc.).
Use recent conversation only to resolve follow-ups. Reply with JSON only."""

def route_fallback(query):
    q = query.lower()
    if any(k in q for k in ["policy", "procedure", "rule", "authorization", "approval", "require"]):
        return {"route": "POLICY", "reason": "keyword fallback"}
    if any(k in q for k in ["how many", "average", "count", "list", "show", "patients", "billing", "admitted"]):
        return {"route": "DATABASE", "reason": "keyword fallback"}
    return {"route": "SMALLTALK", "reason": "keyword fallback"}

def route_query(query, history=None):
    ctx = ""
    if history:
        ctx = "Recent conversation:\n" + "\n".join(
            f"{h['role']}: {h['content']}" for h in history[-4:]) + "\n\n"
    try:
        data = json.loads(ask_llm(ROUTER_SYSTEM, ctx + "Question: " + query,
                                  json_mode=True, max_tokens=200))
        if data.get("route") not in {"DATABASE", "POLICY", "SMALLTALK", "OUT_OF_DOMAIN"}:
            raise ValueError("unknown route")
        return data
    except Exception:
        return route_fallback(query)

def _sql_system():
    return f"""You convert a question into ONE SQLite SELECT query.
{SCHEMA}
Rules:
- Output JSON only: {{"sql": "SELECT ..."}}
- SELECT only. Never write/modify data.
- Prefer aggregates (COUNT, AVG, SUM, GROUP BY) when asked.
- Match categorical values exactly as listed. Do not invent columns."""

def is_safe_select(sql):
    s = sql.strip().rstrip(";").lower()
    if not s.startswith("select"):
        return False
    banned = ["insert", "update", "delete", "drop", "alter", "create", "attach", "pragma", ";", "--"]
    return not any(b in s for b in banned)

def enforce_limit(sql):
    low = sql.lower()
    if any(a in low for a in ["count(", "avg(", "sum(", "min(", "max(", "group by"]):
        return sql
    return sql if "limit" in low else sql.rstrip(";") + f" LIMIT {MAX_ROWS}"

def run_sql_agent(query):
    try:
        raw = ask_llm(_sql_system(), "Question: " + query, json_mode=True, max_tokens=400)
        sql = json.loads(raw).get("sql", "").strip()
    except Exception as e:
        return {"ok": False, "error": f"Could not generate SQL: {e}", "sql": None}
    if not is_safe_select(sql):
        return {"ok": False, "error": "Blocked: not a safe read-only SELECT.", "sql": sql}
    sql = enforce_limit(sql)
    try:
        cur = _conn.execute(sql)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
    except Exception as e:
        return {"ok": False, "error": f"SQL execution failed: {e}", "sql": sql}
    return {"ok": True, "sql": sql, "columns": cols,
            "rows": [list(r) for r in rows[:MAX_ROWS]], "row_count": len(rows)}

RAG_SYSTEM = """You are a hospital policy assistant. Answer ONLY using the policy excerpts in
CONTEXT. Rules:
- If the answer is not in the context, reply exactly:
  "I don't have that in the hospital policy documents."
- Never use outside knowledge or guess.
- Be concise and cite the policy name(s) you used, e.g. (insurance_policy)."""

def run_rag_agent(query, threshold=SIM_THRESHOLD):
    hits = retrieve(query, k=TOP_K)
    top = hits[0]["score"] if hits else 0.0
    if top < threshold:
        return {"grounded": False, "sources": hits,
                "answer": "I don't have that in the hospital policy documents."}
    context = "\n\n".join(f"[{h['source']}] {h['text']}" for h in hits)
    answer = ask_llm(RAG_SYSTEM, f"CONTEXT:\n{context}\n\nQUESTION: {query}", max_tokens=500)
    return {"grounded": True, "sources": hits, "answer": answer}

FORMAT_SYSTEM = """You are a hospital knowledge assistant. Write a short, clear answer for staff
using ONLY the data provided. Never invent numbers or facts. If given SQL result rows, state
the figures exactly as provided."""

def format_sql_result(query, result):
    if not result["ok"]:
        return f"I couldn't answer that from the database. {result['error']}"
    if result["row_count"] == 0:
        return "No matching records were found in the database."
    payload = {"question": query, "columns": result["columns"],
               "rows": result["rows"][:50], "row_count": result["row_count"]}
    return ask_llm(FORMAT_SYSTEM,
        "Summarize in 1-3 sentences using ONLY these results; quote exact numbers.\n"
        + json.dumps(payload), max_tokens=350)

def init(verbose=True):
    """Build the DB and the FAISS index once (called lazily on first request)."""
    global _conn, _embedder, _index, CHUNKS, SCHEMA
    if _conn is not None:
        return
    if verbose: print("[engine] building database ...")
    df = load_and_clean(CSV_PATH)
    _conn = build_database(df)
    SCHEMA = schema_text()

    if verbose: print("[engine] building knowledge base ...")
    CHUNKS = []
    for name, text in POLICY_DOCUMENTS.items():
        CHUNKS += chunk_text(text, name)

    from sentence_transformers import SentenceTransformer
    import faiss
    _embedder = SentenceTransformer(EMBED_MODEL)
    emb = _embedder.encode([c["text"] for c in CHUNKS], normalize_embeddings=True)
    _index = faiss.IndexFlatIP(emb.shape[1])
    _index.add(emb)
    if verbose: print(f"[engine] ready - {_index.ntotal} chunks indexed.")

def _pipeline_label(route):
    if route == "DATABASE":  return ["Orchestrator", "SQL Agent", "Formatter"]
    if route == "POLICY":    return ["Orchestrator", "RAG Agent", "Formatter"]
    return ["Orchestrator", "Direct reply"]

def answer_query(question):
    """Same route -> agent -> format flow as ask_assistant(), plus per-stage timings
    and structured evidence for the UI. Errors are caught so the API never 500s."""
    init()
    t0 = time.time()
    trace, sources, sql, evidence = [], None, None, {"type": "none"}

    ts = time.time()
    try:
        decision = route_query(question, HISTORY)
    except Exception as e:
        decision = {"route": "OUT_OF_DOMAIN", "reason": f"router error: {e}"}
    route = decision["route"]
    trace.append({"stage": "Query routing", "detail": f"Classified as {route}.",
                  "ms": int((time.time()-ts)*1000)})

    try:
        if route == "DATABASE":
            ts = time.time()
            res = run_sql_agent(question)
            sql = res.get("sql")
            trace.append({"stage": "SQL generation + execution",
                          "detail": ("Read-only SELECT executed." if res["ok"]
                                     else "Query blocked or failed."),
                          "ms": int((time.time()-ts)*1000)})
            ts = time.time()
            answer = format_sql_result(question, res)
            trace.append({"stage": "Answer formatting", "detail": "Grounded in returned rows.",
                          "ms": int((time.time()-ts)*1000)})
            evidence = {"type": "database", "ok": res["ok"], "sql": res.get("sql"),
                        "error": res.get("error"),
                        "columns": res.get("columns", []), "rows": res.get("rows", []),
                        "row_count": res.get("row_count", 0)}

        elif route == "POLICY":
            ts = time.time()
            res = run_rag_agent(question)
            trace.append({"stage": "Vector retrieval",
                          "detail": f"FAISS top-{TOP_K}; best score {res['sources'][0]['score']:.2f}."
                                    if res["sources"] else "No chunks.",
                          "ms": int((time.time()-ts)*1000)})
            answer = res["answer"]
            src = [{"source": s["source"], "score": round(s["score"], 3),
                    "match": max(0, min(100, round(s["score"]*100))),
                    "snippet": s["text"].strip().replace("\n", " ")[:180]}
                   for s in res["sources"]]
            sources = [{"source": s["source"], "score": s["score"]} for s in src]
            evidence = {"type": "policy", "grounded": res["grounded"],
                        "sources": src, "threshold": SIM_THRESHOLD}
            trace.append({"stage": "LLM generation",
                          "detail": ("Answer grounded in retrieved policy."
                                     if res["grounded"] else "Below threshold - refused."),
                          "ms": 0})

        elif route == "SMALLTALK":
            answer = ("I'm the hospital query assistant. I can answer questions about patient "
                      "records (counts, averages, lists) and hospital policies (admission, billing, "
                      "discharge, insurance, emergency). What would you like to know?")
        else:
            answer = ("That's outside what I can help with. I can answer questions about this "
                      "hospital's patient records and its policies.")
    except Exception as e:
        answer = f"Something went wrong while answering. ({e})"

    latency = round(time.time() - t0, 2)
    LOG.append({"query": question, "route": route, "reason": decision.get("reason"),
                "sql": sql, "sources": sources, "latency_s": latency})
    HISTORY.append({"role": "user", "content": question})
    HISTORY.append({"role": "assistant", "content": answer})

    return {"question": question, "answer": answer, "route": route,
            "reason": decision.get("reason"), "pipeline": _pipeline_label(route),
            "sql": sql, "sources": sources, "evidence": evidence,
            "trace": trace, "latency_s": latency}

def get_policies():
    return [{"name": k, "text": v.strip()} for k, v in POLICY_DOCUMENTS.items()]

def get_history():
    return LOG

def get_stats():
    """Dataset + session stats for the Dashboard / Stats panel."""
    init()
    def one(q): return _conn.execute(q).fetchone()[0]
    conditions = _conn.execute(
        "SELECT medical_condition, COUNT(*) FROM patient_records "
        "GROUP BY medical_condition ORDER BY COUNT(*) DESC").fetchall()
    routes = {}
    for r in LOG:
        routes[r["route"]] = routes.get(r["route"], 0) + 1
    avg_latency = round(sum(r["latency_s"] for r in LOG)/len(LOG), 2) if LOG else 0.0
    return {
        "patients": one("SELECT COUNT(*) FROM patients"),
        "admissions": one("SELECT COUNT(*) FROM admissions"),
        "hospitals": one("SELECT COUNT(DISTINCT hospital) FROM admissions"),
        "policies": len(POLICY_DOCUMENTS),
        "chunks": len(CHUNKS),
        "conditions": [{"name": c, "count": n} for c, n in conditions],
        "session_queries": len(LOG),
        "avg_latency_s": avg_latency,
        "routes": routes,
        "model": GROQ_MODEL,
        "embed_model": EMBED_MODEL,
        "threshold": SIM_THRESHOLD,
    }
