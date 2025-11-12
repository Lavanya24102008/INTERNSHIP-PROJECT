let patientId = 'patient_' + Date.now();
let lastRiskLevelShown = null; // prevent repeated risk blocks
let uploadedFiles = [];
let currentLanguage = 'en'; // 'en' for English, 'ta' for Tamil
let isListening = false;
let recognition = null;
let isSending = false; // prevent concurrent sends and improve flow
let firstQuestionAsked = false; // legacy guard (kept if needed)
let introMessageSent = false; // ensure combined intro is posted once

// Contact details state
let contactInfo = { name: '', phone: '', email: '' };

// Hook up contact form on DOM ready
window.addEventListener('DOMContentLoaded', () => {
    const saveBtn = document.getElementById('saveContactBtn');
    if (saveBtn) {
        saveBtn.addEventListener('click', saveContactDetails);
    }
    // Lock upload and chat until contact details are saved
    lockMainSections();
    // If previously saved, auto-unlock and hide contact on reload
    try {
        const key = `contactSaved_${patientId}`;
        const saved = localStorage.getItem(key);
        if (saved === '1') {
            unlockMainSections();
            const contactSection = document.querySelector('.contact-section');
            if (contactSection) contactSection.style.display = 'none';
        }
    } catch (_) {}
});

async function saveContactDetails() {
    const nameEl = document.getElementById('contactName');
    const phoneEl = document.getElementById('contactPhone');
    const emailEl = document.getElementById('contactEmail');
    const statusEl = document.getElementById('contactStatus');

    const name = (nameEl?.value || '').trim();
    const phone = (phoneEl?.value || '').trim();
    const email = (emailEl?.value || '').trim();

    if (!name || !phone) {
        statusEl.textContent = 'Please enter your name and phone number.';
        statusEl.style.color = '#ef4444';
        return;
    }

    // Basic email check if provided
    if (email && !/^\S+@\S+\.\S+$/.test(email)) {
        statusEl.textContent = 'Please enter a valid email address.';
        statusEl.style.color = '#ef4444';
        return;
    }

    statusEl.textContent = 'Saving...';
    statusEl.style.color = '#555';

    try {
        const res = await fetch('/api/contact', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ patient_id: patientId, name, phone, email })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Failed to save');

        contactInfo = { name, phone, email };
        statusEl.textContent = 'Saved';
        statusEl.style.color = '#10b981';
        unlockMainSections();
        // Hide contact section after save
        const contactSection = document.querySelector('.contact-section');
        if (contactSection) contactSection.style.display = 'none';
        // Remember saved state
        try { localStorage.setItem(`contactSaved_${patientId}`, '1'); } catch (_) {}
    } catch (e) {
        statusEl.textContent = 'Error: ' + e.message;
        statusEl.style.color = '#ef4444';
    }
}

function lockMainSections() {
    const uploadSection = document.querySelector('.upload-section');
    const chatSection = document.querySelector('.chat-section');
    if (uploadSection) uploadSection.style.display = 'none';
    if (chatSection) chatSection.style.display = 'none';
}

function unlockMainSections() {
    const uploadSection = document.querySelector('.upload-section');
    const chatSection = document.querySelector('.chat-section');
    const contactSection = document.querySelector('.contact-section');
    if (uploadSection) uploadSection.style.display = '';
    if (chatSection) chatSection.style.display = '';
    if (contactSection) contactSection.style.display = 'none';
}

// Initialize speech recognition
if ('webkitSpeechRecognition' in window || 'SpeechRecognition' in window) {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    recognition = new SpeechRecognition();
    recognition.continuous = false;
    recognition.interimResults = false;
    recognition.lang = currentLanguage === 'ta' ? 'ta-IN' : 'en-US';
    
    recognition.onresult = function(event) {
        const transcript = event.results[0][0].transcript;
        chatInput.value = transcript;
        isListening = false;
        updateVoiceButton();
        hideVoiceStatus();
    };
    
    recognition.onerror = function(event) {
        console.error('Speech recognition error:', event.error);
        isListening = false;
        updateVoiceButton();
        showVoiceStatus('Error: ' + event.error, 'error');
    };
    
    recognition.onend = function() {
        isListening = false;
        updateVoiceButton();
    };
} else {
    console.warn('Speech recognition not supported');
}

