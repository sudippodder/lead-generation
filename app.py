import streamlit as st
import sqlite3
import hashlib
import time
import pandas as pd
import json
import os
from dotenv import load_dotenv
from streamlit_cookies_manager import CookieManager
import job_lead_pipeline    
import job_lead
import job_lead_api
import job_lead_ranker
import job_lead_db
import sqlite
# --- Configuration ---
load_dotenv()

DATABASE_FILE = os.getenv("DATABASE_FILE", "auth_db.sqlite")
DEFAULT_USER_ROLE = os.getenv("DEFAULT_USER_ROLE", "user")
ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASS = os.getenv("ADMIN_PASS", "adminpass")

USER_COOKIE_KEY = "user_session_data"
COOKIE_EXPIRY_DAYS = 30

# cookies = CookieManager()

# if not cookies.ready():
#     st.info("Loading cookies...")
#     st.stop()

# --- Utility Functions for Hashing ---
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def check_password(password, hashed_password):
    return hash_password(password) == hashed_password

# --- Database Functions ---
def init_db():
    conn = sqlite3.connect(DATABASE_FILE)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            email TEXT,
            full_name TEXT,
            role TEXT DEFAULT 'user'
        )
    """)
    conn.commit()
    conn.close()

def add_user(username, password, email, full_name, role=DEFAULT_USER_ROLE):
    hashed_password = hash_password(password)
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        c = conn.cursor()
        c.execute("INSERT INTO users (username, password_hash, email, full_name, role) VALUES (?, ?, ?, ?, ?)",
                  (username, hashed_password, email, full_name, role))
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        return False
    except Exception as e:
        st.error(f"Database error during registration: {e}")
        return False

def verify_credentials(username, password):
    conn = sqlite3.connect(DATABASE_FILE)
    c = conn.cursor()
    c.execute("SELECT id, password_hash, email, full_name, role FROM users WHERE username = ?", (username,))
    result = c.fetchone()
    conn.close()

    if result:
        user_id, password_hash, email, full_name, role = result
        if check_password(password, password_hash):
            return {"id": user_id, "username": username, "email": email, "full_name": full_name, "role": role}
    return None

def get_all_users():
    conn = sqlite3.connect(DATABASE_FILE)
    c = conn.cursor()
    c.execute("SELECT id, username, email, full_name, role FROM users")
    users = c.fetchall()
    conn.close()
    return [{"id": row[0], "username": row[1], "email": row[2], "full_name": row[3], "role": row[4]} for row in users]

# --- Session State Management ---
def initialize_session_state():
    #user_info_json = cookies.get("auth_user")
    user_info_json = []
    persistent_info = {}
    if user_info_json:
        try:
            persistent_info = json.loads(user_info_json)
        except json.JSONDecodeError:
            persistent_info = {}

    if 'logged_in' not in st.session_state:
        st.session_state['logged_in'] = bool(persistent_info)
    if 'user_info' not in st.session_state:
        st.session_state['user_info'] = persistent_info

    if 'page' not in st.session_state:
        st.session_state['page'] = 'dashboard' if st.session_state['logged_in'] else 'login'

    # Check for demo admin user
    if not verify_credentials(ADMIN_USER, ADMIN_PASS):
        if add_user(ADMIN_USER, ADMIN_PASS, "admin@example.com", "System Admin", "admin"):
            pass

def login_user(username, password, remember=False):
    user_info = verify_credentials(username, password)
    if user_info:
        st.session_state['logged_in'] = True
        st.session_state['user_info'] = user_info
        st.session_state['page'] = 'dashboard'

        if remember:
            st.session_state["remember_login"] = True

        cookies["auth_user"] = json.dumps(user_info)
        cookies.save()
        st.success(f"Login successful! Welcome, {user_info['username']}.")
        time.sleep(1)
        st.rerun()
    else:
        st.error("Invalid username or password.")

def logout_user():
    cookies["auth_user"] = ""
    cookies.save()

    keys_to_delete = list(st.session_state.keys())
    for key in keys_to_delete:
        del st.session_state[key]
        
    st.session_state['logged_in'] = False
    st.session_state['user_info'] = None
    st.session_state["remember_login"] = False
    st.session_state['page'] = 'login'
    st.toast("Logged out successfully!", icon="👋")
    time.sleep(0.5)
    st.rerun()

# --- Page Rendering Functions ---
def show_login_page():
    if st.session_state.get("remember_login") and st.session_state.get("username"):
        st.session_state["logged_in"] = True
        st.rerun()

    st.title("🔐 Secure Login Portal")
    st.markdown("---")

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Sign In")
        with st.form("login_form"):
            login_username = st.text_input("Username", key="login_user")
            login_password = st.text_input("Password", type="password", key="login_pass")
            remember_me = st.checkbox("Remember Me", key="remember_me")
            login_button = st.form_submit_button("Log In", type="primary")

            if login_button:
                login_user(login_username, login_password, remember_me)

    with col2:
        st.subheader("New User Registration")
        with st.form("register_form"):
            reg_username = st.text_input("Choose Username*", key="reg_user")
            reg_password = st.text_input("Set Password*", type="password", key="reg_pass")
            reg_name = st.text_input("Full Name", key="reg_name")
            reg_email = st.text_input("Email", key="reg_email")
            register_button = st.form_submit_button("Register Account", type="secondary")

            if register_button:
                if reg_username and reg_password:
                    if add_user(reg_username, reg_password, reg_email, reg_name):
                        st.success("Registration successful! You can now log in.")
                    else:
                        st.error("Registration failed. Username might already be taken.")
                else:
                    st.error("Username and Password are required.")


def show_dashboard():
    user = st.session_state['user_info']
    st.title("🏠 Dashboard")
    st.header(f"Welcome back, {user['full_name'] or user['username']}!", divider='blue')

    st.markdown(f"""
        <div style="padding: 15px; border-radius: 10px; border-left: 5px solid #00bcd4;">
            <p style="font-size: 1.1em; margin: 0;">
                Your access role is: <strong>{user['role'].capitalize()}</strong>.
            </p>
            <p style="font-size: 0.9em; margin-top: 5px;">
                As a {user['role']}, you have access to specific modules in the sidebar.
            </p>
        </div>
    """, unsafe_allow_html=True)
    st.markdown("---")

def show_profile():
    user = st.session_state['user_info']
    st.title("👤 User Profile")

    st.markdown(f"""
        <div style="border: 1px solid #ddd; padding: 25px; border-radius: 12px;">
            <p><strong>Database ID:</strong> <code>{user['id']}</code></p>
            <p><strong>Username:</strong> <code>{user['username']}</code></p>
            <p><strong>Full Name:</strong> {user['full_name'] or 'Not Provided'}</p>
            <p><strong>Email:</strong> {user['email'] or 'Not Provided'}</p>
            <p><strong>Access Role:</strong> <span style="font-weight: bold; color: {'#d9534f' if user['role'] == 'admin' else '#5cb85c'};">{user['role'].capitalize()}</span></p>
        </div>
    """, unsafe_allow_html=True)
    st.markdown("---")
    st.info("You can customize this page to allow users to update their profile information.")

def show_admin_page():
    user = st.session_state['user_info']

    if user['role'] != 'admin':
        st.error("🛑 ACCESS DENIED: You do not have administrator privileges.")
        st.warning("Only users with the 'admin' role can view this page.")
        return

    st.title("🛠️ Admin Panel")
    st.header("User Management", divider='red')
    st.info("A live list of all registered users in the database.")

    users_data = get_all_users()

    if users_data:
        df = pd.DataFrame(users_data)
        df = df.set_index('id')
        st.dataframe(df, use_container_width=True)
    else:
        st.warning("No users found in the database.")


# --- Main Application Loop ---
def main():
    st.set_page_config(
        page_title="Auth Template",
        layout="wide",
        initial_sidebar_state="expanded"
    )

    init_db()
    initialize_session_state()

    with st.sidebar:
        st.header("Job Leads")
        if "logged_in" in st.session_state and st.session_state["logged_in"]:
            user = st.session_state['user_info']
            st.success(f"Logged in as: {user['username']}")

            st.markdown("### Navigation")
            
            if user['role'] == 'admin':
                user_pages = ['Dashboard', 'Profile', 'Admin Panel','Job Lead Pipeline(Serp API-Free Approach)','Job Lead Pipeline(Free API)','Job Lead Ranker','Job Lead DB']
            else:
                user_pages = ['Dashboard', 'Profile','Job Lead Pipeline(Serp API-Free Approach)','Job Lead Pipeline(Free API)','Job Lead Ranker','Job Lead DB']

            current_page = st.session_state.get("page", "dashboard")
            if current_page == "admin":
                display_current_page = "Admin Panel"
            else:
                display_current_page = current_page.title()

            if display_current_page not in user_pages:
                display_current_page = 'Dashboard'
                
            current_index = user_pages.index(display_current_page)
            
            current_selection = st.radio(
                "Go to:",
                user_pages,
                index=current_index,
                key='nav_radio_user'
            )
            
            if current_selection == 'Admin Panel':
                st.session_state['page'] = 'admin'
            else:
                st.session_state['page'] = current_selection.lower()

            st.markdown("---")
            if st.button("Logout", use_container_width=True, type="primary"):
                logout_user()

        else:
            #st.info("Please sign in or register to access the application features.")
            if st.button("Search", use_container_width=True):
                 st.session_state['page'] = 'login'


    if "logged_in" in st.session_state and st.session_state["logged_in"] == True:
        page = st.session_state['page']
        #st.write(page)
        if page == 'dashboard':
            show_dashboard()
        elif page == 'profile':
            show_profile()
        elif page == 'admin':
            show_admin_page()
        elif page == 'job lead pipeline(serp api-free approach)':
            job_lead_api.run()
        elif page == 'job lead pipeline(free api)':
            job_lead.run()
        elif page == 'job lead ranker':
            job_lead_ranker.run()
        elif page == 'job lead db':
            job_lead_db.run()
        else:
            show_dashboard()
    else:
        #show_login_page()
        job_lead_db.run()
        #sqlite.main() 


if __name__ == '__main__':
    main()
