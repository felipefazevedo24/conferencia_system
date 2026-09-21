/* Cronograma no painel lateral do bundle de Produção. A OS é aberta pelo
   restaurador de navegação do próprio app, preservando mapa e detalhes. */
(() => {
    'use strict';
    const now = new Date();
    const state = {
        tab: 'estrutura', month: now.getMonth() + 1, year: now.getFullYear(),
        classification: '', search: '', deliveries: [], classes: [],
        openBudget: null, loading: false, loaded: false, classesLoaded: false,
        error: '', request: 0
    };
    const monthNames = ['Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho',
        'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro'];
    const element = (tag, className, text) => {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    };
    let searchTimer;
    let renderScheduled = false;

    function deliveryLabel(delivery) {
        const date = delivery.data_entrega?.slice(0, 10);
        if (!date) return 'Sem data';
        if (delivery.percentual === 100 || /conclu|finaliz/i.test(delivery.status || '')) return 'Concluído';
        const today = new Date();
        const todayUTC = Date.UTC(today.getFullYear(), today.getMonth(), today.getDate());
        const [year, month, day] = date.split('-').map(Number);
        const days = Math.round((Date.UTC(year, month - 1, day) - todayUTC) / 86400000);
        if (days < 0) return 'Atrasado';
        if (days === 0) return 'Hoje';
        if (days <= 7) return 'Próximo';
        return 'No prazo';
    }

    function formatDate(value) {
        const date = value?.slice(0, 10);
        if (!date) return '—';
        const [year, month, day] = date.split('-');
        return `${day}/${month}/${year}`;
    }

    function selectedOrder() {
        return new URLSearchParams(location.search).get('os') || '';
    }

    function selectOS(number) {
        if (!number || number === selectedOrder()) return;
        const url = new URL(location.href);
        url.searchParams.set('os', number);
        url.searchParams.delete('item');
        const previous = history.state && typeof history.state === 'object' ? history.state : {};
        const depth = Number(previous.assemblyNavigationDepth || 0) + 1;
        history.pushState({ ...previous, assemblyNavigationDepth: depth }, '', url);
        window.dispatchEvent(new PopStateEvent('popstate', { state: history.state }));
        scheduleRender();
    }

    async function loadClassifications() {
        state.classesLoaded = true;
        try {
            const response = await fetch('/api/producao/cronograma-entregas/classificacoes');
            if (!response.ok) throw new Error();
            state.classes = (await response.json()).classificacoes || [];
            scheduleRender();
        } catch (_) {
            // The delivery request still works without a classification filter.
        }
    }

    async function loadDeliveries() {
        const requestId = ++state.request;
        state.loading = true;
        state.error = '';
        scheduleRender();
        const params = new URLSearchParams({ mes: String(state.month), ano: String(state.year) });
        if (state.classification) params.set('classificacao', state.classification);
        if (state.search.trim()) params.set('pesquisa', state.search.trim());
        try {
            const response = await fetch(`/api/producao/cronograma-entregas?${params}`);
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || 'Falha ao consultar o GRV.');
            if (requestId !== state.request) return;
            state.deliveries = data.entregas || [];
            state.loaded = true;
        } catch (error) {
            if (requestId !== state.request) return;
            state.deliveries = [];
            state.error = error.message || 'Falha ao consultar o GRV.';
            state.loaded = false;
        } finally {
            if (requestId === state.request) {
                state.loading = false;
                scheduleRender();
            }
        }
    }

    function changeMonth(delta) {
        const date = new Date(state.year, state.month - 1 + delta, 1);
        state.month = date.getMonth() + 1;
        state.year = date.getFullYear();
        loadDeliveries();
    }

    function makeTabs() {
        const tabs = element('div', 'delivery-tabs');
        tabs.setAttribute('role', 'tablist');
        [['estrutura', 'Estrutura'], ['cronograma', 'Cronograma de Entrega']].forEach(([id, label]) => {
            const button = element('button', '', label);
            button.type = 'button';
            button.setAttribute('role', 'tab');
            button.setAttribute('aria-selected', String(state.tab === id));
            button.addEventListener('click', () => {
                state.tab = id;
                document.documentElement.dataset.deliveryTab = id;
                scheduleRender();
                if (id === 'cronograma') {
                    if (!state.classesLoaded) loadClassifications();
                    if (!state.loaded && !state.loading) loadDeliveries();
                }
            });
            tabs.append(button);
        });
        return tabs;
    }

    function makeFilters() {
        const wrapper = element('div', 'delivery-filters');
        const period = element('div', 'delivery-period');
        const previous = element('button', 'delivery-nav', '‹');
        previous.type = 'button';
        previous.setAttribute('aria-label', 'Mês anterior');
        previous.addEventListener('click', () => changeMonth(-1));
        const month = element('select', 'delivery-month');
        month.setAttribute('aria-label', 'Mês');
        monthNames.forEach((name, index) => {
            const option = element('option', '', name);
            option.value = String(index + 1);
            month.append(option);
        });
        month.value = String(state.month);
        month.addEventListener('change', () => { state.month = Number(month.value); loadDeliveries(); });
        const year = element('input', 'delivery-year');
        year.type = 'number'; year.min = '2000'; year.max = '2100';
        year.value = String(state.year); year.setAttribute('aria-label', 'Ano');
        year.addEventListener('change', () => {
            const value = Number(year.value);
            if (value >= 2000 && value <= 2100) { state.year = value; loadDeliveries(); }
        });
        const next = element('button', 'delivery-nav', '›');
        next.type = 'button'; next.setAttribute('aria-label', 'Próximo mês');
        next.addEventListener('click', () => changeMonth(1));
        period.append(previous, month, year, next);

        const label = element('label', 'delivery-classification-label', 'Classificação');
        const classification = element('select', 'delivery-classification');
        const all = element('option', '', 'Todas'); all.value = ''; classification.append(all);
        state.classes.forEach((name) => {
            const option = element('option', '', name); option.value = name; classification.append(option);
        });
        classification.value = state.classification;
        classification.addEventListener('change', () => {
            state.classification = classification.value;
            loadDeliveries();
        });
        label.append(classification);
        const search = element('input', 'delivery-search');
        search.type = 'search';
        search.placeholder = 'Pesquisar ORÇ, OS, cliente ou descrição...';
        search.setAttribute('aria-label', 'Pesquisar entregas');
        search.value = state.search;
        search.addEventListener('input', () => {
            state.search = search.value;
            clearTimeout(searchTimer);
            searchTimer = setTimeout(loadDeliveries, 300);
        });
        wrapper.append(period, label, search);
        return wrapper;
    }

    function makeCard(delivery) {
        const card = element('article', 'delivery-card');
        const key = `${delivery.orcamento}/${delivery.versao}`;
        const open = state.openBudget === key;
        const toggle = element('button', 'delivery-card-toggle');
        toggle.type = 'button';
        toggle.setAttribute('aria-expanded', String(open));
        const title = element('strong', '', `ORÇ ${delivery.orcamento}${delivery.versao ? ` · ${delivery.versao}` : ''}`);
        const marker = element('span', 'delivery-chevron', open ? '⌄' : '›');
        toggle.append(title, marker);
        toggle.addEventListener('click', () => {
            state.openBudget = open ? null : key;
            if (!open) {
                const initial = delivery.os.find((order) => order.principal) || delivery.os[0];
                if (initial) selectOS(initial.numero);
            }
            scheduleRender();
        });
        card.append(toggle);
        if (delivery.cliente) card.append(element('div', 'delivery-client', delivery.cliente));
        if (delivery.descricao) card.append(element('div', 'delivery-description', delivery.descricao));
        const meta = element('div', 'delivery-meta');
        meta.append(element('span', '', delivery.classificacoes.join(', ') || 'Sem classificação'));
        meta.append(element('span', '', formatDate(delivery.data_entrega)));
        card.append(meta);
        const status = element('div', 'delivery-status');
        const due = element('span', `delivery-due delivery-due-${deliveryLabel(delivery).toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/\s+/g, '-')}`, deliveryLabel(delivery));
        status.append(due, element('span', '', delivery.status));
        card.append(status);
        if (delivery.percentual !== null) {
            const progress = element('div', 'delivery-progress');
            const track = element('span', 'delivery-progress-track');
            const fill = element('span', 'delivery-progress-fill');
            fill.style.width = `${Math.max(0, Math.min(100, delivery.percentual))}%`;
            track.append(fill);
            progress.append(track, element('span', '', `${delivery.percentual}%`));
            card.append(progress);
        }
        if (open) {
            const orders = element('div', 'delivery-orders');
            orders.append(element('div', 'delivery-orders-title', 'OS vinculadas'));
            if (!delivery.os.length) orders.append(element('p', 'delivery-message', 'Orçamento sem OS vinculada.'));
            delivery.os.forEach((order) => {
                const button = element('button', 'delivery-order', order.numero);
                button.type = 'button';
                if (order.numero === selectedOrder()) button.classList.add('selected');
                if (order.principal) button.append(element('small', '', 'Principal'));
                button.title = order.descricao || order.numero;
                button.addEventListener('click', () => selectOS(order.numero));
                orders.append(button);
            });
            card.append(orders);
        }
        return card;
    }

    function render() {
        renderScheduled = false;
        document.documentElement.dataset.deliveryTab = state.tab;
        const tree = document.querySelector('.tree-panel');
        if (!tree) return;
        const focused = document.activeElement;
        const focusClass = focused?.closest?.('.delivery-panel') ? focused.className : '';
        const caret = focusClass === 'delivery-search' ? focused.selectionStart : null;
        const title = tree.querySelector(':scope > .panel-title');
        if (!title) return;
        const oldTabs = title.querySelector(':scope > .delivery-tabs');
        if (oldTabs) oldTabs.remove();
        const tabs = makeTabs();
        title.prepend(tabs);
        const oldPanel = tree.querySelector(':scope > .delivery-panel');
        if (oldPanel) oldPanel.remove();
        const panel = element('div', 'delivery-panel');
        panel.setAttribute('role', 'tabpanel');
        panel.setAttribute('aria-label', 'Cronograma de Entrega');
        panel.append(makeFilters());
        const list = element('div', 'delivery-list');
        if (state.loading) list.append(element('p', 'delivery-message', 'Carregando entregas...'));
        else if (state.error) list.append(element('p', 'delivery-message delivery-error', state.error));
        else if (!state.deliveries.length)
            list.append(element('p', 'delivery-message', 'Nenhuma entrega prevista para este período.'));
        else state.deliveries.forEach((delivery) => list.append(makeCard(delivery)));
        panel.append(list);
        title.after(panel);
        if (focusClass) {
            const replacement = [...panel.querySelectorAll('input, select')]
                .find((input) => input.className === focusClass);
            replacement?.focus();
            if (replacement && caret !== null) replacement.setSelectionRange(caret, caret);
        }
    }

    function scheduleRender() {
        if (renderScheduled) return;
        renderScheduled = true;
        requestAnimationFrame(render);
    }

    const observer = new MutationObserver((records) => {
        if (records.some((record) => [...record.addedNodes, ...record.removedNodes].some((node) =>
            node.nodeType === 1 && !node.classList?.contains('delivery-tabs') && !node.classList?.contains('delivery-panel')))) {
            const tree = document.querySelector('.tree-panel');
            if (tree && (!tree.querySelector(':scope > .panel-title > .delivery-tabs') || !tree.querySelector(':scope > .delivery-panel')))
                scheduleRender();
        }
    });
    observer.observe(document.getElementById('root'), { childList: true, subtree: true });
    window.addEventListener('popstate', scheduleRender);
    scheduleRender();
})();
