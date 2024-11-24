from flask import Flask, flash, render_template, request, session, url_for, Response, redirect, jsonify
from dotenv import load_dotenv
import os, urllib, requests, logging
from datetime import datetime
import whisper
# import llama_index
# from llama_index import (
#     VectorStoreIndex,
#     Document,
#     ServiceContext,
#     LLMPredictor,
# )
from llama_index.core import VectorStoreIndex
from llama_index.core import Document
from llama_index.core import Settings
from llama_index.llms.openai import OpenAI
from groq import Groq as OrgGroq
from llama_index.llms.groq import Groq

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY')
# os.environ["OPENAI_SECRET_KEY"] = os.getenv('OPENAI_SECRET_KEY')
openai_secret_key = os.getenv('OPENAI_SECRET_KEY')
if openai_secret_key is not None:
    os.environ["OPENAI_API_KEY"] = openai_secret_key
else:
    raise ValueError("OPENAI_SECRET_KEY environment variable is not set.")

ZOOM_OAUTH_AUTHORIZE_API = 'https://zoom.us/oauth/authorize?'
ZOOM_TOKEN_API = 'https://zoom.us/oauth/token'

client = Groq(
    api_key=os.environ.get("GROQ_API_KEY"),
    model="llama-3.2-11b-vision-preview", 
    temperature=0.2
)
client1 = OrgGroq()
# Initialize LlamaIndex components
# llm_predictor = LLMPredictor(llm=OpenAI(temperature=0.0, model="gpt-4"))
# service_context = ServiceContext.from_defaults(llm_predictor=llm_predictor)
Settings.llm = client

def expert_proofread_large_transcript(transcript):
    # Create a Document object with the transcript text
    document = Document(text=transcript)
    
    # Initialize the index with the document
    index = VectorStoreIndex.from_documents([document])
    
    # Create a query engine
    query_engine = index.as_query_engine()
    
    # Define the proofreading prompt
    proofreading_prompt = (
        "You are an expert transcript proofreader who has proofread many English texts "
        "written by both native and non-native English speakers. Please proofread the following text:\n\n"
        "{text}"
        "Instructions for output: \n"
        "1. You will only output the proofread text, do not include anything else in the transcript"
    )
    
    # Execute the query with the proofreading prompt
    response = query_engine.query(proofreading_prompt.format(text=transcript))
    
    return response.response

def generate_questions_from_transcript(transcript):
    # Create a Document object with the transcript text
    document = Document(text=transcript)
    
    # Initialize the index with the document
    index = VectorStoreIndex.from_documents([document])
    
    # Create a query engine
    query_engine = index.as_query_engine()
    
    # Define the question generation prompt
    question_prompt = (
        "You are an expert at creating questions from given text to test students on the core concepts. "
        "Please generate questions from the following text:\n\n"
        "{text}"
    )
    
    # Execute the query with the question generation prompt
    response = query_engine.query(question_prompt.format(text=transcript))
    
    return response.response

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/zoom-login')
def login():
    if not session.get("token"):
        params = {
            'response_type': 'code',
            'client_id': os.getenv("CLIENT_ID"),
            'redirect_uri': os.getenv("ZOOM_REDIRECT_URI"),
        }
        url = ZOOM_OAUTH_AUTHORIZE_API + urllib.parse.urlencode(params)
        return redirect(url)
    else:
        return redirect(url_for("recordings"))
    
@app.route('/recordings')
def recordings():
    token = session.get('token')
    if token is None:
        return redirect(url_for('zoom-login'))
    
    headers = {'Authorization': f'Bearer {token["access_token"]}'}
    
    user_info = requests.get('https://api.zoom.us/v2/users/me', headers=headers)
    if user_info.status_code == 401:  
        access_token = refresh_token()
        headers['Authorization'] = f'Bearer {access_token}'
        user_info = requests.get('https://api.zoom.us/v2/users/me', headers=headers)
        
    user_info_json = user_info.json()
    user_id = user_info_json['id']
    current_date = datetime.now().strftime('%Y-%m-%d')
    params = {
        'from': "2023-01-01",
        'to': current_date
    }
    recordings = requests.get(f'https://api.zoom.us/v2/users/{user_id}/recordings', headers=headers, params=params)
    recordings_json = recordings.json()
    app.logger.debug(f'recordings {recordings_json}')

    return render_template('recordings.html', meetings=recordings_json.get('meetings',[]))

