from typing import Union
import os
from fastapi import FastAPI
from pydantic import BaseModel
import google.generativeai as genai
import requests
import json
from geopy.distance import geodesic
import time

app = FastAPI()


class Guess(BaseModel):
    guess: str
    session_id: str


class Place(BaseModel):
    name: str
    address: str
    lat: float
    long: float


# store chat history in-memory. maps session_id -> chat history {"guess" -> "clue"}.
chat_history = {}

# map session_id --> Place
answers = {}
google_maps_api_key = os.environ.get("GOOGLE_MAPS_API_KEY", "YOUR_API_KEY")

# configure Gemini Developer API
gemini_api_key = os.environ.get("GEMINI_DEV_API_KEY", "YOUR_API_KEY")
genai.configure(api_key=gemini_api_key)


# ------------- HEALTH --------------------------
@app.get("/")
def index():
    return {"response": "🗺️ hello, this is the whereami backend!"}


@app.get("/health")
def health():
    return {"status": "ok"}


# ------------- ON PAGE LOAD --------------------------
# Pick the streetview image to render, ie. "pick the answer."
@app.post("/streetview")
def streetview(g: Guess):
    # 1) gemini picks a random location in the world.
    place_nat = choose_random_location()

    print("\n\n🌐 New session: {}, Gemini chose: {}".format(g.session_id, place_nat))

    # 2) call Google Maps API to return a list of exact places near that location.
    # https://developers.google.com/maps/documentation/places/web-service/text-search
    p = nat_language_to_place(place_nat)
    print("📍 The answer is: {}".format(p))
    answers[g.session_id] = p

    # 3) render the streetview image.
    return {
        "image_url": """https://maps.googleapis.com/maps/api/streetview?size=1200x1200&location={},{}
&fov=80&heading=70&pitch=0&key={}""".format(
            p.lat, p.long, google_maps_api_key
        ),
    }


# ------------- GAMEPLAY --------------------------
@app.post("/guess")
def process_guess(g: Guess):
    txt = g.guess
    r = ""
    if "final guess" in txt.lower():
        d, r = process_final_guess(g)
        r = "### 🏁 Game complete!\nThe correct answer was: {}, {}.\n\nYour final guess was off by: {} miles.\n\n{}".format(
            answers[g.session_id].name, answers[g.session_id].address, d, r
        )
    else:
        r = process_intermediate_guess(g)
    return {"response": "{}".format(r)}


# ------------- GAMEPLAY HELPER FUNCTIONS --------------------------


# on startup, choose a random place in the world...
def choose_random_location():
    model = genai.GenerativeModel(
        model_name="gemini-1.5-flash-001",
        system_instruction=[
            """"
            You are facilitating a geography guessing game. Your job is to pick a random location in the world. 
            Here are the categories of location you could choose from:
            - A famous landmark (eg. Great Pyramids of Giza)
            - A city (eg. Lagos, Nigeria)
            - A national park (eg. Yellowstone National Park, USA)
            - A statue (eg. Statue of Liberty, New York, USA)
            - A bridge (eg. Danyang–Kunshan Grand Bridge, China)
            - A building (eg. Burj Khalifa, Dubai) 
            - A mountain (eg. Mount Everest, Nepal)
            - An island (eg. Easter Island, Chile)
            - A desert (eg. Sahara Desert, Africa)
            - A lake (eg. Lake Baikal, Russia)
            - A museum (eg. Louvre Museum, Paris, France)
            - A beach (eg. Bondi Beach, Australia)
            - A forest (eg. Amazon Rainforest, Brazil)
            - A theater (eg. Sydney Opera House, Australia)
            - A zoo (eg. San Diego Zoo, USA)
            - A castle (eg. Neuschwanstein Castle, Germany)
            - A market (eg. Tsukiji Fish Market, Tokyo, Japan)
            - A stadium (eg. Wembley Stadium, London, England)
            - A church (eg. Sagrada Familia, Barcelona, Spain)
            - A university (eg. Harvard University, Cambridge, USA)
            - A river (eg. Amazon River, South America)
            - A restaurant (eg. Noma, Copenhagen, Denmark)
            - A hotel (eg. Marina Bay Sands, Singapore)
            - A lighthouse (eg. Cape Hatteras Lighthouse, USA)
            
            Pick at random from this list of categories. Then, return a random place in the world matching that category. Do not use the provided examples. Return only the place name. 
    """
        ],
    )

    r = model.generate_content(
        "generate one location according to the system instructions.",
        generation_config=genai.types.GenerationConfig(
            temperature=1.0,
        ),
    )
    return r.text


# converts a natural language place name to a Place object
def nat_language_to_place(place_nat) -> Place:
    url = "https://places.googleapis.com/v1/places:searchText"
    headers = {
        "X-Goog-Api-Key": google_maps_api_key,
        "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.location.latitude,places.location.longitude",
        "Content-Type": "application/json",
    }
    data = {"textQuery": place_nat}
    response = requests.post(url, headers=headers, data=json.dumps(data))
    j = response.json()
    # print("response from Google Maps API: {}".format(j))
    # if err, or no places returned, use the default location.
    if "places" not in j or len(j["places"]) == 0:
        print(
            " ⚠️ Err: could not find exact location for: {}, returning default".format(
                place_nat
            )
        )
        return Place(
            name="Golden Gate Bridge",
            address="Old Conzelman Rd, Mill Valley, CA 94941",
            lat=37.817718,
            long=-122.4732808,
        )
    # otherwise, return the first place we found.
    p = j["places"][0]
    print("p is: {}".format(p))

    # sanitize all strings - remove non unicode chars and punctuation
    name = p["displayName"]["text"]
    name = name.encode("ascii", "ignore").decode("ascii")
    name = name.replace(",", "")
    name = name.replace(".", "")

    address = p["formattedAddress"]
    address = address.encode("ascii", "ignore").decode("ascii")
    address = address.replace(",", "")
    address = address.replace(".", "")

    lat = p["location"]["latitude"]
    long = p["location"]["longitude"]

    return Place(name=name, address=address, lat=lat, long=long)


