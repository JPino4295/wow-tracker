'use strict';

const TYPE_LABELS = {
  mount: 'Montura',
  transmog: 'Transfiguración',
  toy: 'Juguete',
  pet: 'Mascota',
  other: 'Otros',
};
const KIND_LABELS = { raid: 'Raid', rare: 'Rare', boss: 'Boss' };
const PERIOD_LABELS = { daily: 'Diaria', weekly: 'Semanal' };
const WEEKDAYS = ['lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo'];

const state = { users: [], sources: [], items: [], userId: null, meta: null };
const $ = (id) => document.getElementById(id);

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail || detail;
    } catch (_) {
      /* non-JSON error */
    }
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  return response.status === 204 ? null : response.json();
}

function jsonOptions(method, payload) {
  return { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) };
}

function user() {
  return state.users.find((entry) => entry.id === state.userId) || null;
}

function progressOf(item) {
  return item.progress[String(state.userId)] || { done: false, last_done_at: null };
}

function formatDate(value) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('es-ES');
}

function icon(value, big = false) {
  const box = el('div', `icon-box${big ? ' big' : ''}`);
  if (value && /^https?:\/\//i.test(value)) {
    const image = el('img');
    image.src = value;
    image.alt = '';
    box.append(image);
  } else if (value) {
    const image = el('img');
    image.src = `/media/${encodeURIComponent(value)}`;
    image.alt = '';
    image.addEventListener('error', () => box.replaceChildren(el('span', null, '🎲')));
    box.append(image);
  } else {
    box.append(el('span', null, '🎲'));
  }
  return box;
}

function badge(text, className = '') {
  return el('span', `badge ${className}`.trim(), text);
}

function renderUserSelect() {
  const select = $('user-select');
  select.replaceChildren();
  for (const entry of state.users) {
    const option = el('option', null, entry.name);
    option.value = String(entry.id);
    select.append(option);
  }
  select.value = String(state.userId);
}

function renderOwnerOptions(select = $('add-owner')) {
  select.replaceChildren();
  const both = el('option', null, 'Ambos');
  both.value = '';
  select.append(both);
  for (const entry of state.users) {
    const option = el('option', null, entry.name);
    option.value = String(entry.id);
    select.append(option);
  }
}

function ownerName(item) {
  if (item.owner_user_id === null) return 'Ambos';
  return state.users.find((entry) => entry.id === item.owner_user_id)?.name || 'Otro jugador';
}

function isForeign(item) {
  return item.owner_user_id !== null && item.owner_user_id !== state.userId;
}

function selectedValues(select) {
  return Array.from(select.selectedOptions, (option) => option.value);
}

function selectValues(select, values) {
  for (const option of select.options) option.selected = values.includes(option.value);
}

function renderSourceOptions() {
  const filter = $('f-source');
  const filterPrevious = filter.value;
  const add = $('add-sources');
  const edit = $('edit-sources');
  const addPrevious = selectedValues(add);
  const editPrevious = selectedValues(edit);

  filter.replaceChildren(el('option', null, 'Todas'));
  filter.firstChild.value = '';
  add.replaceChildren();
  edit.replaceChildren();

  for (const source of state.sources) {
    const label = `${source.name} · ${KIND_LABELS[source.kind]}`;
    for (const select of [filter, add, edit]) {
      const option = el('option', null, label);
      option.value = String(source.id);
      select.append(option);
    }
  }
  filter.value = filterPrevious;
  selectValues(add, addPrevious);
  selectValues(edit, editPrevious);
}

async function loadMeta() {
  state.meta = await api('/api/meta');
  state.users = state.meta.users;
  const stored = Number(localStorage.getItem('wowtracker.user'));
  state.userId = state.users.some((entry) => entry.id === stored)
    ? stored
    : (state.users[0]?.id ?? null);
  if (state.userId !== null) localStorage.setItem('wowtracker.user', String(state.userId));
  renderUserSelect();
  renderOwnerOptions();
  renderOwnerOptions($('edit-owner'));

  const day = WEEKDAYS[state.meta.weekly_weekday] || 'martes';
  const pad = (hour) => String(hour).padStart(2, '0');
  $('reset-info').textContent =
    `Reset diario ${pad(state.meta.daily_hour_utc)}:00 UTC · semanal ${day} ${pad(state.meta.weekly_hour_utc)}:00 UTC`;
}

async function loadSources() {
  state.sources = await api('/api/sources');
  renderSourceOptions();
}

