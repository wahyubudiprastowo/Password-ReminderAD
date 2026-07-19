(function() {
  const dot = document.getElementById("sseStatus");
  const txt = document.getElementById("sseStatusText");
  let es, retryDelay = 1000, reconnectTimer = null;
  function setConnected() {
    dot?.classList.add("connected");
    dot?.classList.remove("error");
    if (txt) txt.textContent = "System Live";
  }
  function setReconnecting() {
    dot?.classList.remove("connected");
    dot?.classList.add("error");
    if (txt) txt.textContent = "Reconnecting...";
  }
  function connect() {
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    es = new EventSource("/api/stream");
    es.addEventListener("hello", () => {
      setConnected();
      retryDelay = 1000;
    });
    ["run_started","run_log","run_finished","run_completed","log_cleared"].forEach(ev => {
      es.addEventListener(ev, (e) => {
        const detail = { type: ev, data: JSON.parse(e.data) };
        document.dispatchEvent(new CustomEvent("sse:"+ev, { detail }));
      });
    });
    es.onerror = () => {
      setReconnecting();
      try { es.close(); } catch (_) {}
      reconnectTimer = setTimeout(connect, retryDelay);
      retryDelay = Math.min(retryDelay * 2, 15000);
    };
  }
  connect();
})();
