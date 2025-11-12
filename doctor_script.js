let patients = [];
let highRiskAlertShown = false;

// Load patients on page load
window.addEventListener('DOMContentLoaded', () => {
    refreshPatients();
    setInterval(refreshPatients, 5000);
    // Refresh alerts periodically
    refreshDoctorAlerts();
    setInterval(refreshDoctorAlerts, 5000);
});

async function refreshPatients() {
    try {
        const response = await fetch('/api/patients');
        const data = await response.json();
        
        if (Array.isArray(data)) {
            patients = data;
            displayPatients();
            updateRiskAlerts();
            maybeShowHighRiskPopup();
        }
    } catch (error) {
        console.error('Error fetching patients:', error);
    }
}

async function downloadDoctorReport(patientId) {
    try {
        const response = await fetch(`/api/download-report/${patientId}`);
        if (!response.ok) {
            const err = await response.json().catch(() => ({ error: 'Unknown error' }));
            alert('Error downloading report: ' + (err.error || response.statusText));
            return;
        }
        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `medical_report_${patientId}_${Date.now()}.pdf`;
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
        document.body.removeChild(a);
    } catch (e) {
        alert('Error downloading report: ' + e.message);
    }
}

async function renderRecoveryChart(patientId) {
    try {
        const res = await fetch(`/api/risk-history/${patientId}`);
        const { history } = await res.json();
        const labels = (history || []).map(h => new Date(h.date).toLocaleString());
        const data = (history || []).map(h => h.risk_score);
        const color = (data.length > 1 && data[data.length - 1] - data[0] > 0) ? '#ef4444' : '#10b981';
        const ctx = document.getElementById(`recoveryChart-${patientId}`).getContext('2d');
        // eslint-disable-next-line no-undef
        new Chart(ctx, {
            type: 'line',
            data: {
                labels,
                datasets: [{
                    label: 'Risk Score',
                    data,
                    borderColor: color,
                    backgroundColor: color + '33',
                    tension: 0.3,
                    fill: true,
                    pointRadius: 3
                }]
            },
            options: {
                responsive: true,
                plugins: { legend: { display: false } },
                scales: {
                    y: { suggestedMin: 0, suggestedMax: 100 }
                }
            }
        });
    } catch (e) {
        console.error('Failed to render chart', e);
    }
}

async function refreshDoctorAlerts() {
    try {
        const res = await fetch('/api/doctor-alerts');
        const data = await res.json();
        const panel = document.getElementById('doctorAlertsPanel');
        if (!panel) return;
        const alerts = data.alerts || [];
        if (alerts.length === 0) {
            panel.innerHTML = '<p class="no-alerts">No alerts yet.</p>';
            return;
        }
        // Group by risk level
        const groups = { high: [], moderate: [], low: [] };
        alerts.forEach(a => {
            const level = (a.risk_level || '').toLowerCase();
            if (level === 'high') groups.high.push(a);
            else if (level === 'moderate') groups.moderate.push(a);
            else if (level === 'low') groups.low.push(a);
        });
        // Clear panel and render grouped sections
        panel.innerHTML = '';
        const order = ['high', 'moderate', 'low'];
        order.forEach(level => {
            const list = groups[level];
            if (!list || list.length === 0) return;
            const color = level === 'high' ? '#ef4444' : (level === 'moderate' ? '#f59e0b' : '#10b981');
            const section = document.createElement('div');
            section.className = 'alerts-group';
            section.innerHTML = `
                <h3 class="alerts-heading" style="display:flex;align-items:center;gap:8px;margin:8px 0 12px 0;">
                    <span style="display:inline-block;width:6px;height:18px;background:${color};border-radius:3px;"></span>
                    <span style="font-weight:700;">${level.toUpperCase()} Alerts</span>
                </h3>`;
            panel.appendChild(section);
        });
    } catch (e) {
        console.error('Failed to refresh alerts', e);
    }
}


