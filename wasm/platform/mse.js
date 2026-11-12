// MSE video backend for the TV, used for HEVC streams (window.mseActive).
// The WASM module calls mseStart() when the video stream is set up, _mseReceiveFrame() for every
// frame (Annex B HEVC, read from the WASM heap) and mseStop() when the stream ends. Each frame is
// wrapped in its own fMP4 fragment and appended to a MediaSource, which Tizen decodes through the
// same pipeline as HTML5 video. That pipeline shows all 120 fps, unlike EMSS on this TV.
//
// window.mseOpts tunes how far playback trails the newest frame (all times in ms):
//   pts:        'host' (host timestamps), 'arrival' (time the frame arrived here), 'grid' (host
//               timestamps snapped to exact 1/fps slots, one frame per slot; read at stream start in worker mode)
//   rateAbove:  play at rateHi while more than this is buffered ahead of playback
//   rateHi:     catch-up playback rate
//   seekAbove:  jump to the live edge when more than this is buffered ahead
//   seekTarget: how much to leave buffered after such a jump
//   startAhead: buffered time needed before playback starts
//   idrMB:      ask the host for a keyframe once this many MB of video have arrived since the last
//               one. The buffer can be trimmed only at a keyframe, and the TV's player refuses
//               appends once about 80 MB are buffered. At the stream's full bitrate the buffer peaks
//               up to about 10 MB above this value. Each keyframe shows as a brief flicker, so a
//               size limit asks for far fewer of them than a fixed interval would.
//   sceneMB, sceneRatio: once sceneMB have arrived since the last keyframe, ask for one right after
//               a frame sceneRatio times bigger than the recent average, such as a cut or a fade,
//               where its flicker cannot be seen
//   durMs:      declared frame duration. MSE drops every frame up to the next keyframe after a
//               timestamp gap over twice the previous frame's duration, and the stream has
//               keyframes only on request, so this has to cover the longest gap between frames.
//   rateBelow, rateLo: play at rateLo while less than rateBelow is buffered ahead
//   rateHold:   minimum time between two playback rate changes
//   rateUntil:  once at rateHi, stay there until the buffer falls below this (hysteresis; default rateAbove)
//   rateWindow: judge the buffer by its minimum over this many ms instead of the latest sample (0 = latest)
//   floorFrames: the player's floor, in frame intervals: playback pauses when less than this is buffered
//               ahead. With it set, the thresholds below are measured from the floor and replace rateAbove,
//               rateUntil and rateBelow.
//   marginHi, margin: play at rateHi once the buffer's minimum over rateWindow exceeds the floor by
//               marginHi, until that minimum is back within margin of the floor
//   marginLo, marginMid: play at rateLo once the buffer's median over rateWindow is within marginLo of
//               the floor, until its median over the last 100 ms exceeds the floor by marginMid. Nothing
//               else raises a buffer that sits at the floor, where playback pauses for a frame at a
//               time. The median decides here because single late frames pull the minimum down without
//               the level being low, and the short window stops the slow-down before it overshoots.
//   rebuildAt:  page age (performance.now()) at which the app replaces the threads it created
//               before then, 0 = never. When the app is one minute old, the TV starts to hold
//               back every thread that exists at that moment, for as long as the thread lives. A
//               thread created after that moment runs at full speed, and the value leaves a
//               margin after the minute. At this age the app replaces its idle wasm threads, so
//               a stream that starts later runs on new threads only. A stream that is already
//               running started on the old ones, where the player's worker appends late and the
//               network threads read and send late. It gets a new player, and its receive and
//               input loops move to new threads. The picture is gone while the new player starts.
// Read once when the stream starts (the rest also apply live through mseSetOpts):
//   live:        how the init segment declares its duration. 'none': mvhd 0 and no mehd (Chromium
//                already reads that as a live stream), 'mehd0': adds an mehd box of 0, 'max': all
//                durations 0xFFFFFFFF ("unknown")
//   latencyHint: seconds for Chromium's experimental HTMLMediaElement.latencyHint, null leaves it unset
//   mux:         'fast' (one allocation per frame) or 'old' (the session 2 muxer), for A/B runs
//   worker:      true runs the MediaSource, muxer and appends in a dedicated worker that the decoder
//                thread wakes directly, so no frame waits for the page's main thread. Needs
//                MediaSource.canConstructInDedicatedWorker and Atomics.waitAsync; falls back otherwise.
//   reuse:       write fragments into one reused staging buffer (appendBuffer copies it) instead of a
//                new ArrayBuffer per frame, whose garbage froze the worker ~100 ms every ~24 s
//   uiPad:       false pauses Moonlight's menu gamepad poller (gamepad.js, every 5 ms on the main thread)
//                while the stream runs; the stream's own input goes through the C++ side
//   wake:        how the worker waits for the next frame: 'async' (Atomics.waitAsync), 'spin' (busy poll
//                through its task queue), 'timer' (setTimeout 1), 'wait' (Atomics.wait up to waitMs, then yield)
// The backend polls the gamepads from a 4 ms timer on the main thread instead of Moonlight's input
// thread, whose every gamepad call waits for the main thread.
var MSE_DEFAULTS = { pts: 'grid', rateAbove: 70, rateHi: 1.05, rateBelow: -1e9, rateLo: 0.95, rateUntil: 45, rateWindow: 500, seekAbove: 400, seekTarget: 30,
                     startAhead: 20, idrMB: 50, sceneMB: 30, sceneRatio: 3, durMs: 50, rateHold: 250, live: 'none', latencyHint: null, mux: 'fast', worker: true, wake: 'block', waitMs: 3, reuse: true, uiPad: false, trimSlice: 1, trimGap: 50, diag: false, floorFrames: 4, margin: 12, marginHi: 37, marginLo: 10, marginMid: 18, rebuildAt: 63000, gridPhase: true };
var MSE_TIMESCALE = 90000;
// Kept across a hot reload, so a running stream's video element is not orphaned over the UI
var _mse = window._mse || null;

function _mseOpts() {
  var o = {}, k, user = window.mseOpts || {};
  for (k in MSE_DEFAULTS) o[k] = (k in user) ? user[k] : MSE_DEFAULTS[k];
  return o;
}

// ---- MP4 boxes ----

function _mp4Concat(parts) {
  var len = 0, i, out, pos = 0;
  for (i = 0; i < parts.length; i++) len += parts[i].length;
  out = new Uint8Array(len);
  for (i = 0; i < parts.length; i++) { out.set(parts[i], pos); pos += parts[i].length; }
  return out;
}

function _mp4Bytes(list) { return new Uint8Array(list); }

function _mp4U32(v) { return _mp4Bytes([(v >>> 24) & 255, (v >>> 16) & 255, (v >>> 8) & 255, v & 255]); }
function _mp4U16(v) { return _mp4Bytes([(v >>> 8) & 255, v & 255]); }
function _mp4U64(v) { var hi = Math.floor(v / 4294967296); return _mp4Concat([_mp4U32(hi), _mp4U32(v - hi * 4294967296)]); }

function _mp4Box(type) {
  var payload = _mp4Concat(Array.prototype.slice.call(arguments, 1));
  var head = new Uint8Array(8);
  head.set(_mp4U32(payload.length + 8), 0);
  for (var i = 0; i < 4; i++) head[4 + i] = type.charCodeAt(i);
  return _mp4Concat([head, payload]);
}