// Add a single final report download button once the assessment is completed
function addFinalReportButtonOnce() {
    const messagesDiv = document.getElementById('chatMessages');
    if (!messagesDiv) return;
    if (document.getElementById('finalReportBtn')) return; // already added
    const wrap = document.createElement('div');
    wrap.className = 'message bot-message';
    wrap.innerHTML = `
        <div class="message-content">
            <button id="finalReportBtn" class="download-btn" onclick="downloadReport('${patientId}')" title="Download Final Report" style="background:#0ea5e9;color:#fff;border:none;padding:10px 14px;border-radius:8px;cursor:pointer;">
                📥 Download Final Report
            </button>
        </div>
    `;
    messagesDiv.appendChild(wrap);
    messagesDiv.scrollTop = messagesDiv.scrollHeight;
}

function addIntroMessage(text) {
    const messagesDiv = document.getElementById('chatMessages');
    // Remove any previous intro to avoid duplicates (race protection)
    const existing = messagesDiv.querySelector('[data-intro="1"]');
    if (existing) existing.remove();
    const wrap = document.createElement('div');
    wrap.className = 'message bot-message';
    wrap.setAttribute('data-intro', '1');
    wrap.innerHTML = `
        <div class="message-content">
            <strong>Medical Assistant:</strong>
            ${escapeHtml(text).replace(/\n/g, '<br>')}
        </div>
    `;
    messagesDiv.appendChild(wrap);
    messagesDiv.scrollTop = messagesDiv.scrollHeight;
}

function buildCombinedIntroText() {
    const names = uploadedFiles.map(f => `"${f.name}"`);
    const analyzedEn = names.length ? ` I've analyzed your uploaded ${names.length > 1 ? 'files' : 'file'} ${names.join(', ')}.` : '';
    const analyzedTa = names.length ? ` நான் உங்கள் பதிவேற்றிய ${names.length > 1 ? 'கோப்புகளை' : 'கோப்பை'} ${names.join(', ')} பகுப்பாய்வு செய்துள்ளேன்.` : '';
    if (currentLanguage === 'ta') {
        return `வணக்கம்! நான் அறுவைசிகிச்சைக்குப் பிறகான பராமரிப்பில் உதவுகிறேன். தயவுசெய்து உங்கள் அறிக்கையை அல்லது ஸ்கேனை பதிவேற்றுங்கள்; பின்னர் சிக்கல்கள் உள்ளதா என்பதை மதிப்பிட சில கேள்விகள் கேட்கிறேன்.${analyzedTa} உங்கள் நிலையை நன்றாகப் புரிந்து கொள்ள சில கேள்விகள் கேட்பேன். உங்கள் பிரச்சனை என்ன அல்லது எந்த அறிகுறிகளை அனுபவிக்கிறீர்கள்?`;
    }
    return `Hello! I help with post‑surgical care. Please upload your report or scan, then I'll ask a few focused questions to assess for any complications.${analyzedEn} Let me ask you some questions to better understand your condition. What is your problem or what symptoms are you experiencing?`;
}

