import io
import os
import hashlib
import sqlite3
from datetime import datetime

import pandas as pd
import streamlit as st
from PIL import Image

try:
    from google import genai
except Exception:
    genai = None

# ==========================================================
# 1. CONFIG
# ==========================================================

GEMINI_API_KEY = ""

MODELS = ["gemini-3.8-flash", "gemini-3.8-flash-lite"]  # fallback order
DB_FILE = "swachhconnect.db"

SITES = [
    "NIT More Food Vendor Lane",
    "Ashok Rajpath Metro Work Barrier",
    "Mahendru / Shivpur Drainage Chokepoint",
    "Ganga Ghat Connecting Road",
]

st.set_page_config(
    page_title="SWACHH-NET",
    layout="wide",
    page_icon="🌱",
    initial_sidebar_state="collapsed",
)


def _get_key():
    if GEMINI_API_KEY:
        return GEMINI_API_KEY
    try:
        return st.secrets["GEMINI_API_KEY"]
    except Exception:
        return ""


@st.cache_resource
def get_client(key):
    if not key or genai is None:
        return None
    try:
        return genai.Client(api_key=key)
    except Exception:
        return None


# ==========================================================
# 2. DATABASE
# ==========================================================
def get_db():
    return sqlite3.connect(DB_FILE, check_same_thread=False)


def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute(
        """CREATE TABLE IF NOT EXISTS complaints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            site_location TEXT,
            user_description TEXT,
            ai_category TEXT,
            ai_severity TEXT,
            status TEXT DEFAULT 'Pending',
            photo BLOB
        )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS admin_auth (
            id INTEGER PRIMARY KEY,
            password TEXT
        )"""
    )
    conn.commit()
    conn.close()


init_db()


def hash_pwd(p):
    return hashlib.sha256(p.encode("utf-8")).hexdigest()


def get_admin_password():
    conn = get_db()
    row = conn.execute("SELECT password FROM admin_auth WHERE id = 1").fetchone()
    conn.close()
    return row[0] if row else None


def set_admin_password(new_pwd):
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO admin_auth (id, password) VALUES (1, ?)",
        (hash_pwd(new_pwd),),
    )
    conn.commit()
    conn.close()


# ==========================================================
# 3. AI LOGIC (with safe fallbacks - never shows raw errors)
# ==========================================================
def call_gemini(contents):
    """Returns (text, error_type). error_type: None / nokey / quota / error"""
    client = get_client(_get_key())
    if client is None:
        return None, "nokey"
    last = ""
    for m in MODELS:
        try:
            res = client.models.generate_content(model=m, contents=contents)
            if res and res.text:
                return res.text, None
        except Exception as e:
            last = str(e)
            if "429" in last or "404" in last or "503" in last:
                continue
            break
    return None, ("quota" if "429" in last else "error")


def keyword_classify(text):
    t = text.lower()
    cat = "General Civic Issue"
    if any(k in t for k in ["plastic", "polythene", "bottle", "wrapper"]):
        cat = "Plastic Waste"
    elif any(k in t for k in ["naali", "nali", "drain", "water", "paani", "pani", "jal", "overflow", "sewer"]):
        cat = "Drainage/Water-logging"
    elif any(k in t for k in ["construction", "malba", "debris", "metro", "ret", "sand", "cement", "barrier"]):
        cat = "Construction Debris"
    elif any(k in t for k in ["food", "khana", "sabzi", "vegetable", "fruit", "thela", "vendor"]):
        cat = "Food Waste"
    elif any(k in t for k in ["kachra", "garbage", "trash", "waste", "gandagi"]):
        cat = "Plastic Waste"
    sev = "Medium"
    if any(k in t for k in ["badbu", "bimari", "disease", "block", "overflow", "accident", "danger", "bahut", "serious", "urgent", "critical", "pile"]):
        sev = "Critical"
    elif any(k in t for k in ["thoda", "little", "small", "minor"]):
        sev = "Low"
    return cat, sev


