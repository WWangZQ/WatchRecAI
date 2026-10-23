import { invoke, convertFileSrc } from '@tauri-apps/api/core';
import { listen } from '@tauri-apps/api/event';
import { getCurrentWebviewWindow } from '@tauri-apps/api/webviewWindow';
import { open, save } from '@tauri-apps/plugin-dialog';
import { writeText } from '@tauri-apps/plugin-clipboard-manager';
import './style.css';
import logo from './assets/watchrec.svg';

type Field = 'raw' | 'full_text' | 'summary' | 'translation';
interface Translation { target: string; provider: string; status: string; phase: string; done: number; total: number; text: string; error: string | null; outdated: boolean }
interface Capture { state: string; message: string; recording: boolean; peak: number; captured_bytes: number; recovery_attempts: number }
interface Job { id: string; title: string; created_utc: string; microphone: boolean; paused: boolean; state: string; status: string; finished: boolean; captured_bytes: number; acknowledged_bytes: number; duration_seconds: number | null; raw: string; capture_error: string | null; provider: string }
interface Ai { pipeline: string; draft_text: string; status: string; phase: string; done: number; total: number; full_text: string; summary: string; headline: string; source_preserved_parts: number[]; error: string | null; error_kind: string; retry_after: number; retry_count: number }
interface RecordItem { id: string; title: string; custom_title: string | null; created_utc: string; finished: boolean; paused: boolean }
interface ServerSync { status: string; message: string; error?: string | null; bytes?: number; total_bytes?: number; last_checked?: string | null }
interface Snapshot { jobs: RecordItem[]; selected: Job | null; capture: Capture | null; recording_job: string | null; processing_job: string | null; processing_jobs: string[]; ai: Record<string,Ai>; pending_jobs: number; recovery_errors: string[]; server_sync: ServerSync; active_asr_jobs: string[]; translations: Record<string,Translation> }
interface Settings { ai_enabled: boolean; llm_base_url: string; llm_model: string; api_key_set: boolean; python_path: string; default_asr: string; server_enabled: boolean; server_url: string; server_token_set: boolean; server_ca_cert: string }

