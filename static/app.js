'use strict';

const $ = (id) => document.getElementById(id);
const ns = 'http://www.w3.org/2000/svg';
const relationNames = {conversation: 'Розмова', conflict: 'Конфлікт', support: 'Допомога', family: 'Родинний зв’язок', transfer: 'Передача', other: 'Взаємодія'};
const colors = ['#587cc4', '#378f8a', '#a17cc0', '#dd9a5b', '#687eaa', '#a77889', '#68a379'];
const fallbackExamples = [{id: 'letter', title: 'Лист у бібліотеці', tag: 'Приклад', text: 'Марія зустріла Олега в бібліотеці. Вона передала йому листа. Олег показав листа Анні.'}];
const state = {examples: [], result: null, text: '', selected: null, filter: 'all', busy: false, positions: {}, zoom: 1, panX: 0, panY: 0};
const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (ch) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[ch]));
const clamp = (n, a, b) => Math.min(b, Math.max(a, n));

function setStatus(message, mode = '') {
    const el = $('globalStatus');
    el.className = `status-chip ${mode}`;
    el.innerHTML = `<span class="status-ring"></span>${escapeHtml(message)}`;
}

function setBusy(value) {
    state.busy = value;
    $('analyzeButton').disabled = value;
    $('analyzeButton').classList.toggle('loading', value);
    $('analyzeButton').querySelector('span:nth-child(2)').textContent = value ? 'Виконується аналіз…' : 'Аналізувати текст';
    if (value) setStatus('Аналіз тексту…', 'busy');
}

async function loadExamples() {
    try {
        const response = await fetch('/static/examples.json', {cache: 'no-store'});
        state.examples = response.ok ? await response.json() : fallbackExamples;
    } catch (_) {
        state.examples = fallbackExamples;
    }
    const list = $('exampleList');
    list.innerHTML = state.examples.map((example) => `<button class="example-button" data-example="${escapeHtml(example.id)}" type="button"><span class="story-icon">▤</span><span><strong>${escapeHtml(example.title)}</strong><small>${escapeHtml(example.tag)}</small></span></button>`).join('');
    list.querySelectorAll('[data-example]').forEach((btn) => btn.addEventListener('click', () => chooseExample(btn.dataset.example)));
    if (state.examples.length) chooseExample(state.examples[0].id);
}

function chooseExample(id) {
    const example = state.examples.find((item) => item.id === id);
    if (!example || state.busy) return;
    $('sourceText').value = example.text;
    updateCharCount();
    document.querySelectorAll('[data-example]').forEach((el) => el.classList.toggle('active', el.dataset.example === id));
    $('errorBox').hidden = true;
}

async function checkStatus() {
    try {
        const response = await fetch('/api/status');
        const result = await response.json();
        $('configIndicator').classList.toggle('ok', result.configured);
        $('configText').textContent = result.configured ? 'API налаштовано' : 'Потрібен API-ключ';
        $('configSubtext').textContent = result.configured ? result.model : 'local_config.py';
    } catch (_) {
        $('configText').textContent = 'Сервер недоступний';
        $('configSubtext').textContent = 'Перевір Python-сервер';
    }
}

function updateCharCount() {
    const n = $('sourceText').value.length;
    $('charCount').textContent = `${n.toLocaleString('uk-UA')} / 12 000`;
    $('charCount').style.color = n > 12000 ? '#bc5361' : '';
}

