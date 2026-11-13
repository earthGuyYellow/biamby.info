from __future__ import annotations
import logging
import os
import re
from datetime import datetime
from functools import wraps

import pymysql
import pymysql.cursors
from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, request, Response, session
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# ── Environment ───────────────────────────────────────────────────────────────
load_dotenv()

# ── App Setup ─────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "change-me-in-production")

# ── Rate Limiter ──────────────────────────────────────────────────────────────
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["200 per day"],
    storage_uri="memory://",
)

# ── Logging ───────────────────────────────────────────────────────────────────
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    filename="logs/app.log",
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
console = logging.StreamHandler()
console.setLevel(logging.INFO)
app.logger.addHandler(console)

# ── Database Config ───────────────────────────────────────────────────────────
DB_CONFIG = {
    "host":        "biambyjoshua.mysql.pythonanywhere-services.com",
    "user":        "biambyjoshua",
    "password":    os.environ.get("MYSQL_PASSWORD"),
    "database":    "biambyjoshua$default",
    "charset":     "utf8mb4",
    "cursorclass": pymysql.cursors.DictCursor,
}

# ── Config ────────────────────────────────────────────────────────────────────
REQUIRED_FIELDS = ["name", "email", "subject", "message"]
ANTHROPIC_KEY   = os.environ.get("ANTHROPIC_API_KEY")
ADMIN_USER      = os.environ.get("ADMIN_USER")
ADMIN_PASSWORD  = os.environ.get("ADMIN_PASSWORD")
CAREER_API_TOKEN = os.environ.get("CAREER_API_TOKEN")

# Whitelist of pages the wildcard route is allowed to render
ALLOWED_PAGES = {
    "about.html",
    "projects.html",
    "thankyou.html",
    "404.html",
}


# ── Auth Helpers ──────────────────────────────────────────────────────────────
def check_basic_auth(username, password):
    """Validate HTTP Basic Auth credentials against environment variables."""
    return (
        username == ADMIN_USER and
        password == ADMIN_PASSWORD and
        ADMIN_USER is not None
    )


def require_basic_auth(f):
    """
    Decorator: protects a route with HTTP Basic Auth.
    Browser shows a native username/password dialog.
    Credentials are read from ADMIN_USER and ADMIN_PASSWORD in .env.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_basic_auth(auth.username, auth.password):
            app.logger.warning(
                "Unauthorised admin access attempt from %s",
                request.remote_addr
            )
            return Response(
                "Access denied. Valid credentials required.",
                401,
                {"WWW-Authenticate": 'Basic realm="Admin Area"'}
            )
        return f(*args, **kwargs)
    return decorated


def require_api_token(f):
    """
    Decorator: protects an API route with a bearer token.
    Caller must send header: X-API-Token: <CAREER_API_TOKEN>
    Token is read from CAREER_API_TOKEN in .env.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get("X-API-Token", "")
        if not CAREER_API_TOKEN or token != CAREER_API_TOKEN:
            app.logger.warning(
                "Invalid API token from %s",
                request.remote_addr
            )
            return jsonify({"error": "Unauthorized — invalid or missing API token."}), 401
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    """
    Decorator: protects a route with session-based login.
    Redirects unauthenticated users to /admin/login.
    Use this for future admin UI pages that need persistent login state.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin_logged_in"):
            app.logger.warning(
                "Unauthenticated admin session access from %s",
                request.remote_addr
            )
            return redirect("/admin/login")
        return f(*args, **kwargs)
    return decorated


# ── Database Helpers ──────────────────────────────────────────────────────────
def get_db():
    """Open and return a new database connection."""
    return pymysql.connect(**DB_CONFIG)


def init_db():
    """
    Create the contacts table if it doesn't exist yet.
    Called once on startup — safe to run repeatedly.
    """
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS contacts (
                    id        INT AUTO_INCREMENT PRIMARY KEY,
                    timestamp DATETIME     NOT NULL,
                    name      VARCHAR(120) NOT NULL,
                    email     VARCHAR(120) NOT NULL,
                    subject   VARCHAR(200) NOT NULL,
                    message   TEXT         NOT NULL
                )
            """)
        conn.commit()
        app.logger.info("Database initialised — contacts table ready.")
    except pymysql.MySQLError as e:
        app.logger.error("Database init failed: %s", e)
    finally:
        conn.close()


