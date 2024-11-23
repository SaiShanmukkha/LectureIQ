from flask import Flask, flash, render_template, request, session, url_for, redirect
from dotenv import load_dotenv
import os, urllib, requests, logging
from datetime import datetime
import whisper
from langchain_openai import ChatOpenAI
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain.prompts import ChatPromptTemplate
from langchain.chains import LLMChain
from langchain_core.messages import HumanMessage

load_dotenv()

app = Flask(__name__)


app.secret_key = os.getenv('SECRET_KEY')
os.environ["OPENAI_API_KEY"] = os.getenv('OPENAI_SECRET_KEY')
ZOOM_OAUTH_AUTHORIZE_API = 'https://zoom.us/oauth/authorize?'
ZOOM_TOKEN_API = 'https://zoom.us/oauth/token'

chat = ChatOpenAI(temperature=0.0, model="gpt-4o")

system_template = "You are now an expert transcript proofreader who has proof read many english texts\
                    written by both native and non-native english speakers. You will only give me proof read text."

system_role_questions = "you are now an expert at creating questions from given text such that these questions\
      will test students on the core concept of the text. You will consider this entire text and generate questions from this text."

text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=2000,
    chunk_overlap=200,
    length_function=len
)

prompt = ChatPromptTemplate.from_messages([
    ("system", system_template),
    ("user", "{text}")
])

question_prompt = ChatPromptTemplate.from_messages([
    ("system", system_role_questions),
    ("user", "{transcript}")
])

expert_proofread_chain = prompt | chat
question_chain = question_prompt | chat
def expert_proofread_large_transcript(transcript):
    
    chunks = text_splitter.split_text(transcript)
    
    
    proofread_chunks = []
    for chunk in chunks:
        human_message = HumanMessage(content=chunk)
        result = expert_proofread_chain.invoke([human_message])
        proofread_chunks.append(result.content)
    
    
    return " ".join(proofread_chunks)


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
        
    print(user_info)
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
    

    print(token_response)
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
        if file.get('file_type')=="M4A":
            print("********",file.get("download_url"))
            download_link = f'{file.get("download_url")}?access_token={token["access_token"]}&playback_access_token={recordings_json.get("recording_play_passcode")}'
            local_file = download_audio_file(download_link, "local_file.m4a") 
            model = whisper.load_model("base")
            result = model.transcribe(local_file)
            with open("result.txt", 'w') as f:
                f.write(result["text"])
            
            transcript = result["text"]
            expertly_proofread_transcript = expert_proofread_large_transcript(transcript)
            app.logger.debug(f'transcribe text is {expertly_proofread_transcript}')
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

@app.route('/generate_questions', methods=['POST'])
def generate_questions():
    
    
    
    
    
    
    data = request.get_json()
    if not data or 'transcript' not in data:
        app.logger.debug('No transcript data received')
        return 'Bad Request', 400

    transcript = data['transcript']
    app.logger.debug(f'Transcript received: {transcript}')
    human_msg = HumanMessage(content=transcript)
    result = question_chain.invoke([human_msg])
    app.logger.debug(f'Result is: {result}')
    
    session["questions"] = result.content
    return {'questions': result.content}, 200 

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


if __name__ == '__main__':
    app.run(debug=True)
