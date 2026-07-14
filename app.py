from flask import Flask
from database import init_db, cleanup_old_completed_lists
from routes import register_routes

app = Flask(__name__)
app.secret_key = "change-this-later"

register_routes(app)

if __name__ == "__main__":
    init_db()
    cleanup_old_completed_lists(days_old=30)
    app.run(host="0.0.0.0", port=5001, debug=True)