function queryString() {
  const params = new URLSearchParams();
  const pairs = {
    type: $('f-type').value,
    periodicity: $('f-periodicity').value,
    source_id: $('f-source').value,
    source_kind: $('f-source-kind').value,
    status: $('f-status').value,
    q: $('f-q').value,
  };
  for (const [key, value] of Object.entries(pairs)) {
    if (value) params.set(key, value);
  }
  if (state.userId !== null) params.set('user_id', String(state.userId));
  return params.toString();
}

async function loadItems() {
  state.items = await api(`/api/items?${queryString()}`);
  renderItems();
}

function sourceLabel(item) {
  if (!item.sources.length) return 'Sin fuente asignada';
  return item.sources.map((link) => link.source_name).join(', ');
}

function cardFor(item) {
  const done = progressOf(item).done;
  const foreign = isForeign(item);
  const card = el('article', `card${done ? ' is-done' : ''}${foreign ? ' is-foreign' : ''}`);

  const open = el('button', 'card-open');
  open.type = 'button';
  open.setAttribute('aria-label', `Ver ficha de ${item.name}`);
  const info = el('div', 'card-info');
  info.append(el('div', 'card-title', item.name));
  const badges = el('div', 'badges');
  const cardBadges = [
    badge(TYPE_LABELS[item.type] || item.type),
    badge(PERIOD_LABELS[item.periodicity] || item.periodicity, item.periodicity),
    badge(done ? 'Hecho' : 'Pendiente', done ? 'done' : 'pending'),
  ];
  if (foreign) cardBadges.push(badge(`Solo para ${ownerName(item)}`));
  badges.append(...cardBadges);
  info.append(badges, el('div', 'card-meta', sourceLabel(item)));
  open.append(icon(item.icon_local), info);
  open.addEventListener('click', () => openDetail(item.id));

  const actions = el('div', 'card-actions');
  const link = el('a', null, 'Wowhead ↗');
  link.href = item.url_wowhead || '#';
  link.target = '_blank';
  link.rel = 'noopener noreferrer';
  const doneButton = foreign
    ? el('button', 'done-btn', `Solo para ${ownerName(item)}`)
    : el('button', `done-btn${done ? ' undo' : ''}`, done ? 'Hecho ✓' : 'Marcar hecho');
  doneButton.type = 'button';
  if (foreign) {
    doneButton.disabled = true;
    doneButton.title = 'Este objeto está asignado a otro jugador';
  } else {
    doneButton.addEventListener('click', async () => {
      try {
        const updated = await api(`/api/items/${item.id}/progress?user_id=${state.userId}`,
          jsonOptions('PUT', { done: !done }));
        Object.assign(item, updated);
        renderItems();
      } catch (error) {
        window.alert(error.message);
      }
    });
  }
  actions.append(doneButton, link);
  card.append(open, actions);
  return card;
}

function renderItems() {
  const list = $('item-list');
  list.replaceChildren();
  const visible = state.items.length;
  const ownItems = state.items.filter((item) => !isForeign(item));
  const pending = ownItems.filter((item) => !progressOf(item).done).length;
  $('summary').textContent = `${pending} pendientes de ${visible} visibles · ${user()?.name || '—'}`;

  if (!visible) {
    list.append(el('p', 'empty', 'No hay objetos con estos filtros. Añade uno o limpia los filtros.'));
    return;
  }
  for (const item of state.items) list.append(cardFor(item));
}