function displayPatients() {
    const highRiskDiv = document.getElementById('highRiskPatients');
    const lowRiskDiv = document.getElementById('lowRiskPatients');
    const moderateDiv = document.getElementById('moderatePatients');

    // Clear existing content
    highRiskDiv.innerHTML = '';
    lowRiskDiv.innerHTML = '';
    moderateDiv.innerHTML = '';

    // Separate patients by risk level
    const highRisk = patients.filter(p => p.risk_level === 'high');
    const lowRisk = patients.filter(p => p.risk_level === 'low');
    const moderate = patients.filter(p => p.risk_level === 'moderate');

    if (highRisk.length === 0) {
        highRiskDiv.innerHTML = '<p class="no-patients">No high-risk patients at the moment.</p>';
    } else {
        highRisk.forEach(patient => {
            highRiskDiv.appendChild(createPatientCard(patient));
        });
    }

    if (lowRisk.length === 0) {
        lowRiskDiv.innerHTML = '<p class="no-patients">No low-risk patients at the moment.</p>';
    } else {
        lowRisk.forEach(patient => {
            lowRiskDiv.appendChild(createPatientCard(patient));
        });
    }

    if (moderate.length === 0) {
        moderateDiv.innerHTML = '<p class="no-patients">No moderate patients at the moment.</p>';
    } else {
        moderate.forEach(patient => {
            moderateDiv.appendChild(createPatientCard(patient));
        });
    }
}

function createPatientCard(patient) {
    const card = document.createElement('div');
    card.className = `patient-card ${patient.risk_level}-risk`;
    card.onclick = () => showPatientDetails(patient.patient_id);

    const lastUpdated = new Date(patient.last_updated).toLocaleString();

    card.innerHTML = `
        <div class="patient-card-header">
            <div class="patient-id">${patient.name || patient.patient_id}</div>
            <span class="risk-badge ${patient.risk_level}">
                ${patient.risk_level.toUpperCase()} RISK
            </span>
        </div>
        <div class="patient-info">
            <div class="info-item">
                <strong>Patient ID:</strong> ${patient.patient_id}
            </div>
            <div class="info-item">
                <strong>Conversations:</strong> ${patient.conversation_count || 0}
            </div>
            <div class="info-item">
                <strong>Uploads:</strong> ${patient.upload_count || 0}
            </div>
            <div class="info-item">
                <strong>Last Updated:</strong> ${lastUpdated}
            </div>
            ${patient.details && patient.details.summary ? `
            <div class="info-item">
                <strong>Summary:</strong> ${patient.details.summary.substring(0, 100)}...
            </div>
            ` : ''}
        </div>
        <div class="patient-actions">
            <button class="call-button ${patient.risk_level === 'high' ? 'high-risk' : ''}" 
                    onclick="event.stopPropagation(); callPatient('${patient.patient_id}')">
                📞 Call Patient
            </button>
        </div>
    `;

    return card;
}

function showPatientDetails(patientId) {
    const patient = patients.find(p => p.patient_id === patientId);
    if (!patient) return;

    const modal = document.getElementById('patientModal');
    const modalBody = document.getElementById('modalBody');

    let conversationHtml = '';
    if (patient.full_conversation && patient.full_conversation.length > 0) {
        conversationHtml = '<div class="modal-section"><h3>Conversation History</h3>';
        patient.full_conversation.forEach(msg => {
            const timestamp = new Date(msg.timestamp).toLocaleString();
            conversationHtml += `
                <div class="conversation-item ${msg.role}">
                    <strong>${msg.role === 'user' ? 'Patient' : 'Assistant'} (${timestamp}):</strong>
                    ${escapeHtml(msg.content).replace(/\n/g, '<br>')}
                </div>
            `;
        });
        conversationHtml += '</div>';
    }

    let uploadsHtml = '';
    if (patient.uploads && patient.uploads.length > 0) {
        uploadsHtml = '<div class="modal-section"><h3>Uploaded Files</h3>';
        patient.uploads.forEach(upload => {
            uploadsHtml += `
                <div class="upload-item">
                    <h4>${upload.filename}</h4>
                    <p><strong>Uploaded:</strong> ${new Date(upload.timestamp).toLocaleString()}</p>
                    <p><strong>Analysis:</strong> ${escapeHtml(upload.analysis || 'N/A')}</p>
                </div>
            `;
        });
        uploadsHtml += '</div>';
    }

    modalBody.innerHTML = `
        <div class="modal-header">
            <h2>Patient Details - ${patient.patient_id}</h2>
        </div>
        <div class="modal-section">
            <h3>Patient Information</h3>
            <div class="info-item"><strong>Patient ID:</strong> ${patient.patient_id}</div>
            <div class="info-item"><strong>Risk Level:</strong> 
                <span class="risk-badge ${patient.risk_level}">${patient.risk_level.toUpperCase()}</span>
            </div>
            <div class="info-item"><strong>Conversation Count:</strong> ${patient.conversation_count || 0}</div>
            <div class="info-item"><strong>Upload Count:</strong> ${patient.upload_count || 0}</div>
            <div class="info-item"><strong>Last Updated:</strong> ${new Date(patient.last_updated).toLocaleString()}</div>
            ${patient.details && patient.details.summary ? `
            <div class="info-item"><strong>Summary:</strong> ${patient.details.summary}</div>
            ` : ''}
        </div>
        ${uploadsHtml}
        ${conversationHtml}
        <div class="modal-section">
            <h3>Recovery Progress</h3>
            <canvas id="recoveryChart-${patient.patient_id}" height="140"></canvas>
        </div>
        <div class="patient-actions" style="margin-top: 20px; display:flex; gap:10px;">
            <button class="call-button ${patient.risk_level === 'high' ? 'high-risk' : ''}" 
                    onclick="callPatient('${patient.patient_id}')" style="flex:1;">
                📞 Call Patient Now
            </button>
            <button class="call-button" 
                    onclick="downloadDoctorReport('${patient.patient_id}')" style="flex:1; background:#0ea5e9;">
                📥 Download Report
            </button>
        </div>
    `;

    modal.style.display = 'block';

    // Load and render risk history chart
    renderRecoveryChart(patient.patient_id);
}

