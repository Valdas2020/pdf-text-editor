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
const imagePreview = document.getElementById('image-preview');
const previewImg = document.getElementById('preview-img');
const downloadBtn = document.getElementById('download-btn');

// State
let selectedFile = null;
let currentMode = 'ai'; // 'ai' | 'manual'
let resultBlob = null;
let resultFilename = '';

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

    // Update submit state when inputs change
    row.querySelectorAll('input').forEach(input => {
        input.addEventListener('input', updateSubmitState);
    });
}

function updateRemoveButtons() {
    const rows = replacementRows.querySelectorAll('.replacement-row');
    rows.forEach((row, i) => {
        const btn = row.querySelector('.remove-row');
        if (rows.length <= 1) {
            btn.classList.add('hidden');
        } else {
            btn.classList.remove('hidden');
        }
    });
}

// Attach events to initial row
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
            const find = row.querySelector('.find-input').value.trim();
            if (find) {
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

    // Reset UI
    hideError();
    hideResult();
    showProgress();
    submitBtn.disabled = true;
    submitBtn.classList.add('loading');

    const format = document.querySelector('input[name="format"]:checked').value;

    try {
        let response;

        if (currentMode === 'ai') {
            response = await submitAI(format);
        } else {
            response = await submitManual(format);
        }

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

        // Get result
        const blob = await response.blob();
        const totalReplacements = response.headers.get('X-Total-Replacements') || '0';
        const totalPages = response.headers.get('X-Total-Pages');

        resultBlob = blob;

        // Determine filename
        const baseName = selectedFile.name.replace('.pdf', '');
        if (format === 'pdf') {
            resultFilename = `edited_${baseName}.pdf`;
        } else {
            resultFilename = `edited_${baseName}_page1.${format}`;
        }

        // Show result
        let statsHtml = `<p>Total replacements made: <strong class="text-white">${totalReplacements}</strong></p>`;
        if (totalPages) {
            statsHtml += `<p>Total pages: ${totalPages}</p>`;
        }

        // Try to get metadata
        const metadata = response.headers.get('X-Metadata');
        if (metadata) {
            try {
                const meta = JSON.parse(metadata);
                if (meta.replacements && meta.replacements.length > 0) {
                    statsHtml += '<ul class="mt-2 space-y-1">';
                    for (const r of meta.replacements) {
                        statsHtml += `<li>"${escapeHtml(r.original)}" &rarr; "${escapeHtml(r.replacement)}" (${r.count} times)</li>`;
                    }
                    statsHtml += '</ul>';
                }
                if (meta.parsed_instructions && meta.parsed_instructions.notes) {
                    statsHtml += `<p class="mt-2 text-yellow-400 text-xs">Note: ${escapeHtml(meta.parsed_instructions.notes)}</p>`;
                }
            } catch {}
        }

        const replacementsHeader = response.headers.get('X-Replacements');
        if (replacementsHeader && !metadata) {
            try {
                const repls = JSON.parse(replacementsHeader);
                if (repls.length > 0) {
                    statsHtml += '<ul class="mt-2 space-y-1">';
                    for (const r of repls) {
                        statsHtml += `<li>"${escapeHtml(r.original)}" &rarr; "${escapeHtml(r.replacement)}" (${r.count} times)</li>`;
                    }
                    statsHtml += '</ul>';
                }
            } catch {}
        }

        resultStats.innerHTML = statsHtml;

        // Image preview
        if (format !== 'pdf' && blob.type.startsWith('image/')) {
            const url = URL.createObjectURL(blob);
            previewImg.src = url;
            imagePreview.classList.remove('hidden');
        } else {
            imagePreview.classList.add('hidden');
        }

        showResult();

    } catch (err) {
        showError(err.message);
    } finally {
        hideProgress();
        submitBtn.classList.remove('loading');
        updateSubmitState();
    }
}

async function submitAI(format) {
    const formData = new FormData();
    formData.append('file', selectedFile);
    formData.append('prompt', promptTextarea.value.trim());
    formData.append('output_format', format);

    // Animate progress
    animateProgress();

    return fetch(`${API_BASE}/api/edit`, {
        method: 'POST',
        body: formData,
    });
}

async function submitManual(format) {
    const replacements = {};
    const rows = replacementRows.querySelectorAll('.replacement-row');
    for (const row of rows) {
        const find = row.querySelector('.find-input').value.trim();
        const replace = row.querySelector('.replace-input').value;
        if (find) {
            replacements[find] = replace;
        }
    }

    const formData = new FormData();
    formData.append('file', selectedFile);
    formData.append('replacements', JSON.stringify(replacements));
    formData.append('case_sensitive', caseSensitiveCheckbox.checked);
    formData.append('output_format', format);

    animateProgress();

    return fetch(`${API_BASE}/api/edit-simple`, {
        method: 'POST',
        body: formData,
    });
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

// --- Download ---

downloadBtn.addEventListener('click', () => {
    if (!resultBlob) return;
    const url = URL.createObjectURL(resultBlob);
    const a = document.createElement('a');
    a.href = url;
    a.download = resultFilename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
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

function showResult() {
    resultSection.classList.remove('hidden');
}

function hideResult() {
    resultSection.classList.add('hidden');
    imagePreview.classList.add('hidden');
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}
