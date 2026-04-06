/**
 * Предпросмотр видео: загрузка только по клику.
 *
 * Если кнопка имеет data-s3-url — используем прямой S3 URL (без AJAX к Django).
 * Если нет — резолвим через Django AJAX (fallback).
 * Видео НЕ предзагружается — не забиваем канал и gunicorn workers.
 *
 * Требования к HTML:
 *   - Кнопки: .js-video-preview[data-video-url][data-lead-id]
 *   - Опционально: data-s3-url (прямой S3 URL, встроен в шаблон)
 *   - Модалка: #videoPreviewModal с <video id="videoPreviewPlayer">
 *   - Опционально: #videoLeadId (span), #videoDownloadLink (a)
 */
(function () {
  'use strict';

  var modal = document.getElementById('videoPreviewModal');
  var player = document.getElementById('videoPreviewPlayer');
  if (!modal || !player) return;

  var leadIdSpan = document.getElementById('videoLeadId');
  var downloadLink = document.getElementById('videoDownloadLink');
  var buttons = document.querySelectorAll('.js-video-preview');
  if (!buttons.length) return;

  // ---------- CSS ----------

  var style = document.createElement('style');
  style.textContent = '@keyframes vpSpin{to{transform:rotate(360deg)}}';
  document.head.appendChild(style);

  // ---------- спиннер ----------

  var spinnerOverlay = document.createElement('div');
  spinnerOverlay.style.cssText =
    'position:absolute;inset:0;display:flex;align-items:center;justify-content:center;' +
    'background:rgba(0,0,0,.6);z-index:10;pointer-events:none;';
  spinnerOverlay.innerHTML =
    '<div style="width:48px;height:48px;border:4px solid rgba(255,255,255,.2);' +
    'border-top-color:#fff;border-radius:50%;animation:vpSpin .8s linear infinite;"></div>';

  var modalBody = player.parentElement;
  if (modalBody) modalBody.style.position = 'relative';

  function showSpinner() {
    if (modalBody && !modalBody.contains(spinnerOverlay)) {
      modalBody.appendChild(spinnerOverlay);
    }
  }

  function hideSpinner() {
    if (spinnerOverlay.parentElement) spinnerOverlay.remove();
  }

  // ---------- резолв S3-URL (fallback для кнопок без data-s3-url) ----------

  var urlCache = {};

  function resolveUrl(btn, cb) {
    // Приоритет: data-s3-url (прямой URL, без AJAX)
    var s3url = btn.getAttribute('data-s3-url');
    if (s3url) return cb(s3url);

    // Fallback: AJAX к Django
    var djangoUrl = btn.getAttribute('data-video-url');
    if (urlCache[djangoUrl]) return cb(urlCache[djangoUrl]);

    fetch(djangoUrl, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var url = data.url || djangoUrl;
        urlCache[djangoUrl] = url;
        cb(url);
      })
      .catch(function () {
        urlCache[djangoUrl] = djangoUrl;
        cb(djangoUrl);
      });
  }

  // ---------- открытие видео ----------

  function openVideo(url, leadId) {
    if (leadIdSpan) leadIdSpan.textContent = leadId || '';
    if (downloadLink) downloadLink.href = url;

    showSpinner();
    player.preload = 'auto';
    player.src = url;
    player.autoplay = true;

    var bsModal = bootstrap.Modal.getOrCreateInstance(modal);
    bsModal.show();

    var playPromise = player.play();
    if (playPromise && playPromise.catch) {
      playPromise.catch(function () {});
    }
  }

  player.addEventListener('canplay', function () {
    hideSpinner();
    var p = player.play();
    if (p && p.catch) p.catch(function () {});
  });
  player.addEventListener('error', hideSpinner);

  // ---------- подписка на кнопки ----------

  buttons.forEach(function (btn) {
    btn.addEventListener('click', function () {
      var leadId = btn.getAttribute('data-lead-id') || '';
      resolveUrl(btn, function (url) {
        openVideo(url, leadId);
      });
    });
  });

  // ---------- очистка при закрытии ----------

  modal.addEventListener('hidden.bs.modal', function () {
    player.pause();
    player.removeAttribute('src');
    player.autoplay = false;
    player.load();
    hideSpinner();
  });
})();
