'use strict';
// HermesVPN Connect for Windows — UI. Mirrors the Android app screen by screen.

const TELEGRAM_CHANNEL_URL = 'https://t.me/hermevpn';
const BOT_CHAT_URL = 'https://t.me/YourSalesBot';
const PRIVACY_URL = 'https://app.example.com/';
// Download links shown in the "devices & sharing" section (Android = smart landing page: opens the app or downloads it).
const LINK_ANDROID = 'https://app.example.com/open/';
const LINK_WINDOWS = 'https://app.example.com/app/HermesVPN-Connect-Setup.exe';
const ANDROID_SVG = '<svg viewBox="0 0 24 24" width="26" height="26"><path fill="#3DDC84" d="M17.6 9.48l1.84-3.18a.38.38 0 00-.66-.38l-1.86 3.22a11.4 11.4 0 00-9.84 0L5.22 5.92a.38.38 0 00-.66.38L6.4 9.48A10.8 10.8 0 001 18h22a10.8 10.8 0 00-5.4-8.52zM7 15.25A1.25 1.25 0 118.25 14 1.25 1.25 0 017 15.25zm10 0A1.25 1.25 0 1118.25 14 1.25 1.25 0 0117 15.25z"/></svg>';
const WINDOWS_SVG = '<svg viewBox="0 0 24 24" width="24" height="24"><path fill="#00A4EF" d="M3 5.5l7.5-1v7H3zm8.5-1.2L21 3v8.5h-9.5zM3 12.5h7.5v7L3 18.5zm8.5 0H21V21l-9.5-1.3z"/></svg>';
function shareText() { return t('dev_share_text', LINK_ANDROID, LINK_WINDOWS, BOT_CHAT_URL); }
function promoCard() {
  const c = el(`<div class="promo"><div class="promo-glow"></div>
    <div class="promo-icons"><span class="pi">${ANDROID_SVG}</span><span class="pi">${WINDOWS_SVG}</span></div>
    <div class="promo-title">${t('dev_promo_title')}</div><div class="promo-sub">${t('dev_promo_sub')}</div>
    <div class="promo-btns"><button class="pbtn main">${ANDROID_SVG}<span>${t('dev_get_android')}</span></button><button class="pbtn">${icon('ic_share')}<span>${t('dev_share')}</span></button></div></div>`);
  c.onclick = () => pushScreen(DevicesScreen());
  return c;
}

// Strings that exist only in the Windows build (everything else comes from the Android strings.xml).
const WIN = {
  fa: {
    win_section: 'ویندوز',
    win_mode_title: 'حالت اتصال ویندوز',
    win_mode_tun: 'TUN — همه‌ی برنامه‌ها (پیشنهادی)',
    win_mode_proxy: 'پروکسی سیستم — مرورگرها و برنامه‌های سازگار',
    win_mode_hint: 'در حالت TUN کل ترافیک ویندوز (تلگرام، بازی‌ها، تماس صوتی و تصویری) از تونل رد می‌شود. پروکسی سیستم فقط برای وقتی است که TUN روی سیستم کار نکند.',
    win_autostart: 'اجرا همراه با ویندوز',
    win_autoconnect: 'اتصال خودکار هنگام باز شدن برنامه',
    win_tun_fallback: 'حالت TUN روی این سیستم اجرا نشد؛ با پروکسی سیستم وصل شدی.',
    win_log: 'گزارش اتصال (برای پشتیبانی)',
    win_log_copy: 'کپی گزارش',
    win_admin: 'برنامه بدون دسترسی Administrator اجرا شده؛ حالت TUN در دسترس نیست و از پروکسی سیستم استفاده می‌شود.',
    win_split_hint: 'نام فایل اجرایی برنامه‌ها را انتخاب کن (مثلاً chrome.exe). برنامه‌های در حال اجرا در لیست هستند؛ برای بقیه «افزودن برنامه» را بزن.',
    win_split_add: 'افزودن برنامه (فایل exe)',
    win_split_selected: 'انتخاب‌شده‌ها',
    win_split_running: 'برنامه‌های در حال اجرا',
    win_split_proxy_only: 'جداسازی تونلینگ فقط در حالت TUN کار می‌کند.',
    win_qr_pick: 'انتخاب عکس QR',
    win_qr_paste: 'چسباندن عکس QR از کلیپ‌بورد',
    win_qr_no_image: 'عکسی در کلیپ‌بورد نیست',
    win_activation: 'لینک فعال‌سازی ربات را هم می‌توانی همین‌جا بچسبانی.',
    win_copy_link: 'کپی لینک',
    win_share_title: 'اشتراک‌گذاری کانفیگ',
    win_cancel: 'انصراف',
    win_ok: 'باشه',
    win_reconnect_hint: 'برای اعمال تغییرات یک‌بار قطع و وصل کن.',
    win_update_none_win: 'نسخه‌ی ویندوز {1} آخرین نسخه است.',
    win_restart_admin: 'اجرای دوباره با دسترسی Administrator',
    win_quit: 'خروج کامل',
    upd_banner: 'نسخه‌ی جدید {1} آمد',
    upd_banner_btn: 'به‌روزرسانی',
    upd_card_title: 'نسخه‌ی جدید {1} آماده است',
    upd_card_sub: 'نسخه‌ی فعلی تو {1} است — برای نصب نسخه‌ی جدید بزن.',
    sub_chip: 'لینک ساب',
    sub_menu: 'افزودن لینک ساب (Subscription)',
    sub_title: 'افزودن لینک ساب',
    sub_hint: 'لینک اشتراک (sub) را اینجا بچسبان — لینک ساب HermesVPN یا هر سرویس دیگر. همه‌ی کانفیگ‌های داخلش وارد می‌شوند و با «به‌روزرسانی» تازه می‌شوند.',
    sub_link_ph: 'https://…/sub/…',
    sub_name_ph: 'نام (اختیاری)',
    dev_promo_title: 'HermesVPN روی همه‌ی دستگاه‌هات',
    dev_promo_sub: 'با همین یک اکانت، روی گوشی اندروید و کامپیوتر ویندوز وصل شو.',
    dev_get_android: 'دریافت اپ اندروید',
    dev_share: 'معرفی به دوستان',
    dev_screen: 'دستگاه‌ها و اشتراک‌گذاری',
    dev_android_title: 'اپ اندروید',
    dev_android_hint: 'با دوربین گوشی این کد را اسکن کن تا اپ دانلود شود؛ بعد با همین یوزرنیم و پسورد وارد شو — کانفیگ‌ها خودکار می‌آیند.',
    dev_windows_title: 'ویندوز',
    dev_windows_hint: 'روی همین کامپیوتر نصب است. برای کامپیوتر دیگر، لینک فایل نصبی را بفرست.',
    dev_installed: 'نصب‌شده · نسخه‌ی {1}',
    dev_open: 'باز کردن لینک',
    dev_copy: 'کپی لینک',
    dev_share_title: 'معرفی HermesVPN به دوستان',
    dev_share_hint: 'لینک دانلود را برای دوستانت بفرست — تست رایگان هم دارند.',
    dev_share_telegram: 'تلگرام',
    dev_share_whatsapp: 'واتس‌اپ',
    dev_share_copy: 'کپی متن',
    dev_share_text: 'من از HermesVPN استفاده می‌کنم — سریع، امن و بدون قطعی 🚀\n\n📱 اندروید: {1}\n💻 ویندوز: {2}\n\n🛒 خرید و تست رایگان: {3}',
    dev_pill: 'HermesVPN روی گوشی هم — اسکن کن و نصب کن',
    dev_same_account: 'یک اکانت برای همه',
  },
  en: {
    win_section: 'Windows',
    win_mode_title: 'Windows connection mode',
    win_mode_tun: 'TUN — all apps (recommended)',
    win_mode_proxy: 'System proxy — browsers and proxy-aware apps',
    win_mode_hint: 'TUN sends all Windows traffic (Telegram, games, voice and video calls) through the tunnel. Use system proxy only if TUN does not work on this PC.',
    win_autostart: 'Start with Windows',
    win_autoconnect: 'Connect automatically when the app opens',
    win_tun_fallback: 'TUN could not start on this PC; connected as system proxy instead.',
    win_log: 'Connection log (for support)',
    win_log_copy: 'Copy log',
    win_admin: 'The app is not running as Administrator, so TUN is unavailable and system proxy is used.',
    win_split_hint: 'Pick programs by their executable name (e.g. chrome.exe). Running programs are listed; use “Add program” for others.',
    win_split_add: 'Add program (.exe file)',
    win_split_selected: 'Selected',
    win_split_running: 'Running programs',
    win_split_proxy_only: 'Split tunneling works in TUN mode only.',
    win_qr_pick: 'Choose QR image',
    win_qr_paste: 'Paste QR image from clipboard',
    win_qr_no_image: 'No image in clipboard',
    win_activation: 'You can paste the bot’s activation link here too.',
    win_copy_link: 'Copy link',
    win_share_title: 'Share config',
    win_cancel: 'Cancel',
    win_ok: 'OK',
    win_reconnect_hint: 'Reconnect once to apply the changes.',
    win_update_none_win: 'Windows version {1} is the latest.',
    win_restart_admin: 'Restart as Administrator',
    win_quit: 'Quit',
    upd_banner: 'Version {1} is available',
    upd_banner_btn: 'Update',
    upd_card_title: 'Version {1} is ready',
    upd_card_sub: 'You have {1} — tap to install the new version.',
    sub_chip: 'Sub link',
    sub_menu: 'Add subscription (sub) link',
    sub_title: 'Add subscription link',
    sub_hint: 'Paste a subscription (sub) link — HermesVPN or any other provider. All its configs are imported and refreshed with “Update”.',
    sub_link_ph: 'https://…/sub/…',
    sub_name_ph: 'Name (optional)',
    dev_promo_title: 'HermesVPN on all your devices',
    dev_promo_sub: 'One account — connect your Android phone and Windows PC.',
    dev_get_android: 'Get the Android app',
    dev_share: 'Invite friends',
    dev_screen: 'Devices & sharing',
    dev_android_title: 'Android app',
    dev_android_hint: 'Scan this code with your phone camera to download the app, then sign in with the same username and password — your configs come along.',
    dev_windows_title: 'Windows',
    dev_windows_hint: 'Installed on this PC. For another computer, send the installer link.',
    dev_installed: 'Installed · version {1}',
    dev_open: 'Open link',
    dev_copy: 'Copy link',
    dev_share_title: 'Share HermesVPN with friends',
    dev_share_hint: 'Send them the download link — there is a free trial too.',
    dev_share_telegram: 'Telegram',
    dev_share_whatsapp: 'WhatsApp',
    dev_share_copy: 'Copy text',
    dev_share_text: 'I use HermesVPN — fast, private and always on 🚀\n\n📱 Android: {1}\n💻 Windows: {2}\n\n🛒 Buy or try free: {3}',
    dev_pill: 'HermesVPN on your phone too — scan to install',
    dev_same_account: 'One account for all',
    update_channel: 'Updates', update_notif_title: 'Version {1} is ready', update_notif_body: 'Tap to install', update_checking: 'Checking…',
    update_available_title: 'Version {1} is ready', update_default_notes: 'Speed improvements and bug fixes.', update_none_title: 'You are up to date',
    update_none_body: 'Version {1} is the latest.', update_download: 'Download & install', update_install: 'Install', update_downloading: 'Downloading…',
    update_progress: '{1} of {2} MB', update_installing: 'Installing…', update_failed: 'Download failed.', update_retry: 'Retry', update_later: 'Later',
    update_close: 'Close', update_check_menu: 'Check for updates',
  },
};

let STR = { en: {}, fa: {} };
let LANG = 'fa';
let ST = { prefs: {}, running: false };
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