async function analyze() {
    if (state.busy) return;
    const text = $('sourceText').value.trim();
    $('errorBox').hidden = true;
    if (!text || text.length > 12000) {
        showError(!text ? 'Введи текст для аналізу.' : 'Текст перевищує 12 000 символів.');
        return;
    }
    state.text = text;
    setBusy(true);
    try {
        const response = await fetch('/api/analyze', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({text})});
        let data;
        try { data = await response.json(); } catch (_) { throw new Error('Сервер повернув некоректну відповідь.'); }
        if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Не вдалося виконати аналіз.');
        if (!Array.isArray(data.characters) || !Array.isArray(data.relations)) throw new Error('Некоректний формат результату.');
        state.result = data;
        state.selected = null;
        state.zoom = window.innerWidth <= 620 ? 1.45 : 1;
        state.panX = 0;
        state.panY = 0;
        state.filter = 'all';
        document.querySelectorAll('.filter-chip').forEach((el) => el.classList.toggle('active', el.dataset.filter === 'all'));
        render();
        setStatus(['complete', 'not_required'].includes(data.metadata?.agent_status) ? 'Аналіз завершено' : 'Частковий результат', ['complete', 'not_required'].includes(data.metadata?.agent_status) ? '' : 'partial');
    } catch (err) {
        showError(err.message || 'Помилка аналізу.');
        setStatus('Помилка аналізу', 'error');
    } finally {
        setBusy(false);
        if (state.result && $('errorBox').hidden) setStatus(['complete', 'not_required'].includes(state.result.metadata?.agent_status) ? 'Аналіз завершено' : 'Частковий результат', ['complete', 'not_required'].includes(state.result.metadata?.agent_status) ? '' : 'partial');
    }
}

function showError(msg) {
    $('errorBox').textContent = msg;
    $('errorBox').hidden = false;
}

function render() {
    const r = state.result;
    if (!r) return;
    $('countCharacters').textContent = r.characters.length;
    $('countRelations').textContent = r.relations.length;
    $('countMentions').textContent = r.mentions.length;
    $('countUnresolved').textContent = r.mentions.filter((m) => !m.character_id).length;
    $('graphSubtitle').textContent = `${r.characters.length} персонажів · ${r.relations.length} підтверджених взаємодій`;
    $('modelInfo').textContent = r.metadata?.model ? `${r.metadata.model} · ${r.metadata.requests ?? '?'} запитів` : '';
    $('graphEmpty').hidden = true;
    $('graphSvg').removeAttribute('hidden');
    $('graphHint').hidden = false;
    $('exportButton').disabled = false;
    layoutNodes(r.characters);
    renderGraph();
    renderSentences();
    renderAgentTrace();
    renderInspector();
}

function layoutNodes(characters) {
    const n = characters.length;
    const radius = clamp(115 + n * 30, 172, 230);
    state.positions = {};
    characters.forEach((c, i) => {
        const angle = -Math.PI / 2 + (i * 2 * Math.PI) / Math.max(n, 1);
        state.positions[c.id] = {x: 400 + (n === 1 ? 0 : radius * Math.cos(angle)), y: 255 + (n === 1 ? 0 : radius * Math.sin(angle))};
    });
}

function createSvg(tag, attrs = {}, parent) {
    const el = document.createElementNS(ns, tag);
    for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, String(value));
    if (parent) parent.appendChild(el);
    return el;
}

function updateView() {
    $('graphTransform').setAttribute('transform', `translate(${state.panX} ${state.panY}) translate(400 260) scale(${state.zoom}) translate(-400 -260)`);
}

function labelShort(text) {
    return text.length > 25 ? text.slice(0, 23) + '…' : text;
}