function _mp4Full(type, version, flags) {
  var rest = Array.prototype.slice.call(arguments, 3);
  return _mp4Box.apply(null, [type, _mp4Bytes([version, (flags >> 16) & 255, (flags >> 8) & 255, flags & 255])].concat(rest));
}

var _MP4_MATRIX = _mp4Concat([_mp4U32(0x10000), _mp4U32(0), _mp4U32(0), _mp4U32(0), _mp4U32(0x10000), _mp4U32(0), _mp4U32(0), _mp4U32(0), _mp4U32(0x40000000)]);

function _hevcUnescape(nal) {
  var out = [], zeros = 0;
  for (var i = 0; i < nal.length; i++) {
    if (zeros >= 2 && nal[i] === 3) { zeros = 0; continue; }
    out.push(nal[i]);
    zeros = nal[i] === 0 ? zeros + 1 : 0;
  }
  return out;
}

function _hevcInitSegment(vps, sps, pps, width, height, main10, live) {
  var dur = live === 'max' ? 0xFFFFFFFF : 0;
  var rbsp = _hevcUnescape(sps.subarray(2));
  var ptl = rbsp.slice(1, 13);
  // Samsung's TTVD decoder (EMSS, WebRTC) sizes itself from this level: 5.0 (150) at 1440p gets a 60 fps decoder, 5.1 (153) a 120 fps one
  console.log('[mse] stream SPS: profile ' + (ptl[0] & 31) + ', general_level_idc ' + ptl[11] + ', ' + width + 'x' + height);
  var bitDepth = main10 ? 2 : 0;
  var arrays = [];
  [[32, vps], [33, sps], [34, pps]].forEach(function (a) {
    arrays.push(_mp4Bytes([0x80 | a[0]]), _mp4U16(1), _mp4U16(a[1].length), a[1]);
  });
  var hvcC = _mp4Box('hvcC',
    _mp4Bytes([1]), _mp4Bytes(ptl),
    _mp4U16(0xF000), _mp4Bytes([0xFC, 0xFC | 1, 0xF8 | bitDepth, 0xF8 | bitDepth]),
    _mp4U16(0), _mp4Bytes([0x0F, 3]), _mp4Concat(arrays));
  var compressor = new Uint8Array(32);
  var sampleEntry = _mp4Box('hev1',
    new Uint8Array(6), _mp4U16(1), new Uint8Array(16), _mp4U16(width), _mp4U16(height),
    _mp4U32(0x480000), _mp4U32(0x480000), _mp4U32(0), _mp4U16(1), compressor, _mp4U16(0x18), _mp4U16(0xFFFF), hvcC);
  var stbl = _mp4Box('stbl',
    _mp4Full('stsd', 0, 0, _mp4U32(1), sampleEntry),
    _mp4Full('stts', 0, 0, _mp4U32(0)),
    _mp4Full('stsc', 0, 0, _mp4U32(0)),
    _mp4Full('stsz', 0, 0, _mp4U32(0), _mp4U32(0)),
    _mp4Full('stco', 0, 0, _mp4U32(0)));
  var minf = _mp4Box('minf',
    _mp4Full('vmhd', 0, 1, new Uint8Array(8)),
    _mp4Box('dinf', _mp4Full('dref', 0, 0, _mp4U32(1), _mp4Full('url ', 0, 1))),
    stbl);
  var mdia = _mp4Box('mdia',
    _mp4Full('mdhd', 0, 0, _mp4U32(0), _mp4U32(0), _mp4U32(MSE_TIMESCALE), _mp4U32(dur), _mp4U16(0x55C4), _mp4U16(0)),
    _mp4Full('hdlr', 0, 0, _mp4U32(0), _mp4Bytes([118, 105, 100, 101]), new Uint8Array(12), _mp4Bytes([86, 105, 100, 101, 111, 0])),
    minf);
  var trak = _mp4Box('trak',
    _mp4Full('tkhd', 0, 3, _mp4U32(0), _mp4U32(0), _mp4U32(1), _mp4U32(0), _mp4U32(dur), new Uint8Array(8),
      _mp4U16(0), _mp4U16(0), _mp4U16(0), _mp4U16(0), _MP4_MATRIX, _mp4U32(width * 65536), _mp4U32(height * 65536)),
    mdia);
  var moov = _mp4Box('moov',
    _mp4Full('mvhd', 0, 0, _mp4U32(0), _mp4U32(0), _mp4U32(1000), _mp4U32(dur), _mp4U32(0x10000), _mp4U16(0x100),
      new Uint8Array(10), _MP4_MATRIX, new Uint8Array(24), _mp4U32(2)),
    trak,
    live === 'mehd0'
      ? _mp4Box('mvex', _mp4Full('mehd', 0, 0, _mp4U32(0)), _mp4Full('trex', 0, 0, _mp4U32(1), _mp4U32(1), _mp4U32(0), _mp4U32(0), _mp4U32(0)))
      : _mp4Box('mvex', _mp4Full('trex', 0, 0, _mp4U32(1), _mp4U32(1), _mp4U32(0), _mp4U32(0), _mp4U32(0))));
  var ftyp = _mp4Box('ftyp', _mp4Bytes([105, 115, 111, 53]), _mp4U32(512),
    _mp4Bytes([105, 115, 111, 53, 105, 115, 111, 54, 109, 112, 52, 49]));
  return _mp4Concat([ftyp, moov]);
}

function _mp4Fragment(seq, decodeTime, duration, sample, key) {
  var trunSize = 8 + 4 + 4 + 4 + 12;
  var moofSize = 8 + 16 + 8 + (16 + 20 + trunSize);
  var traf = _mp4Box('traf',
    _mp4Full('tfhd', 0, 0x020000, _mp4U32(1)),
    _mp4Full('tfdt', 1, 0, _mp4U64(decodeTime)),
    _mp4Full('trun', 0, 0x000701, _mp4U32(1), _mp4U32(moofSize + 8),
      _mp4U32(duration), _mp4U32(sample.length), _mp4U32(key ? 0x02000000 : 0x01010000)));
  var moof = _mp4Box('moof', _mp4Full('mfhd', 0, 0, _mp4U32(seq)), traf);
  return _mp4Concat([moof, _mp4U32(sample.length + 8), _mp4Bytes([109, 100, 97, 116]), sample]);
}

// Splits Annex B into NAL units (views into data)
function _annexBNals(data) {
  var nals = [], i = 0, start = -1, n = data.length;
  while (i + 2 < n) {
    if (data[i] === 0 && data[i + 1] === 0 && data[i + 2] === 1) {
      if (start >= 0) {
        var end = i;
        while (end > start && data[end - 1] === 0) end--;
        nals.push(data.subarray(start, end));
      }
      i += 3;
      start = i;
    } else {
      i++;
    }
  }
  if (start >= 0 && start < n) nals.push(data.subarray(start, n));
  return nals;
}

// Fast path: NAL [start, end) pairs into _nalIdx, and the fragment written into one buffer.
var _nalIdx = [];

function _annexBScan(d) {
  var n = d.length, i = 0, start = -1, end, k = 0;
  while (i + 2 < n) {
    var c = d[i + 2];
    if (c > 1) { i += 3; continue; }
    if (c === 1) {
      if (d[i + 1] === 0 && d[i] === 0) {
        if (start >= 0) {
          end = i;
          while (end > start && d[end - 1] === 0) end--;
          _nalIdx[k++] = start; _nalIdx[k++] = end;
        }
        start = i + 3;
      }
      i += 3;
      continue;
    }
    i++;
  }
  if (start >= 0 && start < n) { _nalIdx[k++] = start; _nalIdx[k++] = n; }
  return k;
}