function openDetail(id) {
  const item = state.items.find((entry) => entry.id === id);
  if (!item) return;
  const foreign = isForeign(item);
  const body = $('detail-body');
  body.replaceChildren();

  const head = el('div', 'detail-head');
  const titles = el('div');
  titles.append(el('h2', null, item.name));
  const badges = el('div', 'badges');
  const detailBadges = [
    badge(TYPE_LABELS[item.type] || item.type),
    badge(PERIOD_LABELS[item.periodicity] || item.periodicity, item.periodicity),
    badge(`Para: ${ownerName(item)}`),
  ];
  if (isForeign(item)) detailBadges.push(badge('Solo lectura'));
  badges.append(...detailBadges);
  titles.append(badges);
  head.append(icon(item.icon_local, true), titles);
  body.append(head);

  if (!foreign) {
    const editBar = el('div', 'detail-actions');
    const editButton = el('button', 'ghost', '✎ Editar');
    editButton.type = 'button';
    editButton.addEventListener('click', () => openEdit(item));
    editBar.append(editButton);
    body.append(editBar);
  }

  if (item.description) {
    const section = el('div', 'detail-section');
    section.append(el('h3', null, 'Descripción'), el('p', null, item.description));
    body.append(section);
  }

  const sourceSection = el('div', 'detail-section');
  sourceSection.append(el('h3', null, 'Fuentes y drop'));
  if (!item.sources.length) {
    sourceSection.append(el('p', 'muted', 'Sin fuentes asignadas.'));
  }
  const editableSources = [];
  for (const link of item.sources) {
    const row = el('div', 'source-row');
    row.append(el('div', null,
      `${KIND_LABELS[link.source_kind] || link.source_kind}: ${link.source_name}${link.zone ? ` · ${link.zone}` : ''}`));

    const inputs = el('div', 'source-edit');
    const rate = el('input');
    rate.type = 'number';
    rate.min = '0';
    rate.max = '100';
    rate.step = '0.01';
    rate.placeholder = 'Drop %';
    rate.value = link.drop_rate === null ? '' : String(link.drop_rate);
    rate.setAttribute('aria-label', `Drop rate de ${link.source_name}`);
    const difficulty = el('input');
    difficulty.value = link.difficulty || '';
    difficulty.placeholder = 'Dificultad';
    difficulty.setAttribute('aria-label', `Dificultad de ${link.source_name}`);
    const conditions = el('input');
    conditions.value = link.conditions || '';
    conditions.placeholder = 'Condiciones';
    conditions.setAttribute('aria-label', `Condiciones de ${link.source_name}`);
    if (foreign) {
      rate.disabled = true;
      difficulty.disabled = true;
      conditions.disabled = true;
    }
    inputs.append(rate, difficulty, conditions);
    row.append(inputs);
    editableSources.push({ source_id: link.source_id, rate, difficulty, conditions });

    if (link.wowhead_url) {
      const anchor = el('a', 'small', 'Wowhead ↗');
      anchor.href = link.wowhead_url;
      anchor.target = '_blank';
      anchor.rel = 'noopener noreferrer';
      row.append(anchor);
    }
    sourceSection.append(row);
  }
  if (!foreign && editableSources.length) {
    const saveSources = el('button', 'ghost', 'Guardar fuentes');
    saveSources.type = 'button';
    saveSources.addEventListener('click', async () => {
      const sources = editableSources.map((entry) => ({
        source_id: entry.source_id,
        drop_rate: entry.rate.value === '' ? null : Number(entry.rate.value),
        difficulty: entry.difficulty.value,
        conditions: entry.conditions.value,
      }));
      try {
        const updated = await api(`/api/items/${item.id}`,
          jsonOptions('PATCH', { sources }));
        Object.assign(item, updated);
        renderItems();
        openDetail(item.id);
      } catch (error) {
        window.alert(error.message);
      }
    });
    sourceSection.append(saveSources);
  }
  body.append(sourceSection);

  const notesSection = el('div', 'detail-section');
  notesSection.append(el('h3', null, 'Notas propias'));
  const notes = el('textarea');
  notes.rows = 3;
  notes.value = item.notes || '';
  notes.setAttribute('aria-label', 'Notas propias');
  if (foreign) {
    notes.disabled = true;
    notesSection.append(notes);
  } else {
    const saveNotes = el('button', 'ghost', 'Guardar notas');
    saveNotes.type = 'button';
    saveNotes.addEventListener('click', async () => {
      try {
        const updated = await api(`/api/items/${item.id}`,
          jsonOptions('PATCH', { notes: notes.value }));
        Object.assign(item, updated);
        renderItems();
        saveNotes.textContent = 'Guardado ✓';
      } catch (error) {
        window.alert(error.message);
      }
    });
    notesSection.append(notes, saveNotes);
  }
  body.append(notesSection);

  const progress = progressOf(item);
  const progressSection = el('div', 'detail-section');
  progressSection.append(el('h3', null, 'Progreso y scraping'));
  progressSection.append(el('p', null,
    `${user()?.name || 'Jugador'}: ${progress.done ? 'hecho' : 'pendiente'} · último marcado: ${formatDate(progress.last_done_at)}`));
  progressSection.append(el('p', 'small muted',
    `Próximo reset: ${formatDate(item.next_reset_at)} · última scraping: ${formatDate(item.last_scraped_at)}`));
  if (!foreign) {
    const toggle = el('button', `done-btn${progress.done ? ' undo' : ''}`,
      progress.done ? 'Desmarcar' : 'Marcar como hecho');
    toggle.type = 'button';
    toggle.addEventListener('click', async () => {
      try {
        const updated = await api(`/api/items/${item.id}/progress?user_id=${state.userId}`,
          jsonOptions('PUT', { done: !progress.done }));
        Object.assign(item, updated);
        renderItems();
        openDetail(item.id);
      } catch (error) {
        window.alert(error.message);
      }
    });
    progressSection.append(toggle);
  }
  if (item.url_wowhead) {
    const anchor = el('a', null, 'Abrir en Wowhead ↗');
    anchor.href = item.url_wowhead;
    anchor.target = '_blank';
    anchor.rel = 'noopener noreferrer';
    anchor.style.display = 'inline-block';
    anchor.style.marginLeft = '0.75rem';
    progressSection.append(anchor);
  }
  body.append(progressSection);

  const dialog = $('detail-dialog');
  if (!dialog.open) dialog.showModal();
}