function renderGraph() {
    if (!state.result) return;
    const edgeLayer = $('edgesLayer');
    const nodeLayer = $('nodesLayer');
    edgeLayer.replaceChildren();
    nodeLayer.replaceChildren();
    const data = state.result;
    const filtered = data.relations.filter((relation) => state.filter === 'all' || relation.kind === state.filter);
    const used = new Set();
    filtered.forEach((relation, i) => {
        const a = state.positions[relation.source_id];
        const b = state.positions[relation.target_id];
        if (!a || !b) return;
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const length = Math.max(1, Math.hypot(dx, dy));
        const pair = [relation.source_id, relation.target_id].sort().join('-');
        const parallel = [...used].filter((key) => key.startsWith(pair + ':')).length;
        used.add(`${pair}:${relation.id}`);
        const curve = parallel % 2 === 0 ? parallel * 19 : -parallel * 19;
        const midX = (a.x + b.x) / 2 - (dy / length) * curve;
        const midY = (a.y + b.y) / 2 + (dx / length) * curve;
        const path = `M${a.x},${a.y} Q${midX},${midY} ${b.x},${b.y}`;
        const g = createSvg('g', {class: `graph-edge ${state.selected?.kind === 'relation' && state.selected.id === relation.id ? 'selected' : ''}`, 'data-relation': relation.id}, edgeLayer);
        createSvg('path', {d: path, class: 'edge-line'}, g);
        createSvg('path', {d: path, class: 'edge-hit'}, g);
        const labelX = .25 * a.x + .5 * midX + .25 * b.x;
        const labelY = .25 * a.y + .5 * midY + .25 * b.y;
        const display = labelShort(relation.label);
        const width = clamp(display.length * 6.1 + 18, 50, 185);
        createSvg('rect', {x: labelX - width / 2, y: labelY - 13, rx: 6, width, height: 24, class: 'edge-label-rect'}, g);
        const label = createSvg('text', {x: labelX, y: labelY + 3, 'text-anchor': 'middle'}, g);
        label.textContent = display;
        g.addEventListener('click', (e) => { e.stopPropagation(); selectElement('relation', relation.id); });
    });
    data.characters.forEach((character, i) => {
        const p = state.positions[character.id];
        if (!p) return;
        const g = createSvg('g', {class: `graph-node ${state.selected?.kind === 'character' && state.selected.id === character.id ? 'selected' : ''}`, 'data-character': character.id, transform: `translate(${p.x} ${p.y})`}, nodeLayer);
        createSvg('circle', {r: 30, fill: colors[i % colors.length]}, g);
        const initials = character.name.trim().split(/\s+/).map((part) => part[0]).slice(0, 2).join('').toUpperCase();
        const tx = createSvg('text', {'text-anchor': 'middle', y: 5}, g);
        tx.textContent = initials;
        const label = createSvg('text', {'text-anchor': 'middle', y: 51, class: 'node-label'}, g);
        label.textContent = character.name.length > 22 ? character.name.slice(0, 20) + '…' : character.name;
        g.addEventListener('click', (event) => {event.stopPropagation(); selectElement('character', character.id);});
    });
    updateView();
}

function selectElement(kind, id) {
    state.selected = {kind, id};
    renderGraph();
    renderInspector();
    renderSentences();
}

