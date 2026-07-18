"""Public read-only catalog page for available model cost combos."""

MODELS_PUBLIC_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>可用模型</title>
<style>
:root{
  --bg:#eef2f6;
  --panel:#ffffff;
  --ink:#15202b;
  --muted:#5b6b7c;
  --line:#d5dee8;
  --ok:#0f7a45;
  --ok-bg:#e8f7ee;
  --warn:#9a6700;
  --warn-bg:#fff6dd;
  --bad:#6b7280;
  --bad-bg:#eef0f3;
  --accent:#1f4b7a;
}
*{box-sizing:border-box}
body{
  margin:0;
  font-family:"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
  color:var(--ink);
  background:
    radial-gradient(1200px 500px at 10% -10%, #dce7f3 0%, transparent 55%),
    radial-gradient(900px 420px at 100% 0%, #e7eef5 0%, transparent 50%),
    var(--bg);
  min-height:100vh;
}
.page{max-width:980px;margin:0 auto;padding:40px 20px 64px}
h1{margin:0 0 8px;font-size:32px;letter-spacing:.02em;font-weight:700}
.lead{margin:0 0 22px;color:var(--muted);font-size:14px;line-height:1.6}
.filters{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:18px;align-items:center}
.chip{
  border:1px solid var(--line);
  background:#fff;
  color:var(--ink);
  border-radius:999px;
  padding:7px 12px;
  font-size:13px;
  cursor:pointer;
}
.chip.active{background:var(--accent);border-color:var(--accent);color:#fff}
.chip.ghost{background:transparent}
select.chip{
  appearance:none;
  padding-right:28px;
  background-image:linear-gradient(45deg,transparent 50%,var(--muted) 50%),linear-gradient(135deg,var(--muted) 50%,transparent 50%);
  background-position:calc(100% - 14px) 55%, calc(100% - 9px) 55%;
  background-size:5px 5px,5px 5px;
  background-repeat:no-repeat;
}
.group{
  background:var(--panel);
  border:1px solid var(--line);
  border-radius:14px;
  margin-bottom:12px;
  overflow:hidden;
}
.group-head{
  display:flex;justify-content:space-between;gap:12px;align-items:center;
  padding:14px 16px;cursor:pointer;user-select:none;
}
.group-head:hover{background:#f7fafc}
.group-title{display:flex;flex-wrap:wrap;gap:8px;align-items:center;font-weight:650}
.tag{
  font-size:11px;padding:2px 8px;border-radius:999px;border:1px solid var(--line);
  color:var(--muted);font-weight:500;
}
.meta{color:var(--muted);font-size:12px;white-space:nowrap}
.combos{display:none;border-top:1px solid var(--line)}
.group.open .combos{display:block}
.row{
  display:grid;
  grid-template-columns:1.2fr 1.4fr .7fr .8fr;
  gap:10px;
  padding:12px 16px;
  border-top:1px solid #eef2f6;
  font-size:13px;
  align-items:center;
}
.row:first-child{border-top:none}
.params{color:var(--muted)}
.point{font-variant-numeric:tabular-nums;font-weight:650}
.pill{
  display:inline-flex;align-items:center;justify-content:center;
  min-width:72px;padding:3px 10px;border-radius:999px;font-size:12px;font-weight:600;
}
.pill.available{background:var(--ok-bg);color:var(--ok)}
.pill.tight{background:var(--warn-bg);color:var(--warn)}
.pill.unavailable{background:var(--bad-bg);color:var(--bad)}
.empty,.error{padding:28px;text-align:center;color:var(--muted);background:#fff;border:1px dashed var(--line);border-radius:14px}
.note{margin-top:18px;font-size:12px;color:var(--muted);line-height:1.6}
@media (max-width:720px){
  .row{grid-template-columns:1fr;gap:4px}
  .meta{white-space:normal}
}
</style>
</head>
<body>
<main class="page">
  <h1>可用模型</h1>
  <p class="lead">查看当前可调用的模型与积分组合。可用性随服务容量变化；积分以实际扣费为准。</p>
  <div class="filters">
    <button class="chip active" data-kind="all" onclick="setKind('all')">全部</button>
    <button class="chip" data-kind="image" onclick="setKind('image')">图片</button>
    <button class="chip" data-kind="video" onclick="setKind('video')">视频</button>
    <select id="scene-filter" class="chip" onchange="render()"></select>
    <button class="chip ghost" id="only-available" onclick="toggleOnlyAvailable()">仅显示可用</button>
    <button class="chip ghost" id="show-experimental" onclick="toggleExperimental()">显示未验证</button>
  </div>
  <div id="catalog"><div class="empty">加载中…</div></div>
  <p class="note">本页不展示账号或号池明细；状态为当前容量快照的抽象结果（可用 / 紧张 / 暂不可用）。</p>
</main>
<script>
const state={kind:'all', onlyAvailable:true, showExperimental:false, payload:null};
const STATUS_LABEL={available:'可用', tight:'紧张', unavailable:'暂不可用'};

function setKind(kind){
  state.kind=kind;
  document.querySelectorAll('.chip[data-kind]').forEach(el=>el.classList.toggle('active', el.dataset.kind===kind));
  render();
}
function toggleOnlyAvailable(){
  state.onlyAvailable=!state.onlyAvailable;
  document.getElementById('only-available').classList.toggle('active', state.onlyAvailable);
  render();
}
function toggleExperimental(){
  state.showExperimental=!state.showExperimental;
  document.getElementById('show-experimental').classList.toggle('active', state.showExperimental);
  render();
}
function formatParams(item){
  const parts=[];
  if(item.resolution) parts.push(String(item.resolution));
  if(item.duration!=null) parts.push(item.duration+'秒');
  if(item.is_audio===true) parts.push('有音频');
  if(item.is_audio===false) parts.push('无音频');
  return parts.join(' · ') || '默认参数';
}
function filteredItems(){
  const scene=document.getElementById('scene-filter').value;
  return (state.payload?.items||[]).filter(item=>{
    if(state.kind!=='all' && item.kind!==state.kind) return false;
    if(state.onlyAvailable && item.status==='unavailable') return false;
    if(!state.showExperimental && item.verification_status==='unverified') return false;
    if(scene && item.kind==='video' && item.scene_id!==scene) return false;
    return true;
  });
}
function fillScenes(){
  const select=document.getElementById('scene-filter');
  const scenes=new Map();
  for(const item of (state.payload?.items||[])){
    if(item.kind==='video' && item.scene_id){
      scenes.set(item.scene_id, item.scene_name || item.scene_id);
    }
  }
  const current=select.value;
  select.innerHTML='<option value=\"\">全部场景</option>'+
    [...scenes.entries()].map(([id,name])=>`<option value="${id}">${name}</option>`).join('');
  if([...scenes.keys()].includes(current)) select.value=current;
}
function render(){
  const host=document.getElementById('catalog');
  const items=filteredItems();
  if(!state.payload){host.innerHTML='<div class="empty">加载中…</div>';return;}
  if(!items.length){host.innerHTML='<div class="empty">当前筛选下没有可展示的组合</div>';return;}
  const groups=new Map();
  for(const item of items){
    const key=item.kind+'::'+item.model_name;
    if(!groups.has(key)){
      groups.set(key,{kind:item.kind, model_name:item.model_name, experimental:!!item.experimental, verification_status:item.verification_status, combos:[]});
    }
    const g=groups.get(key);
    g.combos.push(item);
    if(item.experimental) g.experimental=true;
  }
  host.innerHTML=[...groups.values()].map((group, index)=>{
    const available=group.combos.filter(c=>c.status==='available'||c.status==='tight').length;
    const tags=[
      `<span class="tag">${group.kind==='image'?'图片':'视频'}</span>`,
      group.verification_status==='live_verified'?'<span class="tag">已验证</span>':'',
      group.experimental?'<span class="tag">实验性</span>':'',
    ].filter(Boolean).join('');
    const rows=group.combos.map(item=>`
      <div class="row">
        <div>${item.kind==='video'?(item.scene_name||item.scene_id||'视频'):'图片'}</div>
        <div class="params">${formatParams({...item, scene_name:'', scene_id:''})}</div>
        <div class="point">${item.point_cost} 积分</div>
        <div><span class="pill ${item.status}">${STATUS_LABEL[item.status]||item.status}</span></div>
      </div>`).join('');
    return `<section class="group ${index===0?'open':''}">
      <div class="group-head" onclick="this.parentElement.classList.toggle('open')">
        <div class="group-title"><span>${group.model_name}</span>${tags}</div>
        <div class="meta">${available} / ${group.combos.length} 个组合可用</div>
      </div>
      <div class="combos">${rows}</div>
    </section>`;
  }).join('');
}
async function load(){
  try{
    const resp=await fetch('/api/public/model-availability');
    if(!resp.ok) throw new Error('HTTP '+resp.status);
    state.payload=await resp.json();
    fillScenes();
    document.getElementById('only-available').classList.toggle('active', state.onlyAvailable);
    render();
  }catch(err){
    document.getElementById('catalog').innerHTML=`<div class="error">加载失败：${err.message||err}</div>`;
  }
}
load();
</script>
</body>
</html>
"""