// moof + mdat header of a one-sample fragment; the fields that change per frame are patched in
var _MSE_FRAG_HEAD = null;

function _mp4FragHeadLen() {
  if (!_MSE_FRAG_HEAD) _MSE_FRAG_HEAD = _mp4Fragment(0, 0, 0, new Uint8Array(0), false);
  return _MSE_FRAG_HEAD.length;
}

// Writes the fragment at out[base...]; dv is a DataView over out.buffer
function _mp4FragmentInto(out, dv, base, seq, decodeTime, duration, d, k, sampleLen, key) {
  var head = _mp4FragHeadLen();
  out.set(_MSE_FRAG_HEAD, base);
  var hi = Math.floor(decodeTime / 4294967296);
  dv.setUint32(base + 20, seq);
  dv.setUint32(base + 60, hi);
  dv.setUint32(base + 64, decodeTime - hi * 4294967296);
  dv.setUint32(base + 88, duration);
  dv.setUint32(base + 92, sampleLen);
  dv.setUint32(base + 96, key ? 0x02000000 : 0x01010000);
  dv.setUint32(base + 100, sampleLen + 8);
  var pos = base + head;
  for (var j = 0; j < k; j += 2) {
    var st = _nalIdx[j], en = _nalIdx[j + 1];
    if (((d[st] >> 1) & 0x3F) === 35) continue;
    dv.setUint32(pos, en - st);
    out.set(d.subarray(st, en), pos + 4);
    pos += 4 + en - st;
  }
  return head + sampleLen;
}

function _mp4FragmentFast(seq, decodeTime, duration, d, k, sampleLen, key) {
  var out = new Uint8Array(_mp4FragHeadLen() + sampleLen);
  _mp4FragmentInto(out, new DataView(out.buffer), 0, seq, decodeTime, duration, d, k, sampleLen, key);
  return out;
}

// Room for n more bytes in the staging buffer; grows it (rarely) by copying what is staged
function _mseStageReserve(s, n) {
  if (!s.stage) s.stageLen = 0;
  else if (s.stageLen + n <= s.stage.length) return;
  var big = new Uint8Array(Math.max(1 << 20, 2 * (s.stageLen + n)));
  if (s.stage) big.set(s.stage.subarray(0, s.stageLen));
  s.stage = big;
  s.stageDv = new DataView(big.buffer);
}

function _mseStageAdd(s, bytes) {
  _mseStageReserve(s, bytes.length);
  s.stage.set(bytes, s.stageLen);
  s.stageLen += bytes.length;
}

// ---- Playback ----

function mseStart(videoFormat, width, height, fps, needIdrAddr, queueAddr, replacing) {
  mseStop();
  var hevc = (videoFormat & 0x0100) !== 0, main10 = (videoFormat & 0x0200) !== 0;
  if (!hevc && !main10) {
    console.error('[mse] only HEVC is supported, got video format 0x' + videoFormat.toString(16));
    return 0;
  }
  document.querySelectorAll('#mse_video').forEach(function (old) { try { old.pause(); } catch (e) {} old.remove(); });
  var video = document.createElement('video');
  video.id = 'mse_video';
  video.muted = true;
  video.setAttribute('style', 'position: fixed; left: 0; top: 0; width: 100%; height: 100%; object-fit: contain; pointer-events: none;');
  var anchor = document.getElementById('wasm_module');
  anchor.parentNode.insertBefore(video, anchor);
  var startOpts = _mseOpts();
  if (startOpts.latencyHint !== null) {
    if ('latencyHint' in video) video.latencyHint = startOpts.latencyHint;
    console.log('[mse] latencyHint ' + ('latencyHint' in video ? 'set to ' + video.latencyHint : 'not supported'));
  }
  var useWorker = !!(startOpts.worker && queueAddr && typeof MediaSource !== 'undefined' &&
                      MediaSource.canConstructInDedicatedWorker === true && typeof Atomics.waitAsync === 'function');
  if (startOpts.worker && !useWorker) {
    console.warn('[mse] worker mode unavailable: canConstructInDedicatedWorker=' + (typeof MediaSource !== 'undefined' && MediaSource.canConstructInDedicatedWorker) +
                 ', waitAsync=' + (typeof Atomics.waitAsync) + ', queue=' + !!queueAddr);
  }
  var ms = useWorker ? null : new MediaSource();
  if (ms) video.src = URL.createObjectURL(ms);
  var s = _mse = {
    video: video, ms: ms, sb: null, width: width, height: height, fps: fps, main10: main10, opts: startOpts,
    live: startOpts.live, mux: startOpts.mux, appendAt: null,
    needIdrAddr: needIdrAddr, waitIdr: true, params: null, seq: 1, firstPts: null, t0: null,
    pending: [], open: false, playing: false, timer: null, lastTrim: 0, keyTimes: [],
    stats: null, events: [], lastCt: -1, lastCtAt: 0, stalled: false,
    bornAt: performance.now(), startArgs: [videoFormat, width, height, fps, needIdrAddr, queueAddr], replacing: !!replacing,
  };
  _mseResetStats();
  if (useWorker) {
    _mseStartWorker(s, queueAddr);
  } else {
    ms.addEventListener('sourceopen', function () {
      if (_mse !== s) return;
      s.open = true;
      try { ms.duration = Infinity; } catch (e) {}
      _msePump();
    });
  }
  if (!startOpts.uiPad && typeof Controller !== 'undefined' && Controller.stopWatching) {
    Controller.stopWatching();
    s.uiPadStopped = true;
  }
  if (typeof Module !== 'undefined' && typeof Module._mlPollGamepadsMain === 'function') {
    s.padTimer = setInterval(Module._mlPollGamepadsMain, 4);
  }
  video.addEventListener('waiting', function () { if (_mse === s) { s.stats.waiting++; _mseEvent('waiting'); } });
  video.addEventListener('playing', function () { if (_mse === s) _mseEvent('playing'); });
  s.timer = setInterval(_mseControl, 10);
  console.log('[mse] started ' + width + 'x' + height + '@' + fps + (useWorker ? ' in a worker ' : ' ') + JSON.stringify(s.opts) +
              (s.padTimer ? ', gamepads polled on the main thread' : ''));
  return useWorker ? 2 : 1;
}

// ---- Worker mode ----
// The worker runs the same receive, mux and pump functions as the main thread; their source is
// copied into it. Shared with the main thread through s.ctl (Float64Array on a SharedArrayBuffer):
// [0] currentTime (main writes), [1] newest appended frame time, [2] buffered start (worker
// writes), [3] 1 while the main thread wants a seek to the next keyframe, [4] that keyframe's time,
// [5] the recent interval between frames in ms (worker writes).

function _mseShareCtl(s, ctl) {
  s.ctl = ctl;
  Object.defineProperty(s, 'seekWanted', { get: function () { return ctl[3] === 1; }, set: function (v) { ctl[3] = v ? 1 : 0; } });
  Object.defineProperty(s, 'seekTo', { get: function () { return ctl[4] >= 0 ? ctl[4] : null; }, set: function (v) { ctl[4] = v === null ? NaN : v; } });
}

