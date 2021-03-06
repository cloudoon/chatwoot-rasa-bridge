import json

import requests
import os
from flask import Flask, request
import jwt

rasa_url = os.environ.get("RASA_URL")
chatwoot_url = os.environ.get("CHATWOOT_URL")
chatwoot_bot_token = os.environ.get("CHATWOOT_BOT_TOKEN")
rasa_channel = os.environ.get("RASA_CHANNEL")
rasa_jwt_token_secret = os.environ.get("RASA_JWT_TOKEN_SECRET")


def extract_bot_response(response_json):
    response_button_list = []
    if type(response_json) == list:
        response_text_list = []
        for response_object in response_json:
            if response_object.get("text"):
                response_text_list.append(response_object.get("text"))
            if response_object.get("buttons"):
                buttons_object = response_object.get("buttons")
                for button in buttons_object:
                    response_button_list.append(
                        {
                            "title": button.get("title"),
                            "value": button.get("payload"),
                        }
                    )
        response_text = "\n".join(response_text_list)
    else:
        response_text = response_json.get("message")
    return response_text, response_button_list


def send_to_bot(sender, message, conversation_id):
    username = f"{sender}_{conversation_id}"
    data = {"sender": username, "message": message}
    jwt_payload = {"user": {"username": username, "role": "guest"}}
    rasa_jwt_token = jwt.encode(jwt_payload, rasa_jwt_token_secret, algorithm="HS256")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {rasa_jwt_token}",
    }

    r = requests.post(
        f"{rasa_url}/webhooks/{rasa_channel}/webhook",
        json=data,
        headers=headers,
    )
    response_json = r.json()
    response_text, response_button_list = extract_bot_response(response_json)
    return response_text, response_button_list


def send_to_chatwoot(account, conversation, message, response_button_list):
    data = {"content": message}
    if len(response_button_list) > 0:
        data["content_type"] = "input_select"
        data["content_attributes"] = {
            "items": response_button_list,
        }
    url = f"{chatwoot_url}/api/v1/accounts/{account}/conversations/{conversation}/messages"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "api_access_token": f"{chatwoot_bot_token}",
    }

    r = requests.post(url, json=data, headers=headers)
    return r.json()


app = Flask(__name__)


@app.route("/", methods=["POST"])
def rasa():
    data = request.get_json()
    message_type = data.get("message_type")
    message = data.get("content")
    conversation = data.get("conversation", {})
    conversation_id = conversation.get("id")
    contact = data.get("sender", {}).get("id")
    account = data.get("account").get("id")
    create_message = {}
    conversation_status = data.get("conversation", {}).get("status")
    allow_bot_mention = os.getenv("ALLOW_BOT_MENTION", "False")
    bot_name = os.getenv("BOT_NAME")
    is_bot_mention = False
    if (
        allow_bot_mention == "True"
        and message_type == "outgoing"
        and message.startswith(f"@{bot_name}")
    ):
        contact = data["conversation"]["contact_inbox"]["contact_id"]
        message = message.replace(f"@{bot_name}", "")
        is_bot_mention = True
    if data.get("event") == "message_updated":
        contact = data["conversation"]["contact_inbox"]["contact_id"]
        content_attributes = data["content_attributes"]
        submitted_values = content_attributes.get("submitted_values", [])
        submitted_values_text_list = [
            submitted_text.get("value") for submitted_text in submitted_values
        ]
        message = "\n".join(submitted_values_text_list)

    if (
        message_type == "incoming"
        or data.get("event") == "message_updated"
        or is_bot_mention
    ) and conversation_status == "pending":
        text_response, response_button_list = send_to_bot(
            contact, message, conversation_id
        )
        create_message = send_to_chatwoot(
            account, conversation_id, text_response, response_button_list
        )
    return create_message


if __name__ == "__main__":
    app.run(debug=1)
