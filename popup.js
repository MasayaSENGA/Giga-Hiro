document.addEventListener('DOMContentLoaded', function() {
  const lifetimeSelect = document.getElementById('lifetimeSelect');
  const saveButton = document.getElementById('saveButton');
  const statusDiv = document.getElementById('status');

  chrome.storage.sync.get('gigafileLifetime', function(data) {
    if (data.gigafileLifetime) {
      lifetimeSelect.value = data.gigafileLifetime;
    } else {
      lifetimeSelect.value = '5';
    }
  });

  saveButton.addEventListener('click', function() {
    const selectedValue = lifetimeSelect.value;
    chrome.storage.sync.set({ 'gigafileLifetime': selectedValue }, function() {
      statusDiv.textContent = '設定を保存しました！';
      setTimeout(function() {
        statusDiv.textContent = '';
      }, 3000);
    });
  });
});