document.querySelector('#app')!.innerHTML = `
  <aside class="sidebar">
    <div class="brand"><img class="brand-mark" src="${logo}" alt=""><div><h1>WatchRec</h1><span class="edition">NEXT · 开源版</span></div></div>
    <div class="library-heading"><span>我的录音</span><span id="recordCount">0</span></div>
    <input id="search" class="search" type="search" placeholder="搜索录音标题" aria-label="搜索录音标题">
    <nav id="history" aria-label="录音列表"></nav>
    <div class="sidebar-footer"><button id="settings" class="quiet">设置</button><button id="folder" class="quiet">记录文件夹 ↗</button></div>
  </aside>
  <main>
    <header><div><div class="eyebrow">从声音，到清晰的记录</div><h2>留住此刻的想法</h2></div><div class="app-state"><i id="globalDot"></i><span id="globalState">准备就绪</span></div></header>
    <section class="capture-panel" aria-label="录音与导入">
      <div class="capture-actions"><button id="record" class="primary"><span class="record-dot"></span>开始录音</button><button id="stop" hidden>停止录音</button><button id="file">导入音频 <span aria-hidden="true">↗</span></button><div class="mode-control"><label for="provider">转写方式</label><select id="provider"><option value="local">本地 SenseVoice</option></select></div></div>
      <div class="capture-detail"><select id="device" aria-label="麦克风"><option value="">系统默认麦克风</option></select><div class="meter"><i id="level"></i></div><time id="clock">00:00</time><span id="captureMessage">停止录音或导入文件后，自动开始本地转写</span><button id="retryMic" class="quiet compact" hidden>重试麦克风</button></div>
      <div class="server-row"><div><span id="serverStatus" role="status">正在检查服务器连接</span><span id="serverProgress"></span></div><button id="syncNow" class="quiet compact">立即同步</button></div>
    </section>
    <section class="paper" aria-label="录音内容">
      <div class="paper-heading"><div class="paper-title"><span class="eyebrow" id="recordDate">你的下一段记录</span><h3 id="recordTitle">还没有录音</h3><p id="detail">开始录音，也可以把音频文件拖进来。</p></div><button id="rename" class="quiet compact" hidden>改标题</button></div>
      <div id="playback" class="playback" hidden><span>回听</span><select id="audioPart" aria-label="录音分段" hidden></select><audio id="audio" controls preload="metadata"></audio><span id="audioHint" class="muted" hidden>当前格式可在文件夹中打开回听</span></div>
      <div class="tabbar"><div class="tabs" role="tablist" aria-label="稿件版本"><button id="rawTab" role="tab" aria-selected="true">原文<span>01</span></button><button id="fullTab" role="tab" aria-selected="false">清晰全文<span>02</span></button><button id="summaryTab" role="tab" aria-selected="false" hidden>旧版摘要</button><button id="translationTab" role="tab" aria-selected="false">译文<span>03</span></button></div><span id="aiBadge"></span></div>
      <div id="translationTools" class="translation-tools" hidden><div><label for="translationTarget">译为</label><select id="translationTarget"><option value="zh">中文</option><option value="en">English</option></select><button id="translateRun" class="compact">开始翻译</button><span class="translation-provider">使用已配置的 AI 接口</span></div><p id="translationStatus" role="status"></p></div>
      <div class="writing-area"><textarea id="transcript" readonly aria-label="转写文字" spellcheck="false"></textarea><div id="empty"><div class="sound-lines" aria-hidden="true"><i></i><i></i><i></i><i></i><i></i><i></i><i></i></div><h3 id="emptyTitle">让声音变成文字</h3><p id="emptyHint">音频保存在本机，停止录音后自动转写；可选生成清晰全文。</p></div></div>
      <div class="paper-footer"><span id="characters">0 字</span><div><button id="translateOpen" class="quiet" hidden>翻译全文</button><button id="copy" class="quiet" disabled>复制</button><button id="export" class="quiet" disabled>导出文本 ↗</button></div></div>
    </section>
    <footer><div class="progress-track"><i id="progress"></i></div><div class="status-row"><span id="status" role="status">音频和文字自动保存在本机</span><div class="status-actions"><button id="pause" class="quiet compact" hidden>暂停转写</button><button id="retryAi" class="quiet compact" hidden>生成整理稿</button><button id="local" class="quiet compact" hidden>用本地模型重转</button></div></div></footer>
  </main>
  <div id="notice" role="alert" hidden></div>
  <div id="dropOverlay" hidden><h2>放开，开始转写</h2><p>按当前选定的转写方式处理音频</p></div>
  <dialog id="settingsDialog" aria-labelledby="settingsTitle"><div class="eyebrow">WATCHREC / 偏好设置</div><h2 id="settingsTitle">文字整理</h2><p class="intro">本地模型生成原文。启用 AI 后，文字会发送到你配置的接口，用于全文整理、标题和翻译。</p><form id="settingsForm"><label class="check-label"><input id="aiEnabled" type="checkbox">启用 AI 文字整理与翻译</label>
    <label>OpenAI 兼容接口地址<input id="baseUrl" type="url"></label><label>模型<input id="model"></label><label>API Key<input id="apiKey" type="password" autocomplete="off" placeholder="填写 API Key"></label><p id="keyHint" class="muted"></p>
    <details id="serverSettings"><summary>服务器录音同步</summary><label class="check-label"><input id="serverEnabled" type="checkbox">打开后自动下载并转写服务器录音</label><label>服务器地址<input id="serverUrl" type="url" placeholder="https://服务器地址"></label><label>访问令牌<input id="serverToken" type="password" autocomplete="off" placeholder="留空保留现有令牌"></label><p id="serverTokenHint" class="muted"></p><label>服务器证书路径<input id="serverCert" placeholder="使用系统信任的证书时可留空"></label><p class="muted">每 30 秒检查新录音。下载中断后会继续，完成转写后回报服务器。下载完成后进入本地转写队列。</p></details>
    <details><summary>本地转写环境</summary><label>Python 路径<input id="pythonPath" required></label><p class="muted">选择已安装 FunASR、PyTorch 和 requests 的 Python。模型按任务加载，首次转写需要下载模型；请先安装 FFmpeg。</p></details>
    <div class="history-import"><button id="importHistory" type="button" class="quiet">导入旧版录音与稿件 ↗</button><p class="muted">选择旧版 downloads 文件夹。导入音频与已有原文、全文和摘要；未转写音频进入本地队列。重复导入会跳过。</p></div>
    <div class="modal-actions"><button id="settingsCancel" type="button" class="quiet">取消</button><button type="submit" class="primary">保存设置</button></div>
  </form></dialog>
  <dialog id="renameDialog" aria-labelledby="renameTitle"><h2 id="renameTitle">录音标题</h2><form id="renameForm"><input id="titleInput" aria-label="录音标题" maxlength="120"><p class="muted">留空可恢复自动标题。</p><div class="modal-actions"><button id="renameCancel" type="button" class="quiet">取消</button><button type="submit" class="primary">保存</button></div></form></dialog>
  <dialog id="closeDialog" aria-labelledby="closeTitle"><div class="eyebrow">录音与保存</div><h2 id="closeTitle">把录音留在后台？</h2><p id="closeDescription"></p><div class="modal-actions"><button id="closeCancel" class="quiet">留在窗口</button><button id="closeExit">停止并退出</button><button id="closeBackground" class="primary">后台继续录音</button></div></dialog>`;

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
let selected: string | null = null, state: Snapshot | null = null, field: Field = 'raw', busy = false, polling = false;
let translationTarget = 'zh';
let settings: Settings = { ai_enabled:false,llm_base_url:'',llm_model:'',api_key_set:false,python_path:'',default_asr:'local',server_enabled:false,server_url:'',server_token_set:false,server_ca_cert:'' };
let historyKey = '', noticeTimer = 0, audioKey = '', audioPaths: string[] = [], recoveryShown = false;
let textViewKey = '';
const transcript = $<HTMLTextAreaElement>('transcript');
const closeDialog = $<HTMLDialogElement>('closeDialog');
const micActive = () => !!state?.capture && !['stopped','failed'].includes(state.capture.state);
const currentAi = () => selected ? state?.ai[selected] : undefined;
const retrying = new Set<string>();
const currentTranslation = () => state?.translations?.[translationTarget];
const currentText = () => field === 'raw' ? (state?.selected?.raw ?? '') : field === 'translation' ? (currentTranslation()?.outdated ? '' : currentTranslation()?.text ?? '') : (field === 'full_text' && currentAi()?.draft_text ? currentAi()!.draft_text : currentAi()?.[field] ?? '');
const provider = () => $<HTMLSelectElement>('provider').value;
const titleOf = (item: RecordItem) => item.custom_title || state?.ai[item.id]?.headline || item.title || '语音笔记';
function duration(seconds: number) { const n=Math.max(0,Math.floor(seconds)); return n>=3600 ? Math.floor(n/3600)+':'+String(Math.floor(n/60)%60).padStart(2,'0')+':'+String(n%60).padStart(2,'0') : String(Math.floor(n/60)).padStart(2,'0')+':'+String(n%60).padStart(2,'0'); }
function recordingDate(value: string) { return new Date(value.replace(/^(\d{4}-\d{2}-\d{2})[ _](\d{2})-(\d{2})-(\d{2})$/, '$1T$2:$3:$4').replace(' ','T')); }
function dateParts(value: string) { const date=recordingDate(value); return Number.isNaN(date.getTime()) ? { day:value.slice(0,10)||'历史录音',time:'' } : { day:date.toLocaleDateString('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit'}),time:date.toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',hour12:false}) }; }
function notice(message: unknown) { clearTimeout(noticeTimer); $('notice').textContent=String(message); $('notice').hidden=false; noticeTimer=window.setTimeout(()=>{$('notice').hidden=true;},6500); }
function renderHistory() {
  if (!state) return;
  const search=$<HTMLInputElement>('search').value.trim().toLowerCase();
  const key=JSON.stringify([state.jobs.map(j=>[j.id,titleOf(j),j.finished,j.paused,state!.ai[j.id]?.status,state!.active_asr_jobs.includes(j.id)]),selected,search]);
  if(key===historyKey) return; historyKey=key;
  $('recordCount').textContent=String(state.jobs.length);
  const list=$('history'), top=list.scrollTop; list.replaceChildren(); let lastDay='';
  for(const item of state.jobs.filter(j=>titleOf(j).toLowerCase().includes(search))) {
    const date=dateParts(item.created_utc);
    if(date.day!==lastDay) { const label=document.createElement('div');label.className='date-label';label.textContent=date.day;list.append(label);lastDay=date.day; }
    const button=document.createElement('button');button.className='record-item';button.dataset.id=item.id;button.setAttribute('aria-current',String(item.id===selected));
    const title=document.createElement('span');title.className='item-title';title.textContent=titleOf(item);
    const meta=document.createElement('span');meta.className='item-meta';
    const ai=state.ai[item.id];meta.textContent=date.time+' · '+(!item.finished?(item.paused?'已暂停':state.active_asr_jobs.includes(item.id)?'转写中':'等待转写'):ai?.status==='done'||ai?.status==='imported'?'已保存':ai?.status==='empty'?'未识别到文字':ai?.status==='error'?'整理待重试':ai?.status==='retry-wait'?'连接中断 · 自动重试':ai?.status==='running'?'AI 整理中':!settings.ai_enabled?'原文已保存':'等待 AI 整理');
    button.append(title,meta);button.onclick=()=>{selected=item.id;audioKey='';void refresh();};list.append(button);
  }
  if(!list.childElementCount) { const empty=document.createElement('p');empty.className='library-empty';empty.textContent=search?'没有匹配的录音':'你的录音会留在这里。';list.append(empty); }
  list.scrollTop=top;
}
function renderText() {
  const value=currentText();
  const key=selected+':'+field+':'+(field==='translation'?translationTarget:'');
  const changedView=key!==textViewKey;textViewKey=key;
  if(transcript.value!==value||changedView) {
    const nearEnd=transcript.scrollHeight-transcript.clientHeight-transcript.scrollTop<40;
    const top=transcript.scrollTop,start=transcript.selectionStart,end=transcript.selectionEnd;
    transcript.value=value;transcript.setSelectionRange(changedView?0:Math.min(start,value.length),changedView?0:Math.min(end,value.length));
    transcript.scrollTop=changedView?0:(field==='raw'||field==='full_text')&&nearEnd&&start===end?transcript.scrollHeight:top;
  }
  $('empty').hidden=!!value;
  $('summaryTab').hidden=!!currentAi()?.pipeline || !currentAi()?.summary;
  if(field==='summary' && $('summaryTab').hidden) { field='full_text';renderText();return; }
  $('emptyTitle').textContent=field==='raw'?'让声音变成文字':field==='full_text'?'把表达整理清楚':field==='translation'?'换一种语言阅读':'留下值得记住的要点';
  const job=state?.selected;
  $('emptyHint').textContent=!job?'音频保存在本机，停止录音后自动转写；可选生成清晰全文。':field==='raw'?(job.finished?'这段音频没有识别出文字。':job.provider==='local'?'本地模式在录音停止后开始识别。':'文字会随识别陆续显示，句尾可能继续修正。'):currentAi()?.status==='retry-wait'?'AI 连接暂时中断，稍后自动继续，已完成段落已保存。':currentAi()?.error?'整理暂未完成，可在下方重试。':!settings.ai_enabled?'可在设置中启用 AI 全文整理。':!job.finished?'原文识别完成后，会自动生成。':'正在等待整理，完成的稿件会显示在这里。';
  const translated=currentTranslation();
  $('translationTools').hidden=field!=='translation';
  $('translateOpen').hidden=field!=='full_text'||!currentAi()?.full_text;
  $<HTMLButtonElement>('translateRun').disabled=!settings.ai_enabled||!currentAi()?.full_text||translated?.status==='running'||translated?.status==='done'&&!translated.outdated;
  $('translateRun').textContent=translated?.status==='running'?'翻译中…':translated?.outdated?'重新翻译':translated?.status==='done'?'已保存':translated?.done?'继续翻译':'开始翻译';
  $('translationStatus').textContent=translated?.outdated?'全文已更新，点击重新翻译可生成对应译文。':translated?.error??(translated?.status==='running'?'已翻译 '+translated.done+' / '+translated.total+' 段':translated?.status==='done'?'译文已独立保存，可复制或导出。':'选择目标语言后开始，已完成的段落会自动保存。');
  $('translationStatus').className=translated?.error||translated?.outdated?'warning':'';
  if(field==='translation') $('emptyHint').textContent=!currentAi()?.full_text?'先生成全文，再选择目标语言翻译。':translated?.outdated?'全文已更新，请重新生成译文。':translated?.error??(translated?.status==='running'?'正在翻译，完成的段落会陆续显示。':'启用 AI 后点击开始翻译，译文会独立保存。');
  if(job?.finished&&!job.raw.trim()&&currentAi()?.status==='empty') $('emptyHint').textContent='这段音频未识别到文字，无法生成全文、总结或译文。音频已保留，可以回听。';
  $('characters').textContent=Array.from(value).length.toLocaleString()+' 字';
  $<HTMLButtonElement>('copy').disabled=!value;
  $<HTMLButtonElement>('export').disabled=!value || field==='full_text' && !!currentAi()?.draft_text;
  for(const [id,name] of [['rawTab','raw'],['fullTab','full_text'],['summaryTab','summary'],['translationTab','translation']]) $(id).setAttribute('aria-selected',String(field===name));
}
const audioUrl = (path: string) => path.startsWith('http://127.0.0.1:') ? path : convertFileSrc(path);
async function loadAudio(id: string, key: string) {
  try {
    const files=await invoke<string[]>('audio_files',{id});
    if(audioKey!==key) return;
    audioPaths=files; const player=$<HTMLAudioElement>('audio');player.pause();player.removeAttribute('src');player.load();$('audioHint').hidden=true;
    const select=$<HTMLSelectElement>('audioPart');select.replaceChildren();
    files.forEach((_,i)=>select.add(new Option('第 '+(i+1)+' 段',String(i))));
    select.hidden=files.length<2;$('playback').hidden=!files.length;
    if(files.length) player.src=audioUrl(files[0]);
  } catch(error) { notice(error); }
}
function render() {
  if(!state) return; if(state.selected) selected=state.selected.id;
  const sync=state.server_sync;
  $('serverStatus').textContent=sync.error?sync.message+'：'+sync.error:sync.message;
  $('serverStatus').className=sync.status==='error'?'warning':'';
  $('serverStatus').title=sync.last_checked?'上次检查：'+new Date(sync.last_checked).toLocaleString('zh-CN'):'';
  $('serverProgress').textContent=sync.status==='downloading'&&sync.total_bytes?((sync.bytes??0)/1048576).toFixed(1)+' / '+(sync.total_bytes/1048576).toFixed(1)+' MB':'';
  $<HTMLButtonElement>('syncNow').disabled=['checking','downloading','reporting'].includes(sync.status)||!settings.server_enabled;
  const processingJobs=state.processing_jobs??(state.processing_job?[state.processing_job]:[]);
  const cap=state.capture,job=state.selected,ai=currentAi(),active=micActive(),processing=!!selected&&processingJobs.includes(selected);
  $<HTMLButtonElement>('record').disabled=busy||active;$('record').hidden=active;$('stop').hidden=!active;
  $<HTMLButtonElement>('file').disabled=busy;$<HTMLSelectElement>('device').disabled=active||busy;$<HTMLSelectElement>('provider').disabled=active||busy;
  $('globalDot').className=cap?.recording?'recording':active?'waiting':'';
  const waitingNetwork=Object.values(state.ai).filter(a=>a.status==='retry-wait').length;
  const activity=[cap?.recording?'录音中':active?'等待麦克风':'',state.active_asr_jobs.length?'正在转写 '+state.active_asr_jobs.length+' 条':'',processingJobs.length?'正在整理 '+processingJobs.length+' 条':'',waitingNetwork?waitingNetwork+' 条等待连接重试':''].filter(Boolean);
  $('globalState').textContent=activity.join(' · ')||(state.pending_jobs?'转写待完成':'准备就绪');
  $('clock').textContent=duration((cap?.captured_bytes??0)/32000);
  $('level').style.width=(Math.min(1,cap?.peak??0)*100)+'%';
  $('captureMessage').textContent=active?(cap?.message??'等待麦克风'):provider()==='local'?'停止录音后本地识别，模型按需加载':'停止录音或导入文件后，自动开始本地转写';
  $('retryMic').hidden=!active||!!cap?.recording;
  $('recordTitle').textContent=job?titleOf(state.jobs.find(j=>j.id===job.id)!):'还没有录音';
  const date=job?dateParts(job.created_utc):null;$('recordDate').textContent=date?date.day+' / '+date.time:'你的下一段记录';
  $('rename').hidden=!job;$<HTMLButtonElement>('rename').disabled=busy;
  $('detail').textContent=job?'本地模型'+' · '+(job.microphone?'录音 '+duration(job.captured_bytes/32000):'音频 '+(job.duration_seconds?duration(job.duration_seconds):''))+' · '+(job.finished?'原文已保存':'已转写 '+duration(job.acknowledged_bytes/32000)):'开始录音，也可以把音频文件拖进来。';
  $('aiBadge').textContent=ai?.status==='done'?'AI 已整理':ai?.status==='imported'?'历史稿件':ai?.status==='empty'?'未识别到文字':ai?.status==='running'?ai.phase+(ai.total>1?' '+ai.done+'/'+ai.total:''):ai?.status==='error'?'整理待重试':!settings.ai_enabled?'本地转写':job?.finished?'等待整理':'结束后自动整理';
  if(ai?.status==='queued') $('aiBadge').textContent='等待 AI'+(ai.total>1?' · 已保存 '+ai.done+'/'+ai.total+' 段':'');
  $('aiBadge').className=ai?.status==='error'?'warning':'';
  if(ai?.status==='error'&&ai.total>1) $('aiBadge').textContent='整理待重试 · 已保存 '+ai.done+'/'+ai.total+' 段';
  if(ai?.status==='done'&&ai.source_preserved_parts?.length) $('aiBadge').textContent='已整理 · 部分段落保留原文';
  if(ai?.status==='retry-wait') $('aiBadge').textContent='等待自动重试'+(ai.total>1?' · 已保存 '+ai.done+'/'+ai.total+' 段':'');
  $('pause').hidden=!job||job.finished||processing;$('pause').textContent=job?.paused?(job.provider==='local'?'继续本地转写':'继续转写'):'暂停转写';
  const retryButton=$<HTMLButtonElement>('retryAi');
  retryButton.hidden=!settings.ai_enabled||!job?.finished||!job.raw.trim()||processing||ai?.status==='done'&&ai?.pipeline==='local-prose-v1';
  retryButton.disabled=ai?.status==='queued'||!!selected&&retrying.has(selected);
  const retryLabel=selected&&retrying.has(selected)?'正在提交…':ai?.status==='queued'?'已加入整理队列':ai?.status==='retry-wait'?'立即重试':ai?.status==='error'?'重试整理':ai?.status==='imported'?'用 AI 重新整理':'生成清晰全文';
  // Keep the text node stable between pointerdown and pointerup during polling.
  if(retryButton.textContent!==retryLabel) retryButton.textContent=retryLabel;
  $('local').hidden=!job||active&&state.recording_job===job.id;$<HTMLButtonElement>('local').disabled=processing;
  $('status').textContent=!job?'音频和文字自动保存在本机':job.capture_error?'录音保存异常：'+job.capture_error:!job.finished?(job.paused?'已暂停 · '+job.status:job.status)+(active&&state.recording_job===job.id&&job.paused?' · 录音仍在保存':''):ai?.error?'整理未完成：'+ai.error:ai?.status==='done'?(ai.pipeline?'原文与清晰全文已保存':'历史稿件已保存，可生成新版清晰全文'):job.raw.trim()?ai?.phase||'原文已保存，等待 AI 整理':'音频已保存，未识别到文字';
  if(ai?.status==='retry-wait') { const seconds=Math.max(0,Math.ceil(ai.retry_after-Date.now()/1000)); $('status').textContent='AI 连接暂时中断，'+(seconds?seconds+' 秒后自动重试':'等待继续整理')+'；已完成段落已保存。'; }
  if(ai?.status==='done'&&ai.source_preserved_parts?.length) $('status').textContent=`全文已保存，其中 ${ai.source_preserved_parts.length} 段保留 ASR 原文，已标注未经 AI 校对。`;
  const total=job?.duration_seconds??(job?.captured_bytes??0)/32000;
  $('progress').style.width=job?.finished?'100%':total>0?Math.min(100,job!.acknowledged_bytes/32000/total*100)+'%':'0%';$('progress').className=job?.finished?'complete':'';
  renderHistory();renderText();
  const key=job?job.id+':'+String(active&&state.recording_job===job.id):'';
  if(key!==audioKey) { audioKey=key;if(job) void loadAudio(job.id,key);else $('playback').hidden=true; }
  if(state.recovery_errors.length&&!recoveryShown) { recoveryShown=true;notice('部分历史记录需要检查：'+state.recovery_errors.join('；')); }
}
async function refresh() {
  if(polling) return;polling=true;
  const target=selected;
  try{const next=await invoke<Snapshot>('snapshot',{selected:target});if(selected===target){next.jobs.sort((a,b)=>(recordingDate(b.created_utc).getTime()||0)-(recordingDate(a.created_utc).getTime()||0));state=next;render();}}catch(error){notice(error);}finally{polling=false;}
}
async function action(run:()=>Promise<unknown>) { try{await run();}catch(error){notice(error);}finally{await refresh();} }
async function addFile(path: string) {
  if(busy)return;busy=true;render();notice('正在保存音频副本…');
  await action(async()=>{selected=await invoke<string>('add_file',{path,provider:provider()});field='raw';});
  busy=false;render();
}
$('record').onclick=async()=>{busy=true;render();await action(async()=>{selected=await invoke<string>('start_recording',{device:$<HTMLSelectElement>('device').value,provider:provider()});field='raw';});busy=false;render();};
$('stop').onclick=()=>{void action(()=>invoke('stop_recording'));};
$('retryMic').onclick=()=>{void action(()=>invoke('retry_microphone'));};
$('syncNow').onclick=()=>{void action(()=>invoke('sync_now'));};
$('file').onclick=()=>{void action(async()=>{const path=await open({multiple:false,filters:[{name:'音频文件',extensions:['mp3','m4a','wav','aac','flac','ogg','mp4','webm']}]});if(typeof path==='string') await addFile(path);});};
$('search').oninput=renderHistory;
$('provider').onchange=()=>{void action(async()=>{await invoke('save_settings',{settings:{...settings,llm_api_key:'',default_asr:provider()}});settings.default_asr=provider();});};
$('pause').onclick=()=>{if(selected&&state?.selected)void action(()=>invoke('pause',{id:selected,paused:!state!.selected!.paused}));};
$('retryAi').onclick=async()=>{
  const id=selected;if(!id||retrying.has(id))return;
  retrying.add(id);render();
  try{await invoke('retry_ai',{id});notice('已加入整理队列，将从已保存的段落继续。');}
  catch(error){notice(error);}
  finally{retrying.delete(id);await refresh();render();}
};
$('local').onclick=()=>{if(selected)void action(async()=>{await invoke('use_local',{id:selected});field='raw';notice('已加入本地重转队列，上次原文会保留。');});};
for(const [id,name] of [['rawTab','raw'],['fullTab','full_text'],['summaryTab','summary'],['translationTab','translation']] as const) $(id).onclick=()=>{field=name;renderText();};
$('translateOpen').onclick=()=>{field='translation';renderText();};
$('translationTarget').onchange=()=>{translationTarget=$<HTMLSelectElement>('translationTarget').value;renderText();};
$('translateRun').onclick=()=>{if(selected)void action(()=>invoke('translate',{id:selected,target:translationTarget}));};
$('copy').onclick=()=>{void action(async()=>{await writeText(currentText());notice('已复制');});};
$('export').onclick=()=>{void action(async()=>{const path=await save({defaultPath:({raw:'原文',full_text:'清晰全文',summary:'旧版摘要',translation:'译文-'+(translationTarget==='zh'?'中文':'English')}[field])+'.txt',filters:[{name:'文本文档',extensions:['txt']}]});if(path&&selected){await invoke('export_text',{id:selected,field,path,target:translationTarget});notice('文本已导出');}});};
$('folder').onclick=()=>{void action(()=>invoke('open_folder',{id:selected}));};
$('audioPart').onchange=()=>{const path=audioPaths[Number($<HTMLSelectElement>('audioPart').value)];if(path)$<HTMLAudioElement>('audio').src=audioUrl(path);};
$('audio').onerror=()=>{$('audioHint').hidden=false;};
$('settings').onclick=()=>{void action(async()=>{settings=await invoke<Settings>('get_settings');$<HTMLInputElement>('aiEnabled').checked=settings.ai_enabled;$<HTMLInputElement>('baseUrl').value=settings.llm_base_url;$<HTMLInputElement>('model').value=settings.llm_model;$<HTMLInputElement>('pythonPath').value=settings.python_path;$<HTMLInputElement>('apiKey').value='';$('keyHint').textContent=settings.api_key_set?'已配置 API Key，留空则继续使用。':'尚未配置 API Key。';$<HTMLInputElement>('serverEnabled').checked=settings.server_enabled;$<HTMLInputElement>('serverUrl').value=settings.server_url;$<HTMLInputElement>('serverCert').value=settings.server_ca_cert;$<HTMLInputElement>('serverToken').value='';$('serverTokenHint').textContent=settings.server_token_set?'已配置访问令牌，留空则继续使用。':'尚未配置访问令牌。';$<HTMLDialogElement>('settingsDialog').showModal();});};
$('settingsCancel').onclick=()=>{$<HTMLDialogElement>('settingsDialog').close();};
$('settingsForm').onsubmit=e=>{e.preventDefault();void action(async()=>{await invoke('save_settings',{settings:{ai_enabled:$<HTMLInputElement>('aiEnabled').checked,llm_base_url:$<HTMLInputElement>('baseUrl').value.trim(),llm_model:$<HTMLInputElement>('model').value.trim(),llm_api_key:$<HTMLInputElement>('apiKey').value.trim(),python_path:$<HTMLInputElement>('pythonPath').value.trim(),default_asr:provider(),server_enabled:$<HTMLInputElement>('serverEnabled').checked,server_url:$<HTMLInputElement>('serverUrl').value.trim(),server_token:$<HTMLInputElement>('serverToken').value.trim(),server_ca_cert:$<HTMLInputElement>('serverCert').value.trim()}});settings=await invoke<Settings>('get_settings');$<HTMLDialogElement>('settingsDialog').close();notice('设置已保存');});};
$('importHistory').onclick=()=>{void action(async()=>{const path=await open({directory:true,multiple:false,title:'选择旧版 WatchRec 的 downloads 文件夹'});if(typeof path==='string'){notice('正在复制旧音频并加入转写队列…');const result=await invoke<{imported:number;existing:number;errors:string[]}>('import_history',{path});notice('已加入 '+result.imported+' 条录音，跳过 '+result.existing+' 条重复记录。'+(result.errors.length?'\n有 '+result.errors.length+' 个文件无法读取：'+result.errors.slice(0,2).join('；'):''));}});};
$('rename').onclick=()=>{const item=state?.jobs.find(j=>j.id===selected);$<HTMLInputElement>('titleInput').value=item?.custom_title??'';$<HTMLInputElement>('titleInput').placeholder=item?titleOf(item):'';$<HTMLDialogElement>('renameDialog').showModal();};
$('renameCancel').onclick=()=>{$<HTMLDialogElement>('renameDialog').close();};
$('renameForm').onsubmit=e=>{e.preventDefault();void action(async()=>{await invoke('rename',{id:selected,title:$<HTMLInputElement>('titleInput').value});$<HTMLDialogElement>('renameDialog').close();});};
$('closeCancel').onclick=()=>closeDialog.close();
$('closeBackground').onclick=()=>{void action(async()=>{await invoke('hide_to_tray');closeDialog.close();});};
$('closeExit').onclick=()=>{closeDialog.close();void action(()=>invoke('quit_app'));};
await listen<{exit:boolean;recording:boolean}>('close-requested',event=>{
  $('closeTitle').textContent=event.payload.exit?'停止录音并退出？':'把录音留在后台？';
  $('closeDescription').textContent=event.payload.exit?'退出会停止录音并保存已有音频与文字，下次启动会继续未暂停的转写与整理任务。':(event.payload.recording?'后台会继续录音和保存。':'麦克风尚未就绪，后台会继续等待。')+'可从 WatchRec 菜单栏或托盘图标重新打开或停止录音。';
  $('closeBackground').hidden=event.payload.exit;if(!closeDialog.open)closeDialog.showModal();
});
await getCurrentWebviewWindow().onDragDropEvent(event=>{const data=event.payload;$('dropOverlay').hidden=!['enter','over'].includes(data.type);if(data.type==='drop'&&data.paths[0])void addFile(data.paths[0]);});
settings=await invoke<Settings>('get_settings');$<HTMLSelectElement>('provider').value=settings.default_asr;
await refresh();
void invoke<{id:string;name:string}[]>('devices').then(devices=>{const select=$<HTMLSelectElement>('device');select.replaceChildren();for(const device of devices)select.add(new Option(device.name,device.id));}).catch(notice);
window.setInterval(()=>{void refresh();},100);
