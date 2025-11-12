# Medical Dashboard System

A comprehensive medical dashboard system with separate interfaces for patients and hospitals, powered by Flask backend and Groq LLM.

## Features

### Patient Dashboard
- Upload medical scans and reports
- AI-powered analysis of uploaded documents
- Interactive chatbot that asks relevant medical questions
- Risk assessment (Low/High)
- Personalized recommendations for home medication and prevention (Low Risk)
- Urgent doctor consultation warnings (High Risk)

### Hospital Dashboard
- View all patients sorted by risk level (High Risk first)
- See patient conversation history
- View uploaded medical documents
- Call button for each patient
- Detailed patient information modal

## Setup Instructions

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure Groq API Key

Create a `.env` file in the root directory:

```
GROQ_API_KEY=your_groq_api_key_here
```

Get your API key from: https://console.groq.com/

### 3. Create Uploads Directory

The uploads directory will be created automatically when you run the app, but you can create it manually:

```bash
mkdir uploads
```

### 4. Run the Application

```bash
python app.py
```

The application will start on `http://localhost:5000`

### 5. Access Dashboards

- **Patient Dashboard**: http://localhost:5000/
- **Hospital Dashboard**: http://localhost:5000/doctor

## Project Structure

```
Medical/
├── app.py                      # Flask backend application
├── requirements.txt            # Python dependencies
├── .env                        # Environment variables (create this)
├── README.md                   # This file
├── templates/
│   ├── patient_dashboard.html  # Patient interface
│   └── doctor_dashboard.html   # Hospital interface
├── static/
│   ├── patient_styles.css      # Patient dashboard styles
│   ├── doctor_styles.css       # Hospital dashboard styles
│   ├── patient_script.js       # Patient dashboard JavaScript
│   └── doctor_script.js        # Hospital dashboard JavaScript
└── uploads/                    # Uploaded medical files (auto-created)
```

## Usage

### For Patients:
1. Visit the patient dashboard
2. Upload your medical reports/scans by dragging and dropping or clicking to browse
3. Wait for the AI to analyze your documents
4. Start chatting with the medical assistant
5. Answer questions about your condition, surgeries, medications, etc.
6. Receive recommendations based on your risk level

### For Hospitals:
1. Visit the hospital dashboard
2. View patients sorted by risk level (High Risk patients appear first)
3. Click on any patient card to see full details
4. Review conversation history and uploaded documents
5. Use the call button to contact patients

## API Endpoints

- `GET /` - Patient dashboard
- `GET /doctor` - Hospital dashboard
- `POST /api/upload` - Upload medical files
- `POST /api/chat` - Chat with AI assistant
- `GET /api/patients` - Get all patients (for hospital dashboard)
- `GET /api/patient/<patient_id>` - Get specific patient details

## Technologies Used

- **Backend**: Flask (Python)
- **Frontend**: HTML, CSS, JavaScript
- **LLM**: Groq API (Llama 3.1 70B)
- **File Handling**: Werkzeug

## Notes

- Currently uses in-memory storage. For production, implement a database (SQLite, PostgreSQL, etc.)
- File uploads are stored locally in the `uploads/` directory
- The call functionality in the hospital dashboard shows an alert. Integrate with WebRTC or a calling service for production use.

