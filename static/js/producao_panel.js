/* Presentation adapter for the production bundle. No API or business-rule changes. */
(() => {
    'use strict';
    const root = document.documentElement;
    const compact = window.matchMedia('(max-width: 1100px)');
    const views = [['tree', 'Estrutura'], ['map', 'Mapa'], ['operations', 'Operações'], ['details', 'Detalhes']];
    let view = 'map';
    let scheduled = false;
    let parentDocument;

    try { parentDocument = window.parent.document; } catch (_) { parentDocument = document; }
    function syncTheme() {
        const theme = parentDocument.documentElement.dataset.theme;
        root.dataset.theme = theme === 'escuro' ? 'escuro' : 'claro';
    }
    syncTheme();
    parentDocument.addEventListener('sync-theme-change', syncTheme);

    // The floating mobile menu shares the first row now that the page title is removed.
    const parentWindow = parentDocument.defaultView;
    function syncMenuSpace() {
        const menu = parentDocument.getElementById('mobile-menu-toggle');
        root.classList.toggle('production-mobile-menu', parentDocument !== document &&
            !!menu && parentWindow.getComputedStyle(menu).display !== 'none');
    }
    syncMenuSpace();
    parentWindow.addEventListener('resize', syncMenuSpace);

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

    const previewLoads = new WeakMap();
    const activePreviews = new Set();
    const previewVisibility = new IntersectionObserver((entries) => {
        entries.forEach(({ target: image, isIntersecting }) => {
            const load = previewLoads.get(image);
            if (!load) return;
            load.visible = isIntersecting;
            if (!isIntersecting) {
                clearTimeout(load.retry);
                load.retry = null;
                clearTimeout(load.deadline);
                load.deadline = null;
            } else if (image.complete && !load.retry) {
                handlePreviewDimensions(image, load.container);
            }
        });
    }, { rootMargin: '120px' });

    function stopPreview(image) {
        const load = previewLoads.get(image);
        if (load) {
            clearTimeout(load.retry);
            clearTimeout(load.deadline);
        }
        activePreviews.delete(image);
        previewVisibility.unobserve(image);
    }

    function previewSource(image, detail) {
        const source = new URL(image.getAttribute('src'), window.location.href);
        source.searchParams.delete('_preview');
        source.searchParams.set('renderer', 'isometric-cutout-v7');
        if (detail) source.searchParams.set('variant', 'detail');
        return `${source.pathname}${source.search}`;
    }

    function preparePreview(container, detail = false) {
        const image = container.querySelector('img');
        if (!image) {
            container.dataset.previewState = 'unavailable';
            return;
        }
        const source = previewSource(image, detail);
        if (image.dataset.productionSource === source) return;
        stopPreview(image);
        const load = { source, container, detail, visible: false, retry: null, deadline: null, expired: false };
        previewLoads.set(image, load);
        activePreviews.add(image);
        previewVisibility.observe(image);
        image.decoding = 'async';
        image.fetchPriority = detail ? 'high' : 'low';
        image.dataset.productionSource = source;
        image.dataset.previewAttempts = '0';
        container.dataset.previewState = 'loading';
        if (image.getAttribute('src') !== source) image.setAttribute('src', source);
        if (image.complete) handlePreviewDimensions(image, container);
    }

    function handlePreviewDimensions(image, container) {
        const load = previewLoads.get(image);
        if (!load || load.expired) return;
        clearTimeout(load.retry);
        load.retry = null;
        if (image.naturalWidth === 1 && image.naturalHeight === 1) {
            container.dataset.previewState = 'loading';
            if (!load.visible || document.hidden) return;
            if (!load.deadline) {
                load.deadline = window.setTimeout(() => {
                    if (previewLoads.get(image) !== load || !image.isConnected) return;
                    load.expired = true;
                    stopPreview(image);
                    container.dataset.previewState = 'unavailable';
                }, 120000);
            }
            const attempt = Number(image.dataset.previewAttempts || 0) + 1;
            image.dataset.previewAttempts = String(attempt);
            load.retry = window.setTimeout(() => {
                load.retry = null;
                if (!image.isConnected || !load.visible || document.hidden || previewLoads.get(image) !== load) return;
                const detail = container.classList.contains('detail-preview');
                if (previewSource(image, detail) !== image.dataset.productionSource) return;
                const retry = new URL(image.dataset.productionSource, window.location.href);
                retry.searchParams.set('_preview', String(Date.now()));
                image.setAttribute('src', `${retry.pathname}${retry.search}`);
            }, load.detail ? Math.min(100 * (1.25 ** attempt), 500) : Math.min(200 * (1.35 ** attempt), 1000));
            return;
        }
        stopPreview(image);
        if (image.naturalWidth > 1) image.style.removeProperty('display');
        container.dataset.previewState = image.naturalWidth > 0 ? 'ready' : 'unavailable';
    }

    function onPreviewResult(event) {
        const image = event.target;
        if (!(image instanceof HTMLImageElement)) return;
        const container = image.closest('.detail-preview, .node-thumbnail');
        if (!container) return;
        const detail = container.classList.contains('detail-preview');
        if (previewSource(image, detail) !== image.dataset.productionSource) return;
        if (event.type === 'load') handlePreviewDimensions(image, container);
        else {
            stopPreview(image);
            container.dataset.previewState = 'unavailable';
        }
    }

    document.addEventListener('load', onPreviewResult, true);
    document.addEventListener('error', onPreviewResult, true);

    function resumePreviews() {
        activePreviews.forEach((image) => {
            const load = previewLoads.get(image);
            clearTimeout(load.retry);
            load.retry = null;
            if (document.hidden) {
                clearTimeout(load.deadline);
                load.deadline = null;
            }
            if (!document.hidden && image.complete) handlePreviewDimensions(image, load.container);
        });
    }
    document.addEventListener('visibilitychange', resumePreviews);

    function enhance() {
        scheduled = false;
        activePreviews.forEach((image) => {
            if (!image.isConnected) stopPreview(image);
        });
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
        document.querySelectorAll('.details-panel button').forEach((button) => {
            if (/^Ampliar(?: imagem)?$/i.test(button.textContent.trim())) button.remove();
        });
        document.querySelectorAll('.tree-row').forEach((row) => {
            const indent = Number.parseFloat(row.style.paddingLeft || '10');
            const depth = Math.max(0, Math.round((indent - 10) / 18));
            row.dataset.productionDepth = String(depth);
            row.style.setProperty('--production-indent', `${indent}px`);
        });
        const preview = document.querySelector('.detail-preview');
        if (preview) preparePreview(preview, true);
        document.querySelectorAll('.node-thumbnail').forEach((thumbnail) => preparePreview(thumbnail));
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
    const observer = new MutationObserver((records) => {
        records.forEach((record) => {
            if (record.type !== 'attributes') {
                schedule();
                return;
            }
            const image = record.target;
            const container = image.closest('.detail-preview, .node-thumbnail');
            if (container) preparePreview(container, container.classList.contains('detail-preview'));
        });
    });
    observer.observe(document.getElementById('root'), { childList: true, characterData: true, attributes: true, attributeFilter: ['src'], subtree: true });
    root.dataset.productionView = view;
    enhance();
    window.addEventListener('popstate', schedule);
    compact.addEventListener('change', () => selectView(view));
    window.addEventListener('pagehide', (event) => {
        if (event.persisted) return;
        observer.disconnect();
        activePreviews.forEach(stopPreview);
        previewVisibility.disconnect();
        document.removeEventListener('load', onPreviewResult, true);
        document.removeEventListener('error', onPreviewResult, true);
        document.removeEventListener('visibilitychange', resumePreviews);
        parentDocument.removeEventListener('sync-theme-change', syncTheme);
        parentWindow.removeEventListener('resize', syncMenuSpace);
    });
})();
