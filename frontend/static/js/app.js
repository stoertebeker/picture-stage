// Dark-Mode: System-Default + localStorage Override
(function() {
  const html = document.documentElement;
  const stored = localStorage.getItem('theme');
  if (stored === 'dark' || (!stored && window.matchMedia('(prefers-color-scheme: dark)').matches)) {
    html.classList.add('dark');
  } else {
    html.classList.remove('dark');
  }
})();

function toggleDarkMode() {
  const html = document.documentElement;
  html.classList.toggle('dark');
  localStorage.setItem('theme', html.classList.contains('dark') ? 'dark' : 'light');
}

// HTMX Upload Progress (if htmx is loaded)
if (typeof htmx !== 'undefined') {
  htmx.on('htmx:xhr:progress', function(evt) {
    var progress = document.getElementById('upload-progress');
    if (progress) {
      progress.setAttribute('value', evt.detail.loaded / evt.detail.total * 100);
    }
  });
}
