/* Presentation adapter for the production bundle. No API or business-rule changes. */
(() => {
    'use strict';
    const root = document.documentElement;
    const compact = window.matchMedia('(max-width: 1100px)');
    const views = [['tree', 'Estrutura'], ['map', 'Mapa'], ['operations', 'Operações'], ['details', 'Detalhes']];
    let view = 'map';
    let previewHidden = false;
    let sequenceList = false;
    let scheduled = false;
    let parentDocument;

    try { parentDocument = window.parent.document; } catch (_) { parentDocument = document; }
    function syncTheme() {
        const theme = parentDocument.documentElement.dataset.theme;
        root.dataset.theme = theme === 'escuro' ? 'escuro' : 'claro';
    }
    syncTheme();
    parentDocument.addEventListener('sync-theme-change', syncTheme);

    function control(text, className, action) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = `production-control ${className}`;
        button.textContent = text;
        button.addEventListener('click', action);
        return button;
    }

    function selectView(next) {
        view = next;
        root.dataset.productionView = view;
        document.querySelectorAll('[data-production-target]').forEach((button) => {
            button.setAttribute('aria-pressed', String(button.dataset.productionTarget === view));
        });
        // React Flow observes its container; a resize also updates its controls after revealing it.
        window.dispatchEvent(new Event('resize'));
    }

    const dialog = document.createElement('dialog');
    dialog.className = 'production-image-dialog';
    dialog.setAttribute('aria-labelledby', 'production-image-title');
    const dialogHeader = document.createElement('header');
    const dialogTitle = document.createElement('h2');
    dialogTitle.id = 'production-image-title';
    dialogTitle.textContent = 'Imagem da peça';
    dialogHeader.append(dialogTitle, control('Fechar', '', () => dialog.close()));
    const enlargedImage = document.createElement('img');
    dialog.append(dialogHeader, enlargedImage);
    document.body.append(dialog);
    dialog.addEventListener('click', (event) => { if (event.target === dialog) dialog.close(); });
    dialog.addEventListener('close', () => enlargedImage.removeAttribute('src'));

    function enhance() {
        scheduled = false;
        const header = document.querySelector('.app-header');
        if (header && !header.querySelector('.production-view-nav')) {
            const nav = document.createElement('nav');
            nav.className = 'production-view-nav';
            nav.setAttribute('aria-label', 'Visualizações da produção');
            views.forEach(([id, label]) => {
                const button = control(label, '', () => selectView(id));
                button.dataset.productionTarget = id;
                button.setAttribute('aria-pressed', String(id === view));
                nav.append(button);
            });
            header.append(nav);
        }
        const heading = document.querySelector('.sequence-heading');
        if (heading && !heading.querySelector('.production-sequence-toggle')) {
            const toggle = control(sequenceList ? 'Ver sequência' : 'Ver em lista', 'production-sequence-toggle', () => {
                sequenceList = !sequenceList;
                root.classList.toggle('production-sequence-list', sequenceList);
                toggle.textContent = sequenceList ? 'Ver sequência' : 'Ver em lista';
                toggle.setAttribute('aria-pressed', String(sequenceList));
            });
            toggle.setAttribute('aria-pressed', String(sequenceList));
            heading.append(toggle);
        }
        const preview = document.querySelector('.detail-preview');
        if (preview && !preview.previousElementSibling?.classList.contains('production-preview-tools')) {
            const tools = document.createElement('div');
            tools.className = 'production-preview-tools';
            const toggle = control(previewHidden ? 'Mostrar imagem' : 'Recolher imagem', '', () => {
                previewHidden = !previewHidden;
                root.classList.toggle('production-preview-hidden', previewHidden);
                toggle.textContent = previewHidden ? 'Mostrar imagem' : 'Recolher imagem';
                toggle.setAttribute('aria-expanded', String(!previewHidden));
            });
            toggle.setAttribute('aria-expanded', String(!previewHidden));
            const enlarge = control('Ampliar imagem', 'production-enlarge', () => {
                const image = document.querySelector('.detail-preview img');
                if (!image) return;
                enlargedImage.src = image.currentSrc || image.src;
                enlargedImage.alt = image.alt;
                dialog.showModal();
            });
            tools.append(toggle, enlarge);
            preview.before(tools);
        }
        const enlarge = document.querySelector('.production-enlarge');
        if (enlarge) enlarge.disabled = !preview?.querySelector('img');
        // Reflect the selected order when it is supplied through navigation or a deep link.
        const order = new URLSearchParams(window.location.search).get('os');
        const treeTitle = document.querySelector('.tree-panel .panel-title h2');
        const title = order ? `Estrutura da OS · ${order}` : 'Estrutura da OS';
        if (treeTitle && treeTitle.textContent !== title) treeTitle.textContent = title;
    }

    function schedule() {
        if (!scheduled) { scheduled = true; requestAnimationFrame(enhance); }
    }
    // Only child/text changes: graph positions and preview pointer movement do not trigger work.
    const observer = new MutationObserver(schedule);
    observer.observe(document.getElementById('root'), { childList: true, characterData: true, subtree: true });
    root.dataset.productionView = view;
    enhance();
    window.addEventListener('popstate', schedule);
    compact.addEventListener('change', () => selectView(view));
    window.addEventListener('pagehide', (event) => {
        if (event.persisted) return;
        observer.disconnect();
        parentDocument.removeEventListener('sync-theme-change', syncTheme);
    });
})();
