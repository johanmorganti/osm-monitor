// ── API / MCP page ──────────────────────────────────────────────────────────
// The page shows paths; the full URLs (to copy into a client) depend on the
// host it's served from, so they're filled in here from the page's origin.

document.querySelectorAll('[data-url-path]').forEach(el => {
    el.textContent = window.location.origin + el.dataset.urlPath;
});

document.querySelectorAll('.copy-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
        const text = document.getElementById(btn.dataset.copyFrom).textContent;
        try {
            await navigator.clipboard.writeText(text);
            btn.textContent = 'Copied';
        } catch {
            btn.textContent = 'Copy failed';
        }
        setTimeout(() => { btn.textContent = 'Copy'; }, 1500);
    });
});
