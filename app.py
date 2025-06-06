from quart import Quart, request, jsonify
from chatbot import route_user_query

app = Quart(__name__)

@app.route("/query", methods=["POST"])
async def query():
    data = await request.get_json()
    user_input = data.get("message")

    if not user_input:
        return jsonify({"error": "No input provided"}), 400

    result = await route_user_query(user_input)
    return jsonify(result), 200

if __name__ == "__main__":
    app.run(debug=True)