def refresh_token():
    token = session.get("token")
    if not token or "refresh_token" not in token:
        return redirect(url_for('login'))

    client_auth = requests.auth.HTTPBasicAuth(os.getenv("CLIENT_ID"), os.getenv("CLIENT_SECRET"))
    post_data = {
        "grant_type": "refresh_token",
        "refresh_token": token["refresh_token"]
    }
    token_response = requests.post("https://zoom.us/oauth/token",
                                   auth=client_auth,
                                   data=post_data)

    if token_response.status_code != 200:
        return redirect(url_for('login'))

    try:
        token_json = token_response.json()
    except requests.exceptions.JSONDecodeError:
        return redirect(url_for('login'))

    session["token"] = token_json
    return token_json["access_token"]

@app.route('/authorize')
def get_token():
    code = request.args.get('code')
    client_auth = requests.auth.HTTPBasicAuth(os.getenv("CLIENT_ID"), os.getenv("CLIENT_SECRET"))
    app.logger.debug(f'client ID is: {client_auth}')
    post_data = {"grant_type": "authorization_code",
                 "code": code,
                 "redirect_uri": os.getenv("ZOOM_REDIRECT_URI")}
    app.logger.debug(f'post data is {post_data}')
    token_response = requests.post("https://zoom.us/oauth/token",
                             auth=client_auth,
                             data=post_data)
    if token_response.status_code != 200:
        return f"Failed to get token: {token_response.text}"
    
    try:
        token_json = token_response.json()
    except requests.exceptions.JSONDecodeError:
        return "Failed to decode token response"
    
    session["token"] = token_json
    return redirect(url_for('recordings'))

@app.route("/uploadLocalFile", methods=['GET'])
def uploadLocalFile():
    pass

@app.route('/getAudioTranscript', methods=['GET'])
def getTranscript():
    recording_id = request.args.get('meeting_id')
    if not recording_id:
        flash('No recording ID provided')
        return redirect(url_for('recordings'))
    
    token = session.get('token')
    if not token:
        return redirect(url_for('zoom-login'))
    
    headers = {'Authorization': f'Bearer {token["access_token"]}'}
    recordings = requests.get(f'https://api.zoom.us/v2/meetings/{recording_id}/recordings', headers=headers)
    
    if recordings.status_code != 200:
        return f"Failed to retrieve recording: {recordings.text}"
    
    recordings_json = recordings.json()
    app.logger.debug(f'post data is {recordings_json}')

    expertly_proofread_transcript = ''
    recording_files = recordings_json['recording_files']
    for file in recording_files:
        if file.get('file_type') == "M4A":
            download_link = f'{file.get("download_url")}?access_token={token["access_token"]}&playback_access_token={recordings_json.get("recording_play_passcode")}'
            local_file = download_audio_file(download_link, "local_file.m4a")
            # model = whisper.load_model("base")
            # result = model.transcribe(local_file)
            # transcript = result["text"]
            file_path=os.path.abspath('local_file.m4a')
            print(file_path)
            # file_name = file_path + ''
            model = client1
            with open(file_path, "rb") as file:
                create_transcript = model.audio.transcriptions.create(
                    file=(file_path, file.read()),
                    model="whisper-large-v3-turbo",
                    temperature=0.0
                )
            print(create_transcript.text)
            transcript = create_transcript.text
            expertly_proofread_transcript = expert_proofread_large_transcript(transcript)
            app.logger.debug(f'Transcribed text: {expertly_proofread_transcript}')
            session['proofread_transcript'] = expertly_proofread_transcript
    return redirect(url_for('manual_proofread'))