function openEdit(item) {
  const form = $('edit-form');
  form.elements['item_id'].value = String(item.id);
  form.elements['name'].value = item.name || '';
  form.elements['wowhead_id'].value = item.wowhead_id === null ? '' : String(item.wowhead_id);
  form.elements['url_wowhead'].value = item.url_wowhead || '';
  form.elements['type'].value = item.type;
  form.elements['periodicity'].value = item.periodicity;
  form.elements['owner_user_id'].value =
    item.owner_user_id === null ? '' : String(item.owner_user_id);
  form.elements['icon_local'].value = item.icon_local || '';
  form.elements['description'].value = item.description || '';
  form.elements['notes'].value = item.notes || '';
  selectValues($('edit-sources'), item.sources.map((link) => String(link.source_id)));
  $('edit-dialog').showModal();
}

async function removeUser(entry) {
  const message = `¿Eliminar a ${entry.name}?\n\n`
    + 'Solo se permite si no es el último jugador y no tiene objetos asignados '
    + 'ni progreso marcado. Desmarca esos objetos o reasígnalos antes.';
  if (!window.confirm(message)) return;
  try {
    await api(`/api/users/${entry.id}`, { method: 'DELETE' });
    $('settings-dialog').close();
    await loadMeta();
    await loadItems();
  } catch (error) {
    window.alert(error.message);
  }
}

function openSettings() {
  const container = $('settings-users');
  container.replaceChildren();
  for (const entry of state.users) {
    const row = el('div', 'user-row');
    const label = el('label', null, `Jugador ${entry.id}`);
    const input = el('input');
    input.type = 'text';
    input.name = `user-${entry.id}`;
    input.value = entry.name;
    input.maxLength = 100;
    input.required = true;
    label.append(input);

    const remove = el('button', 'icon-btn danger', '🗑');
    remove.type = 'button';
    remove.title = `Eliminar a ${entry.name}`;
    remove.setAttribute('aria-label', `Eliminar a ${entry.name}`);
    if (state.users.length <= 1) {
      remove.disabled = true;
      remove.title = 'No se puede eliminar al último jugador';
    }
    remove.addEventListener('click', () => removeUser(entry));

    row.append(label, remove);
    container.append(row);
  }
  $('settings-dialog').showModal();
}

