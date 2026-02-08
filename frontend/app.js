/**
 * PDF Text Editor — Frontend Application
 */

// Relative URLs — works on any host
const API_BASE = '';

// DOM elements
const dropZone = document.getElementById('drop-zone');
const fileInput = document.getElementById('file-input');
const dropText = document.getElementById('drop-text');
const fileInfo = document.getElementById('file-info');
const fileName = document.getElementById('file-name');
const fileSize = document.getElementById('file-size');
const removeFileBtn = document.getElementById('remove-file');

const modeAiBtn = document.getElementById('mode-ai');
const modeManualBtn = document.getElementById('mode-manual');
const aiInput = document.getElementById('ai-input');
const manualInput = document.getElementById('manual-input');

const promptTextarea = document.getElementById('prompt');
const replacementRows = document.getElementById('replacement-rows');
const addRowBtn = document.getElementById('add-row');
const caseSensitiveCheckbox = document.getElementById('case-sensitive');

const submitBtn = document.getElementById('submit-btn');
const progressSection = document.getElementById('progress-section');
const progressBar = document.getElementById('progress-bar');
const progressText = document.getElementById('progress-text');

const errorSection = document.getElementById('error-section');
const errorText = document.getElementById('error-text');

const resultSection = document.getElementById('result-section');
const resultStats = document.getElementById('result-stats');
const previewContainer = document.getElementById('preview-container');
const payBtn = document.getElementById('pay-btn');

// Payment modal elements
const paymentModal = document.getElementById('payment-modal');
const modalOverlay = document.getElementById('modal-overlay');
const modalCancel = document.getElementById('modal-cancel');
const payTelegramBtn = document.getElementById('pay-telegram-btn');
const payOnchainBtn = document.getElementById('pay-onchain-btn');
const postPaySection = document.getElementById('post-pay-section');
const downloadPageLink = document.getElementById('download-page-link');
const telegramRedirectHint = document.getElementById('telegram-redirect-hint');

// State
let selectedFile = null;
let currentMode = 'ai';
let currentResultFileId = null;

// --- File handling ---

dropZone.addEventListener('click', () => fileInput.click());

dropZone.addEventListener('dragover', (e) => {
    e.preventDefault();
    dropZone.classList.add('drag-over');
});

dropZone.addEventListener('dragleave', () => {
    dropZone.classList.remove('drag-over');
});

dropZone.addEventListener('drop', (e) => {
    e.preventDefault();
    dropZone.classList.remove('drag-over');
    const files = e.dataTransfer.files;
    if (files.length > 0 && files[0].type === 'application/pdf') {
        setFile(files[0]);
    }
});

fileInput.addEventListener('change', () => {
    if (fileInput.files.length > 0) {
        setFile(fileInput.files[0]);
    }
});

removeFileBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    clearFile();
});

function setFile(file) {
    selectedFile = file;
    fileName.textContent = file.name;
    fileSize.textContent = formatSize(file.size);
    dropText.classList.add('hidden');
    fileInfo.classList.remove('hidden');
    updateSubmitState();
}

function clearFile() {
    selectedFile = null;
    fileInput.value = '';
    dropText.classList.remove('hidden');
    fileInfo.classList.add('hidden');
    updateSubmitState();
}

function formatSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

// --- Mode toggle ---

modeAiBtn.addEventListener('click', () => switchMode('ai'));
modeManualBtn.addEventListener('click', () => switchMode('manual'));

function switchMode(mode) {
    currentMode = mode;

    if (mode === 'ai') {
        modeAiBtn.classList.add('bg-accent', 'text-white');
        modeAiBtn.classList.remove('bg-gray-700', 'text-gray-300');
        modeManualBtn.classList.remove('bg-accent', 'text-white');
        modeManualBtn.classList.add('bg-gray-700', 'text-gray-300');
        aiInput.classList.remove('hidden');
        manualInput.classList.add('hidden');
    } else {
        modeManualBtn.classList.add('bg-accent', 'text-white');
        modeManualBtn.classList.remove('bg-gray-700', 'text-gray-300');
        modeAiBtn.classList.remove('bg-accent', 'text-white');
        modeAiBtn.classList.add('bg-gray-700', 'text-gray-300');
        manualInput.classList.remove('hidden');
        aiInput.classList.add('hidden');
    }
    updateSubmitState();
}