def analyze_complaint(user_text, image_bytes=None):
    prompt = (
        f'Analyze this civic grievance for NIT Patna: "{user_text}".\n'
        "Output exactly two lines:\n"
        "Category: [Plastic Waste OR Drainage/Water-logging OR Construction Debris OR Food Waste]\n"
        "Severity: [Low OR Medium OR Critical]"
    )
    contents = [prompt]
    if image_bytes:
        try:
            contents.append(Image.open(io.BytesIO(image_bytes)))
        except Exception:
            pass
    text, err = call_gemini(contents)
    cat, sev = keyword_classify(user_text)  # default fallback
    if text:
        for line in text.splitlines():
            if "Category:" in line:
                cat = line.split("Category:")[1].strip().strip("[]*")
            elif "Severity:" in line:
                sev = line.split("Severity:")[1].strip().strip("[]*")
    return cat, sev


def offline_reply(msg):
    t = msg.lower()
    if any(k in t for k in ["complain", "report", "shikayat", "register"]):
        return "Complaint darj karne ke liye Home par 'Register Complaint' dabayein, location chunein, issue likhein aur photo upload karein. Aapko ek Ticket ID milegi."
    if any(k in t for k in ["status", "track", "ticket"]):
        return "Complaint ka status dekhne ke liye Complain page par 'Track Complaint' tab me apni Ticket ID daalein."
    if any(k in t for k in ["ganga", "ghat", "river", "nadi"]):
        return "Ganga kinare kachra dikhe to 'Ganga Ghat Connecting Road' location chunke photo ke saath complaint darj karein. Plastic aur puja samagri ko alag-alag ek jagah jama karein, nadi me na daalein."
    if any(k in t for k in ["naali", "drain", "water", "paani", "jal"]):
        return "Water-logging ki complaint 'Mahendru / Shivpur Drainage Chokepoint' ya sambandhit location chunke karein. Naali me plastic ya kachra na daalein, isse block hoti hai."
    if any(k in t for k in ["kachra", "garbage", "trash", "waste", "plastic"]):
        return "Kachra hamesha dustbin me daalein, geela aur sookha alag rakhein. Jahan ढेर jama ho wahan ki photo ke saath complaint register karein, Nagar Nigam tak report pahunch jayegi."
    if any(k in t for k in ["metro", "ashok", "construction", "barrier"]):
        return "Ashok Rajpath metro work ke malba/barrier ki dikkat ke liye 'Ashok Rajpath Metro Work Barrier' location chunke complaint darj karein."
    return "Main NIT Patna ke aas-pass kachra, drainage aur safai issues me madad karta hu. Aap complaint kaise karein, status kaise dekhein ya kachra kaise handle karein, ye pooch sakte hain."


def ask_chatbot(msg):
    text, err = call_gemini(
        "You are Swachh Mitra AI, a helpful sanitation assistant for the NIT Patna area "
        "(Ashok Rajpath, NIT More, Mahendru, Ganga Ghat). Reply politely in Hinglish, "
        f"in at most 4 short sentences: {msg}"
    )
    return text if text else offline_reply(msg)


# ==========================================================
# 4. SESSION STATE
# ==========================================================
if "page" not in st.session_state:
    st.session_state.page = "Home"
if "admin_logged_in" not in st.session_state:
    st.session_state.admin_logged_in = False
if "chat_history" not in st.session_state:
    st.session_state.chat_history = [
        {
            "role": "assistant",
            "content": "Namaste! Safai issue par koi sawal hai?",
        }
    ]


def go(name):
    st.session_state.page = name


def back_button():
    if st.button("⬅ Back to Home", use_container_width=True):
        go("Home")
        st.rerun()


# ==========================================================
# 5. HOME
# ==========================================================
def page_home():
    st.title("🌱 SWACHH-NET")
    st.caption("AI-Powered Sanitation & Waste Assement for Campus Hygiene Hubs")
    st.divider()

    st.write("### Choose a Portal to Continue")
    st.write("")

    col1, col2 = st.columns(2, gap="large")
    with col1:
        with st.container(border=True):
            st.markdown("### 📢 Complain")
            st.write("NIT More, Ashok Rajpath ya Mahendru me kachra aur water-logging report karein.")
            st.write("")
            if st.button("Register Complaint ➔", use_container_width=True, type="primary"):
                go("Complain")
                st.rerun()
    with col2:
        with st.container(border=True):
            st.markdown("### 🛠 Admin")
            st.write("Authorized staff only: Review the Complaints and update status.")
            st.write("")
            if st.button("Open Admin Panel ➔", use_container_width=True):
                go("Admin")
                st.rerun()

    st.write("")
    st.write("")
    _, bot_col = st.columns([8, 2])
    with bot_col:
        with st.popover("💬 AI Chatbot", use_container_width=True):
            st.markdown("#### Swachh Mitra 🤖")
            st.caption("NIT Patna safai helpline AI")

            chat_box = st.container(height=300)
            with st.form("chat_form", clear_on_submit=True):
                q = st.text_input("Ask Here...", key="chat_q")
                sent = st.form_submit_button("Send ➤", use_container_width=True)

            if sent and q.strip():
                st.session_state.chat_history.append({"role": "user", "content": q.strip()})
                with st.spinner("Thinking..."):
                    reply = ask_chatbot(q.strip())
                st.session_state.chat_history.append({"role": "assistant", "content": reply})

            with chat_box:
                for m in st.session_state.chat_history:
                    with st.chat_message(m["role"]):
                        st.write(m["content"])