function renderInspector() {
    const box = $('inspector');
    const r = state.result;
    if (!r || !state.selected) {
        box.innerHTML = '<div class="placeholder-icon">⌕</div><h3>Оберіть елемент графа</h3><p>Натисни на персонажа або зв’язок, щоб переглянути деталі та підтверджувальні цитати.</p>';
        return;
    }
    if (state.selected.kind === 'character') {
        const c = r.characters.find((x) => x.id === state.selected.id);
        if (!c) return;
        const mentions = r.mentions.filter((m) => m.character_id === c.id);
        const related = r.relations.filter((rel) => rel.source_id === c.id || rel.target_id === c.id);
        const aliases = c.aliases.length ? `<div class="detail-subtitle">Інші згадки: ${c.aliases.map(escapeHtml).join(', ')}</div>` : '';
        box.innerHTML = `<div class="detail-type">ПЕРСОНАЖ</div><div class="detail-name">${escapeHtml(c.name)}</div>${aliases}<div class="detail-separator"></div><div class="detail-section-heading">ЗГАДКИ В ТЕКСТІ (${mentions.length})</div>${mentions.length ? mentions.slice(0, 12).map((m) => `<div class="mention-row">«${escapeHtml(m.text)}»<small>Речення ${m.sentence_id + 1}</small></div>`).join('') : '<p>Немає верифікованих згадок.</p>'}<div class="detail-separator"></div><div class="detail-section-heading">ВЗАЄМОДІЇ (${related.length})</div>${related.length ? related.map((rel) => `<div class="mention-row">${escapeHtml(rel.label)} <small>${escapeHtml(relationNames[rel.kind])}</small></div>`).join('') : '<p>Зв’язків не знайдено.</p>'}`;
    } else {
        const rel = r.relations.find((x) => x.id === state.selected.id);
        if (!rel) return;
        const source = r.characters.find((c) => c.id === rel.source_id)?.name || rel.source_id;
        const target = r.characters.find((c) => c.id === rel.target_id)?.name || rel.target_id;
        box.innerHTML = `<div class="detail-type">${escapeHtml(relationNames[rel.kind] || 'Взаємодія')}</div><div class="detail-name">${escapeHtml(rel.label)}</div><div class="detail-subtitle">${escapeHtml(source)} → ${escapeHtml(target)}</div><div class="detail-separator"></div><div class="detail-section-heading">ЦИТАТИ З ОРИГІНАЛУ</div>${rel.evidence.map((ev) => `<div class="quote-card"><small>Речення ${ev.sentence_id + 1}</small>«${escapeHtml(ev.quote)}»</div>`).join('')}<button class="inspector-button" id="jumpToEvidence">Показати речення →</button>`;
        const link = $('jumpToEvidence');
        if (link) link.addEventListener('click', () => highlightSentence(rel.evidence[0].sentence_id));
    }
}

function markupMentions(sentence, mentions) {
    const occurrences = [];
    for (const mention of mentions) {
        if (!mention.text) continue;
        let offset = 0;
        const needle = mention.text.toLocaleLowerCase();
        const haystack = sentence.toLocaleLowerCase();
        while (offset < sentence.length) {
            const pos = haystack.indexOf(needle, offset);
            if (pos < 0) break;
            occurrences.push({start: pos, end: pos + mention.text.length, unresolved: !mention.character_id});
            offset = pos + mention.text.length;
        }
    }
    occurrences.sort((a, b) => a.start - b.start || b.end - a.end);
    let cursor = 0;
    let html = '';
    for (const occ of occurrences) {
        if (occ.start < cursor) continue;
        html += escapeHtml(sentence.slice(cursor, occ.start));
        html += `<mark class="mention-mark ${occ.unresolved ? 'unresolved' : ''}">${escapeHtml(sentence.slice(occ.start, occ.end))}</mark>`;
        cursor = occ.end;
    }
    return html + escapeHtml(sentence.slice(cursor));
}

function renderSentences() {
    if (!state.result) return;
    const r = state.result;
    $('sentencesCount').textContent = `${r.sentences.length} речень`;
    $('sentences').innerHTML = r.sentences.map((sentence, i) => {
        const mentions = r.mentions.filter((m) => m.sentence_id === i);
        const focused = state.selected?.kind === 'relation' && r.relations.some((rel) => rel.id === state.selected.id && rel.evidence.some((ev) => ev.sentence_id === i));
        return `<div class="sentence-row ${focused ? 'focused' : ''}" data-sentence="${i}"><div class="sentence-index">${String(i + 1).padStart(2, '0')}</div><div class="sentence-body">${markupMentions(sentence, mentions)}</div></div>`;
    }).join('');
}

function highlightSentence(index) {
    const el = document.querySelector(`[data-sentence="${index}"]`);
    if (!el) return;
    document.querySelectorAll('.sentence-row').forEach((node) => node.classList.remove('focused'));
    el.classList.add('focused');
    el.scrollIntoView({behavior: 'smooth', block: 'center'});
}