function _mseStartWorker(s, queueAddr) {
  var ctl = new Float64Array(new SharedArrayBuffer(48));
  ctl[0] = 0; ctl[1] = NaN; ctl[2] = NaN; ctl[3] = 0; ctl[4] = NaN; ctl[5] = 0;
  _mseShareCtl(s, ctl);
  Object.defineProperty(s, 'newest', { get: function () { return ctl[1] >= 0 ? ctl[1] : undefined; }, set: function () {} });
  var url = URL.createObjectURL(new Blob([_mseWorkerSource()], { type: 'text/javascript' }));
  s.worker = new Worker(url);
  s.worker.onerror = function (e) { console.error('[mse] worker error: ' + e.message); };
  s.worker.onmessage = function (e) {
    if (_mse !== s) return;
    var m = e.data, st = s.stats, k;
    if (m.type === 'handle') {
      s.video.srcObject = m.handle;
    } else if (m.type === 'ev') {
      _mseEvent(m.kind, m.info);
    } else if (m.type === 'stats') {
      ['frames', 'appended', 'dropped', 'idr', 'resets', 'trims', 'bytes'].forEach(function (k) { st[k] += m.stats[k]; });
      for (k = 0; k < 10; k++) st.steps[k] += m.stats.steps[k];
      // Nothing reads these unless the inspector asks for a summary, so they start over before they grow large
      if (st.lag.length > 30000) _mseResetStats();
      ['recvToAppend', 'muxMs', 'appendMs', 'sizes', 'lag', 'reasm', 'cq'].forEach(function (k) { Array.prototype.push.apply(st[k], m.stats[k]); });
    }
  };
  s.worker.postMessage({ type: 'init', buffer: HEAPU8.buffer, ctl: ctl.buffer, queueAddr: queueAddr, needIdrAddr: s.needIdrAddr,
                         width: s.width, height: s.height, fps: s.fps, main10: s.main10, opts: s.opts, mainOrigin: performance.timeOrigin, replacing: s.replacing });
}

function _mseWorkerSource() {
  var fns = [_mp4Concat, _mp4Bytes, _mp4U32, _mp4U16, _mp4U64, _mp4Box, _mp4Full, _hevcUnescape, _hevcInitSegment, _mp4Fragment,
             _annexBNals, _annexBScan, _mp4FragHeadLen, _mp4FragmentInto, _mp4FragmentFast, _mseStageReserve, _mseStageAdd, _mseShareCtl, _mseResetStats, _mseRequestIdr, _mseReceiveFrame, _msePump, _mseTask];
  return 'var MSE_TIMESCALE = ' + MSE_TIMESCALE + ', _nalIdx = [], _MSE_FRAG_HEAD = null, _mse = null, HEAPU8 = null, HEAP32 = null;\n' +
    'var _MP4_MATRIX = new Uint8Array([' + Array.prototype.join.call(_MP4_MATRIX, ',') + ']);\n' +
    'function _mseEvent(kind, info) { postMessage({ type: "ev", kind: kind, info: info }); }\n' +
    fns.map(String).join('\n') + '\n(' + _mseWorkerMain + ')();\n';
}

// Runs inside the worker (see _mseWorkerSource)
function _mseWorkerMain() {
  var SLOTS = 64, SLOT_INTS = 8;
  self.onmessage = function (e) {
    var m = e.data;
    if (m.type !== 'init') return;
    HEAPU8 = new Uint8Array(m.buffer);
    HEAP32 = new Int32Array(m.buffer);
    var ctl = new Float64Array(m.ctl), q = m.queueAddr >> 2, originShift = m.mainOrigin - performance.timeOrigin;
    var ms = new MediaSource();
    var s = _mse = {
      video: { get currentTime() { return ctl[0]; } }, ms: ms, sb: null, width: m.width, height: m.height, fps: m.fps, main10: m.main10,
      opts: m.opts, live: m.opts.live, mux: m.opts.mux, appendAt: null, needIdrAddr: m.needIdrAddr, waitIdr: true, params: null,
      seq: 1, firstPts: null, t0: null, pending: [], open: false, lastTrim: 0, keyTimes: [], stats: null,
    };
    _mseShareCtl(s, ctl);
    _mseResetStats();
    ms.addEventListener('sourceopen', function () {
      s.open = true;
      try { ms.duration = Infinity; } catch (err) {}
      _msePump();
    });
    postMessage({ type: 'handle', handle: ms.handle }, [ms.handle]);
    setInterval(function () { postMessage({ type: 'stats', stats: s.stats }); _mseResetStats(); }, 250);
    var read = Atomics.load(HEAP32, q), chan = new MessageChannel(), wake = m.opts.wake || 'async', waitMs = m.opts.waitMs || 3;
    // A player that replaces another one starts in the middle of the stream, where the host sends a
    // keyframe only on request. The request comes after the queue position is read, because the
    // worker skips a keyframe that arrived before that.
    if (m.replacing) _mseRequestIdr('the player was replaced');
    // Diagnostics: how the loop was entered, how long it waited, and gaps in a 5 ms heartbeat
    var diag = s.diag = { via: 'i', since: performance.now(), waited: 0, shift: originShift }, hbLast = performance.now();
    if (m.opts.diag) setInterval(function () {
      var n = performance.now(), d = n - hbLast;
      hbLast = n;
      if (d > 20) _mseEvent('whb', [Math.round(d), Math.round(n - d - originShift)]);
    }, 5);
    function onWake() { loop('w'); }
    function loop(via) {
      var t0 = performance.now();
      diag.via = via; diag.waited = t0 - diag.since;
      var w = Atomics.load(HEAP32, q);
      if (w - read > SLOTS) { _mseRequestIdr('worker fell ' + (w - read) + ' frames behind'); read = w; }
      while (read !== w) {
        var b = q + 1 + (read % SLOTS) * SLOT_INTS;
        _mseReceiveFrame(HEAP32[b], HEAP32[b + 1], HEAP32[b + 2], HEAP32[b + 3], HEAP32[b + 4], HEAP32[b + 5] + originShift, HEAP32[b + 6] - HEAP32[b + 5], HEAP32[b + 7] - HEAP32[b + 6]);
        read++;
      }
      _mseTask('loop', t0);
      diag.since = performance.now();
      // Yield between batches so the MediaSource events run, then sleep until the next frame
      if (Atomics.load(HEAP32, q) !== read) { chan.port2.postMessage('c'); return; }
      if (wake === 'spin') { chan.port2.postMessage('s'); return; }
      if (wake === 'timer') { setTimeout(loop, 1, 't'); return; }
      if (wake === 'wait') { Atomics.wait(HEAP32, q, read, waitMs); chan.port2.postMessage('b'); return; }
      if (wake === 'block') {
        if (!s.open || !s.sb) { Atomics.wait(HEAP32, q, read, waitMs); chan.port2.postMessage('b'); return; }
        // An append or trim is in flight: its updateend can only run from the event loop, and its
        // handler re-enters here
        if (s.sb.updating) return;
        Atomics.wait(HEAP32, q, read, 20);
        chan.port2.postMessage('b');
        return;
      }
      var r = Atomics.waitAsync(HEAP32, q, read);
      if (r.async) r.value.then(onWake); else chan.port2.postMessage('c');
    }
    chan.port1.onmessage = function (e) { loop(e.data); };
    if (wake === 'block') s.afterUpdate = function () { loop('u'); };
    loop('i');
  };
}

