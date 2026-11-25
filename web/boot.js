(() => {
  'use strict';
  const id = 'hermes-command-center';
  const script = document.currentScript;
  if (!script?.src || document.querySelector('[data-hcc-open]')) return;
  const entry = new URL('index.html', script.src);
  if (entry.origin !== location.origin) return;
  const extension = window.hermesExt?.register?.(id);
  const label = 'Command Center';
  // Top-level navigation releases the old Hermes document and its event streams.
  extension?.settings?.registerConfigure?.(({ restoreFocus } = {}) => {
    restoreFocus?.();
    location.assign(entry.href);
  });

  const icon = '<svg width="20" height="20" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2 21 7v10l-9 5-9-5V7zM3 7l9 5 9-5M12 12v10M7.5 4.5l9 5v6"/></svg>';
  for (const [selector, className] of [['.rail', 'rail-btn nav-tab'], ['.sidebar-nav', 'nav-tab']]) {
    const nav = document.querySelector(selector);
    if (!nav) continue;
    const link = document.createElement('a');
    link.href = entry.href;
    link.className = `${className} has-tooltip`;
    link.dataset.hccOpen = '';
    link.dataset.tooltip = label;
    link.title = label;
    link.setAttribute('aria-label', label);
    link.innerHTML = icon;
    nav.insertBefore(link, nav.querySelector('.rail-spacer, [data-panel="settings"]'));
  }
  if (!document.querySelector('[data-hcc-open]')) {
    const nav = document.querySelector('.app-titlebar') || document.body;
    const link = document.createElement('a');
    link.href = entry.href;
    link.textContent = label;
    link.dataset.hccOpen = '';
    nav.append(link);
  }
})();
