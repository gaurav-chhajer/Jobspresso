(() => {
  'use strict';
  const list = document.getElementById('match-list');
  const status = document.getElementById('job-selection-status');
  let panel = document.getElementById('job-detail');
  if (!list || !status || !panel) return;
  const slider = document.getElementById('jobs-score');
  if (slider) {
    const updateScoreSlider = () => {
      const min = Number(slider.min);
      const max = Number(slider.max);
      const range = max - min;
      const progress = range > 0 ? Math.max(0, Math.min(100, ((Number(slider.value) - min) / range) * 100)) : 0;
      slider.style.setProperty('--score-progress', `${progress}%`);
      slider.nextElementSibling.value = `${slider.value}%`;
    };
    slider.addEventListener('input', updateScoreSlider);
    updateScoreSlider();
  }
  list.classList.add('js-tiles');
  const tiles = [...list.querySelectorAll('[data-company-panel]')];
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  let activeRole = '';
  const roleSelector = id => `[data-role-id="${CSS.escape(id)}"]`;
  function syncPanel() {
    const open = tiles.find(tile => tile.classList.contains('is-open'));
    panel.hidden = !open || !panel.dataset.jobId || activeRole !== panel.dataset.jobId || !open.querySelector(roleSelector(panel.dataset.jobId));
  }
  function pickRole(tile, jobId) {
    tile.querySelectorAll('[data-role-id]').forEach(role => role.classList.toggle('is-shown', role.dataset.roleId === jobId));
    tile.querySelectorAll('[data-role-pick]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.rolePick === jobId)));
    activeRole = jobId;
    syncPanel();
  }
  function setOpen(tile, open) {
    tile.classList.toggle('is-open', open);
    tile.querySelector('[data-company-choice]')?.setAttribute('aria-expanded', String(open));
  }
  function openTile(tile, jobId) {
    tiles.forEach(item => setOpen(item, item === tile));
    pickRole(tile, jobId || tile.querySelector('[data-role-pick]')?.dataset.rolePick || '');
  }
  function closeTiles() {
    tiles.forEach(tile => setOpen(tile, false));
    activeRole = '';
    syncPanel();
  }
  const initialRole = list.querySelector('.company-role.is-selected');
  const hashTile = tiles.find(tile => `#${tile.id}` === window.location.hash);
  if (initialRole) openTile(initialRole.closest('[data-company-panel]'), initialRole.dataset.roleId);
  else if (hashTile) openTile(hashTile);
  else closeTiles();
  let pending;
  let sequence = 0;

  function highlight(jobId) {
    list.querySelectorAll('.job-tile .jt-link').forEach(link => {
      const selected = new URL(link.href, window.location.href).searchParams.get('job') === jobId;
      link.closest('.job-tile').classList.toggle('is-selected', selected);
      if (selected) link.setAttribute('aria-current', 'true');
      else link.removeAttribute('aria-current');
    });
    const owner = [...list.querySelectorAll('[data-role-id]')].find(role => role.dataset.roleId === jobId)?.closest('[data-company-panel]');
    if (owner) openTile(owner, jobId);
    else syncPanel();
  }

  async function select(url, push) {
    const request = ++sequence;
    if (pending) pending.abort();
    const controller = new AbortController();
    pending = controller;
    const timeout = window.setTimeout(() => controller.abort(), 8000);
    panel.setAttribute('aria-busy', 'true');
    status.textContent = 'Loading selected match...';
    try {
      // Reuse authenticated, escaped HTML; retain the surrounding page and its scroll position.
      const response = await fetch(url.href, {signal: controller.signal, cache: 'no-store', redirect: 'error'});
      if (!response.ok) throw new Error('match_unavailable');
      const page = new DOMParser().parseFromString(await response.text(), 'text/html');
      const next = page.getElementById('job-detail');
      const requested = url.searchParams.get('job');
      if (!next || !next.dataset.jobId || (requested && next.dataset.jobId !== requested)) {
        throw new Error('match_unavailable');
      }
      if (request !== sequence) return;
      const replacement = document.importNode(next, true);
      // Update history only after validating the response; failed requests retain the old URL.
      if (push) window.history.pushState(null, '', url.href);
      panel.replaceWith(replacement);
      panel = replacement;
      panel.scrollTop = 0;
      highlight(panel.dataset.jobId);
      panel.scrollIntoView({block: 'start'});
      panel.focus({preventScroll: true});
      status.textContent = 'Selected match updated.';
    } catch (error) {
      if (request !== sequence) return; // Superseded selections and page shutdown own cancellation.
      status.textContent = 'Could not load the selected match. Previous details remain visible. Try again or reload to sign in.';
    } finally {
      window.clearTimeout(timeout);
      if (request === sequence) {
        panel.removeAttribute('aria-busy');
        pending = null;
      }
    }
  }

  function click(event) {
    if (event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    const toggle = event.target.closest('[data-company-choice]');
    const pick = event.target.closest('[data-role-pick]');
    if ((toggle && list.contains(toggle)) || (pick && list.contains(pick))) {
      event.preventDefault();
      sequence += 1;
      if (pending) pending.abort();
      pending = null;
      panel.removeAttribute('aria-busy');
      const tile = (toggle || pick).closest('[data-company-panel]');
      if (pick) {
        pickRole(tile, pick.dataset.rolePick);
        status.textContent = 'Role selected.';
      } else if (tile.classList.contains('is-open')) {
        closeTiles();
        status.textContent = 'Company roles closed.';
      } else {
        openTile(tile);
        status.textContent = 'Showing roles for ' + (tile.querySelector('.co-name')?.textContent || 'this company') + '.';
        window.requestAnimationFrame(() => tile.scrollIntoView({block: 'nearest', behavior: reduceMotion ? 'auto' : 'smooth'}));
      }
      return;
    }
    const link = event.target.closest('.jt-link');
    if (!link || !list.contains(link) || (link.target && link.target !== '_self') || link.hasAttribute('download')) return;
    const url = new URL(link.href, window.location.href);
    if (url.origin !== window.location.origin || url.pathname !== window.location.pathname || !url.searchParams.get('job')) return;
    event.preventDefault();
    // Abort a pending different selection even when returning to the currently displayed job.
    if (url.searchParams.get('job') === panel.dataset.jobId) {
      sequence += 1;
      if (pending) pending.abort();
      pending = null;
      panel.removeAttribute('aria-busy');
      status.textContent = 'Selected match updated.';
      return;
    }
    void select(url, true);
  }

  function restore() {
    void select(new URL(window.location.href), false);
  }
  function cleanup(event) {
    sequence += 1;
    if (pending) pending.abort();
    pending = null;
    panel.removeAttribute('aria-busy');
    if (event.persisted) return;
    list.removeEventListener('click', click);
    window.removeEventListener('popstate', restore);
    window.removeEventListener('pagehide', cleanup);
  }
  list.addEventListener('click', click);
  list.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    const open = event.target.closest('.co-tile.is-open');
    if (!open) return;
    closeTiles();
    open.querySelector('[data-company-choice]')?.focus();
  });
  window.addEventListener('popstate', restore);
  window.addEventListener('pagehide', cleanup);
})();