function renderAgentTrace() {
    if (!state.result) return;
    const r = state.result;
    $('actionsCount').textContent = `${r.agent_events.length} кроків`;
    $('agentTrace').innerHTML = r.agent_events.map((e, i) => `<div class="trace-item"><div class="trace-icon ${escapeHtml(e.kind)}">${e.kind === 'tool' ? '⌕' : e.kind === 'validation' ? (e.step.includes('не завершена') ? '!' : '✓') : e.kind === 'model' ? '✦' : '≡'}</div><div class="trace-body"><strong>${escapeHtml(e.step)}</strong><p>${escapeHtml(e.detail)}</p></div></div>`).join('');
    $('warnings').innerHTML = r.warnings.slice(0, 10).map((w) => `<div class="warning-item">${escapeHtml(w)}</div>`).join('');
}

function setupGraphInteractions() {
    const svg = $('graphSvg');
    let drag = null;
    svg.addEventListener('pointerdown', (event) => {
        const node = event.target.closest?.('[data-character]');
        if (event.button !== 0) return;
        drag = {type: node ? 'node' : 'canvas', id: node?.dataset.character, clientX: event.clientX, clientY: event.clientY, moved: false};
        svg.setPointerCapture(event.pointerId);
    });
    svg.addEventListener('pointermove', (event) => {
        if (!drag) return;
        const dx = (event.clientX - drag.clientX) * 800 / Math.max(svg.clientWidth, 1);
        const dy = (event.clientY - drag.clientY) * 520 / Math.max(svg.clientHeight, 1);
        if (Math.abs(dx) > .2 || Math.abs(dy) > .2) drag.moved = true;
        drag.clientX = event.clientX;
        drag.clientY = event.clientY;
        if (drag.type === 'node' && state.positions[drag.id]) {
            state.positions[drag.id].x += dx / state.zoom;
            state.positions[drag.id].y += dy / state.zoom;
            renderGraph();
        } else {
            state.panX += dx;
            state.panY += dy;
            updateView();
        }
    });
    svg.addEventListener('pointerup', () => {drag = null;});
    svg.addEventListener('pointercancel', () => {drag = null;});
    svg.addEventListener('wheel', (event) => {
        event.preventDefault();
        state.zoom = clamp(state.zoom * (event.deltaY > 0 ? .9 : 1.1), .35, 3);
        updateView();
    }, {passive: false});
    $('zoomIn').addEventListener('click', () => {state.zoom = clamp(state.zoom * 1.2, .35, 3); updateView();});
    $('zoomOut').addEventListener('click', () => {state.zoom = clamp(state.zoom / 1.2, .35, 3); updateView();});
    $('resetView').addEventListener('click', () => {state.zoom = window.innerWidth <= 620 ? 1.45 : 1; state.panX = 0; state.panY = 0; if (state.result) layoutNodes(state.result.characters); renderGraph();});
    document.querySelectorAll('.filter-chip').forEach((btn) => btn.addEventListener('click', () => {
        state.filter = btn.dataset.filter;
        document.querySelectorAll('.filter-chip').forEach((x) => x.classList.toggle('active', x === btn));
        renderGraph();
    }));
}

function exportResult() {
    if (!state.result) return;
    const blob = new Blob([JSON.stringify({source: state.text, ...state.result}, null, 2)], {type: 'application/json;charset=utf-8'});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'narrativegraph-analysis.json';
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function initialize() {
    $('sourceText').addEventListener('input', updateCharCount);
    $('analyzeButton').addEventListener('click', analyze);
    $('loadFile').addEventListener('click', () => $('fileInput').click());
    $('fileInput').addEventListener('change', async (event) => {
        const file = event.target.files?.[0];
        if (!file) return;
        if (file.size > 120000) return showError('TXT-файл завеликий. Використай короткий уривок.');
        $('sourceText').value = await file.text();
        document.querySelectorAll('[data-example]').forEach((btn) => btn.classList.remove('active'));
        updateCharCount();
        event.target.value = '';
        $('errorBox').hidden = true;
    });
    $('exportButton').addEventListener('click', exportResult);
    setupGraphInteractions();
    await Promise.all([loadExamples(), checkStatus()]);
}

initialize();
