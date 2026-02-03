/** @odoo-module **/

// Lightweight enhancer: adds an OCR scan button next to each textarea used for codes_input in the wizard.
// - Opens camera (file input capture) on PDA.
// - Uses Tesseract.js (loaded from CDN) to OCR the image.
// - Extracts code pattern CS + 5 digits (accepts optional dash), number >= 54801.
// - If confidence < 70%, asks to re-take or cancel. If OK, appends to textarea.

(function () {
    const MIN_CONFIDENCE = 70;
    const CODE_REGEX = /CS[-\s]?([0-9]{5})/i;

    function ensureTesseractLoaded() {
        return new Promise((resolve, reject) => {
            if (window.Tesseract && window.Tesseract.recognize) {
                resolve(window.Tesseract);
                return;
            }
            const script = document.createElement('script');
            script.src = 'https://cdn.jsdelivr.net/npm/tesseract.js@4.0.2/dist/tesseract.min.js';
            script.async = true;
            script.onload = () => resolve(window.Tesseract);
            script.onerror = () => reject(new Error('No se pudo cargar Tesseract.js'));
            document.head.appendChild(script);
        });
    }

    function normalizeCode(text) {
        const m = text && text.match(CODE_REGEX);
        if (!m) return null;
        const num = m[1];
        const n = parseInt(num, 10);
        if (!Number.isFinite(n) || n < 54801) return null;
        return `CS${num}`.toUpperCase();
    }

    async function runOCR(file) {
        const Tesseract = await ensureTesseractLoaded();
        const result = await Tesseract.recognize(file, 'eng');
        return result;
    }

    function appendCodeToTextarea(textarea, code) {
        const current = textarea.value.trim();
        const lines = current ? current.split(/\r?\n/) : [];
        lines.push(code);
        textarea.value = lines.join('\n');
        // Trigger input event so Odoo detects change
        const evt = new Event('input', { bubbles: true });
        textarea.dispatchEvent(evt);
    }

    function createScanButton(textarea) {
        if (textarea.dataset.ctOcrAttached) return; // prevent duplicate buttons
        textarea.dataset.ctOcrAttached = '1';

        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'btn btn-secondary o_btn_ct_ocr';
        btn.textContent = 'Escanear (OCR)';
        btn.style.marginTop = '4px';

        const fileInput = document.createElement('input');
        fileInput.type = 'file';
        fileInput.accept = 'image/*';
        fileInput.capture = 'environment';
        fileInput.style.display = 'none';

        btn.addEventListener('click', () => fileInput.click());
        fileInput.addEventListener('change', async () => {
            try {
                const file = fileInput.files && fileInput.files[0];
                if (!file) return;
                // Provide minimal feedback
                btn.disabled = true; btn.textContent = 'Leyendo...';
                const result = await runOCR(file);
                const text = (result && result.data && result.data.text) || '';
                const conf = (result && result.data && typeof result.data.confidence === 'number') ? result.data.confidence : 0;
                const code = normalizeCode(text);
                btn.disabled = false; btn.textContent = 'Escanear (OCR)';

                if (!code) {
                    alert('No se detectó un código válido (formato CS##### con mínimo CS54801). Reintente o digite manualmente.');
                    return;
                }
                if (conf < MIN_CONFIDENCE) {
                    const retry = confirm(`Confianza OCR ${conf.toFixed(0)}%. ¿Reintentar toma o cancelar para digitar manualmente?\nAceptar = Reintentar, Cancelar = Usar este código igualmente.`);
                    if (retry) return; // user chose to retry; do nothing
                }
                appendCodeToTextarea(textarea, code);
            } catch (e) {
                console.error(e);
                alert('Error de OCR. Verifique permisos de cámara/imagen y reintente.');
            } finally {
                // reset file input so user can select the same image again if needed
                fileInput.value = '';
                btn.disabled = false; btn.textContent = 'Escanear (OCR)';
            }
        });

        // Insert after the textarea
        const container = document.createElement('div');
        container.appendChild(btn);
        container.appendChild(fileInput);
        textarea.parentNode && textarea.parentNode.appendChild(container);
    }

    function scanForTextareas(root) {
        const nodes = root.querySelectorAll('textarea[data-name="codes_input"], textarea[name="codes_input"]');
        nodes.forEach(createScanButton);
    }

    // Run on initial load
    document.addEventListener('DOMContentLoaded', () => {
        scanForTextareas(document);
        // Observe dynamic UI updates in Odoo SPA
        const mo = new MutationObserver((mutations) => {
            for (const m of mutations) {
                m.addedNodes && m.addedNodes.forEach((n) => {
                    if (n.nodeType === 1) {
                        scanForTextareas(n);
                    }
                });
            }
        });
        mo.observe(document.body, { childList: true, subtree: true });
    });
})();