function _mseEvent(kind, info) {
  var s = _mse;
  if (!s) return;
  var v = s.video, b = s.sb && s.sb.buffered.length ? s.sb.buffered : null;
  s.events.push([Math.round(performance.now()), kind, info === undefined ? null : info, Math.round(v.currentTime * 1000),
    s.newest !== undefined ? Math.round((s.newest - v.currentTime) * 1000) : null]);
  if (s.events.length > 20000) s.events.splice(0, 5000);
}

// [time ms, kind, info, currentTime ms, buffered ahead ms] since the given time
function mseEvents(since) {
  if (!_mse) return 'not running';
  return JSON.stringify(_mse.events.filter(function (e) { return e[0] >= (since || 0); }));
}

function mseStop() {
  var s = _mse;
  if (!s) return;
  _mse = null;
  clearInterval(s.timer);
  clearInterval(s.padTimer);
  clearTimeout(s.rebuildTimer);
  if (s.uiPadStopped) Controller.startWatching();
  _mseDropPlayer(s);
  console.log('[mse] stopped');
}

function _mseDropPlayer(s) {
  if (s.dropped) return;
  s.dropped = true;
  s.playing = false;
  if (s.worker) s.worker.terminate();
  try { s.video.pause(); } catch (e) {}
  s.video.removeAttribute('src');
  s.video.srcObject = null;
  try { s.video.load(); } catch (e) {}
  s.video.remove();
}

// The new player starts after a pause, because the TV releases the old player in the background. A
// player created during the release blocks the page for long enough to break the sound. The gamepad
// timer of the old player runs through the pause, so no input is lost.
function _mseRebuild() {
  var s = _mse, a = s.startArgs;
  clearInterval(s.timer);
  _mseDropPlayer(s);
  _mseRenewThreads(_mseHandOverThreads);
  s.rebuildTimer = setTimeout(function () {
    mseStart(a[0], a[1], a[2], a[3], a[4], a[5], true);
    _mseEvent('rebuild');
  }, 250);
}

// ---- Threads ----
// emscripten creates the app's threads when the page loads and reuses them for every stream, so
// all of them exist at the moment the TV starts to hold back threads (see rebuildAt). From here
// on the pool hands out only threads created after that moment: the idle ones are replaced at
// once, and one that is in use is replaced when its work ends.
var _mseThreadsRenewed = false, _mseThreadsReady = false, _mseThreadsWaiting = [];

function _mseRenewThreads(then) {
  if (then) _mseThreadsWaiting.push(then);
  if (_mseThreadsReady) { _mseThreadsWaiting.splice(0).forEach(function (f) { f(); }); return; }
  if (_mseThreadsRenewed || typeof PThread === 'undefined') return;
  _mseThreadsRenewed = true;
  var idle = PThread.unusedWorkers.splice(0), toPool = PThread.returnWorkerToPool;
  idle.forEach(function (w) { w.terminate(); });
  PThread.runningWorkers.forEach(function (w) { w.mseOld = true; });
  PThread.returnWorkerToPool = function (worker) {
    toPool(worker);
    if (!worker.mseOld) return;
    worker.mseOld = false;
    PThread.unusedWorkers.splice(PThread.unusedWorkers.indexOf(worker), 1);
    worker.terminate();
    PThread.allocateUnusedWorkers(1);
    // The pool hands out its last thread first, and the replacement is still loading
    PThread.unusedWorkers.unshift(PThread.unusedWorkers.pop());
  };
  PThread.allocateUnusedWorkers(idle.length, function () {
    _mseThreadsReady = true;
    _mseThreadsWaiting.splice(0).forEach(function (f) { f(); });
  });
}

// The stream may still be connecting when its player passes rebuildAt, and the module hands
// over only once the connection is up, so the request repeats until the module takes it.
function _mseHandOverThreads(tries) {
  if (!_mse) return;
  if (Module._mlRespawnStreamThreads()) { _mseEvent('threads'); return; }
  if (!(tries >= 20)) setTimeout(_mseHandOverThreads, 250, (tries || 0) + 1);
}

setTimeout(function () { if (_mseOpts().rebuildAt > 0) _mseRenewThreads(); }, Math.max(0, MSE_DEFAULTS.rebuildAt - performance.now()));

function _mseResetStats() {
  _mse.stats = { since: performance.now(), frames: 0, appended: 0, dropped: 0, idr: 0, resets: 0, seeks: 0, waiting: 0, trims: 0,
                 rateMs: 0, ahead: null, aheadN: 0, recvToAppend: [], muxMs: [], appendMs: [], sizes: [], lag: [], reasm: [], cq: [], steps: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0], bytes: 0, q0: _mse.video.getVideoPlaybackQuality ? _mse.video.getVideoPlaybackQuality() : null };
}

function _mseRequestIdr(reason) {
  var s = _mse;
  console.warn('[mse] requesting IDR: ' + reason);
  s.waitIdr = true;
  s.pending = [];
  s.stageLen = 0;
  s.stats.resets++;
  Atomics.store(HEAP32, s.needIdrAddr >> 2, 1);
}

