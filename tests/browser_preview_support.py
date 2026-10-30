"""Browser-side MJPEG camera fixture with controllable failures."""
STREAM_FIXTURE = r'''(() => {
  const realFetch = window.fetch.bind(window);
  window.streamMode = 'live';
  window.streamStarts = 0;
  window.streamStops = 0;
  window.fetch = async (url, options = {}) => {
    if (!String(url).startsWith('/api/stream/')) return realFetch(url, options);
    window.streamStarts++;
    if (window.streamMode === 'error') return new Response('', {status: 502});
    const mode = window.streamMode;
    const canvas = document.createElement('canvas');
    canvas.width = 640; canvas.height = 360;
    const jpeg = Uint8Array.from(atob(canvas.toDataURL('image/jpeg').split(',')[1]), c => c.charCodeAt(0));
    const encode = text => new TextEncoder().encode(text);
    const header = encode('\r\nContent-Type: image/jpeg\r\nContent-Length: ' + jpeg.length + '\r\n\r\n');
    const tail = encode('\r\n--frame');
    const part = new Uint8Array(header.length + jpeg.length + tail.length);
    part.set(header); part.set(jpeg, header.length); part.set(tail, header.length + jpeg.length);
    let timer, closed = false;
    const cleanup = () => {
      if (closed) return;
      closed = true; clearInterval(timer); window.streamStops++;
    };
    const body = new ReadableStream({
      start(controller) {
        controller.enqueue(encode('--frame'));
        controller.enqueue(part);
        if (mode !== 'stall') timer = setInterval(() => {
          if (mode === 'eof') { cleanup(); controller.close(); }
          else controller.enqueue(part);
        }, 100);
        options.signal.addEventListener('abort', () => {
          if (!closed) { cleanup(); controller.error(new DOMException('Aborted', 'AbortError')); }
        });
      },
      cancel() { cleanup(); },
    });
    return new Response(body, {headers: {'Content-Type': 'multipart/x-mixed-replace; boundary="frame"'}});
  };
})();'''