# as-the-crow-flies distance between two points
# round up to the nearest mile
def calculate_dist(user_place: Place, answer_place: Place) -> int:
    miles = geodesic(
        (user_place.lat, user_place.long), (answer_place.lat, answer_place.long)
    ).miles
    return int(miles)


# The user can make 2 types of guesses: intermediate and final.
# intermediate is asking Gemini for clues. final = trying to guess the exact address.
# for an intermediate guess, we translate the user's guess into a lat/long,
# calculate dist and direction (between the guess and the answer), then have
# gemini return a VAGUE clue, such as: "a bit further west," or "wrong continent."
# gemini has the full chat history so it can also say "you're getting warmer"
# if the user is getting progressively closer to the answer.
def process_intermediate_guess(g: Guess):
    # store user's session chat history
    if g.session_id not in chat_history:
        chat_history[g.session_id] = [g.guess]
    else:
        chat_history[g.session_id].append(g.guess)
    print(
        "chat history for session {}: {}".format(
            g.session_id, chat_history[g.session_id]
        )
    )
    # 1) convert the user's guess to a Place
    user_place = nat_language_to_place(g.guess)
    print("📍 Found place for user guess: {}".format(user_place))

    # 2) calculate the distance between the user's guess and the answer
    d = calculate_dist(user_place, answers[g.session_id])
    print("📏 Dist btwn. user guess and answer: {} miles".format(d))

    # 3) given that distance, prompt gemini to return a clue for the user
    system = """
    You are facilitating a geography guessing game. The user is trying to guess what the answer location is, based on an image of that location that they see on their screen. 
    
    You will be given the user's guess, the answer location, and the full chat 
    history so far. Your job is to provide one clue. 
    
    For the current guess, you'll be shown not only their natural language prompt, but an exact location - and the exact distance between their guess and the answer.
    
    Your job is to provide a VAGUE clue to help the user guess the location.  
    DO NOT say the answer, or any part of the answer. Do not reply with the user's guess, verbatim.  
    If the user has the right country, state, or province, you can say that, eg. "Getting hotter, you're in the right country!"
    
    Examples of clues you could provide:  
    "You're getting warmer!" 
    "Wrong contintent. Try somewhere in Africa." 
    
    If the user's natural language guess is a specific question, reply to that question specifically - without giving the answer.
    
    Reply with ONLY a short, simple clue. Use a friendly, playful tone. 
    """

    model = genai.GenerativeModel(
        model_name="gemini-1.5-flash-001",
        system_instruction=[system],
    )

    pretty_user_place = "{}, {}".format(user_place.name, user_place.address)
    pretty_answer = "{}, {}".format(
        answers[g.session_id].name, answers[g.session_id].address
    )

    prompt = "User natural language guess or question: {},\nUser's exact place guess: {},\nAnswer (exact location): {},\nDistance from answer: {} miles".format(
        g.guess, pretty_user_place, pretty_answer, d
    )
    print("\n📝 PROMPT: {}\n".format(prompt))

    # retry logic - try up to 3 times, with a 2 second backoff
    for i in range(3):
        try:
            r = model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.8,
                ),
            )
            return r.text
        except Exception as e:
            print("⚠️ Error generating content: {}".format(e))
            time.sleep(2)


# for a final guess, we do a similar intial processing to an intermediate guess
# (translate guess to lat/long, calculate dist), but then we rate the user's guess, tell them how many miles off they were, and close out the chat session, ending the game.
# use gemini to return a fun fact about that city
def process_final_guess(g: Guess):
    # 1) convert the user's guess to a Place
    user_place = nat_language_to_place(g.guess)
    print("📍 Found place for user guess: {}".format(user_place))

    # 2) calculate the distance between the user's guess and the answer
    d = calculate_dist(user_place, answers[g.session_id])
    print("📏 Dist btwn. user final guess and answer: {} miles".format(d))

    # 3) given that distance, prompt gemini to return a clue for the user
    system = """
    You are facilitating a geography guessing game. The user has guessed 
    what location they think the answer is. You'll be given the user's guess, 
    the answer, and the distance (in miles) between the guess and the answer. 
    
    Your job is to return a rating of the user's guess, based on the distance, 
    closing out the game. 
    A distance of 0-5 miles is "spot on!" 
    6-25 miles is "pretty close!"
    25-50 miles is "not bad!" 
    50+ miles is "not quite there- better luck next time!"
    
    Be friendly, encouraging, and playful with your response.
    """
    model = genai.GenerativeModel(
        model_name="gemini-1.5-flash-001",
        system_instruction=[system],
    )

    pretty_user_place = "{}, {}".format(user_place.name, user_place.address)
    pretty_answer = "{}, {}".format(
        answers[g.session_id].name, answers[g.session_id].address
    )

    prompt = "User natural language final guess: {},\nUser's exact place guess: {},\nAnswer (exact location): {},\nDistance from answer: {} miles".format(
        g.guess, pretty_user_place, pretty_answer, d
    )
    print("\n📝 PROMPT: {}\n".format(prompt))

    # retry logic - try up to 3 times, with a 2 second backoff
    for i in range(3):
        try:
            r = model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.5,
                ),
            )
            return d, r.text
        except Exception as e:
            print("⚠️ Error generating content: {}".format(e))
            time.sleep(2)