function _mseReceiveFrame(ptr, len, idr, frameNumber, ptsMs, recvMs, reasmMs, queueMs) {
  var s = _mse;
  if (!s) return;
  var now = performance.now();
  s.stats.frames++;
  if (s.lastRecvAt !== undefined && now - s.lastRecvAt > 20) _mseEvent('agap', [Math.round(now - s.lastRecvAt), Math.round(recvMs - s.lastRecvMs), ptsMs - s.lastPtsMs]);
  if (s.opts.diag && s.lastPtsMs !== undefined && ptsMs - s.lastPtsMs > 13) _mseEvent('pgap', ptsMs - s.lastPtsMs);
  if (s.lastPtsMs !== undefined) {
    var gap = Math.min(50, Math.max(4, ptsMs - s.lastPtsMs));
    s.cadence = s.cadence ? s.cadence * 0.95 + gap * 0.05 : gap;
    if (s.ctl) s.ctl[5] = s.cadence;
  }
  s.lastRecvAt = now; s.lastPtsMs = ptsMs; s.lastRecvMs = recvMs;
  var lag = now - recvMs, dg = s.diag;
  s.stats.lag.push(lag);
  if (reasmMs !== undefined) { s.stats.reasm.push(reasmMs); s.stats.cq.push(queueMs); }
  if (lag > 20) _mseEvent('wlate', [Math.round(lag), dg ? dg.via : 'm', dg ? Math.round(dg.waited) : null, Math.round(now - (dg ? dg.shift : 0)), reasmMs, queueMs]);
  if (frameNumber % 120 === 0) _mseEvent('clk', [ptsMs, Math.round(recvMs * 10) / 10, Math.round(now * 10) / 10]);
  var dd = HEAPU8.subarray(ptr, ptr + len), payload = 0, nalStart = -1, nalType = 0;
  for (var q = 0; q + 3 < len; q++) {
    if (dd[q] === 0 && dd[q + 1] === 0 && dd[q + 2] === 1) {
      if (nalStart >= 0 && nalType !== 38) payload += q - nalStart;
      nalStart = q + 3; nalType = (dd[q + 3] >> 1) & 0x3F; q += 2;
    }
  }
  if (nalStart >= 0 && nalType !== 38) payload += len - nalStart;
  s.stats.sizes.push(payload);
  if (idr) { s.keyBytes = 0; s.askedAt = null; }
  else {
    var avg = s.avgBytes || payload, mb = 1048576;
    s.keyBytes = (s.keyBytes || 0) + payload;
    var scene = s.keyBytes > s.opts.sceneMB * mb && payload > s.opts.sceneRatio * avg;
    if ((scene || s.keyBytes > s.opts.idrMB * mb) && (s.askedAt === null || s.askedAt === undefined || s.keyBytes - s.askedAt > 4 * mb)) {
      s.askedAt = s.keyBytes;
      Atomics.store(HEAP32, s.needIdrAddr >> 2, 2);
      _mseEvent('idrReq', scene ? 'scene' : 'size');
    }
    s.avgBytes = avg * 0.95 + payload * 0.05;
  }
  if (s.waitIdr && !idr) { s.stats.dropped++; return; }
  var d = HEAPU8.subarray(ptr, ptr + len);
  var vps = null, sps = null, pps = null, sample = null, k = 0, sampleLen = 0, i, type;
  if (s.mux === 'old') {
    var nals = _annexBNals(d), parts = [];
    for (i = 0; i < nals.length; i++) {
      type = (nals[i][0] >> 1) & 0x3F;
      if (type === 32) vps = nals[i];
      else if (type === 33) sps = nals[i];
      else if (type === 34) pps = nals[i];
      else if (type === 35) continue;
      parts.push(_mp4U32(nals[i].length), nals[i]);
    }
    sample = _mp4Concat(parts);
  } else {
    k = _annexBScan(d);
    for (i = 0; i < k; i += 2) {
      type = (d[_nalIdx[i]] >> 1) & 0x3F;
      if (type === 35) continue;
      sampleLen += 4 + _nalIdx[i + 1] - _nalIdx[i];
      if (idr && type >= 32 && type <= 34) {
        var nal = d.subarray(_nalIdx[i], _nalIdx[i + 1]);
        if (type === 32) vps = nal; else if (type === 33) sps = nal; else pps = nal;
      }
    }
  }
  if (idr) {
    s.stats.idr++;
    if (vps && sps && pps) {
      var key = Array.prototype.join.call(sps, ',') + '|' + Array.prototype.join.call(pps, ',');
      if (key !== s.params) {
        s.params = key;
        var initSeg = _hevcInitSegment(vps.slice(), sps.slice(), pps.slice(), s.width, s.height, s.main10, s.live);
        if (s.opts.reuse) { _mseStageAdd(s, initSeg); s.pending.push({ init: true }); } else s.pending.push({ init: true, data: initSeg });
      }
    }
    if (!s.params) { s.stats.dropped++; return; }
    s.waitIdr = false;
  }
  var t;
  if (s.opts.pts === 'arrival') {
    if (s.t0 === null) s.t0 = now;
    t = Math.round((now - s.t0) * MSE_TIMESCALE / 1000);
  } else if (s.opts.pts === 'grid') {
    if (s.firstPts === null) s.firstPts = ptsMs;
    var pos = (ptsMs - s.firstPts) * (s.fps || 120) / 1000 + (s.gridShift || 0);
    var slot = Math.round(pos);
    if (s.opts.gridPhase) {
      // Frames whose timestamps sit near the edge of a slot fall to either side of it from one
      // frame to the next, which shows as judder. Moving the grid by half a slot centres them.
      s.gridOff = (s.gridOff === undefined ? 0.25 : s.gridOff) * 0.97 + Math.abs(pos - slot) * 0.03;
      s.gridAge = (s.gridAge || 0) + 1;
      if (s.gridOff > 0.32 && s.gridAge > 120) {
        s.gridShift = (s.gridShift || 0) + 0.5;
        s.gridOff = 0.25; s.gridAge = 0;
        _mseEvent('gridShift', Math.round(pos * 100) / 100);
      }
    }
    var bumped = s.lastSlot !== undefined && slot <= s.lastSlot;
    if (bumped) slot = s.lastSlot + 1;
    if (s.lastSlot !== undefined) { var step = bumped ? 0 : Math.min(9, slot - s.lastSlot); s.stats.steps[step]++; }
    s.lastSlot = slot;
    t = Math.round(slot * MSE_TIMESCALE / (s.fps || 120));
  } else {
    if (s.firstPts === null) s.firstPts = ptsMs;
    t = (ptsMs - s.firstPts) * (MSE_TIMESCALE / 1000);
  }
  if (s.lastT !== undefined && t <= s.lastT) t = s.lastT + 1;
  s.lastT = t;
  if (idr) {
    s.keyTimes.push(t / MSE_TIMESCALE);
    _mseEvent('idr', Math.round(t / 90));
    if (s.seekWanted) { s.seekWanted = false; s.seekTo = t / MSE_TIMESCALE; }
  }
  var dur = Math.round(MSE_TIMESCALE * s.opts.durMs / 1000);
  if (s.opts.reuse) {
    if (sample) _mseStageAdd(s, _mp4Fragment(s.seq++, t, dur, sample, idr));
    else {
      _mseStageReserve(s, _mp4FragHeadLen() + sampleLen);
      s.stageLen += _mp4FragmentInto(s.stage, s.stageDv, s.stageLen, s.seq++, t, dur, d, k, sampleLen, idr);
    }
    s.pending.push({ recv: recvMs, bytes: len, t: t });
  } else {
    var frag = sample ? _mp4Fragment(s.seq++, t, dur, sample, idr) : _mp4FragmentFast(s.seq++, t, dur, d, k, sampleLen, idr);
    s.pending.push({ data: frag, recv: recvMs, bytes: len, t: t });
  }
  s.stats.muxMs.push(performance.now() - now);
  _msePump();
}