function addRiskBlock(level, score) {
    const messagesDiv = document.getElementById('chatMessages');
    const container = document.createElement('div');
    container.className = 'message bot-message';

    const levelKey = (level === 'medium') ? 'moderate' : level;
    const cls = `risk-block ${levelKey}`;

    let title = '';
    let lines = [];
    if (levelKey === 'high') {
        title = '⚠️ HIGH RISK DETECTED';
        lines = [
            'Based on your symptoms, this requires URGENT medical attention:',
            '1. Contact your doctor IMMEDIATELY',
            '2. Go to emergency care if symptoms are severe',
            '3. Do NOT delay - complications can worsen quickly'
        ];
    } else if (levelKey === 'moderate') {
        title = 'ℹ️ MODERATE RISK';
        lines = [
            'Monitor symptoms closely and follow the guidance provided.',
            'Contact your doctor if symptoms persist or worsen.'
        ];
    } else if (levelKey === 'low') {
        title = '✅ LOW RISK';
        lines = [
            'Continue routine care and follow recommendations.',
            'Report any new or worsening symptoms.'
        ];
    } else {
        title = '🔎 RISK UNKNOWN';
        lines = [
            'Please provide more details or upload relevant reports for a better assessment.'
        ];
    }

    const content = document.createElement('div');
    content.className = `message-content ${cls}`;
    const scoreHtml = (typeof score === 'number' && !Number.isNaN(score)) ? ` <span style="font-weight:600;color:#0ea5e9;">(Score: ${score})</span>` : '';
    content.innerHTML = `
        <div class="risk-title">${title}${scoreHtml}</div>
        <div class="risk-lines">${lines.map(l => `<div>${escapeHtml(l)}</div>`).join('')}</div>
    `;
    container.appendChild(content);
    messagesDiv.appendChild(container);
    messagesDiv.scrollTop = messagesDiv.scrollHeight;
}

// File upload handling
const uploadArea = document.getElementById('uploadArea');
const fileInput = document.getElementById('fileInput');
const uploadStatus = document.getElementById('uploadStatus');
const uploadedFilesDiv = document.getElementById('uploadedFiles');
const chatInput = document.getElementById('chatInput');
const sendButton = document.getElementById('sendButton');
const voiceButton = document.getElementById('voiceButton');
const voiceStatus = document.getElementById('voiceStatus');
const langBtn = document.getElementById('langBtn');
const langDropdown = document.getElementById('langDropdown');

// Enable chat after first upload (now handled by the new enableChat function)

// Drag and drop
uploadArea.addEventListener('click', () => fileInput.click());

uploadArea.addEventListener('dragover', (e) => {
    e.preventDefault();
    uploadArea.classList.add('dragover');
});

uploadArea.addEventListener('dragleave', () => {
    uploadArea.classList.remove('dragover');
});

uploadArea.addEventListener('drop', (e) => {
    e.preventDefault();
    uploadArea.classList.remove('dragover');
    const files = e.dataTransfer.files;
    handleFiles(files);
});

fileInput.addEventListener('change', (e) => {
    handleFiles(e.target.files);
});

function handleFiles(files) {
    Array.from(files).forEach(file => {
        uploadFile(file);
    });
}

async function uploadFile(file) {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('patient_id', patientId);

    uploadStatus.style.display = 'block';
    uploadStatus.className = '';
    uploadStatus.textContent = `Uploading ${file.name}...`;

    try {
        const response = await fetch('/api/upload', {
            method: 'POST',
            body: formData
        });

        const data = await response.json();

        if (response.ok) {
            uploadStatus.className = 'success';
            uploadStatus.textContent = `✓ ${file.name} uploaded successfully!`;
            
            uploadedFiles.push({
                name: file.name,
                analysis: data.analysis
            });

            displayUploadedFile(file.name, data.analysis, data.is_image, data.gradcam_image_path, data.gradcam_analysis);
            enableChat();

            // Update the initial greeting to combine greeting + analyzed uploads into one text box
            updateCombinedIntro();

            // After two uploads, post the combined intro as the FIRST bot message and wait for user's answer
            if (uploadedFiles.length >= 2 && !introMessageSent) {
                const intro = buildCombinedIntroText();
                if (intro) {
                    addIntroMessage(intro);
                    introMessageSent = true;
                }
                // Do NOT auto-call requestNextQuestion here; we wait for user's answer first
            }

            setTimeout(() => {
                uploadStatus.style.display = 'none';
            }, 3000);
        } else {
            uploadStatus.className = 'error';
            uploadStatus.textContent = `✗ Error: ${data.error}`;
        }
    } catch (error) {
        uploadStatus.className = 'error';
        uploadStatus.textContent = `✗ Upload failed: ${error.message}`;
    }
}