def download_audio_file(url, local_filename):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
    }
    
    with requests.get(url, stream=True, headers=headers, allow_redirects=True) as r:
        r.raise_for_status()
        with open(local_filename, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
    return local_filename

# @app.route('/chat_with_context', methods=['POST'])
# def generate_questions():
#     data = request.get_json()
#     if not data or 'transcript' not in data:
#         app.logger.debug('No transcript data received')
#         return 'Bad Request', 400

#     transcript = data['transcript']
#     app.logger.debug(f'Transcript received: {transcript}')
#     questions = generate_questions_from_transcript(transcript)
#     app.logger.debug(f'Generated questions: {questions}')
    
#     session["questions"] = questions
#     return {'questions': questions}, 200


@app.route('/chat_with_context', methods=['POST'])
def chat_with_context():
    data = request.get_json()
    if not data or 'transcript' not in data:
        app.logger.debug('No transcript data received')
        return 'Bad Request', 400

    transcript = data['transcript']
    app.logger.debug(f'Transcript received: {transcript}')
    questions = generate_questions_from_transcript(transcript)
    app.logger.debug(f'Generated questions: {questions}')
    
    session["questions"] = questions
    return {'questions': questions}, 200

@app.route('/print_pdf')
def print_pdf():
    questions = session.get('questions', '')
    return render_template('questions.html', questions=questions)

@app.route('/manual_proofread')
def manual_proofread():
    proofread_transcript = session.get('proofread_transcript', None)
    if proofread_transcript:
        return render_template("manual_proofread.html", transcript=proofread_transcript)
    return redirect(url_for('getAudioTranscript'))

@app.route('/save_questions', methods=['GET', 'POST'])
def save_questions():
    # Retrieve questions from session
    questions = session.get("questions", None)

    if questions is None:
        return jsonify({"status": "error", "message": "No questions were found in the session"}), 404

    try:
        # Create the content for the file
        if isinstance(questions, list):
            file_content = "\n".join(questions)
        else:
            file_content = str(questions)

        # Return the file as a downloadable response
        return Response(
            file_content,
            mimetype="text/plain",
            headers={"Content-Disposition": "attachment;filename=questions.txt"}
        )
    except Exception as e:
        return jsonify({"status": "error", "message": f"Failed to save questions: {str(e)}"}), 500

@app.route('/save_editable_transcript', methods=['GET', 'POST'])
def save_editable_transcript():
    data = request.get_json()
    if not data or 'transcript' not in data:
        app.logger.debug('No transcript data received')
        return 'Bad Request', 400

    transcript = data['transcript']
    app.logger.debug(f'Transcript received: {transcript}')
    # questions=session.get('questions', '')
    # return render_template('chat.html')
    session["edited_transcript"] = transcript
    return transcript

# @app.route('/chat_interface')

@app.route('/chat', methods=['GET', 'POST'])
def chat():
    if request.method == 'POST':
        user_message = request.json.get('message', '').strip()

        if not user_message:
            return jsonify({"response": "Please type a message."}), 200

        # Retrieve the context (edited transcript) from the session
        edited_transcript = session.get('edited_transcript', '')

        # Generate a context-aware response
        response = generate_response(user_message, edited_transcript)

        return jsonify({"response": response}), 200

    return render_template('chat.html')

def generate_response(user_message, context):
    """
    Generate a context-aware response to the user's message using the Groq client.
    Args:
        user_message (str): The message sent by the user.
        context (str): The context (e.g., edited transcript).
    Returns:
        str: The chatbot's response.
    """
    # Construct the messages list with context and user message
    messages = [
        {"role": "system", "content": f"Context: {context}.\n You will answer based on the context based on user's question. You will be a normal friendly chatbot."},
        {"role": "user", "content": user_message}
    ]

    # Generate the response using the Groq client
    try:
        response = client1.chat.completions.create(
            model="llama3-70b-8192",
            messages=messages,
            max_tokens=100,
            temperature=1.2
        )
        return response.choices[0].message.content
    except Exception as e:
        app.logger.error(f"Error generating response: {e}")
        return "I'm sorry, but I couldn't generate a response at this time."


if __name__ == '__main__':
    app.run(debug=True)

 