# ==========================================================
# 6. COMPLAIN
# ==========================================================
def page_complain():
    back_col, title_col = st.columns([2, 8])
    with back_col:
        back_button()
    with title_col:
        st.subheader("📢 Register Civic Grievance")

    tab1, tab2 = st.tabs(["📝 New Complaint", "🔍 Track Complaint"])

    with tab1:
        with st.container(border=True):
            left, right = st.columns(2, gap="medium")
            with left:
                location = st.selectbox("Select Spot / Location:", SITES)
                desc = st.text_area(
                    "Complaint Details (Hinglish/English):",
                    placeholder="Jaise: Ashok Rajpath par kachra faila hai aur badbu aa rahi hai...",
                    height=150,
                )
            with right:
                photo = st.file_uploader(
                    "Upload Problem Site Photo (Optional):", type=["jpg", "jpeg", "png"]
                )
                if photo:
                    st.image(photo, caption="Uploaded Spot Preview", width=260)

            st.write("")
            if st.button("Submit Grievance", type="primary", use_container_width=True):
                if not desc.strip():
                    st.warning("Kripya issue ka description zaroor likhein.")
                else:
                    with st.spinner("AI photo aur text categorize kar raha hai..."):
                        img_bytes = photo.getvalue() if photo else None
                        cat, sev = analyze_complaint(desc, img_bytes)
                        conn = get_db()
                        cur = conn.cursor()
                        now = datetime.now().strftime("%Y-%m-%d %H:%M")
                        cur.execute(
                            """INSERT INTO complaints
                               (timestamp, site_location, user_description, ai_category, ai_severity, status, photo)
                               VALUES (?, ?, ?, ?, ?, 'Pending', ?)""",
                            (now, location, desc.strip(), cat, sev, img_bytes),
                        )
                        conn.commit()
                        ticket_id = cur.lastrowid
                        conn.close()
                    st.success(f"Complaint successfully register ho gayi! Ticket ID: #{ticket_id}")
                    st.info(f"**AI Tagged Category:** {cat}  |  **Estimated Severity:** {sev}")

    with tab2:
        with st.container(border=True):
            tid = st.number_input("Apni Ticket ID daalein:", min_value=1, step=1, value=1)
            if st.button("Check Status", use_container_width=True):
                conn = get_db()
                row = conn.execute(
                    "SELECT timestamp, site_location, ai_category, ai_severity, status "
                    "FROM complaints WHERE id = ?",
                    (int(tid),),
                ).fetchone()
                conn.close()
                if row:
                    st.success(f"Ticket #{int(tid)} mil gayi")
                    st.write(f"**Date:** {row[0]}")
                    st.write(f"**Location:** {row[1]}")
                    st.write(f"**Category:** {row[2]}  |  **Severity:** {row[3]}")
                    st.write(f"**Status:** {row[4]}")
                else:
                    st.warning("Is ID ki koi complaint nahi mili.")


