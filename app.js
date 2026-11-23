'use strict';
document.addEventListener('DOMContentLoaded',()=>{
  const menu=document.getElementById('menu-toggle');if(menu)menu.addEventListener('click',()=>document.getElementById('sidebar')?.classList.toggle('open'));
  const kind=document.getElementById('source-kind'),file=document.getElementById('source-file'),period=document.getElementById('reference-period');
  const caption=document.getElementById('file-caption');
  if(kind&&file){const upd=()=>{const pdf=kind.value==='branch_pdf';file.accept=pdf?'.pdf':'.xlsx';if(period){period.placeholder=kind.value==='targets'?'2026':pdf?'2026-10':'2026-10'}};kind.addEventListener('change',upd);upd();file.addEventListener('change',()=>{if(file.files.length&&caption)caption.textContent=file.files[0].name+' · '+(file.files[0].size/1048576).toFixed(1)+' MB'})}
  document.querySelectorAll('form[data-confirm]').forEach(el=>el.addEventListener('submit',ev=>{if(!window.confirm(el.dataset.confirm||'هل أنت متأكد؟'))ev.preventDefault()}));
  document.querySelectorAll('[data-toggle]').forEach(btn=>btn.addEventListener('click',()=>{const row=document.getElementById(btn.dataset.toggle);if(row)row.hidden=!row.hidden}));
  document.querySelectorAll('input[data-filter-target]').forEach(input=>input.addEventListener('input',()=>{const table=document.querySelector(input.dataset.filterTarget);if(!table)return;table.querySelectorAll('tbody tr').forEach(tr=>{tr.hidden=!tr.innerText.toLowerCase().includes(input.value.trim().toLowerCase())})}));
  document.querySelectorAll('[data-print]').forEach(btn=>btn.addEventListener('click',()=>window.print()));
  document.querySelectorAll('form[data-loading-form]').forEach(form=>form.addEventListener('submit',()=>{const btn=form.querySelector('[type=submit]');if(btn){btn.textContent='جاري رفع الملف والتحقق منه…';btn.disabled=true}}));
});

// Installable Android PWA; all data endpoints remain online-only.
if ('serviceWorker' in navigator && (location.protocol === 'https:' || location.hostname === 'localhost')) {
  window.addEventListener('load', () => navigator.serviceWorker.register('/sw.js').catch(() => {}));
}
// Mobile menu closes on navigation and when tapping outside of the sidebar.
document.addEventListener('DOMContentLoaded', () => {
  const sidebar=document.getElementById('sidebar');
  if(!sidebar)return;
  sidebar.querySelectorAll('a').forEach(a=>a.addEventListener('click',()=>sidebar.classList.remove('open')));
  document.addEventListener('click',event=>{
    if(!sidebar.classList.contains('open'))return;
    if(!sidebar.contains(event.target) && !event.target.closest('#menu-toggle'))sidebar.classList.remove('open');
  });
});
