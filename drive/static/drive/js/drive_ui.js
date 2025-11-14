(function () {
  function ready(fn) {
    if (document.readyState !== 'loading') {
      fn();
    } else {
      document.addEventListener('DOMContentLoaded', fn);
    }
  }

  function toggleView(root, view) {
    const listView = root.querySelector('[data-drive-list]');
    const gridView = root.querySelector('[data-drive-grid]');
    const toggleButtons = root.querySelectorAll('[data-drive-toggle]');
    if (!listView || !gridView) {
      return;
    }

    const showGrid = view === 'grid';
    listView.dataset.driveHidden = showGrid ? 'true' : 'false';
    gridView.dataset.driveHidden = showGrid ? 'false' : 'true';

    toggleButtons.forEach((btn) => {
      if (btn.dataset.driveToggle === view) {
        btn.classList.add('active');
        btn.setAttribute('aria-pressed', 'true');
      } else {
        btn.classList.remove('active');
        btn.setAttribute('aria-pressed', 'false');
      }
    });
  }

  function initViewToggle(root) {
    const toggleButtons = root.querySelectorAll('[data-drive-toggle]');
    if (!toggleButtons.length) {
      return;
    }

    const storageKey = root.dataset.driveStorageKey || 'drive:view';
    const initial = root.dataset.driveDefaultView || 'list';
    toggleView(root, initial);

    toggleButtons.forEach((btn) => {
      btn.addEventListener('click', (event) => {
        event.preventDefault();
        const next = btn.dataset.driveToggle;
        toggleView(root, next);
        try {
          window.localStorage.setItem(storageKey, next);
        } catch (err) {
          // Ignore storage errors (Safari private mode etc.)
        }
      });
    });

    try {
      const stored = window.localStorage.getItem(storageKey);
      if (stored && stored !== initial) {
        toggleView(root, stored);
      }
    } catch (err) {
      // ignore
    }
  }

  function initDrawer(root) {
    const drawerHost = document.querySelector('[data-drive-drawer]');
    if (!drawerHost) {
      return;
    }

    const gridContainer = document.getElementById('drive-documents-grid-container');

    function closeDrawer() {
      drawerHost.classList.remove('is-open');
      drawerHost.setAttribute('aria-hidden', 'true');
      drawerHost.innerHTML = '';
      drawerHost.removeAttribute('data-current-url');
    }

    function attachDrawerEvents() {
      const closeButton = drawerHost.querySelector('[data-drive-close]');
      if (closeButton) {
        closeButton.addEventListener('click', (event) => {
          event.preventDefault();
          closeDrawer();
        });
      }

      drawerHost.querySelectorAll('form[data-drive-refresh="drawer"]').forEach((form) => {
        form.addEventListener('submit', (event) => {
          event.preventDefault();
          const action = form.getAttribute('action');
          if (!action) {
            return;
          }
          const formData = new FormData(form);
          fetch(action, {
            method: form.getAttribute('method') || 'POST',
            body: formData,
            headers: {
              'X-Requested-With': 'XMLHttpRequest',
            },
            credentials: 'same-origin',
          })
            .then((resp) => {
              if (resp.status === 302) {
                // Force reload to respect redirect semantics.
                window.location.href = resp.headers.get('Location');
                return Promise.reject(new Error('redirect'));
              }
              if (!resp.ok) {
                throw new Error('Request failed');
              }
              return refreshDrawer();
            })
            .catch((err) => {
              if (err.message === 'redirect') {
                return;
              }
              console.error(err);
            });
        });
      });

      drawerHost.querySelectorAll('[data-drive-revert-button]').forEach((button) => {
        button.addEventListener('click', (event) => {
          const version = button.dataset.version;
          const form = button.closest('form');
          if (!form) {
            return;
          }
          const confirmMessage = version
            ? `Revert to version ${version}?`
            : 'Revert to selected version?';
          if (!window.confirm(confirmMessage)) {
            event.preventDefault();
            return;
          }
          const reasonInput = form.querySelector('[name="note"]');
          if (reasonInput && !reasonInput.value) {
            const response = window.prompt('Optional note for the revert:', '');
            if (response !== null) {
              reasonInput.value = response;
            }
          }
        });
      });
    }

    function refreshDrawer() {
      const url = drawerHost.getAttribute('data-current-url');
      if (!url) {
        return Promise.resolve();
      }
      drawerHost.classList.add('is-open');
      drawerHost.setAttribute('aria-hidden', 'false');
      drawerHost.innerHTML = '<div class="p-3 text-muted">Refreshing…</div>';
      return fetch(url, {
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
        credentials: 'same-origin',
      })
        .then((resp) => resp.text())
        .then((html) => {
          drawerHost.innerHTML = html;
          attachDrawerEvents();
        })
        .catch((err) => {
          console.error(err);
          drawerHost.innerHTML = '<div class="p-3 text-danger">Unable to load document details.</div>';
        });
    }

    function openDrawer(url) {
      if (!url) {
        return;
      }
      if (gridContainer && gridContainer.classList.contains('is-editing')) {
        return;
      }
      drawerHost.setAttribute('data-current-url', url);
      drawerHost.classList.add('is-open');
      drawerHost.setAttribute('aria-hidden', 'false');
      drawerHost.innerHTML = '<div class="p-3 text-muted">Loading…</div>';
      fetch(url, {
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
        credentials: 'same-origin',
      })
        .then((resp) => {
          if (!resp.ok) {
            throw new Error('Failed to fetch drawer content');
          }
          return resp.text();
        })
        .then((html) => {
          drawerHost.innerHTML = html;
          attachDrawerEvents();
          const focusTarget = drawerHost.querySelector('[data-drive-focus]');
          if (focusTarget) {
            focusTarget.focus();
          }
        })
        .catch((err) => {
          console.error(err);
          drawerHost.innerHTML = '<div class="p-3 text-danger">Unable to load document details.</div>';
        });
    }

    function handleActivation(event, source, explicitUrl) {
      if (event.defaultPrevented) {
        return;
      }
      if (event.target.closest('[data-drive-ignore]')) {
        return;
      }
      if (gridContainer && gridContainer.classList.contains('is-editing')) {
        return;
      }
      event.preventDefault();
      const url = explicitUrl || source.dataset.detailUrl || source.getAttribute('href');
      openDrawer(url);
    }

    root.querySelectorAll('[data-drive-open]').forEach((item) => {
      item.addEventListener('click', (event) => {
        handleActivation(event, item, item.dataset.detailUrl);
      });
      item.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          handleActivation(event, item, item.dataset.detailUrl);
        }
      });
    });

    root.querySelectorAll('[data-drive-card]').forEach((card) => {
      card.addEventListener('click', (event) => handleActivation(event, card, card.dataset.detailUrl));
      card.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          handleActivation(event, card, card.dataset.detailUrl);
        }
      });
      const anchor = card.querySelector('a');
      if (anchor) {
        anchor.addEventListener('click', (event) => handleActivation(event, card, card.dataset.detailUrl));
      }
    });

    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && drawerHost.classList.contains('is-open')) {
        closeDrawer();
      }
    });
  }

  function initUploadModal() {
    const modal = document.querySelector('[data-drive-upload-modal]');
    if (!modal) {
      return;
    }

    const docTypeSelect = modal.querySelector('[data-drive-doc-type]');
    if (!docTypeSelect) {
      return;
    }

    const hint = modal.querySelector('[data-drive-mtr-project-hint]');
    const hasProject = modal.dataset.driveHasProject !== 'false';
    const disableMTR = !hasProject;
    const mtrOptions = docTypeSelect.querySelectorAll('option[value="MTR"]');

    if (disableMTR) {
      mtrOptions.forEach((option) => {
        option.disabled = true;
      });
      if (docTypeSelect.value === 'MTR') {
        docTypeSelect.value = '';
      }
      if (hint) {
        hint.classList.remove('d-none');
      }
    } else {
      mtrOptions.forEach((option) => {
        option.disabled = false;
      });
      if (hint) {
        hint.classList.add('d-none');
      }
    }

    if (disableMTR) {
      docTypeSelect.addEventListener('change', () => {
        if (docTypeSelect.value === 'MTR') {
          docTypeSelect.value = '';
        }
      });
    }
  }

  function initInlineEditing() {
    const gridContainer = document.getElementById('drive-documents-grid-container');
    const grid = document.getElementById('drive-documents-grid');
    const toggleButton = document.getElementById('drive-documents-edit-toggle');
    const editingIndicator = document.getElementById('drive-documents-editing-indicator');
    const csrfInput = document.querySelector('#drive-documents-inline-csrf input[name=csrfmiddlewaretoken]');
    const csrfToken = csrfInput ? csrfInput.value : '';

    if (!gridContainer || !grid || !toggleButton) {
      return;
    }

    if (editingIndicator) {
      editingIndicator.style.display = 'none';
    }

    let activeCell = null;
    let activeInput = null;
    let originalValue = '';

    function isEditing() {
      return gridContainer.classList.contains('is-editing');
    }

    function closeActiveInput(options = {}) {
      if (!activeCell || !activeInput) {
        return;
      }

      const cell = activeCell;
      const displayEl = cell.querySelector('.cell-display');
      if (!displayEl) {
        cell.classList.remove('is-editing', 'has-error', 'saving');
        const orphanFeedback = cell.querySelector('.cell-feedback');
        if (orphanFeedback) {
          orphanFeedback.remove();
        }
        activeInput.remove();
        activeCell = null;
        activeInput = null;
        originalValue = '';
        return;
      }

      const { revert = false, value = null, display = null } = options;
      const nextValue = revert
        ? originalValue
        : value !== null
          ? value
          : activeInput.value;
      const nextDisplay = display !== null ? display : (nextValue || '—');

      displayEl.textContent = nextDisplay;
      cell.dataset.value = nextValue;

      cell.classList.remove('is-editing', 'has-error', 'saving');
      const feedback = cell.querySelector('.cell-feedback');
      if (feedback) {
        feedback.remove();
      }
      activeInput.remove();
      activeCell = null;
      activeInput = null;
      originalValue = '';
    }

    function showError(cell, message) {
      cell.classList.add('has-error');
      let feedback = cell.querySelector('.cell-feedback');
      if (!feedback) {
        feedback = document.createElement('div');
        feedback.className = 'cell-feedback drive-inline-feedback';
        cell.appendChild(feedback);
      }
      feedback.textContent = message;
    }

    function clearError(cell) {
      cell.classList.remove('has-error');
      const feedback = cell.querySelector('.cell-feedback');
      if (feedback) {
        feedback.remove();
      }
    }

    function activateCell(cell) {
      if (activeCell === cell) {
        return;
      }

      if (activeInput) {
        activeInput.blur();
      }

      const currentValue = cell.dataset.value || '';
      const input = document.createElement('input');
      input.type = 'text';
      input.value = currentValue;
      input.className = 'form-control form-control-sm inline-editor';
      input.setAttribute('aria-label', (cell.dataset.field || '') + ' editor');
      if (cell.dataset.field === 'doc_type') {
        input.setAttribute('list', 'drive-doc-type-options');
        input.setAttribute('autocomplete', 'off');
      }

      clearError(cell);
      cell.classList.add('is-editing');
      const display = cell.querySelector('.cell-display');
      if (display) {
        display.textContent = '';
      }
      cell.appendChild(input);
      input.focus();
      input.select();

      originalValue = currentValue;
      activeCell = cell;
      activeInput = input;

      input.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') {
          event.preventDefault();
          input.blur();
        } else if (event.key === 'Escape') {
          event.preventDefault();
          closeActiveInput({ revert: true });
        }
      });

      input.addEventListener('blur', () => {
        commitChange(cell, input);
      });
    }

    function validate(field, value) {
      if ((field === 'number' || field === 'name') && !value.trim()) {
        return 'This value is required.';
      }
      if (field === 'doc_type' && value && value.length > 16) {
        return 'Document type is too long.';
      }
      return '';
    }

    function commitChange(cell, input) {
      const field = cell.dataset.field;
      if (!field) {
        closeActiveInput({ revert: true });
        return;
      }
      const value = input.value || '';
      const error = validate(field, value);
      if (error) {
        showError(cell, error);
        closeActiveInput({ revert: true });
        return;
      }
      const display = cell.querySelector('.cell-display');
      if (display) {
        display.textContent = value || '—';
      }
      cell.dataset.value = value;
      if (!cell.dataset.updateUrl) {
        const row = cell.closest('tr[data-update-url]');
        if (row) {
          cell.dataset.updateUrl = row.dataset.updateUrl;
        }
      }
      const endpoint = cell.dataset.updateUrl;
      if (!endpoint) {
        closeActiveInput();
        return;
      }
      cell.classList.add('saving');
      clearError(cell);

      fetch(endpoint, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': csrfToken,
          'X-Requested-With': 'XMLHttpRequest',
        },
        body: JSON.stringify({ field, value }),
        credentials: 'same-origin',
      })
        .then((resp) => resp.json())
        .then((data) => {
          if (data.error) {
            showError(cell, data.error);
            closeActiveInput({ revert: true });
            return;
          }
          closeActiveInput({ value: data.value, display: data.display });
        })
        .catch((err) => {
          console.error(err);
          showError(cell, 'Unable to save.');
          closeActiveInput({ revert: true });
        })
        .finally(() => {
          cell.classList.remove('saving');
        });
    }

    toggleButton.addEventListener('click', (event) => {
      event.preventDefault();
      const editing = isEditing();
      if (editing) {
        gridContainer.classList.remove('is-editing');
        toggleButton.setAttribute('aria-pressed', 'false');
        toggleButton.textContent = 'Edit';
        if (editingIndicator) {
          editingIndicator.style.display = 'none';
        }
      } else {
        gridContainer.classList.add('is-editing');
        toggleButton.setAttribute('aria-pressed', 'true');
        toggleButton.textContent = 'Done';
        if (editingIndicator) {
          editingIndicator.style.display = 'inline-flex';
        }
      }
    });

    grid.addEventListener('click', (event) => {
      const cell = event.target.closest('td[data-editable="true"]');
      if (!cell || !isEditing()) {
        return;
      }
      event.preventDefault();
      activateCell(cell);
    });
  }

  ready(() => {
    const shell = document.querySelector('[data-drive-shell]');
    if (!shell) {
      initUploadModal();
      return;
    }
    initViewToggle(shell);
    initDrawer(shell);
    initInlineEditing();
    initUploadModal();
  });
})();