function displayUploadedFile(filename, analysis, isImage = false, gradcamImagePath = null, gradcamAnalysis = null) {
    const fileDiv = document.createElement('div');
    fileDiv.className = 'uploaded-file';
    
    let content = `
        <div style="flex: 1;">
            <div class="file-name">${isImage ? '🖼️' : '📄'} ${filename}</div>
            <div class="file-analysis">${analysis ? analysis.substring(0, 200) + '...' : 'File uploaded successfully'}</div>
    `;
    
    if (isImage && gradcamImagePath) {
        // gradcamImagePath is like 'uploads/gradcam_<server_saved_filename>'
        // Extract the server-saved filename after the 'gradcam_' prefix
        const gradcamPathParts = gradcamImagePath.split(/[\\\/]/);
        const gradcamFileWithPrefix = gradcamPathParts.pop();
        const serverSavedName = gradcamFileWithPrefix.replace(/^gradcam_/, '');
        const encodedName = encodeURIComponent(serverSavedName);
        const errorId = `gradcam-error-${Math.random().toString(36).slice(2)}`;
        content += `
            <div class="gradcam-section" style="margin-top: 15px;">
                <h4 style="margin: 10px 0 5px 0; color: #667eea; font-size: 1.1em;">X-Ray Grad-CAM Analysis</h4>
                <img src="/api/gradcam-image/${encodedName}" alt="Grad-CAM Analysis" 
                     style="max-width: 100%; border: 2px solid #667eea; border-radius: 8px; margin: 10px 0; cursor: pointer;"
                     onclick="this.style.transform = this.style.transform === 'scale(1.5)' ? 'scale(1)' : 'scale(1.5)'; this.style.transition = 'transform 0.3s';"
                     onerror="document.getElementById('${errorId}').style.display='block'; this.style.display='none';">
                <div id="${errorId}" style="display:none; font-size: 0.9em; color: #e53e3e; margin-top: 8px;">Unable to load Grad-CAM image. Please try re-uploading the image.</div>
                ${gradcamAnalysis ? `<p style="font-size: 0.9em; color: #666; margin-top: 5px;">${gradcamAnalysis}</p>` : ''}
                <p style="font-size: 0.85em; color: #999; margin-top: 5px; font-style: italic;">Click image to zoom</p>
            </div>
        `;
    }
    
    content += `
        </div>
    `;
    
    fileDiv.innerHTML = content;
    uploadedFilesDiv.appendChild(fileDiv);
}

async function downloadReport(patientId) {
    try {
        const response = await fetch(`/api/download-report/${patientId}`);
        if (response.ok) {
            const blob = await response.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `medical_report_${patientId}_${Date.now()}.pdf`;
            document.body.appendChild(a);
            a.click();
            window.URL.revokeObjectURL(url);
            document.body.removeChild(a);
        } else {
            const error = await response.json();
            alert('Error downloading report: ' + (error.error || 'Unknown error'));
        }
    } catch (error) {
        alert('Error downloading report: ' + error.message);
    }
}

// Chat functionality
chatInput.addEventListener('keypress', (e) => {
    if (e.key === 'Enter' && !chatInput.disabled && !isSending) {
        sendMessage();
    }
});