function _msePump() {
  var s = _mse;
  if (!s || !s.open || !s.pending.length) return;
  if (!s.sb) {
    try {
      s.sb = s.ms.addSourceBuffer(s.main10 ? 'video/mp4; codecs="hev1.2.4.L153.B0"' : 'video/mp4; codecs="hev1.1.6.L153.B0"');
    } catch (e) {
      console.error('[mse] addSourceBuffer failed: ' + e);
      return;
    }
    s.sb.mode = 'segments';
    s.sb.addEventListener('updateend', function () {
      var t0 = performance.now();
      if (s.rmAt) { _mseEvent('rm', Math.round((t0 - s.rmAt) * 10) / 10); s.rmAt = 0; }
      if (_mse === s && s.appendAt !== null) { s.stats.appendMs.push(t0 - s.appendAt); s.appendAt = null; }
      if (s.ctl && s.sb.buffered.length) s.ctl[2] = s.sb.buffered.start(0);
      _mseTask('buf', t0);
      _msePump();
      _mseTask('upd', t0);
      if (s.afterUpdate) s.afterUpdate();
    });
    s.sb.addEventListener('error', function () { if (_mse === s) _mseRequestIdr('source buffer error'); });
  }
  if (s.sb.updating) return;
  var now = performance.now();
  var v = s.video;
  // remove() also drops every frame up to the next keyframe, and the stream has keyframes only
  // where one was requested, so the buffer is trimmed only up to a keyframe behind playback.
  // Freeing a whole stretch between keyframes at once stalls this thread for tens of ms, so it
  // goes in slices of trimSlice seconds, working back from that keyframe.
  if (s.trimEnd !== undefined && s.trimEnd !== null) {
    // A trim in progress: one slice per trimGap ms, newest first, so each remove() frees little
    if (now - s.lastTrim >= s.opts.trimGap && s.sb.buffered.length) {
      s.lastTrim = now;
      var from = Math.max(s.sb.buffered.start(0), s.trimEnd - s.opts.trimSlice);
      if (s.trimEnd - from < 0.004) s.trimEnd = null;
      else {
        s.rmAt = performance.now();
        s.sb.remove(from, s.trimEnd);
        s.trimEnd = from - s.sb.buffered.start(0) < 0.004 ? null : from;
        return;
      }
    }
  } else if (now - s.lastTrim > 1000 && s.sb.buffered.length) {
    s.lastTrim = now;
    var cut = null;
    while (s.keyTimes.length > 1 && s.keyTimes[1] < v.currentTime - 0.5) cut = s.keyTimes.shift();
    if (cut !== null && s.keyTimes[0] > s.sb.buffered.start(0) + 0.1) {
      s.stats.trims++;
      _mseEvent('trim', Math.round(s.keyTimes[0] * 1000));
      s.rmAt = performance.now();
      if (s.opts.trimSlice > 0) {
        var first = Math.max(s.sb.buffered.start(0), s.keyTimes[0] - s.opts.trimSlice);
        s.sb.remove(first, s.keyTimes[0] - 0.001);
        s.trimEnd = first - s.sb.buffered.start(0) < 0.004 ? null : first;
      } else s.sb.remove(0, s.keyTimes[0] - 0.001);
      return;
    }
  }
  var batch = s.pending;
  s.pending = [];
  var chunks = [];
  for (var i = 0; i < batch.length; i++) {
    if (!s.opts.reuse) chunks.push(batch[i].data);
    if (!batch[i].init) {
      s.newest = batch[i].t / MSE_TIMESCALE;
      s.stats.appended++;
      s.stats.bytes += batch[i].bytes;
      s.stats.recvToAppend.push(now - batch[i].recv);
    }
  }
  if (s.ctl) s.ctl[1] = s.newest;
  try {
    s.appendAt = performance.now();
    if (s.opts.reuse) {
      var len = s.stageLen;
      s.stageLen = 0;
      s.sb.appendBuffer(s.stage.subarray(0, len));
    } else s.sb.appendBuffer(chunks.length === 1 ? chunks[0] : _mp4Concat(chunks));
    _mseTask('append', s.appendAt);
  } catch (e) {
    _mseRequestIdr('appendBuffer threw ' + e.name);
  }
}

// Reports a synchronous stretch over 6 ms: [kind, ms, start in main-thread time]
function _mseTask(kind, t0) {
  var d = performance.now() - t0;
  if (d > 6) _mseEvent('wtask', [kind, Math.round(d * 10) / 10, Math.round(t0 - (_mse && _mse.diag ? _mse.diag.shift : 0))]);
}

function _mseControl() {
  var s = _mse;
  if (!s) return;
  var tc = performance.now();
  if (s.lastCtl && tc - s.lastCtl > 30) _mseEvent('mhb', Math.round(tc - s.lastCtl));
  s.lastCtl = tc;
  var v = s.video, o = s.opts;
  if (s.worker && o.rebuildAt > 0 && s.bornAt < o.rebuildAt && tc >= o.rebuildAt) { _mseRebuild(); return; }
  if (s.worker) s.ctl[0] = v.currentTime;
  else if (!s.sb || !s.sb.buffered.length) return;
  if (s.newest === undefined) return;
  var end = s.newest, start = s.worker ? s.ctl[2] : s.sb.buffered.start(0);
  var ahead = (end - v.currentTime) * 1000;
  if (!s.playing) {
    if (ahead >= o.startAhead) {
      s.playing = true;
      if (start >= 0 && v.currentTime < start) v.currentTime = start;
      v.play().catch(function (e) { console.error('[mse] play failed: ' + e); });
    }
    return;
  }
  var st = s.stats, ah = st.ahead || (st.ahead = new Float32Array(_MSE_AHEAD_MAX));
  if (st.aheadN < _MSE_AHEAD_MAX) ah[st.aheadN++] = ahead;
  var nowMs = performance.now();
  if (v.currentTime !== s.lastCt) {
    if (s.stalled) _mseEvent('resume', Math.round(nowMs - s.lastCtAt));
    s.stalled = false;
    s.lastCt = v.currentTime;
    s.lastCtAt = nowMs;
  } else if (!s.stalled && nowMs - s.lastCtAt > 60) {
    s.stalled = true;
    _mseEvent('stall');
  }
  // A seek decodes forward from the keyframe before the target, so it jumps only onto a keyframe
  if (s.seekTo !== null && s.seekTo !== undefined && end >= s.seekTo + o.seekTarget / 1000) {
    _mseEvent('seek', Math.round(s.seekTo * 1000));
    v.currentTime = s.seekTo;
    s.seekTo = null;
    s.stats.seeks++;
  } else if (ahead > o.seekAbove && !s.seekWanted && (s.seekTo === null || s.seekTo === undefined)) {
    s.seekWanted = true;
    Atomics.store(HEAP32, s.needIdrAddr >> 2, 2);
  }
  var gauge = ahead;
  if (o.rateWindow > 0) {
    var h = s.hist || (s.hist = { t: new Float64Array(_MSE_HIST), v: new Float64Array(_MSE_HIST), tmp: new Float64Array(_MSE_HIST), head: 0, n: 0 });
    if (h.n === _MSE_HIST) { h.head = (h.head + 1) % _MSE_HIST; h.n--; }
    var at = (h.head + h.n) % _MSE_HIST, hx;
    h.t[at] = nowMs; h.v[at] = ahead; h.n++;
    while (h.n && h.t[h.head] < nowMs - o.rateWindow) { h.head = (h.head + 1) % _MSE_HIST; h.n--; }
    for (var hi = 0; hi < h.n; hi++) { hx = h.v[(h.head + hi) % _MSE_HIST]; if (hx < gauge) gauge = hx; }
  }
  var until = o.rateUntil === undefined || o.rateUntil === null ? o.rateAbove : o.rateUntil, above = o.rateAbove;
  var cadence = s.worker ? s.ctl[5] : s.cadence;
  if (o.floorFrames > 0 && cadence > 0) { until = o.floorFrames * cadence + o.margin; above = o.floorFrames * cadence + o.marginHi; }
  s.floorMs = o.floorFrames > 0 && cadence > 0 ? o.floorFrames * cadence : null;
  var rate = gauge > above || (s.appliedRate === o.rateHi && o.rateHi !== 1 && gauge > until) ? o.rateHi : gauge < o.rateBelow ? o.rateLo : 1.0;
  if (s.floorMs !== null && o.rateWindow > 0 && rate !== o.rateHi) {
    var level = _mseMedian(s.hist, s.hist.n), recent = _mseMedian(s.hist, 10);
    rate = level < s.floorMs + o.marginLo || (s.appliedRate === o.rateLo && recent < s.floorMs + o.marginMid) ? o.rateLo : 1.0;
  }
  if ((s.appliedRate === undefined ? 1 : s.appliedRate) !== rate && nowMs - (s.lastRateAt || 0) >= o.rateHold) { v.playbackRate = rate; s.appliedRate = rate; s.lastRateAt = nowMs; _mseEvent('rate', rate); }
  if (rate !== 1.0) s.stats.rateMs += 10;
}

