"""Embedded admin console HTML served at /admin."""

ADMIN_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>OreateAI Gateway</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Segoe UI Variable','Segoe UI','PingFang SC','Hiragino Sans GB','Microsoft YaHei UI',sans-serif;background:#f5f5f7;color:#1d1d1f;padding:0;min-height:100vh}
body.login-mode{overflow:hidden;background:#0f1419}
.nav{background:#fff;border-bottom:1px solid #e5e5e5;padding:16px 32px;display:flex;align-items:center;gap:16px;position:sticky;top:0;z-index:100;animation:slideDown .5s cubic-bezier(.22,1,.36,1)}
.nav h1{font-size:18px;font-weight:600;letter-spacing:-.3px}
.nav a{color:#1d1d1f;text-decoration:none;font-size:14px;padding:6px 16px;border-radius:8px;transition:.2s;cursor:pointer}
.nav a:hover{background:#f0f0f0}
.nav .badge{background:#1d1d1f;color:#fff;font-size:11px;padding:2px 8px;border-radius:12px;margin-left:4px}
.container{max-width:1920px;margin:0 auto;padding:24px 28px;width:100%}
.login-screen{position:fixed;inset:0;z-index:50;display:flex;align-items:center;justify-content:center;padding:24px;overflow:auto;
  background:
    radial-gradient(ellipse 80% 60% at 18% 12%,rgba(56,189,168,.22),transparent 55%),
    radial-gradient(ellipse 70% 50% at 88% 82%,rgba(90,140,200,.18),transparent 50%),
    linear-gradient(160deg,#0f1419 0%,#1a2330 48%,#121820 100%);
  animation:loginFade .55s cubic-bezier(.22,1,.36,1)}
.login-screen::before{content:'';position:absolute;inset:0;pointer-events:none;opacity:.35;
  background-image:linear-gradient(rgba(255,255,255,.035) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.035) 1px,transparent 1px);
  background-size:48px 48px;mask-image:radial-gradient(ellipse 70% 60% at 50% 45%,#000 20%,transparent 75%)}
.login-card{position:relative;width:min(400px,100%);padding:40px 36px 32px;border-radius:20px;
  background:rgba(255,255,255,.94);backdrop-filter:blur(18px);-webkit-backdrop-filter:blur(18px);
  border:1px solid rgba(255,255,255,.55);box-shadow:0 28px 64px rgba(0,0,0,.28),0 2px 8px rgba(0,0,0,.08);
  animation:loginRise .6s cubic-bezier(.22,1,.36,1)}
.login-brand{font-size:28px;font-weight:700;letter-spacing:-.6px;line-height:1.15;color:#0f1419}
.login-tagline{margin-top:8px;margin-bottom:28px;font-size:13px;color:#6e6e73;line-height:1.5}
.login-field{margin-top:14px}
.login-field:first-of-type{margin-top:0}
.login-field label{display:block;font-size:12px;font-weight:500;color:#6e6e73;margin-bottom:6px}
.login-field input{width:100%;font-size:14px;padding:12px 14px;border:1px solid #d8d8dd;border-radius:12px;background:#fafafa;outline:none;transition:border-color .2s,box-shadow .2s,background .2s}
.login-field input:focus{border-color:#1d1d1f;background:#fff;box-shadow:0 0 0 3px rgba(15,20,25,.08)}
.login-actions{margin-top:22px}
.login-actions .btn-primary{width:100%;padding:12px 20px;border-radius:12px;font-size:15px;letter-spacing:.2px}
.login-error{color:#c62828;font-size:12px;margin-top:12px;min-height:16px;text-align:center}
@keyframes loginFade{from{opacity:0}to{opacity:1}}
@keyframes loginRise{from{opacity:0;transform:translateY(18px) scale(.98)}to{opacity:1;transform:translateY(0) scale(1)}}
@media(max-width:480px){
  .login-card{padding:32px 22px 26px;border-radius:16px}
  .login-brand{font-size:24px}
}
.section{background:#fff;border-radius:16px;padding:24px;margin-bottom:20px;box-shadow:0 1px 3px rgba(0,0,0,.04);animation:fadeUp .6s cubic-bezier(.22,1,.36,1)}
.section h2{font-size:15px;font-weight:600;margin-bottom:16px;display:flex;align-items:center;gap:8px}
.row{display:flex;gap:12px;flex-wrap:wrap;align-items:end}
.col{flex:1;min-width:200px}
label{display:block;font-size:12px;color:#86868b;margin-bottom:4px}
input,select,textarea{width:100%;font-size:14px;padding:10px 12px;border:1px solid #d2d2d7;border-radius:10px;background:#fff;transition:.2s;outline:none}
input:focus,select:focus,textarea:focus{border-color:#1d1d1f;box-shadow:0 0 0 3px rgba(0,0,0,.06)}
textarea{min-height:80px;resize:vertical;font-family:inherit}
button{font-size:14px;padding:10px 20px;border:none;border-radius:10px;cursor:pointer;transition:all .25s cubic-bezier(.22,1,.36,1);font-weight:500}
button:active{transform:scale(.96)}
.btn-primary{background:#1d1d1f;color:#fff}.btn-primary:hover{background:#000}
.btn-secondary{background:#f0f0f0;color:#1d1d1f}.btn-secondary:hover{background:#e5e5e5}
.btn-danger{background:#ff3b30;color:#fff}.btn-danger:hover{background:#d62d20}
.btn-provider-active{background:#e8f5e9;color:#1b5e20;border:1px solid #81c784;box-shadow:inset 0 0 0 1px rgba(46,125,50,.12);cursor:default}
.btn-provider-active:hover{background:#e8f5e9}
.btn-sm{padding:6px 14px;font-size:12px;border-radius:8px}
#out-provider-hint.is-active{color:#1b5e20;font-weight:600}
.table-wrap{overflow-x:auto;border-radius:10px;border:1px solid #e5e5e5}
table{width:100%;border-collapse:collapse;font-size:13px}
.accounts-table{min-width:1760px}
th{background:#f5f5f7;padding:10px 12px;text-align:left;font-weight:500;border-bottom:1px solid #e5e5e5;white-space:nowrap}
td{padding:10px 12px;border-bottom:1px solid #f0f0f0;vertical-align:middle}
.accounts-table td{white-space:nowrap}
.accounts-table td.email-cell{max-width:260px;overflow:hidden;text-overflow:ellipsis}
.accounts-table td.password-cell,.accounts-table td.health-cell{white-space:normal}
.accounts-table td.actions-cell{white-space:nowrap;min-width:300px;position:sticky;right:0;background:#fff;box-shadow:-8px 0 12px rgba(0,0,0,.05);z-index:2}
.accounts-table th:last-child{position:sticky;right:0;background:#f5f5f7;min-width:300px;box-shadow:-8px 0 12px rgba(0,0,0,.05);z-index:3}
.accounts-table tr:hover td.actions-cell{background:#fafafa}
.row-actions{display:inline-flex;gap:6px;align-items:center;flex-wrap:nowrap}
tr:last-child td{border-bottom:none}
tr:hover td{background:#fafafa}
.tag{display:inline-block;font-size:11px;padding:2px 8px;border-radius:6px;font-weight:500;white-space:nowrap}
.tag-green{background:#e8f5e9;color:#2e7d32}
.tag-red{background:#ffebee;color:#c62828}
.tag-gray{background:#f5f5f5;color:#616161}
.tag-blue{background:#e3f2fd;color:#1565c0}
.copy-btn{display:inline-flex;align-items:center;gap:4px;cursor:pointer;font-size:11px;padding:3px 10px;border-radius:6px;background:#f0f0f0;border:none;transition:.15s}
.copy-btn:hover{background:#e0e0e0}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin-bottom:0}
.stat-card{background:#f5f5f7;border-radius:12px;padding:16px;text-align:center}
.stat-card .num{font-size:28px;font-weight:700;letter-spacing:-.5px}
.stat-card .label{font-size:12px;color:#86868b;margin-top:2px}
.pool-capacity-panel{border:1px solid #e5e5e5;background:#fafafa;border-radius:12px;padding:14px;margin-bottom:16px}
.pool-capacity-panel .stats{grid-template-columns:repeat(auto-fit,minmax(150px,1fr))}
.pool-capacity-note{font-size:12px;color:#6e6e73;line-height:1.65;margin-top:10px}
.avail-filters{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:0 0 14px}
.avail-chip{border:1px solid #d2d2d7;background:#fff;border-radius:999px;padding:6px 12px;font-size:12px;cursor:pointer}
.avail-chip.active{background:#1d1d1f;border-color:#1d1d1f;color:#fff}
.avail-group{border:1px solid #e5e5e5;border-radius:12px;margin-bottom:10px;overflow:hidden;background:#fff}
.avail-group-head{display:flex;justify-content:space-between;gap:12px;align-items:center;padding:12px 14px;cursor:pointer}
.avail-group-head:hover{background:#fafafa}
.avail-group-title{display:flex;flex-wrap:wrap;gap:8px;align-items:center;font-weight:600}
.avail-tag{font-size:11px;color:#6e6e73;border:1px solid #e5e5e5;border-radius:999px;padding:2px 8px;font-weight:500}
.avail-meta{font-size:12px;color:#6e6e73}
.avail-combos{display:none;border-top:1px solid #eee}
.avail-group.open .avail-combos{display:block}
.avail-row{display:grid;grid-template-columns:1.1fr 1.3fr .6fr .7fr .7fr .7fr;gap:8px;padding:10px 14px;border-top:1px solid #f2f2f2;font-size:12px;align-items:center}
.avail-row:first-child{border-top:0}
.avail-pill{display:inline-flex;min-width:52px;justify-content:center;padding:2px 8px;border-radius:999px;font-weight:600}
.avail-pill.available{background:#e8f7ee;color:#0f7a45}
.avail-pill.tight{background:#fff6dd;color:#9a6700}
.avail-pill.unavailable{background:#eef0f3;color:#6b7280}
.avail-note{font-size:12px;color:#6e6e73;margin-top:10px;line-height:1.6}
@media (max-width:900px){.avail-row{grid-template-columns:1fr 1fr;}}
.reserve-target-editor{display:flex;align-items:center;gap:6px;min-width:150px}
.reserve-target-editor input{width:86px;padding:6px 8px;font-size:12px}
.point-value{font-variant-numeric:tabular-nums;white-space:nowrap}
.point-value small{display:block;color:#86868b;font-size:11px;margin-top:3px}
.endpoint-box{background:#f5f5f7;border-radius:10px;padding:12px 16px;margin-bottom:12px;font-family:monospace;font-size:13px}
.endpoint-box .url{font-weight:600;color:#1d1d1f}
.endpoint-box .desc{font-size:12px;color:#86868b;margin-top:2px}
.task-preview-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}
.task-preview-card{background:#f5f5f7;border-radius:12px;padding:12px}
.task-preview-card h3{font-size:13px;font-weight:600;margin-bottom:8px}
.task-preview-meta{font-size:12px;line-height:1.65;color:#3a3a3c;word-break:break-word}
.task-preview-assets{display:flex;flex-direction:column;gap:8px}
.task-preview-media{max-width:100%;border-radius:10px;border:1px solid #e5e5e5;background:#000}
.clean-asset-shell{display:flex;flex-direction:column;gap:8px}
.clean-asset-status{font-size:12px;color:#6e6e73;min-height:18px}
.clean-asset-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.clean-asset-actions a{font-size:12px;color:#1565c0;text-decoration:none}
.clean-asset-actions a:hover{text-decoration:underline}
.generation-result{margin-top:12px;min-height:0}
.generation-result-card{background:#f5f5f7;border:1px solid #e5e5e5;border-radius:12px;padding:14px}
.generation-result-title{display:flex;align-items:center;justify-content:space-between;gap:12px;font-size:14px;font-weight:600}
.generation-result-meta{margin-top:6px;color:#6e6e73;font-size:12px;line-height:1.6}
.generation-result-assets{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px;margin-top:12px}
.generation-result-assets .task-preview-media{width:100%;max-height:520px;object-fit:contain}
.task-actions{display:flex;flex-wrap:wrap;gap:6px}
.list-filters{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:12px}
.list-filter-actions{display:flex;gap:8px;align-items:end;flex-wrap:wrap;margin-bottom:12px}
.list-pagination{display:flex;align-items:center;justify-content:flex-end;gap:8px;margin-top:12px;min-height:32px}
.list-status{font-size:12px;color:#6e6e73;margin-right:auto}
.list-status.error{color:#c62828}
.apikey-shell{display:flex;flex-direction:column;gap:18px}
.apikey-header{display:flex;align-items:flex-start;justify-content:space-between;gap:16px}
.apikey-header h2{margin-bottom:5px}
.apikey-subtitle{font-size:13px;color:#6e6e73;line-height:1.6}
.apikey-tabs{display:flex;gap:6px;padding:4px;background:#f5f5f7;border-radius:10px;width:max-content}
.apikey-tab{padding:7px 14px;background:transparent;color:#6e6e73;border-radius:7px;font-size:13px}
.apikey-tab.active{background:#fff;color:#1d1d1f;box-shadow:0 1px 4px rgba(0,0,0,.08)}
.apikey-summary{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}
.apikey-summary-card{border:1px solid #e5e5e5;border-radius:12px;padding:14px 16px;background:#fafafa}
.apikey-summary-card strong{display:block;font-size:22px;line-height:1.2}
.apikey-summary-card span{display:block;margin-top:4px;color:#86868b;font-size:12px}
.apikey-toolbar{display:grid;grid-template-columns:minmax(220px,1fr) 170px auto;gap:10px;align-items:end}
.apikey-name{font-weight:600;color:#1d1d1f}
.apikey-meta{font-size:11px;color:#86868b;margin-top:4px;line-height:1.45}
.apikey-value{display:flex;align-items:center;gap:8px}
.apikey-value code{font-size:11px;color:#3a3a3c;background:#f5f5f7;padding:5px 8px;border-radius:6px}
.apikey-actions{display:flex;gap:6px;white-space:nowrap}
.quota-cell{min-width:155px}
.quota-main{font-weight:600;font-variant-numeric:tabular-nums}
.quota-sub{font-size:11px;color:#86868b;margin-top:3px}
.quota-track{height:5px;background:#ececf0;border-radius:999px;margin-top:7px;overflow:hidden}
.quota-fill{height:100%;background:#1d1d1f;border-radius:999px;transition:width .25s}
.scope-summary{font-size:12px;line-height:1.55;max-width:190px;color:#3a3a3c}
.empty-state{text-align:center;padding:42px 16px;color:#86868b}
.drawer-backdrop{position:fixed;inset:0;background:rgba(0,0,0,.32);z-index:190;opacity:1;transition:opacity .2s}
.drawer{position:fixed;top:0;right:0;width:min(520px,100vw);height:100vh;background:#fff;z-index:200;box-shadow:-16px 0 40px rgba(0,0,0,.14);display:flex;flex-direction:column;animation:drawerIn .25s cubic-bezier(.22,1,.36,1)}
.drawer-header{display:flex;justify-content:space-between;gap:16px;padding:22px 24px;border-bottom:1px solid #e5e5e5}
.drawer-header h3{font-size:18px;margin-bottom:4px}
.drawer-header p{font-size:12px;color:#86868b}
.drawer-close{width:34px;height:34px;padding:0;border-radius:50%;background:#f0f0f0;font-size:20px;line-height:1}
.drawer-body{padding:22px 24px;overflow:auto;flex:1}
.drawer-section{padding-bottom:22px;margin-bottom:22px;border-bottom:1px solid #ededf0}
.drawer-section:last-child{border-bottom:none;margin-bottom:0}
.drawer-section-title{font-size:14px;font-weight:600;margin-bottom:12px}
.drawer-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.drawer-help{font-size:11px;color:#86868b;line-height:1.55;margin-top:6px}
.drawer-switches{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.drawer-switch{display:flex;align-items:center;gap:9px;border:1px solid #e5e5e5;border-radius:10px;padding:11px 12px;color:#3a3a3c;font-size:13px}
.drawer-switch input{width:auto}
.drawer-footer{display:flex;justify-content:flex-end;gap:8px;padding:16px 24px;border-top:1px solid #e5e5e5;background:#fff}
.secret-card{background:#f5f5f7;border:1px solid #e5e5e5;border-radius:12px;padding:14px;margin-top:12px}
.secret-card code{display:block;word-break:break-all;font-size:12px;line-height:1.6;margin-bottom:10px}
.operations-stack{display:flex;flex-direction:column;gap:24px}
.registration-progress{border:1px solid #e5e5e5;background:#fafafa;border-radius:12px;padding:14px;margin:0 0 16px}
.registration-progress-head{display:flex;align-items:center;justify-content:space-between;gap:12px;font-size:13px;font-weight:600}
.registration-track{height:8px;background:#e8e8ed;border-radius:999px;overflow:hidden;margin:10px 0}
.registration-fill{height:100%;background:#1d1d1f;border-radius:999px;transition:width .3s}
.registration-meta{font-size:12px;color:#6e6e73;line-height:1.7}
.registration-results{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
.reg-console-controls{display:flex;gap:12px;flex-wrap:wrap;align-items:end;margin-bottom:16px}
.reg-console-controls .col{flex:0 0 140px;min-width:120px}
.reg-console-hint{font-size:12px;color:#86868b;align-self:center;padding-bottom:10px}
.reg-pipeline{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0 12px}
.reg-pipeline-step{font-size:11px;padding:5px 10px;border-radius:999px;background:#ececef;color:#6e6e73}
.reg-pipeline-step.active{background:#1d1d1f;color:#fff}
.reg-pipeline-step.done{background:#e8f5e9;color:#2e7d32}
.reg-event-log{max-height:240px;overflow:auto;border:1px solid #e8e8ed;border-radius:10px;background:#fff;padding:8px 10px;font-size:12px;line-height:1.55}
.reg-event-row{display:grid;grid-template-columns:64px minmax(120px,1.2fr) minmax(140px,1.6fr);gap:8px;padding:4px 0;border-bottom:1px solid #f3f3f4}
.reg-event-row:last-child{border-bottom:none}
.reg-event-row.level-success{color:#2e7d32}
.reg-event-row.level-error{color:#c62828}
.reg-event-time{color:#86868b;font-variant-numeric:tabular-nums}
.reg-event-email{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;word-break:break-all}
.reg-result-banner{display:flex;align-items:center;justify-content:space-between;gap:12px;border-radius:10px;padding:10px 12px;margin:0 0 12px;font-size:13px}
.reg-result-banner.ok{background:#e8f5e9;color:#1b5e20}
.reg-result-banner.warn{background:#fff8e1;color:#8d6e00}
.reg-result-banner.err{background:#ffebee;color:#b71c1c}
.reg-result-banner button{border:none;background:transparent;color:inherit;cursor:pointer;font-size:12px;text-decoration:underline}
.toast-host{position:fixed;right:20px;bottom:20px;z-index:1000;display:flex;flex-direction:column;gap:8px;max-width:360px;pointer-events:none}
.toast{pointer-events:auto;background:#1d1d1f;color:#fff;border-radius:10px;padding:12px 14px;font-size:13px;line-height:1.45;box-shadow:0 8px 24px rgba(0,0,0,.18);animation:fadeUp .25s ease}
.toast.ok{background:#2e7d32}
.toast.warn{background:#8d6e00}
.toast.err{background:#c62828}
.confirm-backdrop{position:fixed;inset:0;z-index:1100;background:rgba(15,20,25,.42);display:flex;align-items:center;justify-content:center;padding:20px;animation:loginFade .18s ease}
.confirm-dialog{width:min(420px,100%);background:#fff;border-radius:16px;padding:22px 22px 18px;border:1px solid #e5e5e5;box-shadow:0 18px 48px rgba(0,0,0,.18);animation:loginRise .22s cubic-bezier(.22,1,.36,1)}
.confirm-message{font-size:14px;line-height:1.65;color:#1d1d1f;margin:0 0 18px;white-space:pre-wrap}
.confirm-actions{display:flex;justify-content:flex-end;gap:8px}
.password-cell{min-width:190px}
.password-value{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:12px;word-break:break-all}
.password-actions{display:flex;gap:5px;margin-top:6px}
.docs-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:14px}
.docs-card{border:1px solid #e5e5e5;border-radius:12px;padding:16px;background:#fafafa}
.docs-card h3{font-size:14px;margin-bottom:8px}
.docs-card p,.docs-card li{font-size:12px;color:#5f5f64;line-height:1.75}
.docs-card ul{padding-left:18px}
.docs-code-head{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-top:12px}
.docs-code-head strong{font-size:12px}
.docs-card pre{max-height:none;white-space:pre-wrap;word-break:break-word;margin-top:6px;background:#fff}
.docs-flow{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:12px 0}
.docs-flow span{font-size:12px;background:#f0f0f0;border-radius:999px;padding:6px 10px}
@keyframes drawerIn{from{transform:translateX(24px);opacity:.4}to{transform:translateX(0);opacity:1}}
@media(max-width:760px){
  .container{padding:16px}
  .nav{padding:12px 16px;overflow-x:auto}
  .apikey-header{flex-direction:column}
  .apikey-summary{grid-template-columns:1fr}
  .apikey-toolbar{grid-template-columns:1fr}
  .drawer{width:100vw}
  .drawer-grid,.drawer-switches{grid-template-columns:1fr}
  .apikey-actions{flex-wrap:wrap}
}
button:disabled{cursor:not-allowed;opacity:.5;transform:none}
.hidden{display:none!important}
@keyframes fadeUp{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:translateY(0)}}
@keyframes slideDown{from{opacity:0;transform:translateY(-12px)}to{opacity:1;transform:translateY(0)}}
.msg{padding:8px 12px;margin-bottom:8px;border-radius:8px;animation:fadeUp .3s cubic-bezier(.22,1,.36,1)}.msg-user{background:#e8f5e9;text-align:right}.msg-assistant{background:#f0f0f0}.msg strong{font-weight:600;font-size:12px;color:#666}pre{background:#fafafa;border:1px solid #eee;padding:12px;border-radius:10px;overflow:auto;font-size:12px;max-height:300px}
</style>
</head>
<body>

<div id="login-panel" class="login-screen hidden">
  <div class="login-card">
    <div class="login-brand">OreateAI</div>
    <p class="login-tagline">Gateway 管理控制台</p>
    <form onsubmit="event.preventDefault();adminLogin()">
      <div class="login-field"><label for="login-user">用户名</label><input id="login-user" autocomplete="username" value="admin"></div>
      <div class="login-field"><label for="login-pass">密码</label><input id="login-pass" type="password" autocomplete="current-password"></div>
      <div class="login-actions"><button type="submit" class="btn-primary">登录</button></div>
      <div id="login-error" class="login-error" role="alert"></div>
    </form>
  </div>
</div>

<div id="app-shell" class="hidden">
<div class="nav">
  <h1>OreateAI Gateway</h1>
  <a onclick="switchTab('pool')">号池 <span class="badge" id="pool-count">0</span></a>
  <a onclick="switchTab('models')">可用模型</a>
  <a onclick="switchTab('outlook')">Out 邮箱 <span class="badge" id="outlook-count">0</span></a>
  <a onclick="switchTab('generate')">生成</a>
  <a onclick="switchTab('tasks')">任务</a>
  <a onclick="switchTab('apikeys')">API Keys</a>
  <a onclick="switchTab('docs')">API 文档</a>
  <a onclick="switchTab('settings')">设置</a>
  <span style="flex:1"></span>
  <span style="font-size:12px;color:#86868b" id="status-text">就绪</span>
  <button class="btn-secondary btn-sm" onclick="logout()">退出</button>
</div>

<div class="container">
<div id="toast-host" class="toast-host" aria-live="polite"></div>

<div class="stats">
  <div class="stat-card"><div class="num" id="st-total">-</div><div class="label">总账号</div></div>
  <div class="stat-card"><div class="num" id="st-verified">-</div><div class="label">可用</div></div>
  <div class="stat-card"><div class="num" id="st-tasks">-</div><div class="label">任务数</div></div>
  <div class="stat-card"><div class="num" id="st-apikeys">-</div><div class="label">API Keys</div></div>
</div>

<!-- Tab: 号池 -->
<div id="tab-pool" class="section">
  <h2>📋 号池管理</h2>
  <div class="pool-capacity-panel">
    <div class="stats">
      <div class="stat-card"><div class="num" id="capacity-total-points">-</div><div class="label">已知总积分</div></div>
      <div class="stat-card"><div class="num" id="capacity-reserved-points">-</div><div class="label">活动任务预留</div></div>
      <div class="stat-card"><div class="num" id="capacity-max-available">-</div><div class="label">单号最高可用</div></div>
      <div class="stat-card"><div class="num" id="capacity-tier-455">-</div><div class="label">455 点任务容量</div></div>
    </div>
    <div id="pool-capacity-note" class="pool-capacity-note">正在加载积分容量…</div>
  </div>
  <div class="reg-console-controls">
    <div class="col"><label>注册数量</label><input id="reg_count" type="number" min="1" max="50" step="1" value="1"></div>
    <div><button id="reg-start" class="btn-primary" onclick="startRegistrationFromControls()">开始注册</button></div>
    <div><button id="maintenance-start" class="btn-secondary" onclick="maintainPool()">体检并补号</button></div>
    <div><button class="btn-danger" onclick="purgeZombieAccounts()">清理僵尸号</button></div>
    <div><button class="btn-secondary" onclick="toggleImport()">导入账号</button></div>
    <div><button class="btn-secondary" onclick="switchTab('outlook')">Out 邮箱管理</button></div>
    <div id="reg-concurrency-hint" class="reg-console-hint">并发：-</div>
    <div id="outlook-pool-hint" class="reg-console-hint">Outlook 池：-</div>
  </div>
  <div id="registration-result-banner" class="reg-result-banner hidden"></div>
  <div id="maintenance-result-banner" class="reg-result-banner hidden"></div>
  <div id="registration-progress" class="registration-progress hidden"></div>
  <div id="maintenance-progress" class="registration-progress hidden"></div>
  <div id="import-area" class="hidden" style="margin-bottom:12px">
    <div class="row">
      <div class="col"><input id="imp-email" placeholder="邮箱"></div>
      <div class="col"><input id="imp-pwd" placeholder="密码"></div>
      <div><button class="btn-primary" onclick="doImport()">导入</button></div>
      <div><button class="btn-secondary" onclick="document.getElementById('import-area').classList.add('hidden')">取消</button></div>
    </div>
  </div>
  <div class="table-wrap">
    <table class="accounts-table">
      <thead><tr>
        <th>ID</th><th>邮箱</th><th>密码</th><th>状态</th><th>健康</th><th>来源</th><th>OUID</th><th>余额 / 可用</th><th>活动任务预留</th><th>储备目标</th><th>更新时间</th><th>创建时间</th><th>操作</th>
      </tr></thead>
      <tbody id="accounts-tbody"></tbody>
    </table>
  </div>
</div>

<!-- Tab: 可用模型 -->
<div id="tab-models" class="section hidden">
  <h2>可用模型</h2>
  <p class="reg-console-hint" style="margin:0 0 12px">按上游价目展开组合，并用号池单账号可花积分判断能否接单。用户公开页：<a href="/models" target="_blank" rel="noopener">/models</a></p>
  <div class="pool-capacity-panel">
    <div class="stats">
      <div class="stat-card"><div class="num" id="avail-known">-</div><div class="label">已知余额账号</div></div>
      <div class="stat-card"><div class="num" id="avail-max">-</div><div class="label">单号最高可用</div></div>
      <div class="stat-card"><div class="num" id="avail-reserved">-</div><div class="label">活动预留</div></div>
      <div class="stat-card"><div class="num" id="avail-combo-count">-</div><div class="label">展示组合数</div></div>
    </div>
  </div>
  <div class="avail-filters">
    <button class="avail-chip active" data-avail-kind="all" onclick="setAvailKind('all')">全部</button>
    <button class="avail-chip" data-avail-kind="image" onclick="setAvailKind('image')">图片</button>
    <button class="avail-chip" data-avail-kind="video" onclick="setAvailKind('video')">视频</button>
    <select id="avail-scene" class="avail-chip" onchange="renderModelAvailability()"></select>
    <button class="avail-chip" id="avail-only-ok" onclick="toggleAvailOnlyOk()">仅可用</button>
    <button class="avail-chip" id="avail-show-exp" onclick="toggleAvailShowExp()">显示未验证</button>
    <button class="avail-chip" id="avail-include-disabled" onclick="toggleAvailIncludeDisabled()">含已关闭策略</button>
    <button class="btn-secondary btn-sm" onclick="loadModelAvailability()">刷新</button>
  </div>
  <div id="avail-catalog"><div class="reg-console-hint">加载中…</div></div>
  <p class="avail-note">可用性以单账号能否接单为准（不是总积分÷单价）。公开展示页不暴露账号数与容量数字。</p>
</div>

<!-- Tab: Out 邮箱 -->
<div id="tab-outlook" class="section hidden">
  <h2>📬 Out 邮箱管理</h2>
  <p class="reg-console-hint" style="margin:0 0 12px">导入 Outlook/Hotmail 卡密到邮箱池，供注册任务取用。支持 txt 自动识别，也可粘贴。</p>
  <div class="stats" style="margin-bottom:14px">
    <div class="stat-card"><div class="num" id="out-st-available">-</div><div class="label">可用</div></div>
    <div class="stat-card"><div class="num" id="out-st-leased">-</div><div class="label">占用中</div></div>
    <div class="stat-card"><div class="num" id="out-st-used">-</div><div class="label">已使用</div></div>
    <div class="stat-card"><div class="num" id="out-st-error">-</div><div class="label">异常</div></div>
    <div class="stat-card"><div class="num" id="out-st-total">-</div><div class="label">总计</div></div>
  </div>
  <div class="row" style="margin-bottom:12px;align-items:center;gap:8px;flex-wrap:wrap">
    <div id="out-provider-hint" class="reg-console-hint">注册源：-</div>
    <div><button id="out-use-provider-btn" class="btn-primary" onclick="useOutlookForRegistration()">设为注册邮箱源</button></div>
    <div><button class="btn-secondary" onclick="loadOutlookMailboxes()">刷新列表</button></div>
    <div><button class="btn-secondary" onclick="purgeOutlookMailboxes(['used','error','disabled'], true)">清理已用/异常/已注册</button></div>
  </div>
  <div class="row" style="margin-bottom:12px;align-items:flex-end;gap:8px;flex-wrap:wrap">
    <div class="col" style="min-width:220px">
      <label>导入卡密 txt</label>
      <input id="outlook-import-file" type="file" accept=".txt,text/plain,.csv" onchange="importOutlookMailFile(this)">
    </div>
    <div id="outlook-import-filename" class="reg-console-hint"></div>
    <div id="outlook-import-result" class="reg-console-hint"></div>
  </div>
  <details style="margin-bottom:14px">
    <summary style="cursor:pointer;font-size:13px;color:#6e6e73">粘贴导入（可选）</summary>
    <div style="margin-top:8px">
      <textarea id="outlook-import-text" rows="5" style="width:100%;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px" placeholder="支持：&#10;邮箱----密码----client_id----refresh_token&#10;邮箱----密码----https://host/api/mail-new?refresh_token=...&client_id=...&#10;http://host/get?key=xxx&email=邮箱----密码----client_id----refresh_token"></textarea>
      <div class="row" style="margin-top:8px">
        <div><button class="btn-primary" onclick="importOutlookMailboxes()">导入粘贴内容</button></div>
      </div>
    </div>
  </details>
  <div class="row" style="margin-bottom:10px;gap:8px;flex-wrap:wrap;align-items:flex-end">
    <div class="col"><label>状态</label>
      <select id="out-filter-status" onchange="loadOutlookMailboxes()">
        <option value="all">全部</option>
        <option value="available">可用</option>
        <option value="leased">占用中</option>
        <option value="used">已使用</option>
        <option value="error">异常</option>
        <option value="disabled">禁用</option>
      </select>
    </div>
    <div class="col" style="flex:2"><label>搜索邮箱</label><input id="out-filter-q" placeholder="邮箱关键词" onkeydown="if(event.key==='Enter')loadOutlookMailboxes()"></div>
    <div><button class="btn-secondary" onclick="loadOutlookMailboxes()">筛选</button></div>
  </div>
  <div class="table-wrap">
    <table class="accounts-table">
      <thead><tr>
        <th>ID</th><th>邮箱</th><th>密码</th><th>状态</th><th>Client ID</th><th>错误</th><th>领取时间</th><th>使用时间</th><th>更新时间</th><th>操作</th>
      </tr></thead>
      <tbody id="outlook-tbody"><tr><td colspan="10" style="color:#86868b">加载中…</td></tr></tbody>
    </table>
  </div>
</div>

<!-- Tab: 生成 -->
<div id="tab-generate" class="section hidden">
  <h2>🎨 图片 / 🎬 视频 生成</h2>
  <div style="margin-bottom:16px">
    <div class="endpoint-box">
      <div class="url">POST <span id="gw-url">/v1/generate</span></div>
      <div class="desc">Authorization: Bearer &lt;API Key&gt; &nbsp;|&nbsp; Content-Type: application/json</div>
    </div>
    <div style="font-size:12px;color:#86868b;margin-top:4px">
      示例: <code id="gw-example">curl -H "Authorization: Bearer &lt;key&gt;" -H "Content-Type: application/json" -d '{"kind":"image","prompt":"hello"}' http://localhost:8894/v1/generate</code>
      <button class="copy-btn" onclick="copyExample()" style="margin-left:4px">复制</button>
    </div>
  </div>
  <div class="row">
    <div class="col"><label>类型</label><select id="g-kind" onchange="applyGenerateOptions()"><option value="image">图片</option><option value="video">视频</option></select></div>
    <div class="col"><label>账号ID（留空自动分配）</label><input id="g-account" placeholder="auto"></div>
    <div class="col"><label>模型</label><select id="g-model" onchange="applyModelOptions()"></select></div>
    <div class="col"><label>比例</label><select id="g-ratio"></select></div>
  </div>
  <div class="row">
    <div class="col"><label>分辨率</label><select id="g-res"></select></div>
    <div class="col"><label>视频时长</label><select id="g-dur"></select></div>
    <div class="col"><label>视频场景</label><select id="g-scene"></select></div>
  </div>
  <div id="g-model-desc" style="font-size:12px;color:#6e6e73;margin-top:8px"></div>
  <div style="margin-top:12px"><label>描述词</label><textarea id="g-prompt" placeholder="请输入描述词..."></textarea></div>
  <div style="margin-top:12px;display:flex;gap:8px">
    <button id="g-submit" class="btn-primary" onclick="gatewayGenerate()">提交生成</button>
    <button class="btn-secondary" onclick="document.getElementById('g-result').innerHTML=''">清空</button>
  </div>
  <div id="g-result" class="generation-result"></div>
</div>

<!-- Tab: 任务 -->
<div id="tab-tasks" class="section hidden">
  <h2>📦 任务列表</h2>
  <div class="list-filters">
    <div><label>状态</label><select id="task-filter-status"><option value="">全部</option><option value="queued">待处理</option><option value="running">生成中</option><option value="submitted">已提交</option><option value="hydrating">获取结果中</option><option value="completed">已完成</option><option value="failed">失败</option><option value="expired">已过期</option><option value="cancelled">已取消</option></select></div>
    <div><label>类型</label><select id="task-filter-kind"><option value="">全部</option><option value="image">图片</option><option value="video">视频</option></select></div>
    <div><label>模型</label><input id="task-filter-model-name" placeholder="模型名"></div>
    <div><label>场景</label><input id="task-filter-scene-id" placeholder="scene_id"></div>
    <div><label>客户 ID</label><input id="task-filter-client-id" type="number" min="1" step="1"></div>
    <div><label>API Key ID</label><input id="task-filter-api-key-id" type="number" min="1" step="1"></div>
    <div><label>账号 ID</label><input id="task-filter-account-id" type="number" min="1" step="1"></div>
    <div><label>错误码</label><input id="task-filter-error-code" placeholder="error_code"></div>
    <div><label>开始日期</label><input id="task-filter-date-from" type="date"></div>
    <div><label>结束日期</label><input id="task-filter-date-to" type="date"></div>
    <div><label>每页</label><select id="task-page-size"><option value="25">25</option><option value="50" selected>50</option><option value="100">100</option><option value="200">200</option></select></div>
  </div>
  <div class="list-filter-actions">
    <button class="btn-primary btn-sm" onclick="applyTaskFilters()">应用筛选</button>
    <button class="btn-secondary btn-sm" onclick="resetTaskFilters()">重置</button>
    <button class="btn-secondary btn-sm" onclick="void loadTasks().catch(()=>{})">刷新</button>
  </div>
  <div class="table-wrap">
    <table>
      <thead><tr><th>ID</th><th>类型</th><th>账号</th><th>状态</th><th>提示词</th><th>chatId</th><th>时间</th><th>操作</th></tr></thead>
      <tbody id="tasks-tbody"></tbody>
    </table>
  </div>
  <div class="list-pagination">
    <span id="tasks-list-status" class="list-status"></span>
    <button id="tasks-prev" class="btn-secondary btn-sm" onclick="previousTaskPage()">上一页</button>
    <button id="tasks-next" class="btn-secondary btn-sm" onclick="nextTaskPage()">下一页</button>
  </div>
  <div id="task-preview" class="section hidden" style="margin-top:16px">
    <h2>🔎 任务详情</h2>
    <div id="task-preview-body" class="task-preview-grid"></div>
  </div>
</div>

<!-- Tab: API Keys -->
<div id="tab-apikeys" class="section hidden">
  <div class="apikey-shell">
    <div class="apikey-header">
      <div>
        <h2>API Key 管理</h2>
        <div class="apikey-subtitle">一个 Key 对应一个客户，可独立设置额度、访问范围和启用状态。</div>
      </div>
      <div class="apikey-tabs" role="tablist" aria-label="API Key 页面">
        <button id="apikey-tab-keys" class="apikey-tab active" onclick="switchApiKeyPanel('keys')" role="tab">Key 管理</button>
        <button id="apikey-tab-operations" class="apikey-tab" onclick="switchApiKeyPanel('operations')" role="tab">调用日志</button>
      </div>
    </div>

    <div id="apikeys-key-panel">
      <div class="apikey-summary">
        <div class="apikey-summary-card"><strong id="ak-summary-total">0</strong><span>客户 / Key 总数</span></div>
        <div class="apikey-summary-card"><strong id="ak-summary-enabled">0</strong><span>当前启用</span></div>
        <div class="apikey-summary-card"><strong id="ak-summary-usage">0</strong><span>今日用量（点数）</span></div>
      </div>
      <div class="endpoint-box" style="margin-top:14px">
        <div class="url">POST /v1/generate</div>
        <div class="desc">请求头：<code>Authorization: Bearer &lt;API Key&gt;</code></div>
      </div>
      <div class="apikey-toolbar">
        <div><label>搜索客户或 Key</label><input id="ak-search" placeholder="客户名称、Key 前缀" oninput="renderApiKeys()"></div>
        <div><label>状态</label><select id="ak-status-filter" onchange="renderApiKeys()"><option value="">全部状态</option><option value="enabled">启用</option><option value="disabled">停用</option><option value="expired">已过期</option><option value="deleted">已删除</option></select></div>
        <button class="btn-primary" onclick="openApiKeyEditor()">创建客户 Key</button>
      </div>
      <div class="table-wrap" style="margin-top:14px">
        <table>
          <thead><tr><th>客户</th><th>API Key</th><th>状态</th><th>今日额度</th><th>访问范围</th><th>最后使用</th><th>操作</th></tr></thead>
          <tbody id="apikeys-tbody"></tbody>
        </table>
      </div>
    </div>

    <div id="apikeys-operations-panel" class="hidden">
      <div class="operations-stack">
        <div>
          <h2>用量日志</h2>
          <div class="list-filters">
            <div><label>类型</label><select id="usage-filter-kind"><option value="">全部</option><option value="image">图片</option><option value="video">视频</option></select></div>
            <div><label>状态</label><select id="usage-filter-status"><option value="">全部</option><option value="queued">待处理</option><option value="running">生成中</option><option value="submitted">已提交</option><option value="hydrating">获取结果中</option><option value="completed">已完成</option><option value="failed">失败</option><option value="expired">已过期</option><option value="cancelled">已取消</option></select></div>
            <div><label>模型</label><input id="usage-filter-model-name" placeholder="模型名"></div>
            <div><label>客户 Key ID</label><input id="usage-filter-api-key-id" type="number" min="1" step="1"></div>
            <div><label>账号 ID</label><input id="usage-filter-account-id" type="number" min="1" step="1"></div>
            <div><label>错误码</label><input id="usage-filter-error-code" placeholder="error_code"></div>
            <div><label>开始日期</label><input id="usage-filter-date-from" type="date"></div>
            <div><label>结束日期</label><input id="usage-filter-date-to" type="date"></div>
            <div><label>每页</label><select id="usage-page-size"><option value="25">25</option><option value="50" selected>50</option><option value="100">100</option><option value="200">200</option></select></div>
          </div>
          <div class="list-filter-actions">
            <button class="btn-primary btn-sm" onclick="applyUsageFilters()">应用筛选</button>
            <button class="btn-secondary btn-sm" onclick="resetUsageFilters()">重置</button>
            <button class="btn-secondary btn-sm" onclick="void loadUsage().catch(()=>{})">刷新</button>
          </div>
          <div class="table-wrap">
            <table><thead><tr><th>ID</th><th>类型</th><th>账号</th><th>模型</th><th>点数</th><th>错误码</th><th>状态</th><th>提示词</th><th>时间</th></tr></thead><tbody id="usage-tbody"></tbody></table>
          </div>
          <div class="list-pagination"><span id="usage-list-status" class="list-status"></span><button id="usage-prev" class="btn-secondary btn-sm" onclick="previousUsagePage()">上一页</button><button id="usage-next" class="btn-secondary btn-sm" onclick="nextUsagePage()">下一页</button></div>
        </div>

        <div>
          <h2>上传素材</h2>
          <div class="list-filters">
            <div><label>类型</label><select id="upload-filter-kind"><option value="">全部</option><option value="image">图片</option><option value="video">视频</option></select></div>
            <div><label>状态</label><select id="upload-filter-status"><option value="">全部</option><option value="pending">待处理</option><option value="uploading">上传中</option><option value="completed">已完成</option><option value="failed">失败</option><option value="deleted">已删除</option></select></div>
            <div><label>客户 Key ID</label><input id="upload-filter-api-key-id" type="number" min="1" step="1"></div>
            <div><label>账号 ID</label><input id="upload-filter-account-id" type="number" min="1" step="1"></div>
            <div><label>开始日期</label><input id="upload-filter-date-from" type="date"></div>
            <div><label>结束日期</label><input id="upload-filter-date-to" type="date"></div>
            <div><label>每页</label><select id="upload-page-size"><option value="25">25</option><option value="50" selected>50</option><option value="100">100</option><option value="200">200</option></select></div>
          </div>
          <div class="list-filter-actions">
            <button class="btn-primary btn-sm" onclick="applyUploadFilters()">应用筛选</button>
            <button class="btn-secondary btn-sm" onclick="resetUploadFilters()">重置</button>
            <button class="btn-secondary btn-sm" onclick="void loadUploads().catch(()=>{})">刷新</button>
          </div>
          <div class="table-wrap">
            <table><thead><tr><th>ID</th><th>类型</th><th>账号</th><th>Key</th><th>文件</th><th>Object</th><th>关联任务</th><th>状态</th><th>时间</th><th>操作</th></tr></thead><tbody id="uploads-tbody"></tbody></table>
          </div>
          <div class="list-pagination"><span id="uploads-list-status" class="list-status"></span><button id="uploads-prev" class="btn-secondary btn-sm" onclick="previousUploadPage()">上一页</button><button id="uploads-next" class="btn-secondary btn-sm" onclick="nextUploadPage()">下一页</button></div>
        </div>

        <div>
          <h2>成本报表</h2>
          <div class="row" style="margin-bottom:16px">
            <div class="col"><label>开始日期</label><input id="cost-date-from" type="date"></div>
            <div class="col"><label>结束日期</label><input id="cost-date-to" type="date"></div>
            <div class="col"><label>模型</label><input id="cost-model-name" placeholder="模型名（可选）"></div>
            <div class="col" style="align-self:end"><button class="btn-secondary" onclick="loadCostReport()">刷新报表</button></div>
          </div>
          <div class="table-wrap">
            <table><thead><tr><th>日期</th><th>客户 / Key</th><th>账号</th><th>模型</th><th>请求数</th><th>预估点数</th><th>实际点数</th><th>成功扣点</th><th>失败扣点</th></tr></thead><tbody id="cost-report-tbody"></tbody></table>
          </div>
        </div>
      </div>
    </div>
  </div>

  <div id="apikey-editor-backdrop" class="drawer-backdrop hidden" onclick="closeApiKeyEditor()"></div>
  <aside id="apikey-editor" class="drawer hidden" role="dialog" aria-modal="true" aria-labelledby="apikey-editor-title">
    <div class="drawer-header">
      <div><h3 id="apikey-editor-title">创建客户 Key</h3><p id="apikey-editor-subtitle">创建后即可复制完整 Key。</p></div>
      <button class="drawer-close" onclick="closeApiKeyEditor()" aria-label="关闭">×</button>
    </div>
    <div class="drawer-body">
      <div class="drawer-section">
        <div class="drawer-section-title">客户信息</div>
        <label>客户名称</label>
        <input id="ak-editor-name" maxlength="120" placeholder="例如：上海设计工作室">
        <div class="drawer-help">客户和 Key 是一一对应关系，名称将显示在列表和报表中。</div>
        <div style="margin-top:12px"><label>备注</label><input id="ak-editor-note" maxlength="240" placeholder="可填写套餐、负责人或用途"></div>
      </div>
      <div class="drawer-section">
        <div class="drawer-section-title">额度与频率</div>
        <div class="drawer-grid">
          <div><label>每日点数额度</label><input id="ak-editor-points" type="number" min="0" step="1" placeholder="留空继承"><div class="drawer-help">0 表示不限额。</div></div>
          <div><label>每日请求数</label><input id="ak-editor-requests" type="number" min="0" step="1" placeholder="留空继承"><div class="drawer-help">0 表示不限次数。</div></div>
          <div><label>每分钟请求数</label><input id="ak-editor-rate" type="number" min="0" step="1" placeholder="留空继承"><div class="drawer-help">用于限制突发调用。</div></div>
        </div>
      </div>
      <div class="drawer-section">
        <div class="drawer-section-title">访问权限</div>
        <div class="drawer-switches">
          <label class="drawer-switch"><input id="ak-editor-kind-image" type="checkbox" checked>允许图片生成</label>
          <label class="drawer-switch"><input id="ak-editor-kind-video" type="checkbox" checked>允许视频生成</label>
          <label class="drawer-switch"><input id="ak-editor-uploads" type="checkbox" checked>允许上传素材</label>
          <label class="drawer-switch"><input id="ak-editor-experimental" type="checkbox">允许实验模型</label>
        </div>
        <div style="margin-top:12px"><label>允许模型</label><input id="ak-editor-models" placeholder="留空表示全部；多个模型用逗号分隔"></div>
        <div class="drawer-grid" style="margin-top:12px">
          <div><label>允许分辨率</label><input id="ak-editor-resolutions" placeholder="例如：1K,2K,4K"></div>
          <div><label>允许视频时长</label><input id="ak-editor-durations" placeholder="例如：5,10"></div>
        </div>
        <div style="margin-top:12px"><label>允许视频场景</label><input id="ak-editor-scenes" placeholder="留空表示全部场景"></div>
      </div>
      <div class="drawer-section">
        <div class="drawer-section-title">Key 状态</div>
        <label class="drawer-switch"><input id="ak-editor-enabled" type="checkbox" checked>启用此 Key</label>
      </div>
      <div id="ak-new" class="secret-card hidden">
        <strong>新 Key 已创建</strong>
        <div class="drawer-help">请立即复制并妥善保存。</div>
        <code id="ak-new-value"></code>
        <button class="btn-primary btn-sm" onclick="copyKey()">复制完整 Key</button>
      </div>
    </div>
    <div class="drawer-footer">
      <button class="btn-secondary" onclick="closeApiKeyEditor()">取消</button>
      <button id="ak-editor-save" class="btn-primary" onclick="saveApiKeyEditor()">创建 Key</button>
    </div>
  </aside>
</div>

<!-- Tab: API 调用文档 -->
<div id="tab-docs" class="section hidden">
  <h2>📘 API 调用文档</h2>
  <div class="endpoint-box">
    <div class="url">服务地址：<span id="api-doc-base">当前站点</span></div>
    <div class="desc">所有请求使用 Authorization: Bearer &lt;API Key&gt;。API Key 可在“API Keys”页面创建和复制。</div>
  </div>
  <div class="docs-flow">
    <span>1. 提交生成</span><strong>→</strong><span>2. 获得 task_id</span><strong>→</strong><span>3. 轮询任务</span><strong>→</strong><span>4. 读取 assets</span>
  </div>
  <div class="docs-grid">
    <div class="docs-card">
      <h3>new-api 接入</h3>
      <p>在 new-api 中新增 <strong>OpenAI</strong> 渠道，用于模型同步与图片生成分发。</p>
      <ul>
        <li>渠道基础地址：<code id="api-doc-new-api-base"></code>（填写站点根地址，不要追加 <code>/v1</code>）</li>
        <li>密钥：填写本系统“API Keys”页面创建的完整 Key</li>
        <li>先执行“拉取模型”，再按需勾选图片模型</li>
        <li>渠道测试请选择“图片生成”；默认聊天测试不适用于媒体上游</li>
      </ul>
    </div>
    <div class="docs-card">
      <h3>sub2api 接入</h3>
      <p>在 sub2api 中按 OpenAI API Key 上游配置，图片和视频请求会转发到本系统。</p>
      <ul>
        <li>上游基础地址：<code id="api-doc-sub2api-base"></code>（必须以 <code>/v1</code> 结尾）</li>
        <li>密钥：填写本系统“API Keys”页面创建的完整 Key</li>
        <li>图片生成：<code>/images/generations</code>；图片编辑：<code>/images/edits</code></li>
        <li>视频生成：<code>/videos/generations</code></li>
        <li>当前图片接口不支持 stream=true，图片编辑暂不支持 mask 蒙版</li>
      </ul>
    </div>
    <div class="docs-card">
      <h3>统一生成接口</h3>
      <p><code>POST /v1/generate</code> 同时支持图片和视频。接口立即返回任务 ID，不需要让调用端长时间保持连接。</p>
      <div class="docs-code-head"><strong>图片生成（curl）</strong><button class="copy-btn" onclick="copyDocCode('api-doc-image-curl')">复制</button></div>
      <pre><code id="api-doc-image-curl"></code></pre>
      <div class="docs-code-head"><strong>视频生成（curl）</strong><button class="copy-btn" onclick="copyDocCode('api-doc-video-curl')">复制</button></div>
      <pre><code id="api-doc-video-curl"></code></pre>
    </div>
    <div class="docs-card">
      <h3>查询任务结果</h3>
      <p>图片通常较快，视频可能需要数分钟。请每 2～5 秒查询一次，直到状态为“已完成”“失败”“已取消”或“已过期”。</p>
      <div class="docs-code-head"><strong>轮询任务（curl）</strong><button class="copy-btn" onclick="copyDocCode('api-doc-task-curl')">复制</button></div>
      <pre><code id="api-doc-task-curl"></code></pre>
      <ul>
        <li><code>queued/running/submitted/hydrating</code>：仍在处理</li>
        <li><code>completed</code>：从 <code>task.assets</code> 读取结果地址</li>
        <li><code>failed/expired</code>：查看 <code>error_code</code> 与 <code>error_message</code></li>
      </ul>
    </div>
    <div class="docs-card">
      <h3>Python 完整示例</h3>
      <p>示例包含提交、容忍视频长耗时、轮询和异常处理。</p>
      <div class="docs-code-head"><strong>Python</strong><button class="copy-btn" onclick="copyDocCode('api-doc-python')">复制</button></div>
      <pre><code id="api-doc-python"></code></pre>
    </div>
    <div class="docs-card">
      <h3>可用接口</h3>
      <ul>
        <li><code>GET /v1/models</code>：模型列表</li>
        <li><code>GET /v1/models/{model}</code>：查询单个可用模型</li>
        <li><code>GET /v1/capabilities</code>：模型、比例、分辨率、时长与场景能力</li>
        <li><code>GET /models</code>：公开可用模型展示页（不含账号信息）</li>
        <li><code>GET /api/public/model-availability</code>：公开模型价目与可用性 JSON</li>
        <li><code>POST /v1/images/generations</code>：OpenAI 风格图片接口</li>
        <li><code>POST /v1/images/edits</code>：OpenAI multipart 图片编辑接口</li>
        <li><code>POST /v1/videos</code> / <code>/v1/videos/generations</code>：视频接口</li>
        <li><code>POST /v1/uploads</code>：上传参考图片或视频</li>
        <li><code>POST /v1/tasks/{task_id}/retry</code>：重试失败任务</li>
        <li><code>POST /v1/tasks/{task_id}/cancel</code>：取消任务</li>
      </ul>
      <p style="margin-top:10px">返回 401 表示 Key 无效；403 表示 Key 权限不足；429 表示额度或频率受限；5xx 表示服务或上游暂时不可用。</p>
    </div>
  </div>
</div>

<!-- Tab: 设置 -->
<div id="tab-settings" class="section hidden">
  <h2>⚙️ 系统设置</h2>
  <div class="row">
    <div class="col"><label>服务端口</label><input id="s-port" type="number" min="1" max="65535" step="1" value="8894"></div>
  </div>
  <div class="row" style="margin-top:12px">
    <div class="col"><label>OreateAI 基础 URL</label><input id="s-base" value="https://www.oreateai.com"></div>
    <div class="col"><label>默认图片模型</label><input id="s-img-model" value=""></div>
    <div class="col"><label>默认视频模型</label><input id="s-vid-model" value=""></div>
  </div>
  <h3 style="margin-top:20px;font-size:14px">📧 注册邮箱配置</h3>
  <div class="row" style="margin-top:8px">
    <div class="col"><label>邮箱来源</label>
      <select id="s-mail-provider">
        <option value="outlook">Outlook（卡密 / msOauth2api）</option>
        <option value="yyds">YYDS 临时邮箱</option>
      </select>
    </div>
    <div class="col"><label>取件模式</label>
      <select id="s-mail-mode">
        <option value="auto">自动（/get → mail-new → Graph 直连）</option>
        <option value="get">仅 /get 卡密接口</option>
        <option value="msoauth2">仅 msOauth2api /api/mail-new</option>
        <option value="graph">仅 Microsoft Graph 直连</option>
      </select>
    </div>
  </div>
  <div class="row" style="margin-top:8px">
    <div class="col"><label>API 地址</label><input id="s-mail-url" value="https://maliapi.215.im/v1"></div>
    <div class="col" style="flex:2"><label>API Key / Password</label><input id="s-mail-key" type="password" autocomplete="off" placeholder="mail api key"></div>
  </div>
  <div class="row" style="margin-top:8px">
    <div class="col" style="flex:3"><label>YYDS 首选域名（逗号分隔，仅 YYDS 生效）</label><input id="s-mail-domains" placeholder="domain1.xyz,domain2.xyz"></div>
  </div>
  <h3 style="margin-top:20px;font-size:14px">📦 号池配置</h3>
  <div class="row" style="margin-top:8px">
    <div class="col"><label>最低账号数</label><input id="s-min" type="number" min="0" step="1" value="3"></div>
    <div class="col"><label>维护目标数</label><input id="s-target" type="number" min="0" step="1" value="5"></div>
  </div>
  <div class="row" style="margin-top:8px">
    <div class="col"><label>自动维护间隔（秒，0=关闭）</label><input id="s-maintain-interval" type="number" min="0" step="1" value="3600"></div>
    <div class="col"><label>注册并发数（1-8）</label><input id="s-reg-concurrency" type="number" min="1" max="8" step="1" value="1"></div>
    <div class="col"><label>自动补号上限（0-50）</label><input id="s-auto-register-max" type="number" min="0" max="50" step="1" value="5"></div>
  </div>
  <div class="row" style="margin-top:8px">
    <div class="col"><label>生成探针间隔（秒，0=自动不探针）</label><input id="s-probe-interval" type="number" min="0" step="1" value="86400"></div>
    <div class="col"><label>每轮自动探针上限（0-200）</label><input id="s-probe-max" type="number" min="0" max="200" step="1" value="3"></div>
  </div>
  <div class="row" style="margin-top:8px">
    <div class="col"><label>维护时自动签到</label>
      <select id="s-auto-checkin">
        <option value="true">开启（每日登录一次）</option>
        <option value="false">关闭</option>
      </select>
    </div>
    <div class="col"><label>签到日界时区</label><input id="s-checkin-timezone" value="Asia/Shanghai" placeholder="Asia/Shanghai"></div>
  </div>
  <p class="reg-console-hint" style="margin:8px 0 0">说明：自动维护默认只刷新余额/签到；生成探针每个账号默认最多每天 1 次，且每轮最多 3 个。手动「体检并补号」仍可强制探针全部候选账号。</p>
  <div style="margin-top:16px"><button class="btn-primary" onclick="saveSettings()">保存设置</button></div>
  <pre id="settings-raw" style="margin-top:12px"></pre>

  <h3 style="margin-top:20px;font-size:14px">管理员账号</h3>
  <div class="row" style="margin-top:8px">
    <div class="col"><label>当前密码</label><input id="cred-current" type="password" autocomplete="current-password"></div>
    <div class="col"><label>新用户名</label><input id="cred-user" autocomplete="username"></div>
    <div class="col"><label>新密码</label><input id="cred-pass" type="password" autocomplete="new-password"></div>
    <div class="col"><label>确认新密码</label><input id="cred-confirm" type="password" autocomplete="new-password"></div>
  </div>
  <div style="margin-top:16px"><button class="btn-secondary" onclick="changeCredentials()">修改账号密码</button></div>

  <h3 style="margin-top:20px;font-size:14px">模型能力</h3>
  <div style="margin-top:8px;display:flex;gap:8px;align-items:center">
    <button class="btn-secondary" onclick="refreshCapabilities()">刷新模型能力</button>
    <span style="font-size:12px;color:#86868b" id="cap-status">未加载</span>
  </div>

  <h3 style="margin-top:20px;font-size:14px">🧾 审计日志</h3>
  <div class="table-wrap" style="margin-top:8px">
    <table>
      <thead><tr><th>时间</th><th>用户</th><th>动作</th><th>路径</th><th>状态</th><th>详情</th></tr></thead>
      <tbody id="audit-tbody"></tbody>
    </table>
  </div>

  <h3 style="margin-top:20px;font-size:14px">🗄 备份与恢复</h3>
  <div class="row" style="margin-top:8px">
    <div class="col"><button class="btn-secondary" onclick="downloadBackup()">下载备份</button></div>
    <div class="col"><label>恢复包</label><input id="restore-file" type="file" accept=".zip,application/zip"></div>
    <div class="col" style="align-self:end"><button class="btn-danger" onclick="restoreBackup()">恢复备份</button></div>
  </div>
</div>

</div>
</div>

<script>
const BASE = location.origin;
let adminToken = localStorage.getItem('oreate_admin_token') || '';

async function copyText(t) {
  const text=String(t ?? '');
  if(navigator.clipboard?.writeText){
    try{
      await navigator.clipboard.writeText(text);
      return;
    }catch(_error){}
  }
  const textarea=document.createElement('textarea');
  textarea.value=text;
  textarea.setAttribute('readonly','');
  textarea.style.position='fixed';
  textarea.style.opacity='0';
  document.body.appendChild(textarea);
  textarea.select();
  const copied=document.execCommand('copy');
  textarea.remove();
  if(!copied) throw new Error('浏览器未允许写入剪贴板');
}
function authHeaders(options={}){
  const headers = {};
  // Multipart uploads must omit Content-Type so the browser can set the boundary.
  if(!options.multipart) headers['Content-Type']='application/json';
  if (adminToken) headers.Authorization = 'Bearer ' + adminToken;
  return headers;
}
function showLogin(message=''){
  document.body.classList.add('login-mode');
  document.getElementById('login-panel').classList.remove('hidden');
  document.getElementById('app-shell').classList.add('hidden');
  document.getElementById('login-error').textContent = message;
}
function showApp(){
  document.body.classList.remove('login-mode');
  document.getElementById('login-panel').classList.add('hidden');
  document.getElementById('app-shell').classList.remove('hidden');
}
async function adminLogin(){
  const r = await fetch(BASE + '/api/admin/login', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({
      username:document.getElementById('login-user').value,
      password:document.getElementById('login-pass').value
    })
  });
  const data = await r.json().catch(()=>({}));
  if (!r.ok || !data.token) {
    showLogin(data.detail || '登录失败');
    return;
  }
  adminToken = data.token;
  localStorage.setItem('oreate_admin_token', adminToken);
  await init();
}

function logout(){
  if (adminToken) {
    fetch(BASE + '/api/admin/logout', {
      method:'POST',
      headers: authHeaders()
    }).catch(()=>{});
  }
  adminToken = '';
  localStorage.removeItem('oreate_admin_token');
  showLogin();
}
async function downloadBackup(){
  const r=await fetch(BASE + '/api/admin/backup', {headers: authHeaders()});
  if(!r.ok) throw new Error('backup failed');
  const blob=await r.blob();
  const url=URL.createObjectURL(blob);
  const a=document.createElement('a');
  a.href=url;
  a.download='oreate-backup.zip';
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(()=>URL.revokeObjectURL(url), 1000);
}
async function restoreBackup(){
  const input=document.getElementById('restore-file');
  const file=input?.files?.[0];
  if(!file){ showToast('请选择备份文件','warn'); return; }
  if(!(await showConfirm('确认恢复备份？当前数据库和配置将被替换。',{confirmText:'确认恢复',danger:true}))) return;
  const form=new FormData();
  form.append('confirm', 'true');
  form.append('file', file);
  const r=await fetch(BASE + '/api/admin/restore', {
    method:'POST',
    headers: authHeaders({multipart:true}),
    body: form
  });
  const data=await r.json().catch(()=>({}));
  if(!r.ok) throw new Error(data.detail || 'restore failed');
  showToast('恢复完成，请重新登录','ok');
  adminToken='';
  localStorage.removeItem('oreate_admin_token');
  showLogin('恢复完成，请重新登录');
}
function switchTab(name) {
  document.querySelectorAll('#tab-pool,#tab-models,#tab-outlook,#tab-generate,#tab-tasks,#tab-apikeys,#tab-docs,#tab-settings').forEach(el => {
    el.classList.toggle('hidden', el.id !== 'tab-'+name);
  });
  if(name==='outlook') loadOutlookMailboxes();
  if(name==='models') loadModelAvailability();
}

// Init
async function init() {
  if (!adminToken) {
    showLogin();
    return;
  }
  showApp();
  document.getElementById('status-text').textContent = '加载中...';
  try {
    await Promise.all([loadAccounts(), loadTasks(), loadApiKeys(), loadUsage(), loadUploads(), loadCostReport(), loadAuditLogs(), loadSettings()]);
    refreshOutlookPoolHint();
    await loadCapabilities();
  } catch (e) {
    document.getElementById('status-text').textContent = '未授权';
    showLogin('登录已失效');
    return;
  }
  const v = state.accounts.filter(accountIsGenerateReady).length;
  document.getElementById('status-text').textContent = `就绪 — ${v} 可用账号`;
  document.getElementById('gw-url').textContent = location.origin + '/v1/generate';
  document.getElementById('gw-example').textContent =
    `curl -H "Authorization: Bearer <key>" -H "Content-Type: application/json" -d '{"kind":"image","prompt":"hello"}' ${location.origin}/v1/generate`;
  populateApiDocs();
}
function copyExample() { copyText(document.getElementById('gw-example').textContent); }
function copyDocCode(id) { copyText(document.getElementById(id)?.textContent || ''); }
function populateApiDocs(){
  const origin=location.origin;
  const base=document.getElementById('api-doc-base');
  if(base) base.textContent=origin;
  const newApiBase=document.getElementById('api-doc-new-api-base');
  if(newApiBase) newApiBase.textContent=origin;
  const sub2apiBase=document.getElementById('api-doc-sub2api-base');
  if(sub2apiBase) sub2apiBase.textContent=origin + '/v1';
  const imageCurl=`curl -X POST "${origin}/v1/generate" \\
  -H "Authorization: Bearer <API_KEY>" \\
  -H "Content-Type: application/json" \\
  -d '{"kind":"image","prompt":"一只在窗边晒太阳的橘猫","model_name":"Google Nano Banana 2","ratio":"1:1","resolution":"1K"}'`;
  const videoCurl=`curl -X POST "${origin}/v1/generate" \\
  -H "Authorization: Bearer <API_KEY>" \\
  -H "Content-Type: application/json" \\
  -d '{"kind":"video","prompt":"猫狗在雨夜霓虹街头追逐","model_name":"Seedance 2.0 Mini","ratio":"16:9","resolution":"720","duration":5,"scene_id":"text_or_image"}'`;
  const taskCurl=`curl "${origin}/v1/tasks/<TASK_ID>" \\
  -H "Authorization: Bearer <API_KEY>"`;
  const python=`import time
import requests

BASE_URL = "${origin}"
API_KEY = "<API_KEY>"
headers = {"Authorization": f"Bearer {API_KEY}"}

response = requests.post(
    f"{BASE_URL}/v1/generate",
    headers=headers,
    json={
        "kind": "video",
        "prompt": "猫狗在雨夜霓虹街头追逐",
        "ratio": "16:9",
        "resolution": "720",
        "duration": 5,
        "scene_id": "text_or_image",
    },
    timeout=30,
)
response.raise_for_status()
task_id = response.json()["task_id"]

deadline = time.time() + 15 * 60
while time.time() < deadline:
    result = requests.get(
        f"{BASE_URL}/v1/tasks/{task_id}",
        headers=headers,
        timeout=30,
    )
    result.raise_for_status()
    task = result.json()["task"]
    if task["status"] == "completed":
        print(task["assets"])
        break
    if task["status"] in {"failed", "cancelled", "expired"}:
        raise RuntimeError(task.get("error_message") or task["status"])
    time.sleep(3)
else:
    print("任务仍在处理中，请稍后继续查询", task_id)`;
  [
    ['api-doc-image-curl',imageCurl],
    ['api-doc-video-curl',videoCurl],
    ['api-doc-task-curl',taskCurl],
    ['api-doc-python',python],
  ].forEach(([id,value]) => {
    const element=document.getElementById(id);
    if(element) element.textContent=value;
  });
}

// === Accounts ===
function createListPageState(limit=50){
  const normalizedLimit=Number.isSafeInteger(Number(limit)) && Number(limit)>0 ? Number(limit) : 50;
  return {limit:normalizedLimit,offset:0,total:0,hasMore:false,loading:false,error:'',filters:{},requestId:0};
}
function listQueryParams(page){
  const params=new URLSearchParams();
  params.set('limit',String(page.limit));
  params.set('offset',String(page.offset));
  Object.entries(page.filters||{}).forEach(([key,value]) => {
    const text=String(value ?? '').trim();
    if(text) params.set(key,text);
  });
  return params;
}
function applyListPage(page,response){
  const limit=Number(response.limit);
  const offset=Number(response.offset);
  const total=Number(response.total);
  if(Number.isSafeInteger(limit) && limit>0) page.limit=limit;
  if(Number.isSafeInteger(offset) && offset>=0) page.offset=offset;
  page.total=Number.isSafeInteger(total) && total>=0 ? total : 0;
  page.hasMore=Boolean(response.has_more);
  return Array.isArray(response.items) ? response.items : [];
}
function listCanPrevious(page){
  return !page.loading && page.offset>0;
}
function listCanNext(page){
  return !page.loading && Boolean(page.hasMore);
}
function listPageSummary(page){
  if(page.loading) return '加载中…';
  if(page.error) return `加载失败：${page.error}`;
  const pages=page.total>0 ? Math.ceil(page.total/page.limit) : 0;
  const current=page.total>0 ? Math.floor(page.offset/page.limit)+1 : 0;
  return `第 ${current} / ${pages} 页 · 共 ${page.total} 条`;
}
let state = {
  accounts:[],tasks:[],apikeys:[],clients:[],usage:[],uploads:[],costReport:[],auditLogs:[],
  accountCredentials:{},revealedAccountPasswords:{},outlookCredentials:{},revealedOutlookPasswords:{},
  registrationJob:null,maintenanceJob:null,
  registrationLogPinned:false,maintenanceLogPinned:false,
  capacity:null,
  settings:{},outlookMailboxes:[],capabilities:{image:{models:[]},video:{models:[],scenes:[]}},
  lists:{
    tasks:createListPageState(50),
    usage:createListPageState(50),
    uploads:createListPageState(50),
  },
};
function formatApiError(payload, fallback='request failed'){
  let detail=payload;
  if(payload && typeof payload === 'object' && !Array.isArray(payload)){
    detail=payload.detail ?? payload.error?.message ?? payload.message;
  }
  if(Array.isArray(detail)){
    return detail.map(item => {
      const location=Array.isArray(item?.loc) ? item.loc.filter(part => part !== 'body').join('.') : '';
      const message=item?.msg || String(item);
      return location ? `${location}: ${message}` : message;
    }).join('\\n');
  }
  if(detail && typeof detail === 'object') return detail.message || JSON.stringify(detail);
  return String(detail || fallback);
}
async function api(m,u,b){
  const o={method:m,headers:authHeaders()};
  if(b) o.body=JSON.stringify(b);
  let r;
  try{
    r=await fetch(BASE+u,o);
  }catch(_error){
    throw new Error('网络连接失败');
  }
  const data = await r.json().catch(()=>({}));
  if (r.status === 401) throw new Error(formatApiError(data, '登录已失效'));
  if (!r.ok) throw new Error(formatApiError(data, '请求失败'));
  return data;
}
function listFiltersFromInputs(fields){
  const filters={};
  Object.entries(fields).forEach(([queryName,elementId]) => {
    const value=document.getElementById(elementId)?.value;
    if(String(value ?? '').trim()) filters[queryName]=String(value).trim();
  });
  return filters;
}
function listLimitFromInput(elementId,fallback){
  const value=Number(document.getElementById(elementId)?.value);
  return Number.isSafeInteger(value) && value>=1 && value<=200 ? value : fallback;
}
function clearListInputs(elementIds,pageSizeId){
  elementIds.forEach(id => {
    const element=document.getElementById(id);
    if(element) element.value='';
  });
  const pageSize=document.getElementById(pageSizeId);
  if(pageSize) pageSize.value='50';
}
function renderListControls(name){
  const page=state.lists[name];
  const prefix=name;
  const status=document.getElementById(`${prefix}-list-status`);
  const previous=document.getElementById(`${prefix}-prev`);
  const next=document.getElementById(`${prefix}-next`);
  if(status){
    status.textContent=listPageSummary(page);
    status.classList.toggle('error',Boolean(page.error));
  }
  if(previous) previous.disabled=!listCanPrevious(page);
  if(next) next.disabled=!listCanNext(page);
}
function renderListTableState(tbody,page,columnCount){
  if(page.loading){
    tbody.innerHTML=`<tr><td colspan="${columnCount}" style="text-align:center;color:#86868b">加载中…</td></tr>`;
    return true;
  }
  if(page.error){
    tbody.innerHTML=`<tr><td colspan="${columnCount}" style="text-align:center;color:#c62828">加载失败：${escapeHtml(page.error)}</td></tr>`;
    return true;
  }
  return false;
}
async function loadOperationalList(name,path,render,afterLoad=null){
  const page=state.lists[name];
  const requestId=++page.requestId;
  page.loading=true;
  page.error='';
  render();
  renderListControls(name);
  try{
    const response=await api('GET',`${path}?${listQueryParams(page).toString()}`);
    if(requestId!==page.requestId) return null;
    const items=applyListPage(page,response);
    if(page.total===0 && page.offset>0){
      page.offset=0;
    }
    if(page.total>0 && page.offset>=page.total){
      page.offset=Math.floor((page.total-1)/page.limit)*page.limit;
      page.loading=false;
      return loadOperationalList(name,path,render,afterLoad);
    }
    state[name]=items;
    page.loading=false;
    render();
    renderListControls(name);
    if(afterLoad) afterLoad();
    return items;
  }catch(error){
    if(requestId!==page.requestId) return null;
    page.loading=false;
    page.error=error?.message || String(error);
    render();
    renderListControls(name);
    throw error;
  }
}
function changeOperationalListPage(name,direction,loader){
  const page=state.lists[name];
  if(page.loading) return;
  if(direction<0 && listCanPrevious(page)){
    page.offset=Math.max(0,page.offset-page.limit);
  }else if(direction>0 && listCanNext(page)){
    page.offset+=page.limit;
  }else{
    return;
  }
  void loader().catch(()=>{});
}
function escapeHtml(value){
  return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
const ADMIN_LABELS=Object.freeze({
  taskStatus:Object.freeze({
    queued:'待处理',
    running:'生成中',
    submitted:'已提交',
    hydrating:'获取结果中',
    completed:'已完成',
    failed:'失败',
    expired:'已过期',
    cancelled:'已取消',
  }),
  taskPhase:Object.freeze({
    generation:'生成',
    hydration:'获取结果',
  }),
  accountStatus:Object.freeze({
    verified:'已验证',
    active:'可用',
    new:'待验证',
    pending_validation:'真实生成验证中',
    invalid:'已失效',
    disabled:'已停用',
    expired:'已过期',
    signup_failed:'注册失败',
    email_domain_rejected:'邮箱域名被拒',
    confirm_failed:'验证失败',
  }),
  healthStatus:Object.freeze({
    healthy:'健康',
    cooling:'冷却中',
    low_balance:'余额不足',
    invalid:'不可用',
    risk_control:'风控限制',
    disabled:'已隔离',
    pending:'待验证',
    unknown:'未知',
  }),
  riskStatus:Object.freeze({
    clean:'正常',
    risk_control:'风控限制',
    invalid:'不可用',
  }),
  apiKeyStatus:Object.freeze({
    enabled:'启用',
    disabled:'停用',
    expired:'已过期',
    deleted:'已删除',
  }),
  clientStatus:Object.freeze({
    active:'启用',
    inactive:'停用',
    disabled:'停用',
  }),
  verificationStatus:Object.freeze({
    live_verified:'在线验证',
    unit_tested:'已测试',
    unverified:'未验证',
  }),
  kind:Object.freeze({
    image:'图片',
    video:'视频',
    audio:'音频',
  }),
  uploadStatus:Object.freeze({
    pending:'待处理',
    uploading:'上传中',
    completed:'已完成',
    failed:'失败',
    deleted:'已删除',
  }),
  source:Object.freeze({
    auto:'自动注册',
    import:'手动导入',
    imported:'手动导入',
    manual:'手动导入',
    demo:'演示数据',
  }),
});
function adminLabel(category,value){
  const original=String(value ?? '').trim();
  if(!original) return '-';
  const normalized=original.toLowerCase();
  return ADMIN_LABELS[category]?.[normalized] || original;
}
function normalizedOptionValues(values){
  const out=[];
  (Array.isArray(values)?values:[]).forEach(v => {
    const s=String(v ?? '').trim();
    if(s && !out.includes(s)) out.push(s);
  });
  return out;
}
function setSelectOptions(id, items, selectedValue='', emptyLabel='默认'){
  const el=document.getElementById(id);
  if(!el) return;
  const selected=String(selectedValue ?? '');
  const options=[];
  if(emptyLabel !== null) options.push(`<option value="">${escapeHtml(emptyLabel)}</option>`);
  (Array.isArray(items)?items:[]).forEach(item => {
    const value=String((item && typeof item === 'object' ? item.value : item) ?? '');
    if(!value) return;
    const label=String((item && typeof item === 'object' ? item.label : item) ?? value);
    options.push(`<option value="${escapeHtml(value)}">${escapeHtml(label)}</option>`);
  });
  el.innerHTML=options.join('');
  if([...el.options].some(o=>o.value===selected)) {
    el.value=selected;
  } else if(el.options.length > (emptyLabel === null ? 0 : 1)) {
    el.selectedIndex=emptyLabel === null ? 0 : 1;
  } else {
    el.value='';
  }
}
function valueOptions(values){
  return normalizedOptionValues(values).map(v => ({value:v,label:v}));
}
function capabilityModels(kind){
  return (((state.capabilities || {})[kind] || {}).models || []);
}
function capabilityScenes(){
  return (((state.capabilities || {}).video || {}).scenes || []);
}
function policyBadge(item){
  const verification = item?.verification_status || 'unverified';
  return `${adminLabel('verificationStatus',verification)}${item?.experimental ? ' · 实验性' : ''}`;
}
function modelOptionLabel(model){
  const title = model.description ? `${model.name} - ${model.description}` : model.name;
  return `${title} · ${policyBadge(model)}`;
}
function sceneOptionLabel(scene){
  const title = scene.name ? `${scene.name} - ${scene.scene_id}` : scene.scene_id;
  return `${title} · ${policyBadge(scene)}`;
}
function defaultModel(kind){
  return kind === 'video' ? (state.settings.oreate?.default_video_model || '') : (state.settings.oreate?.default_image_model || '');
}
function defaultRatio(kind){
  return kind === 'video' ? (state.settings.oreate?.default_video_ratio || '') : (state.settings.oreate?.default_image_ratio || '');
}
function defaultResolution(kind){
  return kind === 'video' ? (state.settings.oreate?.default_video_resolution || '') : (state.settings.oreate?.default_image_resolution || '');
}
function setVideoFieldsVisible(visible){
  ['g-dur','g-scene'].forEach(id => {
    const wrap=document.getElementById(id)?.closest('.col');
    if(wrap) wrap.style.display = visible ? '' : 'none';
  });
}
function setCapabilityState(payload){
  state.capabilities={
    image: payload?.image || {models:[]},
    video: payload?.video || {models:[],scenes:[]},
    source_account_id: payload?.source_account_id || null,
  };
}
async function loadAccounts(){
  const [accountsResponse,capacityResponse]=await Promise.all([
    api('GET','/api/accounts'),
    api('GET','/api/pool/capacity'),
  ]);
  state.accounts=accountsResponse.items||[];
  state.capacity=capacityResponse||null;
  renderPoolCapacity();
  renderAccounts();
  updateStats();
}
async function loadClients(){
  const r=await api('GET','/api/admin/clients');
  state.clients=r.items||[];
  renderClients();
  renderClientSelect();
}
function renderClientSelect(){
  setSelectOptions(
    'ak-client',
    (state.clients||[]).map(c => ({value:String(c.id), label:`${c.name}${c.contact ? ` · ${c.contact}` : ''}`})),
    '',
    '未绑定'
  );
}
function renderPoolCapacity(){
  const capacity=state.capacity||{};
  const tier455=(capacity.tiers||[]).find(tier => Number(tier.point_cost)===455)||{};
  const setText=(id,value) => {
    const element=document.getElementById(id);
    if(element) element.textContent=value;
  };
  setText('capacity-total-points',Number(capacity.total_points||0).toLocaleString());
  setText('capacity-reserved-points',Number(capacity.reserved_points||0).toLocaleString());
  setText('capacity-max-available',Number(capacity.max_available_points||0).toLocaleString());
  setText('capacity-tier-455',Number(tier455.task_capacity||0).toLocaleString());
  const note=document.getElementById('pool-capacity-note');
  if(!note) return;
  const known=Number(capacity.known_balance_accounts||0);
  const total=Number(capacity.account_count||0);
  const dailyGain=Number(capacity.daily_point_gain_total||0);
  const ready455=Number(tier455.ready_accounts||0);
  const estimatedReadyDays=Number(tier455.estimated_ready_days);
  const base=`${known} / ${total} 个账号已知积分；预计每日签到补充 ${dailyGain.toLocaleString()} 点。`;
  note.textContent=ready455>0
    ? `${base} 当前有 ${ready455} 个账号可直接承接 455 点任务。`
    : Number.isFinite(estimatedReadyDays) && estimatedReadyDays>0
      ? `${base} 当前没有单个账号可承接 455 点任务，最快预计还需 ${estimatedReadyDays} 天签到累积。`
      : `${base} 当前没有单个账号可承接 455 点任务，需要刷新余额或补充高积分账号。`;
}
function renderAccounts(){
  const tbody=document.getElementById('accounts-tbody');
  tbody.innerHTML = state.accounts.map(a => {
    const sc = a.status==='verified'?'tag-green':a.status==='new'?'tag-blue':'tag-gray';
    const hc = a.health_status==='healthy'?'tag-green':a.health_status==='cooling'?'tag-blue':a.health_status==='low_balance'||a.health_status==='risk_control'?'tag-red':'tag-gray';
    const em = a.email||'';
    const restPoint = a.rest_point ?? '-';
    const availablePoints = a.available_points ?? '-';
    const activeReservedPoints = Number(a.active_reserved_points||0);
    const reserveTargetPoints = Number(a.reserve_target_points||0);
    const balanceUpdatedAt = a.balance_updated_at ? new Date((a.balance_updated_at||0)*1000).toLocaleString() : '-';
    const healthMeta = `${adminLabel('riskStatus',a.risk_status||'clean')}${a.cooling ? ` · 剩余 ${a.cooldown_remaining_seconds || 0} 秒` : ''}`;
    const readinessMarks = accountReadinessMarks(a);
    const credential=state.accountCredentials[a.id];
    const passwordVisible=Boolean(state.revealedAccountPasswords[a.id]);
    const passwordText=!a.has_password?'未保存':passwordVisible?(credential?.password||'读取中…'):'••••••••';
    return `<tr>
      <td>${a.id}</td>
      <td class="email-cell" title="${escapeHtml(em)}">${escapeHtml(em)} <button class="copy-btn" data-copy-value="${escapeHtml(em)}" onclick="copyText(this.dataset.copyValue)">📋</button></td>
      <td class="password-cell">
        <div class="password-value">${escapeHtml(passwordText)}</div>
        ${a.has_password?`<div class="password-actions"><button class="copy-btn" onclick="toggleAccountPassword(${a.id})">${passwordVisible?'隐藏密码':'查看密码'}</button><button class="copy-btn" onclick="copyAccountPassword(${a.id})">复制密码</button></div>`:''}
      </td>
      <td><span class="tag ${sc}">${escapeHtml(adminLabel('accountStatus',a.status))}</span></td>
      <td class="health-cell"><span class="tag ${hc}">${escapeHtml(adminLabel('healthStatus',a.health_status))}</span><div style="font-size:11px;color:#86868b;white-space:nowrap">${escapeHtml(healthMeta)}</div><div style="font-size:10px;color:#86868b;letter-spacing:0.5px;margin-top:2px">${readinessMarks}</div></td>
      <td>${escapeHtml(adminLabel('source',a.source))}</td>
      <td style="font-family:monospace;font-size:11px">${escapeHtml(a.ouid_preview||'')}</td>
      <td class="point-value">${restPoint}<small>可用 ${availablePoints}</small></td>
      <td class="point-value">${activeReservedPoints}</td>
      <td>
        <div class="reserve-target-editor">
          <input id="reserve-target-${a.id}" type="number" min="0" max="1000000" step="1" value="${reserveTargetPoints}" aria-label="账号 ${a.id} 储备目标">
          <button class="btn-sm btn-secondary" onclick="saveReserveTarget(${a.id})">保存</button>
        </div>
      </td>
      <td style="font-size:11px">${balanceUpdatedAt}</td>
      <td style="font-size:11px">${new Date((a.created_at||0)*1000).toLocaleString()}</td>
      <td class="actions-cell"><div class="row-actions"><button class="btn-sm btn-secondary" onclick="generateWith(${a.id})">生成</button><button class="btn-sm btn-secondary" onclick="refreshAccountBalance(${a.id})">刷新余额</button>${(!a.has_session || a.status==='disabled'||a.status==='invalid'||a.status==='pending_validation')?`<button class="btn-sm btn-primary" onclick="activateAccount(${a.id})">${a.has_session?'重新激活':'激活'}</button>`:''}</div></td>
    </tr>`;
  }).join('');
  document.getElementById('pool-count').textContent = state.accounts.filter(accountIsGenerateReady).length;
}
function accountIsGenerateReady(a){
  if(typeof a?.generate_ready === 'boolean') return a.generate_ready;
  return a?.health_status==='healthy';
}
function accountReadinessMarks(a){
  const mark=(ready,label)=>ready===true
    ? `<span style="color:#34c759">${label}✓</span>`
    : `<span style="color:#c7c7cc">${label}✗</span>`;
  return `${mark(a.auth_ready===true,'A')} ${mark(a.points_ready===true,'P')} ${mark(accountIsGenerateReady(a),'G')}`;
}
async function activateAccount(accountId){
  const ok=await showConfirm(`激活账号 #${accountId}？将用已存密码重新登录、写入会话，并刷新积分（不消耗生成探针）。`);
  if(!ok) return;
  const status=document.getElementById('status-text');
  try{
    if(status) status.textContent=`正在激活账号 #${accountId}…`;
    const data=await api('POST',`/api/accounts/${accountId}/activate`);
    await loadAccounts();
    const st=data?.status || '-';
    const points=data?.item?.rest_point ?? data?.item?.available_points ?? '-';
    if(status) status.textContent=`账号 #${accountId} 激活结果：${st}，积分 ${points}`;
    showToast(
      data?.ok
        ? `账号 #${accountId} 已登录激活，积分 ${points}`
        : `账号 #${accountId} 激活未完成（${st}）`,
      data?.ok?'ok':'warn'
    );
  }catch(error){
    if(status) status.textContent='激活失败';
    showToast(`激活失败：${error?.message || String(error)}`,'err');
  }
}
async function reactivateAccount(accountId){
  return activateAccount(accountId);
}
async function purgeZombieAccounts(){
  const ok=await showConfirm(
    '清理僵尸号？将删除已隔离/失效且无法形成可用登录态（无 ouss / 200001）的账号，已验证账号不会动。',
    {confirmText:'确认清理',danger:true}
  );
  if(!ok) return;
  const status=document.getElementById('status-text');
  try{
    if(status) status.textContent='正在清理僵尸号…';
    const data=await api('POST','/api/accounts/purge-zombies',{confirm:true});
    await loadAccounts();
    const deleted=Number(data?.deleted||0);
    const skipped=Number(data?.skipped_active||0);
    const msg=`已清理僵尸号 ${deleted} 个`+(skipped?`，跳过活动任务中 ${skipped} 个`:'');
    if(status) status.textContent=msg;
    showToast(msg, deleted?'ok':'warn');
  }catch(error){
    if(status) status.textContent='清理僵尸号失败';
    showToast('清理僵尸号失败：'+(error?.message||String(error)),'err');
  }
}
async function saveReserveTarget(accountId){
  const input=document.getElementById(`reserve-target-${accountId}`);
  const reserveTarget=Number(input?.value);
  if(!Number.isInteger(reserveTarget) || reserveTarget<0){
    showToast('储备目标必须是大于或等于 0 的整数','warn');
    return;
  }
  const status=document.getElementById('status-text');
  try{
    if(status) status.textContent='正在保存储备目标…';
    await api('PUT',`/api/accounts/${accountId}/reserve-target`,{reserve_target_points:reserveTarget});
    await loadAccounts();
    if(status) status.textContent=`账号 #${accountId} 储备目标已更新`;
  }catch(error){
    if(status) status.textContent='储备目标保存失败';
    showToast(`储备目标保存失败：${error?.message || String(error)}`,'err');
  }
}
async function loadAccountCredentials(accountId){
  if(state.accountCredentials[accountId]) return state.accountCredentials[accountId];
  const credentials=await api('GET',`/api/accounts/${accountId}/credentials`);
  state.accountCredentials[accountId]=credentials;
  return credentials;
}
async function toggleAccountPassword(accountId){
  try{
    if(!state.accountCredentials[accountId]) await loadAccountCredentials(accountId);
    state.revealedAccountPasswords[accountId]=!state.revealedAccountPasswords[accountId];
    renderAccounts();
  }catch(error){
    showToast(`读取密码失败：${error?.message || String(error)}`,'err');
  }
}
async function copyAccountPassword(accountId){
  try{
    const credentials=await loadAccountCredentials(accountId);
    await copyText(credentials.password || '');
  }catch(error){
    showToast(`复制密码失败：${error?.message || String(error)}`,'err');
  }
}
function showToast(message, level='ok'){
  const host=document.getElementById('toast-host');
  if(!host) return;
  const el=document.createElement('div');
  el.className='toast '+(level||'ok');
  el.textContent=String(message||'');
  host.appendChild(el);
  setTimeout(()=>{el.remove();},4200);
}
function showConfirm(message, options={}){
  const confirmText=options.confirmText || '确定';
  const cancelText=options.cancelText || '取消';
  const danger=Boolean(options.danger);
  return new Promise((resolve)=>{
    document.getElementById('confirm-backdrop')?.remove();
    const backdrop=document.createElement('div');
    backdrop.id='confirm-backdrop';
    backdrop.className='confirm-backdrop';
    backdrop.innerHTML='<div class="confirm-dialog" role="dialog" aria-modal="true"><p class="confirm-message"></p><div class="confirm-actions"><button type="button" class="btn-secondary confirm-cancel"></button><button type="button" class="confirm-ok"></button></div></div>';
    backdrop.querySelector('.confirm-message').textContent=String(message||'');
    const cancelBtn=backdrop.querySelector('.confirm-cancel');
    const okBtn=backdrop.querySelector('.confirm-ok');
    cancelBtn.textContent=cancelText;
    okBtn.textContent=confirmText;
    okBtn.className=(danger?'btn-danger':'btn-primary')+' confirm-ok';
    let settled=false;
    const onKey=(event)=>{
      if(event.key==='Escape') finish(false);
      if(event.key==='Enter') finish(true);
    };
    const finish=(value)=>{
      if(settled) return;
      settled=true;
      document.removeEventListener('keydown', onKey);
      backdrop.remove();
      resolve(value);
    };
    cancelBtn.onclick=()=>finish(false);
    okBtn.onclick=()=>finish(true);
    backdrop.addEventListener('click',(event)=>{ if(event.target===backdrop) finish(false); });
    document.addEventListener('keydown', onKey);
    document.body.appendChild(backdrop);
    okBtn.focus();
  });
}
function showResultBanner(panelId, message, level='ok', onViewFailures=null){
  const panel=document.getElementById(panelId);
  if(!panel) return;
  panel.className='reg-result-banner '+(level||'ok');
  panel.classList.remove('hidden');
  panel.innerHTML='<span>'+escapeHtml(message)+'</span><span></span>';
  const actions=panel.lastElementChild;
  if(onViewFailures){
    const viewBtn=document.createElement('button');
    viewBtn.type='button';
    viewBtn.textContent='查看失败';
    viewBtn.onclick=onViewFailures;
    actions.appendChild(viewBtn);
  }
  const closeBtn=document.createElement('button');
  closeBtn.type='button';
  closeBtn.textContent='关闭';
  closeBtn.onclick=()=>panel.classList.add('hidden');
  actions.appendChild(closeBtn);
}
function formatEventTime(ts){
  const date=new Date((Number(ts)||0)*1000);
  if(Number.isNaN(date.getTime())) return '--:--:--';
  return date.toLocaleTimeString('zh-CN',{hour12:false});
}
function maskEmail(email){
  const value=String(email||'').trim();
  const at=value.indexOf('@');
  if(at<=1) return value || '-';
  return value.slice(0,1)+'***'+value.slice(at);
}
function registrationStepLabel(step){
  return ({
    queued:'等待开始',
    starting:'正在启动',
    create_mailbox:'正在创建邮箱',
    signup_attempt:'正在提交注册',
    email_verification:'正在等待邮箱验证',
    login_and_save:'正在登录并保存账号',
    generation_validation:'正在验证真实生成能力',
    completed:'当前账号处理完成',
    account_done:'账号注册结束',
    interrupted:'任务已中断',
    failed:'任务执行失败',
    registration_error:'注册过程异常',
  })[String(step||'').toLowerCase()] || String(step||'处理中');
}
function registrationStatusLabel(status){
  return ({
    queued:'等待中',
    running:'注册中',
    completed:'已完成',
    completed_with_errors:'部分成功',
    failed:'失败',
  })[String(status||'').toLowerCase()] || String(status||'');
}
function registrationPipelineSteps(){
  return [
    {id:'create_mailbox',label:'建邮'},
    {id:'signup_attempt',label:'注册'},
    {id:'email_verification',label:'验邮'},
    {id:'login_and_save',label:'登录'},
    {id:'generation_validation',label:'探针'},
    {id:'completed',label:'入库'},
  ];
}
function pipelineStepClass(stepId, currentStep, status){
  const order=registrationPipelineSteps().map(s=>s.id);
  const current=String(currentStep||'').toLowerCase();
  const idx=order.indexOf(stepId);
  const curIdx=order.indexOf(current);
  const terminal=['completed','completed_with_errors','failed'].includes(String(status||'').toLowerCase());
  if(terminal) return 'done';
  if(curIdx<0) return idx===0?'active':'';
  if(idx<curIdx) return 'done';
  if(idx===curIdx) return 'active';
  return '';
}
function renderRegistrationEventLog(events){
  if(!Array.isArray(events) || !events.length){
    return '<div class="registration-meta">等待事件日志…</div>';
  }
  const rows=events.slice(-200).map(event=>{
    const level=String(event?.level||'info');
    const message=event?.message || registrationStepLabel(event?.step);
    return `<div class="reg-event-row level-${escapeHtml(level)}"><span class="reg-event-time">${escapeHtml(formatEventTime(event?.ts))}</span><span class="reg-event-email">${escapeHtml(maskEmail(event?.email))}</span><span>${escapeHtml(message)}</span></div>`;
  }).join('');
  return `<div id="registration-event-log" class="reg-event-log">${rows}</div>`;
}
function outlookStatusLabel(status){
  return ({available:'可用',leased:'占用中',used:'已使用',error:'异常',disabled:'禁用'})[String(status||'').toLowerCase()] || (status||'-');
}
function renderOutlookStats(stats={}, provider='', baseUrl=''){
  const s=stats||{};
  const set=(id,val)=>{const el=document.getElementById(id); if(el) el.textContent=String(val??0);};
  set('out-st-available', s.available||0);
  set('out-st-leased', s.leased||0);
  set('out-st-used', s.used||0);
  set('out-st-error', s.error||0);
  set('out-st-total', s.total||0);
  const badge=document.getElementById('outlook-count');
  if(badge) badge.textContent=String(s.available||0);
  const active=String(provider||'').toLowerCase()==='outlook';
  const hint=document.getElementById('outlook-pool-hint');
  if(hint){
    hint.textContent=`Outlook 池：可用 ${s.available||0} / 总计 ${s.total||0}`+(active?'（当前注册源）':'');
  }
  const providerHint=document.getElementById('out-provider-hint');
  if(providerHint){
    providerHint.classList.toggle('is-active', active);
    providerHint.textContent=active
      ? `当前注册源：Outlook · API ${baseUrl||'-'}`
      : `注册源：YYDS / 其他（未选 Outlook） · API ${baseUrl||'-'}`;
  }
  const providerBtn=document.getElementById('out-use-provider-btn');
  if(providerBtn){
    providerBtn.className=active?'btn-provider-active':'btn-primary';
    providerBtn.textContent=active?'✓ 当前注册源：Outlook':'设为注册邮箱源';
    providerBtn.disabled=active;
    providerBtn.title=active?'已经是 Outlook 注册源':'切换注册任务使用 Outlook 邮箱池';
  }
}
async function refreshOutlookPoolHint(){
  try{
    const data=await api('GET','/api/mail/outlook?limit=1');
    renderOutlookStats(data?.stats||{}, data?.provider || state.settings?.mail?.provider || 'yyds', data?.base_url||'');
  }catch(error){
    const el=document.getElementById('outlook-pool-hint');
    if(el) el.textContent='Outlook 池：加载失败';
  }
}
function renderOutlookTable(){
  const tbody=document.getElementById('outlook-tbody');
  if(!tbody) return;
  if(!state.outlookMailboxes.length){
    tbody.innerHTML='<tr><td colspan="10" style="color:#86868b">暂无邮箱，请先导入卡密 txt</td></tr>';
    return;
  }
  tbody.innerHTML=state.outlookMailboxes.map(item=>{
    const st=String(item.status||'');
    const tag=st==='available'?'tag-green':st==='error'?'tag-red':st==='leased'?'tag-blue':'';
    const passwordVisible=Boolean(state.revealedOutlookPasswords[item.id]);
    const credential=state.outlookCredentials[item.id];
    const passwordText=!item.has_password?'未保存':passwordVisible?(credential?.password||'读取中…'):'••••••••';
    return `<tr>
      <td>${item.id}</td>
      <td>${escapeHtml(item.email||'')}</td>
      <td class="password-cell">
        <div class="password-value">${escapeHtml(passwordText)}</div>
        ${item.has_password?`<div class="password-actions"><button class="copy-btn" onclick="toggleOutlookPassword(${item.id})">${passwordVisible?'隐藏密码':'查看密码'}</button><button class="copy-btn" onclick="copyOutlookPassword(${item.id})">复制密码</button></div>`:''}
      </td>
      <td><span class="tag ${tag}">${escapeHtml(outlookStatusLabel(st))}</span></td>
      <td style="font-size:11px">${escapeHtml((item.client_id||'').slice(0,13))}…</td>
      <td style="max-width:180px;overflow:hidden;text-overflow:ellipsis;font-size:11px">${escapeHtml(item.last_error||'-')}</td>
      <td style="font-size:11px">${item.leased_at?new Date(item.leased_at*1000).toLocaleString():'-'}</td>
      <td style="font-size:11px">${item.used_at?new Date(item.used_at*1000).toLocaleString():'-'}</td>
      <td style="font-size:11px">${item.updated_at?new Date(item.updated_at*1000).toLocaleString():'-'}</td>
      <td>
        <button class="btn-secondary btn-sm" onclick="releaseOutlookMailbox(${item.id})">释放</button>
        <button class="btn-secondary btn-sm" onclick="deleteOutlookMailbox(${item.id})">删除</button>
      </td>
    </tr>`;
  }).join('');
}
async function loadOutlookMailboxes(){
  const tbody=document.getElementById('outlook-tbody');
  const status=document.getElementById('out-filter-status')?.value || 'all';
  const q=document.getElementById('out-filter-q')?.value || '';
  const params=new URLSearchParams({limit:'500'});
  if(status && status!=='all') params.set('status', status);
  if(String(q).trim()) params.set('q', String(q).trim());
  try{
    const data=await api('GET',`/api/mail/outlook?${params.toString()}`);
    state.outlookMailboxes=data.items||[];
    renderOutlookStats(data.stats||{}, data.provider||'', data.base_url||'');
    renderOutlookTable();
  }catch(error){
    if(tbody) tbody.innerHTML=`<tr><td colspan="10" style="color:#c62828">${escapeHtml(error?.message||String(error))}</td></tr>`;
  }
}
async function loadOutlookCredentials(mailboxId){
  if(state.outlookCredentials[mailboxId]) return state.outlookCredentials[mailboxId];
  const credentials=await api('GET',`/api/mail/outlook/${mailboxId}/credentials`);
  state.outlookCredentials[mailboxId]=credentials;
  return credentials;
}
async function toggleOutlookPassword(mailboxId){
  try{
    if(!state.outlookCredentials[mailboxId]) await loadOutlookCredentials(mailboxId);
    state.revealedOutlookPasswords[mailboxId]=!state.revealedOutlookPasswords[mailboxId];
    renderOutlookTable();
  }catch(error){
    showToast(`读取密码失败：${error?.message || String(error)}`,'err');
  }
}
async function copyOutlookPassword(mailboxId){
  try{
    const credentials=await loadOutlookCredentials(mailboxId);
    await copyText(credentials.password || '');
  }catch(error){
    showToast(`复制密码失败：${error?.message || String(error)}`,'err');
  }
}
function showOutlookImportResult(data, filename=''){
  const resultEl=document.getElementById('outlook-import-result');
  const nameEl=document.getElementById('outlook-import-filename');
  const errors=Array.isArray(data?.parse_errors)?data.parse_errors.length:0;
  const msg=`导入完成${filename?`（${filename}）`:''}：新增 ${data.inserted||0}，更新 ${data.updated||0}，可用 ${data.stats?.available||0}`+(errors?`，未识别 ${errors} 行`:'');
  if(resultEl) resultEl.textContent=msg;
  if(nameEl && filename) nameEl.textContent=`已选：${filename}`;
  showToast(msg,'ok');
}
async function importOutlookMailboxes(){
  const text=document.getElementById('outlook-import-text').value||'';
  const resultEl=document.getElementById('outlook-import-result');
  if(!text.trim()){
    showToast('请先粘贴 Outlook 卡密，或直接选择 txt 文件','err');
    return;
  }
  try{
    const data=await api('POST','/api/mail/outlook/import',{text, apply_detected_endpoint:true});
    showOutlookImportResult(data);
    try{await loadSettings();}catch(_){ }
    await loadOutlookMailboxes();
  }catch(error){
    const msg=error?.message || String(error);
    if(resultEl) resultEl.textContent=msg;
    showToast('导入失败：'+msg,'err');
  }
}
async function importOutlookMailFile(input){
  const file=input?.files?.[0];
  const resultEl=document.getElementById('outlook-import-result');
  if(!file) return;
  const nameEl=document.getElementById('outlook-import-filename');
  if(nameEl) nameEl.textContent=`正在识别：${file.name}`;
  try{
    const form=new FormData();
    form.append('file', file);
    form.append('apply_detected_endpoint', 'true');
    const response=await fetch(BASE+'/api/mail/outlook/import-file',{
      method:'POST',
      headers:authHeaders({multipart:true}),
      body:form,
    });
    const data=await response.json().catch(()=>({}));
    if(response.status===401) throw new Error(formatApiError(data,'登录已失效'));
    if(!response.ok) throw new Error(formatApiError(data,'导入失败'));
    showOutlookImportResult(data, file.name);
    try{await loadSettings();}catch(_){ }
    await loadOutlookMailboxes();
  }catch(error){
    const msg=error?.message || String(error);
    if(resultEl) resultEl.textContent=msg;
    showToast('导入失败：'+msg,'err');
  }finally{
    if(input) input.value='';
  }
}
async function releaseOutlookMailbox(id){
  const ok=await showConfirm(`确定释放邮箱 #${id} 回可用池？`);
  if(!ok) return;
  try{
    await api('POST',`/api/mail/outlook/${id}/release`);
    showToast('已释放','ok');
    await loadOutlookMailboxes();
  }catch(error){
    showToast('释放失败：'+(error?.message||String(error)),'err');
  }
}
async function deleteOutlookMailbox(id){
  const ok=await showConfirm(`确定删除邮箱 #${id}？此操作不可恢复。`);
  if(!ok) return;
  try{
    await api('DELETE',`/api/mail/outlook/${id}`);
    showToast('已删除','ok');
    await loadOutlookMailboxes();
  }catch(error){
    showToast('删除失败：'+(error?.message||String(error)),'err');
  }
}
async function purgeOutlookMailboxes(statuses, includeRegistered=true){
  const ok=await showConfirm(
    includeRegistered
      ? '确定清理已用/异常/禁用邮箱，并移除已注册入号池的卡密？此操作不可恢复。'
      : `确定清理状态为 ${(statuses||[]).join('/')} 的 Out 邮箱？`
  );
  if(!ok) return;
  try{
    const data=await api('POST','/api/mail/outlook/purge',{
      statuses:statuses||['used','error','disabled'],
      include_registered:Boolean(includeRegistered),
    });
    const registered=Number(data.deleted_registered||0);
    showToast(
      registered>0
        ? `已清理 ${data.deleted||0} 个（含已注册 ${registered}）`
        : `已清理 ${data.deleted||0} 个`,
      'ok'
    );
    await loadOutlookMailboxes();
  }catch(error){
    showToast('清理失败：'+(error?.message||String(error)),'err');
  }
}
async function useOutlookForRegistration(){
  const providerBtn=document.getElementById('out-use-provider-btn');
  if(providerBtn?.disabled) return;
  try{
    const data=await api('POST','/api/mail/outlook/use-for-registration');
    showToast('已切换注册源为 Outlook','ok');
    try{await loadSettings();}catch(_){ }
    if(state.settings?.mail) state.settings.mail.provider='outlook';
    renderOutlookStats(data.stats||{}, data.provider||'outlook', data.base_url||'');
  }catch(error){
    showToast('切换失败：'+(error?.message||String(error)),'err');
  }
}
function updateRegistrationConcurrencyHint(){
  const hint=document.getElementById('reg-concurrency-hint');
  if(!hint) return;
  const concurrency=Number(state.settings?.pool?.registration_concurrency);
  hint.textContent=`并发：${Number.isSafeInteger(concurrency)&&concurrency>0?concurrency:3}`;
}
function renderRegistrationProgress(job,connectionMessage=''){
  const panel=document.getElementById('registration-progress');
  if(!panel || !job) return;
  const total=Math.max(1,Number(job.total)||1);
  const completed=Math.max(0,Number(job.completed)||0);
  const percent=Math.min(100,Math.round(completed/total*100));
  const events=Array.isArray(job.events)?job.events:[];
  const pipeline=registrationPipelineSteps().map(step=>{
    const cls=pipelineStepClass(step.id, job.current_step, job.status);
    return `<span class="reg-pipeline-step ${cls}">${escapeHtml(step.label)}</span>`;
  }).join('');
  const previousLog=document.getElementById('registration-event-log');
  const previousPinned=state.registrationLogPinned;
  const previousScroll=previousLog?previousLog.scrollTop:0;
  panel.classList.remove('hidden');
  panel.innerHTML=`
    <div class="registration-progress-head">
      <span>注册任务 #${escapeHtml(job.id||'-')} · ${escapeHtml(registrationStatusLabel(job.status))}</span>
      <span>${completed} / ${total} · 成功 ${Number(job.succeeded)||0} · 失败 ${Number(job.failed)||0}</span>
    </div>
    <div class="registration-track"><div class="registration-fill" style="width:${percent}%"></div></div>
    <div class="reg-pipeline">${pipeline}</div>
    <div class="registration-meta">
      当前步骤：${escapeHtml(registrationStepLabel(job.current_step))}
      ${job.current_email?` · 当前邮箱：${escapeHtml(job.current_email)}`:''}
      ${connectionMessage?`<br>${escapeHtml(connectionMessage)}`:''}
      ${job.error_message?`<br><span style="color:#c62828">${escapeHtml(job.error_message)}</span>`:''}
    </div>
    ${renderRegistrationEventLog(events)}`;
  const log=document.getElementById('registration-event-log');
  if(log){
    log.addEventListener('scroll',()=>{
      const distance=log.scrollHeight-log.scrollTop-log.clientHeight;
      state.registrationLogPinned=distance>24;
    });
    if(previousPinned) log.scrollTop=previousScroll;
    else log.scrollTop=log.scrollHeight;
  }
}
function setRegistrationButtonsDisabled(disabled){
  const button=document.getElementById('reg-start');
  if(button){
    button.disabled=disabled;
    button.textContent=disabled?'注册中…':'开始注册';
  }
}
async function pollRegistrationJob(jobId){
  const terminal=new Set(['completed','completed_with_errors','failed']);
  while(true){
    await new Promise(resolve=>setTimeout(resolve,1000));
    let response;
    try{
      response=await api('GET',`/api/register/jobs/${jobId}`);
    }catch(error){
      renderRegistrationProgress(state.registrationJob,'连接暂时中断，正在重试注册进度…');
      continue;
    }
    const job=response.job;
    state.registrationJob=job;
    renderRegistrationProgress(job);
    if(terminal.has(String(job.status||'').toLowerCase())){
      setRegistrationButtonsDisabled(false);
      await loadAccounts();
      const succeeded=Number(job.succeeded)||0;
      const failed=Number(job.failed)||0;
      document.getElementById('status-text').textContent=`注册完成 — 成功 ${succeeded}，失败 ${failed}`;
      const level=failed? (succeeded?'warn':'err') : 'ok';
      const message=failed
        ?`注册完成：成功 ${succeeded} 个，失败 ${failed} 个`
        :`注册成功：${succeeded} 个账号`;
      showResultBanner('registration-result-banner', message, level, failed?()=>{
        const log=document.getElementById('registration-event-log');
        if(log) log.scrollTop=log.scrollHeight;
      }:null);
      showToast(message, level);
      return job;
    }
  }
}
async function startRegistration(count){
  if(state.registrationJob && ['queued','running'].includes(String(state.registrationJob.status||'').toLowerCase())){
    showToast('已有注册任务正在执行','warn');
    return null;
  }
  const total=Number(count);
  if(!Number.isSafeInteger(total) || total<1 || total>50){
    showToast('注册数量必须是 1～50 的整数','err');
    return null;
  }
  try{localStorage.setItem('oreate_reg_count', String(total));}catch(_){ }
  setRegistrationButtonsDisabled(true);
  document.getElementById('registration-result-banner')?.classList.add('hidden');
  document.getElementById('status-text').textContent='正在创建注册任务…';
  updateRegistrationConcurrencyHint();
  try{
    const response=await api('POST','/api/register/jobs',{count:total});
    state.registrationJob=response.job;
    state.registrationLogPinned=false;
    renderRegistrationProgress(response.job);
    document.getElementById('status-text').textContent='账号注册中…';
    return await pollRegistrationJob(response.job.id);
  }catch(error){
    setRegistrationButtonsDisabled(false);
    document.getElementById('status-text').textContent='注册任务创建失败';
    showToast(`注册失败：${error?.message || String(error)}`,'err');
    return null;
  }
}
function startRegistrationFromControls(){
  return startRegistration(Number(document.getElementById('reg_count').value||1));
}
async function registerOne(){return startRegistration(1);}
async function registerBatch(){return startRegistration(Number(document.getElementById('reg_count').value||1));}
function maintenanceStepLabel(step){
  const normalized=String(step||'').toLowerCase();
  if(normalized.startsWith('register_')){
    return `补号：${registrationStepLabel(normalized.slice('register_'.length))}`;
  }
  return ({
    queued:'等待开始',
    scanning:'正在扫描号池',
    checking_account:'正在检测账号健康状态',
    daily_checkin:'正在登录签到领取每日余额',
    checking_generation:'正在验证真实生成能力',
    refreshing_session:'正在刷新账号登录状态',
    gateway_risk:'生成环境异常，已停止检测',
    supplementing:'正在补充健康账号',
    finalizing:'正在汇总检测结果',
    completed:'维护任务已完成',
    interrupted:'任务已中断',
    failed:'任务执行失败',
  })[normalized] || String(step||'处理中');
}
function maintenanceStatusLabel(status){
  return ({
    queued:'等待中',
    running:'检测中',
    completed:'已完成',
    completed_with_errors:'部分完成',
    failed:'失败',
  })[String(status||'').toLowerCase()] || String(status||'');
}
function maintenanceItemLabel(item){
  if(item?.category==='registration'){
    return `${item?.email||'新账号'} · ${item?.action==='registered'?'补号成功':'补号失败'}`;
  }
  const category=({
    risk_control:'风控账号',
    gateway_risk:'生成环境异常',
    invalid:'失效账号',
    check_failed:'检测异常',
    daily_checkin:'每日签到',
  })[item?.category] || item?.category || '账号';
  const action=({
    isolated:'已隔离',
    detected:'已发现',
    cooling:'已进入冷却',
    aborted:'已停止检测',
    checked_in:'已签到',
  })[item?.action] || item?.action || '已处理';
  return `${item?.email||`账号 #${item?.account_id||'-'}`} · ${category} · ${action}`;
}
function renderMaintenanceProgress(job,connectionMessage=''){
  const panel=document.getElementById('maintenance-progress');
  if(!panel || !job) return;
  const total=Math.max(0,Number(job.total_accounts)||0);
  const checked=Math.max(0,Number(job.checked_accounts)||0);
  const percent=total===0?100:Math.min(100,Math.round(checked/total*100));
  const items=(Array.isArray(job.items)?job.items:[]).slice(-40);
  const rows=items.map(item=>{
    const failed=item?.action==='registration_failed'||item?.category==='check_failed';
    const level=failed?'error':(item?.action==='isolated'?'info':'success');
    return `<div class="reg-event-row level-${level}"><span class="reg-event-time">--:--:--</span><span class="reg-event-email">${escapeHtml(maskEmail(item?.email||`#${item?.account_id||'-'}`))}</span><span>${escapeHtml(maintenanceItemLabel(item))}</span></div>`;
  }).join('');
  panel.classList.remove('hidden');
  panel.innerHTML=`
    <div class="registration-progress-head">
      <span>号池维护 #${escapeHtml(job.id||'-')} · ${escapeHtml(maintenanceStatusLabel(job.status))}</span>
      <span>已检测 ${checked} / ${total}</span>
    </div>
    <div class="registration-track"><div class="registration-fill" style="width:${percent}%"></div></div>
    <div class="registration-meta">
      当前步骤：${escapeHtml(maintenanceStepLabel(job.current_step))}
      ${job.current_email?` · 当前账号：${escapeHtml(job.current_email)}`:''}
      <br>检测前健康 ${Number(job.healthy_before)||0} 个 · 签到 ${Number(job.checked_in)||0} 个 · 风控 ${Number(job.risk_found)||0} 个 · 失效 ${Number(job.invalid_found)||0} 个 · 已隔离 ${Number(job.isolated_accounts)||0} 个
      <br>计划补号 ${Number(job.registration_target)||0} 个 · 成功 ${Number(job.registered)||0} 个 · 失败 ${Number(job.registration_failed)||0} 个 · 当前健康 ${Number(job.healthy_after)||0} 个
      ${connectionMessage?`<br>${escapeHtml(connectionMessage)}`:''}
      ${job.error_message?`<br><span style="color:#c62828">${escapeHtml(job.error_message)}</span>`:''}
    </div>
    <div id="maintenance-event-log" class="reg-event-log">${rows || '<div class="registration-meta">等待检测结果…</div>'}</div>`;
  const log=document.getElementById('maintenance-event-log');
  if(log && !state.maintenanceLogPinned) log.scrollTop=log.scrollHeight;
}
function setMaintenanceButtonDisabled(disabled){
  const button=document.getElementById('maintenance-start');
  if(button) button.disabled=disabled;
}
async function pollMaintenanceJob(jobId){
  const terminal=new Set(['completed','completed_with_errors','failed']);
  while(true){
    await new Promise(resolve=>setTimeout(resolve,1000));
    let response;
    try{
      response=await api('GET',`/api/pool/maintenance/jobs/${jobId}`);
    }catch(error){
      renderMaintenanceProgress(state.maintenanceJob,'连接暂时中断，正在重试维护进度…');
      continue;
    }
    const job=response.job;
    state.maintenanceJob=job;
    renderMaintenanceProgress(job);
    if(terminal.has(String(job.status||'').toLowerCase())){
      setMaintenanceButtonDisabled(false);
      await loadAccounts();
      await loadCapabilities();
      document.getElementById('status-text').textContent=`维护完成 — 健康 ${job.healthy_after||0}，隔离 ${job.isolated_accounts||0}，补号 ${job.registered||0}`;
      const prefix=job.status==='completed'
        ?'号池维护完成'
        :job.status==='failed'
          ?'号池维护失败'
          :'号池维护部分完成';
      const message=`${prefix}：健康 ${job.healthy_after||0}，隔离 ${job.isolated_accounts||0}，补号成功 ${job.registered||0}，失败 ${job.registration_failed||0}`;
      const level=job.status==='completed'?'ok':(job.status==='failed'?'err':'warn');
      showResultBanner('maintenance-result-banner', message, level);
      showToast(message, level);
      return job;
    }
  }
}
async function maintainPool(){
  if(state.maintenanceJob && ['queued','running'].includes(String(state.maintenanceJob.status||'').toLowerCase())){
    showToast('已有号池维护任务正在执行','warn');
    return null;
  }
  if(!(await showConfirm('将批量检测全部账号：刷新余额/签到，并对候选账号强制做一次低成本图片生成探针（会扣积分）；风险和失效账号会被隔离，再按健康账号缺口自动补号。是否继续？',{confirmText:'开始体检'}))) return null;
  const configuredTarget=Number(state.settings?.pool?.maintain_target);
  const target=Number.isSafeInteger(configuredTarget)&&configuredTarget>0?configuredTarget:5;
  const requestedMax=Number(document.getElementById('reg_count').value||1);
  const maxRegister=Number.isSafeInteger(requestedMax)&&requestedMax>=0&&requestedMax<=50?requestedMax:1;
  setMaintenanceButtonDisabled(true);
  document.getElementById('maintenance-result-banner')?.classList.add('hidden');
  document.getElementById('status-text').textContent='正在创建号池维护任务…';
  try{
    const response=await api('POST','/api/pool/maintenance/jobs',{
      clean_risk:true,
      supplement:true,
      target_healthy:target,
      max_register:maxRegister,
    });
    state.maintenanceJob=response.job;
    state.maintenanceLogPinned=false;
    renderMaintenanceProgress(response.job);
    document.getElementById('status-text').textContent='正在批量检测账号…';
    return await pollMaintenanceJob(response.job.id);
  }catch(error){
    setMaintenanceButtonDisabled(false);
    document.getElementById('status-text').textContent='号池维护任务创建失败';
    showToast(`号池维护失败：${error?.message||String(error)}`,'err');
    return null;
  }
}
function toggleImport(){document.getElementById('import-area').classList.toggle('hidden');}
async function doImport(){const r=await api('POST','/api/accounts/import',{email:document.getElementById('imp-email').value,password:document.getElementById('imp-pwd').value});await loadAccounts();showToast(r.ok?'导入成功':'导入失败', r.ok?'ok':'err');}

function generateWith(aid){switchTab('generate');document.getElementById('g-account').value=aid;}
async function refreshAccountBalance(aid){await api('POST',`/api/accounts/${aid}/refresh-balance`);await loadAccounts();}

// === Available models ===
const availState={kind:'all', onlyOk:true, showExp:false, includeDisabled:false, payload:null};
const AVAIL_STATUS={available:'可用', tight:'紧张', unavailable:'不足'};
function setAvailKind(kind){
  availState.kind=kind;
  document.querySelectorAll('[data-avail-kind]').forEach(el=>el.classList.toggle('active', el.dataset.availKind===kind));
  renderModelAvailability();
}
function toggleAvailOnlyOk(){
  availState.onlyOk=!availState.onlyOk;
  document.getElementById('avail-only-ok').classList.toggle('active', availState.onlyOk);
  renderModelAvailability();
}
function toggleAvailShowExp(){
  availState.showExp=!availState.showExp;
  document.getElementById('avail-show-exp').classList.toggle('active', availState.showExp);
  renderModelAvailability();
}
function toggleAvailIncludeDisabled(){
  availState.includeDisabled=!availState.includeDisabled;
  document.getElementById('avail-include-disabled').classList.toggle('active', availState.includeDisabled);
  loadModelAvailability();
}
function availParams(item){
  const parts=[];
  if(item.resolution) parts.push(escapeHtml(String(item.resolution)));
  if(item.duration!=null) parts.push(escapeHtml(String(item.duration))+'s');
  if(item.is_audio===true) parts.push('音频');
  if(item.is_audio===false) parts.push('无音频');
  return parts.join(' · ') || '-';
}
function filteredAvailItems(){
  const scene=document.getElementById('avail-scene')?.value||'';
  return (availState.payload?.items||[]).filter(item=>{
    if(availState.kind!=='all' && item.kind!==availState.kind) return false;
    if(availState.onlyOk && item.status==='unavailable') return false;
    if(!availState.showExp && item.verification_status==='unverified') return false;
    if(scene && item.kind==='video' && item.scene_id!==scene) return false;
    return true;
  });
}
function fillAvailScenes(){
  const select=document.getElementById('avail-scene');
  if(!select) return;
  const scenes=new Map();
  for(const item of (availState.payload?.items||[])){
    if(item.kind==='video' && item.scene_id) scenes.set(item.scene_id, item.scene_name||item.scene_id);
  }
  const current=select.value;
  select.innerHTML='<option value="">全部场景</option>'+[...scenes.entries()].map(([id,name])=>`<option value="${escapeHtml(id)}">${escapeHtml(name)}</option>`).join('');
  if([...scenes.keys()].includes(current)) select.value=current;
}
function renderModelAvailability(){
  const host=document.getElementById('avail-catalog');
  if(!host) return;
  const setText=(id,value)=>{const el=document.getElementById(id); if(el) el.textContent=value;};
  const pool=availState.payload?.pool||{};
  setText('avail-known', `${Number(pool.known_balance_accounts||0)}/${Number(pool.account_count||0)}`);
  setText('avail-max', Number(pool.max_available_points||0).toLocaleString());
  setText('avail-reserved', Number(pool.reserved_points||0).toLocaleString());
  const items=filteredAvailItems();
  setText('avail-combo-count', String(items.length));
  if(!availState.payload){host.innerHTML='<div class="reg-console-hint">加载中…</div>';return;}
  if(!items.length){host.innerHTML='<div class="reg-console-hint">当前筛选下没有组合</div>';return;}
  const groups=new Map();
  for(const item of items){
    const key=item.kind+'::'+item.model_name;
    if(!groups.has(key)) groups.set(key,{kind:item.kind,model_name:item.model_name,experimental:!!item.experimental,verification_status:item.verification_status,combos:[]});
    const g=groups.get(key);
    g.combos.push(item);
    if(item.experimental) g.experimental=true;
  }
  host.innerHTML=[...groups.values()].map((group,index)=>{
    const ok=group.combos.filter(c=>c.status==='available'||c.status==='tight').length;
    const tags=[
      `<span class="avail-tag">${group.kind==='image'?'图片':'视频'}</span>`,
      group.verification_status==='live_verified'?'<span class="avail-tag">在线验证</span>':'',
      group.experimental?'<span class="avail-tag">实验性</span>':'',
    ].filter(Boolean).join('');
    const rows=group.combos.map(item=>`<div class="avail-row">
      <div>${escapeHtml(item.kind==='video'?(item.scene_name||item.scene_id||'-'):'图片')}</div>
      <div>${availParams(item)}</div>
      <div>${escapeHtml(item.point_cost)}</div>
      <div>${escapeHtml(item.ready_accounts)}</div>
      <div>${escapeHtml(item.task_capacity)}</div>
      <div><span class="avail-pill ${escapeHtml(item.status)}">${escapeHtml(AVAIL_STATUS[item.status]||item.status)}</span></div>
    </div>`).join('');
    return `<section class="avail-group ${index===0?'open':''}">
      <div class="avail-group-head" onclick="this.parentElement.classList.toggle('open')">
        <div class="avail-group-title"><span>${escapeHtml(group.model_name)}</span>${tags}</div>
        <div class="avail-meta">可用 ${ok} / 共 ${group.combos.length} 组合</div>
      </div>
      <div class="avail-combos">
        <div class="avail-row" style="font-weight:600;color:#6e6e73"><div>场景</div><div>参数</div><div>单价</div><div>可接账号</div><div>容量</div><div>状态</div></div>
        ${rows}
      </div>
    </section>`;
  }).join('');
}
async function loadModelAvailability(){
  const host=document.getElementById('avail-catalog');
  if(host) host.innerHTML='<div class="reg-console-hint">加载中…</div>';
  document.getElementById('avail-only-ok')?.classList.toggle('active', availState.onlyOk);
  document.getElementById('avail-show-exp')?.classList.toggle('active', availState.showExp);
  document.getElementById('avail-include-disabled')?.classList.toggle('active', availState.includeDisabled);
  try{
    const q=availState.includeDisabled?'?include_disabled=true':'';
    availState.payload=await api('GET','/api/pool/model-availability'+q);
    fillAvailScenes();
    renderModelAvailability();
  }catch(e){
    if(host) host.innerHTML=`<div class="reg-console-hint">加载失败：${escapeHtml(e.message||e)}</div>`;
  }
}

// === Generate ===
async function loadCapabilities(){
  const status=document.getElementById('cap-status');
  if(status) status.textContent='加载中...';
  try {
    const r=await api('GET','/api/models/capabilities');
    setCapabilityState(r);
    const imageCount=capabilityModels('image').length;
    const videoCount=capabilityModels('video').length;
    const sceneSummary=capabilityScenes().map(s => `${s.scene_id}:${policyBadge(s)}`).join(' | ');
    if(status) status.textContent=`账号 ${r.source_account_id || '-'} · 图片模型 ${imageCount} · 视频模型 ${videoCount}${sceneSummary ? ` · 场景 ${sceneSummary}` : ''}`;
  } catch(e) {
    setCapabilityState({});
    if(status) status.textContent='未加载：' + e.message;
  }
  applyGenerateOptions();
}
async function refreshCapabilities(){
  const status=document.getElementById('cap-status');
  if(status) status.textContent='刷新中...';
  try {
    const r=await api('POST','/api/models/refresh');
    setCapabilityState(r);
    const imageCount=capabilityModels('image').length;
    const videoCount=capabilityModels('video').length;
    if(status) status.textContent=`账号 ${r.source_account_id || '-'} · 图片模型 ${imageCount} · 视频模型 ${videoCount}`;
    applyGenerateOptions();
    showToast('已刷新模型能力','ok');
  } catch(e) {
    if(status) status.textContent='刷新失败：' + e.message;
    showToast('刷新失败：' + e.message,'err');
  }
}
function selectedModel(kind){
  const name=document.getElementById('g-model').value;
  return capabilityModels(kind).find(m => m.name === name) || null;
}
function applyGenerateOptions(){
  const kind=document.getElementById('g-kind').value;
  const models=capabilityModels(kind);
  const current=document.getElementById('g-model').value;
  const configured=defaultModel(kind);
  const selected=models.some(m=>m.name===current) ? current : (models.some(m=>m.name===configured) ? configured : (models[0]?.name || ''));
  setVideoFieldsVisible(kind === 'video');
  setSelectOptions(
    'g-model',
    models.map(m => ({value:m.name,label:modelOptionLabel(m)})),
    selected,
    models.length ? null : '使用默认模型'
  );
  applyModelOptions();
}
function applyModelOptions(){
  const kind=document.getElementById('g-kind').value;
  const model=selectedModel(kind);
  const desc=document.getElementById('g-model-desc');
  if(model) {
    desc.textContent = model.description || '';
    setSelectOptions('g-ratio', valueOptions(model.ratios), defaultRatio(kind), model.ratios?.length ? null : '默认比例');
    setSelectOptions('g-res', valueOptions(model.resolutions), defaultResolution(kind), model.resolutions?.length ? null : '默认分辨率');
  } else {
    desc.textContent = capabilityModels(kind).length ? '' : '模型能力未加载，可在设置页刷新模型能力。';
    setSelectOptions('g-ratio', valueOptions([defaultRatio(kind)]), defaultRatio(kind), '默认比例');
    setSelectOptions('g-res', valueOptions([defaultResolution(kind)]), defaultResolution(kind), '默认分辨率');
  }
  if(kind === 'video') {
    setSelectOptions('g-dur', valueOptions(model?.durations || [state.settings.oreate?.default_video_duration]), state.settings.oreate?.default_video_duration || '', model?.durations?.length ? null : '默认时长');
    setSelectOptions(
      'g-scene',
      capabilityScenes().map(s => ({value:s.scene_id,label:sceneOptionLabel(s)})),
      state.settings.oreate?.default_video_scene || '',
      capabilityScenes().length ? null : '默认场景'
    );
  } else {
    setSelectOptions('g-dur', [], '', '默认时长');
    setSelectOptions('g-scene', [], '', '默认场景');
  }
}
const GENERATION_TERMINAL_STATUSES=new Set(['completed','failed','cancelled','expired']);
function renderGenerateResult(task,message=''){
  const panel=document.getElementById('g-result');
  if(!panel) return;
  revokeCleanTaskImages(panel);
  if(!task){
    panel.textContent=message;
    return;
  }
  const assets=Array.isArray(task?.assets) ? task.assets : [];
  const status=String(task?.status || 'queued').toLowerCase();
  const statusLabel=adminLabel('taskStatus',task?.status);
  const errorMessage=task?.error_message ? `<div style="color:#c62828">${escapeHtml(task.error_message)}</div>` : '';
  const assetHtml=assets.length
    ? assets.map((asset,index)=>renderTaskAsset(asset,task,index)).filter(Boolean).join('')
    : '<div class="task-preview-meta">任务完成后将在这里显示生成结果。</div>';
  panel.innerHTML=`
    <div class="generation-result-card">
      <div class="generation-result-title">
        <span>生成结果 · 任务 #${escapeHtml(task?.id || '-')}</span>
        <span class="tag ${status==='completed'?'tag-green':status==='failed'?'tag-red':'tag-blue'}">${escapeHtml(statusLabel)}</span>
      </div>
      <div class="generation-result-meta">
        <div>${escapeHtml(message || (status==='completed'?'生成完成':'正在等待生成结果…'))}</div>
        <div>模型：${escapeHtml(task?.model_name || task?.payload?.model_name || '-')} · 比例：${escapeHtml(task?.ratio || task?.payload?.ratio || '-')} · 分辨率：${escapeHtml(task?.resolution || task?.payload?.resolution || '-')}</div>
        ${errorMessage}
      </div>
      <div class="generation-result-assets">${assetHtml}</div>
    </div>`;
  void loadCleanTaskImages(panel);
}
async function waitForGeneratedTask(taskId,{initialTask=null,timeoutMs=120000,pollIntervalMs=1000,maxNetworkFailures=8}={}){
  let task=initialTask;
  let networkFailures=0;
  const deadline=Date.now()+Math.max(0,timeoutMs);
  while(true){
    if(task){
      renderGenerateResult(task);
      if(GENERATION_TERMINAL_STATUSES.has(String(task.status || '').toLowerCase())) return task;
    }
    if(Date.now()>=deadline) return task;
    await new Promise(resolve=>setTimeout(resolve,pollIntervalMs));
    try{
      const response=await api('GET',`/api/tasks/${taskId}`);
      task=response.task;
      networkFailures=0;
    }catch(error){
      networkFailures+=1;
      const retryMessage=`连接暂时中断，正在重试（${networkFailures}/${maxNetworkFailures}）…`;
      renderGenerateResult(task,retryMessage);
      if(networkFailures>=maxNetworkFailures) return task;
    }
  }
}
async function gatewayGenerate(){
  const prompt=document.getElementById('g-prompt').value.trim();
  const submitButton=document.getElementById('g-submit');
  if(!prompt){
    renderGenerateResult(null,'请输入描述词后再提交。');
    return null;
  }
  const payload={
    kind: document.getElementById('g-kind').value,
    prompt,
    model_name: document.getElementById('g-model').value||null,
    ratio: document.getElementById('g-ratio').value||null,
    resolution: document.getElementById('g-res').value||null,
    duration: document.getElementById('g-dur').value?Number(document.getElementById('g-dur').value):null,
    scene_id: document.getElementById('g-scene').value||null,
    account_id: document.getElementById('g-account').value?Number(document.getElementById('g-account').value):null,
  };
  submitButton.disabled=true;
  submitButton.textContent='生成中…';
  renderGenerateResult(null,'正在提交生成任务…');
  try{
    const r=await api('POST','/api/media/generate',payload);
    const initialTask=r.task || {
      id:r.task_id,
      status:r.status,
      model_name:payload.model_name,
      ratio:payload.ratio,
      resolution:payload.resolution,
      payload,
      assets:[],
    };
    const isVideo=payload.kind==='video';
    const task=await waitForGeneratedTask(r.task_id,{
      initialTask,
      timeoutMs:isVideo?15*60*1000:3*60*1000,
      pollIntervalMs:isVideo?2000:1000,
    });
    if(task){
      const terminal=GENERATION_TERMINAL_STATUSES.has(String(task.status || '').toLowerCase());
      if(!terminal) renderGenerateResult(task,'任务仍在处理中，可前往“任务”页继续查看。');
    }
    await loadTasks();
    return task;
  }catch(error){
    renderGenerateResult(null,`提交生成失败：${error?.message || String(error)}`);
    return null;
  }finally{
    submitButton.disabled=false;
    submitButton.textContent='提交生成';
  }
}

// === Tasks ===
async function loadTasks(){
  return loadOperationalList('tasks','/api/tasks',renderTasks,updateStats);
}
function applyTaskFilters(){
  const page=state.lists.tasks;
  page.filters=listFiltersFromInputs({
    status:'task-filter-status',
    kind:'task-filter-kind',
    model_name:'task-filter-model-name',
    scene_id:'task-filter-scene-id',
    client_id:'task-filter-client-id',
    api_key_id:'task-filter-api-key-id',
    account_id:'task-filter-account-id',
    error_code:'task-filter-error-code',
    date_from:'task-filter-date-from',
    date_to:'task-filter-date-to',
  });
  page.limit=listLimitFromInput('task-page-size',page.limit);
  page.offset=0;
  void loadTasks().catch(()=>{});
}
function resetTaskFilters(){
  clearListInputs([
    'task-filter-status','task-filter-kind','task-filter-model-name','task-filter-scene-id',
    'task-filter-client-id','task-filter-api-key-id','task-filter-account-id',
    'task-filter-error-code','task-filter-date-from','task-filter-date-to',
  ],'task-page-size');
  const page=state.lists.tasks;
  page.filters={};
  page.limit=50;
  page.offset=0;
  void loadTasks().catch(()=>{});
}
function previousTaskPage(){changeOperationalListPage('tasks',-1,loadTasks);}
function nextTaskPage(){changeOperationalListPage('tasks',1,loadTasks);}
function taskCanRetry(status){
  return ['failed','expired'].includes(String(status ?? '').toLowerCase());
}
function taskCanHydrate(status){
  return ['submitted','hydrating'].includes(String(status ?? '').toLowerCase());
}
function taskCanCancel(status){
  return ['queued','running','submitted','hydrating'].includes(String(status ?? '').toLowerCase());
}
function taskActionButtons(task){
  const id=Number(task?.id);
  if(!Number.isSafeInteger(id) || id <= 0) return '';
  const status=String(task?.status ?? '').toLowerCase();
  const buttons=[];
  if(taskCanHydrate(status)) buttons.push(`<button class="btn-sm btn-secondary" onclick="hydrateTask(${id})">重水合</button>`);
  if(taskCanRetry(status)) buttons.push(`<button class="btn-sm btn-secondary" onclick="retryTask(${id})">重试</button>`);
  if(taskCanCancel(status)) buttons.push(`<button class="btn-sm btn-danger" onclick="cancelTask(${id})">取消</button>`);
  return buttons.join('');
}
function renderTasks(){
  const tbody=document.getElementById('tasks-tbody');
  const page=state.lists?.tasks || {loading:false,error:''};
  if(typeof renderListTableState==='function' && renderListTableState(tbody,page,8)) return;
  const tasks=Array.isArray(state.tasks) ? state.tasks : [];
  if(!tasks.length){
    tbody.innerHTML='<tr><td colspan="8" style="text-align:center;color:#86868b">暂无任务</td></tr>';
    return;
  }
  tbody.innerHTML = tasks.map(t => {
    const statusClass=t.status==='completed'?'tag-green':t.status==='failed'?'tag-red':t.status==='cancelled'?'tag-gray':t.status==='submitted'?'tag-blue':'tag-gray';
    const kindClass=t.kind==='image'?'tag-blue':'tag-green';
    return `<tr>
      <td>${t.id}</td>
      <td><span class="tag ${kindClass}">${escapeHtml(adminLabel('kind',t.kind))}</span></td>
      <td>${t.account_id||'-'}</td>
      <td><span class="tag ${statusClass}">${escapeHtml(adminLabel('taskStatus',t.status))}${t.cancel_requested_at ? ' · 取消中' : ''}</span></td>
      <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis">${escapeHtml((t.prompt||'').substring(0,40))}</td>
      <td style="font-family:monospace;font-size:11px">${escapeHtml((t.chat_id||'').substring(0,12))}</td>
      <td style="font-size:11px">${new Date((t.created_at||0)*1000).toLocaleString()}</td>
      <td>
        <div class="task-actions">
          <button class="btn-sm btn-secondary" onclick="loadTaskDetail(${t.id})">详情</button>
          ${taskActionButtons(t)}
        </div>
      </td>
    </tr>`;
  }).join('');
}
function safeAssetUrl(asset){
  const raw=String(asset || '').trim();
  if(/^data:image\/(?:png|jpeg|jpg|gif|webp);base64,[a-z0-9+/=]+$/i.test(raw)) return raw;
  try{
    const parsed=new URL(raw,BASE);
    if(parsed.origin===BASE || parsed.protocol==='https:') return parsed.href;
  }catch(_error){}
  return '';
}
function revokeCleanTaskImages(root){
  if(!root) return;
  root.querySelectorAll('[data-clean-blob-url]').forEach(image=>{
    const blobUrl=image.dataset.cleanBlobUrl;
    if(blobUrl) URL.revokeObjectURL(blobUrl);
    delete image.dataset.cleanBlobUrl;
  });
}
function cleanTaskAssetUrl(taskId,assetIndex){
  return `${BASE}/api/tasks/${taskId}/assets/${assetIndex}/clean`;
}
function imageBlobDataUrl(blob){
  return new Promise((resolve,reject)=>{
    const reader=new FileReader();
    reader.onload=()=>resolve(String(reader.result || ''));
    reader.onerror=()=>reject(reader.error || new Error('图片预览读取失败'));
    reader.readAsDataURL(blob);
  });
}
async function loadCleanTaskImages(root){
  if(!root) return;
  const images=Array.from(root.querySelectorAll('img[data-clean-task-id]'));
  await Promise.all(images.map(async image=>{
    const taskId=Number(image.dataset.cleanTaskId);
    const assetIndex=Number(image.dataset.cleanAssetIndex);
    const originalUrl=safeAssetUrl(image.dataset.originalSrc);
    const shell=image.closest('.clean-asset-shell');
    const status=shell?.querySelector('.clean-asset-status');
    try{
      const response=await fetch(cleanTaskAssetUrl(taskId,assetIndex),{headers:authHeaders()});
      if(!response.ok) throw new Error(`HTTP ${response.status}`);
      const blob=await response.blob();
      if(!String(blob.type || '').startsWith('image/')) throw new Error('返回内容不是图片');
      image.src=await imageBlobDataUrl(blob);
      if(typeof image.decode==='function') await image.decode();
      image.classList.remove('hidden');
      const removed=response.headers.get('x-watermark-removed')==='true';
      if(status) status.textContent=removed ? '已生成无水印预览' : '未检测到可移除的上游水印';
    }catch(_error){
      if(originalUrl){
        image.src=originalUrl;
        if(typeof image.decode==='function') await image.decode().catch(()=>{});
        image.classList.remove('hidden');
      }
      if(status) status.textContent=originalUrl ? '无水印处理失败，已显示上游原图' : '无水印处理失败';
    }
  }));
}
async function downloadCleanTaskAsset(taskId,assetIndex,button=null){
  const originalLabel=button?.textContent || '';
  if(button){
    button.disabled=true;
    button.textContent='处理中…';
  }
  try{
    const response=await fetch(cleanTaskAssetUrl(taskId,assetIndex),{headers:authHeaders()});
    if(!response.ok) throw new Error(`HTTP ${response.status}`);
    const blob=await response.blob();
    const extension=blob.type==='image/png'?'png':blob.type==='image/webp'?'webp':'jpg';
    const blobUrl=URL.createObjectURL(blob);
    const link=document.createElement('a');
    link.href=blobUrl;
    link.download=`任务-${taskId}-无水印-${assetIndex+1}.${extension}`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(()=>URL.revokeObjectURL(blobUrl),0);
  }catch(error){
    showToast(`下载无水印图片失败：${error?.message || String(error)}`,'err');
  }finally{
    if(button){
      button.disabled=false;
      button.textContent=originalLabel;
    }
  }
}
function renderTaskAsset(asset,task=null,assetIndex=0){
  const url=safeAssetUrl(asset);
  if(!url) return '';
  if(/\\.(mp4|mov|webm)(\\?|$)/i.test(url)) {
    return `<video class="task-preview-media" controls src="${escapeHtml(url)}"></video>`;
  }
  const taskId=Number(task?.id);
  if(!Number.isSafeInteger(taskId) || taskId <= 0){
    return `<img class="task-preview-media" src="${escapeHtml(url)}" alt="生成结果">`;
  }
  return `<div class="clean-asset-shell">
    <div class="clean-asset-status">正在生成无水印预览…</div>
    <img class="task-preview-media hidden" data-clean-task-id="${taskId}" data-clean-asset-index="${assetIndex}" data-original-src="${escapeHtml(url)}" alt="无水印生成结果">
    <div class="clean-asset-actions">
      <button class="btn-sm btn-secondary" onclick="downloadCleanTaskAsset(${taskId},${assetIndex},this)">下载无水印</button>
      <a href="${escapeHtml(url)}" target="_blank" rel="noreferrer">打开上游原图</a>
    </div>
  </div>`;
}
function taskAssetLink(asset,index){
  const url=safeAssetUrl(asset);
  if(!url || url.startsWith('data:')) return '';
  return `<div><a href="${escapeHtml(url)}" target="_blank" rel="noreferrer">打开结果 ${index+1}</a></div>`;
}
function renderTaskPreview(task){
  const panel=document.getElementById('task-preview');
  const body=document.getElementById('task-preview-body');
  if(!panel || !body) return;
  revokeCleanTaskImages(body);
  const assets=Array.isArray(task?.assets) ? task.assets : [];
  const attempts=Array.isArray(task?.attempts) ? task.attempts : [];
  const scene=capabilityScenes().find(s => s.scene_id === (task?.scene_id || task?.payload?.scene_id)) || null;
  const model=capabilityModels(task?.kind || 'image').find(m => m.name === (task?.model_name || task?.payload?.model_name)) || null;
  const assetHtml=assets.length ? assets.map((asset,index)=>renderTaskAsset(asset,task,index)).join('') : '<div class="task-preview-meta">暂无结果</div>';
  body.innerHTML = `
    <div class="task-preview-card">
      <h3>基础信息</h3>
      <div class="task-preview-meta">
        <div><strong>ID</strong> ${task?.id || '-'}</div>
        <div><strong>状态</strong> ${escapeHtml(adminLabel('taskStatus',task?.status))}</div>
        <div><strong>模型</strong> ${escapeHtml(task?.model_name || '-')}</div>
        <div><strong>场景</strong> ${escapeHtml(task?.scene_id || '-')}</div>
        <div><strong>验证</strong> ${escapeHtml(adminLabel('verificationStatus',scene?.verification_status || model?.verification_status || task?.verification_status))}</div>
        <div><strong>实验性</strong> ${(scene?.experimental ?? model?.experimental ?? task?.experimental) ? '是' : '否'}</div>
        <div><strong>点数</strong> ${task?.estimated_point_cost ?? '-'}</div>
        <div><strong>实际</strong> ${task?.actual_point_cost ?? '-'}</div>
        <div><strong>前后余额</strong> ${task?.balance_before_rest_point ?? '-'} → ${task?.balance_after_rest_point ?? '-'}</div>
        <div><strong>错误码</strong> ${escapeHtml(task?.error_code || '-')}</div>
      </div>
      <div style="margin-top:12px" class="task-actions">
        ${taskActionButtons(task)}
      </div>
    </div>
    <div class="task-preview-card">
      <h3>结果预览</h3>
      <div class="task-preview-assets">${assetHtml}</div>
      <div style="margin-top:8px;font-size:12px;color:#86868b">${assets.map(taskAssetLink).join('')}</div>
    </div>
    <div class="task-preview-card">
      <h3>参数与尝试</h3>
      <div class="task-preview-meta"><pre style="white-space:pre-wrap">${escapeHtml(JSON.stringify(task?.payload || {}, null, 2))}</pre></div>
      <div class="task-preview-meta" style="margin-top:8px"><pre style="white-space:pre-wrap">${escapeHtml(JSON.stringify(attempts, null, 2))}</pre></div>
    </div>
  `;
  panel.classList.remove('hidden');
  void loadCleanTaskImages(body);
}
async function loadTaskDetail(id){
  const r=await api('GET',`/api/tasks/${id}`);
  renderTaskPreview(r.task);
  return r.task;
}
async function runTaskAction(id, action, label){
  let result;
  try{
    result=await api('POST',`/api/tasks/${id}/${action}`);
  }catch(error){
    showToast(`任务 #${id} ${label}失败：${error?.message || String(error)}`,'err');
    return null;
  }
  renderTaskPreview(result.task);
  try{
    await loadTasks();
  }catch(error){
    showToast(`任务 #${id} ${label}成功，但列表刷新失败：${error?.message || String(error)}`,'warn');
  }
  return result.task;
}
async function retryTask(id){
  return runTaskAction(id,'retry','重试');
}
async function cancelTask(id){
  if(!(await showConfirm(`确认取消任务 #${id}？`,{confirmText:'确认取消',danger:true}))) return null;
  return runTaskAction(id,'cancel','取消');
}
async function hydrateTask(id){
  return runTaskAction(id,'hydrate','重水合');
}

// === API Keys ===
async function loadApiKeys(){const r=await api('GET','/api/admin/apikeys');state.apikeys=r.items||[];renderApiKeys();updateStats();}
function scopeCsv(values){return (Array.isArray(values)?values:[]).join(',');}
function splitScopeInput(rawValue){
  return String(rawValue ?? '').split(',').map(value=>value.trim()).filter((value,index,items)=>value && items.indexOf(value)===index);
}
function optionalNonNegativeIntegerValue(rawValue,label='限额'){
  const text=String(rawValue ?? '').trim();
  if(text==='') return null;
  if(!/^[0-9]+$/.test(text)) throw new Error(`${label}必须是非负整数`);
  const value=Number(text);
  if(!Number.isSafeInteger(value)) throw new Error(`${label}超出安全整数范围`);
  return value;
}
function apiKeyLimitInputValue(rawValue){
  try{
    const value=optionalNonNegativeIntegerValue(rawValue);
    return value===null ? '' : String(value);
  }catch(_error){
    return '';
  }
}
function apiKeyStatusTagClass(status){
  return ({enabled:'tag-green',disabled:'tag-gray',expired:'tag-red',deleted:'tag-gray'})[status] || 'tag-gray';
}
let apiKeyEditorId=null;
function switchApiKeyPanel(panel){
  const showingKeys=panel!=='operations';
  document.getElementById('apikeys-key-panel')?.classList.toggle('hidden',!showingKeys);
  document.getElementById('apikeys-operations-panel')?.classList.toggle('hidden',showingKeys);
  document.getElementById('apikey-tab-keys')?.classList.toggle('active',showingKeys);
  document.getElementById('apikey-tab-operations')?.classList.toggle('active',!showingKeys);
}
function apiKeyDisplayName(key){
  return String(key?.name || key?.client_name || `客户 Key #${key?.id ?? '-'}`);
}
function apiKeyQuotaSummary(key){
  const used=Math.max(0,Number(key?.today_point_usage)||0);
  const configured=key?.daily_point_limit;
  const limit=configured===null || configured===undefined || configured==='' ? null : Math.max(0,Number(configured)||0);
  if(limit===null) return {used,limit:null,main:`${used} / 继承`,sub:'每日点数额度继承系统设置',percent:0};
  if(limit===0) return {used,limit:0,main:`${used} / 不限`,sub:'每日点数不限额',percent:0};
  const remaining=Math.max(0,limit-used);
  return {used,limit,main:`${used} / ${limit}`,sub:`剩余 ${remaining} 点`,percent:Math.min(100,Math.round(used/limit*100))};
}
function apiKeyScopeSummary(key){
  const kinds=Array.isArray(key?.allowed_kinds) && key.allowed_kinds.length ? key.allowed_kinds.map(value=>adminLabel('kind',value)).join('、') : '图片、视频';
  const models=Array.isArray(key?.allowed_models) && key.allowed_models.length ? `${key.allowed_models.length} 个指定模型` : '全部模型';
  return `${escapeHtml(kinds)}<div class="apikey-meta">${escapeHtml(models)}</div>`;
}
function updateApiKeySummary(){
  const keys=Array.isArray(state.apikeys)?state.apikeys:[];
  const active=keys.filter(key=>String(key.status ?? (key.enabled?'enabled':'disabled')).toLowerCase()==='enabled').length;
  const usage=keys.reduce((sum,key)=>sum+(Number(key.today_point_usage)||0),0);
  const totalEl=document.getElementById('ak-summary-total');
  const enabledEl=document.getElementById('ak-summary-enabled');
  const usageEl=document.getElementById('ak-summary-usage');
  if(totalEl) totalEl.textContent=String(keys.filter(key=>String(key.status)!=='deleted').length);
  if(enabledEl) enabledEl.textContent=String(active);
  if(usageEl) usageEl.textContent=String(usage);
}
function renderApiKeys(){
  const tbody=document.getElementById('apikeys-tbody');
  if(!tbody) return;
  const search=String(document.getElementById('ak-search')?.value||'').trim().toLowerCase();
  const statusFilter=String(document.getElementById('ak-status-filter')?.value||'').trim().toLowerCase();
  const keys=(Array.isArray(state.apikeys)?state.apikeys:[]).filter(key=>{
    const status=String(key.status ?? (key.enabled?'enabled':'disabled')).toLowerCase();
    if(statusFilter && status!==statusFilter) return false;
    if(!search) return true;
    return [apiKeyDisplayName(key),key.key_preview,key.id].some(value=>String(value??'').toLowerCase().includes(search));
  });
  updateApiKeySummary();
  if(!keys.length){
    tbody.innerHTML='<tr><td colspan="7" class="empty-state">没有符合条件的客户 Key</td></tr>';
    return;
  }
  tbody.innerHTML = keys.map(k => {
    const kp=escapeHtml(k.key_preview||'');
    const keyStatus=String(k.status ?? (k.enabled ? 'enabled':'disabled')).toLowerCase();
    const statusClass=apiKeyStatusTagClass(keyStatus);
    const quota=apiKeyQuotaSummary(k);
    const toggleLabel=keyStatus==='enabled'?'停用':'启用';
    const disabledActions=keyStatus==='deleted';
    return `<tr>
      <td><div class="apikey-name">${escapeHtml(apiKeyDisplayName(k))}</div><div class="apikey-meta">ID ${k.id} · 创建于 ${new Date((k.created_at||0)*1000).toLocaleDateString()}</div></td>
      <td><div class="apikey-value"><code>${kp}</code><button class="copy-btn" onclick="copyApiKey(${k.id})">复制</button></div></td>
      <td><span class="tag ${statusClass}">${escapeHtml(adminLabel('apiKeyStatus',keyStatus))}</span></td>
      <td class="quota-cell"><div class="quota-main">${escapeHtml(quota.main)}</div><div class="quota-sub">${escapeHtml(quota.sub)} · 今日 ${Number(k.today_request_count)||0} 次</div>${quota.limit>0?`<div class="quota-track"><div class="quota-fill" style="width:${quota.percent}%"></div></div>`:''}</td>
      <td><div class="scope-summary">${apiKeyScopeSummary(k)}</div></td>
      <td><div style="font-size:12px">${k.last_used_at?new Date(k.last_used_at*1000).toLocaleString():'从未调用'}</div></td>
      <td><div class="apikey-actions"><button class="btn-sm btn-secondary" onclick="openApiKeyEditor(${k.id})" ${disabledActions?'disabled':''}>编辑</button><button class="btn-sm btn-secondary" onclick="toggleApiKey(${k.id},${keyStatus!=='enabled'})" ${disabledActions?'disabled':''}>${toggleLabel}</button><button class="btn-sm btn-danger" onclick="deleteKey(${k.id})" ${disabledActions?'disabled':''}>删除</button></div></td>
    </tr>`;
  }).join('');
}
function renderClients(){
  // 历史客户数据仅用于兼容旧 Key 和报表，不再提供独立管理界面。
}
function setApiKeyEditorValue(id,value){
  const element=document.getElementById(id);
  if(element) element.value=value ?? '';
}
function setApiKeyEditorChecked(id,value){
  const element=document.getElementById(id);
  if(element) element.checked=Boolean(value);
}
function openApiKeyEditor(id=null){
  const key=id===null?null:(state.apikeys||[]).find(item=>Number(item.id)===Number(id));
  if(id!==null && !key){showToast('未找到此 Key，请刷新后重试','warn');return;}
  apiKeyEditorId=key?Number(key.id):null;
  document.getElementById('apikey-editor-title').textContent=key?'编辑客户 Key':'创建客户 Key';
  document.getElementById('apikey-editor-subtitle').textContent=key?'修改额度、权限和启用状态。':'创建后即可复制完整 Key。';
  document.getElementById('ak-editor-save').textContent=key?'保存修改':'创建 Key';
  document.getElementById('ak-editor-save').disabled=false;
  setApiKeyEditorValue('ak-editor-name',key?apiKeyDisplayName(key):'');
  setApiKeyEditorValue('ak-editor-note',key?.rotation_note||'');
  setApiKeyEditorValue('ak-editor-rate',apiKeyLimitInputValue(key?.rate_limit_per_minute));
  setApiKeyEditorValue('ak-editor-requests',apiKeyLimitInputValue(key?.daily_request_limit));
  setApiKeyEditorValue('ak-editor-points',apiKeyLimitInputValue(key?.daily_point_limit));
  setApiKeyEditorValue('ak-editor-models',scopeCsv(key?.allowed_models));
  setApiKeyEditorValue('ak-editor-scenes',scopeCsv(key?.allowed_scenes));
  setApiKeyEditorValue('ak-editor-resolutions',scopeCsv(key?.allowed_resolutions));
  setApiKeyEditorValue('ak-editor-durations',scopeCsv(key?.allowed_durations));
  const kinds=Array.isArray(key?.allowed_kinds)&&key.allowed_kinds.length?key.allowed_kinds:['image','video'];
  setApiKeyEditorChecked('ak-editor-kind-image',kinds.includes('image'));
  setApiKeyEditorChecked('ak-editor-kind-video',kinds.includes('video'));
  setApiKeyEditorChecked('ak-editor-uploads',key?key.allow_uploads!==false:true);
  setApiKeyEditorChecked('ak-editor-experimental',Boolean(key?.allow_experimental));
  setApiKeyEditorChecked('ak-editor-enabled',key?String(key.status ?? (key.enabled?'enabled':'disabled'))==='enabled':true);
  document.getElementById('ak-new').classList.add('hidden');
  document.getElementById('ak-new-value').textContent='';
  document.getElementById('apikey-editor-backdrop').classList.remove('hidden');
  document.getElementById('apikey-editor').classList.remove('hidden');
  setTimeout(()=>document.getElementById('ak-editor-name')?.focus(),0);
}
function closeApiKeyEditor(){
  document.getElementById('apikey-editor-backdrop')?.classList.add('hidden');
  document.getElementById('apikey-editor')?.classList.add('hidden');
  apiKeyEditorId=null;
}
function apiKeyEditorBody(){
  const name=String(document.getElementById('ak-editor-name')?.value||'').trim();
  if(!name) throw new Error('请填写客户名称');
  const allowedKinds=[];
  if(document.getElementById('ak-editor-kind-image')?.checked) allowedKinds.push('image');
  if(document.getElementById('ak-editor-kind-video')?.checked) allowedKinds.push('video');
  if(!allowedKinds.length) throw new Error('图片和视频至少允许一种');
  return {
    name,
    rotation_note:String(document.getElementById('ak-editor-note')?.value||'').trim(),
    rate_limit_per_minute:optionalNonNegativeIntegerValue(document.getElementById('ak-editor-rate')?.value,'每分钟请求数'),
    daily_request_limit:optionalNonNegativeIntegerValue(document.getElementById('ak-editor-requests')?.value,'每日请求数'),
    daily_point_limit:optionalNonNegativeIntegerValue(document.getElementById('ak-editor-points')?.value,'每日点数额度'),
    allowed_kinds:allowedKinds,
    allowed_models:splitScopeInput(document.getElementById('ak-editor-models')?.value),
    allowed_scenes:splitScopeInput(document.getElementById('ak-editor-scenes')?.value),
    allowed_resolutions:splitScopeInput(document.getElementById('ak-editor-resolutions')?.value),
    allowed_durations:splitScopeInput(document.getElementById('ak-editor-durations')?.value),
    allow_uploads:Boolean(document.getElementById('ak-editor-uploads')?.checked),
    allow_experimental:Boolean(document.getElementById('ak-editor-experimental')?.checked),
    enabled:Boolean(document.getElementById('ak-editor-enabled')?.checked),
  };
}
async function createApiKey(bodyOverride=null){
  const body=bodyOverride || apiKeyEditorBody();
  const result=await api('POST','/api/admin/apikeys',body);
  if(!result.item?.key) throw new Error('服务端未返回新 Key');
  document.getElementById('ak-new-value').textContent=result.item.key;
  document.getElementById('ak-new').classList.remove('hidden');
  document.getElementById('ak-editor-save').disabled=true;
  await loadApiKeys();
  return result.item;
}
async function updateApiKeyPolicy(id,bodyOverride=null){
  const body=bodyOverride || apiKeyEditorBody();
  try{
    await api('PATCH','/api/admin/apikeys/'+id,body);
  }catch(error){
    showToast('保存失败：'+(error?.message || String(error)),'err');
    return null;
  }
  try{
    await loadApiKeys();
  }catch(error){
    showToast('已保存但刷新失败：'+(error?.message || String(error)),'warn');
  }
  return true;
}
async function saveApiKeyEditor(){
  const saveButton=document.getElementById('ak-editor-save');
  saveButton.disabled=true;
  try{
    if(apiKeyEditorId===null){
      await createApiKey();
    }else{
      const saved=await updateApiKeyPolicy(apiKeyEditorId);
      if(saved) closeApiKeyEditor();
    }
  }catch(error){
    showToast('保存失败：'+(error?.message || String(error)),'err');
    saveButton.disabled=false;
  }
}
async function copyApiKey(id){
  try{
    const result=await api('GET',`/api/admin/apikeys/${id}/secret`);
    await copyText(result.key);
    showToast('完整 Key 已复制','ok');
  }catch(error){
    showToast('复制失败：'+(error?.message || String(error)),'err');
  }
}
async function copyKey(){
  const value=document.getElementById('ak-new-value').textContent;
  if(!value) return;
  await copyText(value);
  showToast('完整 Key 已复制','ok');
}
async function toggleApiKey(id,enabled){
  const action=enabled?'启用':'停用';
  if(!(await showConfirm(`确认${action}此客户 Key？`,{confirmText:'确认'+action,danger:!enabled}))) return;
  const saved=await updateApiKeyPolicy(id,{enabled});
  if(saved) showToast(`已${action}`,'ok');
}
async function deleteKey(id){if(!(await showConfirm('确认删除此客户 Key？删除后不可恢复。',{confirmText:'确认删除',danger:true}))) return; await api('DELETE','/api/admin/apikeys/'+id);await loadApiKeys(); showToast('已删除客户 Key','ok');}

// === Usage ===
async function loadUsage(){
  return loadOperationalList('usage','/api/admin/usage',renderUsage);
}
function applyUsageFilters(){
  const page=state.lists.usage;
  page.filters=listFiltersFromInputs({
    kind:'usage-filter-kind',
    status:'usage-filter-status',
    model_name:'usage-filter-model-name',
    api_key_id:'usage-filter-api-key-id',
    account_id:'usage-filter-account-id',
    error_code:'usage-filter-error-code',
    date_from:'usage-filter-date-from',
    date_to:'usage-filter-date-to',
  });
  page.limit=listLimitFromInput('usage-page-size',page.limit);
  page.offset=0;
  void loadUsage().catch(()=>{});
}
function resetUsageFilters(){
  clearListInputs([
    'usage-filter-kind','usage-filter-status','usage-filter-model-name',
    'usage-filter-api-key-id','usage-filter-account-id','usage-filter-error-code',
    'usage-filter-date-from','usage-filter-date-to',
  ],'usage-page-size');
  const page=state.lists.usage;
  page.filters={};
  page.limit=50;
  page.offset=0;
  void loadUsage().catch(()=>{});
}
function previousUsagePage(){changeOperationalListPage('usage',-1,loadUsage);}
function nextUsagePage(){changeOperationalListPage('usage',1,loadUsage);}
function renderUsage(){
  const tbody=document.getElementById('usage-tbody');
  const page=state.lists?.usage || {loading:false,error:''};
  if(renderListTableState(tbody,page,9)) return;
  const usage=Array.isArray(state.usage) ? state.usage : [];
  if(!usage.length){
    tbody.innerHTML='<tr><td colspan="9" style="text-align:center;color:#86868b">暂无用量记录</td></tr>';
    return;
  }
  tbody.innerHTML = usage.map(u => {
    return `<tr><td>${u.id}</td><td><span class="tag ${u.kind==='image'?'tag-blue':'tag-green'}">${escapeHtml(adminLabel('kind',u.kind))}</span></td><td>${escapeHtml(u.account_email||u.account_id||'-')}</td><td>${escapeHtml(u.model_name||'-')}</td><td>${u.estimated_point_cost ?? '-'}</td><td>${escapeHtml(u.error_code||'-')}</td><td>${escapeHtml(adminLabel('taskStatus',u.status))}</td><td style="max-width:200px;overflow:hidden;text-overflow:ellipsis">${escapeHtml((u.prompt||'').substring(0,40))}</td><td style="font-size:11px">${new Date((u.created_at||0)*1000).toLocaleString()}</td></tr>`;
  }).join('');
}

async function loadUploads(){
  return loadOperationalList('uploads','/api/admin/uploads',renderUploads);
}
function applyUploadFilters(){
  const page=state.lists.uploads;
  page.filters=listFiltersFromInputs({
    kind:'upload-filter-kind',
    status:'upload-filter-status',
    api_key_id:'upload-filter-api-key-id',
    account_id:'upload-filter-account-id',
    date_from:'upload-filter-date-from',
    date_to:'upload-filter-date-to',
  });
  page.limit=listLimitFromInput('upload-page-size',page.limit);
  page.offset=0;
  void loadUploads().catch(()=>{});
}
function resetUploadFilters(){
  clearListInputs([
    'upload-filter-kind','upload-filter-status','upload-filter-api-key-id',
    'upload-filter-account-id','upload-filter-date-from','upload-filter-date-to',
  ],'upload-page-size');
  const page=state.lists.uploads;
  page.filters={};
  page.limit=50;
  page.offset=0;
  void loadUploads().catch(()=>{});
}
function previousUploadPage(){changeOperationalListPage('uploads',-1,loadUploads);}
function nextUploadPage(){changeOperationalListPage('uploads',1,loadUploads);}
function renderUploads(){
  const tbody=document.getElementById('uploads-tbody');
  if(!tbody) return;
  const page=state.lists?.uploads || {loading:false,error:''};
  if(renderListTableState(tbody,page,10)) return;
  const uploads=Array.isArray(state.uploads) ? state.uploads : [];
  if(!uploads.length){
    tbody.innerHTML='<tr><td colspan="10" style="text-align:center;color:#86868b">暂无上传素材</td></tr>';
    return;
  }
  tbody.innerHTML = uploads.map(item => {
    const objectPath=String(item.object_path||'');
    const attachment=JSON.stringify(item.attachment||{});
    const kindClass=item.kind==='image'?'tag-blue':item.kind==='video'?'tag-green':'tag-gray';
    return `<tr><td>${item.id}</td><td><span class="tag ${kindClass}">${escapeHtml(adminLabel('kind',item.kind))}</span></td><td>${escapeHtml(item.account_email||item.account_id||'-')}</td><td>${escapeHtml(item.api_key_name||item.api_key_id||'-')}</td><td>${escapeHtml(item.file_name||'-')}<div style="font-size:11px;color:#86868b">${escapeHtml(item.content_type||'')}</div></td><td style="font-family:monospace;font-size:11px;max-width:220px;overflow:hidden;text-overflow:ellipsis">${escapeHtml(objectPath)}</td><td>${item.related_task_count ?? 0}</td><td>${escapeHtml(adminLabel('uploadStatus',item.status))}</td><td style="font-size:11px">${new Date((item.created_at||0)*1000).toLocaleString()}</td><td><button class="btn-sm btn-secondary" data-copy-value="${escapeHtml(objectPath)}" onclick="copyText(this.dataset.copyValue)">对象路径</button> <button class="btn-sm btn-secondary" data-copy-value="${escapeHtml(attachment)}" onclick="copyText(this.dataset.copyValue)">附件信息</button></td></tr>`;
  }).join('');
}

async function loadCostReport(){
  const params=new URLSearchParams();
  const dateFrom=document.getElementById('cost-date-from')?.value||'';
  const dateTo=document.getElementById('cost-date-to')?.value||'';
  const modelName=document.getElementById('cost-model-name')?.value||'';
  if(dateFrom) params.set('date_from', dateFrom);
  if(dateTo) params.set('date_to', dateTo);
  if(modelName) params.set('model_name', modelName);
  const query=params.toString();
  const r=await api('GET','/api/admin/cost-report'+(query?`?${query}`:''));
  state.costReport=r.items||[];
  renderCostReport();
}
function renderCostReport(){
  const tbody=document.getElementById('cost-report-tbody');
  if(!tbody) return;
  tbody.innerHTML = (state.costReport||[]).map(item => {
    const customerName=item.client_name || item.api_key_name || '-';
    return `<tr><td>${escapeHtml(item.report_date||'-')}</td><td>${escapeHtml(customerName)}</td><td>${escapeHtml(item.account_email||'-')}</td><td>${escapeHtml(item.model_name||'-')}</td><td>${item.request_count ?? 0}</td><td>${item.estimated_point_cost ?? 0}</td><td>${item.actual_point_cost ?? 0}</td><td>${item.success_actual_point_cost ?? 0}</td><td>${item.failed_actual_point_cost ?? 0}</td></tr>`;
  }).join('');
}

async function loadAuditLogs(){const r=await api('GET','/api/admin/audit-logs');state.auditLogs=r.items||[];renderAuditLogs();}
function renderAuditLogs(){
  const tbody=document.getElementById('audit-tbody');
  if(!tbody) return;
  tbody.innerHTML = (state.auditLogs||[]).slice(0,50).map(a => {
    return `<tr><td style="font-size:11px">${new Date((a.created_at||0)*1000).toLocaleString()}</td><td>${escapeHtml(a.admin_username||'-')}</td><td>${escapeHtml(a.action||'-')}</td><td style="font-size:11px">${escapeHtml(a.path||'-')}</td><td>${a.status_code ?? '-'}</td><td style="max-width:260px;overflow:hidden;text-overflow:ellipsis;font-size:11px">${escapeHtml(JSON.stringify(a.details || {}))}</td></tr>`;
  }).join('');
}

// === Settings ===
async function loadSettings(){
  state.settings=await api('GET','/api/admin/settings');
  const s=state.settings;
  document.getElementById('s-port').value=s.server?.port??8894;
  document.getElementById('s-base').value=s.oreate?.base_url||'';
  document.getElementById('s-img-model').value=s.oreate?.default_image_model||'';
  document.getElementById('s-vid-model').value=s.oreate?.default_video_model||'';
  document.getElementById('s-min').value=s.pool?.min_accounts??3;
  document.getElementById('s-target').value=s.pool?.maintain_target??5;
  document.getElementById('s-maintain-interval').value=s.pool?.maintain_check_interval??3600;
  document.getElementById('s-reg-concurrency').value=s.pool?.registration_concurrency??1;
  document.getElementById('s-auto-register-max').value=s.pool?.auto_maintain_max_register??5;
  document.getElementById('s-probe-interval').value=s.pool?.generation_probe_interval_sec??86400;
  document.getElementById('s-probe-max').value=s.pool?.generation_probe_max_per_cycle??3;
  document.getElementById('s-auto-checkin').value=s.pool?.auto_checkin_enabled===false?'false':'true';
  document.getElementById('s-checkin-timezone').value=s.pool?.checkin_timezone||'Asia/Shanghai';
  updateRegistrationConcurrencyHint();
  try{
    const savedCount=Number(localStorage.getItem('oreate_reg_count'));
    if(Number.isSafeInteger(savedCount) && savedCount>=1 && savedCount<=50){
      document.getElementById('reg_count').value=savedCount;
    }
  }catch(_){ }
  document.getElementById('s-mail-provider').value=(s.mail?.provider||'yyds').toLowerCase()==='yyds'?'yyds':'outlook';
  document.getElementById('s-mail-mode').value=s.mail?.api_mode||'auto';
  document.getElementById('s-mail-url').value=s.mail?.base_url||'';
  document.getElementById('s-mail-key').value='';
  document.getElementById('s-mail-key').placeholder=s.mail?.api_key==='__redacted__'?'留空不修改':'mail api key / password';
  document.getElementById('s-mail-domains').value=(s.mail?.preferred_domains||[]).join(',');
  document.getElementById('cred-user').value=s.server?.admin_username||'';
  document.getElementById('settings-raw').textContent=JSON.stringify(s,null,2);
  refreshOutlookPoolHint();
}
function requiredIntegerValue(id,label,min,max=null){
  const raw=document.getElementById(id).value.trim();
  if(!raw || !/^-?[0-9]+$/.test(raw)) throw new Error(`${label}必须是整数`);
  const value=Number(raw);
  if(!Number.isSafeInteger(value)) throw new Error(`${label}超出安全整数范围`);
  if(value < min) throw new Error(`${label}不能小于 ${min}`);
  if(max !== null && value > max) throw new Error(`${label}不能大于 ${max}`);
  return value;
}
async function saveSettings(){
  try{
    const port=requiredIntegerValue('s-port','服务端口',1,65535);
    const minAccounts=requiredIntegerValue('s-min','最低账号数',0);
    const maintainTarget=requiredIntegerValue('s-target','维护目标数',0);
    const maintainInterval=requiredIntegerValue('s-maintain-interval','自动维护间隔',0);
    const registrationConcurrency=requiredIntegerValue('s-reg-concurrency','注册并发数',1,8);
    const autoMaintainMaxRegister=requiredIntegerValue('s-auto-register-max','自动补号上限',0,50);
    const probeInterval=requiredIntegerValue('s-probe-interval','生成探针间隔',0);
    const probeMax=requiredIntegerValue('s-probe-max','每轮自动探针上限',0,200);
    const autoCheckinEnabled=document.getElementById('s-auto-checkin').value==='true';
    const checkinTimezone=document.getElementById('s-checkin-timezone').value.trim()||'Asia/Shanghai';
    if(maintainTarget < minAccounts) throw new Error('维护目标数不能小于最低账号数');
    const doms=document.getElementById('s-mail-domains').value.split(',').map(s=>s.trim()).filter(Boolean);
    const body={
      server:{port},
      oreate:{
        base_url:document.getElementById('s-base').value,
        default_image_model:document.getElementById('s-img-model').value,
        default_video_model:document.getElementById('s-vid-model').value,
      },
      mail:{
        provider:document.getElementById('s-mail-provider').value,
        api_mode:document.getElementById('s-mail-mode').value,
        base_url:document.getElementById('s-mail-url').value,
        preferred_domains:doms,
      },
      pool:{
        min_accounts:minAccounts,
        maintain_target:maintainTarget,
        maintain_check_interval:maintainInterval,
        registration_concurrency:registrationConcurrency,
        auto_maintain_max_register:autoMaintainMaxRegister,
        generation_probe_interval_sec:probeInterval,
        generation_probe_max_per_cycle:probeMax,
        auto_checkin_enabled:autoCheckinEnabled,
        checkin_timezone:checkinTimezone,
      },
    };
    const mailKey=document.getElementById('s-mail-key').value.trim();
    if(mailKey) body.mail.api_key=mailKey;
    const r=await api('PUT','/api/admin/settings',body);
    const restartMessage=r.restart_required ? '，服务端口变更需重启后生效' : '';
    try{
      await loadSettings();
    }catch(refreshError){
      showToast('已保存但刷新失败'+restartMessage+'：'+(refreshError?.message || String(refreshError)),'warn');
      return;
    }
    showToast('已保存'+restartMessage,'ok');
  }catch(error){
    showToast('保存失败：'+(error?.message || String(error)),'err');
  }
}
async function changeCredentials(){
  const body={
    current_password:document.getElementById('cred-current').value,
    new_username:document.getElementById('cred-user').value,
    new_password:document.getElementById('cred-pass').value,
    confirm_password:document.getElementById('cred-confirm').value,
  };
  if(!body.current_password || !body.new_username || !body.new_password || !body.confirm_password){
    showToast('请填写当前密码、新用户名、新密码和确认密码','warn');
    return;
  }
  const r=await api('POST','/api/admin/credentials',body);
  if(r.ok){
    document.getElementById('cred-current').value='';
    document.getElementById('cred-pass').value='';
    document.getElementById('cred-confirm').value='';
    adminToken='';
    localStorage.removeItem('oreate_admin_token');
    document.getElementById('login-user').value=body.new_username;
    showLogin('账号密码已修改，请重新登录');
  }
}
function updateStats(){
  const a=state.accounts||[];
  document.getElementById('st-total').textContent=a.length;
  document.getElementById('st-verified').textContent=a.filter(accountIsGenerateReady).length;
  document.getElementById('st-tasks').textContent=state.lists.tasks.total;
  document.getElementById('st-apikeys').textContent=(state.apikeys||[]).length;
}
document.addEventListener('keydown',event=>{
  if(event.key==='Escape' && !document.getElementById('apikey-editor')?.classList.contains('hidden')){
    closeApiKeyEditor();
  }
});
init();
</script>
</body>
</html>"""
