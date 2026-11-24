(() => {
  'use strict';
  const id = 'hermes-command-center';
  const script = document.currentScript;
  if (!script?.src || document.getElementById('hcc-host-dialog')) return;
  const entry = new URL('index.html', script.src);
  if (entry.origin !== location.origin) return;
  const extension = window.hermesExt?.register?.(id);
  const label = 'Command Center';
  const host = document.createElement('dialog');
  host.id = 'hcc-host-dialog';
  host.setAttribute('aria-label', label);
  Object.assign(host.style, {
    width: '100%', height: '100%', maxWidth: 'none', maxHeight: 'none',
    margin: '0', padding: '0', border: '0', background: '#080d17', overflow: 'hidden',
  });
  document.body.append(host);
  let frame;
  let focusBack;

  async function open(opener, restoreFocus) {
    if (host.open) return;
    focusBack = restoreFocus || (() => opener?.focus());
    frame = document.createElement('iframe');
    frame.title = label;
    frame.allowFullscreen = true;
    frame.srcdoc = '<!doctype html><meta charset="utf-8"><p>Loading Command Center…</p>';
    Object.assign(frame.style, { display: 'block', width: '100%', height: '100%', border: '0' });
    const activeFrame = frame;
    host.append(activeFrame);
    host.showModal();
    try {
      const response = await fetch(entry, { credentials: 'same-origin', redirect: 'error' });
      if (!response.ok) throw new Error('HCC assets are unavailable');
      const page = new DOMParser().parseFromString(await response.text(), 'text/html');
      const base = page.createElement('base');
      base.href = entry.href;
      page.head.prepend(base);
      if (frame === activeFrame) activeFrame.srcdoc = `<!doctype html>${page.documentElement.outerHTML}`;
    } catch {
      if (frame === activeFrame) activeFrame.srcdoc = '<!doctype html><meta charset="utf-8"><p>Command Center assets are unavailable. Close this panel and reload WebUI.</p>';
    }
  }

  host.addEventListener('close', () => {
    frame?.remove();
    frame = null;
    focusBack?.();
    focusBack = null;
  });
  window.addEventListener('message', event => {
    if (event.origin === location.origin && event.source === frame?.contentWindow && event.data?.type === 'hcc:close') host.close();
  });
  extension?.settings?.registerConfigure?.(({ opener, restoreFocus }) => open(opener, restoreFocus));

  const icon = '<svg width="20" height="20" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2 21 7v10l-9 5-9-5V7zM3 7l9 5 9-5M12 12v10M7.5 4.5l9 5v6"/></svg>';
  for (const [selector, className] of [['.rail', 'rail-btn nav-tab'], ['.sidebar-nav', 'nav-tab']]) {
    const nav = document.querySelector(selector);
    if (!nav) continue;
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `${className} has-tooltip`;
    button.dataset.hccOpen = '';
    button.dataset.tooltip = label;
    button.title = label;
    button.setAttribute('aria-label', label);
    button.setAttribute('aria-haspopup', 'dialog');
    button.innerHTML = icon;
    button.addEventListener('click', () => open(button));
    nav.insertBefore(button, nav.querySelector('.rail-spacer, [data-panel="settings"]'));
  }
  if (!document.querySelector('[data-hcc-open]')) {
    const nav = document.querySelector('.app-titlebar') || document.body;
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = label;
    button.dataset.hccOpen = '';
    button.addEventListener('click', () => open(button));
    nav.append(button);
  }
})();
