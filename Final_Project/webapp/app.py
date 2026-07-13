import os
from flask import Flask, request, jsonify, render_template

import engine

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"error": "Please enter a question."}), 400
    if not os.environ.get("GROQ_API_KEY"):
        return jsonify({"error": "No GROQ_API_KEY set on the server. "
                                 "Set it and restart, then try again."}), 503
    try:
        return jsonify(engine.answer_query(question))
    except Exception as e:  
        return jsonify({"error": f"The assistant hit an error: {e}"}), 500


@app.route("/api/policies")
def policies():
    return jsonify(engine.get_policies())


@app.route("/api/history")
def history():
    return jsonify(engine.get_history())


@app.route("/api/stats")
def stats():
    try:
        return jsonify(engine.get_stats())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/health")
def health():
    return jsonify({"ok": True, "api_key_set": bool(os.environ.get("GROQ_API_KEY")),
                    "model": engine.GROQ_MODEL})


if __name__ == "__main__":
    print("MedQuery AI - open http://localhost:5000")
    app.run(host="0.0.0.0", port=5000, debug=True)