async function sendMessage() {
    const message = chatInput.value.trim();
    if (!message || chatInput.disabled || isSending) return;

    // Add user message to chat
    addMessage('user', message);
    chatInput.value = '';
    // Disable inputs during request for professional flow
    isSending = true;
    chatInput.disabled = true;
    sendButton.disabled = true;
    voiceButton.disabled = true;

    // Show typing indicator
    const typingId = addTypingIndicator();

    try {
        const response = await fetch('/api/chat', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                patient_id: patientId,
                message: message,
                language: currentLanguage
            })
        });

        removeTypingIndicator(typingId);

        const data = await response.json();

        if (response.ok) {
            addMessage('bot', data.message);

            const level = (data.risk_level || '').toLowerCase();
            if (level && level !== 'unknown') {
                // If backend message already contains a risk banner text, don't add another block
                const msgText = (data.message || '').toLowerCase();
                const containsRiskBanner = msgText.includes('high risk detected') || msgText.includes('moderate risk') || msgText.includes('low risk');

                // Also skip if the same risk level was just shown
                if (!containsRiskBanner && level !== lastRiskLevelShown) {
                    // If the last chat bubble is already a risk block of same level, skip
                    const messagesDiv = document.getElementById('chatMessages');
                    const lastChild = messagesDiv.lastElementChild;
                    const isSameRiskLast = lastChild && lastChild.querySelector && lastChild.querySelector(`.message-content.risk-block.${level === 'medium' ? 'moderate' : level}`);
                    if (!isSameRiskLast) {
                        addRiskBlock(level, data.risk_score);
                        lastRiskLevelShown = level;
                    }
                }

                if (level === 'high') {
                    showHighRiskWarning();
                }
                // If the bot did not ask a question in this turn, treat as assessment end and show download button
                if (data.message && data.message.indexOf('?') === -1) {
                    addFinalReportButtonOnce();
                }
            } else {
                // Reset when no risk provided
                lastRiskLevelShown = null;
            }
        } else {
            addMessage('bot', `Error: ${data.error}`);
        }
    } catch (error) {
        removeTypingIndicator(typingId);
        addMessage('bot', `Error connecting to server: ${error.message}`);
    } finally {
        // Re-enable inputs
        isSending = false;
        chatInput.disabled = false;
        sendButton.disabled = false;
        voiceButton.disabled = false;
        chatInput.focus();
    }
}

