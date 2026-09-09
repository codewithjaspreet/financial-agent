"""
Minimal Streamlit showcase UI. Talks to the FastAPI backend over HTTP only --
no direct DB/service imports, so this stays a thin client on the same API
contract anyone else (or a real frontend) would use.

Run: streamlit run frontend/app.py  (backend must be running: uvicorn main:app)
"""
import requests
import streamlit as st

API = "http://localhost:8000"

st.set_page_config(page_title="Collections Agent", layout="wide")

if "token" not in st.session_state:
    st.session_state.token = None
if "last_run" not in st.session_state:
    st.session_state.last_run = None

st.title("Collections Agent — Demo")


def auth_headers():
    return {"Authorization": f"Bearer {st.session_state.token}"}


# ---- login -----------------------------------------------------------------
if not st.session_state.token:
    st.subheader("Login")
    email = st.text_input("Email", value="demo@tenant-a.test")
    password = st.text_input("Password", value="demo-password-123", type="password")
    if st.button("Login"):
        r = requests.post(f"{API}/login", json={"email": email, "password": password})
        if r.status_code == 200:
            st.session_state.token = r.json()["token"]
            st.rerun()
        else:
            st.error(f"Login failed ({r.status_code}): {r.text}")
    st.stop()

st.success("Logged in")
if st.sidebar.button("Log out"):
    st.session_state.token = None
    st.rerun()

tab_ask, tab_balance, tab_priority, tab_trace = st.tabs(
    ["Ask", "Customer Balance", "Priority List", "Run Trace"]
)

# ---- ask (full agent) -------------------------------------------------------
with tab_ask:
    question = st.text_area("Question", "Who should we follow up with today?")
    col1, col2 = st.columns(2)
    on_date = col1.date_input("As-of date (optional)", value=None)
    if col2.button("Ask", type="primary"):
        body = {"question": question}
        if on_date:
            body["on_date"] = str(on_date)
        with st.spinner("Running the agent (Gemini + tool calls)..."):
            r = requests.post(f"{API}/ask", json=body, headers=auth_headers())
        # Stored in session_state, not rendered inline -- a Streamlit rerun
        # triggered by ANY later interaction wipes inline results otherwise.
        st.session_state.ask_error = None if r.status_code == 200 else f"{r.status_code}: {r.text}"
        st.session_state.ask_result = r.json() if r.status_code == 200 else None

    if st.session_state.get("ask_error"):
        st.error(st.session_state.ask_error)

    data = st.session_state.get("ask_result")
    if data:
        st.session_state.last_run = data["run_id"]
        state = data["state"]
        if state == "answered":
            st.success(f"**Answer:** {data['answer']}")
        elif state == "clarify":
            st.warning(f"Needs clarification: {data['reason']}")
            st.write("Did you mean one of:")
            for opt in data.get("options") or []:
                st.write(f"- {opt['name']} (score {opt['score']:.2f})")
        elif state == "abstain":
            st.warning(f"Abstained: {data['reason']}")
            for c in data.get("conflicts") or []:
                st.write(f"⚠️ {c['note']}")
        st.caption(f"run_id: {data['run_id']} · policy_version: {data['policy_version']}")

        if data.get("draft"):
            draft = data["draft"]
            st.divider()
            st.write("**Draft created** — approve to send:")
            st.code(draft["idem_key"])
            if st.button("Approve & Send"):
                ar = requests.post(
                    f"{API}/drafts/{draft['outbound_id']}/approve",
                    headers={**auth_headers(), "Idempotency-Key": draft["idem_key"]},
                )
                st.write(ar.json() if ar.status_code == 200 else f"{ar.status_code}: {ar.text}")

# ---- deterministic balance (no Gemini) --------------------------------------
with tab_balance:
    customer_id = st.text_input("Customer ID (UUID)")
    on_date_b = st.date_input("As-of date", value=None, key="bal_date")
    if st.button("Get Balance") and customer_id:
        params = {"on_date": str(on_date_b)} if on_date_b else {}
        r = requests.get(f"{API}/customers/{customer_id}/balance", params=params, headers=auth_headers())
        st.session_state.balance_result = r.json() if r.status_code == 200 else {"error": r.text}
    if st.session_state.get("balance_result") is not None:
        st.json(st.session_state.balance_result)

# ---- priority list -----------------------------------------------------------
with tab_priority:
    if st.button("Load Priority List"):
        r = requests.get(f"{API}/priority", headers=auth_headers())
        st.session_state.priority_result = r.json()["customers"] if r.status_code == 200 else {"error": r.text}
    result = st.session_state.get("priority_result")
    if isinstance(result, list):
        for c in result:
            if c.get("score") is None:
                st.write(f"— (skipped: {c['reasons'][0]['note']})")
            else:
                st.write(f"**#{c['rank']}** — {c.get('outstanding', {}).get('display', '?')} "
                         f"(score {c['score']:.2f}) — {c['customer_id']}")
    elif result:
        st.error(result["error"])

# ---- provenance trace --------------------------------------------------------
with tab_trace:
    run_id = st.text_input("Run ID", value=st.session_state.last_run or "")
    if st.button("Show Trace") and run_id:
        r = requests.get(f"{API}/runs/{run_id}/trace", headers=auth_headers())
        st.session_state.trace_result = r.json() if r.status_code == 200 else {"error": r.text}
    if st.session_state.get("trace_result") is not None:
        st.json(st.session_state.trace_result)
