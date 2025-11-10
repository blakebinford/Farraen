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
