import base64
import io
import time
import uuid

import requests
import os

from elasticapm.contrib.flask import ElasticAPM
from flask import Flask, request
import jwt

from utils import get_text_from_image, get_text_from_pdf

rasa_url = os.getenv("RASA_URL")
chatwoot_url = os.getenv("CHATWOOT_URL")
chatwoot_bot_token = os.getenv("CHATWOOT_BOT_TOKEN")
rasa_channel = os.getenv("RASA_CHANNEL")
rasa_jwt_token_secret = os.getenv("RASA_JWT_TOKEN_SECRET")
csat_message = os.getenv("CHATWOOT_CSAT_MESSAGE", "Please rate the conversation")
max_message_characters = int(os.getenv("MAX_MESSAGE_CHARACTERS", "420"))
try:
    enable_csat = int(os.getenv("CHATWOOT_ENABLE_CSAT", "0"))
except ValueError:
    enable_csat = 0
try:
    typing_status_enabled = int(os.getenv("CHATWOOT_TYPING_STATUS_ENABLED", "0"))
except ValueError:
    typing_status_enabled = 0
try:
    BOT_RESPONSE_RETRY_COUNT = int(os.getenv("BOT_RESPONSE_RETRY_COUNT", "3"))
except ValueError:
    BOT_RESPONSE_RETRY_COUNT = 3
try:
    SLEEP_SECONDS_BETWEEN_RETRIES = int(os.getenv("SLEEP_SECONDS_BETWEEN_RETRIES", "5"))
except ValueError:
    SLEEP_SECONDS_BETWEEN_RETRIES = 5


def get_image_file(image_url) -> io.BytesIO:
    """
    Get image file from url
    :param image_url: image url
    :return: image file
    """
    if image_url.startswith("data:image/jpg;base64,"):
        image_content = image_url.replace("data:image/jpg;base64,", "")
        image_content = base64.b64decode(image_content)
    else:
        image_content = requests.get(image_url).content
    image_file = io.BytesIO(image_content)
    return image_file


def extract_bot_response(response_json):
    """
    Extract bot response
    :param response_json: response json
    :return: (response_text, response_button_list, custom_json_response, image_file)
    """
    response_button_list = []
    custom_json_response = {}
    image_file = None
    if type(response_json) is list:
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
            if response_object.get("custom"):
                custom_json_response = response_object.get("custom")
            if response_object.get("image"):
                image_url = response_object.get("image")
                image_file = get_image_file(image_url)
        response_text = "\n".join(response_text_list)
    else:
        response_text = response_json.get("message")
    is_empty_response = (
        not response_text
        and not response_button_list
        and not custom_json_response
        and not image_file
    )
    return (
        response_text,
        response_button_list,
        custom_json_response,
        image_file,
        is_empty_response,
    )


def send_to_bot(sender, message, conversation_id):
    """
    Send message to bot
    :param sender: sender id
    :param message: message to be sent
    :param conversation_id: conversation id
    :return: (response_text, response_button_list, custom_json_response, image_file)
    """
    message = message[:max_message_characters]
    username = f"{sender}_{conversation_id}"
    data = {"sender": username, "message": message}
    jwt_payload = {"user": {"username": username, "role": "guest"}}
    rasa_jwt_token = jwt.encode(jwt_payload, rasa_jwt_token_secret, algorithm="HS256")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {rasa_jwt_token}",
    }
    response_button_list = []
    custom_json_response = {}
    image_file = None
    response_text = ""
    is_empty_response = False

    for _ in range(BOT_RESPONSE_RETRY_COUNT):
        response = requests.post(
            f"{rasa_url}/webhooks/{rasa_channel}/webhook",
            json=data,
            headers=headers,
        )
        if response.status_code == 503:
            time.sleep(SLEEP_SECONDS_BETWEEN_RETRIES)
            continue
        response_json = response.json()
        (
            response_text,
            response_button_list,
            custom_json_response,
            image_file,
            is_empty_response,
        ) = extract_bot_response(response_json)

        if not is_empty_response:
            break
        time.sleep(SLEEP_SECONDS_BETWEEN_RETRIES)

    return (
        response_text,
        response_button_list,
        custom_json_response,
        image_file,
        is_empty_response,
    )