function closeModal() {
    document.getElementById('patientModal').style.display = 'none';
}

function callPatient(patientId) {
    const patient = patients.find(p => p.patient_id === patientId);
    if (patient) {
        // In a real implementation, this would initiate a call
        alert(`Calling ${patient.patient_id}...\n\nIn production, this would initiate a video/voice call to the patient.`);
        
        // You can integrate with WebRTC or other calling APIs here
        console.log('Calling patient:', patientId);
    }
}

function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Close modal when clicking outside
window.onclick = function(event) {
    const modal = document.getElementById('patientModal');
    if (event.target == modal) {
        closeModal();
    }
}


function maybeShowHighRiskPopup() {
    if (typeof highRiskAlertShown === 'undefined') {
        window.highRiskAlertShown = false;
    }
    if (highRiskAlertShown) return;
    const highRisk = patients.filter(p => p.risk_level === 'high');
    if (highRisk.length === 0) return;

    highRiskAlertShown = true;

    const modal = document.createElement('div');
    modal.className = 'modal';
    modal.id = 'highRiskAlertModal';

    const content = document.createElement('div');
    content.className = 'modal-content';

    const closeBtn = document.createElement('span');
    closeBtn.className = 'close';
    closeBtn.innerHTML = '&times;';
    closeBtn.onclick = () => document.body.removeChild(modal);

    const header = document.createElement('div');
    header.className = 'modal-header';
    header.innerHTML = '<h2>High-Risk Patients Alert</h2>';

    const body = document.createElement('div');
    body.className = 'modal-section';
    body.innerHTML = `<p style="margin-bottom:10px;">⚠️ ${highRisk.length} high-risk ${highRisk.length===1?'patient requires':'patients require'} immediate attention.</p>`;

    const list = document.createElement('div');
    highRisk.forEach(p => {
        const item = document.createElement('div');
        item.className = 'conversation-item';
        item.style.display = 'flex';
        item.style.justifyContent = 'space-between';
        item.style.alignItems = 'center';
        item.innerHTML = `
            <div>
                <strong>${p.name || p.patient_id}</strong><br>
                <span style="font-size:0.9em;color:#555;">Updated: ${new Date(p.last_updated).toLocaleString()}</span>
            </div>
            <button class="call-button high-risk" style="width:auto; min-width:140px;" onclick="document.body.removeChild(document.getElementById('highRiskAlertModal')); callPatient('${p.patient_id}')">📞 Call Now</button>
        `;
        list.appendChild(item);
    });

    const actions = document.createElement('div');
    actions.style.marginTop = '16px';
    const dismiss = document.createElement('button');
    dismiss.className = 'call-button';
    dismiss.style.background = '#6c757d';
    dismiss.textContent = 'Dismiss';
    dismiss.onclick = () => document.body.removeChild(modal);
    actions.appendChild(dismiss);

    body.appendChild(list);
    body.appendChild(actions);

    content.appendChild(closeBtn);
    content.appendChild(header);
    content.appendChild(body);
    modal.appendChild(content);
    document.body.appendChild(modal);

    modal.style.display = 'block';
    modal.onclick = function(event) {
        if (event.target === modal) {
            document.body.removeChild(modal);
        }
    };
}
