from flask import Flask, request, jsonify, render_template, send_file
from flask_cors import CORS
from groq import Groq
import os
from dotenv import load_dotenv
import json
from datetime import datetime, timedelta
import sqlite3
try:
    import PyPDF2
    PDF_AVAILABLE = True
except ImportError:
    PDF_AVAILABLE = False
    print("⚠️ PyPDF2 not installed. PDF parsing will be limited.")

try:
    from PIL import Image
    import numpy as np
    import cv2
    import matplotlib
    matplotlib.use('Agg')  # Non-interactive backend
    import matplotlib.pyplot as plt
    IMAGE_AVAILABLE = True
except ImportError:
    IMAGE_AVAILABLE = False
    print("⚠️ Image processing libraries not installed. X-ray analysis will be limited.")

try:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import ImageReader
    REPORT_AVAILABLE = True
except ImportError:
    REPORT_AVAILABLE = False
    print("⚠️ ReportLab not installed. PDF report generation will be limited.")

load_dotenv()

app = Flask(__name__)
CORS(app)

# Initialize Groq client
GROQ_API_KEY = os.getenv('GROQ_API_KEY')
GROQ_MODEL = os.getenv('GROQ_MODEL', 'llama-3.1-8b-instant')  # Default model, can be overridden in .env

if GROQ_API_KEY:
    GROQ_API_KEY = GROQ_API_KEY.strip()  # Remove any whitespace

if not GROQ_API_KEY or GROQ_API_KEY == '' or 'your_groq_api_key_here' in GROQ_API_KEY:
    print("⚠️ WARNING: GROQ_API_KEY not found or invalid in .env file!")
    print("Please create a .env file with: GROQ_API_KEY=your_actual_api_key")
    client = None
else:
    print(f"✅ Groq API key loaded successfully (length: {len(GROQ_API_KEY)})")
    try:
        client = Groq(api_key=GROQ_API_KEY)
        # Test the connection with a simple request
        print("✅ Groq API client initialized successfully")
    except Exception as e:
        print(f"❌ Error initializing Groq client: {str(e)}")
        client = None

# In-memory storage (in production, use a database)
patients_data = []
patient_conversations = {}

# --- SQLite setup for risk history and alerts ---
DB_PATH = os.path.join(os.path.dirname(__file__), 'medical.db')

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS risk_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id TEXT NOT NULL,
            date TEXT NOT NULL,
            risk_score INTEGER NOT NULL,
            trend_status TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS doctor_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_id TEXT NOT NULL,
            risk_score INTEGER NOT NULL,
            risk_level TEXT NOT NULL,
            status_message TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()

def add_risk_entry(patient_id: str, risk_score: int, trend_status: str):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO risk_history (patient_id, date, risk_score, trend_status) VALUES (?,?,?,?)",
        (patient_id, datetime.now().isoformat(), int(risk_score), trend_status or '')
    )
    conn.commit()
    conn.close()

def get_risk_history_from_db(patient_id: str):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT date, risk_score, trend_status FROM risk_history WHERE patient_id=? ORDER BY date ASC", (patient_id,))
    rows = cur.fetchall()
    conn.close()
    return [dict(date=r['date'], risk_score=r['risk_score'], trend_status=r['trend_status']) for r in rows]