def send_to_chatwoot(
    account,
    conversation,
    message,
    response_button_list,
    custom_json_response,
    image_file,
    is_private=False,
    send_csat=False,
):
    """
    Send message to chatwoot
    :param account: account id
    :param conversation: conversation id
    :param message: message to be sent
    :param response_button_list: list of buttons to be sent
    :param custom_json_response: custom json response
    :param image_file: image file
    :param is_private: is the message private
    :param send_csat: send csat message
    """
    data = {"content": message, "private": is_private}
    if len(response_button_list) > 0:
        data["content_type"] = "input_select"
        data["content_attributes"] = {
            "items": response_button_list,
        }
    if len(custom_json_response.keys()) > 0:
        data["content_type"] = custom_json_response.get("type")
        data["content_attributes"] = {
            "items": custom_json_response.get("elements"),
        }
    if send_csat:
        data["content_type"] = "input_csat"
        data["content"] = csat_message
    url = f"{chatwoot_url}/api/v1/accounts/{account}/conversations/{conversation}/messages"
    if image_file:
        if data.get("private") is False:
            data.pop("private")
        image_name = f"{uuid.uuid4().hex}.jpg"
        files = [("attachments[]", (image_name, image_file, "image/jpg"))]
        headers = {
            "Accept": "application/json",
            "api_access_token": f"{chatwoot_bot_token}",
        }
        r = requests.post(url, data=data, files=files, headers=headers)
    else:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "api_access_token": f"{chatwoot_bot_token}",
        }

        r = requests.post(url, json=data, headers=headers)
    return r.json()


def toggle_typing_status(account, conversation, status):
    """
    Toggle typing status of the bot
    :param account: account id
    :param conversation: conversation id
    :param status: typing status
    """
    url = f"{chatwoot_url}/api/v1/accounts/{account}/conversations/{conversation}/toggle_typing_status"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "api_access_token": f"{chatwoot_bot_token}",
    }
    data = {"status": status}
    r = requests.post(url, json=data, headers=headers)
    return r.json()


def get_message_attachments(conversation):
    """
    Extract attachments urls from conversation messages
    :param conversation: conversation object
    :return:
    """
    attachments = []
    for message in conversation.get("messages", []):
        for attachment in message.get("attachments", []):
            attachments.append(attachment.get("data_url"))
    return attachments


app = Flask(__name__)
app.config["ELASTIC_APM"] = {
    "SERVICE_NAME": os.getenv("ELASTIC_APM_SERVICE_NAME", "chatwoot-rasa"),
    "SERVER_URL": os.getenv("ELASTIC_APM_SERVER_URL"),
    "ENVIRONMENT": os.getenv("ELASTIC_APM_ENVIRONMENT", "production"),
}
apm = ElasticAPM(app)


@app.route("/health-check/", methods=["GET"])
def health_check():
    """Health check endpoint"""
    return "OK"


@app.route("/", methods=["POST"])
def rasa():
    """Rasa endpoint"""
    data = request.get_json()
    message_type = data.get("message_type")
    is_private = data.get("private")
    message = data.get("content")
    conversation = data.get("conversation", {})
    conversation_id = conversation.get("id")
    sender_id = (data.get("sender") or {}).get("id")
    content_type = data.get("content_type")
    attachments_urls = get_message_attachments(conversation)
    if (
        message is None
        and data.get("event") == "message_created"
        and len(attachments_urls) > 0
    ):
        message = ""
        for attachment_url in attachments_urls:
            if attachment_url.endswith(".pdf"):
                message += get_text_from_pdf(attachment_url)
            else:
                message += get_text_from_image(attachment_url)
    contact = sender_id
    if data.get("account"):
        account = data.get("account").get("id")
    else:
        account = data.get("messages", [{}])[0].get("account_id")
    create_message = {}
    if data.get("conversation"):
        conversation_status = data.get("conversation").get("status")
    else:
        conversation_status = data.get("status")
        conversation_id = data.get("messages", [{}])[0].get("conversation_id")
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
    if data.get("event") == "message_updated" and content_type != "input_csat":
        contact = data["conversation"]["contact_inbox"]["contact_id"]
        content_attributes = data["content_attributes"]
        submitted_values = content_attributes.get("submitted_values", [])
        submitted_values_text_list = [
            submitted_text.get("value") for submitted_text in submitted_values
        ]
        message = "\n".join(map(str, submitted_values_text_list))

    if (
        (message_type == "incoming" or data.get("event") == "message_updated")
        and conversation_status == "pending"
        and content_type != "input_csat"
        and message not in ["", None]
    ) or is_bot_mention:
        if is_bot_mention and conversation_status == "pending":
            is_private = False
        elif is_bot_mention:
            contact = f"agent-{sender_id}"
        if typing_status_enabled:
            toggle_typing_status(account, conversation_id, "on")
        (
            text_response,
            response_button_list,
            custom_json_response,
            image_file,
            is_empty_response,
        ) = send_to_bot(contact, message, conversation_id)
        create_message = send_to_chatwoot(
            account,
            conversation_id,
            text_response,
            response_button_list,
            custom_json_response,
            image_file,
            is_private=is_private,
        )
        if typing_status_enabled:
            toggle_typing_status(account, conversation_id, "off")
        toggle_typing_status(account, conversation_id, "off")
    elif conversation_status == "resolved" and message_type is None and enable_csat:
        create_message = send_to_chatwoot(
            account, conversation_id, None, [], {}, None, send_csat=True
        )
    return create_message


if __name__ == "__main__":
    app.run(debug=1)
