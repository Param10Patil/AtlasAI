const form = document.querySelector('#incident-form');
const status = document.querySelector('#status');
form?.addEventListener('submit', (event) => {
  event.preventDefault();
  status.textContent = 'The investigation API will be connected in the API/UI implementation pass.';
});