def add_doctor_alert(patient_id: str, risk_score: int, risk_level: str, status_message: str):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO doctor_alerts (patient_id, risk_score, risk_level, status_message, created_at) VALUES (?,?,?,?,?)",
        (patient_id, int(risk_score), risk_level, status_message, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()

def get_doctor_alerts_from_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT patient_id, risk_score, risk_level, status_message, created_at FROM doctor_alerts ORDER BY created_at DESC LIMIT 100")
    rows = cur.fetchall()
    conn.close()
    return [dict(patient_id=r['patient_id'], risk_score=r['risk_score'], risk_level=r['risk_level'], status_message=r['status_message'], created_at=r['created_at']) for r in rows]

@app.route('/api/contact', methods=['POST'])
def api_contact():
    try:
        data = request.json or {}
        patient_id = data.get('patient_id') or 'patient_1'
        name = (data.get('name') or '').strip()
        phone = (data.get('phone') or '').strip()
        email = (data.get('email') or '').strip()
        if patient_id not in patient_conversations:
            patient_conversations[patient_id] = {
                'patient_id': patient_id,
                'uploads': [],
                'conversation': [],
                'risk_level': 'unknown',
                'details': {},
                'surgery_info': {},
                'symptoms_asked': [],
                'symptoms_prompted': [],
                'last_prompted_symptom': None,
                'dialogue_stage': 'initial',
                'contact': {},
            }
        patient_conversations[patient_id]['contact'] = {
            'name': name,
            'phone': phone,
            'email': email
        }
        # Update dashboard list so name shows on card
        update_patients_list(patient_id)
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def map_level_to_score(level: str) -> int:
    if not level:
        return 40
    level = level.lower()
    if level == 'high':
        return 85
    if level in ('moderate','medium'):
        return 55
    if level == 'low':
        return 25
    return 40

def compute_trend_status(scores: list) -> str:
    if len(scores) < 2:
        return 'stable'
    delta = scores[-1] - scores[0]
    if delta < 0:
        return 'improving'
    if delta > 0:
        return 'worsening'
    return 'stable'

def build_doctor_payload(patient_id: str, score: int = None):
    data = patient_conversations.get(patient_id, {})
    uploads = data.get('uploads', [])
    last_uploads = []
    try:
        for u in uploads[-3:]:  # last up to 3
            last_uploads.append({
                'filename': u.get('filename'),
                'timestamp': u.get('timestamp'),
                'is_image': u.get('is_image', False),
                'gradcam_image_path': u.get('gradcam_image_path')
            })
    except Exception:
        pass
    recent_msgs = []
    try:
        for m in data.get('conversation', [])[-5:]:
            recent_msgs.append({
                'role': m.get('role'),
                'content': m.get('content'),
                'timestamp': m.get('timestamp')
            })
    except Exception:
        pass
    payload = {
        'patient_id': patient_id,
        'risk_level': data.get('risk_level', 'unknown'),
        'risk_score': score,
        'surgery_info': data.get('surgery_info', {}),
        'symptoms_asked': data.get('symptoms_asked', []),
        'recent_messages': recent_msgs,
        'latest_uploads': last_uploads,
        'contact': data.get('contact', {})
    }
    return payload

def send_email_to_doctor(patient_id: str, payload: dict = None):
    # Stub: In production, integrate SMTP/provider or webhook here
    print(f"[Notify] Doctor notified for {patient_id}")
    if payload:
        try:
            print("[Notify] Payload:")
            print(json.dumps(payload, indent=2))
        except Exception:
            pass

def schedule_reminder(patient_id: str):
    # Stub: In production, schedule a task via Celery/cron
    print(f"[Reminder] Follow-up scheduled in 24h for {patient_id}")

# Initialize DB at startup
init_db()

# Configure upload folder
UPLOAD_FOLDER = 'uploads'
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

@app.route('/')
def home():
    return render_template('home.html')

@app.route('/patient')
def patient():
    return render_template('patient_dashboard.html')

@app.route('/doctor')
def doctor():
    return render_template('doctor_dashboard.html')

@app.route('/api/upload', methods=['POST'])
def upload_file():
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400
        
        file = request.files['file']
        patient_id = request.form.get('patient_id', 'patient_1')
        
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400
        
        # Save file
        filename = f"{patient_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{file.filename}"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        
        # Read file content
        file_content = ""
        is_image = False
        gradcam_analysis = None
        gradcam_image_path = None
        
        if filename.endswith('.txt'):
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                file_content = f.read()
        elif filename.endswith('.pdf') and PDF_AVAILABLE:
            file_content = extract_text_from_pdf(filepath)
        elif filename.endswith('.pdf'):
            file_content = "PDF file uploaded. Text extraction requires PyPDF2 library."
        elif filename.lower().endswith(('.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.dcm')):
            is_image = True
        
        # Analyze file with LLM
        analysis = analyze_uploaded_data(file_content, filename)
        
        # Store patient data
        if patient_id not in patient_conversations:
            patient_conversations[patient_id] = {
                'patient_id': patient_id,
                'uploads': [],
                'conversation': [],
                'risk_level': 'unknown',
                'details': {},
                'surgery_info': {},
                'symptoms_asked': [],
                'symptoms_prompted': [],
                'last_prompted_symptom': None,
                'dialogue_stage': 'initial',
                'contact': {},
                'pain_followups': {
                    'asked_location': False,
                    'asked_intensity': False
                }
            }
        
        # Extract surgery information from analysis
        surgery_info = extract_surgery_info(analysis, file_content)

        # If this was an image, now run Grad-CAM with surgery_info to focus highlights
        if is_image and IMAGE_AVAILABLE:
            try:
                # If we couldn't extract surgery info from this file, fallback to any existing info for this patient
                effective_surgery_info = surgery_info if surgery_info else patient_conversations.get(patient_id, {}).get('surgery_info', {})
                gradcam_analysis, gradcam_image_path = analyze_xray_with_gradcam(filepath, filename, effective_surgery_info)
            except Exception as e:
                print(f"Grad-CAM analysis error: {str(e)}")
                gradcam_analysis = f"Image uploaded. Analysis available: {str(e)}"
        
        upload_data = {
            'filename': filename,
            'content': file_content,
            'analysis': analysis,
            'surgery_info': surgery_info,
            'timestamp': datetime.now().isoformat(),
            'is_image': is_image
        }
        
        if gradcam_analysis:
            upload_data['gradcam_analysis'] = gradcam_analysis
        if gradcam_image_path:
            upload_data['gradcam_image_path'] = gradcam_image_path
        
        patient_conversations[patient_id]['uploads'].append(upload_data)
        
        # Update surgery info if found
        if surgery_info.get('surgery_type'):
            patient_conversations[patient_id]['surgery_info'] = surgery_info
            patient_conversations[patient_id]['dialogue_stage'] = 'symptoms_inquiry'
        
        response_data = {
            'message': 'File uploaded successfully',
            'analysis': analysis,
            'filename': filename,
            'is_image': is_image
        }
        
        if gradcam_analysis:
            response_data['gradcam_analysis'] = gradcam_analysis
        if gradcam_image_path:
            response_data['gradcam_image_path'] = gradcam_image_path.replace('\\', '/')
        # Keep hospital dashboard in sync
        try:
            update_patients_list(patient_id)
        except Exception:
            pass

        return jsonify(response_data)
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/risk-history/<patient_id>', methods=['GET'])
def api_risk_history(patient_id):
    try:
        history = get_risk_history_from_db(patient_id)
        return jsonify({'patient_id': patient_id, 'history': history})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/doctor-alerts', methods=['GET'])
def api_doctor_alerts():
    try:
        alerts = get_doctor_alerts_from_db()
        return jsonify({'alerts': alerts})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def extract_text_from_pdf(pdf_path):
    """Extract text from PDF file"""
    if not PDF_AVAILABLE:
        return "PDF parsing not available. Please install PyPDF2."
    
    try:
        text = ""
        with open(pdf_path, 'rb') as file:
            pdf_reader = PyPDF2.PdfReader(file)
            for page_num in range(min(len(pdf_reader.pages), 5)):  # Limit to first 5 pages
                page = pdf_reader.pages[page_num]
                text += page.extract_text() + "\n"
        return text
    except Exception as e:
        return f"Error reading PDF: {str(e)}"

def analyze_uploaded_data(content, filename):
    """Analyze uploaded medical data using Groq LLM - Focus on surgery identification"""
    if not client:
        return "Error: Groq API is not configured. Please add GROQ_API_KEY to your .env file."
    
    try:
        # Limit content to 2000 chars for surgery analysis
        truncated_content = content[:2000] if len(content) > 2000 else content
        
        prompt = f"""Analyze this medical report and identify:
1. SURGERY TYPE: What specific surgery was performed? (e.g., appendectomy, knee replacement, cataract surgery)
2. SURGERY DATE: When was it performed?
3. PATIENT CONDITION: Current status mentioned in report

Be specific about surgery type.

File: {filename}
Content: {truncated_content}

Format: "Surgery Type: [type], Date: [date], Status: [status]"
"""
        
        chat_completion = client.chat.completions.create(
            messages=[
                {"role": "system", "content": "Medical surgery report analyzer. Identify surgery type precisely."},
                {"role": "user", "content": prompt}
            ],
            model=GROQ_MODEL,
            temperature=0.2,
            max_tokens=200
        )
        
        return chat_completion.choices[0].message.content
    except Exception as e:
        return f"Analysis error: {str(e)}. Please check your Groq API key and connection."

def extract_surgery_info(analysis, file_content):
    """Extract structured surgery information from analysis"""
    if not client:
        return {}
    
    try:
        # Use LLM to extract structured info
        prompt = f"""From this analysis, extract JSON format strictly with these keys (include site/side if present, else empty string):
{{
  "surgery_type": "specific surgery name",
  "surgery_date": "date if mentioned",
  "site": "anatomical region (e.g., knee, lung, abdomen) or empty if unknown",
  "side": "left/right/bilateral or empty if unknown",
  "common_complications": ["list of 3-5 common complications for this surgery type"],
  "recovery_timeline": "typical recovery period"
}}

Analysis: {analysis[:700]}

Return only valid JSON, no other text."""
        
        chat_completion = client.chat.completions.create(
            messages=[
                {"role": "system", "content": "Extract surgery info as JSON. List common complications for the surgery type."},
                {"role": "user", "content": prompt}
            ],
            model=GROQ_MODEL,
            temperature=0.2,
            max_tokens=300
        )
        
        response = chat_completion.choices[0].message.content.strip()
        # Try to extract JSON from response
        if '{' in response:
            json_start = response.find('{')
            json_end = response.rfind('}') + 1
            json_str = response[json_start:json_end]
            try:
                return json.loads(json_str)
            except:
                pass
        
        # Fallback: parse text
        surgery_type = "Unknown"
        if "Surgery Type:" in analysis or "surgery" in analysis.lower():
            # Try to extract from text
            lines = analysis.split('\n')
            for line in lines:
                if 'surgery' in line.lower() or 'procedure' in line.lower():
                    surgery_type = line[:100]
                    break
        
        return {
            "surgery_type": surgery_type,
            "common_complications": ["infection", "bleeding", "pain", "swelling", "delayed healing"]
        }
    except Exception as e:
        return {
            "surgery_type": "Unknown",
            "common_complications": ["infection", "bleeding", "pain", "swelling", "delayed healing"]
        }

@app.route('/api/chat', methods=['POST'])
def chat():
    try:
        data = request.json
        patient_id = data.get('patient_id', 'patient_1')
        message = data.get('message', '')
        
        # Initialize conversation if needed
        if patient_id not in patient_conversations:
            patient_conversations[patient_id] = {
                'patient_id': patient_id,
                'uploads': [],
                'conversation': [],
                'risk_level': 'unknown',
                'details': {},
                'surgery_info': {},
                'symptoms_asked': [],
                'symptoms_prompted': [],
                'last_prompted_symptom': None,
                'dialogue_stage': 'initial'
            }
        
        # Add user message to conversation
        patient_conversations[patient_id]['conversation'].append({
            'role': 'user',
            'content': message,
            'timestamp': datetime.now().isoformat()
        })
        
        # Auto-escalation: if already escalated, do not ask new questions
        if patient_conversations[patient_id].get('dialogue_stage') == 'escalated':
            hold_msg = "We have already notified your doctor due to severe symptoms. Please follow urgent care advice and await contact."
            response = {
                'message': hold_msg,
                'risk_level': patient_conversations[patient_id].get('risk_level', 'high'),
                'details': {'escalated': True}
            }
        else:
            # Pre-detect severe pain and auto-escalate before calling LLM
            uml = (message or '').lower()
            pain_terms = any(w in uml for w in ['pain', 'hurt', 'ache', 'aching', 'painful'])
            severe_terms = any(w in uml for w in ['severe', 'very bad', 'extreme', 'unbearable', 'worst', 'heavy', 'heacy'])
            if pain_terms and severe_terms:
                patient_conversations[patient_id]['dialogue_stage'] = 'escalated'
                response = {
                    'message': "Severe pain detected. I'm escalating your case to the doctor now. If symptoms are intense, please seek urgent care immediately.",
                    'risk_level': 'high',
                    'details': {'severity': 'severe', 'escalated': True}
                }
            else:
                # Get LLM response with language support
                language = data.get('language', 'en')
                response = get_chat_response(patient_id, message, language)

        # Enforce one-question-per-turn: keep only the first question if multiple are present
        def _first_question_only(txt: str) -> str:
            try:
                if not txt:
                    return txt
                # If multiple '?', keep text up to and including the first '?'
                qpos = txt.find('?')
                if qpos == -1:
                    return txt
                # If there are additional questions after, trim
                if txt.find('?', qpos + 1) != -1:
                    return txt[:qpos + 1]
                return txt
            except Exception:
                return txt

        if isinstance(response, dict) and 'message' in response:
            response['message'] = _first_question_only(response.get('message') or '')
        
        # Add assistant response to conversation
        patient_conversations[patient_id]['conversation'].append({
            'role': 'assistant',
            'content': response['message'],
            'timestamp': datetime.now().isoformat()
        })
        
        # Update risk level if assessed, log risk history and alerts
        if 'risk_level' in response:
            lvl = response['risk_level']
            patient_conversations[patient_id]['risk_level'] = lvl
            patient_conversations[patient_id]['details'].update(response.get('details', {}))
            # Only proceed for concrete levels
            if lvl in ('low', 'moderate', 'medium', 'high'):
                norm_level = 'moderate' if lvl == 'medium' else lvl
                # Map to numeric score and store
                score = map_level_to_score(norm_level)
                # Expose score in API response
                response['risk_score'] = score
                # Inject score into assistant narrative text if not already present
                try:
                    base_msg = response.get('message') or ''
                    if 'score:' not in base_msg.lower():
                        response['message'] = f"Risk score: {score}\n" + base_msg
                except Exception:
                    pass
                # Pull previous scores for trend
                history = get_risk_history_from_db(patient_id)
                prev_scores = [h['risk_score'] for h in history[-3:]]  # last up to 3
                window = prev_scores + [score]
                trend = compute_trend_status(window) if window else 'stable'
                add_risk_entry(patient_id, score, trend)

                # Add a short trend line ONLY if there is no question in this turn
                base_msg = response.get('message') or ''
                if '?' not in base_msg:
                    trend_line = ''
                    if trend == 'improving':
                        trend_line = "\nYour recovery trend is improving!"
                    elif trend == 'worsening':
                        trend_line = "\nYour condition is worsening, please consult your doctor."
                    elif trend == 'stable':
                        trend_line = "\nYour status appears stable at the moment."
                    response['message'] = base_msg + trend_line
                else:
                    response['message'] = base_msg

                # Alerts and reminders
                status_msg = ''
                if score > 70:
                    # If we escalated due to severe symptoms, reflect that in status
                    if patient_conversations[patient_id].get('dialogue_stage') == 'escalated':
                        status_msg = 'Severe pain – CALL PATIENT NOW'
                    else:
                        status_msg = 'High risk – CALL PATIENT NOW'
                    add_doctor_alert(patient_id, score, 'high', status_msg)
                    send_email_to_doctor(patient_id, build_doctor_payload(patient_id, score))
                elif 40 <= score <= 70:
                    status_msg = 'Moderate risk – Follow-up scheduled in 24h'
                    add_doctor_alert(patient_id, score, 'moderate', status_msg)
                    schedule_reminder(patient_id)
                else:
                    status_msg = 'Low risk – Preventive care suggested'
                    add_doctor_alert(patient_id, score, 'low', status_msg)
            else:
                # Unknown risk: do not attach a numeric score or create alerts/history
                response.pop('risk_score', None)
        
        # Track symptoms being asked about or mentioned in patient responses
        user_message_lower = data.get('message', '').lower()
        symptoms_tracked = patient_conversations[patient_id].setdefault('symptoms_asked', [])
        prompted = patient_conversations[patient_id].setdefault('symptoms_prompted', [])
        last_prompted = patient_conversations[patient_id].get('last_prompted_symptom')
        
        # Track when patient answers about symptoms
        if any(word in user_message_lower for word in ['pain', 'hurt', 'ache', 'sore', 'painful']):
            if 'pain' not in symptoms_tracked:
                symptoms_tracked.append('pain')
            if 'pain' in prompted:
                prompted.remove('pain')
            if last_prompted == 'pain':
                patient_conversations[patient_id]['last_prompted_symptom'] = None
        if any(word in user_message_lower for word in ['swell', 'swollen', 'inflammation', 'puffy', 'swelling']):
            if 'swelling' not in symptoms_tracked:
                symptoms_tracked.append('swelling')
            if 'swelling' in prompted:
                prompted.remove('swelling')
            if last_prompted == 'swelling':
                patient_conversations[patient_id]['last_prompted_symptom'] = None
        if any(word in user_message_lower for word in ['bleed', 'blood', 'hemorrhage', 'bleeding']):
            if 'bleeding' not in symptoms_tracked:
                symptoms_tracked.append('bleeding')
            if 'bleeding' in prompted:
                prompted.remove('bleeding')
            if last_prompted == 'bleeding':
                patient_conversations[patient_id]['last_prompted_symptom'] = None
        if any(word in user_message_lower for word in ['infection', 'fever', 'pus', 'discharge', 'infected', 'feverish']):
            if 'infection' not in symptoms_tracked:
                symptoms_tracked.append('infection')
            if 'infection' in prompted:
                prompted.remove('infection')
            if last_prompted == 'infection':
                patient_conversations[patient_id]['last_prompted_symptom'] = None
        if any(word in user_message_lower for word in ['heal', 'healing', 'recovery', 'not healing', 'slow healing']):
            if 'delayed healing' not in symptoms_tracked:
                symptoms_tracked.append('delayed healing')
            if 'delayed healing' in prompted:
                prompted.remove('delayed healing')
            if last_prompted == 'delayed healing':
                patient_conversations[patient_id]['last_prompted_symptom'] = None

        # If user gave a generic answer to the last prompted symptom (e.g., yes/no/okay), mark it as answered
        generic_ack_words = ['yes', 'no', 'yeah', 'nope', 'ok', 'okay', 'fine', 'better', 'worse', 'same', 'normal', 'not sure']
        if last_prompted and (any(w in user_message_lower for w in generic_ack_words) or len(user_message_lower.split()) <= 4):
            if last_prompted not in symptoms_tracked:
                symptoms_tracked.append(last_prompted)
            if last_prompted in prompted:
                prompted.remove(last_prompted)
            patient_conversations[patient_id]['last_prompted_symptom'] = None

        # If enough symptoms addressed, mark assessment stage
        if len(symptoms_tracked) >= 5:
            patient_conversations[patient_id]['dialogue_stage'] = 'assessment_complete'
        
        # Update hospital dashboard (always keep it current)
        update_patients_list(patient_id)
        
        return jsonify(response)
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def truncate_text(text, max_chars=500):
    """Truncate text to maximum characters"""
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "..."

def get_chat_response(patient_id, user_message, language='en'):
    """Get chat response from LLM with risk assessment and language support"""
    if not client:
        error_msg = "Error: Groq API is not configured. Please add GROQ_API_KEY to your .env file and restart the server."
        if language == 'ta':
            error_msg = "பிழை: Groq API கட்டமைக்கப்படவில்லை. தயவுசெய்து .env கோப்பில் GROQ_API_KEY சேர்க்கவும்."
        return {
            'message': error_msg,
            'risk_level': 'unknown',
            'details': {}
        }
    
    try:
        patient_data = patient_conversations.get(patient_id, {})
        is_tamil = language == 'ta'
        
        # Build context from uploads (truncated to save tokens)
        context = ""
        if patient_data.get('uploads'):
            context = "Medical Data: "
            # Only take the most recent upload and truncate it
            latest_upload = patient_data['uploads'][-1] if patient_data['uploads'] else None
            if latest_upload:
                analysis = latest_upload.get('analysis', '')
                context += truncate_text(analysis, max_chars=400)  # Limit to 400 chars
        
        # Build conversation history (limit to last 3 messages, truncate each)
        conversation_history = ""
        recent_messages = patient_data.get('conversation', [])[-3:]  # Last 3 messages only
        for msg in recent_messages:
            role_name = "Patient" if msg['role'] == 'user' else "Assistant"
            content = truncate_text(msg['content'], max_chars=200)  # Limit each message to 200 chars
            conversation_history += f"{role_name}: {content}\n"
        
        # Enhanced system prompt with surgery-focused dialogue flow
        surgery_info = patient_data.get('surgery_info', {})
        surgery_type = surgery_info.get('surgery_type', '')
        complications = surgery_info.get('common_complications', [])
        symptoms_asked = patient_data.get('symptoms_asked', [])
        symptoms_prompted = patient_data.get('symptoms_prompted', [])
        last_prompted_symptom = patient_data.get('last_prompted_symptom', None)
        pain_followups = patient_data.setdefault('pain_followups', {'asked_location': False, 'asked_intensity': False})
        dialogue_stage = patient_data.get('dialogue_stage', 'initial')

        # Handle user complaint about repeated questions: skip last prompted symptom
        try:
            uml = user_message.lower()
            if any(x in uml for x in ['repeat', 'repeated', 'again', 'same question']):
                if last_prompted_symptom and last_prompted_symptom not in symptoms_asked:
                    symptoms_asked.append(last_prompted_symptom)
                    patient_conversations[patient_id]['symptoms_asked'] = symptoms_asked
        except Exception:
            pass

        # Determine which symptoms haven't been asked
        key_symptoms = ['pain', 'swelling', 'bleeding', 'infection', 'delayed healing', 'fever', 'discharge']
        # Avoid re-prompting symptoms already prompted or answered
        remaining_symptoms = [s for s in key_symptoms if s not in set(symptoms_asked + symptoms_prompted)]
        
        # Determine if we should assess risk now or continue asking
        all_key_symptoms_asked = len(symptoms_asked) >= 5 or dialogue_stage == 'assessment_complete'
        should_assess_risk = all_key_symptoms_asked or 'severe' in user_message.lower() or 'emergency' in user_message.lower()
        
        # Language-specific system prompts
        if is_tamil:
            system_prompt = f"""நீங்கள் ஒரு மருத்துவ உதவியாளர் பாட். அறுவை சிகிச்சைக்குப் பிறகு பராமரிப்புக்காக நோயாளிகளுக்கு உதவுகிறீர்கள். அறுவை சிகிச்சை: {surgery_type if surgery_type else 'தெரியவில்லை'}.

மிக முக்கியமான விதிகள்:
1. எல்லா கேள்விகளையும் தமிழில் மட்டுமே கேட்கவும் - ஒருபோதும் ஆங்கிலத்தில் கேட்காதீர்கள்
2. ஒவ்வொரு பதிலுக்கும் ஒரு கேள்வியை மட்டும் கேட்கவும் - ஒருபோதும் பல கேள்விகளை ஒரே நேரத்தில் கேட்காதீர்கள்
3. அடுத்த கேள்வியைக் கேட்க முன்பு நோயாளியின் பதிலுக்குக் காத்திருக்கவும்
4. அனைத்து அறிகுறிகளும் மதிப்பீடு செய்யப்படும் வரை பரிந்துரைகளை வழங்காதீர்கள்
5. எப்போதும் பச்சாதாபமாகவும் தொழில்முறையாகவும் இருங்கள்

உரையாடல் பாய்வு:
- கேட்க வேண்டிய அறிகுறிகள் (ஒரு நேரத்தில் ஒன்று): வலி, வீக்கம், இரத்தப்போக்கு, தொற்று, குணமடைய தாமதம்
- ஒவ்வொரு பதிலுக்கும் பிறகு, அது உயர் ஆபத்து (கடுமையான/அவசர) என்பதை பகுப்பாய்வு செய்யவும் அல்லது தொடர்ந்து கேட்கவும்
- 5 அறிகுறிகளும் கேட்கப்பட்ட பிறகு, முழுமையான ஆபத்து மதிப்பீடு மற்றும் பரிந்துரைகளை வழங்கவும்

தற்போதைய நிலை: {'மதிப்பீடு' if should_assess_risk else 'கேள்விகள் கேட்கிறது'}
ஏற்கனவே கேட்ட அறிகுறிகள்: {', '.join(symptoms_asked) if symptoms_asked else 'இல்லை'}

முக்கியம்: நீங்கள் அனுப்பும் எல்லா பதில்களும், கேள்விகளும், பரிந்துரைகளும் தமிழில் மட்டுமே இருக்க வேண்டும். ஆங்கிலத்தில் எதுவும் எழுத வேண்டாம்."""
        else:
            system_prompt = f"""Medical assistant for post-surgery care. Surgery: {surgery_type if surgery_type else 'Unknown'}.

CRITICAL RULES:
1. Ask ONLY ONE question per response - never ask multiple questions at once
2. Wait for patient's answer before asking the next question
3. Do NOT provide recommendations until all symptoms are assessed
4. Be empathetic and professional

Dialogue flow:
- Symptoms to ask (ONE at a time): pain, swelling, bleeding, infection, delayed healing
- After EACH answer, analyze if it indicates HIGH RISK (severe/urgent) or continue asking
- Only after all 5 symptoms asked, provide full risk assessment and recommendations

Current stage: {'ASSESSMENT' if should_assess_risk else 'ASKING QUESTIONS'}
Symptoms already asked: {', '.join(symptoms_asked) if symptoms_asked else 'None'}
"""
        
        # Build context-aware prompt
        user_context = ""
        if surgery_type and surgery_type != "Unknown":
            user_context = f"Surgery: {surgery_type}.\n"
        
        if symptoms_asked:
            user_context += f"Asked about: {', '.join(symptoms_asked)}.\n"
        
        # Build compact user prompt
        user_prompt = user_message
        if context or user_context:
            prompt_parts = []
            if user_context:
                prompt_parts.append(user_context)
            if context:
                prompt_parts.append(f"Report: {context}")
            if conversation_history:
                prompt_parts.append(f"Recent chat:\n{conversation_history}")
            prompt_parts.append(f"Patient: {user_message}")
            user_prompt = "\n".join(prompt_parts)
        
        # Add specific guidance for next question or assessment
        if is_tamil:
            if should_assess_risk:
                user_prompt += "\n\nஉங்களிடம் போதுமான தகவல்கள் உள்ளன. இப்போது ஆபத்து நிலையை மதிப்பீடு செய்து பரிந்துரைகளை வழங்கவும். பயன்படுத்தவும்: [RISK_LEVEL: LOW/MODERATE/HIGH]"
            elif remaining_symptoms and dialogue_stage == 'symptoms_inquiry':
                next_symptom = remaining_symptoms[0]
                symptom_questions_ta = {
                    'pain': 'வலியை விவரிக்க முடியுமா? அது மிதமான, நடுத்தர, அல்லது கடுமையானதா?',
                    'swelling': 'அறுவை சிகிச்சை தளத்தில் எந்த வீக்கமும் உள்ளதா? அதை எப்படி விவரிப்பீர்கள்?',
                    'bleeding': 'நீங்கள் எந்த இரத்தப்போக்கையும் கவனித்தீர்களா? அது இலேசான, நடுத்தர, அல்லது கனமானதா?',
                    'infection': 'உங்களுக்கு காய்ச்சல், சீழ், வெளியேற்றம் அல்லது தொற்று அறிகுறிகள் உள்ளனவா?',
                    'delayed healing': 'காயம் சாதாரணமாக குணமாகிறதா, அல்லது குணமடைய தாமதம் குறித்த கவலைகள் உள்ளனவா?'
                }
                question = symptom_questions_ta.get(next_symptom, f'{next_symptom} பற்றி சொல்லுங்கள்.')
                user_prompt += f"\n\nஇந்த ஒரு கேள்வியை மட்டும் கேட்கவும்: '{question}' பல கேள்விகளை கேட்காதீர்கள். பதிலுக்கு காத்திருக்கவும்."
        else:
            if should_assess_risk:
                user_prompt += "\n\nYou have enough information. Assess risk level NOW and provide recommendations. Use format: [RISK_LEVEL: LOW/MODERATE/HIGH]"
            elif remaining_symptoms and dialogue_stage == 'symptoms_inquiry':
                next_symptom = remaining_symptoms[0]
                symptom_questions = {
                    'pain': 'Can you describe the pain? Is it mild, moderate, or severe?',
                    'swelling': 'Is there any swelling at the surgical site? How would you describe it?',
                    'bleeding': 'Have you noticed any bleeding? Is it light, moderate, or heavy?',
                    'infection': 'Do you have a fever, pus, discharge, or signs of infection?',
                    'delayed healing': 'Is the wound healing normally, or are there concerns about delayed healing?'
                }
                question = symptom_questions.get(next_symptom, f'Tell me about {next_symptom}.')
                user_prompt += f"\n\nAsk ONLY this ONE question: '{question}' Do NOT ask multiple questions. Wait for answer."
                # Track that we have prompted this symptom to avoid repetition
                if next_symptom not in symptoms_prompted:
                    symptoms_prompted.append(next_symptom)
                    patient_conversations[patient_id]['symptoms_prompted'] = symptoms_prompted
                    patient_conversations[patient_id]['last_prompted_symptom'] = next_symptom
        
        chat_completion = client.chat.completions.create(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            model=GROQ_MODEL,
            temperature=0.7,
            max_tokens=500  # Limit response to save tokens
        )
        
        response_text = chat_completion.choices[0].message.content
        
        # Extract risk level from response
        risk_level = 'unknown'
        details = {}
        
        if '[RISK_LEVEL: HIGH]' in response_text:
            risk_level = 'high'
            response_text = response_text.replace('[RISK_LEVEL: HIGH]', '').strip()
        elif '[RISK_LEVEL: MODERATE]' in response_text:
            risk_level = 'moderate'
            response_text = response_text.replace('[RISK_LEVEL: MODERATE]', '').strip()
        elif '[RISK_LEVEL: LOW]' in response_text:
            risk_level = 'low'
            response_text = response_text.replace('[RISK_LEVEL: LOW]', '').strip()
        
        if '[DETAILS:' in response_text:
            details_start = response_text.find('[DETAILS:')
            details_end = response_text.find(']', details_start)
            if details_end != -1:
                details_text = response_text[details_start+9:details_end]
                details = {'summary': details_text}
                response_text = response_text[:details_start] + response_text[details_end+1:]
        
        # Clean up response
        response_text = response_text.strip()
        
        # Add structured recommendations ONLY after full assessment
        patient_data = patient_conversations.get(patient_id, {})
        symptoms_count = len(patient_data.get('symptoms_asked', []))
        dialogue_stage = patient_data.get('dialogue_stage', 'initial')
        
        # Only provide full recommendations if we've asked multiple symptoms or risk is assessed
        if risk_level == 'high':
            warning = "\n\n⚠️ HIGH RISK DETECTED ⚠️\n\nBased on your symptoms, this requires URGENT medical attention:\n\n1. Contact your doctor IMMEDIATELY\n2. Go to emergency care if symptoms are severe\n3. Do NOT delay - complications can worsen quickly\n\nYour doctor has been automatically notified."
            response_text += warning
            # Update dialogue stage and ensure hospital dashboard is updated
            if patient_id in patient_conversations:
                patient_conversations[patient_id]['dialogue_stage'] = 'urgent_care'
                patient_conversations[patient_id]['risk_level'] = 'high'
                # Force update to hospital dashboard
                update_patients_list(patient_id)
        elif risk_level == 'low' and (symptoms_count >= 3 or dialogue_stage == 'assessment_complete'):
            # Only show recommendations if we've gathered enough information
            if 'preventive' not in response_text.lower() and 'medication' not in response_text.lower() and 'recommendation' not in response_text.lower():
                recommendations = "\n\n💡 PREVENTIVE MEASURES & HOME CARE:\n\n"
                surgery_info = patient_conversations.get(patient_id, {}).get('surgery_info', {})
                surgery_type = surgery_info.get('surgery_type', 'surgery')
                
                recommendations += f"• Keep the surgical site clean and dry\n"
                recommendations += f"• Take prescribed medications as directed\n"
                recommendations += f"• Watch for signs of infection (fever, redness, pus)\n"
                recommendations += f"• Avoid strenuous activities during recovery\n"
                recommendations += f"• Follow your doctor's post-operative instructions\n\n"
                recommendations += f"SUITABLE MEDICATIONS (consult doctor first):\n"
                recommendations += f"• Pain management: Acetaminophen or Ibuprofen (as prescribed)\n"
                recommendations += f"• Infection prevention: Keep area clean, change dressings regularly\n"
                recommendations += f"• Swelling reduction: Apply ice packs, elevate if applicable\n\n"
                recommendations += "⚠️ Monitor closely. Contact doctor if symptoms worsen or persist."
                response_text += recommendations
            
            if patient_id in patient_conversations:
                patient_conversations[patient_id]['dialogue_stage'] = 'follow_up'
                # Update hospital dashboard
                update_patients_list(patient_id)
        elif risk_level == 'low' and symptoms_count < 3:
            # Still gathering information, don't show full recommendations yet
            if patient_id in patient_conversations:
                patient_conversations[patient_id]['dialogue_stage'] = 'symptoms_inquiry'
        
        return {
            'message': response_text,
            'risk_level': risk_level,
            'details': details
        }
    
    except Exception as e:
        return {
            'message': f"Error processing request: {str(e)}",
            'risk_level': 'unknown',
            'details': {}
        }

def update_patients_list(patient_id):
    """Update the patients list for hospital dashboard"""
    patient_data = patient_conversations.get(patient_id, {})
    contact = patient_data.get('contact', {})
    contact_name = (contact.get('name') or '').strip() if isinstance(contact, dict) else ''
    
    # Find and update or create patient entry
    patient_entry = None
    for p in patients_data:
        if p['patient_id'] == patient_id:
            patient_entry = p
            break
    
    if not patient_entry:
        patient_entry = {
            'patient_id': patient_id,
            'name': contact_name or f'Patient {patient_id}',
            'risk_level': patient_data.get('risk_level', 'unknown'),
            'last_updated': datetime.now().isoformat(),
            'details': patient_data.get('details', {}),
            'conversation_count': len(patient_data.get('conversation', [])),
            'upload_count': len(patient_data.get('uploads', [])),
            'surgery_info': patient_data.get('surgery_info', {}),
            'symptoms_asked': patient_data.get('symptoms_asked', [])
        }
        patients_data.append(patient_entry)
    else:
        if contact_name:
            patient_entry['name'] = contact_name
        patient_entry['risk_level'] = patient_data.get('risk_level', patient_entry['risk_level'])
        patient_entry['last_updated'] = datetime.now().isoformat()
        patient_entry['details'] = patient_data.get('details', patient_entry['details'])
        patient_entry['conversation_count'] = len(patient_data.get('conversation', []))
        patient_entry['upload_count'] = len(patient_data.get('uploads', []))
        patient_entry['surgery_info'] = patient_data.get('surgery_info', patient_entry.get('surgery_info', {}))
        patient_entry['symptoms_asked'] = patient_data.get('symptoms_asked', patient_entry.get('symptoms_asked', []))
    
    # Sort: high risk first, then by last updated
    patients_data.sort(key=lambda x: (
        x['risk_level'] != 'high',
        x['risk_level'] == 'unknown',
        x['last_updated']
    ), reverse=False)

@app.route('/api/patients', methods=['GET'])
def get_patients():
    """Get all patients for hospital dashboard"""
    # Sort: high risk first, then low risk, then unknown
    sorted_patients = sorted(patients_data, key=lambda x: (
        x['risk_level'] != 'high',
        x['risk_level'] == 'unknown',
        x['last_updated']
    ))
    
    # Get full conversation and upload details for each patient
    for patient in sorted_patients:
        patient_id = patient['patient_id']
        if patient_id in patient_conversations:
            patient['full_conversation'] = patient_conversations[patient_id].get('conversation', [])
            patient['uploads'] = patient_conversations[patient_id].get('uploads', [])
    
    return jsonify(sorted_patients)

@app.route('/api/patient/<patient_id>', methods=['GET'])
def get_patient_details(patient_id):
    """Get detailed information about a specific patient"""
    if patient_id in patient_conversations:
        return jsonify(patient_conversations[patient_id])
    return jsonify({'error': 'Patient not found'}), 404

def analyze_xray_with_gradcam(image_path, filename, surgery_info=None):
    """Analyze X-ray image using Grad-CAM to highlight important regions.
    If surgery_info is provided, restrict highlights to the expected surgery region.
    """
    if not IMAGE_AVAILABLE:
        return "Image analysis libraries not available", None
    
    try:
        # Load and preprocess image
        img = Image.open(image_path).convert('RGB')
        img_array = np.array(img)
        
        # Resize if too large
        if img_array.shape[0] > 512 or img_array.shape[1] > 512:
            img.thumbnail((512, 512), Image.Resampling.LANCZOS)
            img_array = np.array(img)
        
        # Convert to grayscale if it's an X-ray (typically grayscale)
        if len(img_array.shape) == 3:
            gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
        else:
            gray = img_array
        
        # Normalize
        gray = gray.astype(np.float32) / 255.0
        # Simplified Grad-CAM simulation with clearer highlighting
        # 1) Build an activation map using gradients/edges as a proxy
        g_u8 = (gray * 255).astype(np.uint8)
        edges = cv2.Canny(g_u8, 50, 150)
        sobelx = cv2.Sobel(g_u8, cv2.CV_16S, 1, 0)
        sobely = cv2.Sobel(g_u8, cv2.CV_16S, 0, 1)
        sobel = cv2.convertScaleAbs(cv2.addWeighted(cv2.convertScaleAbs(sobelx), 0.5, cv2.convertScaleAbs(sobely), 0.5, 0))
        act_map = cv2.addWeighted(edges, 0.5, sobel, 0.5, 0)

        # 2) Smooth and normalize activation map
        act_map_blur = cv2.GaussianBlur(act_map, (9, 9), 0)
        act_map_norm = cv2.normalize(act_map_blur, None, 0, 255, cv2.NORM_MINMAX)
        # Remove border artifacts (2% margin)
        h_map, w_map = act_map_norm.shape[:2]
        m = max(2, int(0.02 * min(h_map, w_map)))
        act_map_norm[:m, :] = 0
        act_map_norm[-m:, :] = 0
        act_map_norm[:, :m] = 0
        act_map_norm[:, -m:] = 0

        # 3) If surgery_info suggests a location, build a region-of-interest (ROI) mask
        roi_mask = None
        region_note = None
        location_label = None
        if isinstance(surgery_info, dict) and surgery_info:
            st = f"{surgery_info.get('surgery_type','')} {surgery_info.get('site','')} {surgery_info.get('side','')}".lower()
            # Heuristic mapping to image regions
            h, w = act_map_norm.shape[:2]
            x0, y0, x1, y1 = 0, 0, w, h
            # Vertical thirds
            upper_band = (0, 0, w, h//3)
            middle_band = (0, h//3, w, 2*h//3)
            lower_band = (0, 2*h//3, w, h)
            # Horizontal halves
            left_half = (0, 0, w//2, h)
            right_half = (w//2, 0, w, h)

            # Choose vertical band
            if any(k in st for k in ['shoulder', 'elbow', 'wrist', 'hand', 'clavicle', 'lung', 'chest', 'thorax', 'rib']):
                ysel = upper_band
                region_note = 'upper'
                # Label
                if any(k in st for k in ['lung', 'chest', 'thorax', 'rib']):
                    location_label = 'Chest/Thorax'
                elif any(k in st for k in ['shoulder', 'clavicle', 'arm', 'elbow', 'wrist', 'hand']):
                    location_label = 'Shoulder/Arm'
            elif any(k in st for k in ['abdomen', 'stomach', 'liver', 'spleen', 'kidney']):
                ysel = middle_band
                region_note = 'middle'
                location_label = 'Abdomen'
            elif any(k in st for k in ['hip', 'pelvis', 'knee', 'ankle', 'foot', 'append', 'appendectomy', 'hernia']):
                ysel = lower_band
                region_note = 'lower'
                if any(k in st for k in ['hip', 'pelvis']):
                    location_label = 'Pelvis/Hips'
                elif any(k in st for k in ['knee', 'ankle', 'foot']):
                    location_label = 'Knee/Leg'
            else:
                ysel = (0, 0, w, h)

            # Choose left/right if specified
            if any(k in st for k in ['left', 'lt', 'lhs', 'l.']):
                xsel = left_half
                region_note = f"left {region_note or ''}".strip()
                if location_label:
                    location_label = f"Left {location_label}"
            elif any(k in st for k in ['right', 'rt', 'rhs', 'r.']):
                xsel = right_half
                region_note = f"right {region_note or ''}".strip()
                if location_label:
                    location_label = f"Right {location_label}"
            else:
                xsel = (0, 0, w, h)

            # Intersect selections
            xA0, yA0, xA1, yA1 = xsel
            xB0, yB0, xB1, yB1 = ysel
            x0, y0 = max(xA0, xB0), max(yA0, yB0)
            x1, y1 = min(xA1, xB1), min(yA1, yB1)
            roi_mask = np.zeros_like(act_map_norm, dtype=np.uint8)
            roi_mask[y0:y1, x0:x1] = 255

        # 4) If ROI exists, zero activations outside it so ONLY the affected part is colored
        inner_roi_mask = None
        if roi_mask is not None:
            act_map_norm = cv2.bitwise_and(act_map_norm, roi_mask)
            # Build an inner ROI (shrunken by 10%) to bias selection toward the core region
            try:
                w_roi = x1 - x0
                h_roi = y1 - y0
                inset_x = max(1, int(0.1 * w_roi))
                inset_y = max(1, int(0.1 * h_roi))
                ix0, iy0 = x0 + inset_x, y0 + inset_y
                ix1, iy1 = x1 - inset_x, y1 - inset_y
                inner_roi_mask = np.zeros_like(roi_mask, dtype=np.uint8)
                if ix1 > ix0 and iy1 > iy0:
                    inner_roi_mask[iy0:iy1, ix0:ix1] = 255
                else:
                    inner_roi_mask = roi_mask.copy()
            except Exception:
                inner_roi_mask = roi_mask.copy()

        # 5) Create color heatmap from the (possibly masked) activations
        heatmap = cv2.applyColorMap(act_map_norm, cv2.COLORMAP_VIRIDIS)

        # 6) Overlay heatmap on original grayscale RGB
        img_rgb = cv2.cvtColor(g_u8, cv2.COLOR_GRAY2RGB)
        overlay = cv2.addWeighted(img_rgb, 0.6, heatmap, 0.4, 0)

        # 7) Highlight top-activation regions with contour outlines, restricted by ROI if present
        # Threshold at high percentile to get hotspots (tighten to 90th)
        thresh_val = int(np.percentile(act_map_norm.flatten(), 90))
        _, mask = cv2.threshold(act_map_norm, thresh_val, 255, cv2.THRESH_BINARY)
        if roi_mask is not None:
            # Emphasize ROI center using a Gaussian weight
            ry, rx = np.where(roi_mask > 0)
            if len(ry) > 0:
                y0, y1 = ry.min(), ry.max()
                x0, x1 = rx.min(), rx.max()
                cy, cx = (y0 + y1) // 2, (x0 + x1) // 2
                yy, xx = np.mgrid[0:h_map, 0:w_map]
                sigma_y = max(5, (y1 - y0) / 4)
                sigma_x = max(5, (x1 - x0) / 4)
                gauss = np.exp(-(((yy - cy) ** 2) / (2 * sigma_y ** 2) + ((xx - cx) ** 2) / (2 * sigma_x ** 2)))
                gauss = (255 * (gauss / gauss.max())).astype(np.uint8)
                weighted = cv2.multiply(mask, gauss, scale=1/255.0)
                mask = cv2.bitwise_and(weighted, roi_mask)
            else:
                mask = cv2.bitwise_and(mask, roi_mask)
        # Clean small noise
        kernel = np.ones((3, 3), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
        mask = cv2.morphologyEx(mask, cv2.MORPH_DILATE, kernel, iterations=1)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        # Filter contours to those overlapping the INNER ROI core when available
        filtered_contours = []
        if inner_roi_mask is not None:
            for cnt in contours:
                tmp = np.zeros_like(mask)
                cv2.drawContours(tmp, [cnt], -1, 255, thickness=-1)
                overlap = cv2.countNonZero(cv2.bitwise_and(tmp, inner_roi_mask))
                area_cnt = max(1, cv2.countNonZero(tmp))
                if overlap / float(area_cnt) >= 0.3:  # at least 30% inside inner ROI
                    filtered_contours.append(cnt)
        else:
            filtered_contours = contours

        # Fallback if nothing passes filter: keep the largest original contour (if any)
        if inner_roi_mask is not None and not filtered_contours and contours:
            largest = max(contours, key=cv2.contourArea)
            filtered_contours = [largest]

        highlighted_regions = 0
        for cnt in filtered_contours:
            area = cv2.contourArea(cnt)
            if area < 50:  # skip tiny specks
                continue
            highlighted_regions += 1
            # Draw teal outline and semi-transparent fill (RGB)
            teal = (0, 200, 180)
            cv2.drawContours(overlay, [cnt], -1, teal, thickness=2)
            cv2.fillPoly(overlay, [cnt], color=teal, lineType=cv2.LINE_AA)
        # Slightly blend back to reduce solid fill intensity
        overlay = cv2.addWeighted(img_rgb, 0.35, overlay, 0.65, 0)

        # Save Grad-CAM visualization with outlines
        gradcam_filename = f"gradcam_{filename}"
        gradcam_path = os.path.join(app.config['UPLOAD_FOLDER'], gradcam_filename)
        cv2.imwrite(gradcam_path, cv2.cvtColor(overlay, cv2.COLOR_RGB2BGR))
        
        # Analyze regions
        analysis_text = (
            "X-ray Analysis:\n"
            f"- Image dimensions: {img_array.shape}\n"
            f"- Highlighted regions (approx.): {highlighted_regions}\n"
            + (f"- Region focus: {region_note}\n" if region_note else "") +
            (f"- Estimated location: {location_label}\n" if location_label else "") +
            "- Areas of interest outlined in teal within the surgery region and emphasized on the heatmap"
        )
        
        return analysis_text, gradcam_path
    except Exception as e:
        return f"X-ray analysis completed with basic processing. Error: {str(e)}", None

@app.route('/api/download-report/<patient_id>', methods=['GET'])
def download_report(patient_id):
    """Generate and download patient report as PDF"""
    if patient_id not in patient_conversations:
        return jsonify({'error': 'Patient not found'}), 404
    
    if not REPORT_AVAILABLE:
        return jsonify({'error': 'PDF generation not available'}), 500
    
    try:
        from io import BytesIO
        patient_data = patient_conversations[patient_id]
        
        # Create PDF
        buffer = BytesIO()
        c = canvas.Canvas(buffer, pagesize=letter)
        width, height = letter
        
        # Title
        c.setFont("Helvetica-Bold", 20)
        c.drawString(50, height - 50, "Medical Report - Patient Analysis")
        
        y_position = height - 100
        
        # Patient Information
        c.setFont("Helvetica-Bold", 14)
        c.drawString(50, y_position, "Patient Information")
        y_position -= 25
        
        c.setFont("Helvetica", 12)
        c.drawString(50, y_position, f"Patient ID: {patient_id}")
        y_position -= 20
        c.drawString(50, y_position, f"Risk Level: {patient_data.get('risk_level', 'Unknown').upper()}")
        y_position -= 20
        c.drawString(50, y_position, f"Report Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        y_position -= 40
        
        # Surgery Information
        surgery_info = patient_data.get('surgery_info', {})
        if surgery_info.get('surgery_type'):
            c.setFont("Helvetica-Bold", 14)
            c.drawString(50, y_position, "Surgery Information")
            y_position -= 25
            
            c.setFont("Helvetica", 12)
            c.drawString(50, y_position, f"Surgery Type: {surgery_info.get('surgery_type', 'N/A')}")
            y_position -= 20
            if surgery_info.get('surgery_date'):
                c.drawString(50, y_position, f"Surgery Date: {surgery_info.get('surgery_date', 'N/A')}")
                y_position -= 20
            y_position -= 20
        
        # Uploaded Files
        uploads = patient_data.get('uploads', [])
        if uploads:
            c.setFont("Helvetica-Bold", 14)
            c.drawString(50, y_position, "Uploaded Files")
            y_position -= 25
            
            c.setFont("Helvetica", 12)
            for upload in uploads[-5:]:  # Last 5 uploads
                if y_position < 100:
                    c.showPage()
                    y_position = height - 50
                
                c.drawString(50, y_position, f"File: {upload.get('filename', 'N/A')}")
                y_position -= 15
                analysis = upload.get('analysis', '')
                if analysis:
                    # Wrap long text
                    text_lines = []
                    words = analysis.split()
                    current_line = ""
                    for word in words:
                        if len(current_line + word) < 80:
                            current_line += word + " "
                        else:
                            text_lines.append(current_line)
                            current_line = word + " "
                    if current_line:
                        text_lines.append(current_line)
                    
                    for line in text_lines[:5]:  # Limit to 5 lines
                        if y_position < 100:
                            c.showPage()
                            y_position = height - 50
                        c.setFont("Helvetica", 10)
                        c.drawString(70, y_position, line[:90])
                        y_position -= 15
                
                # If Grad-CAM is present for this upload, embed image and brief analysis
                gradcam_path = upload.get('gradcam_image_path')
                gradcam_text = upload.get('gradcam_analysis')
                if gradcam_path and os.path.exists(gradcam_path):
                    try:
                        # Space before image
                        y_position -= 5
                        if y_position < 200:
                            c.showPage()
                            y_position = height - 50
                        c.setFont("Helvetica-Oblique", 11)
                        c.drawString(70, y_position, "Grad-CAM Visualization (areas of interest highlighted)")
                        y_position -= 10
                        # Add surgery focus label if available
                        # Prefer upload-level surgery_info, fallback to patient-level
                        try:
                            upload_si = upload.get('surgery_info', {}) or {}
                            patient_si = patient_data.get('surgery_info', {}) or {}
                            si = upload_si if upload_si.get('surgery_type') else patient_si
                            if si and si.get('surgery_type'):
                                focus_text = f"Surgery focus: {si.get('surgery_type','')}"
                                side = si.get('side') or ''
                                site = si.get('site') or ''
                                extra = ' '.join([x for x in [side, site] if x])
                                if extra:
                                    focus_text += f" ({extra})"
                                c.setFont("Helvetica", 10)
                                c.drawString(70, y_position, focus_text[:100])
                                y_position -= 12
                        except Exception:
                            pass
                        # Fit image to page width with max height
                        max_img_width = width - 120
                        max_img_height = 220
                        img_reader = ImageReader(gradcam_path)
                        img_w, img_h = img_reader.getSize()
                        scale = min(max_img_width / img_w, max_img_height / img_h)
                        draw_w = img_w * scale
                        draw_h = img_h * scale
                        c.drawImage(img_reader, 70, y_position - draw_h, width=draw_w, height=draw_h)
                        y_position -= (draw_h + 10)
                        # Grad-CAM analysis text (1-2 lines)
                        if gradcam_text:
                            c.setFont("Helvetica", 10)
                            lines = []
                            words = gradcam_text.split()
                            curr = ""
                            for w in words:
                                if len(curr + w) < 90:
                                    curr += w + " "
                                else:
                                    lines.append(curr)
                                    curr = w + " "
                            if curr:
                                lines.append(curr)
                            for line in lines[:2]:
                                if y_position < 100:
                                    c.showPage()
                                    y_position = height - 50
                                c.drawString(70, y_position, line[:100])
                                y_position -= 14
                    except Exception:
                        # If embedding fails, continue without blocking report generation
                        pass

                y_position -= 10
        
        # Conversation Summary
        conversation = patient_data.get('conversation', [])
        if conversation:
            if y_position < 150:
                c.showPage()
                y_position = height - 50
            
            c.setFont("Helvetica-Bold", 14)
            c.drawString(50, y_position, "Conversation Summary")
            y_position -= 25
            
            c.setFont("Helvetica", 12)
            c.drawString(50, y_position, f"Total Messages: {len(conversation)}")
            y_position -= 20
            
            # Symptoms asked
            symptoms = patient_data.get('symptoms_asked', [])
            if symptoms:
                c.drawString(50, y_position, f"Symptoms Discussed: {', '.join(symptoms)}")
                y_position -= 20

            # Full Conversation
            if y_position < 120:
                c.showPage()
                y_position = height - 50
            c.setFont("Helvetica-Bold", 14)
            c.drawString(50, y_position, "Full Conversation")
            y_position -= 20
            c.setFont("Helvetica", 10)
            # Print each message chronologically
            for msg in conversation:
                role = 'Patient' if msg.get('role') == 'user' else 'Assistant'
                timestamp = msg.get('timestamp', '')
                header = f"{role} ({timestamp}):"
                # Header line
                if y_position < 80:
                    c.showPage()
                    y_position = height - 50
                c.setFont("Helvetica-Bold", 10)
                c.drawString(50, y_position, header[:110])
                y_position -= 12
                # Message content wrapped
                c.setFont("Helvetica", 10)
                content_text = (msg.get('content') or '').replace('\r', '')
                words = content_text.split()
                line = ''
                for w in words:
                    if len(line + w) < 110:
                        line += w + ' '
                    else:
                        if y_position < 70:
                            c.showPage()
                            y_position = height - 50
                            c.setFont("Helvetica", 10)
                        c.drawString(60, y_position, line[:120])
                        y_position -= 12
                        line = w + ' '
                if line:
                    if y_position < 70:
                        c.showPage()
                        y_position = height - 50
                        c.setFont("Helvetica", 10)
                    c.drawString(60, y_position, line[:120])
                    y_position -= 14
                # Spacer between messages
                y_position -= 4
        
        # Recommendations
        details = patient_data.get('details', {})
        if details.get('summary'):
            if y_position < 100:
                c.showPage()
                y_position = height - 50
            
            c.setFont("Helvetica-Bold", 14)
            c.drawString(50, y_position, "Summary & Recommendations")
            y_position -= 25
            
            c.setFont("Helvetica", 12)
            summary = details.get('summary', '')
            words = summary.split()
            current_line = ""
            for word in words:
                if len(current_line + word) < 80:
                    current_line += word + " "
                else:
                    c.drawString(50, y_position, current_line)
                    y_position -= 15
                    current_line = word + " "
                    if y_position < 100:
                        c.showPage()
                        y_position = height - 50
            if current_line:
                c.drawString(50, y_position, current_line)
        
        c.save()
        buffer.seek(0)
        
        # Return PDF
        report_filename = f"medical_report_{patient_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        return send_file(
            buffer,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=report_filename
        )
    
    except Exception as e:
        return jsonify({'error': f'Error generating report: {str(e)}'}), 500

@app.route('/api/gradcam-image/<filename>', methods=['GET'])
def get_gradcam_image(filename):
    """Serve Grad-CAM processed image"""
    try:
        gradcam_path = os.path.join(app.config['UPLOAD_FOLDER'], f"gradcam_{filename}")
        if os.path.exists(gradcam_path):
            return send_file(gradcam_path, mimetype='image/jpeg')
        return jsonify({'error': 'Grad-CAM image not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5000)

