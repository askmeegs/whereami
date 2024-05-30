import os

import requests
import streamlit as st
import secrets


# helper function - send prompt to backend
def get_chat_response(user_prompt: str, messages: []) -> str:
    request = {"guess": user_prompt, "session_id": st.session_state.token}
    response = requests.post(backend_url + "/guess", json=request)
    if response.status_code != 200:
        raise Exception("Error getting chat response: {}".format(response.text))
    return response.json()["response"]


def fetch_streetview():  # -> str:
    request = {"guess": "", "session_id": st.session_state.token}
    response = requests.post(backend_url + "/streetview", json=request)
    if response.status_code != 200:
        raise Exception("Error getting streetview image: {}".format(response.text))
    return response.json()["image_url"]


st.set_page_config(
    page_title="whereami?",
    page_icon="images/favicon.png",
    initial_sidebar_state="auto",
)
backend_url = os.environ.get("BACKEND_URL", "http://localhost:8000")


def reset_game():
    print("enter reset function.")
    new_token = secrets.token_urlsafe(16)
    print("⚠️ regenerating new token: {}".format(new_token))
    st.session_state.token = new_token
    print("🗺️ resetting streetview image.")
    st.session_state.image_url = fetch_streetview()
    st.session_state.messages = []


col1, col2 = st.columns(2)

with col1:
    st.title("📍 where am i?")

    reset_button = st.button(label="reset game")

    if reset_button:
        reset_game()
    if "messages" not in st.session_state:
        st.session_state.messages = []

    if prompt := st.chat_input("your guess here!"):
        st.session_state.messages.append({"role": "user", "content": prompt})
        full_response = ""
        for response in get_chat_response(prompt, st.session_state.messages[:-1]):
            full_response += response
        st.session_state.messages.append(
            {"role": "assistant", "content": full_response}
        )
    i = 1
    for message in st.session_state.messages:
        if message["role"] == "assistant":
            if "game complete" in message["content"].lower():
                st.markdown("{}".format(message["content"]))
            else:
                st.markdown("✨ **Clue #{}:** {}".format(i, message["content"]))
                i += 1

with col2:
    streetview_html = """
        <img style="padding:2px;border:thin solid #92ff92;" src="{}" width=600>
    """.format(
        st.session_state.image_url
    )
    st.markdown(streetview_html, unsafe_allow_html=True)
    st.subheader("🗺️ How to play:")
    st.markdown(
        "Guess the location of the street view image shown. You can start by asking for clues, like `is the location in Southeast Asia?`"
    )
    st.markdown(
        "To make your final guess, type `FINAL GUESS:` followed by your guess. the final guess can be an exact address, the name of a city, or a landmark. Then, the assistant will tell you how close you are to the correct location."
    )
    st.markdown("Refresh this page to start a new game.")


st.markdown(
    "made with 💚 by [megan](https://github.com/askmeegs). powered by [gemini flash](https://deepmind.google/technologies/gemini/flash/) and the [google maps api](https://developers.google.com/maps/documentation/)"
)
