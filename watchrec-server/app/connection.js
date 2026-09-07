// First-run setup is also always accessible from the header.
(() => {
  const style = document.createElement('style');
  style.textContent = `
    .connection-entry{margin-left:auto;display:flex;align-items:center;gap:14px;font-size:13px;color:var(--secondary)}
    #connectionOverlay .modal{width:580px;max-height:92vh;display:flex;flex-direction:column}
    #connectionOverlay .modal-body{overflow-y:auto;font-size:16px}
    #connectionOverlay .modal-hint{font-size:14px;margin-bottom:12px}
    #connectionOverlay label{font-size:14px;letter-spacing:0;text-transform:none}
    #connectionOverlay .modal-head{flex-shrink:0}
    #connectionOverlay .modal-foot{flex-wrap:wrap;flex-shrink:0}
    #connectionOverlay select{padding:10px;border:1px solid var(--border-medium);border-radius:8px;background:var(--bg);color:var(--text);width:100%;font:inherit}
    #connectionOverlay input[type=checkbox]{width:auto;margin-right:8px}
    #connectionOverlay details{margin-top:18px}
    #connectionOverlay summary{cursor:pointer;color:var(--secondary)}
    #connPath{overflow-wrap:anywhere;font-size:12px;margin-top:12px;color:var(--secondary)}
    #connHelp{margin:8px 0;color:var(--secondary);font-size:13px}
    #connMsg{white-space:pre-wrap;font-size:14px}
    @media(max-width:600px){.connection-entry span{display:none}#connectionOverlay .modal{max-width:calc(100vw - 24px)}}`;
  document.head.appendChild(style);
  const entry = document.createElement('div');
  entry.className = 'connection-entry';
  entry.innerHTML = '<span id="connectionStatus">本地模式</span><button class="btn-ghost" id="connectionOpen">连接与设置</button>';
  document.querySelector('.header').appendChild(entry);
  document.body.insertAdjacentHTML('beforeend', `
    <div class="modal-overlay" id="connectionOverlay" role="dialog" aria-modal="true" aria-labelledby="connTitle">
      <div class="modal">
        <div class="modal-head" id="connTitle">连接你的录音设备<button class="close btn-ghost" id="connClose" aria-label="关闭">×</button></div>
        <div class="modal-body">
          <p class="modal-hint">手表负责录音，VPS 暂存音频，这台电脑负责转写。还没准备好服务器，也可以先导入音频使用。</p>
          <label for="connUrl">VPS 服务器地址</label>
          <input id="connUrl" type="url" placeholder="https://rec.example.com 或 http://192.168.1.20:8765" spellcheck="false" autocomplete="off">
          <p id="connHelp">填写完整地址，包含 http:// 或 https://。不用添加 /health 或 /upload。公网使用 HTTPS；HTTP 会明文传输录音与密钥。</p>
          <label for="connToken">连接密钥（VPS 的 APP_TOKEN）</label>
          <input id="connToken" type="password" placeholder="与手表、VPS 填写同一份密钥" autocomplete="off">
          <p class="modal-hint">服务器、手表和电脑使用相同密钥。它与 AI 服务的 API Key 是两回事。更换服务器时，请同时更新密钥。</p>
          <details><summary>电脑端口、局域网直传与转写设备</summary>
            <label for="connPort">电脑本地端口</label><input id="connPort" type="number" min="1024" max="65535" value="18765">
            <label><input id="connLan" type="checkbox">允许手表在同一 Wi-Fi 下直接上传</label>
            <p class="modal-hint">需在电脑防火墙放行以上 TCP 端口；网页和设置仍仅允许本机访问。未开启时全部通过 VPS 同步。</p>
            <label for="connIp">电脑局域网 IPv4（可留空自动探测）</label><input id="connIp" placeholder="192.168.1.20" autocomplete="off">
            <label for="connDevice">转写设备</label><select id="connDevice"><option value="auto">自动选择（无可用显卡时使用 CPU）</option><option value="cpu">CPU</option><option value="cuda:0">NVIDIA CUDA（需 GPU 版 PyTorch）</option></select>
            <p class="modal-hint">Windows 便携包使用 CPU。首次转写会下载模型，需要联网并预留数 GB 磁盘空间。</p>
          </details>
          <p id="connPath"></p>
          <div class="modal-msg" id="connMsg" role="status" aria-live="polite"></div>
        </div>
        <div class="modal-foot">
          <button class="btn-ghost" id="connLocal">先使用本地导入</button>
          <button class="btn-ghost" id="connTest">测试连接</button>
          <button class="btn-primary" id="connSave">保存设置</button>
        </div>
      </div>
    </div>`);
  const el = id => document.getElementById(id);
  let state;
  const close = () => {
    el('connectionOverlay').classList.remove('open');
    localStorage.setItem('watchrec-onboarded', 'yes');
    el('connectionOpen').focus();
  };
  const body = () => ({vps_base_url:el('connUrl').value.trim(),app_token:el('connToken').value.trim(),
    local_port:Number(el('connPort').value),lan_enabled:el('connLan').checked,
    lan_ip_override:el('connIp').value.trim(),asr_device:el('connDevice').value});
  const message = (text, ok) => {
    el('connMsg').textContent = text;
    el('connMsg').className = 'modal-msg ' + (ok ? 'ok' : 'err');
  };
  async function refresh() {
    const res = await fetch('/api/connection');
    if (!res.ok) throw new Error('无法读取电脑端设置');
    state = await res.json();
    el('connectionStatus').textContent = state.restart_required ? '设置已保存 · 等待重启' : (state.configured ? '已配置 VPS 同步' : '未连接 VPS · 本地模式');
    return state;
  }
  async function open() {
    el('connectionOverlay').classList.add('open');
    el('connMsg').textContent = '';
    try {
      const s = await refresh();
      el('connLocal').textContent = s.configured ? '关闭' : '先使用本地导入';
      el('connUrl').value = s.vps_base_url;
      el('connToken').value = '';
      el('connToken').placeholder = s.token_set ? '已保存，留空保留原密钥' : '与手表、VPS 填写同一份密钥';
      el('connPort').value = s.local_port;
      el('connLan').checked = s.lan_enabled;
      el('connIp').value = s.lan_ip_override;
      el('connDevice').value = s.asr_device;
      el('connPath').textContent = '本机配置和录音目录：' + s.data_dir;
      if (s.restart_required) message('连接设置已保存。请等待正在处理的任务完成，关闭并重新打开电脑端。', true);
      el('connUrl').focus();
    } catch(e) { message(e.message, false); }
  }
  async function submit(test) {
    const buttons = ['connSave','connTest'];
    buttons.forEach(id => el(id).disabled = true);
    message(test ? '正在测试连接…' : '正在保存…', true);
    try {
      const res = await fetch('/api/connection' + (test ? '/test' : ''), {
        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body())});
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || '请求失败');
      if (test) message(data.message, data.ok);
      else {
        message(data.restart_required ? '已保存。请等待当前任务完成，关闭并重新打开电脑端后生效。端口变更后，请使用新的地址打开网页。' : '已保存，设置已生效。', true);
        el('connToken').value = '';
        el('connToken').placeholder = data.token_set ? '已保存，留空保留原密钥' : '请填写连接密钥';
        localStorage.setItem('watchrec-onboarded', 'yes');
        await refresh();
      }
    } catch(e) { message(e.message, false); }
    finally { buttons.forEach(id => el(id).disabled = false); }
  }
  el('connectionOpen').onclick = open;
  el('connClose').onclick = close;
  el('connLocal').onclick = close;
  el('connSave').onclick = () => submit(false);
  el('connTest').onclick = () => submit(true);
  el('connectionOverlay').addEventListener('keydown', e => { if(e.key === 'Escape') close(); });
  refresh().then(s => { if (!s.token_set && !localStorage.getItem('watchrec-onboarded')) open(); }).catch(e => {
    el('connectionStatus').textContent = e.message;
  });
})();