var _MSE_HIST = 256, _MSE_AHEAD_MAX = 32768;

function _mseMedian(h, count) {
  var n = Math.min(count, h.n), tmp = h.tmp, from = h.head + h.n - n, i, j, x;
  if (!n) return 0;
  for (i = 0; i < n; i++) {
    x = h.v[(from + i) % _MSE_HIST];
    for (j = i - 1; j >= 0 && tmp[j] > x; j--) tmp[j + 1] = tmp[j];
    tmp[j + 1] = x;
  }
  return tmp[n >> 1];
}

function _msePct(a, p) {
  if (!a.length) return null;
  var b = a.slice().sort(function (x, y) { return x - y; });
  return Math.round(b[Math.floor((b.length - 1) * p)] * 10) / 10;
}

// Summary since the last call, for the inspector
function mseSummary(reset) {
  var s = _mse;
  if (!s) return 'not running';
  var st = s.stats, secs = (performance.now() - st.since) / 1000, ahead = st.ahead ? st.ahead.subarray(0, st.aheadN) : [];
  var q = s.video.getVideoPlaybackQuality ? s.video.getVideoPlaybackQuality() : null;
  var out = {
    secs: Math.round(secs * 10) / 10,
    inFps: Math.round(st.frames / secs * 10) / 10,
    appendedFps: Math.round(st.appended / secs * 10) / 10,
    mbps: Math.round(st.bytes * 8 / secs / 1e5) / 10,
    dropped: st.dropped, idr: st.idr, resets: st.resets, seeks: st.seeks, waiting: st.waiting, trims: st.trims,
    bufferedSec: s.sb && s.sb.buffered.length ? Math.round((s.sb.buffered.end(s.sb.buffered.length - 1) - s.sb.buffered.start(0)) * 10) / 10 : 0,
    catchUpPct: Math.round(st.rateMs / 10 / secs),
    steps: st.steps,
    floorMs: s.floorMs ? Math.round(s.floorMs * 10) / 10 : null,
    aheadMs: { p10: _msePct(ahead, 0.1), p50: _msePct(ahead, 0.5), p90: _msePct(ahead, 0.9), max: _msePct(ahead, 1) },
    lagMs: { p50: _msePct(st.lag, 0.5), p90: _msePct(st.lag, 0.9), p99: _msePct(st.lag, 0.99), max: _msePct(st.lag, 1) },
    reasmMs: { p50: _msePct(st.reasm, 0.5), p90: _msePct(st.reasm, 0.9), p99: _msePct(st.reasm, 0.99), max: _msePct(st.reasm, 1) },
    queueMs: { p50: _msePct(st.cq, 0.5), p90: _msePct(st.cq, 0.9), p99: _msePct(st.cq, 0.99), max: _msePct(st.cq, 1) },
    recvToAppendMs: { p50: _msePct(st.recvToAppend, 0.5), p90: _msePct(st.recvToAppend, 0.9) },
    sizes: (function () { var a = st.sizes.slice().sort(function (x, y) { return x - y; }), med = a[a.length >> 1] || 0; return { n: a.length, p10: a[Math.floor(a.length * 0.1)], p50: med, tiny: a.filter(function (x) { return x < med / 4; }).length }; })(),
    muxMs: { p50: _msePct(st.muxMs, 0.5), p90: _msePct(st.muxMs, 0.9), max: _msePct(st.muxMs, 1) },
    appendMs: { p50: _msePct(st.appendMs, 0.5), p90: _msePct(st.appendMs, 0.9), max: _msePct(st.appendMs, 1) },
    decoded: q && st.q0 ? q.totalVideoFrames - st.q0.totalVideoFrames : null,
    decoderDropped: q && st.q0 ? q.droppedVideoFrames - st.q0.droppedVideoFrames : null,
    video: [s.video.videoWidth, s.video.videoHeight, Math.round(s.video.currentTime * 100) / 100, s.video.playbackRate],
    opts: s.opts,
  };
  if (reset !== false) _mseResetStats();
  return JSON.stringify(out);
}

function mseSetOpts(opts) {
  window.mseOpts = Object.assign({}, window.mseOpts || {}, opts);
  if (_mse) _mse.opts = _mseOpts();
  return JSON.stringify(_mseOpts());
}

// ---- Latency overlay ----
// Shows the homelab's clock (performance.now() + window.hostOffsetMs, in 1/120 s ticks, 12-bit
// Gray code) in the right half of the screen, level with the code the host pattern draws in the
// left half of the stream. One camera frame of both gives the stream's glass-to-glass latency.

var _latOverlay = null;

function latOverlay(on) {
  if (_latOverlay) { cancelAnimationFrame(_latOverlay.raf); _latOverlay.el.remove(); _latOverlay = null; }
  if (!on) return 'off';
  var el = document.createElement('div');
  el.setAttribute('style', 'position: fixed; left: 50%; width: 50%; top: 40%; height: 20%; z-index: 99999; display: flex; pointer-events: none; background: #000;');
  var cells = [];
  for (var i = 0; i < 14; i++) {
    var c = document.createElement('div');
    c.setAttribute('style', 'flex: 1; margin: 0 6px; background: #000;');
    el.appendChild(c);
    cells.push(c);
  }
  document.body.appendChild(el);
  var state = _latOverlay = { el: el, raf: 0, last: -1 };
  function frame() {
    if (_latOverlay !== state) return;
    var v = Math.floor((performance.now() + (window.hostOffsetMs || 0)) * 0.12) & 4095;
    var cal = !!window.latCal;
    var key = cal ? -2 : v;
    if (key !== state.last) {
      state.last = key;
      var g = v ^ (v >> 1);
      cells[0].style.background = '#fff';
      cells[1].style.background = cal ? '#fff' : '#000';
      for (var b = 0; b < 12; b++) cells[2 + b].style.background = (cal || (g >> (11 - b)) & 1) ? '#fff' : '#000';
    }
    state.raf = requestAnimationFrame(frame);
  }
  frame();
  return 'on';
}

// ---- Gamepad timing ----
// Records every button edge the TV's Gamepad API reports, in homelab time:
// [poll time, gamepad.timestamp, pad index, button, pressed]. The host's latency tool sets
// window.hostOffsetMs and matches these edges against Sunshine's virtual pad.

var _padLog = null;

function padLog(on) {
  if (_padLog) { clearInterval(_padLog.timer); }
  if (!on) { var n = _padLog ? _padLog.events.length : 0; return 'off, ' + n + ' events kept'; }
  var state = _padLog = { timer: 0, events: [], last: {} };
  state.timer = setInterval(function () {
    var off = window.hostOffsetMs || 0, now = performance.now() + off, pads = navigator.getGamepads();
    for (var p = 0; p < pads.length; p++) {
      var g = pads[p];
      if (!g) continue;
      var prev = state.last[p] || [];
      for (var b = 0; b < g.buttons.length; b++) {
        var pr = g.buttons[b].pressed;
        if (prev[b] !== undefined && prev[b] !== pr) state.events.push([now, g.timestamp + off, p, b, pr ? 1 : 0]);
        prev[b] = pr;
      }
      state.last[p] = prev;
    }
    if (state.events.length > 5000) state.events.shift();
  }, 1);
  return 'on';
}

function padLogDump() {
  return JSON.stringify({ offsetMs: window.hostOffsetMs || 0, events: _padLog ? _padLog.events : [] });
}
