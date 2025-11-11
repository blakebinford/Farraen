(function () {
  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('[data-modal-open]')
      .forEach(trigger => {
        const target = document.getElementById(trigger.dataset.modalOpen);
        trigger.addEventListener('click', () => {
          target?.setAttribute('aria-hidden', 'false');
          target?.querySelector('[data-modal-close]')?.focus();
        });
      });

    document.querySelectorAll('[data-modal-close]')
      .forEach(button => {
        button.addEventListener('click', () => {
          const backdrop = button.closest('.modal-backdrop');
          backdrop?.setAttribute('aria-hidden', 'true');
        });
      });

    document.querySelectorAll('.modal-backdrop')
      .forEach(backdrop => {
        backdrop.addEventListener('click', (event) => {
          if (event.target === backdrop) {
            backdrop.setAttribute('aria-hidden', 'true');
          }
        });
      });

    document.querySelectorAll('.tabs')
      .forEach(tabList => {
        const tabs = () => tabList.querySelectorAll('.tab');
        const activateTab = (trigger) => {
          const panelId = trigger.getAttribute('aria-controls');
          if (!panelId) return;

          tabs().forEach(tab => {
            const active = tab === trigger;
            tab.classList.toggle('tab--active', active);
            tab.setAttribute('aria-selected', active ? 'true' : 'false');
            if (active) {
              tab.focus();
            }
          });

          const panelsWrapper = tabList.nextElementSibling;
          if (!panelsWrapper) return;
          panelsWrapper.querySelectorAll('.tabs__panel').forEach(panel => {
            if (panel.id === panelId) {
              panel.removeAttribute('hidden');
            } else {
              panel.setAttribute('hidden', '');
            }
          });
        };

        tabList.addEventListener('click', (event) => {
          const trigger = event.target.closest('.tab');
          if (!trigger) return;
          activateTab(trigger);
        });

        tabList.addEventListener('keydown', (event) => {
          const current = tabList.querySelector('.tab--active');
          if (!current) return;
          const tabArray = Array.from(tabs());
          const index = tabArray.indexOf(current);
          if (index === -1) return;

          if (['ArrowRight', 'ArrowLeft', 'Home', 'End'].includes(event.key)) {
            event.preventDefault();
          }

          if (event.key === 'ArrowRight') {
            const next = tabArray[(index + 1) % tabArray.length];
            activateTab(next);
          } else if (event.key === 'ArrowLeft') {
            const prev = tabArray[(index - 1 + tabArray.length) % tabArray.length];
            activateTab(prev);
          } else if (event.key === 'Home') {
            activateTab(tabArray[0]);
          } else if (event.key === 'End') {
            activateTab(tabArray[tabArray.length - 1]);
          }
        });
      });

    const toastRegion = document.querySelector('[data-toast-region]');
    document.querySelectorAll('[data-demo-toast]')
      .forEach(button => {
        button.addEventListener('click', () => {
          const toast = document.createElement('div');
          toast.className = 'toast';
          toast.innerHTML = `
            <div class="toast__title">${button.dataset.toastTitle || 'Notification'}</div>
            <div class="toast__body">${button.dataset.toastMessage || 'Action completed successfully.'}</div>
          `;
          toastRegion?.appendChild(toast);
          setTimeout(() => {
            toast.classList.add('toast--visible');
          }, 10);
          setTimeout(() => {
            toast.classList.remove('toast--visible');
            toast.addEventListener('transitionend', () => toast.remove(), { once: true });
          }, 4000);
        });
      });
  });
})();
