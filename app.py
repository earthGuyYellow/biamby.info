import csv
import logging
import os
from datetime import datetime

from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, request

# ── Environment ──────────────────────────────────────────────────────────────
load_dotenv()

# ── App Setup ─────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY")

# ── Logging ───────────────────────────────────────────────────────────────────
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    filename="logs/app.log",
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
# Also log to console during development
console = logging.StreamHandler()
console.setLevel(logging.INFO)
app.logger.addHandler(console)

# ── Config ────────────────────────────────────────────────────────────────────
DATA_DIR        = "data"
CONTACTS_CSV    = os.path.join(DATA_DIR, "contacts.csv")
CSV_HEADERS     = ["timestamp", "name", "email", "subject", "message"]
REQUIRED_FIELDS = ["name", "email", "subject", "message"]
ANTHROPIC_KEY   = os.environ.get("ANTHROPIC_API_KEY")

# Whitelist of pages the wildcard route is allowed to render
ALLOWED_PAGES = {
    "about.html",
    "projects.html",
    "thankyou.html",
    "404.html",
}

os.makedirs(DATA_DIR, exist_ok=True)


# ── Helpers ───────────────────────────────────────────────────────────────────
def validate_form(data: dict) -> tuple[bool, str]:
    """Return (is_valid, error_message)."""
    for field in REQUIRED_FIELDS:
        if not data.get(field, "").strip():
            return False, f"Missing required field: {field}"
    if "@" not in data.get("email", ""):
        return False, "Invalid email address."
    return True, ""


def write_to_csv(data: dict) -> None:
    """Append a sanitized, timestamped row to the contacts CSV."""
    file_exists = os.path.isfile(CONTACTS_CSV)
    with open(CONTACTS_CSV, mode="a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=CSV_HEADERS,
            delimiter=",",
            quotechar='"',
            quoting=csv.QUOTE_MINIMAL,
        )
        if not file_exists:
            writer.writeheader()           # Write headers on first run
        writer.writerow({
            "timestamp": datetime.now().isoformat(sep=" ", timespec="seconds"),
            "name":      data["name"].strip(),
            "email":     data["email"].strip().lower(),
            "subject":   data["subject"].strip(),
            "message":   data["message"].strip(),
        })


# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/")
def homepage():
    app.logger.info("Homepage visited")
    return render_template("index.html")


@app.route("/<string:page_name>")
def html_page(page_name):
    if page_name not in ALLOWED_PAGES:
        app.logger.warning("Blocked request for unlisted page: %s", page_name)
        return render_template("404.html"), 404
    return render_template(page_name)


@app.route("/submit_form", methods=["POST"])
def submit_form():
    if request.method != "POST":
        return "Method not allowed.", 405

    data = request.form.to_dict()

    # Validate input before touching the filesystem
    is_valid, error_msg = validate_form(data)
    if not is_valid:
        app.logger.warning("Form validation failed: %s", error_msg)
        return f"Form error: {error_msg}", 400

    try:
        write_to_csv(data)
        app.logger.info("Form submitted by %s", data.get("email"))
        return redirect("/thankyou.html")
    except (IOError, OSError) as e:
        app.logger.error("CSV write failed: %s", e)
        return "Could not save your message. Please try again later.", 500

# ── Error Handlers ────────────────────────────────────────────────────────────
@app.errorhandler(404)
def page_not_found(e):
    app.logger.warning("404: %s", request.path)
    return render_template("404.html"), 404


@app.errorhandler(500)
def internal_error(e):
    app.logger.error("500 error: %s", e)
    return render_template("500.html"), 500


# ── Entry Point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    app.run(debug=debug_mode)