// --- Manual replacement rows ---

addRowBtn.addEventListener('click', () => {
    addReplacementRow();
    updateRemoveButtons();
});

function addReplacementRow() {
    const row = document.createElement('div');
    row.className = 'replacement-row flex gap-3 mb-3';
    row.innerHTML = `
        <input type="text" placeholder="Find text..."
               class="flex-1 bg-gray-800 border border-gray-700 rounded-lg px-4 py-2 text-gray-100 placeholder-gray-500 focus:outline-none focus:border-accent find-input">
        <span class="self-center text-gray-500">&rarr;</span>
        <input type="text" placeholder="Replace with..."
               class="flex-1 bg-gray-800 border border-gray-700 rounded-lg px-4 py-2 text-gray-100 placeholder-gray-500 focus:outline-none focus:border-accent replace-input">
        <button class="remove-row text-gray-500 hover:text-red-400 px-2">&times;</button>
    `;
    replacementRows.appendChild(row);
    row.querySelector('.remove-row').addEventListener('click', () => {
        row.remove();
        updateRemoveButtons();
        updateSubmitState();
    });
    row.querySelectorAll('input').forEach(input => {
        input.addEventListener('input', updateSubmitState);
    });
}

function updateRemoveButtons() {
    const rows = replacementRows.querySelectorAll('.replacement-row');
    rows.forEach(row => {
        const btn = row.querySelector('.remove-row');
        if (rows.length <= 1) {
            btn.classList.add('hidden');
        } else {
            btn.classList.remove('hidden');
        }
    });
}

document.querySelectorAll('.replacement-row input').forEach(input => {
    input.addEventListener('input', updateSubmitState);
});
promptTextarea.addEventListener('input', updateSubmitState);

// --- Submit state ---

function updateSubmitState() {
    let hasInput = false;
    if (currentMode === 'ai') {
        hasInput = promptTextarea.value.trim().length > 0;
    } else {
        const rows = replacementRows.querySelectorAll('.replacement-row');
        for (const row of rows) {
            if (row.querySelector('.find-input').value.trim()) {
                hasInput = true;
                break;
            }
        }
    }
    submitBtn.disabled = !selectedFile || !hasInput;
}

// --- Submit ---

submitBtn.addEventListener('click', handleSubmit);

async function handleSubmit() {
    if (!selectedFile) return;

    hideError();
    hideResult();
    showProgress();
    submitBtn.disabled = true;
    submitBtn.classList.add('loading');

    const format = document.querySelector('input[name="format"]:checked').value;

    try {
        const formData = new FormData();
        formData.append('file', selectedFile);
        formData.append('output_format', format);

        if (currentMode === 'ai') {
            formData.append('prompt', promptTextarea.value.trim());
        } else {
            const replacements = {};
            const rows = replacementRows.querySelectorAll('.replacement-row');
            for (const row of rows) {
                const find = row.querySelector('.find-input').value.trim();
                const replace = row.querySelector('.replace-input').value;
                if (find) replacements[find] = replace;
            }
            formData.append('replacements', JSON.stringify(replacements));
            formData.append('case_sensitive', caseSensitiveCheckbox.checked);
        }

        animateProgress();

        const endpoint = currentMode === 'ai' ? '/api/edit' : '/api/edit-simple';
        const response = await fetch(`${API_BASE}${endpoint}`, {
            method: 'POST',
            body: formData,
        });

        if (!response.ok) {
            let detail = 'Processing failed';
            try {
                const err = await response.json();
                detail = err.detail || detail;
            } catch {
                detail = await response.text() || detail;
            }
            throw new Error(detail);
        }

        const data = await response.json();
        showResultFromData(data);

    } catch (err) {
        showError(err.message);
    } finally {
        hideProgress();
        submitBtn.classList.remove('loading');
        updateSubmitState();
    }
}