def write_to_db(data):
    """Insert a validated, sanitised contact form row into MySQL."""
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                INSERT INTO contacts (timestamp, name, email, subject, message)
                VALUES (%s, %s, %s, %s, %s)
            """, (
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                data["name"].strip(),
                data["email"].strip().lower(),
                data["subject"].strip(),
                data["message"].strip(),
            ))
        conn.commit()
    except pymysql.MySQLError as e:
        app.logger.error("DB write failed: %s", e)
        raise
    finally:
        conn.close()


# ── Form Validation ───────────────────────────────────────────────────────────
SPAM_PATTERNS = [
    "http://", "https://", "click here", "buy now", "free offer",
    "casino", "viagra", "crypto", "bitcoin", "earn money", "make money",
    "seo", "backlink", "adult", "xxx",
]

EMAIL_REGEX = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')


def validate_form(data):
    """Return (is_valid, error_message) with layered spam protection."""

    # ── Honeypot check ──
    if data.get("website", "").strip():
        app.logger.warning("Honeypot triggered — bot submission rejected.")
        return False, "Submission rejected."

    # ── Required fields ──
    for field in REQUIRED_FIELDS:
        if not data.get(field, "").strip():
            return False, f"Missing required field: {field}"

    # ── Length limits ──
    if len(data.get("name", "")) > 120:
        return False, "Name too long."
    if len(data.get("subject", "")) > 200:
        return False, "Subject too long."
    if len(data.get("message", "")) > 2000:
        return False, "Message too long."

    # ── Email format ──
    if not EMAIL_REGEX.match(data.get("email", "")):
        return False, "Invalid email address."

    # ── Spam pattern check ──
    message_lower = data.get("message", "").lower()
    subject_lower = data.get("subject", "").lower()
    for pattern in SPAM_PATTERNS:
        if pattern in message_lower or pattern in subject_lower:
            app.logger.warning("Spam pattern detected: %s", pattern)
            return False, "Message contains prohibited content."

    return True, ""


# ── Initialise DB on startup ──────────────────────────────────────────────────
init_db()


# ── Public Routes ─────────────────────────────────────────────────────────────
@app.route("/")
def homepage():
    app.logger.info("Homepage visited")
    return render_template("index_21.html")


@app.route("/<string:page_name>")
def html_page(page_name):
    if page_name not in ALLOWED_PAGES:
        app.logger.warning("Blocked request for unlisted page: %s", page_name)
        return render_template("404.html"), 404
    return render_template(page_name)


@app.route("/robots.txt")
def robots():
    return app.send_static_file("robots.txt")


@app.route("/submit_form", methods=["POST"])
@limiter.limit("5 per hour")
def submit_form():
    data = request.form.to_dict()

    is_valid, error_msg = validate_form(data)
    if not is_valid:
        app.logger.warning("Form validation failed: %s", error_msg)
        return f"Form error: {error_msg}", 400

    try:
        write_to_db(data)
        app.logger.info("Form submitted by %s", data.get("email"))
        return redirect("/thankyou.html")
    except pymysql.MySQLError as e:
        app.logger.error("Database write failed: %s", e)
        return "Could not save your message. Please try again later.", 500


# ── Career Agent API ──────────────────────────────────────────────────────────
@app.route("/api/career", methods=["POST"])
@require_api_token
def career_agent():
    """
    Receives a JSON payload from the Career Agent frontend and
    proxies it to the Anthropic API server-side so the API key
    is never exposed to the browser.

    Expected payload:
        { "action": "search_jobs" | "recommend_projects" | "update_resume",
          "context": { ...any relevant data... } }

    Requires header: X-API-Token: <CAREER_API_TOKEN>
    """
    if not ANTHROPIC_KEY:
        app.logger.error("ANTHROPIC_API_KEY not set in environment.")
        return jsonify({"error": "Service not configured."}), 503

    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({"error": "Invalid JSON payload."}), 400

    action = payload.get("action")
    app.logger.info("Career agent action requested: %s", action)

    # ── Placeholder — replace with real Anthropic SDK calls ──
    return jsonify({
        "status":  "scaffold_ready",
        "action":  action,
        "message": "Career Agent endpoint is live. Anthropic integration coming soon.",
    }), 200


# ── Admin Routes ──────────────────────────────────────────────────────────────
@app.route("/admin/contacts")
@require_basic_auth
def view_contacts():
    """
    View all contact form submissions as JSON.
    Protected by HTTP Basic Auth — credentials in .env.
    """
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM contacts ORDER BY timestamp DESC")
            rows = cursor.fetchall()
        return jsonify(rows), 200
    except pymysql.MySQLError as e:
        app.logger.error("Failed to fetch contacts: %s", e)
        return jsonify({"error": "Could not retrieve contacts."}), 500
    finally:
        conn.close()


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    """
    Session-based login for future admin UI pages.
    GET  → renders login form (admin_login.html — create when needed)
    POST → validates password, sets session flag, redirects to admin
    """
    if request.method == "POST":
        password = request.form.get("password", "")
        if password == ADMIN_PASSWORD and ADMIN_PASSWORD is not None:
            session["admin_logged_in"] = True
            app.logger.info("Admin login successful from %s", request.remote_addr)
            return redirect("/admin/contacts")
        app.logger.warning("Failed admin login from %s", request.remote_addr)
        return "Invalid password.", 401
    return "Admin login — POST with password field.", 200


@app.route("/admin/logout")
def admin_logout():
    """Clears the admin session."""
    session.pop("admin_logged_in", None)
    app.logger.info("Admin logout from %s", request.remote_addr)
    return redirect("/")


# ── Error Handlers ────────────────────────────────────────────────────────────
@app.errorhandler(404)
def page_not_found(e):
    app.logger.warning("404: %s", request.path)
    return render_template("404.html"), 404


@app.errorhandler(429)
def rate_limit_exceeded(e):
    app.logger.warning("Rate limit exceeded from %s", request.remote_addr)
    return "Too many requests. Please wait before trying again.", 429


@app.errorhandler(500)
def internal_error(e):
    app.logger.error("500 error: %s", e)
    return "An internal error occurred. Please try again later.", 500


# ── Entry Point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    app.run(debug=debug_mode)