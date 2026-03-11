from __future__ import annotations
import logging
import os
from datetime import datetime

import pymysql
import pymysql.cursors
from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, request

# ── Environment ───────────────────────────────────────────────────────────────
load_dotenv()

# ── App Setup ─────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "change me")

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
    "host":     "biambyjoshua.mysql.pythonanywhere-services.com",
    "user":     "biambyjoshua",
    "password": os.environ.get("MYSQL_PASSWORD"),
    "database": "biambyjoshua$default",
    "charset":  "utf8mb4",
    "cursorclass": pymysql.cursors.DictCursor,
}

# ── Config ────────────────────────────────────────────────────────────────────
REQUIRED_FIELDS = ["name", "email", "subject", "message"]
ANTHROPIC_KEY   = os.environ.get("ANTHROPIC_API_KEY")

# Whitelist of pages the wildcard route is allowed to render
ALLOWED_PAGES = {
    "about.html",
    "projects.html",
    "thankyou.html",
    "404.html",
}


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
def validate_form(data):
    """Return (is_valid, error_message)."""
    for field in REQUIRED_FIELDS:
        if not data.get(field, "").strip():
            return False, f"Missing required field: {field}"
    if "@" not in data.get("email", ""):
        return False, "Invalid email address."
    return True, ""


# ── Initialise DB on startup ──────────────────────────────────────────────────
init_db()


# ── Routes ────────────────────────────────────────────────────────────────────
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


@app.route("/submit_form", methods=["POST"])
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


# ── Career Agent API (scaffold — ready for Claude integration) ────────────────
@app.route("/api/career", methods=["POST"])
def career_agent():
    """
    Receives a JSON payload from the Career Agent frontend and
    proxies it to the Anthropic API server-side so the API key
    is never exposed to the browser.

    Expected payload:
        { "action": "search_jobs" | "recommend_projects" | "update_resume",
          "context": { ...any relevant data... } }

    TODO: Wire up Anthropic SDK calls here once the agent is built.
    """
    if not ANTHROPIC_KEY:
        app.logger.error("ANTHROPIC_API_KEY not set in environment.")
        return jsonify({"error": "Service not configured."}), 503

    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({"error": "Invalid JSON payload."}), 400

    action = payload.get("action")
    app.logger.info("Career agent action requested: %s", action)

    return jsonify({
        "status":  "scaffold_ready",
        "action":  action,
        "message": "Career Agent endpoint is live. Anthropic integration coming soon.",
    }), 200


# ── Contacts Viewer (admin only — protect this route in production) ───────────
@app.route("/admin/contacts")
def view_contacts():
    """
    Quick way to view all contact form submissions in the browser.
    TODO: Add authentication before going to production.
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


# ── Error Handlers ────────────────────────────────────────────────────────────
@app.errorhandler(404)
def page_not_found(e):
    app.logger.warning("404: %s", request.path)
    return render_template("404.html"), 404


@app.errorhandler(500)
def internal_error(e):
    app.logger.error("500 error: %s", e)
    return "An internal error occurred. Please try again later.", 500


# ── Entry Point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    app.run(debug=debug_mode)