# ==========================================================
# 7. ADMIN
# ==========================================================
def page_admin():
    saved = get_admin_password()

    # Case 1: first-time setup
    if not saved:
        back_col, _ = st.columns([2, 8])
        with back_col:
            back_button()
        _, center, _ = st.columns([1, 2, 1])
        with center:
            with st.container(border=True):
                st.subheader("🔑 First-Time Admin Setup")
                st.info("Abhi tak koi admin password set nahi hai. Apna naya password banayein.")
                p1 = st.text_input("Enter New Password:", type="password", key="setup_p1")
                p2 = st.text_input("Confirm Password:", type="password", key="setup_p2")
                if st.button("Create Admin Password", type="primary", use_container_width=True):
                    if not p1.strip():
                        st.warning("Password blank nahi ho sakta.")
                    elif p1 != p2:
                        st.error("Passwords match nahi kar rahe hain!")
                    else:
                        set_admin_password(p1)
                        st.success("Admin password ban gaya! Ab login karein.")
                        st.rerun()
        return

    # Case 2: login
    if not st.session_state.admin_logged_in:
        back_col, _ = st.columns([2, 8])
        with back_col:
            back_button()
        _, center, _ = st.columns([1, 2, 1])
        with center:
            with st.container(border=True):
                st.subheader("🔒 Admin Access Login")
                st.caption("Yeh panel sirf Nagar Nigam / NITP Admin staff ke liye hai.")
                pwd = st.text_input("Enter Admin Password:", type="password", key="login_pwd")
                if st.button("Login to Dashboard", type="primary", use_container_width=True):
                    if hash_pwd(pwd) == saved:
                        st.session_state.admin_logged_in = True
                        st.rerun()
                    else:
                        st.error("Galat Password! Kripya sahi password enter karein.")
        return

    # Case 3: dashboard
    back_col, title_col, logout_col = st.columns([2, 6, 2])
    with back_col:
        back_button()
    with title_col:
        st.subheader("🛠 Municipal & Admin Dashboard")
    with logout_col:
        if st.button("🚪 Logout", use_container_width=True):
            st.session_state.admin_logged_in = False
            go("Home")
            st.rerun()

    conn = get_db()
    df = pd.read_sql_query(
        "SELECT id, timestamp, site_location, user_description, ai_category, ai_severity, status "
        "FROM complaints ORDER BY id DESC",
        conn,
    )
    conn.close()

    if df.empty:
        st.info("Abhi tak koi complaint register nahi hui hai.")
    else:
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total Tickets", len(df))
        m2.metric("Pending", int((df["status"] == "Pending").sum()))
        m3.metric("In Progress", int((df["status"] == "In Progress").sum()))
        m4.metric("Resolved", int((df["status"] == "Resolved").sum()))

        c1, c2 = st.columns(2)
        with c1:
            st.write("#### Complaints by Category")
            st.bar_chart(df["ai_category"].value_counts())
        with c2:
            st.write("#### Complaints by Location")
            st.bar_chart(df["site_location"].value_counts())

        st.write("#### Complaints Record")
        st.dataframe(df, use_container_width=True)

        with st.container(border=True):
            st.write("#### Update Complaint Status")
            a1, a2, a3 = st.columns(3)
            with a1:
                sel_id = st.selectbox("Ticket ID chunein:", df["id"].tolist(), key="sel_id")
            with a2:
                sel_status = st.selectbox(
                    "New Status:", ["Pending", "In Progress", "Resolved"], key="sel_status"
                )
            with a3:
                st.write("")
                st.write("")
                if st.button("Save Status", type="primary", use_container_width=True):
                    conn = get_db()
                    conn.execute(
                        "UPDATE complaints SET status = ? WHERE id = ?",
                        (sel_status, int(sel_id)),
                    )
                    conn.commit()
                    conn.close()
                    st.success(f"Ticket #{sel_id} status updated to {sel_status}!")
                    st.rerun()

        with st.expander("🖼 View Ticket Photo"):
            conn = get_db()
            row = conn.execute(
                "SELECT photo FROM complaints WHERE id = ?", (int(sel_id),)
            ).fetchone()
            conn.close()
            if row and row[0]:
                st.image(row[0], caption=f"Ticket #{sel_id}", width=300)
            else:
                st.caption("Is ticket me photo upload nahi hui.")

    with st.expander("⚙️ Admin Settings (Change Password)"):
        c_a, c_b = st.columns(2)
        with c_a:
            old_p = st.text_input("Current Password:", type="password", key="old_pwd")
        with c_b:
            new_p = st.text_input("New Password:", type="password", key="new_pwd")
        if st.button("Update Password"):
            if hash_pwd(old_p) != saved:
                st.error("Purana password galat hai.")
            elif not new_p.strip():
                st.warning("Naya password khali nahi ho sakta.")
            else:
                set_admin_password(new_p)
                st.success("Password successfully update ho gaya!")


# ==========================================================
# 8. ROUTER
# ==========================================================
if st.session_state.page == "Home":
    page_home()
elif st.session_state.page == "Complain":
    page_complain()
else:
    page_admin()