function t(key, ...args) {
  let s = (WIN[LANG] && WIN[LANG][key]) ?? (STR[LANG] && STR[LANG][key]) ?? (LANG === 'fa' ? STR.en[key] : undefined) ?? WIN.en[key] ?? STR.en[key] ?? key;
  args.forEach((a, i) => { s = s.split(`{${i + 1}}`).join(String(a)); });
  return s;
}
function esc(s) { return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
function icon(name, cls = '') { return `<span class="icon ${cls}" style="--i:url(assets/icons/${name}.svg)"></span>`; }
function el(html) { const d = document.createElement('div'); d.innerHTML = html.trim(); return d.firstElementChild; }
function fa(n) { return LANG === 'fa' ? String(n).replace(/\d/g, d => '۰۱۲۳۴۵۶۷۸۹'[d]) : String(n); }
function fmtNum(n) { return Number(n || 0).toLocaleString('en-US'); }
function fmtDate(ms, withTime) {
  const d = new Date(ms); const p = x => String(x).padStart(2, '0');
  let s = `${d.getFullYear()}/${p(d.getMonth() + 1)}/${p(d.getDate())}`;
  if (withTime) s += ` ${p(d.getHours())}:${p(d.getMinutes())}`;
  return s;
}
const GERMANY_FLAG = '<svg width="22" height="15" viewBox="0 0 5 3" style="vertical-align:-2px;border-radius:2px;margin-inline-end:8px"><rect width="5" height="1" fill="#000"/><rect y="1" width="5" height="1" fill="#D00"/><rect y="2" width="5" height="1" fill="#FFCE00"/></svg>';

// ---------- backend ----------
const api = new Proxy({}, {
  get: (_, name) => async (...args) => {
    try { return await window.go.main.App[name](...args); }
    catch (e) { console.error(name, e); return null; }
  },
});
const pref = (k, d) => (ST.prefs && ST.prefs[k] !== undefined && ST.prefs[k] !== null) ? ST.prefs[k] : d;
async function setPref(k, v) { ST.prefs[k] = v; await api.SetPref(k, v); }
async function refreshState() { const s = await api.GetState(); if (s) ST = s; return ST; }

// ---------- feedback ----------
let toastTimer;
function toast(msg, long) {
  $$('.toast').forEach(x => x.remove());
  const n = el(`<div class="toast">${esc(msg)}</div>`);
  document.body.appendChild(n);
  clearTimeout(toastTimer); toastTimer = setTimeout(() => n.remove(), long ? 4200 : 2400);
}
function dialog({ title, body = '', buttons = [], onOpen }) {
  return new Promise(resolve => {
    const ov = el(`<div class="overlay"><div class="dialog">${title ? `<h2>${esc(title)}</h2>` : ''}<div class="dbody"></div><div class="btns"></div></div></div>`);
    const dbody = $('.dbody', ov);
    if (typeof body === 'string') dbody.innerHTML = body; else dbody.appendChild(body);
    const close = (v) => { ov.remove(); document.removeEventListener('keydown', onKey); resolve(v); };
    const onKey = (e) => { if (e.key === 'Escape') close(null); if (e.key === 'Enter' && e.target.tagName !== 'TEXTAREA') { const p = buttons.find(b => b.primary); if (p) $$('.btns .btn', ov)[buttons.indexOf(p)].click(); } };
    document.addEventListener('keydown', onKey);
    for (const b of buttons) {
      const n = el(`<button class="btn ${b.primary ? '' : 'secondary'} ${b.cls || ''}">${esc(b.label)}</button>`);
      n.onclick = async () => { const r = b.onClick ? await b.onClick(dbody) : b.value; if (r !== false) close(r === undefined ? b.value : r); };
      $('.btns', ov).appendChild(n);
    }
    ov.addEventListener('mousedown', e => { if (e.target === ov) close(null); });
    document.body.appendChild(ov);
    if (onOpen) onOpen(dbody);
    const f = $('input,textarea', dbody); if (f) setTimeout(() => f.focus(), 30);
  });
}
const confirmDlg = (title, msg, okLabel) => dialog({ title, body: `<div class="msg">${esc(msg)}</div>`, buttons: [{ label: t('win_cancel'), value: false }, { label: okLabel || t('win_ok'), primary: true, value: true }] });

function popupMenu(anchor, items) {
  const ov = el('<div class="overlay clear"></div>');
  const m = el('<div class="menu"></div>');
  for (const it of items) {
    if (it === '-') { m.appendChild(el('<div class="msep"></div>')); continue; }
    const n = el(`<div class="mi">${it.icon ? icon(it.icon) : ''}<span>${esc(it.label)}</span></div>`);
    n.onclick = () => { ov.remove(); it.onClick && it.onClick(n); };
    m.appendChild(n);
  }
  ov.appendChild(m);
  ov.addEventListener('mousedown', e => { if (e.target === ov) ov.remove(); });
  document.body.appendChild(ov);
  const r = anchor.getBoundingClientRect(), mw = m.offsetWidth, mh = m.offsetHeight;
  let x = document.dir === 'rtl' ? r.left : r.right - mw; x = Math.max(8, Math.min(x, innerWidth - mw - 8));
  let y = r.bottom + 4; if (y + mh > innerHeight - 8) y = Math.max(8, r.top - mh - 4);
  m.style.left = x + 'px'; m.style.top = y + 'px';
}
function pickFile(accept, asText) {
  return new Promise(resolve => {
    const inp = document.createElement('input'); inp.type = 'file'; inp.accept = accept || '';
    inp.onchange = () => {
      const f = inp.files[0]; if (!f) return resolve(null);
      const rd = new FileReader();
      rd.onload = () => resolve({ name: f.name, data: rd.result });
      if (asText) rd.readAsText(f); else rd.readAsDataURL(f);
    };
    inp.click();
  });
}
function loadImage(src) { return new Promise((res, rej) => { const i = new Image(); i.onload = () => res(i); i.onerror = rej; i.src = src; }); }
async function decodeQR(dataUrl) {
  const img = await loadImage(dataUrl);
  for (const maxSide of [1600, 900, 2400]) {
    const s = Math.min(1, maxSide / Math.max(img.width, img.height));
    const c = document.createElement('canvas'); c.width = Math.round(img.width * s); c.height = Math.round(img.height * s);
    const g = c.getContext('2d'); g.drawImage(img, 0, 0, c.width, c.height);
    const d = g.getImageData(0, 0, c.width, c.height);
    const r = jsQR(d.data, c.width, c.height, { inversionAttempts: 'attemptBoth' });
    if (r && r.data) return r.data;
  }
  return null;
}
async function scaleImage(dataUrl, size, square) {
  const img = await loadImage(dataUrl);
  const c = document.createElement('canvas');
  if (square) {
    c.width = c.height = size;
    const s = Math.min(img.width, img.height);
    c.getContext('2d').drawImage(img, (img.width - s) / 2, (img.height - s) / 2, s, s, 0, 0, size, size);
  } else {
    const k = Math.min(1, size / Math.max(img.width, img.height));
    c.width = Math.round(img.width * k); c.height = Math.round(img.height * k);
    c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
  }
  return c.toDataURL('image/jpeg', 0.8).split(',')[1];
}
function qrDataURL(text) {
  const q = qrcode(0, 'L'); q.addData(unescape(encodeURIComponent(text))); q.make();
  return q.createDataURL(6, 2);
}

// ---------- theme & language ----------
function applyTheme() {
  const mode = pref('theme_mode', 'system');
  const dark = mode === 'dark' || (mode !== 'light' && matchMedia('(prefers-color-scheme: dark)').matches);
  document.documentElement.classList.toggle('dark', dark);
}
function applyDesk() { document.documentElement.classList.toggle('wide', innerWidth >= 820); }
window.addEventListener('resize', applyDesk);
function applyLang() {
  const p = pref('app_language', null);
  if (p === 'en' || p === 'fa') LANG = p;
  else if (p === '') LANG = (navigator.language || 'en').toLowerCase().startsWith('fa') ? 'fa' : 'en';
  else LANG = 'fa'; // first run: Persian, like the customers' phones
  document.documentElement.lang = LANG;
  document.documentElement.dir = LANG === 'fa' ? 'rtl' : 'ltr';
}
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', applyTheme);

// ---------- navigation ----------
const app = () => $('#app');
const stack = [];
let currentTab = 'home';
const TABS = { home: () => HomeScreen(), configs: () => ConfigsScreen(), settings: () => SettingsScreen() };
const tabCache = {};

function bottomNav(active) {
  const n = el(`<div class="bottomnav">
    <div class="navbrand"><img src="assets/app.png" alt=""><span>${t('app_name')}</span></div>
    <div class="tab ${active === 'home' ? 'on' : ''}" data-t="home">${icon('ic_home')}<span>${t('tab_home')}</span></div>
    <div class="tab ${active === 'configs' ? 'on' : ''}" data-t="configs">${icon('ic_list')}<span>${t('tab_configs')}</span></div>
    <div class="tab ${active === 'settings' ? 'on' : ''}" data-t="settings">${icon('ic_settings')}<span>${t('tab_settings')}</span></div>
    <div class="tab desk-only" data-x="store">${icon('ic_cart')}<span>${t('home_store')}</span></div>
    <div class="navfoot desk-only"><span class="ver">HermesVPN Connect · v${esc(ST.version || '')}</span></div></div>`);
  $$('.tab[data-t]', n).forEach(x => x.onclick = () => switchTab(x.dataset.t));
  if (UPD) $('.tab[data-t=settings]', n).classList.add('has-update');
  $('.tab[data-x=store]', n).onclick = () => openPurchase();
  return n;
}
function switchTab(name) {
  while (stack.length) popScreen(true);
  if (currentTab === name && tabCache[name]) { tabCache[name].onShow && tabCache[name].onShow(); return; }
  if (tabCache[currentTab]) { tabCache[currentTab].node.style.display = 'none'; tabCache[currentTab].onHide && tabCache[currentTab].onHide(); }
  currentTab = name;
  if (!tabCache[name]) { tabCache[name] = TABS[name](); app().appendChild(tabCache[name].node); }
  tabCache[name].node.style.display = '';
  tabCache[name].onShow && tabCache[name].onShow();
}
function pushScreen(scr) {
  const top = stack.length ? stack[stack.length - 1] : tabCache[currentTab];
  if (top && top.onHide) top.onHide();
  scr.node.classList.add('push');
  stack.push(scr); app().appendChild(scr.node);
  scr.onShow && scr.onShow();
}
function popScreen(silent) {
  const s = stack.pop(); if (!s) return;
  s.onHide && s.onHide(); s.destroy && s.destroy(); s.node.remove();
  if (!silent) { const top = stack.length ? stack[stack.length - 1] : tabCache[currentTab]; top && top.onShow && top.onShow(); }
}
function rebuildAll() {
  while (stack.length) popScreen(true);
  for (const k of Object.keys(tabCache)) { tabCache[k].destroy && tabCache[k].destroy(); tabCache[k].node.remove(); delete tabCache[k]; }
  const cur = currentTab; currentTab = null; switchTab(cur || 'settings');
}
function pageShell(title, inner) {
  const n = el(`<div class="screen"><div class="topbar"><button class="iconbtn back">${icon('ic_back', 'flip')}</button><h1>${esc(title)}</h1></div><div class="scroll"></div></div>`);
  $('.back', n).onclick = () => popScreen();
  if (inner) $('.scroll', n).innerHTML = inner;
  return n;
}
function switchRow(label, sub, key, def, onChange) {
  const n = el(`<label class="row"><div class="rt"><div class="title">${esc(label)}</div>${sub ? `<div class="sub">${esc(sub)}</div>` : ''}</div>
    <span class="switch"><input type="checkbox" ${pref(key, def) ? 'checked' : ''}><span></span></span></label>`);
  $('input', n).onchange = async (e) => { await setPref(key, e.target.checked); onChange && onChange(e.target.checked); };
  return n;
}
function radioGroup(options, current, onPick) {
  const card = el('<div class="card"></div>');
  const draw = (cur) => {
    card.innerHTML = '';
    for (const o of options) {
      const r = el(`<div class="row"><span class="radio ${o.value === cur ? 'on' : ''}"></span><div class="rt"><div class="title">${esc(o.label)}</div>${o.sub ? `<div class="sub">${esc(o.sub)}</div>` : ''}</div></div>`);
      r.onclick = () => { draw(o.value); onPick(o.value); };
      card.appendChild(r);
    }
  };
  draw(current);
  return card;
}
function navRow(iconName, title, value, onClick, sub) {
  const r = el(`<div class="row">${iconName ? icon(iconName, 'lead') : ''}<div class="rt"><div class="title">${esc(title)}</div>${sub ? `<div class="sub">${esc(sub)}</div>` : ''}</div>${value != null ? `<span class="val">${esc(value)}</span>` : ''}${icon('ic_chevron', 'chev flip')}</div>`);
  r.onclick = onClick; return r;
}

// ======================= HOME (MainActivity) =======================
function isHermesEntry(e) {
  return e && (e.group === 'HermesVPN' || e.account || /example\.com$/i.test(e.address || ''));
}
function formatSpeed(b) {
  if (b >= 1048576) return (b / 1048576).toFixed(1) + ' MB/s';
  if (b >= 1024) return (b / 1024).toFixed(0) + ' KB/s';
  return b + ' B/s';
}
function HomeScreen() {
  const n = el(`<div class="screen home">
    <div class="stage">
      <div class="scrim"></div>
      <div class="top"><img src="assets/app.png" alt=""><div class="apptitle">${t('app_name')}</div>
        <div class="topbtn" id="buyBtn">${icon('ic_cart')}<span class="lbl">${t('home_store')}</span></div>
        <div class="topbtn" id="setBtn">${icon('ic_settings')}<span class="lbl">${t('settings_title')}</span></div></div>
      <div class="orbwrap breathe"><div class="orb-glow"></div><div class="orb-ring3"></div><div class="orb-ring2"></div>
        <div class="pulse outer"></div><div class="pulse inner"></div><div class="orb-btn" id="connectBtn">${t('btn_connect')}</div></div>
      <div class="info"><div class="lbl">${t('home_current_location')}</div><div class="loc" id="loc"></div><div class="dur" id="dur">00:00:00</div><div class="err" id="err"></div></div>
      <div class="homebottom"><canvas class="graph"></canvas>
        <div class="stats"><div><div class="sl">${t('label_download_speed')}</div><div class="sv" id="dl" style="color:#8EE9FF">0 B/s</div></div><div class="div"></div>
        <div><div class="sl">${t('label_upload_speed')}</div><div class="sv" id="ul" style="color:#B9A4FF">0 B/s</div></div><div class="div"></div>
        <div><div class="sl">${t('label_ping')}</div><div class="sv" id="pg">--</div></div></div></div>
    </div></div>`);
  n.appendChild(bottomNav('home'));
  const stage = $('.stage', n);
  let globe = null, graph = null, timer = null;
  const btn = $('#connectBtn', n);
  $('#setBtn', n).onclick = () => switchTab('settings');
  if (UPD) $('#setBtn', n).classList.add('has-update');
  $('#buyBtn', n).onclick = () => openPurchase();
  btn.onclick = async () => {
    if (ST.connecting) return;
    if (ST.running) { await api.Disconnect(); await refreshState(); update(); return; }
    btn.textContent = t('status_connecting'); n.classList.add('busy'); $('#err', n).textContent = '';
    const r = await api.Connect();
    n.classList.remove('busy');
    await refreshState(); update();
    if (r && !r.ok && r.error) {
      toast(t(r.error) + (r.detail ? ': ' + r.detail.split('\n').slice(-1)[0] : ''), true);
      if (r.detail) $('#err', n).textContent = r.detail.split('\n').slice(-2).join(' ').slice(0, 220);
      if (r.goto === 'settings') pushScreen(SubscriptionSettingsScreen());
    }
  };
  function tick() {
    if (!ST.running || !ST.startedAt) return;
    const s = Math.max(0, Math.floor((Date.now() - ST.startedAt) / 1000));
    const p = x => String(x).padStart(2, '0');
    $('#dur', n).textContent = `${p(Math.floor(s / 3600))}:${p(Math.floor(s % 3600 / 60))}:${p(s % 60)}`;
  }
  function update() {
    const e = ST.selected;
    $('#loc', n).innerHTML = !e ? '' : isHermesEntry(e) ? GERMANY_FLAG + esc(t('location_germany')) : esc(e.name);
    n.classList.toggle('connected', !!ST.running);
    if (ST.connecting) btn.textContent = t('status_connecting');
    else btn.textContent = ST.running ? t('btn_disconnect') : t('btn_connect');
    clearInterval(timer);
    if (ST.running) { tick(); timer = setInterval(tick, 1000); }
    else {
      $('#dl', n).textContent = formatSpeed(0); $('#ul', n).textContent = formatSpeed(0); $('#pg', n).textContent = '--';
      graph && graph.clear();
    }
  }
  const onSpeed = (s) => { if (!ST.running) return; $('#dl', n).textContent = formatSpeed(s.down); $('#ul', n).textContent = formatSpeed(s.up); graph && graph.add(s.down, s.up); };
  const onPing = (ms) => { $('#pg', n).textContent = ms != null && ms >= 0 ? `${ms} ms` : '--'; };
  return {
    node: n, update,
    onShow() {
      if (!globe) {
        globe = new HermesGlobe(stage); graph = new SpeedGraph($('canvas.graph', n));
        globe.onLayout = (gb) => {
          const ow = $('.orbwrap', n), inf = $('.info', n);
          n.classList.toggle('landscape', gb.landscape);
          if (gb.landscape) {
            const w = gb.r * 1.25;
            Object.assign(ow.style, { width: w + 'px', maxWidth: 'none', left: gb.cx + 'px', top: (gb.cy - gb.r * 1.375) + 'px' });
            Object.assign(inf.style, { top: (gb.cy + gb.r * 0.02) + 'px', left: (gb.cx - 230) + 'px', right: 'auto', width: '460px' });
          } else { ow.style.cssText = ''; inf.style.cssText = ''; }
          graph && graph.draw();
        };
        globe.layout();
      }
      globe.start(); update();
      HOME_HOOKS.speed = onSpeed; HOME_HOOKS.ping = onPing;
    },
    onHide() { globe && globe.stop(); HOME_HOOKS.speed = null; HOME_HOOKS.ping = null; },
    destroy() { globe && globe.stop(); clearInterval(timer); },
  };
}
const HOME_HOOKS = { speed: null, ping: null };

// ======================= CONFIGS (ConfigsActivity) =======================
function maskAddress(a) { a = a || ''; return a.length <= 8 ? a : a.slice(0, 4) + '****' + a.slice(-4); }
function ConfigsScreen() {
  const n = el(`<div class="screen">
    <div class="cfgbar"><h1>${t('tab_configs')}</h1>
      <button class="iconbtn" id="favBtn" title="${t('configs_favorites')}">${icon('ic_star_outline')}</button>
      <button class="iconbtn" id="qrBtn" title="${t('configs_scan_qr')}">${icon('ic_qr')}</button>
      <button class="iconbtn" id="addBtn" title="${t('configs_add')}">${icon('ic_add')}</button>
      <button class="iconbtn" id="menuBtn" title="${t('configs_menu')}">${icon('ic_more_vert')}</button></div>
    <div class="cfgtools"><span class="chip subchip" id="subBtn">${icon('ic_add')}<span>${t('sub_chip')}</span></span><span class="chip" id="pingAll">${icon('ic_speed')}<span>${t('configs_ping_all')}</span></span>
      <span class="chip" id="sortBtn">${icon('ic_sort')}<span id="sortTxt"></span></span></div>
    <div class="scroll" id="list"></div></div>`);
  n.appendChild(bottomNav('configs'));
  // accordion: one collapsible section per account (and per local/subscription group); by default only the
  // section holding the selected config is open — 10 accounts × 6 routes no longer make one endless list
  const toggled = new Map();
  let expandedId = null, favOnly = false;
  const list = $('#list', n);

  async function render() {
    const v = await api.ListConfigs(); if (!v) return;
    const sortMode = pref('configs_sort_mode', 'ping');
    $('#sortTxt', n).textContent = t(sortMode === 'name' ? 'sort_by_name' : 'sort_by_ping');
    let all = v.entries || [];
    if (favOnly) all = all.filter(e => e.favorite);
    if (sortMode === 'name') all = [...all].sort((a, b) => a.name.toLowerCase().localeCompare(b.name.toLowerCase()));
    else all = [...all].sort((a, b) => key(a) - key(b));
    function key(e) { return e.ping >= 0 ? e.ping : e.ping === -1 ? 1e6 : 2e6; }
    list.innerHTML = '';
    if (!all.length) { list.appendChild(el(`<div class="empty">${esc(t('configs_empty'))}\n\n${esc(t('win_activation'))}</div>`)); return; }
    const groups = new Map();
    const mainName = pref('auth_username', '') || 'HermesVPN';
    const gkey = e => e.group === 'HermesVPN' ? 'HermesVPN|' + (e.account || '') : e.group;
    for (const e of all) { const k = gkey(e); if (!groups.has(k)) groups.set(k, []); groups.get(k).push(e); }
    for (const [k, entries] of groups) {
      const g = entries[0].group;
      const acct = g === 'HermesVPN' ? (entries[0].account || mainName) : '';
      const title = acct ? '👤 ' + acct : g;
      const hasSel = entries.some(e => e.id === v.selectedId);
      const exp = toggled.has(k) ? toggled.get(k) : (hasSel || groups.size === 1);
      const best = entries.filter(e => e.ping >= 0).sort((a, b) => a.ping - b.ping)[0];
      const gr = el(`<div class="group ${exp ? '' : 'collapsed'}">${icon('ic_expand', 'exp')}<span class="gname">${esc(title)} (${fa(entries.length)})${hasSel ? ' ●' : ''}</span>
        <span class="ginfo">${esc(best ? t('group_best_ping', best.ping) : t('group_no_info'))}</span>
        <button class="iconbtn gping" title="${t('configs_ping_all')}">${icon('ic_speed')}</button><button class="iconbtn gmenu">${icon('ic_more_vert')}</button></div>`);
      gr.onclick = (ev) => { if (ev.target.closest('.iconbtn')) return; toggled.set(k, !exp); render(); };
      $('.gping', gr).onclick = () => pingEntries(entries.map(e => e.id));
      $('.gmenu', gr).onclick = (ev) => groupMenu(g, ev.currentTarget);
      list.appendChild(gr);
      if (!exp) continue;
      for (const e of entries) list.appendChild(configRow(acct ? { ...e, name: e.name.replace(' · ' + (e.account || ''), '') } : e, e.id === v.selectedId, e.id === expandedId));
    }
  }
  function configRow(e, sel, expanded) {
    let pcls = '', ptxt = t('ping_unknown');
    if (e.ping === -2) { pcls = 'bad'; ptxt = t('ping_timeout'); }
    else if (e.ping >= 0) { pcls = e.ping < 300 ? 'good' : 'ok'; ptxt = `${e.ping} ms`; }
    const r = el(`<div class="cfg ${sel ? 'sel' : ''}"><div class="body"><span class="badge">${esc(e.protocol)}</span>
      <div style="flex:1;min-width:0"><div class="cname">${e.favorite ? icon('ic_star', 'fav-mark') + ' ' : ''}${esc(e.name)}</div><div class="caddr">${esc(maskAddress(e.address))}</div></div>
      <span class="ping ${pcls}">${esc(ptxt)}</span></div>
      ${expanded ? `<div class="actions"><button class="iconbtn" data-a="edit" title="${t('action_rename')}">${icon('ic_edit')}</button>
        <button class="iconbtn" data-a="fav" title="${t('configs_favorites')}">${icon(e.favorite ? 'ic_star' : 'ic_star_outline')}</button>
        <button class="iconbtn" data-a="share" title="${t('action_share')}">${icon('ic_share')}</button>
        <button class="iconbtn" data-a="del" title="${t('action_delete')}">${icon('ic_delete')}</button></div>` : ''}</div>`);
    $('.body', r).onclick = async () => { await api.SelectConfig(e.id); expandedId = expandedId === e.id ? null : e.id; await refreshState(); render(); };
    $$('.actions .iconbtn', r).forEach(b => b.onclick = async () => {
      const a = b.dataset.a;
      if (a === 'edit') {
        const name = await dialog({ title: t('action_rename'), body: `<input class="input" value="${esc(e.name)}">`,
          buttons: [{ label: t('win_cancel'), value: null }, { label: t('btn_save'), primary: true, onClick: d => $('input', d).value.trim() }] });
        if (name) { await api.RenameConfig(e.id, name); render(); }
      } else if (a === 'fav') { await api.ToggleFavorite(e.id); render(); }
      else if (a === 'share') shareDialog(e);
      else if (a === 'del') {
        if (await confirmDlg(t('action_delete'), t('delete_config_confirm', e.name), t('action_delete'))) { await api.DeleteConfig(e.id); await refreshState(); render(); }
      }
    });
    return r;
  }
  function shareDialog(e) {
    if (/^hvpn:\/\//.test(e.uri)) { toast(t('tunnel_no_share'), true); return; }
    const isLink = !/^json:\/\//.test(e.uri);
    const body = isLink ? `<div class="qrbox"><img src="${qrDataURL(e.uri)}"></div><div class="mono">${esc(e.uri)}</div>` : `<div class="mono">${esc(e.rawOutbound || '')}</div>`;
    dialog({ title: t('win_share_title'), body, buttons: [{ label: t('update_close'), value: null },
      { label: t('win_copy_link'), primary: true, onClick: async () => { await api.CopyText(isLink ? e.uri : e.rawOutbound); toast(t('purchase_copied_toast')); } }] });
  }
  async function pingEntries(ids) {
    const chip = $('#pingAll span:last-child', n); chip.textContent = t('configs_pinging');
    await api.PingEntries(ids || []);
    chip.textContent = t('configs_ping_all'); render();
  }
  function groupMenu(g, anchor) {
    const items = [{ label: t('configs_ping_all'), icon: 'ic_speed', onClick: async () => { const v = await api.ListConfigs(); pingEntries(v.entries.filter(e => e.group === g).map(e => e.id)); } }];
    if (g === 'HermesVPN') items.push({ label: t('subscription_update_now'), icon: 'ic_refresh', onClick: refreshAccount });
    else items.push({ label: t('group_delete_all'), icon: 'ic_delete', onClick: async () => {
      if (await confirmDlg(t('group_delete_all'), t('group_delete_all_confirm', g), t('action_delete'))) { await api.DeleteGroup(g); await refreshState(); render(); }
    } });
    popupMenu(anchor, items);
  }
  async function refreshAccount() {
    toast(t('status_fetching'));
    const r = await api.RefreshAll(); if (!r) return;
    if (r.accountOk || (r.subs && r.subs.added > 0)) toast(t('subscription_updated'));
    else if (r.subs && r.subs.error === 'no_subscriptions' && !r.accountOk) toast(t('error_network'));
    else toast(t('subscription_up_to_date'));
    await refreshState(); render();
  }
  function importMessage(r) {
    if (!r) return t('add_config_invalid');
    if (r.added > 0 && r.skipped > 0) return t('add_config_added_skipped', r.added, r.skipped);
    if (r.added > 0) return t('add_config_added', r.added);
    if (r.error === 'network') return t('error_network');
    if (r.error && /^(deeplink_|error_|tunnel_)/.test(r.error)) return t(r.error);
    if (r.skipped > 0) return t('add_config_all_duplicates');
    return t('add_config_invalid');
  }
  async function importText(text) {
    if (!text) { toast(t('add_config_clipboard_empty')); return; }
    const r = await api.ImportText(text); toast(importMessage(r), true); await refreshState(); render();
  }
  async function manualDialog(proto) {
    const titles = { vless: 'manual_config_title_vless', trojan: 'manual_config_title_trojan', shadowsocks: 'manual_config_title_shadowsocks', socks: 'manual_config_title_socks' };
    const secretHint = proto === 'vless' ? t('manual_hint_uuid') : t('manual_hint_password');
    const body = `<input class="input" id="m_remark" placeholder="${t('manual_hint_remark')}" style="margin-bottom:8px">
      <input class="input ltr" id="m_addr" placeholder="${t('manual_hint_address')}" style="margin-bottom:8px">
      <input class="input ltr" id="m_port" inputmode="numeric" placeholder="${t('manual_hint_port')}" style="margin-bottom:8px">
      <input class="input ltr" id="m_secret" placeholder="${secretHint}" style="margin-bottom:8px">
      ${proto === 'shadowsocks' ? `<input class="input ltr" id="m_method" placeholder="${t('manual_hint_method')}">` : ''}
      ${proto === 'socks' ? `<input class="input ltr" id="m_user" placeholder="${t('manual_hint_username')}">` : ''}`;
    const vals = await dialog({ title: t(titles[proto]), body, buttons: [{ label: t('win_cancel'), value: null }, { label: t('btn_save'), primary: true,
      onClick: d => { const g = id => ($('#' + id, d) || {}).value || ''; return { remark: g('m_remark'), addr: g('m_addr'), port: g('m_port'), secret: g('m_secret'), method: g('m_method'), user: g('m_user') }; } }] });
    if (!vals) return;
    if (!vals.addr.trim() || !vals.port.trim() || !vals.secret.trim()) { toast(t('manual_config_missing_fields', secretHint), true); return; }
    const r = await api.ManualConfig(proto, vals.remark, vals.addr, vals.port, vals.secret, vals.method, vals.user);
    toast(importMessage(r), true); await refreshState(); render();
  }
  async function addSubscriptionDialog() {
    const clip = (await api.ClipboardText()) || '';
    const pre = /^https?:\/\//i.test(clip) ? clip : '';
    const vals = await dialog({ title: t('sub_title'),
      body: `<div class="hint" style="margin:0 0 12px">${t('sub_hint')}</div>
        <input class="input ltr" id="s_url" placeholder="${t('sub_link_ph')}" value="${esc(pre)}" style="margin-bottom:8px">
        <input class="input" id="s_name" placeholder="${t('sub_name_ph')}">`,
      buttons: [{ label: t('win_cancel'), value: null }, { label: t('subscription_save_and_update'), primary: true, onClick: d => ({ name: $('#s_name', d).value, url: $('#s_url', d).value }) }] });
    if (!vals) return;
    const url = vals.url.trim();
    if (!url) { toast(t('add_config_invalid')); return; }
    toast(t('status_fetching'));
    // HermesVPN's own sub link (/sub/<id>) or account id -> account path (same as Android); anything else -> generic subscription
    if (url.includes('/sub/') || !/^https?:\/\//i.test(url) || IsActivation(url)) {
      const r = await api.SubscriptionSave(url);
      if (r && r.key === 'error_not_found' && /^https?:\/\//i.test(url)) {
        // not a HermesVPN account: treat as another provider's sub link
        const g = await api.AddSubscription(vals.name.trim() || 'Subscription', url);
        toast(g && g.added > 0 ? t('subscription_added') : (g && g.error === 'network' ? t('error_network') : t('add_config_invalid')), true);
      } else toast(t(r.key, r.n), true);
    } else {
      const r = await api.AddSubscription(vals.name.trim() || 'Subscription', url);
      toast(r && r.added > 0 ? t('subscription_added') : (r && r.error === 'network' ? t('error_network') : t('add_config_invalid')), true);
    }
    await refreshState(); render();
  }
  function IsActivation(u) { return /^hermesvpn:\/\//i.test(u) || /app\.example\.com\/open/i.test(u); }
  async function qrFromImage(dataUrl) {
    const text = await decodeQR(dataUrl).catch(() => null);
    if (!text) { toast(t('add_config_no_qr_in_image'), true); return; }
    importText(text);
  }
  $('#favBtn', n).onclick = (ev) => { favOnly = !favOnly; ev.currentTarget.innerHTML = icon(favOnly ? 'ic_star' : 'ic_star_outline'); render(); };
  $('#qrBtn', n).onclick = (ev) => popupMenu(ev.currentTarget, [
    { label: t('win_qr_pick'), icon: 'ic_gallery', onClick: async () => { const f = await pickFile('image/*'); if (f) qrFromImage(f.data); } },
    { label: t('win_qr_paste'), icon: 'ic_copy', onClick: async () => {
      try {
        const items = await navigator.clipboard.read();
        for (const it of items) { const ty = it.types.find(x => x.startsWith('image/')); if (ty) { const b = await it.getType(ty); const rd = new FileReader(); rd.onload = () => qrFromImage(rd.result); rd.readAsDataURL(b); return; } }
      } catch (e) { }
      toast(t('win_qr_no_image'));
    } },
  ]);
  $('#addBtn', n).onclick = (ev) => popupMenu(ev.currentTarget, [
    { label: t('sub_menu'), icon: 'ic_link', onClick: addSubscriptionDialog },
    '-',
    { label: t('add_menu_uri_clipboard'), icon: 'ic_link', onClick: async () => importText(await api.ClipboardText()) },
    { label: t('add_menu_json_clipboard'), icon: 'ic_copy', onClick: async () => {
      const txt = await api.ClipboardText(); if (!txt) { toast(t('add_config_clipboard_empty')); return; }
      const r = await api.ImportJSON(txt); toast(r && r.ok ? t('add_config_json_added') : t('add_config_not_json'), true); render();
    } },
    { label: t('add_menu_v2box_file'), icon: 'ic_file', onClick: async () => {
      const f = await pickFile('', true); if (!f) return;
      const r = await api.ImportFileText(f.data); toast(importMessage(r), true); await refreshState(); render();
    } },
    '-',
    { label: t('add_menu_manual') + ' — VLESS', icon: 'ic_edit', onClick: () => manualDialog('vless') },
    { label: t('add_menu_manual') + ' — Trojan', icon: 'ic_edit', onClick: () => manualDialog('trojan') },
    { label: t('add_menu_manual') + ' — Shadowsocks', icon: 'ic_edit', onClick: () => manualDialog('shadowsocks') },
    { label: t('add_menu_manual') + ' — SOCKS', icon: 'ic_edit', onClick: () => manualDialog('socks') },
    '-',
    { label: t('add_menu_update_all'), icon: 'ic_refresh', onClick: refreshAccount },
  ]);
  $('#menuBtn', n).onclick = (ev) => popupMenu(ev.currentTarget, [
    { label: t('subscription_update_now'), icon: 'ic_refresh', onClick: refreshAccount },
    { label: t('configs_ping_all'), icon: 'ic_speed', onClick: () => pingEntries([]) },
    { label: t('subscription_settings'), icon: 'ic_link', onClick: () => pushScreen(SubscriptionSettingsScreen()) },
  ]);
  $('#pingAll', n).onclick = () => pingEntries([]);
  $('#subBtn', n).onclick = () => addSubscriptionDialog();
  $('#sortBtn', n).onclick = (ev) => popupMenu(ev.currentTarget, [
    { label: t('sort_by_ping'), onClick: async () => { await setPref('configs_sort_mode', 'ping'); render(); } },
    { label: t('sort_by_name'), onClick: async () => { await setPref('configs_sort_mode', 'name'); render(); } },
  ]);
  // paste anywhere on the configs tab = import (Ctrl+V)
  const onPaste = (e) => { if (currentTab !== 'configs' || stack.length || $('.overlay')) return; const txt = e.clipboardData.getData('text'); if (txt) importText(txt); };
  document.addEventListener('paste', onPaste);
  return { node: n, onShow: render, render, destroy() { document.removeEventListener('paste', onPaste); } };
}

// ======================= SETTINGS (SettingsActivity) =======================
function langLabel() { const p = pref('app_language', null); return p === 'en' ? t('lang_english') : p === 'fa' ? t('lang_persian') : p === '' ? t('language_auto') : t('lang_persian'); }
function SettingsScreen() {
  const n = el(`<div class="screen"><div class="topbar"><h1 style="font-size:26px;margin:0 8px">${t('settings_title')}</h1></div><div class="scroll"></div></div>`);
  n.appendChild(bottomNav('settings'));
  const sc = $('.scroll', n);
  function render() {
    sc.innerHTML = '';
    if (!ST.admin) sc.appendChild(el(`<div class="adminwarn">${esc(t('win_admin'))}</div>`));
    sc.appendChild(promoCard());
    sc.appendChild(el(`<div class="section-title">${t('section_device')}</div>`));
    const dev = el('<div class="card"></div>');
    const lang = navRow('ic_language', t('language_row'), langLabel(), (ev) => popupMenu(lang, [
      { label: t('language_auto'), onClick: () => setLang('') }, { label: t('lang_english'), onClick: () => setLang('en') }, { label: t('lang_persian'), onClick: () => setLang('fa') }]));
    dev.appendChild(lang);
    const id = pref('device_id', '');
    const idRow = el(`<div class="row">${icon('ic_info', 'lead')}<div class="rt"><div class="title">${t('device_id')}</div><div class="sub ltr" style="user-select:text">${esc(id)}</div><div class="sub">${t('device_id_hint')}</div></div><button class="iconbtn">${icon('ic_copy')}</button></div>`);
    $('.iconbtn', idRow).onclick = async (e) => { e.stopPropagation(); await api.CopyText(id); toast(t('purchase_copied_toast')); };
    dev.appendChild(idRow);
    dev.appendChild(navRow('ic_person', t('profile_title'), pref('auth_username', '') || null, () => pushScreen(pref('auth_username', '') ? ProfileScreen() : AuthScreen(false))));
    dev.appendChild(navRow('ic_crown', t('subscription_info'), null, () => pushScreen(SubscriptionInfoScreen())));
    sc.appendChild(dev);
    sc.appendChild(el(`<div class="section-title">${t('section_settings')}</div>`));
    const s = el('<div class="card"></div>');
    s.appendChild(navRow('ic_tunnel', t('tunnel_settings'), null, () => pushScreen(TunnelSettingsScreen())));
    s.appendChild(navRow('ic_dns', t('dns_settings'), null, () => pushScreen(DnsScreen())));
    s.appendChild(navRow('ic_route', t('route_settings'), null, () => pushScreen(RouteScreen())));
    s.appendChild(navRow('ic_link', t('subscription_settings'), null, () => pushScreen(SubscriptionSettingsScreen())));
    s.appendChild(navRow('ic_speed', t('speed_test'), null, () => pushScreen(SpeedTestScreen())));
    s.appendChild(navRow('ic_appearance', t('settings_theme_section'), null, () => pushScreen(AppearanceScreen())));
    s.appendChild(navRow('ic_info', t('update_check_menu'), null, () => pushScreen(UpdateScreen())));
    s.appendChild(navRow('ic_info', t('settings_about_section'), null, () => pushScreen(AboutScreen())));
    sc.appendChild(s);
    sc.appendChild(el(`<div class="section-title">${t('section_community')}</div>`));
    const c = el('<div class="card"></div>');
    c.appendChild(navRow('ic_telegram', t('telegram_channel_row'), null, () => api.OpenURL(TELEGRAM_CHANNEL_URL)));
    c.appendChild(navRow('ic_person', t('support_row'), null, () => api.OpenURL(BOT_CHAT_URL)));
    sc.appendChild(c);
    const uc = updateCard();
    if (uc) { sc.appendChild(el(`<div class="section-title">${t('update_check_menu')}</div>`)); sc.appendChild(uc); }
    const q = el(`<button class="btn danger" style="margin-top:22px">${t('win_quit')}</button>`);
    q.onclick = () => api.Quit();
    sc.appendChild(q);
  }
  return { node: n, onShow: async () => { await refreshState(); render(); } };
}
async function setLang(v) { await setPref('app_language', v); applyLang(); rebuildAll(); }

// ---------- auth (AuthActivity) ----------
function AuthScreen(startOnRegister) {
  const n = pageShell(t('btn_login'));
  const sc = $('.scroll', n);
  sc.innerHTML = `<div class="segmented"><div id="tabL">${t('btn_login')}</div><div id="tabR">${t('btn_register')}</div></div>
    <div id="loginForm"><div class="hint" style="margin-bottom:8px">${t('login_hint')}</div>
      <input class="input" id="lid" placeholder="${t('hint_username_or_email')}" value="${esc(pref('auth_username', ''))}" style="margin:8px 0">
      <input class="input" id="lpw" type="password" placeholder="${t('hint_password')}">
      <button class="btn" id="loginBtn">${t('btn_login')}</button><span class="link" id="forgot">${t('btn_forgot_password')}</span></div>
    <div id="regForm"><div class="hint" style="margin-bottom:8px">${t('register_hint')}</div>
      <input class="input" id="rtg" placeholder="${t('hint_telegram_username')}" style="margin:8px 0">
      <input class="input" id="rem" placeholder="${t('hint_email')}" style="margin-bottom:8px">
      <input class="input" id="rus" placeholder="${t('hint_new_username')}" style="margin-bottom:8px">
      <input class="input" id="rpw" type="password" placeholder="${t('hint_password')}">
      <button class="btn" id="regBtn">${t('btn_register')}</button><span class="link" id="regHelp">${t('register_no_account')}</span></div>`;
  const show = (login) => {
    $('#loginForm', n).style.display = login ? '' : 'none'; $('#regForm', n).style.display = login ? 'none' : '';
    $('#tabL', n).classList.toggle('on', login); $('#tabR', n).classList.toggle('on', !login);
  };
  show(!startOnRegister);
  $('#tabL', n).onclick = () => show(true); $('#tabR', n).onclick = () => show(false);
  $('#forgot', n).onclick = () => pushScreen(ForgotScreen());
  $('#regHelp', n).onclick = () => openPurchase();
  const done = (r) => { toast(t('login_success_with_configs', r.count), true); refreshState().then(() => { popScreen(); }); };
  $('#loginBtn', n).onclick = async (e) => {
    e.target.disabled = true; const r = await api.Login($('#lid', n).value, $('#lpw', n).value); e.target.disabled = false;
    if (!r || !r.ok) { toast(t((r && r.error) || 'error_network'), true); return; } done(r);
  };
  $('#regBtn', n).onclick = async (e) => {
    e.target.disabled = true; const r = await api.Register($('#rtg', n).value, $('#rem', n).value, $('#rus', n).value, $('#rpw', n).value); e.target.disabled = false;
    if (!r || !r.ok) { toast(t((r && r.error) || 'error_network'), true); return; } done(r);
  };
  return { node: n };
}
function ForgotScreen() {
  const n = pageShell(t('forgot_password_title'), `<div class="hint">${t('forgot_password_hint')}</div>
    <input class="input" id="fid" placeholder="${t('hint_username_or_email')}" style="margin-top:14px">
    <button class="btn" id="fbtn">${t('forgot_password_send')}</button>
    <div class="hint" id="fres" style="display:none;color:var(--connected)"></div>
    <span class="link" id="fbot">${t('forgot_password_open_bot')}</span>`);
  $('#fbtn', n).onclick = async (e) => {
    const id = $('#fid', n).value.trim(); if (!id) { toast(t('error_fill_all_fields')); return; }
    e.target.disabled = true; const ok = await api.ForgotPassword(id); e.target.disabled = false;
    if (!ok) { toast(t('error_network'), true); return; }
    $('#fres', n).style.display = ''; $('#fres', n).textContent = t('forgot_password_sent');
  };
  $('#fbot', n).onclick = () => api.OpenURL(BOT_CHAT_URL);
  return { node: n };
}

// ---------- profile (ProfileActivity) ----------
function bytesGB(total) { return total > 0 ? `${Math.floor(total / (1024 ** 3))} GB` : t('subscription_unlimited'); }
function ProfileScreen() {
  const n = pageShell(t('profile_title'));
  const sc = $('.scroll', n);
  sc.innerHTML = `<div class="avatar" id="av">${icon('ic_person')}<div class="badge2">${icon('ic_edit_white')}</div></div>
    <div class="center" style="margin-top:10px;font-size:18px;font-weight:700" id="pname">${esc(pref('auth_username', ''))}</div>
    <div class="center hint" id="pmail"></div>
    <div class="section-title">${t('subscription_info')}</div>
    <div class="card"><div class="kv"><span class="k">${t('subscription_expiry')}</span><span class="v" id="pexp">—</span></div>
      <div class="kv"><span class="k">${t('subscription_traffic')}</span><span class="v" id="ptr">—</span></div>
      <div class="kv"><span class="k">${t('subscription_configs_count')}</span><span class="v" id="pcnt">—</span></div></div>
    <div class="section-title">${t('profile_section_details')}</div>
    <div class="card pad"><input class="input" id="dn" placeholder="${t('profile_display_name')}" style="margin-bottom:8px"><input class="input ltr" id="em" placeholder="${t('hint_email')}">
      <button class="btn" id="saveP">${t('btn_save')}</button></div>
    <div class="section-title">${t('profile_section_security')}</div>
    <div class="card pad"><input class="input" id="np" type="password" placeholder="${t('profile_new_password')}"><button class="btn" id="chP">${t('profile_change_password')}</button></div>
    <button class="btn" id="buy" style="margin-top:20px">${t('purchase_menu_item')}</button>
    <button class="btn danger" id="lo">${t('btn_logout')}</button>`;
  const showAvatar = (b64) => { if (b64) { const a = $('#av', n); a.style.backgroundImage = `url(data:image/jpeg;base64,${b64})`; $('.icon', a).style.display = 'none'; } };
  showAvatar(pref('profile_avatar', ''));
  async function load() {
    const r = await api.ProfileFetch(); if (!r || !r.ok) return;
    const p = r.profile || {}, acc = r.account || {};
    $('#pname', n).textContent = p.display_name || p.username || pref('auth_username', '');
    $('#pmail', n).textContent = p.email || '';
    $('#dn', n).value = p.display_name || ''; $('#em', n).value = p.email || '';
    $('#pexp', n).textContent = acc.expiry_time > 0 ? fmtDate(acc.expiry_time) : t('subscription_unlimited');
    $('#ptr', n).textContent = bytesGB(acc.total_gb || 0);
    $('#pcnt', n).textContent = (acc.links || []).length;
    if (p.avatar) showAvatar(p.avatar);
  }
  $('#av', n).onclick = async () => {
    const f = await pickFile('image/*'); if (!f) return;
    let b64; try { b64 = await scaleImage(f.data, 256, true); } catch (e) { toast(t('profile_avatar_failed'), true); return; }
    const r = await api.ProfileUpdate({ avatar: b64 });
    if (!r || !r.ok) { toast(t('error_network'), true); return; }
    showAvatar(b64); toast(t('saved_toast'));
  };
  $('#saveP', n).onclick = async () => {
    const r = await api.ProfileUpdate({ display_name: $('#dn', n).value.trim(), email: $('#em', n).value.trim() });
    if (r && r.error === 'error_bad_email') { toast(t('error_bad_email'), true); return; }
    toast(r && r.ok ? t('saved_toast') : t('error_network')); if (r && r.ok) load();
  };
  $('#chP', n).onclick = async () => {
    const np = $('#np', n).value; if (np.length < 6) { toast(t('error_password_too_short'), true); return; }
    const r = await api.ChangePassword(np);
    if (!r || !r.ok) { toast(t('error_network'), true); return; }
    $('#np', n).value = ''; toast(t('profile_password_changed'), true);
  };
  $('#buy', n).onclick = () => openPurchase();
  $('#lo', n).onclick = async () => { await api.Logout(); toast(t('logout_toast')); await refreshState(); popScreen(); };
  load();
  return { node: n };
}

// ---------- subscription info ----------
function SubscriptionInfoScreen() {
  const n = pageShell(t('subscription_info'));
  const sc = $('.scroll', n);
  function render() {
    const u = pref('auth_username', '');
    if (!u) {
      sc.innerHTML = `<div class="card pad" style="margin-top:8px"><div class="hint" style="margin:0">${t('account_logged_out_hint')}</div>
        <button class="btn" id="li">${t('btn_login')}</button><button class="btn secondary" id="rg">${t('btn_register')}</button></div>
        <button class="btn" id="buy" style="margin-top:20px">${t('purchase_menu_item')}</button>`;
      $('#li', n).onclick = () => pushScreen(AuthScreen(false)); $('#rg', n).onclick = () => pushScreen(AuthScreen(true));
      $('#buy', n).onclick = () => openPurchase();
      return;
    }
    const exp = pref('account_expiry', 0), tot = pref('account_total_gb', 0);
    sc.innerHTML = `<div class="hint">${t('account_logged_in_as')}</div><div style="font-size:20px;font-weight:700;margin:4px 6px 12px">${esc(u)}</div>
      <div class="card"><div class="kv"><span class="k">${t('subscription_expiry')}</span><span class="v">${exp > 0 ? fmtDate(exp) : t('subscription_unlimited')}</span></div>
      <div class="kv"><span class="k">${t('subscription_traffic')}</span><span class="v">${bytesGB(tot)}</span></div>
      <div class="kv"><span class="k">${t('subscription_configs_count')}</span><span class="v" id="cc">…</span></div></div>
      <button class="btn" id="buy" style="margin-top:20px">${t('purchase_menu_item')}</button>
      <button class="btn secondary" id="rf">${t('subscription_update_now')}</button>
      <button class="btn danger" id="lo">${t('btn_logout')}</button>`;
    api.ListConfigs().then(v => { if (v) $('#cc', n).textContent = v.entries.filter(e => e.group === 'HermesVPN').length; });
    $('#buy', n).onclick = () => openPurchase();
    $('#rf', n).onclick = refresh;
    $('#lo', n).onclick = async () => { await api.Logout(); toast(t('logout_toast')); await refreshState(); render(); };
  }
  async function refresh() { if (await api.SubscriptionInfoRefresh()) { await refreshState(); render(); } }
  return { node: n, onShow() { render(); if (pref('auth_username', '')) refresh(); } };
}

// ---------- subscription settings ----------
function SubscriptionSettingsScreen() {
  const n = pageShell(t('subscription_settings'));
  const sc = $('.scroll', n);
  sc.innerHTML = `<div class="section-title">${t('subscription_profile_link')}</div>
    <div class="card pad"><input class="input ltr" id="pl" placeholder="${t('hint_account_id')}" value="${esc(pref('sub_id', ''))}">
      <div class="hint">${t('subscription_profile_link_hint')}<br>${t('win_activation')}</div>
      <button class="btn" id="sv">${t('subscription_save_and_update')}</button></div>
    <div class="section-title">${t('subscription_section_update')}</div><div class="card" id="upd"></div>
    <div class="hint">${t('subscription_last_update')}: <span id="lu" class="ltr"></span></div>`;
  const upd = $('#upd', n);
  upd.appendChild(navRow('ic_refresh', t('subscription_update_now'), null, async () => {
    toast(t('status_fetching'));
    const r = await api.RefreshAll();
    toast(r && (r.accountOk || (r.subs && r.subs.added > 0)) ? t('subscription_updated') : t('subscription_up_to_date'));
    await refreshState(); renderLU();
  }));
  upd.appendChild(switchRow(t('subscription_auto_update'), t('subscription_auto_update_hint'), 'subscription_auto_update', true));
  function renderLU() { const ts = pref('last_subscription_update', 0); $('#lu', n).textContent = ts > 0 ? fmtDate(ts, true) : '—'; }
  renderLU();
  $('#sv', n).onclick = async (e) => {
    const raw = $('#pl', n).value.trim(); if (!raw) { toast(t('add_config_invalid')); return; }
    e.target.disabled = true; const r = await api.SubscriptionSave(raw); e.target.disabled = false;
    toast(t(r.key, r.n), true); await refreshState(); renderLU();
  };
  return { node: n };
}

// ---------- tunnel settings (TunnelSettingsActivity + Windows options) ----------
function TunnelSettingsScreen() {
  const n = pageShell(t('tunnel_settings'));
  const sc = $('.scroll', n);
  async function render() {
    await refreshState();
    sc.innerHTML = '';
    if (!ST.admin) sc.appendChild(el(`<div class="adminwarn">${esc(t('win_admin'))}</div>`));
    sc.appendChild(el(`<div class="section-title">${t('win_mode_title')}</div>`));
    sc.appendChild(radioGroup([{ value: 'tun', label: t('win_mode_tun') }, { value: 'proxy', label: t('win_mode_proxy') }], pref('win_connection_mode', 'tun'),
      async v => { await setPref('win_connection_mode', v); if (ST.running) toast(t('win_reconnect_hint')); }));
    sc.appendChild(el(`<div class="hint">${t('win_mode_hint')}</div>`));
    sc.appendChild(el(`<div class="section-title">${t('tunnel_section_apps')}</div>`));
    const mode = pref('tunnel_mode', 'exclude');
    const cnt = (mode === 'include' ? pref('included_apps', []) : pref('excluded_apps', [])).length;
    const apps = el('<div class="card"></div>');
    apps.appendChild(navRow('ic_tune', t('settings_app_exclusions'), cnt === 0 ? t('tunnel_per_app_off') : t('tunnel_per_app_count', cnt), () => pushScreen(AppsScreen())));
    sc.appendChild(apps);
    sc.appendChild(el(`<div class="section-title">${t('tunnel_section_behavior')}</div>`));
    const b = el('<div class="card"></div>');
    b.appendChild(switchRow(t('settings_bypass_lan'), t('settings_bypass_lan_hint'), 'bypass_lan', false));
    b.appendChild(switchRow(t('tunnel_mux'), '', 'mux_enabled', false));
    b.appendChild(switchRow(t('tunnel_ipv6'), t('tunnel_ipv6_hint'), 'enable_ipv6', false));
    sc.appendChild(b);
    sc.appendChild(el(`<div class="hint ltr" style="text-align:start">${esc(pref('last_tun_summary', '') || (LANG === 'fa' ? 'هنوز وصل نشده‌ای — یک‌بار وصل شو تا وضعیت اینجا بیاید.' : 'Not connected yet — connect once to see the tunnel status here.'))}</div>`));
    sc.appendChild(el(`<div class="section-title">${t('tunnel_section_system')}</div>`));
    const s = el('<div class="card"></div>');
    s.appendChild(switchRow(t('settings_screenshot_protection'), t('settings_screenshot_protection_hint'), 'screenshot_protection', false));
    const auto = switchRow(t('win_autostart'), '', 'win_autostart', false, async (on) => { const ok = await api.SetAutostart(on); if (!ok) { toast(t('error_network')); render(); } });
    s.appendChild(auto);
    s.appendChild(switchRow(t('win_autoconnect'), '', 'auto_connect_on_start', false));
    s.appendChild(navRow('ic_file', t('win_log'), null, () => pushScreen(LogScreen())));
    sc.appendChild(s);
    api.AutostartEnabled().then(on => { const i = $('input', auto); if (i && i.checked !== on) i.checked = on; });
  }
  return { node: n, onShow: render };
}
function AppsScreen() {
  const n = pageShell(t('app_exclusion_title'));
  const sc = $('.scroll', n);
  let procs = [], q = '';
  const key = () => pref('tunnel_mode', 'exclude') === 'include' ? 'included_apps' : 'excluded_apps';
  const sel = () => new Set((pref(key(), []) || []).map(x => x.toLowerCase()));
  async function toggle(name) {
    const s = pref(key(), []) || []; const i = s.findIndex(x => x.toLowerCase() === name.toLowerCase());
    if (i >= 0) s.splice(i, 1); else s.push(name);
    await setPref(key(), [...s]); render();
  }
  function render() {
    const mode = pref('tunnel_mode', 'exclude'); const chosen = sel();
    sc.innerHTML = `<div class="segmented"><div id="mEx" class="${mode !== 'include' ? 'on' : ''}">${t('tunnel_mode_exclude')}</div><div id="mIn" class="${mode === 'include' ? 'on' : ''}">${t('tunnel_mode_include')}</div></div>
      <div class="hint">${t('app_exclusion_hint')}</div><div class="hint">${t('win_split_hint')}</div>
      ${pref('win_connection_mode', 'tun') === 'proxy' ? `<div class="adminwarn">${t('win_split_proxy_only')}</div>` : ''}
      <input class="input" id="qq" placeholder="${t('app_exclusion_search')}" value="${esc(q)}" style="margin-top:12px">
      <button class="btn secondary" id="addExe">${icon('ic_add')}${t('win_split_add')}</button>
      <div class="section-title">${t('win_split_selected')}</div><div class="card applist" id="selL"></div>
      <div class="section-title">${t('win_split_running')}</div><div class="card applist" id="runL"></div>`;
    $('#mEx', n).onclick = async () => { await setPref('tunnel_mode', 'exclude'); render(); };
    $('#mIn', n).onclick = async () => { await setPref('tunnel_mode', 'include'); render(); };
    const qq = $('#qq', n); qq.oninput = () => { q = qq.value; const pos = qq.selectionStart; render(); const nq = $('#qq', n); nq.focus(); nq.setSelectionRange(pos, pos); };
    $('#addExe', n).onclick = async () => {
      const inp = document.createElement('input'); inp.type = 'file'; inp.accept = '.exe';
      inp.onchange = () => { const f = inp.files[0]; if (f && !sel().has(f.name.toLowerCase())) toggle(f.name); }; inp.click();
    };
    const match = (s) => !q || s.toLowerCase().includes(q.toLowerCase());
    const row = (name, path, on) => { const r = el(`<div class="row"><div class="rt"><div class="title">${esc(name)}</div>${path ? `<div class="sub">${esc(path)}</div>` : ''}</div><span class="check ${on ? 'on' : ''}"></span></div>`); r.onclick = () => toggle(name); return r; };
    const selL = $('#selL', n), runL = $('#runL', n);
    const chosenNames = (pref(key(), []) || []).filter(match);
    if (!chosenNames.length) selL.appendChild(el(`<div class="row"><div class="sub">${t('tunnel_per_app_off')}</div></div>`));
    chosenNames.forEach(nm => selL.appendChild(row(nm, (procs.find(p => p.name.toLowerCase() === nm.toLowerCase()) || {}).path, true)));
    procs.filter(p => !chosen.has(p.name.toLowerCase()) && (match(p.name) || match(p.path))).forEach(p => runL.appendChild(row(p.name, p.path, false)));
    if (!runL.children.length) runL.appendChild(el('<div class="row"><div class="sub">—</div></div>'));
  }
  return { node: n, async onShow() { render(); procs = (await api.Processes()) || []; render(); } };
}

// ---------- DNS ----------
function DnsScreen() {
  const n = pageShell(t('dns_settings'));
  const sc = $('.scroll', n);
  const cur = pref('dns_server', '1.1.1.1');
  const known = { '1.1.1.1': 'cf', '8.8.8.8': 'gg', '94.140.14.14': 'ad' };
  sc.appendChild(el(`<div class="section-title">${t('dns_section_provider')}</div>`));
  const custom = el(`<div class="card pad" style="margin-top:10px;${known[cur] ? 'display:none' : ''}"><input class="input ltr" id="dnsIn" placeholder="${t('settings_custom_dns')}" value="${esc(cur)}"><button class="btn" id="dnsSave">${t('btn_save')}</button></div>`);
  sc.appendChild(radioGroup([
    { value: '1.1.1.1', label: t('dns_cloudflare') }, { value: '8.8.8.8', label: t('dns_google') }, { value: '94.140.14.14', label: t('dns_adguard') }, { value: 'custom', label: t('dns_custom') },
  ], known[cur] ? cur : 'custom', async v => {
    if (v === 'custom') { custom.style.display = ''; return; }
    custom.style.display = 'none'; $('#dnsIn', custom).value = v; await setPref('dns_server', v);
  }));
  sc.appendChild(custom);
  sc.appendChild(el(`<div class="hint">${t('dns_hint')}</div>`));
  $('#dnsSave', custom).onclick = async () => { const v = $('#dnsIn', custom).value.trim(); await setPref('dns_server', v || '1.1.1.1'); toast(t('saved_toast')); };
  return { node: n };
}

// ---------- route (RouteSettingsActivity) ----------
function RouteScreen() {
  const n = pageShell(t('route_settings'));
  const sc = $('.scroll', n);
  sc.appendChild(el(`<div class="section-title">${t('settings_server_section')}</div>`));
  sc.appendChild(radioGroup([{ value: 'main', label: t('server_main') }, { value: 'reality', label: t('server_reality') }], pref('preferred_server', 'reality'), v => setPref('preferred_server', v)));
  sc.appendChild(el(`<div class="hint">${t('settings_server_hint')}</div>`));
  sc.appendChild(el(`<div class="section-title">${t('route_section_mode')}</div>`));
  sc.appendChild(radioGroup([{ value: 'global', label: t('route_mode_global') }, { value: 'bypass_iran', label: t('route_mode_bypass_iran') }], pref('route_mode', 'bypass_iran'), v => setPref('route_mode', v)));
  sc.appendChild(el(`<div class="hint">${t('route_mode_hint')}</div>`));
  sc.appendChild(el(`<div class="section-title">${t('settings_telegram_section')}</div>`));
  const tg = el(`<div class="card pad"><input class="input ltr" id="tgp" placeholder="${t('hint_telegram_proxy')}"><button class="btn" id="tgo">${t('btn_open_telegram')}</button><div class="hint">${t('settings_telegram_hint')}</div></div>`);
  $('#tgo', tg).onclick = () => {
    const l = $('#tgp', tg).value.trim();
    if (!l.startsWith('tg://proxy') && !l.includes('proxy?server=')) { toast(t('error_bad_proxy_link'), true); return; }
    api.OpenURL(l);
  };
  sc.appendChild(tg);
  return { node: n };
}

// ---------- speed test ----------
function SpeedTestScreen() {
  const n = pageShell(t('speed_test'));
  const sc = $('.scroll', n);
  sc.innerHTML = `<div class="card pad center"><div class="hint" style="margin:0">${t('server_label')}</div><div style="font-weight:700;font-size:16px;margin-top:4px" id="tg"></div>
    <div class="bigresult" id="res">—</div><div class="hint center" id="unit"></div></div>
    <button class="btn" id="lat">${t('speed_test_run_latency')}</button><button class="btn secondary" id="dl">${t('speed_test_run_download')}</button>
    <div class="hint">${t('speed_test_hint')}</div>`;
  $('#tg', n).textContent = ST.selected ? ST.selected.name : t('speed_test_no_config');
  $('#lat', n).onclick = async () => {
    if (!ST.selected) return; $('#res', n).textContent = '…'; $('#unit', n).textContent = t('speed_test_latency');
    const p = await api.SpeedLatency(); $('#res', n).textContent = p >= 0 ? p : t('ping_timeout');
  };
  $('#dl', n).onclick = async () => {
    $('#res', n).textContent = '…'; $('#unit', n).textContent = t('speed_test_download');
    const m = await api.SpeedDownload(); $('#res', n).textContent = m >= 0 ? m.toFixed(1) : '—';
  };
  return { node: n };
}

// ---------- appearance ----------
function AppearanceScreen() {
  const n = pageShell(t('settings_theme_section'));
  const sc = $('.scroll', n);
  sc.appendChild(el(`<div class="section-title">${t('settings_theme_section')}</div>`));
  sc.appendChild(radioGroup([{ value: 'light', label: t('theme_light') }, { value: 'dark', label: t('theme_dark') }, { value: 'system', label: t('theme_system') }],
    pref('theme_mode', 'system'), async v => { await setPref('theme_mode', v); applyTheme(); }));
  sc.appendChild(el(`<div class="section-title">${t('settings_language_section')}</div>`));
  const cur = pref('app_language', null);
  sc.appendChild(radioGroup([{ value: '', label: t('language_auto') }, { value: 'en', label: t('lang_english') }, { value: 'fa', label: t('lang_persian') }],
    cur === null ? 'fa' : cur, v => setLang(v)));
  return { node: n };
}

// ---------- about ----------
function AboutScreen() {
  const n = pageShell(t('settings_about_section'), `<div class="center" style="margin-top:20px"><img src="assets/app.png" style="width:96px;height:96px;border-radius:24px">
    <div style="font-size:22px;font-weight:700;margin-top:12px">${t('app_name')}</div><div class="hint center">${t('app_tagline')}</div></div>
    <div class="card" style="margin-top:24px"><div class="kv"><span class="k">${t('settings_version')}</span><span class="v">${esc(ST.version)} (Windows)</span></div></div>
    <div class="card" style="margin-top:12px" id="pp"></div>`);
  $('#pp', n).appendChild(navRow('ic_info', t('settings_privacy_policy'), null, () => api.OpenURL(PRIVACY_URL)));
  return { node: n };
}

// ---------- devices & sharing ----------
function DevicesScreen() {
  const n = pageShell(t('dev_screen'));
  const sc = $('.scroll', n);
  sc.innerHTML = `<div class="dev-hero"><div class="promo-icons"><span class="pi">${ANDROID_SVG}</span><span class="pi">${WINDOWS_SVG}</span></div>
      <div class="promo-title" style="color:var(--text)">${t('dev_promo_title')}</div><div class="hint center" style="margin:6px 0 0">${t('dev_promo_sub')}</div></div>
    <div class="devgrid">
      <div class="card pad devcard"><div class="dc-head">${ANDROID_SVG}<b>${t('dev_android_title')}</b></div>
        <div class="qrbox"><img src="${qrDataURL(LINK_ANDROID)}" alt=""></div>
        <div class="hint center" style="margin:0 0 6px">${t('dev_android_hint')}</div>
        <div class="row-btns"><button class="btn small" id="aOpen">${t('dev_open')}</button><button class="btn small secondary" id="aCopy">${icon('ic_copy')}${t('dev_copy')}</button></div></div>
      <div class="card pad devcard"><div class="dc-head">${WINDOWS_SVG}<b>${t('dev_windows_title')}</b><span class="okchip">✓ ${t('dev_installed', esc(ST.version || ''))}</span></div>
        <div class="qrbox"><img src="${qrDataURL(LINK_WINDOWS)}" alt=""></div>
        <div class="hint center" style="margin:0 0 6px">${t('dev_windows_hint')}</div>
        <div class="row-btns"><button class="btn small secondary" id="wCopy">${icon('ic_copy')}${t('dev_copy')}</button></div></div>
    </div>
    <div class="section-title">${t('dev_share_title')}</div>
    <div class="card pad"><div class="hint" style="margin:0 0 12px">${t('dev_share_hint')}</div>
      <div class="sharebar"><button class="sbtn tg" id="sTg"><svg viewBox="0 0 24 24" width="22" height="22"><path fill="#fff" d="M9.8 15.3l-.4 5.3c.6 0 .8-.2 1.1-.5l2.6-2.5 5.4 4c1 .5 1.7.3 2-.9l3.6-17c.3-1.5-.6-2.1-1.6-1.7L1.3 9.9C-.2 10.5-.2 11.3 1 11.7l5.4 1.7L19 5.5c.6-.4 1.1-.2.7.2z"/></svg>${t('dev_share_telegram')}</button>
        <button class="sbtn wa" id="sWa"><svg viewBox="0 0 24 24" width="22" height="22"><path fill="#fff" d="M12 2a10 10 0 00-8.6 15.1L2 22l5-1.3A10 10 0 1012 2zm5.4 14.1c-.2.6-1.3 1.2-1.8 1.2-.5.1-1 .2-3.3-.7-2.8-1.1-4.6-4-4.7-4.2-.1-.2-1.1-1.5-1.1-2.9s.7-2 1-2.3c.2-.3.5-.3.7-.3h.5c.2 0 .4 0 .6.5l.8 2c.1.2.1.3 0 .5l-.4.5c-.2.2-.3.4-.1.7.2.3.8 1.3 1.7 2.1 1.2 1 2.1 1.3 2.4 1.5.3.1.5.1.7-.1l.9-1.1c.2-.3.4-.2.7-.1l1.9.9c.3.1.5.2.5.3.1.2.1.8-.1 1.4z"/></svg>${t('dev_share_whatsapp')}</button>
        <button class="sbtn cp" id="sCp">${icon('ic_copy')}${t('dev_share_copy')}</button></div>
      <div class="mono" style="margin-top:12px;white-space:pre-line;direction:auto">${esc(shareText())}</div></div>`;
  const copy = async (s) => { await api.CopyText(s); toast(t('purchase_copied_toast')); };
  $('#aOpen', n).onclick = () => api.OpenURL(LINK_ANDROID);
  $('#aCopy', n).onclick = () => copy(LINK_ANDROID);
  $('#wCopy', n).onclick = () => copy(LINK_WINDOWS);
  $('#sTg', n).onclick = () => api.OpenURL('https://t.me/share/url?url=' + encodeURIComponent(LINK_ANDROID) + '&text=' + encodeURIComponent(shareText()));
  $('#sWa', n).onclick = () => api.OpenURL('https://wa.me/?text=' + encodeURIComponent(shareText()));
  $('#sCp', n).onclick = () => copy(shareText());
  return { node: n };
}

// ---------- connection log ----------
function LogScreen() {
  const n = pageShell(t('win_log'), `<button class="btn secondary" id="cp">${icon('ic_copy')}${t('win_log_copy')}</button><div class="logbox" id="lg" style="margin-top:12px"></div>`);
  let lines = [];
  $('#cp', n).onclick = async () => { await api.CopyText(lines.join('\n')); toast(t('purchase_copied_toast')); };
  return { node: n, async onShow() { lines = (await api.LogTail()) || []; const s = `${pref('last_tun_summary', '')}\n${ST.lastError ? 'last error: ' + ST.lastError + '\n' : ''}\n` + lines.join('\n'); $('#lg', n).textContent = s.trim() || '—'; } };
}

// ---------- purchase (PurchaseActivity) ----------
function openPurchase() {
  if (!pref('auth_username', '')) { toast(t('error_no_link'), true); pushScreen(AuthScreen(false)); return; }
  pushScreen(PurchaseScreen());
}
function PurchaseScreen() {
  const n = pageShell(t('purchase_title'));
  const sc = $('.scroll', n);
  sc.innerHTML = `<div class="section-title">${t('purchase_step_plan')}</div><div class="card" id="plans"><div class="row"><div class="sub">${t('purchase_loading')}</div></div></div>
    <button class="btn" id="cont" style="display:none">${t('purchase_continue')}</button>
    <div id="mSec" style="display:none"><div class="section-title">${t('purchase_step_method')}</div>
      <div class="card"><div class="row" data-m="card">${t('purchase_method_card')}</div><div class="row" data-m="crypto">${t('purchase_method_crypto')}</div>
      <div class="row" data-m="bank_try">${t('purchase_method_bank_try')}</div><div class="row" data-m="card_gateway">${t('purchase_method_gateway')}</div></div></div>
    <div id="dSec" style="display:none"><div class="section-title">${t('purchase_step_details')}</div>
      <div class="card pad"><div class="details" id="det"></div>
        <button class="btn secondary" id="copy">${icon('ic_copy')}${t('purchase_copy')}</button>
        <button class="btn" id="gw" style="display:none">${t('purchase_open_gateway')}</button>
        <button class="btn" id="up" style="display:none">${t('purchase_upload_receipt')}</button>
        <button class="btn secondary" id="chk">${t('purchase_check_status')}</button>
        <div class="hint center" id="stat" style="font-size:14px"></div></div></div>`;
  let plan = null, orderId = null, copyable = '', gwURL = '';
  (async () => {
    const r = await api.Plans();
    const box = $('#plans', n);
    if (!r || !r.ok) { box.innerHTML = `<div class="row"><div class="sub">${t('purchase_error_generic')}</div></div>`; return; }
    box.innerHTML = '';
    for (const p of r.plans || []) {
      const row = el(`<div class="plan"><span class="radio"></span><span class="pl">${esc(p.label)}</span><span class="pp">${fmtNum(p.price)} ${LANG === 'fa' ? 'تومان' : 'Toman'}</span></div>`);
      row.onclick = () => { plan = p.key; $$('.plan .radio', box).forEach(x => x.classList.remove('on')); $('.radio', row).classList.add('on'); if (!orderId) $('#cont', n).style.display = ''; };
      box.appendChild(row);
    }
  })();
  $('#cont', n).onclick = async (e) => {
    if (!plan) return; e.target.disabled = true;
    const r = await api.CreateOrder(plan); e.target.disabled = false;
    if (!r || !r.ok) { toast(r && r.error === 'network' ? t('error_network') : t('purchase_error_generic'), true); return; }
    orderId = r.order_id; $('#cont', n).style.display = 'none'; $('#mSec', n).style.display = '';
  };
  $$('#mSec .row', n).forEach(rw => rw.onclick = async () => {
    const m = rw.dataset.m; $('#dSec', n).style.display = 'none';
    const r = await api.ChooseMethod(orderId, m);
    if (!r || !r.ok) { toast(r && r.error === 'gateway_not_configured' ? t('purchase_gateway_unavailable') : t('purchase_error_generic'), true); return; }
    $('#dSec', n).style.display = ''; $('#gw', n).style.display = 'none'; $('#up', n).style.display = 'none'; $('#stat', n).textContent = '';
    let txt = '';
    if (m === 'card') { txt = `شماره کارت: ${r.card_number}\nبه نام: ${r.card_holder}\nمبلغ: ${fmtNum(r.amount_toman)} تومان`; copyable = r.card_number; $('#up', n).style.display = ''; }
    else if (m === 'bank_try') { txt = `Card: ${r.card_number}\nHolder: ${r.card_holder}\nAmount: ${r.try_amount} TRY`; copyable = r.card_number; $('#up', n).style.display = ''; }
    else if (m === 'crypto') { txt = `Wallet (TRC20 / USDT):\n${r.wallet_address}\n\nAmount: ${r.usdt_amount} USDT\n\n⚠️ TRC20 network only.`; copyable = r.wallet_address; }
    else { gwURL = r.checkout_url; txt = "Tap below to pay securely on the bank's own page."; copyable = gwURL; $('#gw', n).style.display = ''; }
    $('#det', n).textContent = txt;
  });
  $('#copy', n).onclick = async () => { if (!copyable) return; await api.CopyText(copyable); toast(t('purchase_copied_toast')); };
  $('#gw', n).onclick = () => gwURL && api.OpenURL(gwURL);
  $('#up', n).onclick = async (e) => {
    const f = await pickFile('image/*'); if (!f) return;
    e.target.disabled = true; e.target.textContent = t('purchase_receipt_uploading');
    let b64; try { b64 = await scaleImage(f.data, 1600, false); } catch (x) { b64 = f.data.split(',')[1]; }
    const r = await api.UploadReceipt(orderId, b64);
    e.target.disabled = false; e.target.textContent = t('purchase_upload_receipt');
    if (!r || !r.ok) { toast(t('purchase_error_generic'), true); return; }
    $('#stat', n).textContent = r.message || '';
  };
  $('#chk', n).onclick = async () => {
    const r = await api.OrderStatus(orderId);
    if (!r || !r.ok) { $('#stat', n).textContent = t('purchase_error_generic'); return; }
    $('#stat', n).textContent = r.status === 'approved' ? '✅ ' + t('login_success_toast') : r.status === 'pending_review' ? '⏳ …' : r.status;
    if (r.status === 'approved') setTimeout(() => popScreen(), 1500);
  };
  return { node: n };
}

// ---------- update (UpdateActivity) ----------
async function maybeCheckUpdate() {
  if (Date.now() - pref('update_last_check', 0) < 3600 * 1000) return;
  const r = await api.CheckUpdate(); await refreshState();
  if (r && r.available && pref('update_skipped_code', 0) !== r.info.versionCode) {
    const go = await dialog({ title: t('update_available_title', r.info.versionName), body: `<div class="msg">${esc(r.info.notes || t('update_default_notes'))}</div>`,
      buttons: [{ label: t('update_later'), value: false, onClick: () => { api.SkipUpdate(r.info.versionCode); return false === true; } }, { label: t('update_download'), primary: true, value: true }] });
    if (go) pushScreen(UpdateScreen(r));
  }
}
function UpdateScreen(pre) {
  const n = pageShell(t('update_check_menu'));
  const sc = $('.scroll', n);
  async function check() {
    sc.innerHTML = `<div class="spinner"></div><div class="center hint">${t('update_checking')}</div>`;
    const r = pre || await api.CheckUpdate(); pre = null;
    if (!r || !r.ok) { sc.innerHTML = `<div class="center" style="margin-top:40px">${t('error_network')}</div><button class="btn" id="rt">${t('update_retry')}</button>`; $('#rt', n).onclick = check; return; }
    if (!r.available) { sc.innerHTML = `<div class="center" style="margin-top:40px;font-size:20px;font-weight:700">${t('update_none_title')}</div><div class="hint center">${t('win_update_none_win', r.current)}</div><button class="btn secondary" id="cl">${t('update_close')}</button>`; $('#cl', n).onclick = () => popScreen(); return; }
    const i = r.info;
    sc.innerHTML = `<div class="center" style="margin-top:30px;font-size:20px;font-weight:700">${t('update_available_title', esc(i.versionName))}</div>
      <div class="card pad" style="margin-top:14px"><div class="msg" style="white-space:pre-line;line-height:1.8">${esc(i.notes || t('update_default_notes'))}</div></div>
      <div class="progress" id="pb" style="display:none"><div></div></div><div class="hint center" id="pt"></div>
      <button class="btn" id="dl">${t('update_download')}</button>${i.mandatory ? '' : `<button class="btn secondary" id="lt">${t('update_later')}</button>`}`;
    if ($('#lt', n)) $('#lt', n).onclick = () => { api.SkipUpdate(i.versionCode); popScreen(); };
    $('#dl', n).onclick = async (e) => {
      e.target.disabled = true; e.target.textContent = t('update_downloading'); $('#pb', n).style.display = '';
      UPDATE_HOOK = (p) => { if (p.total > 0) $('#pb div', n).style.width = (p.done / p.total * 100) + '%'; $('#pt', n).textContent = t('update_progress', (p.done / 1048576).toFixed(1), p.total > 0 ? (p.total / 1048576).toFixed(1) : '?'); };
      const res = await api.InstallUpdate(i.url);
      if (!res || !res.ok) { e.target.disabled = false; e.target.textContent = t('update_retry'); $('#pt', n).textContent = t('update_failed') + (res && res.error ? ' ' + res.error : ''); }
      else e.target.textContent = t('update_installing');
    };
  }
  check();
  return { node: n };
}
let UPDATE_HOOK = null;
let UPD = null;   // approved newer version, if any
function showUpdateBanner(info) {
  if (!info) return;
  UPD = info;
  applyUpdateBadges();
  if (tabCache.settings && tabCache.settings.render && currentTab === 'settings' && !stack.length) tabCache.settings.render();
  if ($('#updBanner') || pref('update_banner_dismissed_code', 0) === info.versionCode) return;
  const b = el(`<div class="updbanner" id="updBanner"><span class="ub-dot"></span><span class="ub-t">${esc(t('upd_banner', info.versionName))}</span>
    <button class="ub-btn">${esc(t('upd_banner_btn'))}</button><span class="ub-x">×</span></div>`);
  // shown once per version: any click hides it for good (updated or not); the update then
  // lives at the bottom of Settings with a dot on the Settings icon
  b.onclick = (e) => {
    setPref('update_banner_dismissed_code', info.versionCode);
    b.classList.add('gone'); setTimeout(() => b.remove(), 350);
    if (e.target.closest('.ub-btn')) pushScreen(UpdateScreen({ ok: true, available: true, info, current: ST.version }));
  };
  document.body.appendChild(b);
}
function applyUpdateBadges() {
  const on = !!UPD;
  $$('.tab[data-t=settings], #setBtn').forEach(x => x.classList.toggle('has-update', on));
}
function updateCard() {
  if (!UPD) return null;
  const c = el(`<div class="updcard"><div class="uc-ic">${icon('ic_refresh')}</div><div class="rt"><div class="title">${esc(t('upd_card_title', UPD.versionName))}</div>
    <div class="sub">${esc(UPD.notes || t('upd_card_sub', ST.version))}</div></div><button class="btn small">${esc(t('upd_banner_btn'))}</button></div>`);
  c.onclick = () => pushScreen(UpdateScreen({ ok: true, available: true, info: UPD, current: ST.version }));
  return c;
}

// ---------- deep link (DeepLinkActivity) ----------
function DeepLinkScreen(link) {
  const n = el(`<div class="screen"><div class="scroll" style="display:flex;flex-direction:column;justify-content:center;text-align:center">
    <img src="assets/app.png" style="width:84px;height:84px;margin:0 auto 16px;border-radius:20px">
    <div id="sp" class="spinner" style="margin:10px auto"></div>
    <div id="tt" style="font-size:20px;font-weight:700"></div><div id="mm" class="hint center" style="font-size:14px"></div>
    <button class="btn" id="ac" style="display:none"></button>
    <div class="hint center" style="margin-top:24px">${t('deeplink_build', ST.version, ST.build)}</div></div></div>`);
  let ok = false;
  async function run() {
    $('#sp', n).style.display = ''; $('#ac', n).style.display = 'none';
    $('#tt', n).textContent = t('deeplink_working_title'); $('#mm', n).textContent = t('deeplink_working_body');
    const r = await api.HandleActivationLink(link);
    $('#sp', n).style.display = 'none'; $('#ac', n).style.display = '';
    ok = !!(r && r.ok);
    if (ok) {
      $('#tt', n).textContent = t('deeplink_done_title'); $('#mm', n).textContent = t('deeplink_done_body', r.account || '');
      $('#ac', n).textContent = t('deeplink_go_connect');
    } else {
      $('#tt', n).textContent = t('deeplink_failed_title'); $('#mm', n).textContent = t((r && r.message) || 'error_network');
      $('#ac', n).textContent = r && r.retry ? t('deeplink_retry') : t('deeplink_continue');
      $('#ac', n).dataset.retry = r && r.retry ? '1' : '';
    }
    await refreshState();
  }
  $('#ac', n).onclick = () => { if (!ok && $('#ac', n).dataset.retry) run(); else { switchTab('home'); } };
  run();
  return { node: n };
}

// ======================= boot =======================
async function boot() {
  try { STR = await (await fetch('strings.json')).json(); } catch (e) { }
  await refreshState();
  applyTheme(); applyLang(); applyDesk();
  window.runtime.EventsOn('status', (s) => { ST = s; const h = tabCache.home; if (h && h.update) h.update(); });
  window.runtime.EventsOn('speed', (s) => HOME_HOOKS.speed && HOME_HOOKS.speed(s));
  window.runtime.EventsOn('ping', (ms) => HOME_HOOKS.ping && HOME_HOOKS.ping(ms));
  window.runtime.EventsOn('toast', (k) => toast(t(k), true));
  window.runtime.EventsOn('update-progress', (p) => UPDATE_HOOK && UPDATE_HOOK(p));
  window.runtime.EventsOn('update-available', (info) => showUpdateBanner(info));
  window.runtime.EventsOn('update-open', () => pushScreen(UpdateScreen()));
  window.runtime.EventsOn('deeplink', (l) => pushScreen(DeepLinkScreen(l)));
  window.runtime.EventsOn('core-exit', () => { refreshState().then(() => { const h = tabCache.home; h && h.update && h.update(); }); });
  switchTab('home');
  setTimeout(() => { const sp = $('#splash'); if (sp) { sp.classList.add('hide'); setTimeout(() => sp.remove(), 500); } }, 1100);
  if (ST.pendingLink) pushScreen(DeepLinkScreen(ST.pendingLink));
}
window.addEventListener('DOMContentLoaded', () => { const w = () => (window.go && window.runtime) ? boot() : setTimeout(w, 30); w(); });