function bindEvents() {
  for (const id of ['f-type', 'f-periodicity', 'f-source', 'f-source-kind', 'f-status']) {
    $(id).addEventListener('change', loadItems);
  }
  $('f-q').addEventListener('input', loadItems);
  $('btn-clear').addEventListener('click', () => {
    for (const id of ['f-type', 'f-periodicity', 'f-source', 'f-source-kind', 'f-status']) {
      $(id).value = '';
    }
    $('f-q').value = '';
    loadItems();
  });

  $('user-select').addEventListener('change', (event) => {
    state.userId = Number(event.target.value);
    localStorage.setItem('wowtracker.user', String(state.userId));
    loadItems();
  });
  $('btn-settings').addEventListener('click', openSettings);
  $('btn-add').addEventListener('click', () => $('add-dialog').showModal());

  for (const button of document.querySelectorAll('[data-close]')) {
    button.addEventListener('click', () => button.closest('dialog').close());
  }
  for (const dialog of document.querySelectorAll('dialog')) {
    dialog.addEventListener('click', (event) => {
      if (event.target === dialog) dialog.close();
    });
  }

  $('add-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const sourceIds = data.getAll('source_ids').map(Number);
    const shared = {
      drop_rate: data.get('drop_rate') ? Number(data.get('drop_rate')) : null,
      difficulty: data.get('difficulty') || '',
      conditions: data.get('conditions') || '',
    };
    const payload = {
      name: data.get('name'),
      wowhead_id: data.get('wowhead_id') ? Number(data.get('wowhead_id')) : null,
      url_wowhead: data.get('url_wowhead') || '',
      type: data.get('type'),
      periodicity: data.get('periodicity'),
      owner_user_id: data.get('owner_user_id') ? Number(data.get('owner_user_id')) : null,
      icon_local: data.get('icon_local') || '',
      description: data.get('description') || '',
      notes: data.get('notes') || '',
      sources: sourceIds.map((source_id) => ({ source_id, ...shared })),
    };
    try {
      await api('/api/items', jsonOptions('POST', payload));
      form.reset();
      $('add-dialog').close();
      await loadItems();
    } catch (error) {
      window.alert(error.message);
    }
  });

  $('edit-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const itemId = Number(data.get('item_id'));
    const original = state.items.find((entry) => entry.id === itemId);
    const wowheadId = data.get('wowhead_id') ? Number(data.get('wowhead_id')) : null;
    const sourceIds = data.getAll('source_ids').map(Number);
    const originalIds = original ? original.sources.map((link) => link.source_id) : [];
    const payload = {
      name: data.get('name'),
      wowhead_id: wowheadId,
      url_wowhead: data.get('url_wowhead')
        || (wowheadId ? `https://www.wowhead.com/es/item=${wowheadId}` : ''),
      type: data.get('type'),
      periodicity: data.get('periodicity'),
      owner_user_id: data.get('owner_user_id') ? Number(data.get('owner_user_id')) : null,
      icon_local: data.get('icon_local') || '',
      description: data.get('description') || '',
      notes: data.get('notes') || '',
    };
    // Only overwrite item_sources when the selection changed, so existing
    // drop rate/difficulty/conditions edited in the detail view are preserved.
    const sourcesChanged = sourceIds.length !== originalIds.length
      || sourceIds.some((id) => !originalIds.includes(id));
    if (sourcesChanged) {
      const links = new Map(
        original ? original.sources.map((link) => [link.source_id, link]) : []
      );
      payload.sources = sourceIds.map((source_id) => {
        const link = links.get(source_id);
        return {
          source_id,
          drop_rate: link ? link.drop_rate : null,
          difficulty: link ? link.difficulty || '' : '',
          conditions: link ? link.conditions || '' : '',
        };
      });
    }
    try {
      const updated = await api(`/api/items/${itemId}`, jsonOptions('PATCH', payload));
      const index = state.items.findIndex((entry) => entry.id === itemId);
      if (index !== -1) state.items[index] = updated;
      $('edit-dialog').close();
      renderItems();
      openDetail(itemId);
    } catch (error) {
      window.alert(error.message);
    }
  });

  async function createSource(prefix, targetId) {
    const payload = {
      name: $(`${prefix}-name`).value.trim(),
      kind: $(`${prefix}-kind`).value,
      zone: $(`${prefix}-zone`).value.trim(),
      wowhead_url: $(`${prefix}-url`).value.trim(),
    };
    if (!payload.name) {
      window.alert('Escribe el nombre de la fuente.');
      return;
    }
    try {
      const created = await api('/api/sources', jsonOptions('POST', payload));
      for (const suffix of ['name', 'zone', 'url']) $(`${prefix}-${suffix}`).value = '';
      await loadSources();
      const option = Array.from($(targetId).options)
        .find((entry) => entry.value === String(created.id));
      if (option) option.selected = true;
    } catch (error) {
      window.alert(error.message);
    }
  }

  $('btn-new-source').addEventListener('click', () => createSource('new-source', 'add-sources'));
  $('edit-btn-new-source').addEventListener('click', () => createSource('edit-new-source', 'edit-sources'));

  $('settings-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    try {
      for (const entry of state.users) {
        await api(`/api/users/${entry.id}`, jsonOptions('PATCH', { name: data.get(`user-${entry.id}`) }));
      }
      $('settings-dialog').close();
      await loadMeta();
      await loadItems();
    } catch (error) {
      window.alert(error.message);
    }
  });
}

async function init() {
  bindEvents();
  try {
    await loadMeta();
    await loadSources();
    await loadItems();
  } catch (error) {
    $('item-list').replaceChildren(el('p', 'empty', `Error al cargar: ${error.message}`));
  }
}

init();