function addMessage(role, content) {
    const messagesDiv = document.getElementById('chatMessages');
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${role}-message`;
    
    const roleName = role === 'user' ? 'You' : 'Medical Assistant';
    
    messageDiv.innerHTML = `
        <div class="message-content">
            <strong>${roleName}:</strong>
            ${escapeHtml(content).replace(/\n/g, '<br>')}
        </div>
    `;
    
    messagesDiv.appendChild(messageDiv);
    messagesDiv.scrollTop = messagesDiv.scrollHeight;
}

function addTypingIndicator() {
    const messagesDiv = document.getElementById('chatMessages');
    const typingDiv = document.createElement('div');
    typingDiv.id = 'typing-indicator';
    typingDiv.className = 'message bot-message';
    typingDiv.innerHTML = `
        <div class="message-content">
            <strong>Medical Assistant:</strong> <em>Typing...</em>
        </div>
    `;
    messagesDiv.appendChild(typingDiv);
    messagesDiv.scrollTop = messagesDiv.scrollHeight;
    return 'typing-indicator';
}

function removeTypingIndicator(id) {
    const typingDiv = document.getElementById(id);
    if (typingDiv) {
        typingDiv.remove();
    }
}

function showHighRiskWarning() {
    // The warning is already included in the bot message
    // This function can be extended for additional visual warnings
    const messagesDiv = document.getElementById('chatMessages');
    messagesDiv.scrollTop = messagesDiv.scrollHeight;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Language selection
document.querySelectorAll('.lang-option').forEach(option => {
    option.addEventListener('click', function() {
        currentLanguage = this.dataset.lang;
        langBtn.textContent = currentLanguage === 'ta' ? '🌐 Tamil' : '🌐 English';
        langDropdown.classList.remove('show');
        this.classList.add('active');
        document.querySelectorAll('.lang-option').forEach(opt => {
            if (opt !== this) opt.classList.remove('active');
        });
        
        // Update recognition language
        if (recognition) {
            recognition.lang = currentLanguage === 'ta' ? 'ta-IN' : 'en-US';
        }
        
        // Update placeholders
        updatePlaceholders();
        
        // Update initial greeting message
        const greetingEl = document.getElementById('initialGreeting');
        if (greetingEl) {
            greetingEl.textContent = currentLanguage === 'ta'
                ? 'வணக்கம்! உங்கள் உடல்நிலை கவலைகளுக்கு உதவ நான் இங்கு இருக்கிறேன். முதலில் உங்கள் மருத்துவ அறிக்கைகள் அல்லது ஸ்கேன்களை பதிவேற்றவும், பின்னர் உங்கள் நிலை பற்றி சில கேள்விகள் கேட்கிறேன்.'
                : 'Hello! I\'m here to help you with your health concerns. Please upload your medical reports or scans first, and then I\'ll ask you some questions about your condition.';
        }
    });
});

langBtn.addEventListener('click', function(e) {
    e.stopPropagation();
    langDropdown.classList.toggle('show');
});

document.addEventListener('click', function(e) {
    if (!langBtn.contains(e.target) && !langDropdown.contains(e.target)) {
        langDropdown.classList.remove('show');
    }
});

function updatePlaceholders() {
    if (currentLanguage === 'ta') {
        chatInput.placeholder = 'உங்கள் செய்தியை இங்கே தட்டச்சு செய்யவும்...';
    } else {
        chatInput.placeholder = 'Type your message here...';
    }
}

// Voice input functions
function toggleVoiceInput() {
    if (!recognition) {
        alert('Voice input is not supported in your browser. Please use Chrome or Edge.');
        return;
    }
    
    if (isListening) {
        recognition.stop();
        isListening = false;
        updateVoiceButton();
        hideVoiceStatus();
    } else {
        try {
            recognition.start();
            isListening = true;
            updateVoiceButton();
            showVoiceStatus('Listening... Speak now.', 'listening');
        } catch (error) {
            console.error('Error starting recognition:', error);
            showVoiceStatus('Error starting voice input', 'error');
        }
    }
}

function updateVoiceButton() {
    if (isListening) {
        voiceButton.classList.add('recording');
        voiceButton.title = 'Click to stop recording';
    } else {
        voiceButton.classList.remove('recording');
        voiceButton.title = 'Click to start voice input';
    }
}

function showVoiceStatus(message, type) {
    voiceStatus.textContent = message;
    voiceStatus.className = `voice-status show ${type}`;
    setTimeout(() => {
        hideVoiceStatus();
    }, 3000);
}

function hideVoiceStatus() {
    voiceStatus.classList.remove('show');
}

// Enable voice button when chat is enabled
function enableChat() {
    chatInput.disabled = false;
    sendButton.disabled = false;
    voiceButton.disabled = false;
    updatePlaceholders();
}

function updateCombinedIntro() {
    const el = document.getElementById('initialGreeting');
    if (!el) return;
    if (currentLanguage === 'ta') {
        el.textContent = 'வணக்கம்! தொடங்க உங்களின் அறிக்கை அல்லது ஸ்கேனை பதிவேற்றுங்கள்.';
    } else {
        el.textContent = 'Hi! Please upload your report or scan to get started.';
    }
}

async function requestNextQuestion() {
    if (isSending) return;
    isSending = true;
    chatInput.disabled = true;
    sendButton.disabled = true;
    voiceButton.disabled = true;

    const typingId = addTypingIndicator();
    try {
        const response = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ patient_id: patientId, message: '', language: currentLanguage })
        });
        removeTypingIndicator(typingId);
        const data = await response.json();
        if (response.ok) {
            if (data && data.message) addMessage('bot', data.message);
            const level = (data.risk_level || '').toLowerCase();
            if (level && level !== 'unknown') {
                const msgText = (data.message || '').toLowerCase();
                const containsRiskBanner = msgText.includes('high risk detected') || msgText.includes('moderate risk') || msgText.includes('low risk');
                if (!containsRiskBanner && level !== lastRiskLevelShown) {
                    const messagesDiv = document.getElementById('chatMessages');
                    const lastChild = messagesDiv.lastElementChild;
                    const isSameRiskLast = lastChild && lastChild.querySelector && lastChild.querySelector(`.message-content.risk-block.${level === 'medium' ? 'moderate' : level}`);
                    if (!isSameRiskLast) {
                        addRiskBlock(level, data.risk_score);
                        lastRiskLevelShown = level;
                    }
                }
                if (level === 'high') showHighRiskWarning();
                if (data.message && data.message.indexOf('?') === -1) {
                    addFinalReportButtonOnce();
                }
            } else {
                lastRiskLevelShown = null;
            }
        } else {
            addMessage('bot', `Error: ${data.error}`);
        }
    } catch (err) {
        removeTypingIndicator(typingId);
        addMessage('bot', `Error connecting to server: ${err.message}`);
    } finally {
        isSending = false;
        chatInput.disabled = false;
        sendButton.disabled = false;
        voiceButton.disabled = false;
    }
}