function showResultFromData(data) {
    currentResultFileId = data.result_file_id;

    // Stats
    let statsHtml = `<p>Total replacements made: <strong class="text-white">${data.total_replacements}</strong></p>`;
    statsHtml += `<p>Pages: ${data.total_pages} &bull; Format: ${data.output_format.toUpperCase()}</p>`;

    if (data.replacements_report && data.replacements_report.length > 0) {
        statsHtml += '<ul class="mt-2 space-y-1">';
        for (const r of data.replacements_report) {
            statsHtml += `<li>"${escapeHtml(r.original)}" &rarr; "${escapeHtml(r.replacement)}" (${r.count} times)</li>`;
        }
        statsHtml += '</ul>';
    }

    if (data.parsed_instructions && data.parsed_instructions.notes) {
        statsHtml += `<p class="mt-2 text-yellow-400 text-xs">Note: ${escapeHtml(data.parsed_instructions.notes)}</p>`;
    }

    resultStats.innerHTML = statsHtml;

    // Preview images (with watermark)
    previewContainer.innerHTML = '';
    if (data.preview_images && data.preview_images.length > 0) {
        for (let i = 0; i < data.preview_images.length; i++) {
            const img = document.createElement('img');
            img.src = 'data:image/jpeg;base64,' + data.preview_images[i];
            img.alt = `Page ${i + 1}`;
            img.className = 'max-w-full rounded-lg border border-gray-700';
            if (data.preview_images.length > 1) {
                const label = document.createElement('p');
                label.className = 'text-xs text-gray-500 mb-1';
                label.textContent = `Page ${i + 1} of ${data.preview_images.length}`;
                previewContainer.appendChild(label);
            }
            previewContainer.appendChild(img);
        }
    }

    // Update price on button
    payBtn.innerHTML = `&#x1F4B3; Pay &amp; Download Result — $${data.price_usd}`;

    resultSection.classList.remove('hidden');
}

// --- Progress animation ---

let progressInterval = null;

function animateProgress() {
    let pct = 0;
    progressBar.style.width = '0%';
    progressInterval = setInterval(() => {
        pct += Math.random() * 15;
        if (pct > 90) pct = 90;
        progressBar.style.width = pct + '%';
    }, 500);
}

function stopProgressAnimation() {
    if (progressInterval) {
        clearInterval(progressInterval);
        progressInterval = null;
    }
    progressBar.style.width = '100%';
}

// --- Payment Modal ---

payBtn.addEventListener('click', () => openPaymentModal());
modalOverlay.addEventListener('click', () => closePaymentModal());
modalCancel.addEventListener('click', () => closePaymentModal());

function openPaymentModal() {
    postPaySection.classList.add('hidden');
    telegramRedirectHint.classList.remove('hidden');
    paymentModal.classList.remove('hidden');
    document.body.style.overflow = 'hidden';
}

function closePaymentModal() {
    paymentModal.classList.add('hidden');
    document.body.style.overflow = '';
}

// Telegram CryptoBot payment
payTelegramBtn.addEventListener('click', async () => {
    if (!currentResultFileId) return;

    payTelegramBtn.disabled = true;
    payTelegramBtn.style.opacity = '0.7';

    try {
        const resp = await fetch(`${API_BASE}/api/create-invoice/${currentResultFileId}`, {
            method: 'POST',
        });

        if (!resp.ok) {
            let detail = 'Failed to create invoice';
            try {
                const err = await resp.json();
                detail = err.detail || detail;
            } catch {}
            throw new Error(detail);
        }

        const invoice = await resp.json();
        window.open(invoice.pay_url, '_blank');

        // Show post-pay section with download page link
        telegramRedirectHint.classList.add('hidden');
        downloadPageLink.href = `/download-page/${currentResultFileId}`;
        postPaySection.classList.remove('hidden');

    } catch (e) {
        alert('Error: ' + e.message);
    } finally {
        payTelegramBtn.disabled = false;
        payTelegramBtn.style.opacity = '';
    }
});

// On-chain USDT/USDC payment
payOnchainBtn.addEventListener('click', () => {
    if (!currentResultFileId) return;
    window.open(`/onchain-pay/${currentResultFileId}`, '_blank');
    closePaymentModal();
});

// Close modal on Escape key
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !paymentModal.classList.contains('hidden')) {
        closePaymentModal();
    }
});

// --- UI helpers ---

function showProgress() {
    progressSection.classList.remove('hidden');
    progressText.textContent = 'Processing your PDF...';
}

function hideProgress() {
    stopProgressAnimation();
    progressSection.classList.add('hidden');
}

function showError(msg) {
    errorText.textContent = msg;
    errorSection.classList.remove('hidden');
}

function hideError() {
    errorSection.classList.add('hidden');
}

function hideResult() {
    resultSection.classList.add('hidden');
    previewContainer.innerHTML = '';
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}
